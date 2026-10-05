"""Helpers shared by every Streamlit page: database access, saving, login."""

from __future__ import annotations

import hmac
import sqlite3
from typing import Callable

import streamlit as st

from . import config, db, sync
from .book_search import (BookResult, SORT_OPTIONS, author_counts, filter_and_sort,
                          year_bounds)

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


_FILTER_WIDGETS = ("years", "sort", "authors", "rating", "unknown", "owned")


def _clear_filters(key: str) -> None:
    """Put every filter control of one results list back to its starting value."""
    for name in _FILTER_WIDGETS:
        st.session_state.pop(f"{key}_{name}", None)


def result_filters(results: list[BookResult], key: str) -> list[tuple[int, BookResult]]:
    """Filter/sort controls for a list of search results.

    Returns (original position, book) pairs that pass the filters, in the chosen
    order. The original position keeps each card's buttons stable while the
    list is being re-ordered. `key` must change for every new search so the
    controls reset to the new results.
    """
    shown = list(enumerate(results))
    if len(results) < 2:
        return shown
    bounds = year_bounds(results)
    authors = author_counts(results)
    has_ratings = any(b.rating for b in results)
    with st.expander("Filters & sorting", expanded=True):
        c1, c2 = st.columns([3, 2])
        years = bounds or (None, None)
        if bounds and bounds[0] < bounds[1]:
            years = c1.slider("Released between", bounds[0], bounds[1], bounds, key=f"{key}_years")
        sort = c2.selectbox("Sort by", SORT_OPTIONS, key=f"{key}_sort")
        picked: list[str] = []
        if len(authors) > 1:
            counts = dict(authors)
            picked = st.multiselect("Only these authors", list(counts), key=f"{key}_authors",
                                    format_func=lambda a: f"{a} ({counts[a]})",
                                    placeholder="All authors")
        c3, c4, c5 = st.columns(3)
        min_rating = c3.slider("Minimum rating ★", 0.0, 5.0, 0.0, 0.5, key=f"{key}_rating",
                               help="Books with no rating are hidden when this is above 0") \
            if has_ratings else 0.0
        keep_unknown = c4.checkbox("Include books with no year", value=True, key=f"{key}_unknown")
        hide_owned = c5.checkbox("Hide books I already have", value=False, key=f"{key}_owned")
        active = (bool(bounds) and tuple(years) != tuple(bounds)) or sort != SORT_OPTIONS[0] \
            or bool(picked) or min_rating > 0 or not keep_unknown or hide_owned
        st.button("✖ Clear all filters", key=f"{key}_clear", disabled=not active,
                  on_click=_clear_filters, args=(key,))
    owned: set[str] = set()
    if hide_owned:
        conn = get_conn()
        owned = {b["isbn"] for b in db.list_books(conn) if b["isbn"]}
        conn.close()
    kept = filter_and_sort(results, years[0], years[1], sort, keep_unknown, picked, min_rating, owned)
    position = {id(b): i for i, b in shown}
    shown = [(position[id(b)], b) for b in kept]
    st.caption(f"Showing {len(shown)} of {len(results)} books")
    return shown


def result_cards(shown: list[tuple[int, BookResult]], key: str) -> None:
    """One card per search result with a status picker and an Add button."""
    conn = get_conn()
    try:
        for i, book in shown:
            with st.container(border=True):
                left, right = st.columns([1, 4])
                with left:
                    cover(book.cover_url, width=80)
                with right:
                    st.markdown(f"**{book.title}**")
                    rating = f"★ {book.rating:.1f}" if book.rating else None
                    st.caption(" · ".join(filter(None, [
                        book.author, book.year, book.genre, rating,
                        f"ISBN {book.isbn}" if book.isbn else "no ISBN"])))
                    existing = db.get_book_by_isbn(conn, book.isbn) if book.isbn else None
                    if existing:
                        st.info(f"Already in your list ({existing['status']}).")
                        continue
                    c1, c2 = st.columns([2, 1])
                    status = c1.selectbox("Status", db.STATUSES, key=f"{key}_status_{i}",
                                          label_visibility="collapsed")
                    if c2.button("Add", key=f"{key}_add_{i}", type="primary"):
                        fields = book.to_db_fields()
                        if save(lambda c: db.add_book(c, status=status, **fields),
                                f"Add book: {book.title}"):
                            st.toast(f"Added “{book.title}” to {status}")
                            st.rerun()
    finally:
        conn.close()
