"""Reading statistics."""

from datetime import date

import streamlit as st

from booktracker import charts, db, stats, ui

st.title("📊 Stats")

conn = ui.get_conn()
books = db.list_books(conn)
conn.close()
if not books:
    st.info("Add some books to see your stats.")
    st.stop()

year = date.today().year
read_this_year = stats.read_in_year(books, year)
rate = stats.drop_rate(books)
avg = stats.average_rating(books)

c1, c2 = st.columns(2)
c1.metric(f"Read in {year}", len(read_this_year))
c2.metric("To Read", sum(b["status"] == "To Read" for b in books))
c3, c4 = st.columns(2)
c3.metric("Drop rate", f"{rate:.0%}" if rate is not None else "—",
          help="Dropped ÷ (Read + Dropped)")
c4.metric("Average rating", f"{avg:.1f} ★" if avg else "—",
          help="Across all books you've rated")

dark = ui.is_dark()
st.subheader(f"Books finished per month ({year})")
monthly = charts.bar_chart(stats.reads_per_month(books, year), "Books", dark=dark, horizontal=False)
if monthly is None:
    st.caption("Mark books as Read to fill this chart.")
else:
    st.altair_chart(monthly, width="stretch")

st.subheader("Top genres")
genres = charts.bar_chart(stats.top_genres(books), "Books", dark=dark)
if genres is None:
    st.caption("No genres yet.")
else:
    st.altair_chart(genres, width="stretch")
