"""Find movies and TV shows (title, genre, similar titles) through TMDB.

TMDB is free but needs a key: make an account at themoviedb.org, open Settings > API
and put the "API Key" (or the longer "API Read Access Token") in TMDB_API_KEY.
Results use the same BookResult shape as book_search, so every page can treat them alike.
Movies and shows have no ISBN or author; they are told apart by title + release year.
"""

from __future__ import annotations

import logging
import random
import re

import httpx

from . import book_search, config
from .book_search import BookResult, _dedupe, _words, pick_surprises

log = logging.getLogger("booktracker.tmdb")
API = "https://api.themoviedb.org/3"
IMAGE = "https://image.tmdb.org/t/p/w342"
PAGE = 20               # TMDB returns 20 results per page
KINDS = ("movie", "tv")
NO_KEY = "Add a free TMDB_API_KEY to look up movies and TV shows (see the README)."

GENRE_IDS = {
    "movie": {"Action": 28, "Adventure": 12, "Animation": 16, "Comedy": 35, "Crime": 80,
              "Documentary": 99, "Drama": 18, "Family": 10751, "Fantasy": 14, "History": 36,
              "Horror": 27, "Music": 10402, "Mystery": 9648, "Romance": 10749,
              "Science Fiction": 878, "Thriller": 53, "War": 10752, "Western": 37},
    "tv": {"Action & Adventure": 10759, "Animation": 16, "Comedy": 35, "Crime": 80,
           "Documentary": 99, "Drama": 18, "Family": 10751, "Kids": 10762, "Mystery": 9648,
           "News": 10763, "Reality": 10764, "Sci-Fi & Fantasy": 10765, "Soap": 10766,
           "Talk": 10767, "War & Politics": 10768, "Western": 37},
}
GENRE_NAMES = {kind: list(ids) for kind, ids in GENRE_IDS.items()}


class MissingKey(RuntimeError):
    pass


def available() -> bool:
    return bool(config.get_secret("TMDB_API_KEY"))


def _get(path: str, **params) -> dict:
    key = config.get_secret("TMDB_API_KEY")
    if not key:
        raise MissingKey(NO_KEY)
    headers = {"User-Agent": book_search.USER_AGENT}
    if key.startswith("eyJ"):          # the long "Read Access Token" is sent as a bearer token
        headers["Authorization"] = f"Bearer {key}"
    else:
        params["api_key"] = key
    with httpx.Client(timeout=book_search.TIMEOUT, headers=headers) as client:
        r = client.get(f"{API}{path}", params=params)
        r.raise_for_status()
        return r.json()


def _genre_names(kind: str, ids: list[int]) -> list[str]:
    by_id = {i: name for name, i in GENRE_IDS[kind].items()}
    return [by_id[i] for i in ids if i in by_id]


def parse_item(kind: str, item: dict) -> BookResult | None:
    title = item.get("title") if kind == "movie" else item.get("name")
    if not title:
        return None
    genres = _genre_names(kind, item.get("genre_ids") or [g["id"] for g in item.get("genres", [])])
    votes = item.get("vote_count") or 0
    date = item.get("release_date") if kind == "movie" else item.get("first_air_date")
    poster = item.get("poster_path")
    return BookResult(
        title=title,
        cover_url=f"{IMAGE}{poster}" if poster else None,
        genre=genres[0] if genres else None,
        year=(date or "")[:4] or None,
        source="TMDB",
        subjects=genres,
        rating=round(item["vote_average"] / 2, 2) if votes and item.get("vote_average") else None,
        popularity=votes,
    )


def _parse_all(kind: str, items: list[dict]) -> list[BookResult]:
    return [r for r in (parse_item(kind, i) for i in items) if r]


def _failed(exc: Exception) -> str:
    if isinstance(exc, MissingKey):
        return NO_KEY
    log.warning("TMDB request failed: %s", exc)
    return f"TMDB unavailable ({exc})"


def _found(kind: str, results: list[BookResult]) -> str:
    return "Results from TMDB" if results else f"No {'movies' if kind == 'movie' else 'TV shows'} found on TMDB."


