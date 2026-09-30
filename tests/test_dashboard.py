from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from booktracker import charts, config, db, stats

ROOT = Path(__file__).resolve().parent.parent
YEAR = date.today().year


def book(status, rating=None, genre=None, finished=None):
    return {"status": status, "rating": rating, "genre": genre, "date_finished": finished}


BOOKS = [book("Read", 5, "Fantasy", f"{YEAR}-01-10"), book("Read", 3, "Fantasy", f"{YEAR}-01-20"),
         book("Read", 4, "History", f"{YEAR - 1}-12-01"), book("Dropped", 1, "Romance", f"{YEAR}-02-01"),
         book("To Read", None, "Fantasy")]


def test_stats():
    assert len(stats.read_in_year(BOOKS, YEAR)) == 2
    assert stats.drop_rate(BOOKS) == pytest.approx(1 / 4)
    assert stats.average_rating(BOOKS) == pytest.approx(13 / 4)
    assert stats.top_genres(BOOKS)[0] == ("Fantasy", 3)
    assert stats.reads_per_month(BOOKS, YEAR)[0] == ("Jan", 2)
    assert len(stats.reads_per_month(BOOKS, YEAR - 1)) == 12


def test_stats_empty():
    assert stats.drop_rate([]) is None and stats.average_rating([]) is None
    assert charts.bar_chart([("Jan", 0)], "Books") is None


def test_store_colors_are_stable_and_distinct():
    light, dark = charts.store_colors(), charts.store_colors(dark=True)
    assert list(light) == list(dark)
    assert len(set(light.values())) == len(light)
    assert light["Magrudy's"] == charts.SERIES_LIGHT[0]


def test_price_history_chart_builds():
    history = [{"store_name": "Amazon.ae", "type": "online", "price_aed": 55.0, "in_stock": 1,
                "date_checked": "2026-09-20T09:00:00+00:00"},
               {"store_name": "Magrudy's", "type": "in-store", "price_aed": 95.0, "in_stock": 1,
                "date_checked": "2026-09-20T09:00:00+00:00"},
               {"store_name": "Magrudy's", "type": "online", "price_aed": 95.0, "in_stock": 1,
                "date_checked": "2026-09-20T09:00:00+00:00"}]
    spec = charts.price_history_chart(history).to_dict()
    assert len(spec["layer"]) == 3
    assert charts.price_history_chart([]) is None


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    path = tmp_path / "books.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    conn = db.connect(path)
    bid = db.add_book(conn, title="Dune", author="Frank Herbert", isbn="9780441172719", target_price=60)
    db.add_book(conn, title="Emma", status="Read", rating=4, genre="Classics")
    for store, typ, price, when in [("Magrudy's", "online", 81.84, "2026-09-20"), ("Magrudy's", "in-store", 81.84, "2026-09-20"),
                                    ("Amazon.ae", "online", 60.0, "2026-09-20"), ("Amazon.ae", "online", 56.0, "2026-09-27")]:
        db.add_price(conn, bid, store, typ, price, True, f"https://x/{store}",
                     branch_location="Mirdif City Center" if typ == "in-store" else None,
                     date_checked=f"{when}T09:00:00+00:00")
    conn.close()
    return path, bid


def run(page):
    at = AppTest.from_file(str(ROOT / page), default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    return at


def test_prices_page(seeded):
    at = run("views/prices.py")
    assert at.metric[0].value == "AED 56.00" and at.metric[1].value == "Amazon.ae"
    assert [s.value for s in at.subheader][:2] == ["🏬 In-Store (Dubai)", "🌐 Online (UAE delivery)"]
    assert len(at.dataframe) >= 2


def test_virgin_link_saved_from_prices_page(seeded):
    path, bid = seeded
    at = run("views/prices.py")
    link_input = [t for t in at.text_input if t.label == "Product page link"][0]
    link_input.input("https://www.virginmegastore.ae/en/books/x/p/123")
    [b for b in at.button if b.label == "Save link"][0].click().run()
    assert not at.exception
    conn = db.connect(path)
    assert db.store_links(conn, bid) == {"Virgin Megastore": "https://www.virginmegastore.ae/en/books/x/p/123"}


def test_stats_page(seeded):
    at = run("views/stats.py")
    values = {m.label: m.value for m in at.metric}
    assert values[f"Read in {YEAR}"] == "1" and values["Drop rate"] == "0%" and values["Average rating"] == "4.0 ★"
