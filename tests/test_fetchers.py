"""Offline tests for the price fetchers (saved real pages + fake store servers)."""

import json
from pathlib import Path

import httpx
import pytest

from booktracker.fetchers import (amazon_ae, jashanmal, kinokuniya, magrudys, matching, noon,
                                  registry, virgin)
from booktracker.fetchers.base import (BlockedError, Candidate, FetchError, Fetcher, PoliteClient,
                                       PriceResult, RobotsDisallowed, StoreConfig, jsonld_products,
                                       looks_blocked, offer_of, parse_price)
from booktracker.fetchers.browser import BrowserFetcher
from booktracker.prices import collect_prices

FIX = Path(__file__).parent / "fixtures"
ATOMIC = {"id": 1, "title": "Atomic Habits", "author": "James Clear", "isbn": "9781847941831"}
HAIL_MARY = {"id": 2, "title": "Project Hail Mary", "author": "Andy Weir", "isbn": "9780593395561"}


def fixture(name: str) -> str:
    return (FIX / name).read_text()


def client_for(handler, **kw) -> PoliteClient:
    return PoliteClient("Test", 0, 0, transport=httpx.MockTransport(handler), sleep=lambda s: None, **kw)


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def test_parse_price():
    assert parse_price("AED\xa01,234.50") == 1234.5
    assert parse_price("د.إ 55") == 55.0
    assert parse_price(48.77) == 48.77
    assert parse_price("free") is None and parse_price(None) is None


def test_block_detection_has_no_false_positive_on_normal_pages():
    normal = '<script src="https://www.google.com/recaptcha/api.js"></script><h1>Book</h1>'
    assert looks_blocked(200, normal) is None           # the word "captcha" alone is not a block
    assert looks_blocked(200, fixture("virgin_product.html")) is None
    assert looks_blocked(200, fixture("amazon_product.html")) is None
    assert looks_blocked(200, fixture("noon_product.html")) is None


@pytest.mark.parametrize("html,status", [
    ("<form action='/errors/validateCaptcha'>Enter the characters you see below</form>", 200),
    ("<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD></HTML>", 200),
    ("<title>Just a moment...</title>", 200),
    ("ok", 429), ("ok", 403), ("ok", 503),
])
def test_block_detection(html, status):
    assert looks_blocked(status, html)


def test_jsonld_and_offer():
    html = fixture("virgin_product.html")
    product = jsonld_products(html)[0]
    assert offer_of(product)["price"] == "89.0"


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
def test_matching_rules():
    same = Candidate("Atomic Habits", 50, "u", isbn="1847941834")           # ISBN-10 of the same book
    other_ed = Candidate("Atomic Habits", 40, "u", isbn="9780735211292")    # US hardback
    journal = Candidate("Atomic Habits Journal", 20, "u")
    no_isbn = Candidate("Atomic Habits: Tiny Changes", 45, "u", author="Clear, James")
    wrong_author = Candidate("Atomic Habits", 45, "u", author="Someone Else")
    assert matching.verify(same, ATOMIC) == "isbn"
    assert matching.verify(other_ed, ATOMIC) is None
    assert matching.verify(other_ed, ATOMIC, other_editions=True) == "title_author"
    assert matching.verify(journal, ATOMIC) is None
    assert matching.verify(no_isbn, ATOMIC) == "title_author"
    assert matching.verify(wrong_author, ATOMIC) is None


def test_matching_non_latin_and_accents():
    arabic = "هدي عواطفك تغلب علي مخك"
    assert matching.title_matches(arabic + ": طبعة جديدة", arabic)
    assert matching.title_matches("Les Misérables", "Les Miserables")


def test_pick_best_prefers_isbn_then_price_and_skips_missing_price():
    cands = [Candidate("Atomic Habits", 30, "a", author="James Clear"),
             Candidate("Atomic Habits", 60, "b", isbn="9781847941831"),
             Candidate("Atomic Habits", None, "c", isbn="9781847941831")]
    best, method = matching.pick_best(cands, ATOMIC)
    assert (best.url, method) == ("b", "isbn")


