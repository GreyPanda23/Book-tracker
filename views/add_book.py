"""Search by title, author or ISBN (books) or by title (movies, TV shows) and add in one click."""

import streamlit as st

from booktracker import db, tmdb, ui
from booktracker.book_search import BookResult, search_books

kind = ui.current_kind()
words = ui.kind_words(kind)
st.title(f"➕ {words['add']}")

with st.form("search"):
    if kind != "book":
        query = st.text_input(f"{words['singular'].title() if kind == 'movie' else 'TV show'} title",
                              placeholder="e.g. Inception" if kind == "movie" else "e.g. Breaking Bad")
    else:
        query = st.text_input("Book title, author or ISBN",
                              placeholder="e.g. Project Hail Mary or Andy Weir")
    submitted = st.form_submit_button("Search", type="primary")
if submitted:
    with st.spinner("Searching…"):
        results, note = search_books(query) if kind == "book" else tmdb.search_media(kind, query)
    st.session_state[f"search_results_{kind}"] = results
    st.session_state[f"search_note_{kind}"] = note
    st.session_state[f"search_id_{kind}"] = st.session_state.get(f"search_id_{kind}", 0) + 1  # resets filters

if kind != "book":
    if not tmdb.available():
        st.warning(tmdb.NO_KEY)
    st.caption("This product uses the TMDB API but is not endorsed or certified by TMDB.")
results: list[BookResult] = st.session_state.get(f"search_results_{kind}", [])
if f"search_note_{kind}" in st.session_state:
    st.caption(st.session_state[f"search_note_{kind}"])

key = f"search_{kind}{st.session_state.get(f'search_id_{kind}', 0)}"
ui.result_cards(ui.result_filters(results, key), key)

with st.expander(f"Can't find it? Add a {words['singular']} manually"):
    with st.form("manual", clear_on_submit=True):
        title = st.text_input("Title *")
        if kind == "book":
            author = st.text_input("Author")
            isbn = st.text_input("ISBN (10 or 13 digits)")
        else:
            year = st.text_input("Release year")
        genre = st.text_input("Genre")
        cover_url = st.text_input("Cover image URL" if kind == "book" else "Poster image URL")
        status = st.selectbox("Status", db.STATUSES, format_func=lambda s: ui.status_label(s, kind))
        if st.form_submit_button(f"Add {words['singular']}"):
            if not title.strip():
                st.error("Title is required")
            else:
                fields = dict(title=title.strip(), genre=genre or None, cover_url=cover_url or None,
                              status=status, kind=kind)
                if kind == "book":
                    fields.update(author=author or None, isbn=isbn or None)
                else:
                    fields.update(year=year.strip() or None)
                if ui.save(lambda c: db.add_book(c, **fields), f"Add {words['singular']}: {title}"):
                    st.success(f"Added “{title}”")
