"""SQLite database: schema and every read/write the app and jobs need.

Tables
- books      one row per book, identified by ISBN-13
- prices     one row per (book, store, in-store/online, date checked); never
             overwritten, so the full price history is kept
- store_links product pages you pasted for stores that can't search by ISBN
- runs       one row per weekly price update
- fetch_log  errors and block/CAPTCHA detections from the price fetchers
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import config, isbn as isbn_utils

STATUSES = ("To Read", "Read", "Dropped")
PRICE_TYPES = ("online", "in-store")
MATCH_METHODS = ("isbn", "title_author")

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    author        TEXT,
    isbn          TEXT UNIQUE,              -- ISBN-13, used to match stores
    isbn10        TEXT,
    cover_url     TEXT,
    genre         TEXT,
    status        TEXT NOT NULL DEFAULT 'To Read'
                  CHECK (status IN ('To Read', 'Read', 'Dropped')),
    date_added    TEXT NOT NULL,
    date_finished TEXT,                     -- set when status becomes Read/Dropped
    rating        INTEGER CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
    notes         TEXT,
    target_price  REAL                      -- AED; e-mail alert below this
);

CREATE TABLE IF NOT EXISTS prices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id         INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    isbn            TEXT,
    store_name      TEXT NOT NULL,
    type            TEXT NOT NULL CHECK (type IN ('online', 'in-store')),
    branch_location TEXT,                   -- Dubai branch(es) for in-store rows
    price_aed       REAL,
    in_stock        INTEGER,                -- 1 / 0 / NULL (unknown)
    product_url     TEXT,
    match_method    TEXT CHECK (match_method IN ('isbn', 'title_author')),
    date_checked    TEXT NOT NULL,
    run_id          INTEGER REFERENCES runs(id)
);
CREATE INDEX IF NOT EXISTS idx_prices_book ON prices(book_id, date_checked);

CREATE TABLE IF NOT EXISTS store_links (
    book_id    INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    store_name TEXT NOT NULL,
    url        TEXT NOT NULL,              -- product page you pasted (e.g. Virgin)
    PRIMARY KEY (book_id, store_name)
);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    books_checked INTEGER DEFAULT 0,
    prices_saved  INTEGER DEFAULT 0,
    errors        INTEGER DEFAULT 0,
    status        TEXT DEFAULT 'running'   -- running / ok / failed
);

CREATE TABLE IF NOT EXISTS fetch_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER REFERENCES runs(id),
    store_name TEXT,
    book_id    INTEGER,
    level      TEXT,                        -- error / blocked / not_found / info
    message    TEXT,
    created_at TEXT NOT NULL
);
"""

EDITABLE_BOOK_FIELDS = {
    "title", "author", "isbn", "isbn10", "cover_url", "genre", "status",
    "date_finished", "rating", "notes", "target_price",
}


def now() -> str:
    """Current UTC time as ISO text (what every timestamp column stores)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- #
# Connection
# --------------------------------------------------------------------------- #
def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open (and create if needed) the database."""
    path = Path(path or config.DB_PATH)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def _rows(cursor: sqlite3.Cursor) -> list[dict]:
    return [dict(r) for r in cursor.fetchall()]


# --------------------------------------------------------------------------- #
# Books
# --------------------------------------------------------------------------- #
def _validate_book_fields(fields: dict) -> dict:
    fields = {k: v for k, v in fields.items() if k in EDITABLE_BOOK_FIELDS}
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    if fields.get("rating") is not None:
        rating = int(fields["rating"])
        if not 1 <= rating <= 5:
            raise ValueError("rating must be between 1 and 5")
        fields["rating"] = rating
    if fields.get("target_price") is not None:
        price = float(fields["target_price"])
        if price < 0:
            raise ValueError("target price cannot be negative")
        fields["target_price"] = price
    if "isbn" in fields:
        raw = fields["isbn"]
        fields["isbn"] = isbn_utils.normalize(raw) if raw else None
        if raw and fields["isbn"] is None:
            raise ValueError(f"invalid ISBN: {raw}")
        if fields["isbn"] and not fields.get("isbn10"):
            fields["isbn10"] = isbn_utils.isbn13_to_10(fields["isbn"])
    return fields


