"""Find books by title (or ISBN) and return tidy, ready-to-save results.

Google Books is tried first (uses GOOGLE_BOOKS_API_KEY if you set one). If it
fails, is rate-limited or finds nothing, Open Library is used instead, so the
app keeps working without any key.
"""

from __future__ import annotations

import logging
import random
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
    rating: float | None = None       # average reader rating (0-5), for filtering/sorting only
    popularity: int | None = None     # how many readers rated / shelved it, for sorting only

    def to_db_fields(self) -> dict:
        data = asdict(self)
        for key in ("year", "source", "subjects", "rating", "popularity"):
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
        rating=info.get("averageRating"),
        popularity=info.get("ratingsCount"),
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
        rating=round(doc["ratings_average"], 2) if doc.get("ratings_average") else None,
        popularity=doc.get("want_to_read_count") or doc.get("ratings_count"),
    )


OL_FIELDS = ("key,title,author_name,author_key,isbn,cover_i,subject,first_publish_year,"
             "editions,editions.isbn,editions.cover_i,editions.title,"
             "ratings_average,ratings_count,want_to_read_count")


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
SORT_OPTIONS = ("Best match", "Highest rated", "Most popular", "Newest first", "Oldest first")


def year_of(book: BookResult) -> int | None:
    return int(book.year) if book.year and book.year.isdigit() else None


def year_bounds(books: list[BookResult]) -> tuple[int, int] | None:
    """(earliest, latest) release year in the results; None if no book has one."""
    years = [y for y in map(year_of, books) if y]
    return (min(years), max(years)) if years else None


def authors_of(book: BookResult) -> list[str]:
    return [a.strip() for a in (book.author or "").split(",") if a.strip()]


def author_counts(books: list[BookResult]) -> list[tuple[str, int]]:
    """Authors in the results, most books first (each author counted once per book)."""
    counts: dict[str, int] = {}
    for b in books:
        for a in dict.fromkeys(authors_of(b)):
            counts[a] = counts.get(a, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))


def _weighted_rating(book: BookResult, typical: float = 3.7, weight: int = 50) -> float:
    """Rating pulled toward the average when few readers back it (0 if unrated).

    Keeps a 4.9 from 3 readers below a 4.5 from 2,000 readers.
    """
    if not book.rating:
        return 0
    n = book.popularity or 0
    return (n * book.rating + weight * typical) / (n + weight)


def filter_and_sort(books: list[BookResult], first_year: int | None = None,
                    last_year: int | None = None, sort: str = "Best match",
                    keep_unknown_year: bool = True, authors: list[str] | None = None,
                    min_rating: float = 0, hide_isbns: set[str] | None = None) -> list[BookResult]:
    """Keep the books that pass every filter and put them in the chosen order.

    - years: inclusive range; books with no known year are kept unless
      `keep_unknown_year` is False (and are listed last when sorting by date)
    - authors: keep books by any of these authors (empty/None = everyone)
    - min_rating: drop books rated below this (books with no rating are dropped too)
    - hide_isbns: drop books whose ISBN is in this set (e.g. already in your list)
    """
    chosen = set(authors or [])
    out = []
    for b in books:
        y = year_of(b)
        if y is None:
            if not keep_unknown_year:
                continue
        elif (first_year is not None and y < first_year) or (last_year is not None and y > last_year):
            continue
        if chosen and not chosen & set(authors_of(b)):
            continue
        if min_rating and (b.rating or 0) < min_rating:
            continue
        if hide_isbns and b.isbn in hide_isbns:
            continue
        out.append(b)

    def ranked(key, reverse=True):
        known = sorted((b for b in out if key(b)), key=key, reverse=reverse)
        return known + [b for b in out if not key(b)]

    if sort == "Newest first":
        return ranked(year_of)
    if sort == "Oldest first":
        return ranked(year_of, reverse=False)
    if sort == "Highest rated":
        return ranked(_weighted_rating)
    if sort == "Most popular":
        return ranked(lambda b: b.popularity or 0)
    return out


# --------------------------------------------------------------------------- #
# Browse by genre
# --------------------------------------------------------------------------- #
GENRE_NAMES = [name for name, _ in GENRE_KEYWORDS]
GENRE_LIMIT = 60   # books fetched per genre search


def _genre_term(name: str) -> str:
    """The subject word the book sites use for one of our friendly genres."""
    for genre, words in GENRE_KEYWORDS:
        if genre == name:
            return words[0]
    return name.lower()


def _quote(term: str) -> str:
    return '"' + term.replace('"', "") + '"'


def _genre_openlibrary(genres: list[str], match_all: bool, keyword: str, limit: int) -> list[BookResult]:
    terms = [f"subject:{_quote(_genre_term(g))}" for g in genres]
    q = " ".join(terms) if match_all else "(" + " OR ".join(terms) + ")"
    if keyword.strip():
        q += f" {keyword.strip()}"
    return _openlibrary("q", q, limit)


def _genre_google(genres: list[str], match_all: bool, keyword: str, limit: int) -> list[BookResult]:
    extra = f" {keyword.strip()}" if keyword.strip() else ""
    if match_all:
        return _google(" ".join(f"subject:{_quote(_genre_term(g))}" for g in genres) + extra, limit)
    per_genre = max(5, min(40, limit // len(genres)))   # Google has no OR: one search per genre
    merged: list[BookResult] = []
    for g in genres:
        merged += _google(f"subject:{_quote(_genre_term(g))}{extra}", per_genre)
    return merged


def search_by_genres(genres: list[str], match_all: bool = True, keyword: str = "",
                     limit: int = GENRE_LIMIT) -> tuple[list[BookResult], str]:
    """Books in all (or any) of the chosen genres, optionally narrowed by a keyword.

    Open Library is tried first (it has reader ratings to sort by), then
    Google Books. Returns (results, message about the source used).
    """
    genres = [g for g in genres if g]
    if not genres:
        return [], "Pick at least one genre."
    problems = []
    for name, fn in (("Open Library", _genre_openlibrary), ("Google Books", _genre_google)):
        try:
            results = _dedupe(fn(genres, match_all, keyword, limit))
        except Exception as exc:
            log.warning("%s genre search failed: %s", name, exc)
            problems.append(f"{name} unavailable")
            continue
        if results:
            note = f"Results from {name}" + (f" ({'; '.join(problems)})" if problems else "")
            return results[:limit], note
        problems.append(f"{name} found nothing")
    return [], "No books found. " + "; ".join(problems)


def surprise_me(genres: list[str] | None = None, owned_isbns: set[str] | None = None,
                count: int = 5, rng: random.Random | None = None) -> tuple[list[BookResult], str]:
    """A few random, well-liked books you don't have yet.

    Uses the genres you picked (any of them); with none picked, one random genre.
    Prefers books with a decent rating from enough readers, and falls back to
    anything found if too few qualify.
    """
    rng = rng or random.Random()
    owned = owned_isbns or set()
    picked = [g for g in (genres or []) if g] or [rng.choice(GENRE_NAMES)]
    results, note = search_by_genres(picked, match_all=False)
    fresh = [b for b in results if not (b.isbn and b.isbn in owned)]
    liked = [b for b in fresh if (b.rating or 0) >= 3.5 and (b.popularity or 0) >= 20]
    pool = liked if len(liked) >= count else fresh
    if not pool:
        return [], note
    chosen = rng.sample(pool, min(count, len(pool)))
    return chosen, f"🎲 Surprise! {len(chosen)} random picks from {', '.join(picked)}. {note}"


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
