"""Amazon.ae - online only.

For books Amazon's product ID (ASIN) is the ISBN-10, so the product page is
opened directly: /dp/<ISBN-10>. Books with only an ISBN-13 (979-...) and the
title fallback use Amazon's book search (/s?...&i=stripbooks). Both paths are
allowed by robots.txt for general user agents.

Heads-up: Amazon's terms of use don't allow automated access and it shows a
CAPTCHA when it suspects a bot. When that happens the store is skipped for the
rest of the run and the reason is logged.
"""

from __future__ import annotations

import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from .. import isbn as isbn_utils
from .base import Candidate, parse_price
from .browser import BrowserFetcher

BASE = "https://www.amazon.ae"


def _text(soup, selector: str) -> str:
    el = soup.select_one(selector)
    return el.get_text(" ", strip=True) if el else ""


def parse_product_page(html: str, url: str) -> Candidate | None:
    soup = BeautifulSoup(html, "lxml")
    title = _text(soup, "#productTitle")
    if not title:
        return None
    price = None
    for sel in ("#corePriceDisplay_desktop_feature_div .priceToPay",
                "#corePrice_feature_div .a-offscreen", "#price",
                "#tmmSwatches .selected .slot-price", "#tmmSwatches .a-button-selected .a-color-price"):
        price = parse_price(_text(soup, sel))
        if price:
            break
    if not price:
        m = re.search(r'"priceAmount"\s*:\s*([0-9.]+)', html)
        price = float(m.group(1)) if m else None

    availability = _text(soup, "#availability").lower()
    if any(w in availability for w in ("unavailable", "out of stock")):
        in_stock = False
    elif any(w in availability for w in ("in stock", "left in stock", "dispatched", "ships")):
        in_stock = True
    else:
        in_stock = True if soup.select_one("#add-to-cart-button") else None

    isbn = None
    for li in soup.select("#detailBullets_feature_div li, #rpi-attribute-book_details-isbn13, "
                          "#rpi-attribute-book_details-isbn10, #productDetailsTable li"):
        text = li.get_text(" ", strip=True)
        if "ISBN" in text:
            m = re.search(r"97[89][\d-]{10,14}|\b\d{9}[\dX]\b", text)
            if m and (found := isbn_utils.normalize(m.group(0))):
                isbn = found
                if "13" in text:
                    break
    author = ", ".join(a.get_text(strip=True) for a in soup.select("#bylineInfo .author a.a-link-normal")) or None
    return Candidate(title=title, author=author, isbn=isbn, price_aed=price, url=url, in_stock=in_stock)


def search_result_urls(html: str, limit: int = 2) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    urls = []
    for card in soup.select('div[data-component-type="s-search-result"]'):
        link = card.select_one("a[href*='/dp/']")
        asin = card.get("data-asin")
        if asin:
            urls.append(f"{BASE}/dp/{asin}")
        elif link:
            urls.append(BASE + link["href"].split("?")[0] if link["href"].startswith("/") else link["href"])
        if len(urls) >= limit:
            break
    return urls


class AmazonAEFetcher(BrowserFetcher):
    def _product(self, url: str) -> list[Candidate]:
        status, html = self.page_html(url, wait_for="#productTitle")
        if status == 404:
            return []
        c = parse_product_page(html, url)
        return [c] if c else []

    def _search(self, query: str, limit: int) -> list[Candidate]:
        _, html = self.page_html(f"{BASE}/s?k={quote_plus(query)}&i=stripbooks",
                                 wait_for='div[data-component-type="s-search-result"]')
        results = []
        for url in search_result_urls(html, limit):
            results += self._product(url)
        return results

    def find_by_isbn(self, isbn13: str) -> list[Candidate]:
        isbn10 = isbn_utils.isbn13_to_10(isbn13)
        if isbn10:
            return self._product(f"{BASE}/dp/{isbn10}")
        return self._search(isbn13, 1)

    def find_by_title(self, title: str, author: str | None) -> list[Candidate]:
        return self._search(f"{title} {author or ''}".strip(), 2)
