"""Headless Chromium (Playwright) for stores that need a real browser.

Only used when a store has no usable JSON/data endpoint. One browser per
store per run, closed afterwards. Same politeness rules as plain HTTP:
robots.txt first, random pauses, and block/CAPTCHA detection.

One-time setup on your Mac:  python -m playwright install chromium
"""

from __future__ import annotations

import logging
import os
import random

from .base import BROWSER_UA, BlockedError, FetchError, Fetcher, looks_blocked

log = logging.getLogger("booktracker.fetchers.browser")


class BrowserSession:
    def __init__(self, locale: str = "en-AE"):
        self.locale = locale
        self._pw = self._browser = self._page = None

    def page(self):
        if self._page is None:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            launch = {"headless": True}
            if os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"):
                launch["executable_path"] = os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE"]
            if os.environ.get("HTTPS_PROXY"):
                launch["proxy"] = {"server": os.environ["HTTPS_PROXY"]}
            try:
                self._browser = self._pw.chromium.launch(**launch)
            except Exception as exc:
                self.close()
                raise FetchError("Chromium not installed - run: python -m playwright install chromium"
                                 f" ({str(exc).splitlines()[0]})") from exc
            context = self._browser.new_context(
                user_agent=BROWSER_UA, locale=self.locale, timezone_id="Asia/Dubai",
                viewport={"width": 1366, "height": 900})
            self._page = context.new_page()
        return self._page

    def get(self, url: str, settle_ms: int = 2500, wait_for: str | None = None) -> tuple[int | None, str]:
        """Load a page; optionally wait (up to 15 s) until `wait_for` (CSS) appears."""
        page = self.page()
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            if wait_for:
                try:
                    page.wait_for_selector(wait_for, timeout=15_000)
                except Exception:
                    pass  # e.g. no results: the caller decides from the HTML
            page.wait_for_timeout(settle_ms + random.randint(0, 1500))
            return (response.status if response else None), page.content()
        except Exception as exc:
            raise FetchError(f"page load failed: {str(exc).splitlines()[0]}") from exc

    def text(self, url: str) -> tuple[int | None, str]:
        """Plain text of a URL (used for robots.txt)."""
        status, _ = self.get(url, settle_ms=0)
        return status, self.page().inner_text("body")

    def close(self) -> None:
        for obj, method in ((self._browser, "close"), (self._pw, "stop")):
            try:
                if obj:
                    getattr(obj, method)()
            except Exception:
                pass
        self._pw = self._browser = self._page = None


class BrowserFetcher(Fetcher):
    """Base for stores fetched with a headless browser."""

    uses_browser = True

    def __init__(self, cfg, client=None, session: BrowserSession | None = None):
        super().__init__(cfg, client)
        self.browser = session or BrowserSession()

    def page_html(self, url: str, wait_for: str | None = None) -> tuple[int | None, str]:
        """robots.txt check -> polite pause -> load page -> block detection."""
        self.http.check_allowed(url, fetch_text=self.browser.text)
        for attempt in (1, 2):
            self.http.wait_turn()
            try:
                status, html = self.browser.get(url, wait_for=wait_for)
            except FetchError:
                if attempt == 2:
                    raise
                log.info("[%s] page load failed, retrying once: %s", self.name, url)
                continue
            if status in (500, 502, 504) and attempt == 1:
                log.info("[%s] server error %s, retrying once", self.name, status)
                continue
            break
        if status in (500, 502, 504):
            raise FetchError(f"server error {status}")
        reason = looks_blocked(status, html) if status != 404 else None
        if reason:
            raise BlockedError(reason)
        return status, html

    def close(self) -> None:
        self.browser.close()
        super().close()
