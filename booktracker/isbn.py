"""ISBN helpers: clean, validate and convert between ISBN-10 and ISBN-13.

Every book is matched across stores by its ISBN-13, so everything that comes in
(from APIs, store pages or the user) goes through `normalize()` first.
"""

from __future__ import annotations

import re


def clean(raw: str | None) -> str:
    """Strip spaces/hyphens and upper-case the X check digit."""
    return re.sub(r"[^0-9Xx]", "", raw or "").upper()


def is_valid_isbn10(isbn: str) -> bool:
    if not re.fullmatch(r"\d{9}[\dX]", isbn):
        return False
    total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(isbn))
    return total % 11 == 0


def is_valid_isbn13(isbn: str) -> bool:
    if not re.fullmatch(r"\d{13}", isbn):
        return False
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(isbn))
    return total % 10 == 0


def isbn10_to_13(isbn10: str) -> str:
    core = "978" + isbn10[:9]
    check = (10 - sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(core)) % 10) % 10
    return core + str(check)


def isbn13_to_10(isbn13: str) -> str | None:
    """Only 978-prefixed ISBN-13s have an ISBN-10 equivalent."""
    if not isbn13.startswith("978"):
        return None
    core = isbn13[3:12]
    total = sum((10 - i) * int(c) for i, c in enumerate(core))
    check = (11 - total % 11) % 11
    return core + ("X" if check == 10 else str(check))


def normalize(raw: str | None) -> str | None:
    """Return a valid ISBN-13 for any ISBN-10/13 input, or None if invalid."""
    isbn = clean(raw)
    if is_valid_isbn13(isbn):
        return isbn
    if is_valid_isbn10(isbn):
        return isbn10_to_13(isbn)
    return None
