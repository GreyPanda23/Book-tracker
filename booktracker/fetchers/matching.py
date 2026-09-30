"""Decide whether a store's product really is the book we're pricing.

1. Same ISBN (after normalising ISBN-10/13)            -> match_method "isbn"
2. Otherwise the title must be very similar AND the
   author's surname must appear (when both are known) -> match_method "title_author"
Anything else is rejected, so a wrong book is never saved.
"""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

from rapidfuzz import fuzz

from .. import isbn as isbn_utils

if TYPE_CHECKING:
    from .base import Candidate

TITLE_THRESHOLD = 88
NOISE = re.compile(
    r"\b(paperback|hardcover|hardback|mass market|edition|english|by .*|book \d+|"
    r"a novel|the novel|movie tie-in|tie-in|export|reprint|illustrated|unabridged)\b")


def _plain(text: str) -> str:
    """Lower-case, accents removed, punctuation -> spaces. Keeps Arabic etc."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return re.sub(r"\s+", " ", re.sub(r"[^\w ]|_", " ", text)).strip()


def main_title(title: str) -> str:
    """'Dune: Deluxe Edition (Paperback)' -> 'dune'."""
    title = re.split(r"[:(\[]| - ", title or "")[0]
    cleaned = _plain(NOISE.sub(" ", _plain(title)))
    return re.sub(r"^(the|a|an) ", "", cleaned)


def surname(author: str | None) -> str | None:
    if not author:
        return None
    first = re.split(r"[;,&]| and ", author)[0].strip()
    if "," in (author.split(";")[0]):          # "Weir, Andy"
        first = author.split(",")[0]
    parts = _plain(first).split()
    return parts[-1] if parts else None


def title_matches(found: str, wanted: str) -> bool:
    a, b = main_title(found), main_title(wanted)
    if not a or not b:
        return False
    return fuzz.ratio(a, b) >= TITLE_THRESHOLD


def author_matches(found: str | None, wanted: str | None) -> bool:
    """The wanted author's surname must appear in the store's author field.

    If either side has no author we can't check, so only the title decides.
    """
    want = surname(wanted)
    if not want or not found:
        return True
    return want in _plain(found).split()


def verify(candidate: "Candidate", book: dict, other_editions: bool = False) -> str | None:
    """Return 'isbn' / 'title_author' if this product is the book, else None.

    With `other_editions` (the title + author fallback) a product with a
    different ISBN is accepted when title and author match - e.g. the
    paperback when you saved the hardback - and flagged as 'title_author'.
    """
    wanted = isbn_utils.normalize(book.get("isbn"))
    found = isbn_utils.normalize(candidate.isbn)
    if wanted and found:
        if wanted == found:
            return "isbn"
        if not other_editions:
            return None
    if title_matches(candidate.title, book.get("title", "")) and \
            author_matches(candidate.author, book.get("author")):
        return "title_author"
    return None


def pick_best(candidates: list["Candidate"], book: dict,
              other_editions: bool = False) -> tuple["Candidate", str] | None:
    """Best verified candidate: ISBN matches first, then cheapest in-stock."""
    verified = [(c, m) for c in candidates
                if (m := verify(c, book, other_editions)) and c.price_aed]
    if not verified:
        return None
    verified.sort(key=lambda cm: (cm[1] != "isbn", cm[0].in_stock is False, cm[0].price_aed))
    return verified[0]
