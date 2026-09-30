"""Similar-book suggestions (Open Library) and optional Claude recommendations."""

import streamlit as st

from booktracker import db, suggestions, ui

st.title("💡 Suggestions")

conn = ui.get_conn()
books = db.list_books(conn)
conn.close()
if not books:
    st.info("Add some books first, then come back for suggestions.")
    st.stop()

owned_titles = {suggestions._norm_title(b["title"]) for b in books}
owned_isbns = {b["isbn"] for b in books if b["isbn"]}


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def cached_similar(title: str, author: str | None, isbn: str | None):
    return suggestions.similar_books({"title": title, "author": author, "isbn": isbn})


def show_suggestions(items: list[suggestions.Suggestion], key: str) -> None:
    if not items:
        st.warning("No suggestions found for this one. Try another book.")
    for i, s in enumerate(items):
        b = s.book
        with st.container(border=True):
            left, right = st.columns([1, 4])
            with left:
                ui.cover(b.cover_url, width=70)
            with right:
                st.markdown(f"**{b.title}**  \n{b.author or ''}")
                st.caption(f"💡 {s.reason}")
                owned = (b.isbn in owned_isbns) or suggestions._norm_title(b.title) in owned_titles
                if owned:
                    st.caption("✔ Already in your list")
                elif st.button("➕ Add to To Read", key=f"{key}_{i}"):
                    fields = b.to_db_fields()
                    if ui.save(lambda c: db.add_book(c, status="To Read", **fields),
                               f"Add suggestion: {b.title}"):
                        st.toast(f"Added “{b.title}” to To Read")
                        st.rerun()


similar_tab, ai_tab = st.tabs(["📚 Similar to a book", "🤖 AI picks (Claude)"])

with similar_tab:
    choices = {f"{b['title']} — {b['author'] or '?'}": b for b in books}
    default = next((i for i, b in enumerate(books) if b["status"] == "Read"), 0)
    label = st.selectbox("Find books similar to", list(choices), index=default)
    chosen = choices[label]
    with st.spinner("Looking for similar books on Open Library…"):
        try:
            items = cached_similar(chosen["title"], chosen["author"], chosen["isbn"])
        except Exception as exc:
            items = []
            st.error(f"Open Library is not reachable right now ({exc}).")
    show_suggestions(items, f"sim_{chosen['id']}")

with ai_tab:
    if not suggestions.claude_available():
        st.info(
            "Optional: add an **ANTHROPIC_API_KEY** to your `.env` file (or Streamlit "
            "secrets) to get personalised picks based on your Read list and ratings. "
            "See the README, section *Claude recommendations*.")
    else:
        read_count = sum(b["status"] == "Read" for b in books)
        st.caption(f"Uses your {read_count} Read book(s), their ratings and notes, "
                   "and avoids anything you dropped or already plan to read.")
        if st.button("Get recommendations", type="primary"):
            with st.spinner("Asking Claude…"):
                try:
                    st.session_state["ai_suggestions"] = suggestions.claude_recommendations(books)
                except Exception as exc:
                    st.error(f"Couldn't get recommendations: {exc}")
        if "ai_suggestions" in st.session_state:
            show_suggestions(st.session_state["ai_suggestions"], "ai")
