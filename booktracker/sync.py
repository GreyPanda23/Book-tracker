"""Keep the SQLite file in sync with a private GitHub repo (the "option A" setup).

The database lives as `books.db` on a separate `data` branch of your repo, so
saving it never redeploys the Streamlit app. Both the phone app and the weekly
job on your Mac do: download latest -> change -> upload. If someone else
uploaded in between (e.g. you edited on the phone while the weekly job ran),
GitHub rejects the upload; we then download the newer copy, re-apply the change
and try again, so no edit is lost.

Needs two secrets: GITHUB_TOKEN (fine-grained token with "Contents: read and
write" on this one repo) and GITHUB_REPO ("owner/name"). Without them the app
simply works on the local file only.
"""

from __future__ import annotations

import base64
import logging
import sqlite3
import time
from pathlib import Path
from typing import Callable, TypeVar

import httpx

from . import config, db

log = logging.getLogger("booktracker.sync")
API = "https://api.github.com"
REMOTE_NAME = "books.db"
T = TypeVar("T")
_transport: httpx.BaseTransport | None = None  # tests plug in a fake GitHub here


class SyncConflict(Exception):
    """The remote file changed since we downloaded it."""


class SyncError(Exception):
    """Any other GitHub problem (bad token, network...)."""


def _settings() -> tuple[str | None, str | None, str]:
    return (config.get_secret("GITHUB_TOKEN"), config.get_secret("GITHUB_REPO"),
            config.get_secret("GITHUB_DATA_BRANCH", "data"))


def enabled() -> bool:
    token, repo, _ = _settings()
    return bool(token and repo)


def _client() -> httpx.Client:
    token, _, _ = _settings()
    return httpx.Client(base_url=API, timeout=30, transport=_transport, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })


def _sha_file(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".sha")


def local_sha(path: Path | None = None) -> str | None:
    f = _sha_file(Path(path or config.DB_PATH))
    return f.read_text().strip() or None if f.exists() else None


def _save_sha(path: Path, sha: str | None) -> None:
    _sha_file(path).write_text(sha or "")


def _ensure_branch(client: httpx.Client, repo: str, branch: str) -> None:
    r = client.get(f"/repos/{repo}/branches/{branch}")
    if r.status_code == 200:
        return
    if r.status_code != 404:
        raise SyncError(f"GitHub error {r.status_code}: {r.text[:200]}")
    info = client.get(f"/repos/{repo}")
    info.raise_for_status()
    default = info.json()["default_branch"]
    base = client.get(f"/repos/{repo}/git/ref/heads/{default}")
    base.raise_for_status()
    created = client.post(f"/repos/{repo}/git/refs", json={
        "ref": f"refs/heads/{branch}", "sha": base.json()["object"]["sha"]})
    if created.status_code not in (201, 422):  # 422 = created meanwhile
        raise SyncError(f"could not create branch '{branch}': {created.text[:200]}")
    log.info("created data branch %s", branch)


def remote_sha() -> str | None:
    """Blob SHA of books.db on GitHub (None if it isn't there yet)."""
    _, repo, branch = _settings()
    with _client() as client:
        r = client.get(f"/repos/{repo}/contents/", params={"ref": branch})
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise SyncError(f"GitHub error {r.status_code}: {r.text[:200]}")
        for item in r.json():
            if item["name"] == REMOTE_NAME:
                return item["sha"]
    return None


def pull(path: Path | None = None) -> str | None:
    """Download books.db from GitHub into `path`. Returns its SHA."""
    path = Path(path or config.DB_PATH)
    _, repo, branch = _settings()
    with _client() as client:
        _ensure_branch(client, repo, branch)
        r = client.get(f"/repos/{repo}/contents/{REMOTE_NAME}", params={"ref": branch},
                       headers={"Accept": "application/vnd.github.raw+json"})
        if r.status_code == 404:
            log.info("no books.db on GitHub yet; starting with the local copy")
            _save_sha(path, None)
            return None
        if r.status_code != 200:
            raise SyncError(f"download failed {r.status_code}: {r.text[:200]}")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".download")
        tmp.write_bytes(r.content)
        tmp.replace(path)
    sha = remote_sha()
    _save_sha(path, sha)
    log.info("downloaded books.db (%s bytes)", len(r.content))
    return sha


def push(path: Path | None = None, message: str = "Update book database") -> str:
    """Upload books.db. Raises SyncConflict if GitHub has a newer copy."""
    path = Path(path or config.DB_PATH)
    _, repo, branch = _settings()
    body = {"message": message, "branch": branch,
            "content": base64.b64encode(path.read_bytes()).decode()}
    sha = local_sha(path)
    if sha:
        body["sha"] = sha
    with _client() as client:
        _ensure_branch(client, repo, branch)
        r = client.put(f"/repos/{repo}/contents/{REMOTE_NAME}", json=body)
    if r.status_code in (409, 422) and ("sha" in r.text or r.status_code == 409):
        raise SyncConflict(r.text[:200])
    if r.status_code not in (200, 201):
        raise SyncError(f"upload failed {r.status_code}: {r.text[:200]}")
    new_sha = r.json()["content"]["sha"]
    _save_sha(path, new_sha)
    return new_sha


def refresh_if_changed(path: Path | None = None) -> bool:
    """Download only if GitHub has a different copy than ours. True if downloaded."""
    if not enabled():
        return False
    path = Path(path or config.DB_PATH)
    remote = remote_sha()
    if path.exists() and remote == local_sha(path) and remote is not None:
        return False
    if remote is None and path.exists():
        return False
    pull(path)
    return True


def write(change: Callable[[sqlite3.Connection], T], message: str = "Update book database",
          path: Path | None = None, retries: int = 3) -> T:
    """Apply `change(conn)` to the database and upload it (if sync is on).

    On a conflict the newer copy is downloaded and `change` is applied again,
    so `change` must be safe to repeat on a fresh copy.
    """
    path = Path(path or config.DB_PATH)
    for attempt in range(retries):
        conn = db.connect(path)
        try:
            result = change(conn)
        finally:
            conn.close()
        if not enabled():
            return result
        try:
            push(path, message)
            return result
        except SyncConflict:
            log.warning("database changed on GitHub meanwhile; retrying (%s)", attempt + 1)
            pull(path)
            time.sleep(1)
    raise SyncError("could not save to GitHub after several tries; your change is saved locally")