# --------------------------------------------------------------------------- #
# Polite client
# --------------------------------------------------------------------------- #
def test_robots_disallow_is_respected():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /en/search")
        return httpx.Response(200, text="should not be fetched")
    c = client_for(handler)
    with pytest.raises(RobotsDisallowed):
        c.get("https://shop.test/en/search?q=1")
    assert c.get("https://shop.test/en/product/1").text == "should not be fetched"


def test_missing_robots_means_allowed_and_rate_limit_is_retried():
    calls = {"n": 0}
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "5"})
        return httpx.Response(200, json={"ok": True})
    slept = []
    c = PoliteClient("T", 0, 0, transport=httpx.MockTransport(handler), sleep=slept.append)
    assert c.get("https://shop.test/x").json() == {"ok": True}
    assert 5.0 in slept


def test_block_raises_blocked_error():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text="<p>Pardon Our Interruption</p>")
    with pytest.raises(BlockedError):
        client_for(handler).get("https://shop.test/p")


def test_polite_delay_between_requests():
    slept = []
    c = PoliteClient("T", 3, 3, transport=httpx.MockTransport(lambda r: httpx.Response(200, text="")),
                     sleep=slept.append)
    c.get("https://shop.test/a")
    c.get("https://shop.test/b")
    assert slept and 2.5 < slept[-1] <= 3.0


# --------------------------------------------------------------------------- #
# Magrudy's (JSON API)
# --------------------------------------------------------------------------- #
def magrudys_handler(req):
    path = req.url.path
    if path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nAllow: /")
    if path == "/api/item/get/9781847941831":
        return httpx.Response(200, json=json.loads(fixture("magrudys_item.json")))
    if path.startswith("/api/item/get/"):
        return httpx.Response(200, text="null", headers={"content-type": "application/json"})
    if path == "/api/Store":
        return httpx.Response(200, json=json.loads(fixture("magrudys_stores.json")))
    if path == "/api/search/do-search":
        return httpx.Response(200, json={"data": [], "count": 0})
    return httpx.Response(404)


def test_magrudys_parse_and_branches():
    c = magrudys.parse_item(json.loads(fixture("magrudys_item.json")))
    assert (c.price_aed, c.isbn, c.in_stock, c.store_stock) == (95.0, "9781847941831", True, True)
    branches = magrudys.dubai_bookshops(json.loads(fixture("magrudys_stores.json")))
    assert "Mirdif City Center" in branches and all("Abu Dhabi" not in b for b in branches)
    assert "Al Wahda Mall" not in branches   # Abu Dhabi branch excluded


def test_magrudys_fetch_end_to_end():
    cfg = StoreConfig(name="Magrudy's", module="magrudys", physical=True)
    f = magrudys.MagrudysFetcher(cfg, client_for(magrudys_handler))
    rows = f.fetch(ATOMIC)
    assert {r.type for r in rows} == {"online", "in-store"}
    store_row = next(r for r in rows if r.type == "in-store")
    assert store_row.price_aed == 95.0 and "Mirdif City Center" in store_row.branch_location
    assert f.fetch(HAIL_MARY) == []   # not in this fake shop


# --------------------------------------------------------------------------- #
# Jashanmal (Shopify JSON)
# --------------------------------------------------------------------------- #
def jashanmal_handler(req):
    path = req.url.path
    if path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nDisallow: /checkout")
    if path == "/search/suggest.json":
        q = req.url.params["q"]
        products = [{"title": "Atomic Habits", "handle": "atomic-habits", "price": "52.00",
                     "available": True, "type": "Books", "url": "/products/atomic-habits?_pos=1"},
                    {"title": "Atomic Habits Mug", "handle": "mug", "price": "30.00",
                     "available": True, "type": "Home"}]
        return httpx.Response(200, json={"resources": {"results": {"products":
                                                                    products if "tomic" in q or q == "9781847941831" else []}}})
    if path == "/products/atomic-habits.js":
        return httpx.Response(200, json={"title": "Atomic Habits", "handle": "atomic-habits", "type": "Books",
                                         "available": True, "price": 5200,
                                         "variants": [{"barcode": "9781847941831", "price": 5200, "available": True}]})
    return httpx.Response(404)


