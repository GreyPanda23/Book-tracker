"""Browse books or manga by genre (pick one or several), then filter and add them."""

import streamlit as st

from booktracker import book_search, db, manga_search, ui
from booktracker.book_search import owned_keys

kind = ui.current_kind()
words = ui.kind_words(kind)
st.title(f"🧭 Discover {words['plural']} by Genre")
st.caption(f"Pick one or more genres to see popular {words['lower']} in them. Narrow the results by "
           "year, author or rating, then add what looks good.")

genre_names = manga_search.MANGA_GENRE_NAMES if kind == "manga" else book_search.GENRE_NAMES
find = manga_search.search_manga_by_genres if kind == "manga" else book_search.search_by_genres
surprise_fn = manga_search.surprise_manga if kind == "manga" else book_search.surprise_me
hint = ("e.g. tournament, pirates, cooking" if kind == "manga" else "e.g. dragons, detective, Japan")

with st.form("discover"):
    genres = st.multiselect("Genres" + (" and themes" if kind == "manga" else ""), genre_names,
                            placeholder="Choose one or more")
    c1, c2 = st.columns([1, 2])
    mode = c1.radio("Match", ["All of them", "Any of them"], horizontal=True,
                    help="'All' = only those in every genre you picked (e.g. Fantasy + Mystery). "
                         "'Any' = those in at least one of them.")
    keyword = c2.text_input("Also mention (optional)", placeholder=hint)
    b1, b2, _ = st.columns([1, 1, 4])
    submitted = b1.form_submit_button(f"Find {words['lower']}", type="primary")
    surprise = b2.form_submit_button("🎲 Surprise me",
                                     help=f"5 random well-liked {words['lower']} you don't have yet, from "
                                          "the genres picked above (or a random genre if none)")

if submitted or surprise:
    with st.spinner("Searching…"):
        if surprise:
            conn = ui.get_conn()
            owned = owned_keys(db.list_books(conn, kind=kind))
            conn.close()
            results, note = surprise_fn(genres, owned)
        else:
            results, note = find(genres, match_all=mode == "All of them", keyword=keyword)
    st.session_state[f"discover_results_{kind}"] = results
    st.session_state[f"discover_note_{kind}"] = note
    st.session_state[f"discover_id_{kind}"] = st.session_state.get(f"discover_id_{kind}", 0) + 1

if f"discover_note_{kind}" in st.session_state:
    st.caption(st.session_state[f"discover_note_{kind}"])

key = f"discover_{kind}{st.session_state.get(f'discover_id_{kind}', 0)}"
results = st.session_state.get(f"discover_results_{kind}", [])
ui.result_cards(ui.result_filters(results, key), key)
