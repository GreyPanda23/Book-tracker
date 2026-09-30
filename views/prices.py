"""Per-book price comparison (In-Store Dubai vs Online) and price history."""

import pandas as pd
import streamlit as st

from booktracker import charts, db, ui
from booktracker.fetchers.registry import load_store_configs

st.title("💰 Prices")

conn = ui.get_conn()
books = db.list_books(conn)
if not books:
    conn.close()
    st.info("Add books to your To Read list first. Prices are checked every Sunday.")
    st.stop()

# To Read books first - they're the ones prices are checked for
books.sort(key=lambda b: (b["status"] != "To Read", b["title"].lower()))
labels = {f"{ui.STATUS_ICONS[b['status']]} {b['title']} — {b['author'] or '?'}": b for b in books}
book = labels[st.selectbox("Book", list(labels))]

latest = db.latest_prices(conn, book["id"])
history = db.price_history(conn, book["id"])
links = db.store_links(conn, book["id"])
last_run = db.last_successful_run(conn)
health = db.store_health(conn)
conn.close()

left, right = st.columns([1, 4])
with left:
    ui.cover(book["cover_url"], width=80)
with right:
    st.markdown(f"**{book['title']}**  \n{book['author'] or ''}")
    st.caption(" · ".join(filter(None, [f"ISBN {book['isbn']}" if book["isbn"] else "No ISBN",
                                        f"🎯 target AED {book['target_price']:.0f}" if book["target_price"] else None])))
    if book["status"] != "To Read":
        st.caption("ℹ️ Prices are only checked for books marked **To Read**.")

priced = [p for p in latest if p["price_aed"] is not None]
if not priced:
    st.info("No prices yet for this book. They're checked every Sunday by your Mac "
            f"(last update: {last_run['finished_at'][:10] if last_run else 'never'}).")
else:
    best = min(priced, key=lambda p: (p["in_stock"] == 0, p["price_aed"]))
    where = "in store" if best["type"] == "in-store" else "online"
    stock_note = "" if best["in_stock"] != 0 else " (out of stock)"
    c1, c2 = st.columns(2)
    c1.metric("Cheapest now", f"AED {best['price_aed']:.2f}")
    c2.metric("Where", f"{best['store_name']}", help=f"{where}{stock_note}")
    if book["target_price"]:
        gap = best["price_aed"] - book["target_price"]
        st.caption("✅ Below your target price!" if gap < 0 else f"AED {gap:.2f} above your target.")


def price_table(rows: list[dict], in_store: bool) -> None:
    if not rows:
        st.caption("No prices from these stores yet.")
        return
    df = pd.DataFrame([{
        "Store": r["store_name"],
        "Price (AED)": r["price_aed"],
        "Stock": {1: "✅ In stock", 0: "❌ Out of stock"}.get(r["in_stock"], "❔ Unknown"),
        **({"Dubai branches": r["branch_location"]} if in_store else {}),
        "Match": "ISBN" if r["match_method"] == "isbn" else "Title (other edition?)",
        "Checked": (r["date_checked"] or "")[:10],
        "Link": r["product_url"],
    } for r in rows])
    cheapest = df["Price (AED)"].min()

    def highlight(row):
        good = "background-color: rgba(0, 131, 0, 0.18); font-weight: 600"
        return [good if row["Price (AED)"] == cheapest else "" for _ in row]

    st.dataframe(df.style.apply(highlight, axis=1).format({"Price (AED)": "{:.2f}"}),
                 hide_index=True, width="stretch",
                 column_config={"Link": st.column_config.LinkColumn("Link", display_text="Open ↗")})


st.subheader("🏬 In-Store (Dubai)")
st.caption("Website price used as the shop price. Stock shown is what the website reports.")
price_table([p for p in latest if p["type"] == "in-store"], in_store=True)

st.subheader("🌐 Online (UAE delivery)")
price_table([p for p in latest if p["type"] == "online"], in_store=False)

st.subheader("📈 Price history")
chart = charts.price_history_chart(history, dark=ui.is_dark())
if chart is None:
    st.caption("The chart appears after the first weekly update.")
else:
    st.altair_chart(chart, width="stretch")
    with st.expander("Show as table"):
        st.dataframe(pd.DataFrame(history)[["date_checked", "store_name", "type", "price_aed", "in_stock"]],
                     hide_index=True, width="stretch")

# Stores that can't search by ISBN (Virgin) need a pasted product link
link_stores = [c for c in load_store_configs() if c.enabled and c.module == "virgin"]
for store in link_stores:
    with st.expander(f"🔗 {store.name} product link"):
        st.caption(store.notes)
        with st.form(f"link_{store.module}"):
            url = st.text_input("Product page link", value=links.get(store.name, ""),
                                placeholder="https://www.virginmegastore.ae/en/books/.../p/123456")
            if st.form_submit_button("Save link"):
                if ui.save(lambda c: db.set_store_link(c, book["id"], store.name, url.strip() or None),
                           f"Store link for {book['title']}"):
                    st.success("Saved - it will be checked in the next weekly update.")

with st.expander("🩺 Store health"):
    st.caption(f"Last weekly update: {last_run['finished_at'][:16].replace('T', ' ') + ' UTC' if last_run else 'never'}")
    if health:
        st.dataframe(pd.DataFrame(health).rename(columns={
            "store_name": "Store", "last_success": "Last price found", "last_problem_at": "Last problem at",
            "last_problem": "Problem"}), hide_index=True, width="stretch")
    else:
        st.caption("No updates have run yet.")
