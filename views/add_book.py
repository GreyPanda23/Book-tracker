"""Search by title, author or ISBN (books) or title/author (manga) and add in one click."""

import streamlit as st

from booktracker import db, ui
from booktracker.book_search import BookResult, search_books
from booktracker.manga_search import search_manga

kind = ui.current_kind()
words = ui.kind_words(kind)
st.title(f"➕ {words['add']}")

with st.form("search"):
    if kind == "manga":
        query = st.text_input("Manga title or author", placeholder="e.g. Berserk or Eiichiro Oda")
    else:
        query = st.text_input("Book title, author or ISBN",
                              placeholder="e.g. Project Hail Mary or Andy Weir")
    submitted = st.form_submit_button("Search", type="primary")
if submitted:
    with st.spinner("Searching…"):
        results, note = search_manga(query) if kind == "manga" else search_books(query)
    st.session_state[f"search_results_{kind}"] = results
    st.session_state[f"search_note_{kind}"] = note
    st.session_state[f"search_id_{kind}"] = st.session_state.get(f"search_id_{kind}", 0) + 1  # resets filters

results: list[BookResult] = st.session_state.get(f"search_results_{kind}", [])
if f"search_note_{kind}" in st.session_state:
    st.caption(st.session_state[f"search_note_{kind}"])

key = f"search_{kind}{st.session_state.get(f'search_id_{kind}', 0)}"
ui.result_cards(ui.result_filters(results, key), key)

with st.expander(f"Can't find it? Add a {words['singular']} manually"):
    with st.form("manual", clear_on_submit=True):
        title = st.text_input("Title *")
        author = st.text_input("Author")
        isbn = st.text_input("ISBN (10 or 13 digits)" + (", optional" if kind == "manga" else ""))
        genre = st.text_input("Genre")
        cover_url = st.text_input("Cover image URL")
        status = st.selectbox("Status", db.STATUSES)
        if st.form_submit_button(f"Add {words['singular']}"):
            if not title.strip():
                st.error("Title is required")
            else:
                fields = dict(title=title.strip(), author=author or None, isbn=isbn or None,
                              genre=genre or None, cover_url=cover_url or None, status=status, kind=kind)
                if ui.save(lambda c: db.add_book(c, **fields), f"Add {kind}: {title}"):
                    st.success(f"Added “{title}”")
