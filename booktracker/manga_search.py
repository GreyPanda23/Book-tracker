"""Find manga (title, author, genre/theme, similar series) through AniList.

AniList is free and needs no key. It lists manga as series (not volumes), so a
manga entry has no ISBN; it is identified by title + author instead. Results
use the same BookResult shape as book_search, so every page can treat them alike.
"""

from __future__ import annotations

import logging
import random
import re

import httpx

from . import book_search
from .book_search import BookResult, _dedupe, _words, pick_surprises

log = logging.getLogger("booktracker.manga")
URL = "https://graphql.anilist.co"
USER_AGENT = book_search.USER_AGENT
PAGE = 50          # AniList's maximum page size
FORMATS = "[MANGA, ONE_SHOT]"   # leaves out light novels

MANGA_GENRES = ["Action", "Adventure", "Comedy", "Drama", "Ecchi", "Fantasy", "Horror",
                "Mahou Shoujo", "Mecha", "Music", "Mystery", "Psychological", "Romance",
                "Sci-Fi", "Slice of Life", "Sports", "Supernatural", "Thriller"]
# Demographics and popular themes (AniList "tags"); they combine with genres.
MANGA_TAGS = ["Shounen", "Seinen", "Shoujo", "Josei", "Isekai", "School", "Martial Arts",
              "Historical", "Military", "Detective", "Survival", "Time Manipulation",
              "Post-Apocalyptic", "Samurai", "Video Games", "Reincarnation", "Superhero",
              "Super Power", "Magic", "Cyberpunk", "Crime", "Politics", "Space", "Gods"]
MANGA_GENRE_NAMES = MANGA_GENRES + MANGA_TAGS

MEDIA_FIELDS = """
  id type title { romaji english } coverImage { large } startDate { year }
  averageScore popularity genres volumes format
  staff(perPage: 6, sort: RELEVANCE) { edges { role node { name { full } } } }
"""
AUTHOR_ROLES = re.compile(r"story|art|original", re.I)


