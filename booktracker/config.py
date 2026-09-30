"""Central settings: file paths and secrets.

Secrets are read from (in order): real environment variables, a local `.env`
file, and Streamlit Cloud's "Secrets" box. Nothing secret is ever hardcoded.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
DB_PATH = Path(os.environ.get("BOOKTRACKER_DB", DATA_DIR / "books.db"))
STORES_CONFIG = ROOT / "config" / "stores.yaml"

load_dotenv(ROOT / ".env")


def get_secret(name: str, default: str | None = None) -> str | None:
    """Return a secret from the environment, `.env` or Streamlit secrets."""
    value = os.environ.get(name)
    if value:
        return value
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:  # no Streamlit, or no secrets.toml
        pass
    return default


def setup_logging(name: str = "booktracker") -> logging.Logger:
    """Log to the console and to logs/<name>.log."""
    LOG_DIR.mkdir(exist_ok=True)
    logger = logging.getLogger("booktracker")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        for handler in (logging.StreamHandler(), logging.FileHandler(LOG_DIR / f"{name}.log")):
            handler.setFormatter(fmt)
            logger.addHandler(handler)
    return logger
