"""Keep this Mac's books.db up to date with GitHub. Run by your Mac every 15 minutes.

    python -m scripts.sync_db

Downloads the database only if GitHub has a newer copy (for example after you
added a book on your phone). Skips quietly while the price update is running.
"""

from __future__ import annotations

import fcntl
import logging
import sys

from booktracker import config, sync

LOCK_FILE = config.DATA_DIR / ".update.lock"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not sync.enabled():
        logging.info("GitHub sync is not set up; nothing to do")
        return 0
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            logging.info("price update is running; will sync next time")
            return 0
        try:
            updated = sync.refresh_if_changed()
        except Exception as exc:  # network trouble: try again next time
            logging.warning("sync failed: %s", exc)
            return 1
    logging.info("downloaded the newer database" if updated else "already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
