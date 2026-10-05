"""Find books by title (or ISBN) and return tidy, ready-to-save results.

Google Books is tried first (uses GOOGLE_BOOKS_API_KEY if you set one). If it
fails, is rate-limited or finds nothing, Open Library is used instead, so the
app keeps working without any key.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Callable

import httpx

from . import config, isbn as isbn_utils

log = logging.getLogger("booktracker.search")
USER_AGENT = "PersonalBookTracker/1.0 (personal, non-commercial)"
TIMEOUT = 15
AUTHOR_LIMIT = 30   # how many books to list when the query is an author's name

# Canonical genres, checked in order; the first keyword found in the
# categories/subjects wins. More specific genres come before broad ones.
GENRE_KEYWORDS = [
    ("Science Fiction", ["science fiction", "science-fiction", "sci-fi", "space opera", "dystopia"]),
    ("Fantasy", ["fantasy", "magic", "dragons"]),
    ("Mystery", ["mystery", "detective", "crime"]),
    ("Thriller", ["thriller", "suspense", "espionage"]),
    ("Horror", ["horror", "ghost stories"]),
    ("Romance", ["romance", "love stories"]),
    ("Historical Fiction", ["historical fiction"]),
    ("Young Adult", ["young adult", "juvenile fiction"]),
    ("Children", ["juvenile", "children"]),
    ("Graphic Novels", ["comics", "graphic novel", "manga"]),
    ("Poetry", ["poetry", "poems"]),
    ("Biography & Memoir", ["biography", "autobiography", "memoir"]),
    ("Self-Help", ["self-help", "self help", "personal development", "happiness", "success"]),
    ("Business & Economics", ["business", "economics", "finance", "management", "investing"]),
    ("Psychology", ["psychology"]),
    ("Philosophy", ["philosophy"]),
    ("Religion & Spirituality", ["religion", "spirituality", "islam", "christian"]),
    ("History", ["history"]),
    ("Science", ["science", "physics", "biology", "mathematics", "astronomy"]),
    ("Technology", ["computers", "technology", "programming"]),
    ("Health", ["health", "fitness", "nutrition", "medical"]),
    ("Cooking", ["cooking", "cookery", "recipes"]),
    ("Travel", ["travel"]),
    ("Classics", ["classic"]),
    ("Fiction", ["fiction", "novel"]),
    ("Nonfiction", ["nonfiction", "non-fiction"]),
]


@dataclass
class BookResult:
    title: str
    author: str | None = None
    isbn: str | None = None           # ISBN-13
    isbn10: str | None = None
    cover_url: str | None = None
    genre: str | None = None
    year: str | None = None
    source: str = ""
    subjects: list[str] = field(default_factory=list)

    def to_db_fields(self) -> dict:
        data = asdict(self)
        for key in ("year", "source", "subjects"):
            data.pop(key)
        return data


def guess_genre(labels: list[str] | None) -> str | None:
    """Map messy API categories/subjects to one friendly genre."""
    text = " | ".join(labels or []).lower()
    if not text:
        return None
    for genre, words in GENRE_KEYWORDS:
        if any(w in text for w in words):
            return genre
    return (labels[0].split("/")[0].strip().title() or None) if labels else None


def _get(url: str, params: dict) -> dict:
    with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT},
                      follow_redirects=True) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        return r.json()


# --------------------------------------------------------------------------- #
# Google Books
# --------------------------------------------------------------------------- #
def _parse_google(item: dict) -> BookResult | None:
    info = item.get("volumeInfo", {})
    if not info.get("title"):
        return None
    ids = {i.get("type"): i.get("identifier") for i in info.get("industryIdentifiers", [])}
    isbn13 = isbn_utils.normalize(ids.get("ISBN_13") or ids.get("ISBN_10"))
    images = info.get("imageLinks", {})
    cover = images.get("thumbnail") or images.get("smallThumbnail")
    title = info["title"] + (f": {info['subtitle']}" if info.get("subtitle") else "")
    return BookResult(
        title=title,
        author=", ".join(info.get("authors", [])) or None,
        isbn=isbn13,
        isbn10=isbn_utils.isbn13_to_10(isbn13) if isbn13 else None,
        cover_url=cover.replace("http://", "https://") if cover else None,
        genre=guess_genre(info.get("categories")),
        year=(info.get("publishedDate") or "")[:4] or None,
        source="Google Books",
        subjects=info.get("categories", []),
    )


def _google(q: str, limit: int) -> list[BookResult]:
    params = {"q": q, "maxResults": min(limit, 40), "printType": "books"}
    key = config.get_secret("GOOGLE_BOOKS_API_KEY")
    if key:
        params["key"] = key
    data = _get("https://www.googleapis.com/books/v1/volumes", params)
    results = [_parse_google(item) for item in data.get("items", [])]
    return [r for r in results if r]


def search_google(query: str, limit: int = 10) -> list[BookResult]:
    isbn13 = isbn_utils.normalize(query)
    if isbn13:
        return _google(f"isbn:{isbn13}", limit)[:limit]
    return _title_and_author(
        query, limit,
        lambda n: _google(f"intitle:{query}", n),
        lambda n: _google(f'inauthor:"{query}"', n))


# --------------------------------------------------------------------------- #
# Open Library
# --------------------------------------------------------------------------- #
def _best_isbn(candidates: list[str]) -> str | None:
    """Prefer an English-language (978-0/978-1) ISBN-13, then any valid one."""
    valid = [n for n in (isbn_utils.normalize(c) for c in candidates) if n]
    for prefix in ("9780", "9781"):
        for n in valid:
            if n.startswith(prefix):
                return n
    return valid[0] if valid else None


def _parse_openlibrary(doc: dict) -> BookResult | None:
    if not doc.get("title"):
        return None
    edition = (doc.get("editions") or {}).get("docs") or [{}]
    edition = edition[0]
    isbn13 = _best_isbn(edition.get("isbn", [])) or _best_isbn(doc.get("isbn", []))
    cover_id = edition.get("cover_i") or doc.get("cover_i")
    subjects = doc.get("subject", [])[:15]
    return BookResult(
        title=doc["title"],
        author=", ".join(doc.get("author_name", [])[:3]) or None,
        isbn=isbn13,
        isbn10=isbn_utils.isbn13_to_10(isbn13) if isbn13 else None,
        cover_url=f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg" if cover_id else None,
        genre=guess_genre(subjects),
        year=str(doc["first_publish_year"]) if doc.get("first_publish_year") else None,
        source="Open Library",
        subjects=subjects,
    )


OL_FIELDS = ("key,title,author_name,author_key,isbn,cover_i,subject,first_publish_year,"
             "editions,editions.isbn,editions.cover_i,editions.title")


def _openlibrary(field_name: str, value: str, limit: int) -> list[BookResult]:
    params = {"fields": OL_FIELDS, "limit": limit, "lang": "en", field_name: value}
    data = _get("https://openlibrary.org/search.json", params)
    results = [_parse_openlibrary(doc) for doc in data.get("docs", [])]
    return [r for r in results if r]


def search_openlibrary(query: str, limit: int = 10) -> list[BookResult]:
    isbn13 = isbn_utils.normalize(query)
    if isbn13:
        results = _openlibrary("isbn", isbn13, limit)
        for r in results:  # the edition searched for is the one the user wants
            r.isbn, r.isbn10 = isbn13, isbn_utils.isbn13_to_10(isbn13)
        return results[:limit]
    return _title_and_author(
        query, limit,
        lambda n: _openlibrary("title", query, n),
        lambda n: _openlibrary("author", query, n))


# --------------------------------------------------------------------------- #
# Title + author searching
# --------------------------------------------------------------------------- #
def _words(text: str | None) -> list[str]:
    """Lower-case words with accents removed ('Gabriel García Márquez' -> garcia...)."""
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.findall(r"[a-z0-9]+", plain.lower())


def _author_matches(book: BookResult, query_words: list[str]) -> bool:
    """True if every word of the query appears in the book's author name(s)."""
    return bool(query_words) and set(query_words) <= set(_words(book.author))


