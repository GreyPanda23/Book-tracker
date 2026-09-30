"""Magrudy's (magrudy.com) - Dubai bookstore chain + online shop.

Uses the JSON endpoints the website itself calls (no HTML scraping):
- GET  /api/item/get/<ISBN>      -> price incl. VAT, stock
- POST /api/search/do-search     -> title search (fallback)
- GET  /api/Store                -> branch list (we keep the Dubai bookshops)
robots.txt allows everything.
"""

from __future__ import annotations

import logging
import re

from .base import Candidate, FetchError, Fetcher, parse_price

log = logging.getLogger("booktracker.fetchers.magrudys")
BASE = "https://www.magrudy.com"


def product_url(title: str, isbn: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title or "book").strip("-")[:80]
    return f"{BASE}/product/{slug}--{isbn}"


def parse_item(item: dict) -> Candidate | None:
    if not item or not item.get("isbn"):
        return None
    price = parse_price(item.get("unitPriceInclVAT") or item.get("unitPrice"))
    online_qty = item.get("availableQty")
    shop_qty = item.get("inventory")
    return Candidate(
        title=item.get("title") or "",
        author=item.get("author") or None,
        isbn=item["isbn"],
        price_aed=price if price else None,
        url=product_url(item.get("title"), item["isbn"]),
        in_stock=None if online_qty is None else float(online_qty) > 0,
        store_stock=None if shop_qty is None else float(shop_qty) > 0,
    )


def dubai_bookshops(stores: list[dict]) -> list[str]:
    """Branch names in Dubai that sell books, e.g. 'Mirdif City Center'."""
    names = []
    for s in stores:
        location, category = s.get("Location") or "", s.get("Category") or ""
        if "dubai" in location.lower() and "book" in category.lower():
            names.append(s.get("Branch", "").strip().title())
    return names


class MagrudysFetcher(Fetcher):
    _branches: list[str] | None = None

    def find_by_isbn(self, isbn13: str) -> list[Candidate]:
        r = self.http.get(f"{BASE}/api/item/get/{isbn13}", headers={"Accept": "application/json"})
        if r.status_code == 404:
            return []
        try:
            item = r.json()
        except ValueError as exc:
            raise FetchError("unexpected (non-JSON) answer from Magrudy's") from exc
        c = parse_item(item)
        return [c] if c else []

    def find_by_title(self, title: str, author: str | None) -> list[Candidate]:
        body = {"q": title, "stype": "item", "pagenum": 1, "pagesize": 10,
                "appliedFilters": {}, "sortOption": ""}
        r = self.http.post(f"{BASE}/api/search/do-search", json=body)
        try:
            items = (r.json() or {}).get("data") or []
        except ValueError:
            return []
        # Search results don't include stock, so look the best hits up by ISBN
        results = []
        for item in items[:3]:
            if item.get("isbn"):
                results += self.find_by_isbn(item["isbn"])
        return results

    def branches(self) -> list[str]:
        if self._branches is None:
            try:
                live = dubai_bookshops(self.http.get(f"{BASE}/api/Store").json())
            except Exception as exc:  # fall back to the list in stores.yaml
                log.warning("Magrudy's branch list unavailable: %s", exc)
                live = []
            self._branches = live or self.cfg.branches
        return self._branches
