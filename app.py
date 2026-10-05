"""Streamlit entry point. Run with:  streamlit run app.py"""

import streamlit as st

from booktracker import ui

st.set_page_config(page_title="My Book Tracker", page_icon="📚", layout="wide")

if not ui.check_password():
    st.stop()

ui.library_switch()

words = ui.kind_words()   # sidebar names follow the Books / Manga switch
pages = [
    st.Page("views/my_books.py", title=words["my"], icon=words["icon"], default=True),
    st.Page("views/add_book.py", title=words["add"], icon="➕"),
    st.Page("views/discover.py", title="Discover by Genre", icon="🧭"),
    st.Page("views/suggestions.py", title="Suggestions", icon="💡"),
    st.Page("views/prices.py", title="Prices", icon="💰"),
    st.Page("views/stats.py", title="Stats", icon="📊"),
]
st.navigation(pages).run()
