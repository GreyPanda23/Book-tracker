"""Shared machinery for every store fetcher.

A store fetcher only has to answer "what does this store have for this ISBN?"
(and optionally "...for this title + author?"). Everything else lives here:

- polite HTTP: realistic browser headers, robots.txt check before every URL,
  random pauses between requests, timeouts, one retry on server errors
- CAPTCHA / block-page detection (the store is then skipped for the rest of the run)
- match verification (ISBN first, else title + author) before anything is saved
- turning one product into an "online" row and/or an "in-store (Dubai)" row
"""

from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
from protego import Protego

from . import matching

log = logging.getLogger("booktracker.fetchers")

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
BROWSER_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AE,en;q=0.9",
}

# Phrases that only appear on "prove you're human" / blocked pages.
# (The bare word "captcha" is NOT used: many normal pages contain it.)
BLOCK_MARKERS = [
    "validatecaptcha", "enter the characters you see below", "api-services-support@amazon.com",
    "pardon our interruption", "are you a robot", "px-captcha", "cf-chl-",
    "request unsuccessful. incapsula", "unusual traffic from your", "<title>access denied</title>",
    "<title>robot check</title>", "<title>just a moment...</title>", "<title>403 forbidden</title>",
]
BLOCK_STATUSES = {403, 429, 503}


# --------------------------------------------------------------------------- #
# Results & errors
# --------------------------------------------------------------------------- #
@dataclass
class Candidate:
    """A product found on a store's site (before verification)."""
    title: str
    price_aed: float | None
    url: str
    in_stock: bool | None = None
    isbn: str | None = None
    author: str | None = None
    store_stock: bool | None = None   # stock in the physical shops, if the site says


@dataclass
class PriceResult:
    """One row for the `prices` table."""
    store_name: str
    type: str                          # "online" / "in-store"
    price_aed: float | None
    in_stock: bool | None
    product_url: str
    match_method: str                  # "isbn" / "title_author"
    branch_location: str | None = None

    def as_db_row(self, book_id: int) -> dict:
        return dict(book_id=book_id, store_name=self.store_name, type=self.type,
                    price_aed=self.price_aed, in_stock=self.in_stock,
                    product_url=self.product_url, match_method=self.match_method,
                    branch_location=self.branch_location)


class FetchError(Exception):
    """Something went wrong for this book (logged; other books continue)."""


class BlockedError(FetchError):
    """CAPTCHA / bot wall / rate limit: stop using this store for this run."""


class RobotsDisallowed(FetchError):
    """robots.txt does not allow this URL, so we don't fetch it."""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def looks_blocked(status: int | None, html: str | None) -> str | None:
    """Return a reason if the response is a block/CAPTCHA page, else None."""
    if status in BLOCK_STATUSES:
        return f"HTTP {status}"
    text = (html or "")[:200_000].lower()
    for marker in BLOCK_MARKERS:
        if marker in text:
            return f"block page detected ({marker.strip('<>/')})"
    return None


