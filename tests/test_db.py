import pytest

from booktracker import db, isbn


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    yield c
    c.close()


def _book(conn, **kw):
    data = dict(title="Dune", author="Frank Herbert", isbn="9780441172719", genre="Fiction")
    data.update(kw)
    return db.add_book(conn, **data)


# ---- ISBN helpers ----------------------------------------------------------
def test_isbn_normalize_converts_10_to_13():
    assert isbn.normalize("0-441-17271-7") == "9780441172719"
    assert isbn.normalize("978-0-441-17271-9") == "9780441172719"


def test_isbn_invalid_returns_none():
    assert isbn.normalize("1234567890") is None
    assert isbn.normalize("") is None
    assert isbn.normalize(None) is None


def test_isbn13_to_10_handles_x_check_digit():
    assert isbn.isbn13_to_10("9780306406157") == "0306406152"
    assert isbn.isbn13_to_10("9780804429573") == "080442957X"
    assert isbn.isbn13_to_10("9791234567896") is None


# ---- Books -----------------------------------------------------------------
def test_add_and_get_book(conn):
    book_id = _book(conn)
    book = db.get_book(conn, book_id)
    assert book["title"] == "Dune"
    assert book["status"] == "To Read"
    assert book["isbn10"] == "0441172717"
    assert book["date_added"]


def test_add_book_accepts_isbn10_and_dedupes(conn):
    first = _book(conn)
    second = _book(conn, isbn="0441172717")
    assert first == second
    assert len(db.list_books(conn)) == 1


def test_add_book_requires_title(conn):
    with pytest.raises(ValueError):
        db.add_book(conn, title="")


def test_invalid_isbn_rejected(conn):
    with pytest.raises(ValueError):
        _book(conn, isbn="123")


def test_book_without_isbn_allowed(conn):
    book_id = db.add_book(conn, title="Old pamphlet")
    assert db.get_book(conn, book_id)["isbn"] is None


def test_status_changes_and_filter(conn):
    a = _book(conn)
    b = _book(conn, title="Emma", isbn="9780141439587")
    db.set_status(conn, a, "Read")
    assert [x["id"] for x in db.list_books(conn, "Read")] == [a]
    assert [x["id"] for x in db.list_books(conn, "To Read")] == [b]
    assert db.get_book(conn, a)["date_finished"]
    db.set_status(conn, a, "To Read")
    assert db.get_book(conn, a)["date_finished"] is None


def test_invalid_status_rejected(conn):
    book_id = _book(conn)
    with pytest.raises(ValueError):
        db.set_status(conn, book_id, "Reading")
    with pytest.raises(ValueError):
        db.list_books(conn, "Unknown")


def test_rating_and_notes(conn):
    book_id = _book(conn)
    db.update_book(conn, book_id, rating=4, notes="Great world-building", target_price=40)
    book = db.get_book(conn, book_id)
    assert (book["rating"], book["notes"], book["target_price"]) == (4, "Great world-building", 40.0)
    with pytest.raises(ValueError):
        db.update_book(conn, book_id, rating=6)
    with pytest.raises(ValueError):
        db.update_book(conn, book_id, target_price=-1)


def test_update_ignores_unknown_fields(conn):
    book_id = _book(conn)
    db.update_book(conn, book_id, id=999, date_added="x")
    assert db.get_book(conn, book_id)["id"] == book_id


def test_delete_book_cascades_prices(conn):
    book_id = _book(conn)
    db.add_price(conn, book_id, "Magrudy's", "online", 45.0)
    db.delete_book(conn, book_id)
    assert db.get_book(conn, book_id) is None
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 0


# ---- Prices ----------------------------------------------------------------
def test_price_history_is_kept(conn):
    book_id = _book(conn)
    db.add_price(conn, book_id, "Amazon.ae", "online", 50.0, True, date_checked="2026-09-06T08:00:00+00:00")
    db.add_price(conn, book_id, "Amazon.ae", "online", 42.0, True, date_checked="2026-09-13T08:00:00+00:00")
    history = db.price_history(conn, book_id)
    assert [h["price_aed"] for h in history] == [50.0, 42.0]
    latest = db.latest_prices(conn, book_id)
    assert len(latest) == 1 and latest[0]["price_aed"] == 42.0
    assert latest[0]["isbn"] == "9780441172719"


def test_latest_prices_and_cheapest(conn):
    book_id = _book(conn)
    t = "2026-09-13T08:00:00+00:00"
    db.add_price(conn, book_id, "Magrudy's", "in-store", 55.0, True, branch_location="City Walk", date_checked=t)
    db.add_price(conn, book_id, "Magrudy's", "online", 55.0, True, date_checked=t)
    db.add_price(conn, book_id, "Amazon.ae", "online", 39.0, False, date_checked=t)
    db.add_price(conn, book_id, "Virgin Megastore", "online", 48.0, True, date_checked=t)
    db.add_price(conn, book_id, "Noon", "online", None, None, date_checked=t)
    latest = db.latest_prices(conn, book_id)
    assert len(latest) == 5
    assert latest[0]["store_name"] == "Amazon.ae"
    assert latest[-1]["price_aed"] is None
    assert db.cheapest_price(conn, book_id)["store_name"] == "Amazon.ae"
    assert db.cheapest_price(conn, book_id, in_stock_only=True)["store_name"] == "Virgin Megastore"


def test_add_price_validation(conn):
    book_id = _book(conn)
    with pytest.raises(ValueError):
        db.add_price(conn, book_id, "X", "shop", 10)
    with pytest.raises(ValueError):
        db.add_price(conn, book_id, "X", "online", -5)
    with pytest.raises(ValueError):
        db.add_price(conn, 999, "X", "online", 5)
    with pytest.raises(ValueError):
        db.add_price(conn, book_id, "X", "online", 5, match_method="guess")


def test_runs_and_logs(conn):
    book_id = _book(conn)
    assert db.last_successful_run(conn) is None
    run = db.start_run(conn)
    db.add_price(conn, book_id, "Magrudy's", "online", 45.0, run_id=run)
    db.log_fetch(conn, "Noon", "blocked", "CAPTCHA page detected", book_id, run)
    db.finish_run(conn, run, books_checked=1, prices_saved=1, errors=1)
    assert db.last_successful_run(conn)["id"] == run
    health = {h["store_name"]: h for h in db.store_health(conn)}
    assert health["Magrudy's"]["last_success"]
    assert health["Noon"]["last_problem"].startswith("blocked")
