import plistlib
import subprocess
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from booktracker import alerts, config, db, sync
from booktracker.fetchers.base import Candidate, Fetcher, StoreConfig
from scripts import update_prices
from tests.test_sync import FakeGitHub

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# When is the update due?
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("now,slot", [
    ("2026-10-04T09:00", "2026-10-04T09:00"),   # Sunday 09:00 exactly
    ("2026-10-04T08:59", "2026-09-27T09:00"),   # Sunday morning before 9 -> last week's slot
    ("2026-10-07T15:00", "2026-10-04T09:00"),   # Wednesday
    ("2026-10-10T23:00", "2026-10-04T09:00"),   # Saturday night
])
def test_this_weeks_slot(now, slot):
    assert update_prices.this_weeks_slot(datetime.fromisoformat(now)) == datetime.fromisoformat(slot)


def test_is_due(tmp_path, monkeypatch):
    state = tmp_path / ".last_price_run"
    monkeypatch.setattr(update_prices, "STATE_FILE", state)
    wed = datetime(2026, 10, 7, 12)
    assert update_prices.is_due(wed)                       # never ran
    state.write_text("2026-10-04T09:05:00")
    assert not update_prices.is_due(wed)                   # ran this Sunday
    state.write_text("2026-10-03T10:00:00")
    assert update_prices.is_due(wed)                       # Mac was off on Sunday -> catch up


# --------------------------------------------------------------------------- #
# Full run with fake stores
# --------------------------------------------------------------------------- #
PRICES = {"9781847941831": 40.0, "9780441172719": 80.0}


class FakeStore(Fetcher):
    def find_by_isbn(self, isbn13):
        price = PRICES.get(isbn13)
        return [Candidate("x", price, f"https://fake/{isbn13}", True, isbn13)] if price else []


class BrokenStore(Fetcher):
    def find_by_isbn(self, isbn13):
        raise RuntimeError("site changed")


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "books.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(update_prices, "STATE_FILE", tmp_path / ".last_price_run")
    monkeypatch.setattr(update_prices, "LOCK_FILE", tmp_path / ".lock")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    for var in ("GITHUB_TOKEN", "EMAIL_ADDRESS", "EMAIL_APP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(update_prices, "enabled_fetchers", lambda stores=None: [
        FakeStore(StoreConfig(name="Fake", module="x", physical=True, branches=["Dubai Mall"])),
        BrokenStore(StoreConfig(name="Broken", module="x"))])
    conn = db.connect(path)
    atomic = db.add_book(conn, title="Atomic Habits", isbn="9781847941831", target_price=50)
    dune = db.add_book(conn, title="Dune", isbn="9780441172719", target_price=50)
    read = db.add_book(conn, title="Emma", isbn="9780141439587", status="Read")
    conn.close()
    return path, atomic, dune, read


def test_weekly_run_saves_history(env):
    path, atomic, dune, read = env
    assert update_prices.main(["--no-sync"]) == 0
    assert update_prices.main(["--no-sync", "--if-due"]) == 0      # second call: not due, no new run
    conn = db.connect(path)
    rows = conn.execute("SELECT * FROM prices").fetchall()
    assert {r["book_id"] for r in rows} == {atomic, dune}          # only To Read books
    assert len({r["date_checked"] for r in rows}) == 1             # one timestamp per run
    assert {(r["type"], r["branch_location"]) for r in rows} == {("online", None), ("in-store", "Dubai Mall")}
    run = conn.execute("SELECT * FROM runs").fetchall()
    assert len(run) == 1 and run[0]["status"] == "ok" and run[0]["errors"] == 2
    assert conn.execute("SELECT COUNT(*) FROM fetch_log WHERE store_name='Broken'").fetchone()[0] == 2
    conn.close()
    # A second forced run adds rows instead of overwriting
    update_prices.main(["--no-sync"])
    conn = db.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == len(rows) * 2
    assert len(db.price_history(conn, atomic)) == 4


def test_alerts_only_for_new_drops(env, monkeypatch):
    path, atomic, dune, read = env
    sent = []
    monkeypatch.setattr(alerts, "send_price_alerts", lambda drops: sent.append(drops) or bool(drops))
    update_prices.main(["--no-sync"])
    assert [d["title"] for d in sent[-1]] == ["Atomic Habits"]      # 40 < 50; Dune 80 is not
    update_prices.main(["--no-sync"])
    assert sent[-1] == []                                           # same price: no repeat e-mail
    PRICES["9781847941831"] = 35.0
    try:
        update_prices.main(["--no-sync"])
        assert [d["price"] for d in sent[-1]] == [35.0]            # dropped further: e-mail again
    finally:
        PRICES["9781847941831"] = 40.0


def test_email_sending(monkeypatch):
    monkeypatch.setenv("EMAIL_ADDRESS", "me@gmail.com")
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    monkeypatch.delenv("EMAIL_TO", raising=False)
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent["server"] = (host, port)
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def login(self, user, pw):
            sent["login"] = (user, pw)
        def send_message(self, msg):
            sent["msg"] = msg

    drops = [{"title": "Dune", "author": "Frank Herbert", "target": 60.0, "price": 45.0, "store": "Magrudy's",
              "type": "in-store", "url": "https://x", "branch": "Mirdif City Center"}]
    assert alerts.send_price_alerts(drops, smtp_factory=FakeSMTP)
    assert sent["server"] == ("smtp.gmail.com", 465)
    assert sent["login"] == ("me@gmail.com", "abcdefghijklmnop")
    body = sent["msg"].get_content()
    assert "AED 45.00 at Magrudy's (in store: Mirdif City Center)" in body and sent["msg"]["To"] == "me@gmail.com"


def test_email_not_configured_is_harmless(monkeypatch):
    monkeypatch.delenv("EMAIL_ADDRESS", raising=False)
    monkeypatch.setattr(alerts.config, "get_secret", lambda n, d=None: d)
    assert alerts.send_price_alerts([{"title": "x"}]) is False


def test_weekly_run_with_github_sync(env, monkeypatch):
    path, atomic, dune, read = env
    fake = FakeGitHub()
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPO", "me/books")
    monkeypatch.setattr(sync, "_transport", httpx.MockTransport(fake.handler))
    # the phone app uploaded the database first
    sync.push(path)
    update_prices.main([])
    phone_copy = path.parent / "phone.db"
    sync.pull(phone_copy)
    conn = db.connect(phone_copy)
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 4
    assert fake.puts == 2


# --------------------------------------------------------------------------- #
# macOS scheduler files
# --------------------------------------------------------------------------- #
def test_launchd_plist_is_valid():
    template = (ROOT / "scheduler/macos/com.booktracker.weekly.plist").read_text()
    filled = template.replace("__PROJECT__", "/Users/me/BookTracker").replace("__PYTHON__", "/usr/bin/python3")
    plist = plistlib.loads(filled.encode())
    assert plist["ProgramArguments"][-1] == "--if-due"
    assert plist["StartCalendarInterval"] == {"Weekday": 0, "Hour": 9, "Minute": 0}
    assert plist["RunAtLoad"] is True and plist["WorkingDirectory"] == "/Users/me/BookTracker"


@pytest.mark.parametrize("script", ["install.sh", "uninstall.sh"])
def test_shell_scripts_parse(script):
    subprocess.run(["bash", "-n", str(ROOT / "scheduler/macos" / script)], check=True)