# --------------------------------------------------------------------------- #
# Searching by title
# --------------------------------------------------------------------------- #
def search_media(kind: str, query: str, limit: int = 10) -> tuple[list[BookResult], str]:
    query = (query or "").strip()
    if not query:
        return [], f"Type a {'movie' if kind == 'movie' else 'TV show'} title to search."
    try:
        data = _get(f"/search/{kind}", query=query, include_adult="false")
    except Exception as exc:
        return [], _failed(exc)
    results = _dedupe(_parse_all(kind, data.get("results", [])))[:limit]
    return results, _found(kind, results)


# --------------------------------------------------------------------------- #
# Browsing by genre
# --------------------------------------------------------------------------- #
def _by_keyword(kind: str, ids: list[int], match_all: bool, keyword: str, pages: int) -> list[dict]:
    """Title search narrowed to the chosen genres (TMDB can't combine a keyword with genres)."""
    wanted, items = set(ids), []
    for page in range(1, pages + 1):
        data = _get(f"/search/{kind}", query=keyword, include_adult="false", page=page)
        items += data.get("results", [])
        if page >= data.get("total_pages", 1):
            break
    return [i for i in items if (wanted <= set(i.get("genre_ids", [])) if match_all
                                 else wanted & set(i.get("genre_ids", [])))]


def search_by_genres(kind: str, genres: list[str], match_all: bool = True, keyword: str = "",
                     limit: int = book_search.GENRE_LIMIT) -> tuple[list[BookResult], str]:
    """The most popular titles in all (or any) of the chosen genres."""
    ids = [GENRE_IDS[kind][g] for g in genres if g in GENRE_IDS[kind]]
    if not ids:
        return [], "Pick at least one genre."
    pages = -(-limit // PAGE)
    try:
        if keyword.strip():
            items = _by_keyword(kind, ids, match_all, keyword.strip(), pages)
        else:
            items = []
            for page in range(1, pages + 1):
                data = _get(f"/discover/{kind}", with_genres=(",".join if match_all else "|".join)(map(str, ids)),
                            sort_by="popularity.desc", include_adult="false", page=page,
                            **{"vote_count.gte": 100})
                items += data.get("results", [])
                if page >= data.get("total_pages", 1):
                    break
    except Exception as exc:
        return [], _failed(exc)
    results = _dedupe(_parse_all(kind, items))[:limit]
    return results, _found(kind, results)


def surprise(kind: str, genres: list[str] | None = None, owned: set[str] | None = None,
             count: int = 5, rng: random.Random | None = None) -> tuple[list[BookResult], str]:
    """A few random, well-liked titles you don't have yet."""
    rng = rng or random.Random()
    picked = [g for g in (genres or []) if g] or [rng.choice(GENRE_NAMES[kind])]
    results, note = search_by_genres(kind, picked, match_all=False)
    chosen = pick_surprises(results, owned or set(), count, rng)
    if not chosen:
        return [], note
    return chosen, f"🎲 Surprise! {len(chosen)} random picks from {', '.join(picked)}. {note}"


# --------------------------------------------------------------------------- #
# Similar titles
# --------------------------------------------------------------------------- #
def similar(kind: str, title: str, year: str | None = None,
            count: int = 5) -> list[tuple[BookResult, str]]:
    """(title, reason) pairs: what TMDB's viewers of this title also watched."""
    try:
        found = _get(f"/search/{kind}", query=title, include_adult="false").get("results", [])
    except MissingKey:
        raise
    except Exception as exc:
        log.warning("TMDB lookup failed: %s", exc)
        return []
    parsed = [(item, parse_item(kind, item)) for item in found]
    wanted = " ".join(_words(title))
    home = next(((i, b) for i, b in parsed if b and " ".join(_words(b.title)) == wanted
                 and (not year or b.year == year)),
                next(((i, b) for i, b in parsed if b), None))
    if not home:
        return []
    item, base = home
    exclude = {" ".join(_words(base.title))}
    out: list[tuple[BookResult, str]] = []
    for route in ("recommendations", "similar"):
        for rec in _parse_all(kind, _get(f"/{kind}/{item['id']}/{route}").get("results", [])):
            key = " ".join(_words(rec.title))
            if key in exclude or (rec.popularity or 0) < 50:
                continue
            exclude.add(key)
            out.append((rec, f"Fans of {base.title} also liked this"))
            if len(out) >= count:
                return out
    return out