def _dedupe(books: list[BookResult]) -> list[BookResult]:
    """Drop repeats of the same book (same ISBN, or same title + author)."""
    seen, out = set(), []
    for b in books:
        keys = {("t", " ".join(_words(b.title)), " ".join(_words(b.author)))}
        if b.isbn:
            keys.add(("i", b.isbn))
        if keys & seen:
            continue
        seen |= keys
        out.append(b)
    return out


def _title_and_author(query: str, limit: int,
                      by_title: Callable[[int], list[BookResult]],
                      by_author: Callable[[int], list[BookResult]]) -> list[BookResult]:
    """Search by title and by author, and put the author's own books first.

    If the query is an author's name (every word of it is in a result's author
    field) the list grows to AUTHOR_LIMIT books so you see their whole shelf.
    Otherwise it behaves like a plain title search.
    """
    words = _words(query)
    results, errors = {}, []
    for name, fn, n in (("title", by_title, limit), ("author", by_author, AUTHOR_LIMIT)):
        try:
            results[name] = fn(n)
        except Exception as exc:
            errors.append(exc)
            results[name] = []
    if len(errors) == 2:
        raise errors[0]
    author_books = [b for b in results["author"] if _author_matches(b, words)]
    if not author_books:
        return _dedupe(results["title"])[:limit]
    # Books by that author first, then title matches (e.g. books *about* them).
    merged = _dedupe(author_books + [b for b in results["title"] if not _author_matches(b, words)])
    return merged[:AUTHOR_LIMIT]


