"""Weekly price update. Run by your Mac every Sunday (see scheduler/macos).

    python -m scripts.update_prices            # run now
    python -m scripts.update_prices --if-due   # only if this week's run hasn't happened yet
    python -m scripts.update_prices --stores magrudys amazon_ae

Steps: download the latest database from GitHub -> check prices of every
To Read book at every enabled store -> save them with a timestamp (history is
kept) -> e-mail any price drops -> upload the database again.
"""

from __future__ import annotations

import argparse
import fcntl
import sys
from datetime import datetime, timedelta

from booktracker import alerts, config, db, sync
from booktracker.fetchers.registry import enabled_fetchers
from booktracker.prices import collect_prices

STATE_FILE = config.DATA_DIR / ".last_price_run"
LOCK_FILE = config.DATA_DIR / ".update.lock"
RUN_HOUR = 9  # Sunday 09:00 local time


def this_weeks_slot(now: datetime) -> datetime:
    """The most recent Sunday 09:00 at or before `now`."""
    days_since_sunday = (now.weekday() + 1) % 7          # Mon=0 ... Sun=6
    slot = (now - timedelta(days=days_since_sunday)).replace(hour=RUN_HOUR, minute=0, second=0,
                                                              microsecond=0)
    if slot > now:
        slot -= timedelta(days=7)
    return slot


def is_due(now: datetime | None = None) -> bool:
    """True if no successful run has happened since this week's Sunday slot."""
    now = now or datetime.now()
    try:
        last = datetime.fromisoformat(STATE_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return True
    return last < this_weeks_slot(now)


def run(stores: list[str] | None = None, use_sync: bool = True) -> int:
    log = config.setup_logging("update_prices")
    use_sync = use_sync and sync.enabled()
    if use_sync:
        log.info("downloading the latest database from GitHub…")
        sync.pull()
    conn = db.connect()
    books = db.list_books(conn, "To Read")
    links = db.store_links(conn)
    conn.close()
    log.info("checking prices for %d To Read book(s)", len(books))

    fetchers = enabled_fetchers(stores)
    out = collect_prices(books, fetchers, links)
    for store, summary in out.per_store.items():
        log.info("%-18s %s", store, summary)

    checked_at = db.now()   # one timestamp for the whole run

    def save(c):
        run_id = db.start_run(c)
        for row in out.rows:
            db.add_price(c, run_id=run_id, date_checked=checked_at, commit=False, **row)
        for entry in out.logs:
            db.log_fetch(c, run_id=run_id, commit=False, **entry)
        db.finish_run(c, run_id, books_checked=len(books), prices_saved=len(out.rows),
                      errors=out.errors, status="ok")
        return run_id, alerts.find_price_drops(c, run_id)

    stamp = datetime.now().strftime("%Y-%m-%d")
    if use_sync:
        run_id, drops = sync.write(save, message=f"Weekly price update {stamp}")
    else:
        conn = db.connect()
        run_id, drops = save(conn)
        conn.close()
    log.info("saved %d prices (run %s, %d problem(s))", len(out.rows), run_id, out.errors)

    alerts.send_price_alerts(drops)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(datetime.now().isoformat(timespec="seconds"))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--if-due", action="store_true", help="skip if this week's update already ran")
    parser.add_argument("--stores", nargs="*", help="only these stores (e.g. magrudys noon)")
    parser.add_argument("--no-sync", action="store_true", help="don't download/upload via GitHub")
    args = parser.parse_args(argv)

    if args.if_due and not is_due():
        print("This week's price update already ran - nothing to do.")
        return 0
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)   # never two runs at once
        except BlockingIOError:
            print("Another price update is already running.")
            return 0
        return run(args.stores, use_sync=not args.no_sync)


if __name__ == "__main__":
    sys.exit(main())
