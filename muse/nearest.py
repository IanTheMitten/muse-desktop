"""Nearest albums: album centroids over the track vectors, and the two Home
shelves built on them ("For fans of" and "Recommended albums").

On demand rather than a table: at a few hundred albums, brute force over the
centroids is cheaper than keeping a precomputed neighbour table fresh. The
centroids are cached on `vector_fingerprint`, never on a row's `updated_at`.
"""

import logging
import sqlite3

from . import reco, recommend
from .library import queries as library_queries

log = logging.getLogger(__name__)

RECENT_DAYS = 30
FANS_OF_ALBUMS = 8
RECOMMENDED_OWNED = 6
RECOMMENDED_CATALOG = 2

_LEGS = ("audio_vec", "taste_vec")

_cache: dict = {}

# artist name -> catalog artist rowid (or None). `search_artists` scans the
# 17.8 GB mirror (~1.5 s), and the mirror is frozen, so a name always resolves
# the same way. `shortcut: unbounded, one entry per played artist; bound it if
# the library ever grows to tens of thousands of artists.`
_artist_ids: dict = {}


def _mean(vectors: list[list[float]]) -> list[float] | None:
    if not vectors:
        return None
    return [sum(col) / len(vectors) for col in zip(*vectors)]


def _centroid(rowids: list[int], vectors: dict[int, dict]) -> dict:
    return {
        leg: _mean([vectors[r][leg] for r in rowids
                    if r in vectors and vectors[r][leg] is not None])
        for leg in _LEGS
    }


def album_centroids(conn: sqlite3.Connection) -> tuple[dict[int, dict], dict[str, dict]]:
    """(by album_rowid, by artist_name) mean `audio_vec`/`taste_vec`."""
    # The database file is part of the key, as in `recommend.calibration_for`.
    database = conn.execute("PRAGMA database_list").fetchone()[2]
    key = (database, library_queries.vector_fingerprint(conn))
    cached = _cache.get("entry")
    if cached is not None and cached[0] == key:
        return cached[1]
    vectors = recommend.load_vectors(conn)
    by_album: dict[int, list[int]] = {}
    by_artist: dict[str, list[int]] = {}
    for row in conn.execute(
        "SELECT track_rowid, album_rowid, artist_name FROM tracks"
        " WHERE status = 'done'"
    ):
        if row["album_rowid"] is not None:
            by_album.setdefault(row["album_rowid"], []).append(row["track_rowid"])
        by_artist.setdefault(row["artist_name"], []).append(row["track_rowid"])
    result = (
        {a: _centroid(r, vectors) for a, r in by_album.items()},
        {a: _centroid(r, vectors) for a, r in by_artist.items()},
    )
    _cache["entry"] = (key, result)
    return result


def album_similarity(a: dict, b: dict) -> float | None:
    """Mean cosine over the legs both sides have; None when they share none."""
    sims = [reco.cosine_sim(a[leg], b[leg]) for leg in _LEGS
            if a.get(leg) is not None and b.get(leg) is not None]
    return sum(sims) / len(sims) if sims else None


def nearest_albums(centroids: dict[int, dict], query: dict, *,
                   exclude: set[int], n: int) -> list[tuple[int, float]]:
    scored = []
    for rowid, centroid in centroids.items():
        if rowid in exclude:
            continue
        sim = album_similarity(query, centroid)
        if sim is not None:
            scored.append((rowid, sim))
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    return scored[:n]


def _owned_albums(conn: sqlite3.Connection) -> dict[int, dict]:
    rows = conn.execute(
        "SELECT album_rowid, album_name, artist_name,"
        "       COUNT(*)                  AS track_count,"
        "       SUM(duration_ms)          AS runtime_ms,"
        "       MAX(art_path IS NOT NULL) AS has_art"
        "  FROM tracks"
        " WHERE status = 'done' AND album_rowid IS NOT NULL"
        " GROUP BY album_rowid, album_name"
    ).fetchall()
    return {row["album_rowid"]: {**dict(row), "has_art": bool(row["has_art"])}
            for row in rows}


