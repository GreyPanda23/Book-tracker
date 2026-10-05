import httpx
import pytest

from booktracker import book_search as bs

GOOGLE_ITEM = {"volumeInfo": {
    "title": "Dune", "authors": ["Frank Herbert"], "publishedDate": "1990-09-01",
    "industryIdentifiers": [{"type": "ISBN_10", "identifier": "0441172717"},
                            {"type": "ISBN_13", "identifier": "9780441172719"}],
    "categories": ["Fiction / Science Fiction / General"],
    "imageLinks": {"thumbnail": "http://books.google.com/x.jpg"}}}

OL_DOC = {"title": "Project Hail Mary", "author_name": ["Andy Weir"], "first_publish_year": 2021,
          "isbn": ["855651121X", "9780593135204"], "cover_i": 1,
          "subject": ["hard science-fiction", "nyt:hardcover-fiction=2021-05-23"],
          "editions": {"docs": [{"isbn": ["9780593395561", "0593395565"], "cover_i": 15208263}]}}


def test_parse_google():
    r = bs._parse_google(GOOGLE_ITEM)
    assert (r.title, r.author, r.isbn, r.isbn10) == ("Dune", "Frank Herbert", "9780441172719", "0441172717")
    assert r.genre == "Science Fiction"
    assert r.cover_url.startswith("https://")
    assert r.year == "1990"


def test_parse_openlibrary_prefers_edition_isbn():
    r = bs._parse_openlibrary(OL_DOC)
    assert r.isbn == "9780593395561"
    assert r.cover_url == "https://covers.openlibrary.org/b/id/15208263-M.jpg"
    assert r.genre == "Science Fiction"


def test_guess_genre():
    assert bs.guess_genre(["Self-Help", "Habits"]) == "Self-Help"
    assert bs.guess_genre(["Detective and mystery stories"]) == "Mystery"
    assert bs.guess_genre(["Gardening"]) == "Gardening"
    assert bs.guess_genre([]) is None


def test_fallback_to_openlibrary_when_google_fails(monkeypatch):
    def google_down(q, limit):
        raise httpx.HTTPStatusError("429", request=None, response=None)
    monkeypatch.setattr(bs, "search_google", google_down)
    monkeypatch.setattr(bs, "search_openlibrary", lambda q, limit: [bs._parse_openlibrary(OL_DOC)])
    results, note = bs.search_books("project hail mary")
    assert results[0].title == "Project Hail Mary"
    assert "Open Library" in note and "Google Books unavailable" in note


def test_both_fail_gives_message(monkeypatch):
    monkeypatch.setattr(bs, "search_google", lambda q, limit: [])
    def ol_down(q, limit):
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(bs, "search_openlibrary", ol_down)
    results, note = bs.search_books("zzz")
    assert results == [] and "No books found" in note


def test_empty_query():
    assert bs.search_books("  ")[0] == []


def test_to_db_fields_only_has_columns():
    fields = bs._parse_google(GOOGLE_ITEM).to_db_fields()
    assert set(fields) == {"title", "author", "isbn", "isbn10", "cover_url", "genre"}


@pytest.mark.live
def test_live_openlibrary():
    results = bs.search_openlibrary("Dune Frank Herbert", 3)
    assert results and results[0].title


def _book(title, author, isbn=None):
    return bs.BookResult(title=title, author=author, isbn=isbn)


def test_author_name_lists_their_books_first():
    paolini = [_book("Eragon", "Christopher Paolini", "9780375826689"),
               _book("Eldest", "Christopher Paolini"),
               _book("Eragon", "Christopher Paolini", "9780375826689")]   # duplicate edition
    other = [_book("Christopher and His Kind", "Christopher Isherwood")]
    out = bs._title_and_author("christopher paolini", 10, lambda n: other, lambda n: paolini + other)
    assert [b.title for b in out] == ["Eragon", "Eldest", "Christopher and His Kind"]


def test_author_search_ignores_accents_and_case():
    books = [_book("Cien años de soledad", "Gabriel García Márquez")]
    out = bs._title_and_author("GARCIA MARQUEZ", 10, lambda n: [], lambda n: books)
    assert len(out) == 1


def test_title_query_stays_a_title_search():
    titles = [_book("Origin", "Dan Brown")]
    authors = [_book("Something Else", "Origin Smith Jr")]   # author field has the word, query has more
    out = bs._title_and_author("origin of species", 10, lambda n: titles, lambda n: authors)
    assert out == titles


def test_author_search_failure_falls_back_to_title_results():
    def boom(n):
        raise RuntimeError("down")
    titles = [_book("Origin", "Dan Brown")]
    assert bs._title_and_author("origin", 10, lambda n: titles, boom) == titles
    import pytest
    with pytest.raises(RuntimeError):
        bs._title_and_author("origin", 10, boom, boom)
