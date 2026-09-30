"""Jashanmal Bookstore (jashanmal.com) - Shopify shop with Dubai stores.

Uses Shopify's public storefront JSON (allowed by robots.txt):
- GET /search/suggest.json?q=<ISBN or title>   -> matching products
- GET /products/<handle>.js                     -> price, stock, barcode (= ISBN)
"""

from __future__ import annotations

from .base import Candidate, Fetcher, parse_price

BASE = "https://www.jashanmal.com"


def parse_product(p: dict) -> list[Candidate]:
    """One Candidate per variant (a variant's barcode is the ISBN)."""
    url = f"{BASE}/products/{p.get('handle')}"
    results = []
    for v in p.get("variants") or [{}]:
        cents = v.get("price", p.get("price"))
        results.append(Candidate(
            title=p.get("title") or "",
            author=None,
            isbn=v.get("barcode") or v.get("sku") or None,
            price_aed=(cents / 100) if isinstance(cents, (int, float)) else None,
            url=url,
            in_stock=v.get("available", p.get("available")),
        ))
    return results


def is_book(product: dict) -> bool:
    return (product.get("type") or product.get("product_type") or "").lower() == "books"


def parse_suggestion(hit: dict) -> Candidate:
    """A search-suggestion hit (has price/stock but no ISBN)."""
    return Candidate(
        title=hit.get("title") or "",
        price_aed=parse_price(hit.get("price")),
        url=f"{BASE}/products/{handle_of(hit)}",
        in_stock=hit.get("available"),
    )


def handle_of(hit: dict) -> str:
    return hit.get("handle") or hit.get("url", "").split("/products/")[-1].split("?")[0]


class JashanmalFetcher(Fetcher):
    """Shopify rate-limits quickly, so this uses as few requests as possible."""

    def _suggest(self, query: str, limit: int) -> list[dict]:
        r = self.http.get(f"{BASE}/search/suggest.json", params={
            "q": query, "resources[type]": "product", "resources[limit]": limit})
        hits = r.json().get("resources", {}).get("results", {}).get("products") or []
        return [h for h in hits if is_book(h)]

    def find_by_isbn(self, isbn13: str) -> list[Candidate]:
        # Confirm the ISBN on the product itself (its barcode) before trusting it
        for hit in self._suggest(isbn13, 2)[:1]:
            product = self.http.get(f"{BASE}/products/{handle_of(hit)}.js").json()
            return parse_product(product)
        return []

    def find_by_title(self, title: str, author: str | None) -> list[Candidate]:
        return [parse_suggestion(h) for h in self._suggest(title, 5)]
