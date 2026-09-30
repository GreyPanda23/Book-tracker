"""Placeholder for running a single store through Apify later (paid, optional).

Why this exists: if a store keeps blocking the built-in scraper, you can move
just that store to an Apify "actor" without touching anything else. Set
`backend: apify` (and e.g. `apify_actor: <actor id>`) for that store in
config/stores.yaml, put APIFY_TOKEN in `.env`, and implement `find_by_isbn`
below: call the actor with the ISBN, then map each result to a Candidate
(title, price_aed, url, in_stock, isbn). Matching, saving, history and the
dashboard all keep working unchanged, because every store returns Candidates.

Not implemented on purpose: you asked for no paid or third-party APIs.
"""

from __future__ import annotations

from .base import Candidate, FetchError, Fetcher


class ApifyFetcher(Fetcher):
    def find_by_isbn(self, isbn13: str) -> list[Candidate]:
        raise FetchError(f"{self.name}: backend 'apify' is only a placeholder - "
                         "set backend: scraper in config/stores.yaml")
