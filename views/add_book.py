"""Search Google Books / Open Library by title, author or ISBN and add a book in one click."""

import streamlit as st

from booktracker import db, ui
from booktracker.book_search import BookResult, search_books

st.title("➕ Add a Book")

with st.form("search"):
    query = st.text_input("Book title, author or ISBN", placeholder="e.g. Project Hail Mary or Andy Weir")
    submitted = st.form_submit_button("Search", type="primary")
if submitted:
    with st.spinner("Searching…"):
        results, note = search_books(query)
    st.session_state["search_results"] = results
    st.session_state["search_note"] = note
    st.session_state["search_id"] = st.session_state.get("search_id", 0) + 1  # resets the filters

results: list[BookResult] = st.session_state.get("search_results", [])
if "search_note" in st.session_state:
    st.caption(st.session_state["search_note"])

search_id = st.session_state.get("search_id", 0)   # new id per search resets the filters
ui.result_cards(ui.result_filters(results, f"search{search_id}"), f"search{search_id}")

with st.expander("Can't find it? Add a book manually"):
    with st.form("manual", clear_on_submit=True):
        title = st.text_input("Title *")
        author = st.text_input("Author")
        isbn = st.text_input("ISBN (10 or 13 digits)")
        genre = st.text_input("Genre")
        cover_url = st.text_input("Cover image URL")
        status = st.selectbox("Status", db.STATUSES)
        if st.form_submit_button("Add book"):
            if not title.strip():
                st.error("Title is required")
            else:
                fields = dict(title=title.strip(), author=author or None, isbn=isbn or None,
                              genre=genre or None, cover_url=cover_url or None, status=status)
                if ui.save(lambda c: db.add_book(c, **fields), f"Add book: {title}"):
                    st.success(f"Added “{title}”")