def test_jashanmal_fetch():
    cfg = StoreConfig(name="Jashanmal", module="jashanmal", physical=True, branches=["Dubai Mall"])
    f = jashanmal.JashanmalFetcher(cfg, client_for(jashanmal_handler))
    rows = f.fetch(ATOMIC)
    assert [(r.type, r.price_aed, r.match_method) for r in rows] == [
        ("online", 52.0, "isbn"), ("in-store", 52.0, "isbn")]
    assert rows[1].branch_location == "Dubai Mall"
    assert f.fetch(HAIL_MARY) == []


# --------------------------------------------------------------------------- #
# Browser-based stores (parsers on saved real pages + a fake browser)
# --------------------------------------------------------------------------- #
def test_amazon_product_page():
    c = amazon_ae.parse_product_page(fixture("amazon_product.html"), "https://www.amazon.ae/dp/1847941834")
    assert (c.price_aed, c.isbn, c.in_stock) == (55.0, "9781847941831", True)
    assert "Atomic Habits" in c.title and "Clear" in (c.author or "")


def test_amazon_search_results():
    html = ('<div data-component-type="s-search-result" data-asin="0593395565"><a href="/x/dp/0593395565">x</a></div>'
            '<div data-component-type="s-search-result" data-asin="B0CXYZ"><a href="/dp/B0CXYZ">y</a></div>')
    assert amazon_ae.search_result_urls(html, 2) == ["https://www.amazon.ae/dp/0593395565",
                                                     "https://www.amazon.ae/dp/B0CXYZ"]


def test_noon_pages():
    urls = noon.search_result_urls(fixture("noon_search.html"), 2)
    assert len(urls) == 2 and all("/p/" in u and "?" not in u for u in urls)
    c = noon.parse_product_page(fixture("noon_product.html"), urls[1], "9781847941831")
    assert (c.price_aed, c.isbn, c.in_stock) == (48.77, "9781847941831", True)


def test_virgin_page():
    c = virgin.parse_product_page(fixture("virgin_product.html"), "https://www.virginmegastore.ae/p/1")
    assert c.price_aed == 89.0 and c.in_stock is True


def test_kinokuniya_parsers():
    ld = ('<script type="application/ld+json">{"@type":"Product","name":"Dune","isbn":"9780441172719",'
          '"offers":{"@type":"Offer","price":"61.00","priceCurrency":"AED",'
          '"availability":"https://schema.org/InStock"}}</script>')
    c = kinokuniya.parse_product_page(ld, "u")
    assert (c.title, c.price_aed, c.isbn, c.in_stock) == ("Dune", 61.0, "9780441172719", True)
    plain = "<h1>Dune</h1><div>Price: AED 58.50</div><p>Out of stock</p><span>9780441172719</span>"
    c = kinokuniya.parse_product_page(plain, "u", "9780441172719")
    assert (c.price_aed, c.in_stock, c.isbn) == (58.5, False, "9780441172719")


class FakeBrowser:
    """Stands in for Playwright: returns saved pages by URL."""
    def __init__(self, pages: dict[str, tuple[int, str]]):
        self.pages, self.visited = pages, []

    def get(self, url, settle_ms=0, wait_for=None):
        self.visited.append(url)
        for key, value in self.pages.items():
            if key in url:
                return value
        return 404, "<html>not found</html>"

    def text(self, url):
        return 200, "User-agent: *\nDisallow: /_svc/\nAllow: /"

    def close(self):
        pass


def browser_fetcher(cls, pages, **cfg):
    config = StoreConfig(name=cls.__name__, module="x", **cfg)
    return cls(config, client_for(lambda r: httpx.Response(200)), session=FakeBrowser(pages))


def test_amazon_fetch_with_fake_browser():
    f = browser_fetcher(amazon_ae.AmazonAEFetcher, {"/dp/1847941834": (200, fixture("amazon_product.html"))})
    rows = f.fetch(ATOMIC)
    assert [(r.type, r.price_aed, r.match_method) for r in rows] == [("online", 55.0, "isbn")]
    assert f.browser.visited == ["https://www.amazon.ae/dp/1847941834"]


def test_noon_fetch_with_fake_browser():
    f = browser_fetcher(noon.NoonFetcher, {"/search/": (200, fixture("noon_search.html")),
                                           "/p/": (200, fixture("noon_product.html"))})
    rows = f.fetch(ATOMIC)
    assert rows and rows[0].price_aed == 48.77 and rows[0].match_method == "isbn"


