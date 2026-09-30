"""Quick health check: try every enabled store with a few well-known books.

    python -m scripts.check_stores               # all stores
    python -m scripts.check_stores amazon_ae noon  # only some

Nothing is saved; it just prints what each store returned, so you can see
which stores work from your computer.
"""

from __future__ import annotations

import sys
import time

from booktracker import config
from booktracker.fetchers.registry import enabled_fetchers
from booktracker.prices import collect_prices

SAMPLE_BOOKS = [
    {"id": 1, "title": "Atomic Habits", "author": "James Clear", "isbn": "9781847941831"},
    {"id": 2, "title": "Project Hail Mary", "author": "Andy Weir", "isbn": "9780593395561"},
]


def main(argv: list[str]) -> int:
    config.setup_logging("check_stores")
    fetchers = enabled_fetchers(argv or None)
    if not fetchers:
        print("No matching enabled stores. Names:", "magrudys jashanmal kinokuniya amazon_ae noon virgin")
        return 1
    print(f"Checking {', '.join(f.name for f in fetchers)} with {len(SAMPLE_BOOKS)} books "
          "(polite pauses make this take a few minutes)…\n")
    start = time.time()
    out = collect_prices(SAMPLE_BOOKS, fetchers)
    titles = {b["id"]: b["title"] for b in SAMPLE_BOOKS}
    for f in fetchers:
        print(f"=== {f.name}: {out.per_store.get(f.name, 'no result')}")
        for row in out.rows:
            if row["store_name"] == f.name:
                stock = {True: "in stock", False: "out of stock", None: "stock unknown"}[row["in_stock"]]
                print(f"   {titles[row['book_id']][:25]:25} {row['type']:9} AED {row['price_aed']:>7.2f}  "
                      f"{stock:13} [{row['match_method']}]  {row['product_url']}")
        for entry in out.logs:
            if entry["store_name"] == f.name:
                print(f"   ! {entry['level']}: {entry['message']}")
        if getattr(f, "needs_link", False):
            print("   (this store only checks books you've pasted a product link for)")
    print(f"\nDone in {time.time() - start:.0f}s. Problems: {out.errors}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
