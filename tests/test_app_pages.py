"""Click-through tests of the Streamlit pages (no browser needed)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from booktracker import book_search, config, db
from booktracker.book_search import BookResult

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    path = tmp_path / "books.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    return path


def run(page: str) -> AppTest:
    at = AppTest.from_file(str(ROOT / page), default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    return at


def test_home_app_loads(temp_db):
    run("app.py")


def test_my_books_empty(temp_db):
    at = run("views/my_books.py")
    assert "No books yet" in at.info[0].value


def test_search_and_add(temp_db, monkeypatch):
    fake = [BookResult(title="Dune", author="Frank Herbert", isbn="9780441172719",
                       genre="Science Fiction", source="test")]
    monkeypatch.setattr(book_search, "search_books", lambda q, limit=10: (fake, "Results from test"))
    at = run("views/add_book.py")
    at.text_input[0].input("Dune")
    at.button[0].click().run()   # Search (form submit)
    assert not at.exception
    add = [b for b in at.button if b.label == "Add"][0]
    add.click().run()
    assert not at.exception
    conn = db.connect(temp_db)
    books = db.list_books(conn)
    assert [(b["title"], b["status"], b["genre"]) for b in books] == [("Dune", "To Read", "Science Fiction")]


def test_author_results_can_be_filtered_by_year(temp_db, monkeypatch):
    fake = [BookResult(title=t, author="Christopher Paolini", year=y, source="test")
            for t, y in (("Eragon", "2002"), ("Eldest", "2005"), ("Murtagh", "2023"))]
    monkeypatch.setattr(book_search, "search_books", lambda q, limit=10: (fake, "Results from test"))
    at = run("views/add_book.py")
    at.text_input[0].input("Christopher Paolini")
    at.button[0].click().run()
    assert not at.exception
    assert len([b for b in at.button if b.label == "Add"]) == 3
    at.slider[0].set_value((2004, 2023)).run()
    assert len([b for b in at.button if b.label == "Add"]) == 2
    at.selectbox[0].set_value("Oldest first").run()
    assert not at.exception
    assert [m.value for m in at.markdown if m.value.startswith("**")][:2] == ["**Eldest**", "**Murtagh**"]


def test_tabs_and_edit(temp_db):
    conn = db.connect(temp_db)
    a = db.add_book(conn, title="Dune", isbn="9780441172719")
    db.add_book(conn, title="Emma", isbn="9780141439587", status="Read")
    conn.close()
    at = run("views/my_books.py")
    labels = [t.label for t in at.tabs]
    assert labels[0].endswith("To Read (1)") and labels[1].endswith("Currently Reading (0)") \
        and labels[2].endswith("Read (1)")
    # Edit Dune (first tab): set Read, 4 stars, notes
    at.selectbox[0].set_value("Read")
    at.text_area[0].input("Loved it")
    at.button[0].click().run()   # "Save changes" of the first form
    assert not at.exception
    conn = db.connect(temp_db)
    book = db.get_book(conn, a)
    assert (book["status"], book["notes"]) == ("Read", "Loved it")
    assert book["date_finished"]


def test_password_gate(temp_db, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    at = run("app.py")
    assert at.text_input[0].label == "Password"
    at.text_input[0].input("wrong").run()
    assert at.error and at.error[0].value == "Wrong password"
    at.text_input[0].input("s3cret").run()
    assert not at.exception and at.session_state["authenticated"]
