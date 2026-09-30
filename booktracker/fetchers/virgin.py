"""Virgin Megastore UAE (virginmegastore.ae) - Dubai stores + online.

Virgin's robots.txt forbids its search pages (/en/search, ?q=, ?text=), and
its product addresses use internal numbers rather than ISBNs, so there is no
allowed way to find a book automatically. Instead, paste the Virgin product
link for a book once (Prices page); its price is then checked every week.
Product pages carry the price in schema.org data; they show no ISBN, so the
title is checked before a price is saved.
"""

from __future__ import annotations

from .base import Candidate, availability_in_stock, jsonld_products, offer_of, parse_price
from .browser import BrowserFetcher

BASE = "https://www.virginmegastore.ae"


def parse_product_page(html: str, url: str) -> Candidate | None:
    products = jsonld_products(html)
    if not products:
        return None
    product = products[0]
    offer = offer_of(product)
    return Candidate(
        title=product.get("name") or "",
        isbn=product.get("gtin13") or product.get("isbn"),
        price_aed=parse_price(offer.get("price")),
        url=url,
        in_stock=availability_in_stock(offer.get("availability")),
    )


class VirginFetcher(BrowserFetcher):
    needs_link = True

    def fetch(self, book: dict, links: dict[str, str] | None = None):
        url = (links or {}).get(self.name)
        if not url:
            return []            # nothing to check until you paste a link
        from . import matching

        _, html = self.page_html(url)
        candidate = parse_product_page(html, url)
        if not candidate:
            return []
        # The link was chosen by you, so a title/author match is enough
        best = matching.pick_best([candidate], book, other_editions=True)
        return self.to_results(*best) if best else []

    def find_by_isbn(self, isbn13: str) -> list[Candidate]:
        return []
