"""Books Kinokuniya UAE (uae.kinokuniya.com) - The Dubai Mall + online.

Product pages are addressed by ISBN: /bw/<ISBN-13>. The site blocks cloud
servers, so this fetcher could not be tested live while it was built; run
`python -m scripts.check_stores kinokuniya` on your Mac to confirm it works.
Price/stock are read from schema.org data when present, otherwise from the page.
"""

from __future__ import annotations

import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from .base import Candidate, availability_in_stock, jsonld_products, offer_of, parse_price
from .browser import BrowserFetcher

BASE = "https://uae.kinokuniya.com"


def parse_product_page(html: str, url: str, isbn13: str | None = None) -> Candidate | None:
    soup = BeautifulSoup(html, "lxml")
    title, price, in_stock, isbn = "", None, None, None
    for product in jsonld_products(html):
        offer = offer_of(product)
        title = product.get("name") or title
        price = parse_price(offer.get("price")) or price
        in_stock = availability_in_stock(offer.get("availability"))
        isbn = product.get("isbn") or product.get("gtin13") or isbn
    if not title:
        el = soup.select_one("h1") or soup.select_one('meta[property="og:title"]')
        title = (el.get("content") if el and el.name == "meta" else el.get_text(" ", strip=True)) if el else ""
    if not price:
        meta = soup.select_one('meta[itemprop="price"], [itemprop="price"]')
        price = parse_price(meta.get("content") or meta.get_text()) if meta else None
    if not price:
        m = re.search(r"AED\s*([\d,]+(?:\.\d{1,2})?)", soup.get_text(" "))
        price = parse_price(m.group(1)) if m else None
    if in_stock is None:
        text = soup.get_text(" ").lower()
        if "out of stock" in text or "not available" in text:
            in_stock = False
        elif "in stock" in text or "add to cart" in text or "add to basket" in text:
            in_stock = True
    if not isbn and isbn13 and isbn13 in html:
        isbn = isbn13
    if not title or not price:
        return None
    return Candidate(title=title, isbn=isbn, price_aed=price, url=url, in_stock=in_stock)


class KinokuniyaFetcher(BrowserFetcher):
    def find_by_isbn(self, isbn13: str) -> list[Candidate]:
        url = f"{BASE}/bw/{isbn13}"
        status, html = self.page_html(url)
        if status == 404:
            return []
        c = parse_product_page(html, url, isbn13)
        return [c] if c else []

    def find_by_title(self, title: str, author: str | None) -> list[Candidate]:
        query = quote_plus(f"{title} {author or ''}".strip())
        _, html = self.page_html(f"{BASE}/products?utf8=%E2%9C%93&is_searching=true&keywords={query}")
        isbns = list(dict.fromkeys(re.findall(r"/bw/(97[89]\d{10})", html)))[:2]
        results = []
        for isbn in isbns:
            results += self.find_by_isbn(isbn)
        return results