def add_book(conn: sqlite3.Connection, **fields: Any) -> int:
    """Insert a book and return its id.

    If a book with the same ISBN already exists, nothing is inserted and the
    existing id is returned (so adding twice never creates duplicates).
    """
    if not fields.get("title"):
        raise ValueError("title is required")
    fields = _validate_book_fields(fields)
    if fields.get("isbn"):
        existing = get_book_by_isbn(conn, fields["isbn"])
        if existing:
            return existing["id"]
    fields.setdefault("status", "To Read")
    fields["date_added"] = now()
    if fields["status"] in ("Read", "Dropped") and not fields.get("date_finished"):
        fields["date_finished"] = fields["date_added"]
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    cur = conn.execute(f"INSERT INTO books ({cols}) VALUES ({marks})", list(fields.values()))
    conn.commit()
    return cur.lastrowid


def get_book(conn: sqlite3.Connection, book_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    return dict(row) if row else None


def get_book_by_isbn(conn: sqlite3.Connection, isbn: str) -> dict | None:
    isbn13 = isbn_utils.normalize(isbn)
    if not isbn13:
        return None
    row = conn.execute("SELECT * FROM books WHERE isbn = ?", (isbn13,)).fetchone()
    return dict(row) if row else None


def list_books(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    """All books (newest first), optionally only one status."""
    if status:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        return _rows(conn.execute(
            "SELECT * FROM books WHERE status = ? ORDER BY date_added DESC, id DESC", (status,)))
    return _rows(conn.execute("SELECT * FROM books ORDER BY date_added DESC, id DESC"))


def update_book(conn: sqlite3.Connection, book_id: int, **fields: Any) -> None:
    """Change any editable field(s) of a book."""
    fields = _validate_book_fields(fields)
    if not fields:
        return
    if "status" in fields:
        current = get_book(conn, book_id)
        if current and current["status"] != fields["status"]:
            finished = fields["status"] in ("Read", "Dropped")
            fields.setdefault("date_finished", now() if finished else None)
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE books SET {sets} WHERE id = ?", [*fields.values(), book_id])
    conn.commit()


def set_status(conn: sqlite3.Connection, book_id: int, status: str) -> None:
    update_book(conn, book_id, status=status)


def delete_book(conn: sqlite3.Connection, book_id: int) -> None:
    """Remove a book and its price history."""
    conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    conn.commit()


# --------------------------------------------------------------------------- #
# Prices
# --------------------------------------------------------------------------- #
def add_price(
    conn: sqlite3.Connection,
    book_id: int,
    store_name: str,
    type: str,
    price_aed: float | None,
    in_stock: bool | None = None,
    product_url: str | None = None,
    branch_location: str | None = None,
    match_method: str = "isbn",
    date_checked: str | None = None,
    run_id: int | None = None,
    commit: bool = True,
) -> int:
    """Append one price observation (history is never overwritten)."""
    if type not in PRICE_TYPES:
        raise ValueError(f"type must be one of {PRICE_TYPES}")
    if match_method not in MATCH_METHODS:
        raise ValueError(f"match_method must be one of {MATCH_METHODS}")
    if price_aed is not None and price_aed < 0:
        raise ValueError("price cannot be negative")
    book = get_book(conn, book_id)
    if not book:
        raise ValueError(f"no book with id {book_id}")
    cur = conn.execute(
        """INSERT INTO prices (book_id, isbn, store_name, type, branch_location, price_aed,
                               in_stock, product_url, match_method, date_checked, run_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (book_id, book["isbn"], store_name, type, branch_location, price_aed,
         None if in_stock is None else int(bool(in_stock)), product_url, match_method,
         date_checked or now(), run_id),
    )
    if commit:
        conn.commit()
    return cur.lastrowid


def latest_prices(conn: sqlite3.Connection, book_id: int) -> list[dict]:
    """Most recent observation for each store + type, cheapest first."""
    return _rows(conn.execute(
        """SELECT p.* FROM prices p
           JOIN (SELECT store_name, type, MAX(date_checked) AS d
                   FROM prices WHERE book_id = ? GROUP BY store_name, type) last
             ON p.store_name = last.store_name AND p.type = last.type AND p.date_checked = last.d
          WHERE p.book_id = ?
          GROUP BY p.store_name, p.type
          ORDER BY p.price_aed IS NULL, p.price_aed""",
        (book_id, book_id)))


def cheapest_price(conn: sqlite3.Connection, book_id: int, in_stock_only: bool = False) -> dict | None:
    """Cheapest of the latest prices (optionally only in-stock ones)."""
    for row in latest_prices(conn, book_id):
        if row["price_aed"] is None:
            continue
        if in_stock_only and row["in_stock"] == 0:
            continue
        return row
    return None


def price_history(conn: sqlite3.Connection, book_id: int) -> list[dict]:
    """Every price observation for a book, oldest first (for the chart)."""
    return _rows(conn.execute(
        "SELECT * FROM prices WHERE book_id = ? AND price_aed IS NOT NULL "
        "ORDER BY date_checked, store_name", (book_id,)))


def set_store_link(conn: sqlite3.Connection, book_id: int, store_name: str, url: str | None) -> None:
    """Save (or with an empty url, remove) a product link for one store."""
    if url and not url.startswith("https://"):
        raise ValueError("the link must start with https://")
    if url:
        conn.execute("INSERT OR REPLACE INTO store_links (book_id, store_name, url) VALUES (?, ?, ?)",
                     (book_id, store_name, url.strip()))
    else:
        conn.execute("DELETE FROM store_links WHERE book_id = ? AND store_name = ?", (book_id, store_name))
    conn.commit()


def store_links(conn: sqlite3.Connection, book_id: int | None = None) -> dict:
    """{book_id: {store_name: url}} (or {store_name: url} for one book)."""
    rows = conn.execute("SELECT * FROM store_links" + (" WHERE book_id = ?" if book_id else ""),
                        (book_id,) if book_id else ()).fetchall()
    links: dict = {}
    for r in rows:
        links.setdefault(r["book_id"], {})[r["store_name"]] = r["url"]
    return links.get(book_id, {}) if book_id else links


# --------------------------------------------------------------------------- #
# Runs & fetch log
# --------------------------------------------------------------------------- #
def start_run(conn: sqlite3.Connection) -> int:
    cur = conn.execute("INSERT INTO runs (started_at) VALUES (?)", (now(),))
    conn.commit()
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, books_checked: int, prices_saved: int,
               errors: int, status: str = "ok") -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, books_checked = ?, prices_saved = ?, errors = ?, "
        "status = ? WHERE id = ?",
        (now(), books_checked, prices_saved, errors, status, run_id))
    conn.commit()


def last_successful_run(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT * FROM runs WHERE status = 'ok' ORDER BY finished_at DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def log_fetch(conn: sqlite3.Connection, store_name: str, level: str, message: str,
              book_id: int | None = None, run_id: int | None = None, commit: bool = True) -> None:
    conn.execute(
        "INSERT INTO fetch_log (run_id, store_name, book_id, level, message, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, store_name, book_id, level, message[:1000], now()))
    if commit:
        conn.commit()


def store_health(conn: sqlite3.Connection) -> list[dict]:
    """Per store: last successful price and last problem (for the health table)."""
    return _rows(conn.execute(
        """SELECT s.store_name,
                  (SELECT MAX(date_checked) FROM prices p WHERE p.store_name = s.store_name
                      AND p.price_aed IS NOT NULL) AS last_success,
                  (SELECT created_at FROM fetch_log f WHERE f.store_name = s.store_name
                      AND f.level IN ('error', 'blocked') ORDER BY id DESC LIMIT 1) AS last_problem_at,
                  (SELECT level || ': ' || message FROM fetch_log f WHERE f.store_name = s.store_name
                      AND f.level IN ('error', 'blocked') ORDER BY id DESC LIMIT 1) AS last_problem
             FROM (SELECT store_name FROM prices UNION SELECT store_name FROM fetch_log) s
            ORDER BY s.store_name"""))


def executemany_prices(conn: sqlite3.Connection, rows: Iterable[dict], run_id: int | None) -> int:
    """Insert many price dicts (as produced by the weekly job) in one transaction."""
    count = 0
    for row in rows:
        add_price(conn, run_id=run_id, commit=False, **row)
        count += 1
    conn.commit()
    return count
