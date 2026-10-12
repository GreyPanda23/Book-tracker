"""Movies and TV shows: database kinds, the manga clean-up, TMDB parsing/search, and the library switch."""

import random
import sqlite3
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from booktracker import book_search, config, db, suggestions, tmdb
from booktracker.book_search import BookResult

ROOT = Path(__file__).resolve().parent.parent

INCEPTION = {"id": 27205, "title": "Inception", "release_date": "2010-07-15", "poster_path": "/inc.jpg",
             "genre_ids": [28, 878, 12], "vote_average": 8.4, "vote_count": 36000}
BREAKING_BAD = {"id": 1396, "name": "Breaking Bad", "first_air_date": "2008-01-20", "poster_path": "/bb.jpg",
                "genre_ids": [18, 80], "vote_average": 8.9, "vote_count": 14000}


# ------------------------------------------------------------------ database
def test_kinds_and_remakes(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    db.add_book(conn, title="Dune", isbn="9780441172719")
    a = db.add_book(conn, title="Dune", kind="movie", year="2021")
    assert db.add_book(conn, title="dune", kind="movie", year="2021") == a      # same movie: no duplicate
    assert db.add_book(conn, title="Dune", kind="movie", year="1984") != a      # a remake is its own entry
    assert db.add_book(conn, title="Dune", kind="tv", year="2021") != a         # other library
    assert [b["year"] for b in db.list_books(conn, kind="movie")] == ["1984", "2021"]
    assert len(db.list_books(conn, kind="book")) == 1
    with pytest.raises(ValueError):
        db.add_book(conn, title="X", kind="manga")


def test_manga_is_removed_from_an_existing_database(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(db.SCHEMA.replace("    year          TEXT,                     -- release year (movies and TV shows)\n", "")
                      .replace("'book', 'movie', 'tv'", "'book', 'manga'"))
    old.execute("INSERT INTO books (title, isbn, date_added) VALUES ('Origin', '9780385514231', 'x')")
    old.execute("INSERT INTO books (title, kind, date_added) VALUES ('Berserk', 'manga', 'x')")
    old.execute("INSERT INTO prices (book_id, store_name, type, date_checked) VALUES (2, 'Noon', 'online', 'x')")
    old.commit()
    old.close()
    conn = db.connect(path)
    assert [b["title"] for b in db.list_books(conn)] == ["Origin"]
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 0
    db.add_book(conn, title="Inception", kind="movie", year="2010")
    assert len(db.list_books(conn, kind="movie")) == 1
    conn.close()
    assert len(db.list_books(db.connect(path))) == 2         # opening again changes nothing


# ------------------------------------------------------------------ TMDB
def test_parse_movie_and_show():
    m = tmdb.parse_item("movie", INCEPTION)
    assert (m.title, m.year, m.genre, m.author, m.isbn) == ("Inception", "2010", "Action", None, None)
    assert m.cover_url == "https://image.tmdb.org/t/p/w342/inc.jpg"
    assert m.subjects == ["Action", "Science Fiction", "Adventure"]
    assert m.rating == 4.2 and m.popularity == 36000 and m.source == "TMDB"
    t = tmdb.parse_item("tv", BREAKING_BAD)
    assert (t.title, t.year, t.genre) == ("Breaking Bad", "2008", "Drama")
    assert tmdb.parse_item("movie", {"id": 1}) is None
    assert tmdb.parse_item("movie", {"title": "Unrated", "vote_average": 0}).rating is None


def test_search_uses_the_right_endpoint(monkeypatch):
    seen = []
    monkeypatch.setattr(tmdb, "_get", lambda path, **p: seen.append((path, p)) or {"results": [BREAKING_BAD]})
    results, note = tmdb.search_media("tv", " Breaking Bad ")
    assert [b.title for b in results] == ["Breaking Bad"] and "TMDB" in note
    assert seen == [("/search/tv", {"query": "Breaking Bad", "include_adult": "false"})]
    assert tmdb.search_media("movie", "  ")[0] == []


def test_remakes_are_not_deduplicated(monkeypatch):
    items = [dict(INCEPTION, title="Dune", release_date=d) for d in ("1984-12-14", "2021-09-15")]
    monkeypatch.setattr(tmdb, "_get", lambda path, **p: {"results": items})
    assert [b.year for b in tmdb.search_media("movie", "Dune")[0]] == ["1984", "2021"]


def test_missing_key_gives_a_friendly_note(monkeypatch):
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    monkeypatch.setattr(config, "get_secret", lambda name, default=None: None)
    assert not tmdb.available()
    results, note = tmdb.search_media("movie", "Inception")
    assert results == [] and "TMDB_API_KEY" in note


def test_key_formats(monkeypatch):
    sent = {}

    class Fake:
        def __init__(self, **kw): sent["headers"] = kw["headers"]
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, url, params):
            sent["params"] = params
            return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {}})()

    monkeypatch.setattr(tmdb.httpx, "Client", Fake)
    monkeypatch.setattr(config, "get_secret", lambda name, default=None: "abc123")
    tmdb._get("/x")
    assert sent["params"]["api_key"] == "abc123" and "Authorization" not in sent["headers"]
    monkeypatch.setattr(config, "get_secret", lambda name, default=None: "eyJtoken")
    tmdb._get("/x")
    assert sent["headers"]["Authorization"] == "Bearer eyJtoken" and "api_key" not in sent["params"]


