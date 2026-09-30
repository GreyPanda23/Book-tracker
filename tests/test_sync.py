"""Tests for GitHub sync against an in-memory fake of the GitHub contents API."""

import base64
import hashlib
import json

import httpx
import pytest

from booktracker import db, sync


class FakeGitHub:
    def __init__(self):
        self.branches = {"main"}
        self.file: bytes | None = None
        self.puts = 0

    @property
    def sha(self):
        return hashlib.sha1(self.file).hexdigest() if self.file is not None else None

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path.endswith("/branches/data"):
            return httpx.Response(200 if "data" in self.branches else 404, json={})
        if path == "/repos/me/books":
            return httpx.Response(200, json={"default_branch": "main"})
        if path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "abc"}})
        if path.endswith("/git/refs") and method == "POST":
            self.branches.add("data")
            return httpx.Response(201, json={})
        if path.endswith("/contents/") and method == "GET":
            items = [{"name": "books.db", "sha": self.sha}] if self.file is not None else []
            return httpx.Response(200, json=items)
        if path.endswith("/contents/books.db") and method == "GET":
            if self.file is None:
                return httpx.Response(404, json={})
            return httpx.Response(200, content=self.file)
        if path.endswith("/contents/books.db") and method == "PUT":
            body = json.loads(request.content)
            if self.file is not None and body.get("sha") != self.sha:
                return httpx.Response(409, json={"message": "sha does not match"})
            self.file = base64.b64decode(body["content"])
            self.puts += 1
            return httpx.Response(200, json={"content": {"sha": self.sha}})
        return httpx.Response(500, json={"unexpected": path})


@pytest.fixture
def github(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPO", "me/books")
    monkeypatch.setattr(sync, "_transport", httpx.MockTransport(fake.handler))
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)
    return fake


def test_disabled_without_secrets(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(sync.config, "get_secret", lambda name, default=None: default)
    assert not sync.enabled()
    book_id = sync.write(lambda c: db.add_book(c, title="Local only"), path=tmp_path / "b.db")
    assert book_id == 1


def test_first_write_creates_branch_and_uploads(github, tmp_path):
    path = tmp_path / "books.db"
    assert sync.pull(path) is None
    assert "data" in github.branches
    sync.write(lambda c: db.add_book(c, title="Dune", isbn="9780441172719"), path=path)
    assert github.puts == 1 and github.file == path.read_bytes()


def test_pull_downloads_remote_copy(github, tmp_path):
    phone, mac = tmp_path / "phone.db", tmp_path / "mac.db"
    sync.pull(phone)
    sync.write(lambda c: db.add_book(c, title="Dune"), path=phone)
    sync.pull(mac)
    conn = db.connect(mac)
    assert [b["title"] for b in db.list_books(conn)] == ["Dune"]
    conn.close()


def test_conflict_is_merged_not_lost(github, tmp_path):
    phone, mac = tmp_path / "phone.db", tmp_path / "mac.db"
    sync.pull(phone)
    sync.write(lambda c: db.add_book(c, title="Dune"), path=phone)
    sync.pull(mac)  # both now have the same copy
    sync.write(lambda c: db.add_book(c, title="Emma"), path=phone)   # phone saves first
    sync.write(lambda c: db.add_book(c, title="Ulysses"), path=mac)  # mac is stale -> conflict -> retry
    final = tmp_path / "check.db"
    sync.pull(final)
    conn = db.connect(final)
    assert sorted(b["title"] for b in db.list_books(conn)) == ["Dune", "Emma", "Ulysses"]
    conn.close()


def test_refresh_if_changed(github, tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    sync.pull(a)
    sync.write(lambda c: db.add_book(c, title="Dune"), path=a)
    sync.pull(b)
    assert sync.refresh_if_changed(b) is False
    sync.write(lambda c: db.add_book(c, title="Emma"), path=a)
    assert sync.refresh_if_changed(b) is True
