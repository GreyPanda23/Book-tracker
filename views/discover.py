"""Browse books by genre (pick one or several), then filter and add them."""

import streamlit as st

from booktracker import db, ui
from booktracker.book_search import GENRE_NAMES, search_by_genres, surprise_me

st.title("🧭 Discover by Genre")
st.caption("Pick one or more genres to see popular books in them. Narrow the results by year, "
           "author or rating, then add what looks good.")

with st.form("discover"):
    genres = st.multiselect("Genres", GENRE_NAMES, placeholder="Choose one or more genres")
    c1, c2 = st.columns([1, 2])
    mode = c1.radio("Match", ["All of them", "Any of them"], horizontal=True,
                    help="'All' = books that belong to every genre you picked (e.g. Fantasy + "
                         "Mystery). 'Any' = books in at least one of them.")
    keyword = c2.text_input("Also mention (optional)", placeholder="e.g. dragons, detective, Japan")
    b1, b2, _ = st.columns([1, 1, 4])
    submitted = b1.form_submit_button("Find books", type="primary")
    surprise = b2.form_submit_button("🎲 Surprise me",
                                     help="5 random well-liked books you don't have yet, from the "
                                          "genres picked above (or a random genre if none)")

if submitted or surprise:
    with st.spinner("Searching…"):
        if surprise:
            conn = ui.get_conn()
            owned = {b["isbn"] for b in db.list_books(conn) if b["isbn"]}
            conn.close()
            results, note = surprise_me(genres, owned)
        else:
            results, note = search_by_genres(genres, match_all=mode == "All of them", keyword=keyword)
    st.session_state["discover_results"] = results
    st.session_state["discover_note"] = note
    st.session_state["discover_id"] = st.session_state.get("discover_id", 0) + 1

if "discover_note" in st.session_state:
    st.caption(st.session_state["discover_note"])

discover_id = st.session_state.get("discover_id", 0)
results = st.session_state.get("discover_results", [])
ui.result_cards(ui.result_filters(results, f"discover{discover_id}"), f"discover{discover_id}")
