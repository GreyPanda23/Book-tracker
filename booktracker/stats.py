"""Reading statistics for the dashboard (pure functions over the books list)."""

from __future__ import annotations

from collections import Counter
from datetime import date


def _year(ts: str | None) -> int | None:
    try:
        return int(ts[:4]) if ts else None
    except ValueError:
        return None


def read_in_year(books: list[dict], year: int | None = None) -> list[dict]:
    year = year or date.today().year
    return [b for b in books if b["status"] == "Read" and _year(b.get("date_finished")) == year]


def drop_rate(books: list[dict]) -> float | None:
    """Dropped ÷ (Read + Dropped); None when you haven't finished/dropped anything yet."""
    read = sum(b["status"] == "Read" for b in books)
    dropped = sum(b["status"] == "Dropped" for b in books)
    return dropped / (read + dropped) if read + dropped else None


def average_rating(books: list[dict]) -> float | None:
    ratings = [b["rating"] for b in books if b.get("rating")]
    return sum(ratings) / len(ratings) if ratings else None


def top_genres(books: list[dict], n: int = 5) -> list[tuple[str, int]]:
    return Counter(b["genre"] for b in books if b.get("genre")).most_common(n)


def reads_per_month(books: list[dict], year: int | None = None) -> list[tuple[str, int]]:
    """[('Jan', 2), ('Feb', 0), ...] up to the current month (whole year if past)."""
    year = year or date.today().year
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    last = date.today().month if year == date.today().year else 12
    counts = Counter(int(b["date_finished"][5:7]) for b in read_in_year(books, year))
    return [(months[m - 1], counts.get(m, 0)) for m in range(1, last + 1)]
