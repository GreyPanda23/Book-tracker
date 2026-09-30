"""Load config/stores.yaml and build one fetcher per enabled store.

To move a single store to Apify later, set `backend: apify` for it in
stores.yaml - nothing else changes (see apify_stub.py).
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path

import yaml

from .. import config
from .base import Fetcher, StoreConfig

log = logging.getLogger("booktracker.fetchers")

FETCHER_CLASSES = {
    "magrudys": "MagrudysFetcher",
    "jashanmal": "JashanmalFetcher",
    "kinokuniya": "KinokuniyaFetcher",
    "amazon_ae": "AmazonAEFetcher",
    "noon": "NoonFetcher",
    "virgin": "VirginFetcher",
}


def load_store_configs(path: Path | None = None) -> list[StoreConfig]:
    data = yaml.safe_load(Path(path or config.STORES_CONFIG).read_text()) or {}
    fields = set(StoreConfig.__dataclass_fields__)
    return [StoreConfig(**{k: v for k, v in s.items() if k in fields}) for s in data.get("stores", [])]


def build_fetcher(cfg: StoreConfig) -> Fetcher:
    if cfg.backend == "apify":
        from .apify_stub import ApifyFetcher
        return ApifyFetcher(cfg)
    module = importlib.import_module(f"{__package__}.{cfg.module}")
    return getattr(module, FETCHER_CLASSES[cfg.module])(cfg)


def enabled_fetchers(only: list[str] | None = None, path: Path | None = None) -> list[Fetcher]:
    """Fetchers for enabled stores (optionally only the named ones)."""
    wanted = {o.lower() for o in only} if only else None
    fetchers = []
    for cfg in load_store_configs(path):
        if not cfg.enabled or (wanted and cfg.name.lower() not in wanted and cfg.module not in wanted):
            continue
        try:
            fetchers.append(build_fetcher(cfg))
        except Exception as exc:  # a broken store module must not stop the others
            log.error("could not load store %s: %s", cfg.name, exc)
    return fetchers
