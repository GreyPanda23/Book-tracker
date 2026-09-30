"""Noon (noon.com/uae-en) - online only.

Noon's internal API (/_svc/) is disallowed by robots.txt, so this reads the
normal search page and product pages in a headless browser. Prices come from
the schema.org data embedded in each product page; the ISBN is confirmed by
finding it on the product page (in the specifications).
"""

from __future__ import annotations

import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from .base import Candidate, availability_in_stock, jsonld_products, offer_of, parse_price
from .browser import BrowserFetcher

BASE = "https://www.noon.com"


def search_result_urls(html: str, limit: int = 2) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    urls: list[str] = []
    for a in soup.select('a[href*="/p/"]'):
        path = a["href"].split("?")[0]
        url = path if path.startswith("http") else BASE + path
        if url not in urls:
            urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def parse_product_page(html: str, url: str, isbn13: str | None) -> Candidate | None:
    products = jsonld_products(html)
    if not products:
        return None
    product = products[0]
    offer = offer_of(product)
    isbns = set(re.findall(r"97[89]\d{10}", html))
    found_isbn = isbn13 if isbn13 in isbns else (next(iter(isbns)) if len(isbns) == 1 else None)
    return Candidate(
        title=product.get("name") or "",
        isbn=found_isbn,
        price_aed=parse_price(offer.get("price")),
        url=url,
        in_stock=availability_in_stock(offer.get("availability")),
    )


class NoonFetcher(BrowserFetcher):
    def _search(self, query: str, isbn13: str | None, limit: int) -> list[Candidate]:
        _, html = self.page_html(f"{BASE}/uae-en/search/?q={quote_plus(query)}",
                                 wait_for='a[href*="/p/"]')
        results = []
        for url in search_result_urls(html, limit):
            _, page = self.page_html(url, wait_for='script[type="application/ld+json"]')
            if c := parse_product_page(page, url, isbn13):
                results.append(c)
        return results

    def find_by_isbn(self, isbn13: str) -> list[Candidate]:
        return self._search(isbn13, isbn13, 2)

    def find_by_title(self, title: str, author: str | None) -> list[Candidate]:
        return self._search(f"{title} {author or ''}".strip(), None, 2)