def test_genres_all_or_any(monkeypatch):
    seen = []
    monkeypatch.setattr(tmdb, "_get", lambda path, **p: seen.append((path, p)) or {"results": [INCEPTION], "total_pages": 1})
    tmdb.search_by_genres("movie", ["Action", "Science Fiction"], match_all=True)
    tmdb.search_by_genres("movie", ["Action", "Science Fiction"], match_all=False)
    assert [p["with_genres"] for _, p in seen] == ["28,878", "28|878"] and seen[0][0] == "/discover/movie"
    assert tmdb.search_by_genres("movie", [])[0] == []


def test_genre_search_with_keyword_filters_by_genre(monkeypatch):
    drama = dict(BREAKING_BAD, name="Drama Show", genre_ids=[18])
    monkeypatch.setattr(tmdb, "_get", lambda path, **p: {"results": [BREAKING_BAD, drama], "total_pages": 1})
    both, _ = tmdb.search_by_genres("tv", ["Drama", "Crime"], match_all=True, keyword="bad")
    assert [b.title for b in both] == ["Breaking Bad"]
    either, _ = tmdb.search_by_genres("tv", ["Drama", "Crime"], match_all=False, keyword="bad")
    assert len(either) == 2


def test_surprise_skips_owned(monkeypatch):
    many = [BookResult(title=f"M{i}", year="2000", rating=4.2, popularity=500) for i in range(8)]
    monkeypatch.setattr(tmdb, "search_by_genres", lambda *a, **k: (many, "from test"))
    owned = book_search.owned_keys([{"title": "M0", "author": None, "isbn": None, "year": "2000"}])
    picks, note = tmdb.surprise("movie", ["Horror"], owned, rng=random.Random(3))
    assert len(picks) == 5 and "M0" not in [b.title for b in picks] and "Horror" in note


def test_similar_titles(monkeypatch):
    other = dict(INCEPTION, id=2, title="Interstellar", vote_count=900)
    obscure = dict(INCEPTION, id=3, title="Nobody Knows", vote_count=3)
    def fake(path, **p):
        if path.startswith("/search"):
            return {"results": [INCEPTION]}
        return {"results": [INCEPTION, other, obscure] if path.endswith("recommendations") else []}
    monkeypatch.setattr(tmdb, "_get", fake)
    out = tmdb.similar("movie", "Inception", "2010")
    assert [(b.title, why) for b, why in out] == [("Interstellar", "Fans of Inception also liked this")]
    monkeypatch.setattr(tmdb, "_get", lambda path, **p: {"results": []})
    assert tmdb.similar("movie", "Nothing") == []


def test_ai_prompt_uses_watching_words():
    books = [dict(title="Inception", year="2010", status="Read", rating=5, genre="Action"),
             dict(title="Tenet", year="2020", status="Dropped"),
             dict(title="Dune", year="2021", status="To Read")]
    text = suggestions.build_prompt(books, 5, "movie")
    assert "Movies I have watched" in text and "Inception (2010)" in text and "to-watch list" in text
    assert "director" in text and "read" not in text.replace("already", "").lower().split("watched")[0]
    assert "Books I have read" in suggestions.build_prompt([dict(title="Dune", status="Read")], 5)


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


def test_switch_shows_each_library(temp_db):
    conn = db.connect(temp_db)
    db.add_book(conn, title="Dune", isbn="9780441172719")
    db.add_book(conn, title="Inception", kind="movie", year="2010", status="Read")
    db.add_book(conn, title="Breaking Bad", kind="tv", year="2008")
    conn.close()
    assert run("views/my_books.py").title[0].value.endswith("My Books")
    movies = run("views/my_books.py", "🎬 Movies")
    assert movies.title[0].value.endswith("My Movies")
    labels = [t.label for t in movies.tabs]
    assert labels[0].endswith("To Watch (0)") and labels[1].endswith("Watching (0)") \
        and labels[2].endswith("Watched (1)")
    assert any("Inception" in m.value for m in movies.markdown)
    shows = run("views/my_books.py", "📺 TV Shows")
    assert shows.title[0].value.endswith("My TV Shows") and [t.label for t in shows.tabs][0].endswith("To Watch (1)")


def test_app_has_the_library_switch_and_prices_only_for_books(temp_db):
    at = run("app.py")
    assert at.sidebar.radio[0].label == "Library"
    assert at.sidebar.radio[0].options == ["📚 Books", "🎬 Movies", "📺 TV Shows"]
    at.sidebar.radio[0].set_value("📺 TV Shows").run()
    assert not at.exception and at.title[0].value.endswith("My TV Shows")