def _post(query: str, **variables) -> dict:
    with httpx.Client(timeout=book_search.TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
        r = client.post(URL, json={"query": query, "variables": variables})
        r.raise_for_status()
        data = r.json()
    if data.get("errors"):
        raise RuntimeError(data["errors"][0].get("message", "AniList error"))
    return data["data"]


def _authors(media: dict) -> str | None:
    """Story/art creators, e.g. 'Kentarou Miura' (supervisors, editors etc. are skipped)."""
    names: list[str] = []
    for edge in (media.get("staff") or {}).get("edges", []):
        name = edge["node"]["name"]["full"]
        if AUTHOR_ROLES.search(edge.get("role") or "") and name not in names:
            names.append(name)
    return ", ".join(names[:3]) or None


def parse_media(media: dict) -> BookResult | None:
    titles = media.get("title") or {}
    title = titles.get("english") or titles.get("romaji")
    if not title:
        return None
    genres = media.get("genres") or []
    score = media.get("averageScore")
    return BookResult(
        title=title,
        author=_authors(media),
        cover_url=(media.get("coverImage") or {}).get("large"),
        genre=genres[0] if genres else None,
        year=str((media.get("startDate") or {}).get("year") or "") or None,
        source="AniList",
        subjects=genres,
        rating=round(score / 20, 2) if score else None,      # 0-100 -> 0-5 stars
        popularity=media.get("popularity"),
    )


def _parse_all(nodes: list[dict]) -> list[BookResult]:
    return [r for r in (parse_media(m) for m in nodes if m.get("type") in (None, "MANGA")) if r]


# --------------------------------------------------------------------------- #
# Searching by title / author
# --------------------------------------------------------------------------- #
def _by_title(query: str, limit: int) -> list[BookResult]:
    data = _post("query($s: String, $n: Int) { Page(perPage: $n) { media(type: MANGA, search: $s, "
                 f"isAdult: false, format_in: {FORMATS}, sort: SEARCH_MATCH) {{ {MEDIA_FIELDS} }} }} }}",
                 s=query, n=min(limit, PAGE))
    return _parse_all(data["Page"]["media"])


def _romanized(word: str) -> str:
    """Treat spelling variants alike (Kentaro/Kentarou, Eiichiro/Eiichirou)."""
    return re.sub(r"ou|oo", "o", re.sub(r"uu", "u", word))


def _staff_named(query: str) -> list[dict]:
    """Creators whose name contains every word of the query (spelling-tolerant)."""
    words = [_romanized(w) for w in _words(query)]
    if not words:
        return []
    found: dict[int, dict] = {}
    for term in dict.fromkeys([query, *sorted(words, key=len, reverse=True)[:2]]):
        data = _post("query($s: String) { Page(perPage: 8) { staff(search: $s) { id name { full } "
                     "primaryOccupations } } }", s=term)
        for person in data["Page"]["staff"]:
            have = {_romanized(w) for w in _words(person["name"]["full"])}
            if set(words) <= have:
                found[person["id"]] = person
        if found:
            break
    return list(found.values())[:2]


def _by_author(query: str, limit: int) -> list[BookResult]:
    out: list[BookResult] = []
    for person in _staff_named(query):
        data = _post("query($id: Int, $n: Int) { Staff(id: $id) { staffMedia(type: MANGA, perPage: $n, "
                     f"sort: POPULARITY_DESC) {{ nodes {{ {MEDIA_FIELDS} }} }} }} }}",
                     id=person["id"], n=min(limit, PAGE))
        nodes = [m for m in data["Staff"]["staffMedia"]["nodes"]
                 if m.get("format") is None or m.get("format") in ("MANGA", "ONE_SHOT")]
        out += _parse_all(nodes)
    return out


def search_manga(query: str, limit: int = 10) -> tuple[list[BookResult], str]:
    """Search by series title or creator name. A creator's own series come first."""
    query = (query or "").strip()
    if not query:
        return [], "Type a manga title or author to search."
    try:
        titles = _by_title(query, limit)
    except Exception as exc:
        log.warning("AniList title search failed: %s", exc)
        return [], f"AniList unavailable ({exc})"
    try:
        creator = _by_author(query, book_search.AUTHOR_LIMIT)
    except Exception as exc:
        log.warning("AniList author search failed: %s", exc)
        creator = []
    if creator:
        results = _dedupe(creator + titles)[:book_search.AUTHOR_LIMIT]
    else:
        results = _dedupe(titles)[:limit]
    return results, "Results from AniList" if results else "No manga found on AniList."


# --------------------------------------------------------------------------- #
# Browsing by genre / theme
# --------------------------------------------------------------------------- #
def _filter_query(genres: list[str], tags: list[str], keyword: str, limit: int) -> list[BookResult]:
    args = ["type: MANGA", "isAdult: false", f"format_in: {FORMATS}", "sort: POPULARITY_DESC"]
    variables: dict = {"n": min(limit, PAGE)}
    declared = ["$n: Int"]
    if genres:
        args.append("genre_in: $g")
        declared.append("$g: [String]")
        variables["g"] = genres
    if tags:
        args.append("tag_in: $t")
        declared.append("$t: [String]")
        variables["t"] = tags
    if keyword.strip():
        args.append("search: $s")
        declared.append("$s: String")
        variables["s"] = keyword.strip()
        args.remove("sort: POPULARITY_DESC")      # keyword searches rank by relevance
    data = _post(f"query({', '.join(declared)}) {{ Page(perPage: $n) {{ media({', '.join(args)}) "
                 f"{{ {MEDIA_FIELDS} }} }} }}", **variables)
    return _parse_all(data["Page"]["media"])


def _split(names: list[str]) -> tuple[list[str], list[str]]:
    return [n for n in names if n in MANGA_GENRES], [n for n in names if n not in MANGA_GENRES]


def search_manga_by_genres(genres: list[str], match_all: bool = True, keyword: str = "",
                           limit: int = book_search.GENRE_LIMIT) -> tuple[list[BookResult], str]:
    """Manga in all (or any) of the chosen genres/themes, most popular first."""
    genres = [g for g in genres if g]
    if not genres:
        return [], "Pick at least one genre."
    try:
        if match_all:
            g, t = _split(genres)
            results = _filter_query(g, t, keyword, limit)
        else:   # AniList only combines with AND, so ask once per genre and merge
            results = []
            per = max(10, min(PAGE, limit // len(genres) + 5))
            for name in genres:
                g, t = _split([name])
                results += _filter_query(g, t, keyword, per)
            results.sort(key=lambda b: -(b.popularity or 0))
    except Exception as exc:
        log.warning("AniList genre search failed: %s", exc)
        return [], f"AniList unavailable ({exc})"
    results = _dedupe(results)[:limit]
    return results, "Results from AniList" if results else "No manga found on AniList."


def surprise_manga(genres: list[str] | None = None, owned: set[str] | None = None,
                   count: int = 5, rng: random.Random | None = None) -> tuple[list[BookResult], str]:
    """A few random, well-liked manga you don't have yet."""
    rng = rng or random.Random()
    picked = [g for g in (genres or []) if g] or [rng.choice(MANGA_GENRES)]
    results, note = search_manga_by_genres(picked, match_all=False)
    chosen = pick_surprises(results, owned or set(), count, rng)
    if not chosen:
        return [], note
    return chosen, f"🎲 Surprise! {len(chosen)} random picks from {', '.join(picked)}. {note}"


# --------------------------------------------------------------------------- #
# Similar manga
# --------------------------------------------------------------------------- #
def similar_manga(title: str, author: str | None = None, count: int = 5) -> list[tuple[BookResult, str]]:
    """(manga, reason) pairs: what readers of this series also liked, then same creator."""
    try:
        found = _by_title(title, 5)
    except Exception as exc:
        log.warning("AniList lookup failed: %s", exc)
        return []
    wanted = " ".join(_words(title))
    home = next((m for m in found if " ".join(_words(m.title)) == wanted), found[0] if found else None)
    if not home:
        return []
    data = _post("query($s: String) { Media(type: MANGA, search: $s, isAdult: false, sort: SEARCH_MATCH) "
                 "{ id recommendations(perPage: 25, sort: RATING_DESC) { nodes { rating mediaRecommendation "
                 f"{{ {MEDIA_FIELDS} }} }} }} }} }}", s=title)
    nodes = [n for n in (data["Media"]["recommendations"]["nodes"] or [])
             if n.get("mediaRecommendation") and (n.get("rating") or 0) > 0]
    exclude = {" ".join(_words(title))}
    out: list[tuple[BookResult, str]] = []
    for node in nodes:
        parsed = _parse_all([node["mediaRecommendation"]])
        if parsed and " ".join(_words(parsed[0].title)) not in exclude:
            exclude.add(" ".join(_words(parsed[0].title)))
            out.append((parsed[0], f"Readers of {home.title} also liked this"))
        if len(out) >= count - 1:
            break
    for book in (_by_author(author.split(",")[0], 10) if author else []):
        if len(out) >= count:
            break
        key = " ".join(_words(book.title))
        if key not in exclude:
            exclude.add(key)
            out.append((book, f"Also by {book.author.split(',')[0] if book.author else 'the same creator'}"))
    return out[:count]
