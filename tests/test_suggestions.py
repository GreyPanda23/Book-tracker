import json
from pathlib import Path
from types import SimpleNamespace

import anthropic
import pytest
from streamlit.testing.v1 import AppTest

from booktracker import book_search, config, db, suggestions
from booktracker.book_search import BookResult

ROOT = Path(__file__).resolve().parent.parent


def doc(title, author, isbn, readers=100, subjects=None, author_key="A1"):
    return {"title": title, "author_name": [author], "author_key": [author_key],
            "isbn": [isbn], "readinglog_count": readers, "subject": subjects or []}


def test_meaningful_subjects_filters_noise():
    subs = ["nyt:hardcover-fiction=2021", "Accessible book", "Habit", "habit", "Fiction, general",
            "Behavior modification", "Psychology"]
    assert suggestions.meaningful_subjects(subs, 3) == ["Habit", "Behavior modification", "Psychology"]


def test_similar_books_mixes_author_and_subject(monkeypatch):
    calls = []

    def fake_search(params):
        calls.append(params)
        if "isbn" in params:
            return [doc("Project Hail Mary", "Andy Weir", "9780593395561",
                        subjects=["Accessible book", "Science fiction", "Space"])]
        if params.get("q", "").startswith("author_key"):
            return [doc("Project Hail Mary", "Andy Weir", "9780593395561"),   # itself -> skipped
                    doc("The Martian", "Andy Weir", "9780804139021"),
                    doc("Obscure pamphlet", "Andy Weir", "9780000000002", readers=1),  # too obscure
                    doc("Artemis", "Andy Weir", "9780525532101"),
                    doc("Third", "Andy Weir", "9780441172719")]
        return [doc("Ender's Game", "Orson Scott Card", "9780812550702"),
                doc("Der Schwarm", "Frank Schätzing", "9783462034806"),  # German ISBN -> skipped
                doc("Dune", "Frank Herbert", "9780441172719"),
                doc("Emma", "Jane Austen", "9780141439587"),
                doc("Excluded Owned", "X", "9780306406157")]

    monkeypatch.setattr(suggestions, "_ol_search", fake_search)
    result = suggestions.similar_books(
        {"title": "Project Hail Mary", "author": "Andy Weir", "isbn": "9780593395561"},
        exclude_titles={"Excluded Owned"})
    titles = [s.book.title for s in result]
    assert titles[:2] == ["The Martian", "Artemis"]
    assert len(titles) == 5
    assert "Der Schwarm" not in titles and "Obscure pamphlet" not in titles
    assert "Excluded Owned" not in titles and "Project Hail Mary" not in titles
    assert any('subject:"science fiction" AND subject:"space"' == c.get("q") for c in calls)


def test_similar_books_handles_unknown_book(monkeypatch):
    monkeypatch.setattr(suggestions, "_ol_search", lambda p: [])
    assert suggestions.similar_books({"title": "zzzz"}) == []


def test_build_prompt_contains_ratings_and_exclusions():
    books = [dict(title="Dune", author="Frank Herbert", status="Read", rating=5, genre="SF", notes="epic"),
             dict(title="Emma", author="Jane Austen", status="Dropped", rating=None, genre=None, notes=None),
             dict(title="Ulysses", author="James Joyce", status="To Read", rating=None, genre=None, notes=None)]
    prompt = suggestions.build_prompt(books, 5)
    assert "Dune by Frank Herbert [SF] rated 5/5" in prompt
    assert "dropped" in prompt and "Emma" in prompt
    assert "do not recommend these" in prompt and "- Ulysses" in prompt


class FakeClient:
    last_kwargs = None

    def __init__(self, api_key=None):
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        FakeClient.last_kwargs = kwargs
        payload = {"recommendations": [
            {"title": "The Martian", "author": "Andy Weir", "reason": "You loved Dune's survival themes"},
            {"title": "Dune", "author": "Frank Herbert", "reason": "already read -> filtered"},
        ]}
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps(payload))])


def test_claude_recommendations(monkeypatch):
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(book_search, "search_openlibrary", lambda q, limit=10: [
        BookResult(title="The Martian", author="Andy Weir", isbn="9780804139021")])
    books = [dict(title="Dune", author="Frank Herbert", status="Read", rating=5, genre=None, notes=None)]
    recs = suggestions.claude_recommendations(books)
    assert [r.book.title for r in recs] == ["The Martian"]
    assert recs[0].book.isbn == "9780804139021"
    kwargs = FakeClient.last_kwargs
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    assert kwargs["fallbacks"] == "default"


def test_claude_needs_read_books(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    with pytest.raises(ValueError):
        suggestions.claude_recommendations([dict(title="X", status="To Read")])


def test_suggestions_page_add_to_read(tmp_path, monkeypatch):
    path = tmp_path / "books.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    conn = db.connect(path)
    db.add_book(conn, title="Dune", author="Frank Herbert", isbn="9780441172719", status="Read")
    conn.close()
    monkeypatch.setattr(suggestions, "similar_books", lambda book, exclude_titles=None, count=5: [
        suggestions.Suggestion(BookResult(title="Children of Dune", author="Frank Herbert",
                                          isbn="9780441104024"), "Also by Frank Herbert")])
    at = AppTest.from_file(str(ROOT / "views/suggestions.py"), default_timeout=30)
    at.run()
    assert not at.exception
    add = [b for b in at.button if b.label == "➕ Add to To Read"][0]
    add.click().run()
    assert not at.exception
    conn = db.connect(path)
    assert [b["title"] for b in db.list_books(conn, "To Read")] == ["Children of Dune"]