def test_movie_edit_form_has_no_price_fields(temp_db):
    conn = db.connect(temp_db)
    db.add_book(conn, title="Inception", kind="movie", year="2010")
    conn.close()
    at = run("views/my_books.py", "🎬 Movies")
    assert not at.number_input and at.selectbox[0].options == ["To Watch", "Watching", "Watched", "Dropped"]
    assert at.selectbox[0].value == "To Read"   # stored value; shown as To Watch
    at.selectbox[0].set_value("Watched")
    [b for b in at.button if b.label == "Save changes"][0].click().run()
    assert not at.exception
    conn = db.connect(temp_db)
    assert db.list_books(conn, kind="movie")[0]["status"] == "Read"


def test_search_and_add_movie(temp_db, monkeypatch):
    fake = [tmdb.parse_item("movie", INCEPTION)]
    monkeypatch.setattr(tmdb, "search_media", lambda kind, q, limit=10: (fake, "Results from TMDB"))
    at = run("views/add_book.py", "🎬 Movies")
    assert at.title[0].value.endswith("Add a Movie")
    at.text_input[0].input("Inception")
    at.button[0].click().run()
    assert not at.exception
    [b for b in at.button if b.label == "Add"][0].click().run()
    conn = db.connect(temp_db)
    assert [(b["title"], b["kind"], b["year"], b["genre"], b["status"]) for b in db.list_books(conn)] == [
        ("Inception", "movie", "2010", "Action", "To Read")]
    again = run("views/add_book.py", "🎬 Movies")
    again.text_input[0].input("Inception")
    again.button[0].click().run()
    assert not [b for b in again.button if b.label == "Add"] and again.info
    assert "To Watch" in again.info[0].value
    # the same title is still free to add in the TV library
    shows = run("views/add_book.py", "📺 TV Shows")
    shows.text_input[0].input("Inception")
    shows.button[0].click().run()
    assert len([b for b in shows.button if b.label == "Add"]) == 1


def test_add_manually_movie(temp_db):
    at = run("views/add_book.py", "📺 TV Shows")
    assert "Author" not in [t.label for t in at.text_input]
    labels = {t.label: t for t in at.text_input}
    labels["Title *"].input("Severance")
    labels["Release year"].input("2022")
    [b for b in at.button if b.label == "Add TV show"][0].click().run()
    assert not at.exception
    conn = db.connect(temp_db)
    assert [(b["title"], b["kind"], b["year"]) for b in db.list_books(conn)] == [("Severance", "tv", "2022")]


def test_discover_page_uses_tmdb_genres(temp_db, monkeypatch):
    seen = []
    monkeypatch.setattr(tmdb, "search_by_genres",
                        lambda kind, genres, match_all=True, keyword="", limit=60:
                        seen.append((kind, genres, match_all)) or ([tmdb.parse_item("tv", BREAKING_BAD)], "Results from TMDB"))
    at = run("views/discover.py", "📺 TV Shows")
    assert "Sci-Fi & Fantasy" in at.multiselect[0].options and "Self-Help" not in at.multiselect[0].options
    at.multiselect[0].set_value(["Drama", "Crime"])
    at.button[0].click().run()
    assert not at.exception and seen == [("tv", ["Drama", "Crime"], True)]
    assert len([b for b in at.button if b.label == "Add"]) == 1


def test_stats_and_suggestions_pages(temp_db, monkeypatch):
    conn = db.connect(temp_db)
    db.add_book(conn, title="Dune", isbn="9780441172719", status="Read")
    db.add_book(conn, title="Inception", kind="movie", year="2010", genre="Action", status="Read")
    conn.close()
    monkeypatch.setattr(tmdb, "similar", lambda kind, title, year=None, count=5: [
        (BookResult(title="Interstellar", year="2014"), "Fans of Inception also liked this")])
    stats = run("views/stats.py", "🎬 Movies")
    assert stats.title[0].value.endswith("Movies Stats")
    assert any(m.label.startswith("Watched in") for m in stats.metric)
    sugg = run("views/suggestions.py", "🎬 Movies")
    assert any("Interstellar" in m.value for m in sugg.markdown)
    [b for b in sugg.button if "Add to To Watch" in b.label][0].click().run()
    conn = db.connect(temp_db)
    assert [b["title"] for b in db.list_books(conn, kind="movie")] == ["Interstellar", "Inception"]
    assert [b["title"] for b in db.list_books(conn, kind="book")] == ["Dune"]


def test_sidebar_names_follow_the_library():
    from booktracker import ui
    assert (ui.kind_words("book")["my"], ui.kind_words("book")["add"]) == ("My Books", "Add a Book")
    assert (ui.kind_words("movie")["my"], ui.kind_words("movie")["add"]) == ("My Movies", "Add a Movie")
    assert (ui.kind_words("tv")["my"], ui.kind_words("tv")["add"]) == ("My TV Shows", "Add a TV Show")
    assert ui.status_label("Read", "tv") == "Watched" and ui.status_label("Read", "book") == "Read"