# --------------------------------------------------------------------------- #
# Filtering and sorting results by release year
# --------------------------------------------------------------------------- #
SORT_OPTIONS = ("Best match", "Newest first", "Oldest first")


def year_of(book: BookResult) -> int | None:
    return int(book.year) if book.year and book.year.isdigit() else None


def year_bounds(books: list[BookResult]) -> tuple[int, int] | None:
    """(earliest, latest) release year in the results; None if no book has one."""
    years = [y for y in map(year_of, books) if y]
    return (min(years), max(years)) if years else None


def filter_and_sort(books: list[BookResult], first_year: int | None = None,
                    last_year: int | None = None, sort: str = "Best match",
                    keep_unknown_year: bool = True) -> list[BookResult]:
    """Keep books released between the two years (inclusive) and order them.

    Books with no known year are kept (shown last when sorting by date) unless
    `keep_unknown_year` is False.
    """
    out = []
    for b in books:
        y = year_of(b)
        if y is None:
            if keep_unknown_year:
                out.append(b)
        elif (first_year is None or y >= first_year) and (last_year is None or y <= last_year):
            out.append(b)
    if sort in ("Newest first", "Oldest first"):
        newest = sort == "Newest first"
        known = sorted((b for b in out if year_of(b)), key=year_of, reverse=newest)
        out = known + [b for b in out if not year_of(b)]
    return out


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def search_books(query: str, limit: int = 10) -> tuple[list[BookResult], str]:
    """Search by title, author name or ISBN. Returns (results, message about the source used)."""
    query = (query or "").strip()
    if not query:
        return [], "Type a title, author or ISBN to search."
    problems = []
    for name, fn in (("Google Books", search_google), ("Open Library", search_openlibrary)):
        try:
            results = fn(query, limit)
        except Exception as exc:  # network error, 429 quota, bad JSON ...
            log.warning("%s search failed: %s", name, exc)
            problems.append(f"{name} unavailable")
            continue
        if results:
            note = f"Results from {name}" + (f" ({'; '.join(problems)})" if problems else "")
            return results, note
        problems.append(f"{name} found nothing")
    return [], "No books found. " + "; ".join(problems)
