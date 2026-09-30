"""Find books by title (or ISBN) and return tidy, ready-to-save results.

Google Books is tried first (uses GOOGLE_BOOKS_API_KEY if you set one). If it
fails, is rate-limited or finds nothing, Open Library is used instead, so the
app keeps working without any key.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

import httpx

from . import config, isbn as isbn_utils

log = logging.getLogger("booktracker.search")
USER_AGENT = "PersonalBookTracker/1.0 (personal, non-commercial)"
TIMEOUT = 15

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


def search_google(query: str, limit: int = 10) -> list[BookResult]:
    isbn13 = isbn_utils.normalize(query)
    params = {"q": f"isbn:{isbn13}" if isbn13 else f"intitle:{query}",
              "maxResults": min(limit, 40), "printType": "books"}
    key = config.get_secret("GOOGLE_BOOKS_API_KEY")
    if key:
        params["key"] = key
    data = _get("https://www.googleapis.com/books/v1/volumes", params)
    results = [_parse_google(item) for item in data.get("items", [])]
    return [r for r in results if r][:limit]


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


def search_openlibrary(query: str, limit: int = 10) -> list[BookResult]:
    isbn13 = isbn_utils.normalize(query)
    params = {"fields": OL_FIELDS, "limit": limit, "lang": "en"}
    if isbn13:
        params["isbn"] = isbn13
    else:
        params["title"] = query
    data = _get("https://openlibrary.org/search.json", params)
    results = [_parse_openlibrary(doc) for doc in data.get("docs", [])]
    results = [r for r in results if r]
    if isbn13:  # the edition searched for is the one the user wants
        for r in results:
            r.isbn, r.isbn10 = isbn13, isbn_utils.isbn13_to_10(isbn13)
    return results[:limit]


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def search_books(query: str, limit: int = 10) -> tuple[list[BookResult], str]:
    """Search by title or ISBN. Returns (results, message about the source used)."""
    query = (query or "").strip()
    if not query:
        return [], "Type a title or ISBN to search."
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
