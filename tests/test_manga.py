"""Manga library: database kinds, AniList parsing/search, and the Books/Manga switch."""

import random
import sqlite3
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from booktracker import book_search, config, db, manga_search as ms
from booktracker.book_search import BookResult

ROOT = Path(__file__).resolve().parent.parent

BERSERK = {
    "id": 30002, "type": "MANGA", "title": {"romaji": "Berserk", "english": "Berserk"},
    "coverImage": {"large": "https://img/berserk.jpg"}, "startDate": {"year": 1989},
    "averageScore": 92, "popularity": 246398, "genres": ["Action", "Fantasy", "Horror"],
    "volumes": 41, "format": "MANGA",
    "staff": {"edges": [
        {"role": "Story & Art (vols 1-41)", "node": {"name": {"full": "Kentarou Miura"}}},
        {"role": "Supervisor (vols 41- )", "node": {"name": {"full": "Kouji Mori"}}},
        {"role": "Story & Art (vols 41- )", "node": {"name": {"full": "Studio Gaga"}}}]},
}


# ------------------------------------------------------------------ database
def test_kind_defaults_to_book_and_filters(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    db.add_book(conn, title="Dune", isbn="9780441172719")
    db.add_book(conn, title="Berserk", author="Kentarou Miura", kind="manga")
    assert [b["title"] for b in db.list_books(conn, kind="book")] == ["Dune"]
    assert [b["title"] for b in db.list_books(conn, kind="manga")] == ["Berserk"]
    assert len(db.list_books(conn)) == 2
    with pytest.raises(ValueError):
        db.add_book(conn, title="X", kind="comic")


def test_manga_without_isbn_is_not_added_twice(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    a = db.add_book(conn, title="Berserk", author="Kentarou Miura", kind="manga")
    b = db.add_book(conn, title="berserk", author="kentarou miura", kind="manga")
    assert a == b and len(db.list_books(conn)) == 1
    # the same title in the other library is a different entry
    assert db.add_book(conn, title="Berserk", author="Kentarou Miura", kind="book") != a


def test_database_without_kind_column_is_upgraded(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(db.SCHEMA.replace("    kind          TEXT NOT NULL DEFAULT 'book' "
                                        "CHECK (kind IN ('book', 'manga')),\n", ""))
    old.execute("INSERT INTO books (title, isbn, date_added) VALUES ('Origin', '9780385514231', 'x')")
    old.commit()
    old.close()
    conn = db.connect(path)
    assert db.list_books(conn, kind="book")[0]["title"] == "Origin"
    assert db.list_books(conn, kind="manga") == []
    db.add_book(conn, title="Berserk", author="K", kind="manga")
    assert len(db.list_books(conn, kind="manga")) == 1


# ------------------------------------------------------------------ AniList
def test_parse_media():
    b = ms.parse_media(BERSERK)
    assert (b.title, b.year, b.genre, b.isbn) == ("Berserk", "1989", "Action", None)
    assert b.author == "Kentarou Miura, Studio Gaga"       # supervisors are left out
    assert b.rating == 4.6 and b.popularity == 246398 and b.source == "AniList"
    assert "rating" not in b.to_db_fields()


def test_search_puts_the_creators_series_first(monkeypatch):
    other = dict(BERSERK, id=2, title={"romaji": "Berserk of Gluttony", "english": None},
                 staff={"edges": [{"role": "Story", "node": {"name": {"full": "Ichika Isshiki"}}}]})
    vagabond = dict(BERSERK, id=3, title={"romaji": "Vagabond", "english": "Vagabond"})
    monkeypatch.setattr(ms, "_by_title", lambda q, n: [ms.parse_media(other)])
    monkeypatch.setattr(ms, "_by_author", lambda q, n: [ms.parse_media(BERSERK), ms.parse_media(vagabond)])
    results, note = ms.search_manga("Kentaro Miura")
    assert [b.title for b in results] == ["Berserk", "Vagabond", "Berserk of Gluttony"]
    assert "AniList" in note
    assert ms.search_manga("  ")[0] == []


def test_search_survives_author_lookup_failure(monkeypatch):
    def boom(*a):
        raise RuntimeError("down")
    monkeypatch.setattr(ms, "_by_title", lambda q, n: [ms.parse_media(BERSERK)])
    monkeypatch.setattr(ms, "_by_author", boom)
    assert [b.title for b in ms.search_manga("Berserk")[0]] == ["Berserk"]
    monkeypatch.setattr(ms, "_by_title", boom)
    results, note = ms.search_manga("Berserk")
    assert results == [] and "unavailable" in note


def test_spelling_variants_of_a_name_match():
    assert ms._romanized("kentarou") == ms._romanized("kentaro")
    assert ms._romanized("eiichirou") == ms._romanized("eiichiro")


def test_genres_and_themes_are_split_and_combined(monkeypatch):
    calls = []
    monkeypatch.setattr(ms, "_filter_query", lambda g, t, kw, n: calls.append((g, t, kw)) or [ms.parse_media(BERSERK)])
    ms.search_manga_by_genres(["Fantasy", "Seinen", "Isekai"], match_all=True, keyword="dark")
    assert calls[-1] == (["Fantasy"], ["Seinen", "Isekai"], "dark")
    calls.clear()
    results, note = ms.search_manga_by_genres(["Fantasy", "Seinen"], match_all=False)
    assert calls == [(["Fantasy"], [], ""), ([], ["Seinen"], "")] and len(results) == 1   # merged, no repeats
    assert ms.search_manga_by_genres([])[0] == []


def test_surprise_manga_skips_owned(monkeypatch):
    many = [BookResult(title=f"M{i}", author="A", rating=4.2, popularity=500) for i in range(8)]
    monkeypatch.setattr(ms, "search_manga_by_genres", lambda *a, **k: (many, "from test"))
    owned = book_search.owned_keys([{"title": "M0", "author": "A", "isbn": None}])
    picks, note = ms.surprise_manga(["Horror"], owned, rng=random.Random(3))
    assert len(picks) == 5 and "M0" not in [b.title for b in picks] and "Horror" in note


def test_owned_keys_match_without_isbn():
    owned = book_search.owned_keys([{"title": "Berserk", "author": "Kentarou Miura", "isbn": None}])
    assert book_search.book_keys(BookResult(title="berserk", author="Kentarou Miura")) & owned
    assert not book_search.book_keys(BookResult(title="Vagabond", author="Takehiko Inoue")) & owned


# ------------------------------------------------------------------ pages
@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    path = tmp_path / "books.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    return path


def run(page: str, library: str | None = None) -> AppTest:
    at = AppTest.from_file(str(ROOT / page), default_timeout=30)
    if library:
        at.session_state["library"] = library
    at.run()
    assert not at.exception, at.exception
    return at


def test_switch_shows_the_other_library(temp_db):
    conn = db.connect(temp_db)
    db.add_book(conn, title="Dune", isbn="9780441172719")
    db.add_book(conn, title="Berserk", author="Kentarou Miura", kind="manga", status="Read")
    conn.close()
    books = run("views/my_books.py")
    assert books.title[0].value.endswith("My Books")
    assert [t.label for t in books.tabs][0].endswith("To Read (1)")
    manga = run("views/my_books.py", "🎌 Manga")
    assert manga.title[0].value.endswith("My Manga")
    labels = [t.label for t in manga.tabs]
    assert labels[0].endswith("To Read (0)") and labels[2].endswith("Read (1)")
    assert "Berserk" in [m.value for m in manga.markdown if "Berserk" in m.value][0]


def test_app_has_the_library_switch(temp_db):
    at = run("app.py")
    assert at.sidebar.radio[0].label == "Library"
    assert at.sidebar.radio[0].options == ["📚 Books", "🎌 Manga"]
    at.sidebar.radio[0].set_value("🎌 Manga").run()
    assert not at.exception
    assert at.title[0].value.endswith("My Manga")


def test_search_and_add_manga(temp_db, monkeypatch):
    fake = [ms.parse_media(BERSERK)]
    monkeypatch.setattr(ms, "search_manga", lambda q, limit=10: (fake, "Results from AniList"))
    at = run("views/add_book.py", "🎌 Manga")
    assert at.title[0].value.endswith("Add Manga")
    at.text_input[0].input("Berserk")
    at.button[0].click().run()
    assert not at.exception
    [b for b in at.button if b.label == "Add"][0].click().run()
    conn = db.connect(temp_db)
    assert [(b["title"], b["kind"], b["author"], b["status"]) for b in db.list_books(conn)] == [
        ("Berserk", "manga", "Kentarou Miura, Studio Gaga", "To Read")]
    # the card now says it is already in the list, in the manga library only
    again = run("views/add_book.py", "🎌 Manga")
    again.text_input[0].input("Berserk")
    again.button[0].click().run()
    assert not [b for b in again.button if b.label == "Add"] and again.info


def test_manga_discover_page_uses_manga_genres(temp_db, monkeypatch):
    seen = []
    monkeypatch.setattr(ms, "search_manga_by_genres",
                        lambda genres, match_all=True, keyword="", limit=60:
                        seen.append((genres, match_all)) or ([ms.parse_media(BERSERK)], "Results from AniList"))
    at = run("views/discover.py", "🎌 Manga")
    assert "Isekai" in at.multiselect[0].options and "Mecha" in at.multiselect[0].options
    assert "Self-Help" not in at.multiselect[0].options
    at.multiselect[0].set_value(["Horror", "Seinen"])
    at.button[0].click().run()
    assert not at.exception and seen == [(["Horror", "Seinen"], True)]
    assert len([b for b in at.button if b.label == "Add"]) == 1


def test_manga_stats_prices_and_suggestions_pages(temp_db, monkeypatch):
    conn = db.connect(temp_db)
    db.add_book(conn, title="Dune", isbn="9780441172719", status="Read")
    db.add_book(conn, title="Berserk", author="Kentarou Miura", kind="manga", genre="Action")
    conn.close()
    monkeypatch.setattr(ms, "similar_manga", lambda title, author=None, count=5: [
        (BookResult(title="Vinland Saga", author="Makoto Yukimura"), "Readers of Berserk also liked this")])
    stats = run("views/stats.py", "🎌 Manga")
    assert stats.title[0].value.endswith("Manga Stats")
    prices = run("views/prices.py", "🎌 Manga")
    assert any("series" in c.value for c in prices.caption)       # explains title-based pricing
    sugg = run("views/suggestions.py", "🎌 Manga")
    assert any("Vinland Saga" in m.value for m in sugg.markdown)
    [b for b in sugg.button if "Add to To Read" in b.label][0].click().run()
    conn = db.connect(temp_db)
    assert [b["title"] for b in db.list_books(conn, kind="manga")] == ["Vinland Saga", "Berserk"]
    assert [b["title"] for b in db.list_books(conn, kind="book")] == ["Dune"]


def test_sidebar_names_follow_the_library():
    from booktracker import ui
    assert (ui.kind_words("book")["my"], ui.kind_words("book")["add"]) == ("My Books", "Add a Book")
    assert (ui.kind_words("manga")["my"], ui.kind_words("manga")["add"]) == ("My Manga", "Add Manga")