def for_fans_of(conn: sqlite3.Connection, now_ms: int) -> dict | None:
    """The 30-day top artist and the owned albums nearest to them."""
    cutoff = now_ms - RECENT_DAYS * 86_400_000
    top = conn.execute(
        "SELECT t.artist_name FROM plays p JOIN tracks t USING (track_rowid)"
        " WHERE p.started_at >= ? GROUP BY t.artist_name"
        " ORDER BY COUNT(*) DESC, MAX(p.started_at) DESC LIMIT 1",
        (cutoff,),
    ).fetchone()
    if top is None:
        return None
    artist = top["artist_name"]
    by_album, by_artist = album_centroids(conn)
    albums = _owned_albums(conn)
    own = {rowid for rowid, a in albums.items() if a["artist_name"] == artist}
    near = nearest_albums(by_album, by_artist.get(artist, {}), exclude=own,
                          n=FANS_OF_ALBUMS)
    return {"artist_name": artist,
            "albums": [albums[rowid] for rowid, _ in near if rowid in albums]}


def _catalog_card(catalog, artist: str, owned: set[int]) -> dict | None:
    """The artist's first non-owned full album in the catalog, or None."""
    try:
        key = (type(catalog), artist)
        if key not in _artist_ids:
            found = catalog.search_artists(artist, limit=1)
            _artist_ids[key] = found[0]["rowid"] if found else None
        if _artist_ids[key] is None:
            return None
        for album in catalog.artist_albums(_artist_ids[key]):
            if album["album_type"] == "album" and album["rowid"] not in owned:
                return {"owned": False, "album_name": album["name"],
                        "artist_name": artist, "reason": f"More from {artist}"}
    except Exception:
        log.exception("Catalog lookup failed for %s", artist)
    return None


def recommended_albums(conn: sqlite3.Connection, now_ms: int, catalog) -> list[dict]:
    cutoff = now_ms - RECENT_DAYS * 86_400_000
    seeds = [row["album_rowid"] for row in conn.execute(
        "SELECT t.album_rowid FROM plays p JOIN tracks t USING (track_rowid)"
        " WHERE p.started_at >= ? AND t.album_rowid IS NOT NULL"
        " GROUP BY t.album_rowid ORDER BY t.album_rowid", (cutoff,))]
    by_album, _ = album_centroids(conn)
    albums = _owned_albums(conn)
    seeds = [s for s in seeds if s in by_album]

    # Scoring every seed against every album is the slow part (~1 s on a real
    # library). Only the album metadata and catalog cards below move between
    # calls, so cache the ranking on the same key the centroids use plus the
    # seed set. `shortcut: one entry, a different database evicts it.`
    database = conn.execute("PRAGMA database_list").fetchone()[2]
    key = (database, library_queries.vector_fingerprint(conn), tuple(seeds))
    cached = _cache.get("ranking")
    if cached is not None and cached[0] == key:
        ranked = cached[1]
    else:
        best: dict[int, tuple[float, int]] = {}
        for seed in seeds:
            for rowid, sim in nearest_albums(by_album, by_album[seed],
                                             exclude=set(seeds), n=len(by_album)):
                if rowid not in best or sim > best[rowid][0]:
                    best[rowid] = (sim, seed)
        ranked = sorted(best.items(), key=lambda kv: (-kv[1][0], kv[0]))
        _cache["ranking"] = (key, ranked)
    picks = [(rowid, seed) for rowid, (_, seed) in ranked[:RECOMMENDED_OWNED]
             if rowid in albums]

    out = [{**albums[rowid], "owned": True,
            "reason": f"Because you played {albums[seed]['album_name']}"}
           for rowid, seed in picks]

    artists = list(dict.fromkeys(albums[rowid]["artist_name"] for rowid, _ in picks))
    for artist in artists[:RECOMMENDED_CATALOG]:
        card = _catalog_card(catalog, artist, set(albums))
        if card:
            out.append(card)
    return out
