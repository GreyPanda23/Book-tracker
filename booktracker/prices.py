"""Run every store fetcher over a list of books and collect the results.

Stores run side by side (one thread each) but each store handles its books one
at a time with polite pauses. Any problem in one store is logged and never
affects the others. A CAPTCHA/block stops that store for the rest of the run.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .fetchers.base import BlockedError, FetchError, Fetcher, RobotsDisallowed

log = logging.getLogger("booktracker.prices")
STORE_TIME_BUDGET = 30 * 60   # seconds per store per run


@dataclass
class RunOutput:
    rows: list[dict] = field(default_factory=list)       # for db.add_price
    logs: list[dict] = field(default_factory=list)       # for db.log_fetch
    errors: int = 0
    per_store: dict[str, str] = field(default_factory=dict)

    def note(self, store: str, level: str, message: str, book_id: int | None = None) -> None:
        self.logs.append(dict(store_name=store, level=level, message=message, book_id=book_id))
        if level in ("error", "blocked"):
            self.errors += 1
        getattr(log, "warning" if level in ("error", "blocked") else "info")(
            "[%s] %s: %s", store, level, message)


def run_store(fetcher: Fetcher, books: list[dict], links: dict[int, dict], out: RunOutput) -> None:
    started, found = time.monotonic(), 0
    try:
        for book in books:
            if time.monotonic() - started > STORE_TIME_BUDGET:
                out.note(fetcher.name, "error", "time budget used up; remaining books skipped")
                break
            if getattr(fetcher, "needs_link", False) and fetcher.name not in links.get(book["id"], {}):
                continue
            try:
                results = fetcher.fetch(book, links.get(book["id"], {}))
            except BlockedError as exc:
                out.note(fetcher.name, "blocked", f"{exc} - store skipped for the rest of this run", book["id"])
                break
            except RobotsDisallowed as exc:
                out.note(fetcher.name, "error", str(exc), book["id"])
                continue
            except FetchError as exc:
                out.note(fetcher.name, "error", f"{book['title']}: {exc}", book["id"])
                continue
            except Exception as exc:  # a bug in one store must not stop the run
                out.note(fetcher.name, "error", f"{book['title']}: unexpected {type(exc).__name__}: {exc}",
                         book["id"])
                continue
            if not results:
                out.note(fetcher.name, "not_found", f"{book['title']} not found", book["id"])
            for r in results:
                out.rows.append(r.as_db_row(book["id"]))
            found += bool(results)
    finally:
        try:
            fetcher.close()
        except Exception:
            pass
    out.per_store[fetcher.name] = f"{found}/{len(books)} books priced"


def collect_prices(books: list[dict], fetchers: list[Fetcher], links: dict[int, dict] | None = None,
                   parallel: bool = True) -> RunOutput:
    """Fetch prices for `books` from every store in `fetchers`."""
    out = RunOutput()
    links = links or {}
    if not books or not fetchers:
        return out
    if parallel and len(fetchers) > 1:
        with ThreadPoolExecutor(max_workers=len(fetchers)) as pool:
            for future in [pool.submit(run_store, f, books, links, out) for f in fetchers]:
                future.result()
    else:
        for f in fetchers:
            run_store(f, books, links, out)
    return out
