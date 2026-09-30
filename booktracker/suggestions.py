"""Book suggestions.

Free mode (always available): for one book, look it up on Open Library and
suggest other works by the same author plus well-rated books sharing its
subjects.

AI mode (optional): send your Read list and ratings to Claude and get
personalised recommendations. Needs ANTHROPIC_API_KEY in `.env` / secrets.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from . import book_search, config
from .book_search import BookResult

log = logging.getLogger("booktracker.suggestions")
OL_SEARCH = "https://openlibrary.org/search.json"

# Subjects that describe the edition/format rather than the content
BORING_SUBJECTS = re.compile(
    r"^(nyt:|accessible book|protected daisy|in library|large type|lending library|"
    r"open library|overdrive|fiction, general|fiction$|general$|english |long now|"
    r"reading level|award|new york times|bestseller|translations|audiobook|internet archive)",
    re.I)


@dataclass
class Suggestion:
    book: BookResult
    reason: str


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", title.lower().split(":")[0].split("(")[0])


ENGLISH_ISBN_PREFIXES = ("9780", "9781", "9798")  # English-language ISBN groups
MIN_READERS = 10  # skip obscure/misfiled entries nobody has shelved


def _ol_search(params: dict) -> list[dict]:
    params = {"fields": book_search.OL_FIELDS + ",readinglog_count", "lang": "en", **params}
    return book_search._get(OL_SEARCH, params).get("docs", [])


def _lookup_work(book: dict) -> dict | None:
    """Find the Open Library work record for one of your books."""
    tries = []
    if book.get("isbn"):
        tries.append({"isbn": book["isbn"], "limit": 1})
    tries.append({"title": book["title"], "author": (book.get("author") or "").split(",")[0], "limit": 1})
    for params in tries:
        try:
            docs = _ol_search(params)
        except Exception as exc:
            log.warning("Open Library lookup failed: %s", exc)
            continue
        if docs:
            return docs[0]
    return None


def meaningful_subjects(subjects: list[str], limit: int = 3) -> list[str]:
    picked = []
    for s in subjects:
        s = s.strip()
        if not s or len(s) > 40 or BORING_SUBJECTS.search(s):
            continue
        if s.lower() not in (p.lower() for p in picked):
            picked.append(s)
        if len(picked) == limit:
            break
    return picked


def similar_books(book: dict, exclude_titles: set[str] | None = None, count: int = 5) -> list[Suggestion]:
    """Up to `count` suggestions: 2 by the same author, the rest by subject."""
    exclude = {_norm_title(t) for t in (exclude_titles or set())} | {_norm_title(book["title"])}
    work = _lookup_work(book)
    if not work:
        return []
    results: list[Suggestion] = []

    def add(doc: dict, reason: str) -> None:
        if (doc.get("readinglog_count") or 0) < MIN_READERS:
            return
        parsed = book_search._parse_openlibrary(doc)
        if not parsed or not parsed.isbn or _norm_title(parsed.title) in exclude:
            return
        if not parsed.isbn.startswith(ENGLISH_ISBN_PREFIXES):  # skip translations
            return
        exclude.add(_norm_title(parsed.title))
        results.append(Suggestion(parsed, reason))

    author_keys = work.get("author_key") or []
    if author_keys:
        try:
            for doc in _ol_search({"q": f"author_key:{author_keys[0]}", "sort": "readinglog", "limit": 10}):
                if len(results) >= 2:
                    break
                add(doc, f"Also by {', '.join(doc.get('author_name', [])[:1]) or 'the same author'}")
        except Exception as exc:
            log.warning("same-author search failed: %s", exc)

    # Books sharing two subjects are the closest matches; then one subject.
    subjects = meaningful_subjects(work.get("subject", []), 4)
    queries = []
    if len(subjects) >= 2:
        queries.append((subjects[:2], f"Also about {subjects[0].lower()} and {subjects[1].lower()}"))
    queries += [([s], f"Also about {s.lower()}") for s in subjects]
    for subs, reason in queries:
        if len(results) >= count:
            break
        q = " AND ".join(f'subject:"{s.lower()}"' for s in subs)
        try:
            for doc in _ol_search({"q": q, "sort": "readinglog", "limit": 20}):
                if len(results) >= count:
                    break
                add(doc, reason)
        except Exception as exc:
            log.warning("subject search failed (%s): %s", subs, exc)
    return results[:count]


# --------------------------------------------------------------------------- #
# Claude (optional)
# --------------------------------------------------------------------------- #
CLAUDE_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "author": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["title", "author", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["recommendations"],
    "additionalProperties": False,
}


def claude_available() -> bool:
    return bool(config.get_secret("ANTHROPIC_API_KEY"))


def build_prompt(books: list[dict], count: int) -> str:
    def line(b: dict) -> str:
        parts = [f"- {b['title']}"]
        if b.get("author"):
            parts.append(f"by {b['author']}")
        if b.get("genre"):
            parts.append(f"[{b['genre']}]")
        if b.get("rating"):
            parts.append(f"rated {b['rating']}/5")
        if b.get("notes"):
            parts.append(f"— my notes: {b['notes'][:200]}")
        return " ".join(parts)

    read = [b for b in books if b["status"] == "Read"]
    read.sort(key=lambda b: -(b.get("rating") or 0))
    dropped = [b for b in books if b["status"] == "Dropped"]
    to_read = [b for b in books if b["status"] == "To Read"]
    sections = ["Books I have read (highest rated first):", *map(line, read)]
    if dropped:
        sections += ["", "Books I started but dropped (I did not enjoy these):", *map(line, dropped)]
    if to_read:
        sections += ["", "Already on my to-read list (do not recommend these):",
                     *(f"- {b['title']}" for b in to_read)]
    sections += [
        "",
        f"Recommend {count} books I have not read yet that I am likely to enjoy, based on what I "
        "rated highly and what I dropped. Only real, published books with their correct author. "
        "Do not recommend anything listed above. For each, give a one-sentence reason that "
        "refers to specific books from my list.",
    ]
    return "\n".join(sections)


def claude_recommendations(books: list[dict], count: int = 5) -> list[Suggestion]:
    """Ask Claude for recommendations; each is then looked up for cover/ISBN."""
    import anthropic

    if not any(b["status"] == "Read" for b in books):
        raise ValueError("Mark at least one book as Read (ideally with a rating) first.")
    client = anthropic.Anthropic(api_key=config.get_secret("ANTHROPIC_API_KEY"))
    response = client.beta.messages.create(
        model=config.get_secret("CLAUDE_MODEL", "claude-opus-5-5"),
        max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium",
                       "format": {"type": "json_schema", "schema": CLAUDE_SCHEMA}},
        system="You are a well-read, honest book recommender.",
        messages=[{"role": "user", "content": build_prompt(books, count)}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined this request; try again later.")
    text = next((b.text for b in response.content if b.type == "text"), "{}")
    recs = json.loads(text).get("recommendations", [])[:count]

    known = {_norm_title(b["title"]) for b in books}
    suggestions = []
    for rec in recs:
        if _norm_title(rec["title"]) in known:
            continue
        found = None
        try:
            hits = book_search.search_openlibrary(f"{rec['title']}", 5)
            author_first = rec["author"].split()[-1].lower() if rec["author"] else ""
            found = next((h for h in hits if author_first in (h.author or "").lower()), None)
        except Exception as exc:
            log.warning("could not look up %s: %s", rec["title"], exc)
        book = found or BookResult(title=rec["title"], author=rec["author"], source="Claude")
        suggestions.append(Suggestion(book, rec["reason"]))
    return suggestions
