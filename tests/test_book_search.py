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


def _yr(title, year):
    return bs.BookResult(title=title, year=year)


def test_year_filter_and_sort():
    books = [_yr("B", "2010"), _yr("A", "2003"), _yr("C", "2020"), _yr("Nodate", None)]
    assert bs.year_bounds(books) == (2003, 2020)
    out = bs.filter_and_sort(books, 2005, 2020, "Newest first")
    assert [b.title for b in out] == ["C", "B", "Nodate"]
    out = bs.filter_and_sort(books, 2000, 2020, "Oldest first", keep_unknown_year=False)
    assert [b.title for b in out] == ["A", "B", "C"]
    assert [b.title for b in bs.filter_and_sort(books)] == ["B", "A", "C", "Nodate"]  # unchanged order
    assert bs.year_bounds([_yr("x", None)]) is None


def _rated(title, author, year, rating=None, pop=None, isbn=None):
    return bs.BookResult(title=title, author=author, year=year, rating=rating, popularity=pop, isbn=isbn)


def test_filter_by_author_rating_and_owned():
    books = [_rated("A", "Ann Lee", "2001", 4.5, 100, "1"), _rated("B", "Bob Ray", "2005", 3.0, 500, "2"),
             _rated("C", "Ann Lee, Bob Ray", "2010", None, 50, "3")]
    assert [b.title for b in bs.filter_and_sort(books, authors=["Ann Lee"])] == ["A", "C"]
    assert [b.title for b in bs.filter_and_sort(books, min_rating=4)] == ["A"]
    assert [b.title for b in bs.filter_and_sort(books, hide_isbns={"2"})] == ["A", "C"]
    assert [b.title for b in bs.filter_and_sort(books, sort="Most popular")] == ["B", "A", "C"]
    assert [b.title for b in bs.filter_and_sort(books, sort="Highest rated")] == ["A", "B", "C"]
    assert bs.author_counts(books) == [("Bob Ray", 2), ("Ann Lee", 2)] or \
        bs.author_counts(books)[0][1] == 2


def test_genre_query_all_vs_any(monkeypatch):
    seen = []
    monkeypatch.setattr(bs, "_openlibrary", lambda f, v, n: seen.append((f, v)) or [_rated("X", "Y", "2000")])
    res, note = bs.search_by_genres(["Fantasy", "Science Fiction"], match_all=True, keyword="dragons")
    assert seen[-1] == ("q", 'subject:"fantasy" subject:"science fiction" dragons')
    bs.search_by_genres(["Fantasy", "Horror"], match_all=False)
    assert seen[-1] == ("q", '(subject:"fantasy" OR subject:"horror")')
    assert res and "Open Library" in note
    assert bs.search_by_genres([])[0] == []


def test_genre_search_falls_back_to_google(monkeypatch):
    def down(*a):
        raise RuntimeError("down")
    monkeypatch.setattr(bs, "_openlibrary", down)
    monkeypatch.setattr(bs, "_google", lambda q, n: [_rated("G", "H", "1999")])
    res, note = bs.search_by_genres(["Mystery"])
    assert [b.title for b in res] == ["G"] and "Google Books" in note and "Open Library unavailable" in note


def test_rating_not_saved_to_database():
    fields = _rated("T", "A", "2000", 4.2, 10).to_db_fields()
    assert "rating" not in fields and "popularity" not in fields


def test_highest_rated_discounts_books_with_few_readers():
    few = _rated("Few", "A", "2000", 4.9, 3)
    many = _rated("Many", "B", "2000", 4.5, 2000)
    assert [b.title for b in bs.filter_and_sort([few, many], sort="Highest rated")] == ["Many", "Few"]


def test_surprise_me_picks_liked_unowned_books(monkeypatch):
    import random
    books = [_rated(f"Good{i}", "A", "2000", 4.2, 100, str(i)) for i in range(8)]
    books += [_rated("Meh", "A", "2000", 2.0, 100, "90"), _rated("Rare", "A", "2000", 4.9, 2, "91")]
    monkeypatch.setattr(bs, "search_by_genres", lambda g, match_all=True, keyword="", limit=60: (books, "from test"))
    picks, note = bs.surprise_me(["Fantasy"], owned_isbns={"0", "1"}, rng=random.Random(1))
    assert len(picks) == 5 and all(b.title.startswith("Good") for b in picks)
    assert not {b.isbn for b in picks} & {"0", "1"}
    assert "Fantasy" in note and "Surprise" in note
    picks, note = bs.surprise_me(None, rng=random.Random(2))   # no genre: one random genre is used
    assert len(picks) == 5 and any(g in note for g in bs.GENRE_NAMES)


def test_surprise_me_with_nothing_found(monkeypatch):
    monkeypatch.setattr(bs, "search_by_genres", lambda *a, **k: ([], "No books found."))
    assert bs.surprise_me(["Poetry"]) == ([], "No books found.")