def parse_price(text: str | float | int | None) -> float | None:
    """'AED 1,234.50' / 'د.إ 55' / 55 -> 1234.5 / 55.0 (None if no number)."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(text).replace("\xa0", " "))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def jsonld_products(html: str) -> list[dict]:
    """All schema.org Product objects embedded in a page (JSON-LD)."""
    found = []
    for raw in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(raw.strip())
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if item.get("@type") in ("Product", "Book") or "Product" in (item.get("@type") or []):
                    found.append(item)
                stack.extend(v for k, v in item.items() if k == "@graph" and isinstance(v, list))
    return found


def offer_of(product: dict) -> dict:
    offers = product.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if offers.get("@type") == "AggregateOffer" and "lowPrice" in offers:
        offers = {**offers, "price": offers["lowPrice"]}
    if "price" not in offers and isinstance(offers.get("priceSpecification"), dict):
        offers = {**offers, "price": offers["priceSpecification"].get("price")}
    return offers


def availability_in_stock(value: str | None) -> bool | None:
    if not value:
        return None
    v = value.lower()
    if "instock" in v or "limitedavailability" in v or "onlineonly" in v:
        return True
    if "outofstock" in v or "soldout" in v or "discontinued" in v or "preorder" in v:
        return False
    return None


# --------------------------------------------------------------------------- #
# Polite HTTP client
# --------------------------------------------------------------------------- #
class PoliteClient:
    """HTTP client for one store: robots.txt, delays, block detection."""

    def __init__(self, store: str, min_delay: float = 3.0, max_delay: float = 8.0,
                 timeout: float = 30.0, transport: httpx.BaseTransport | None = None,
                 sleep=time.sleep):
        self.store = store
        self.min_delay, self.max_delay = min_delay, max_delay
        self._sleep = sleep
        self._last_request = 0.0
        self._robots: dict[str, Protego | None] = {}
        self._lock = threading.Lock()
        self.client = httpx.Client(headers=BROWSER_HEADERS, timeout=timeout,
                                   follow_redirects=True, transport=transport)

    def close(self) -> None:
        self.client.close()

    # -- politeness ---------------------------------------------------------
    def wait_turn(self) -> None:
        """Pause a random 3-8 s (configurable) since the previous request."""
        with self._lock:
            gap = random.uniform(self.min_delay, self.max_delay)
            remaining = self._last_request + gap - time.monotonic()
            if remaining > 0 and self._last_request:
                self._sleep(remaining)
            self._last_request = time.monotonic()

    def robots_for(self, url: str, fetch_text=None) -> Protego | None:
        origin = "{0.scheme}://{0.netloc}".format(urlparse(url))
        if origin not in self._robots:
            parser = None
            try:
                if fetch_text:
                    status, body = fetch_text(origin + "/robots.txt")
                else:
                    r = self.client.get(origin + "/robots.txt")
                    status, body = r.status_code, r.text
                if status == 200:
                    parser = Protego.parse(body)
                elif 400 <= status < 500:
                    parser = None            # no robots.txt: everything allowed (RFC 9309)
                else:
                    parser = Protego.parse("User-agent: *\nDisallow: /")  # server error: be safe
            except Exception as exc:
                log.warning("[%s] robots.txt unreachable (%s); not fetching", self.store, exc)
                parser = Protego.parse("User-agent: *\nDisallow: /")
            self._robots[origin] = parser
        return self._robots[origin]

    def check_allowed(self, url: str, fetch_text=None) -> None:
        robots = self.robots_for(url, fetch_text)
        if robots is not None and not robots.can_fetch(url, BROWSER_UA):
            raise RobotsDisallowed(f"robots.txt disallows {urlparse(url).path}")

    # -- requests -----------------------------------------------------------
    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        self.check_allowed(url)
        for attempt in (1, 2):
            self.wait_turn()
            try:
                r = self.client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise FetchError(f"network error: {exc}") from exc
                continue
            if r.status_code == 429 and attempt == 1:
                wait = parse_price(r.headers.get("retry-after")) or 30
                if wait <= 90:  # the site asked us to slow down: do so once
                    log.info("[%s] rate limited; waiting %ss as asked", self.store, int(wait))
                    self._sleep(wait)
                    continue
            reason = looks_blocked(r.status_code, r.text if "html" in r.headers.get("content-type", "") else "")
            if reason:
                raise BlockedError(reason)
            if r.status_code >= 500 and attempt == 1:
                continue
            return r
        raise FetchError(f"HTTP {r.status_code}")

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> httpx.Response:
        return self.request("POST", url, **kwargs)


# --------------------------------------------------------------------------- #
# Fetcher base class
# --------------------------------------------------------------------------- #
@dataclass
class StoreConfig:
    name: str
    module: str
    online: bool = True
    physical: bool = False
    branches: list[str] = field(default_factory=list)
    enabled: bool = True
    backend: str = "scraper"
    min_delay: float = 3.0
    max_delay: float = 8.0
    notes: str = ""


class Fetcher:
    """Base class. Subclasses implement `find_by_isbn` (and ideally `find_by_title`)."""

    uses_browser = False

    def __init__(self, cfg: StoreConfig, client: PoliteClient | None = None):
        self.cfg = cfg
        self.name = cfg.name
        self.http = client or PoliteClient(cfg.name, cfg.min_delay, cfg.max_delay)

    # ---- to implement per store ------------------------------------------
    def find_by_isbn(self, isbn13: str) -> list[Candidate]:
        raise NotImplementedError

    def find_by_title(self, title: str, author: str | None) -> list[Candidate]:
        return []

    def branches(self) -> list[str]:
        """Dubai branch names for in-store rows (config list by default)."""
        return self.cfg.branches

    def close(self) -> None:
        self.http.close()

    # ---- shared logic -----------------------------------------------------
    def fetch(self, book: dict, links: dict[str, str] | None = None) -> list[PriceResult]:
        """Find, verify and return price rows for one book ([] if not sold here)."""
        candidates: list[Candidate] = []
        if book.get("isbn"):
            candidates = self.find_by_isbn(book["isbn"])
        best = matching.pick_best(candidates, book)
        if not best and book.get("title"):
            best = matching.pick_best(self.find_by_title(book["title"], book.get("author")), book,
                                      other_editions=True)
        if not best:
            return []
        candidate, method = best
        return self.to_results(candidate, method)

    def to_results(self, c: Candidate, method: str) -> list[PriceResult]:
        rows = []
        if self.cfg.online:
            rows.append(PriceResult(self.name, "online", c.price_aed, c.in_stock, c.url, method))
        if self.cfg.physical and self.branches():
            store_stock = c.store_stock if c.store_stock is not None else c.in_stock
            rows.append(PriceResult(self.name, "in-store", c.price_aed, store_stock, c.url, method,
                                    branch_location="; ".join(self.branches())))
        return rows