def test_captcha_page_raises_blocked():
    captcha = "<title>Robot Check</title><form action='/errors/validateCaptcha'></form>"
    f = browser_fetcher(amazon_ae.AmazonAEFetcher, {"/dp/": (200, captcha)})
    with pytest.raises(BlockedError):
        f.fetch(ATOMIC)


def test_virgin_uses_pasted_link_only():
    f = browser_fetcher(virgin.VirginFetcher, {"/p/1": (200, fixture("virgin_product.html"))},
                        physical=True, branches=["The Dubai Mall"])
    assert f.fetch(ATOMIC, {}) == []               # no link -> nothing fetched
    assert f.browser.visited == []
    book = {"id": 3, "title": "هدي عواطفك تغلب علي مخك القلق والسلبي والمتشائم واعثر علي التوازن والمرونة والهدوء",
            "author": None, "isbn": None}
    rows = f.fetch(book, {f.name: "https://www.virginmegastore.ae/p/1"})
    assert {r.type for r in rows} == {"online", "in-store"} and rows[0].price_aed == 89.0


# --------------------------------------------------------------------------- #
# Runner: one broken store never stops the others
# --------------------------------------------------------------------------- #
class GoodStore(Fetcher):
    def find_by_isbn(self, isbn13):
        return [Candidate("Atomic Habits", 50.0, "https://good/1", True, isbn13)]


class BlockedStore(Fetcher):
    calls = 0

    def find_by_isbn(self, isbn13):
        BlockedStore.calls += 1
        raise BlockedError("CAPTCHA")


class BuggyStore(Fetcher):
    def find_by_isbn(self, isbn13):
        raise KeyError("oops")


class NotFoundStore(Fetcher):
    def find_by_isbn(self, isbn13):
        return []


def make(cls, name):
    return cls(StoreConfig(name=name, module="x"), client_for(lambda r: httpx.Response(200)))


@pytest.mark.parametrize("parallel", [True, False])
def test_runner_isolates_failures(parallel):
    BlockedStore.calls = 0
    books = [ATOMIC, {**ATOMIC, "id": 5, "title": "Atomic Habits (2)"}]
    out = collect_prices(books, [make(GoodStore, "Good"), make(BlockedStore, "Blocked"),
                                 make(BuggyStore, "Buggy"), make(NotFoundStore, "Missing")], parallel=parallel)
    assert [r["store_name"] for r in out.rows] == ["Good", "Good"]
    assert BlockedStore.calls == 1                      # stopped after the first block
    levels = {(e["store_name"], e["level"]) for e in out.logs}
    assert ("Blocked", "blocked") in levels and ("Buggy", "error") in levels and ("Missing", "not_found") in levels
    assert out.errors == 3                              # 1 blocked + 2 buggy


def test_price_result_row():
    row = PriceResult("S", "online", 10.0, True, "u", "isbn").as_db_row(7)
    assert row["book_id"] == 7 and row["store_name"] == "S"


# --------------------------------------------------------------------------- #
# Registry / config
# --------------------------------------------------------------------------- #
def test_registry_loads_all_stores():
    cfgs = registry.load_store_configs()
    assert {c.module for c in cfgs} == set(registry.FETCHER_CLASSES)
    fetchers = registry.enabled_fetchers()
    try:
        assert {f.name for f in fetchers} >= {"Magrudy's", "Jashanmal", "Amazon.ae", "Noon"}
        assert all(isinstance(f, BrowserFetcher) == f.uses_browser for f in fetchers)
    finally:
        for f in fetchers:
            f.close()


def test_apify_backend_swaps_one_store(tmp_path):
    cfg_file = tmp_path / "stores.yaml"
    cfg_file.write_text("stores:\n  - {name: Noon, module: noon, backend: apify}\n"
                        "  - {name: Magrudy's, module: magrudys}\n  - {name: Broken, module: nope}\n")
    fetchers = registry.enabled_fetchers(path=cfg_file)
    assert [type(f).__name__ for f in fetchers] == ["ApifyFetcher", "MagrudysFetcher"]  # Broken skipped
    with pytest.raises(FetchError):
        fetchers[0].fetch(ATOMIC)
