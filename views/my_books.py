"""Your books, filtered by status tabs, with status/rating/notes editing.

While you type in the filter box the tabs give way to one list that groups the
matches by status, so a search shows every match from every section at once.
"""

import streamlit as st

from booktracker import db, ui

words = ui.kind_words()
st.title(f"{words['icon']} {words['my']}")

conn = ui.get_conn()
books = db.list_books(conn, kind=words["kind"])
conn.close()

if not books:
    st.info(f"No {words['lower']} yet. Go to **➕ {words['add']}** to search and add your first one.")
    st.stop()

def clear_search() -> None:
    st.session_state[f"my_books_filter_{words['kind']}"] = ""


box, clear = st.columns([6, 1], vertical_alignment="center")
search = box.text_input("Filter", placeholder=f"Filter {words['lower']} by title, author or genre",
                        label_visibility="collapsed", key=f"my_books_filter_{words['kind']}")
clear.button("✖ Clear", key=f"my_books_clear_{words['kind']}", disabled=not search, on_click=clear_search,
             help="Clear the search and go back to the section tabs")
if search:
    s = search.lower()
    books = [b for b in books if s in " ".join(
        str(b[k] or "") for k in ("title", "author", "genre")).lower()]

by_status = {s: [b for b in books if b["status"] == s] for s in db.STATUSES}


def edit_form(book: dict, key: str) -> None:
    with st.form(f"edit_{key}"):
        status = st.selectbox("Status", db.STATUSES, index=db.STATUSES.index(book["status"]))
        rating = st.feedback("stars", default=(book["rating"] - 1) if book["rating"] else None,
                             key=f"rating_{key}")
        notes = st.text_area("Notes", value=book["notes"] or "")
        c1, c2 = st.columns(2)
        target = c1.number_input("Alert me below (AED)", min_value=0.0, step=5.0,
                                 value=float(book["target_price"] or 0),
                                 help="E-mail alert when a To Read book drops below this price. 0 = off")
        isbn = c2.text_input("ISBN (edition used for prices)", value=book["isbn"] or "")
        if st.form_submit_button("Save changes", type="primary"):
            changes = dict(status=status, notes=notes or None, isbn=isbn or None,
                           rating=(rating + 1) if rating is not None else None,
                           target_price=target or None)
            if ui.save(lambda c: db.update_book(c, book["id"], **changes),
                       f"Update book: {book['title']}"):
                st.toast("Saved")
                st.rerun()
    if st.button("🗑️ Delete this book", key=f"del_{key}"):
        if ui.save(lambda c: db.delete_book(c, book["id"]), f"Delete book: {book['title']}"):
            st.rerun()


def show(book_list: list[dict], tab_key: str) -> None:
    if not book_list:
        st.caption("Nothing here yet.")
    for book in book_list:
        with st.container(border=True):
            left, right = st.columns([1, 4])
            with left:
                ui.cover(book["cover_url"], width=80)
            with right:
                st.markdown(f"**{book['title']}**  \n{book['author'] or 'Unknown author'}")
                meta = [ui.STATUS_ICONS[book["status"]] + " " + book["status"],
                        book["genre"], ui.stars(book["rating"])]
                st.caption(" · ".join(filter(None, meta)))
                if book["notes"]:
                    st.caption(f"📝 {book['notes'][:150]}")
                with st.expander("Edit"):
                    edit_form(book, f"{tab_key}_{book['id']}")


if search:
    st.caption(f"{len(books)} matching {words['lower']} across all sections")
    if not books:
        st.info(f"No {words['singular']} in your list matches that.")
    for status in db.STATUSES:
        if by_status[status]:
            st.subheader(f"{ui.STATUS_ICONS[status]} {status} ({len(by_status[status])})")
            show(by_status[status], status)
else:
    tabs = st.tabs([f"{ui.STATUS_ICONS[s]} {s} ({len(by_status[s])})" for s in db.STATUSES]
                   + [f"All ({len(books)})"])
    for tab, status in zip(tabs, [*db.STATUSES, None]):
        with tab:
            show(by_status[status] if status else books, status or "all")
