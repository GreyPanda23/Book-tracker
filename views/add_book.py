"""Search Google Books / Open Library and add a book in one click."""

import streamlit as st

from booktracker import db, ui
from booktracker.book_search import (BookResult, SORT_OPTIONS, filter_and_sort, search_books,
                                     year_bounds)

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

# Filters: release year range and sort order (only useful with a few results)
shown = list(enumerate(results))
bounds = year_bounds(results)
if len(results) > 1 and bounds:
    fid = st.session_state.get("search_id", 0)
    c1, c2 = st.columns([3, 2])
    first, last = bounds
    years = c1.slider("Released between", first, last, (first, last), key=f"years_{fid}") \
        if first < last else (first, last)
    sort = c2.selectbox("Sort by", SORT_OPTIONS, key=f"sort_{fid}")
    keep_unknown = st.checkbox("Include books with no release year", value=True, key=f"unk_{fid}")
    kept = filter_and_sort([b for _, b in shown], years[0], years[1], sort, keep_unknown)
    order = {id(b): i for i, b in shown}   # keep each book's widget keys stable
    shown = [(order[id(b)], b) for b in kept]
    st.caption(f"Showing {len(shown)} of {len(results)} books")

conn = ui.get_conn()
for i, book in shown:
    with st.container(border=True):
        left, right = st.columns([1, 4])
        with left:
            ui.cover(book.cover_url, width=80)
        with right:
            st.markdown(f"**{book.title}**")
            st.caption(" · ".join(filter(None, [book.author, book.year, book.genre,
                                                  f"ISBN {book.isbn}" if book.isbn else "no ISBN"])))
            existing = db.get_book_by_isbn(conn, book.isbn) if book.isbn else None
            if existing:
                st.info(f"Already in your list ({existing['status']}).")
                continue
            c1, c2 = st.columns([2, 1])
            status = c1.selectbox("Status", db.STATUSES, key=f"status_{i}",
                                  label_visibility="collapsed")
            if c2.button("Add", key=f"add_{i}", type="primary"):
                fields = book.to_db_fields()
                if ui.save(lambda c: db.add_book(c, status=status, **fields),
                           f"Add book: {book.title}"):
                    st.toast(f"Added “{book.title}” to {status}")
                    st.rerun()
conn.close()

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
