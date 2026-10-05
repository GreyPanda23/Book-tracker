"""Helpers shared by every Streamlit page: database access, saving, login."""

from __future__ import annotations

import hmac
import sqlite3
from typing import Callable

import streamlit as st

from . import config, db, sync

STATUS_ICONS = {"To Read": "📖", "Currently Reading": "📘", "Read": "✅", "Dropped": "🚫"}
PLACEHOLDER_COVER = "https://placehold.co/128x192?text=No+cover"


@st.cache_data(ttl=30, show_spinner=False)
def _refresh_from_github() -> str:
    """At most every 30 s, download the database if GitHub has a newer copy."""
    try:
        return "updated" if sync.refresh_if_changed() else "current"
    except Exception as exc:
        return f"error: {exc}"


def get_conn() -> sqlite3.Connection:
    """A fresh connection to the (up-to-date) database for this page run."""
    if sync.enabled():
        state = _refresh_from_github()
        if state.startswith("error"):
            st.warning(f"Couldn't reach GitHub, showing the last saved copy ({state[7:80]}).")
    return db.connect()


def save(change: Callable[[sqlite3.Connection], object], message: str) -> bool:
    """Run a database change and upload it to GitHub (if sync is set up).

    Returns True on success; shows the problem and returns False otherwise.
    """
    try:
        with st.spinner("Saving…"):
            sync.write(change, message=message)
        _refresh_from_github.clear()
        return True
    except sync.SyncError as exc:
        st.error(f"Saved on this device, but not uploaded to GitHub: {exc}")
    except ValueError as exc:
        st.error(str(exc))
    except Exception as exc:  # network trouble etc. - never crash the page
        st.error(f"Something went wrong while saving: {exc}")
    return False


def check_password() -> bool:
    """Simple password gate when APP_PASSWORD is set (recommended online)."""
    expected = config.get_secret("APP_PASSWORD")
    if not expected or st.session_state.get("authenticated"):
        return True
    st.title("📚 My Book Tracker")
    pw = st.text_input("Password", type="password")
    if pw:
        if hmac.compare_digest(pw, expected):
            st.session_state["authenticated"] = True
            st.rerun()
        st.error("Wrong password")
    return False


def is_dark() -> bool:
    """True when the app is showing in dark mode."""
    try:
        return st.context.theme.type == "dark"
    except Exception:
        return False


def cover(url: str | None, width: int = 90) -> None:
    st.image(url or PLACEHOLDER_COVER, width=width)


def stars(rating: int | None) -> str:
    return "★" * rating + "☆" * (5 - rating) if rating else ""
