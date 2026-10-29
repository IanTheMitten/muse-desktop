"""Read queries over the library.

Everything here answers a question the UI asks.

Browse is organised the way the library actually is: 27 artists, 87 albums,
723 playable tracks. A track whose file has gone missing is still returned —
it is part of the library and hiding it would make the loss invisible — but it
carries its status so the UI can show it as unplayable rather than offering it
and failing.
"""

import json
import sqlite3
from difflib import SequenceMatcher

from ..titles import normalize_title
from .art import art_coverage
from .ratings import counts as rating_counts
from .schema import current_version

# What a track looks like everywhere in the API. Selected once so browse,
# search, and the queue cannot drift apart.
TRACK_FIELDS = """
    track_rowid, track_name, artist_name, artist_names, album_name,
    album_rowid, track_no, disc_no, duration_ms, status, is_instrumental,
    art_path IS NOT NULL AS has_art,
    lyrics_path IS NOT NULL AS has_lyrics,
    gain_db,
    -- A correlated subquery rather than a join: every one of these queries
    -- would otherwise need its own LEFT JOIN, and over 723 rows the difference
    -- is unmeasurable. It rides along so a rating is visible everywhere a
    -- track is -- including the hydrated queue, which is what makes it appear
    -- on the other device.
    (SELECT rating FROM ratings WHERE ratings.track_rowid = tracks.track_rowid)
        AS rating
"""


def _track(row: sqlite3.Row) -> dict:
    track = dict(row)
    track["artist_names"] = json.loads(track["artist_names"])
    track["has_art"] = bool(track["has_art"])
    track["has_lyrics"] = bool(track["has_lyrics"])
    track["is_instrumental"] = bool(track["is_instrumental"])
    track["playable"] = track["status"] == "done"
    return track


def artists(conn: sqlite3.Connection) -> list[dict]:
    """Every artist with something in the library, alphabetically.

    Sorted case-insensitively and ignoring a leading `The`, because an artist
    list where `The Beatles` sits under T is a list nobody can scan.
    """
    rows = conn.execute(
        "SELECT artist_name,"
        "       COUNT(*)                    AS track_count,"
        "       COUNT(DISTINCT album_rowid) AS album_count,"
        "       SUM(status = 'done')        AS playable_count,"
        # One album to show the artist by. The browse view is a gallery, and a
        # gallery needs a picture; without this the client would have to ask
        # for every artist's albums just to find one cover to draw.
        "       (SELECT a.album_rowid FROM tracks a"
        "         WHERE a.artist_name = tracks.artist_name"
        "           AND a.art_path IS NOT NULL"
        "         ORDER BY a.album_rowid LIMIT 1) AS art_album_rowid"
        "  FROM tracks"
        " WHERE status IN ('done', 'missing_file')"
        " GROUP BY artist_name"
        " ORDER BY LOWER(LTRIM(REPLACE(artist_name, 'The ', ''))) COLLATE NOCASE"
    ).fetchall()
    return [dict(row) for row in rows]


def songs(conn: sqlite3.Connection) -> list[dict]:
    """Every owned song by title, for the Songs tab's unfiltered list."""
    rows = conn.execute(
        f"SELECT {TRACK_FIELDS} FROM tracks"
        " WHERE status IN ('done', 'missing_file')"
        " ORDER BY track_name COLLATE NOCASE, track_rowid"
    ).fetchall()
    return [_track(row) for row in rows]


def albums(conn: sqlite3.Connection) -> list[dict]:
    """Every album in the library, alphabetically.

    The albums segment of the browse view asks for the whole set once and
    filters it on the client. At this library's scale that is a single response
    of a few hundred rows against a round trip per keystroke, and it is what
    makes switching segments and typing both instant.
    """
    rows = conn.execute(
        "SELECT album_rowid,"
        "       album_name,"
        "       artist_name,"
        "       COUNT(*)                  AS track_count,"
        "       SUM(status = 'done')      AS playable_count,"
        "       SUM(duration_ms)          AS runtime_ms,"
        "       MAX(art_path IS NOT NULL) AS has_art"
        "  FROM tracks"
        " WHERE status IN ('done', 'missing_file') AND album_rowid IS NOT NULL"
        " GROUP BY album_rowid"
        " ORDER BY album_name COLLATE NOCASE"
    ).fetchall()
    return [{**dict(row), "has_art": bool(row["has_art"])} for row in rows]


def albums_by_artist(conn: sqlite3.Connection, artist_name: str) -> list[dict]:
    rows = conn.execute(
        "SELECT album_rowid,"
        "       album_name,"
        "       COUNT(*)                  AS track_count,"
        "       SUM(status = 'done')      AS playable_count,"
        "       SUM(duration_ms)          AS runtime_ms,"
        "       MAX(art_path IS NOT NULL) AS has_art"
        "  FROM tracks"
        " WHERE artist_name = ? AND status IN ('done', 'missing_file')"
        " GROUP BY album_rowid, album_name"
        " ORDER BY album_name COLLATE NOCASE",
        (artist_name,),
    ).fetchall()
    return [{**dict(row), "has_art": bool(row["has_art"])} for row in rows]


def album_tracks(conn: sqlite3.Connection, album_rowid: int) -> list[dict]:
    """An album in the order it was meant to be heard.

    Disc, then track number. NULLs sort last rather than first, so a track
    with no number lands at the end of the album instead of ahead of track 1.
    """
    rows = conn.execute(
        f"SELECT {TRACK_FIELDS}"
        "  FROM tracks"
        " WHERE album_rowid = ? AND status IN ('done', 'missing_file')"
        " ORDER BY disc_no IS NULL, disc_no,"
        "          track_no IS NULL, track_no,"
        "          track_name COLLATE NOCASE",
        (album_rowid,),
    ).fetchall()
    return [_track(row) for row in rows]


def album(conn: sqlite3.Connection, album_rowid: int) -> dict | None:
    row = conn.execute(
        "SELECT album_rowid, album_name, artist_name,"
        "       COUNT(*)         AS track_count,"
        "       SUM(duration_ms) AS runtime_ms"
        "  FROM tracks"
        " WHERE album_rowid = ? AND status IN ('done', 'missing_file')"
        " GROUP BY album_rowid",
        (album_rowid,),
    ).fetchone()
    return dict(row) if row and row["album_rowid"] is not None else None


def _fts_term(text: str) -> str:
    """Quote a user's text so FTS5 treats it as a literal phrase.

    Everything a listener types is data, never query syntax. Unquoted, a stray
    `"` or a bare `OR` is a syntax error and the search 500s; worse, `NOT` and
    friends would silently change what the search means. Doubling the quotes
    is how SQLite escapes them inside a phrase.
    """
    return '"' + text.replace('"', '""') + '"'


def _fuzzy_terms(words: list[str]) -> set[str]:
    """The trigrams of a word, for when the word itself matches nothing.

    A misspelling breaks substring matching outright — `mooon` shares no
    four-character run with `moon` — but it still shares most of its trigrams:
    `moo` and `oon` both survive. Matching those and letting bm25 rank finds
    the track without a second dependency or an edit-distance table.

    Only reached when nothing else matched, because on its own this is far too
    generous.
    """
    terms: set[str] = set()
    for word in words:
        stripped = word.strip()
        if len(stripped) < 4:
            continue  # too short to have a useful spread of trigrams
        terms.update(
            stripped[at : at + 3] for at in range(len(stripped) - 2)
        )
    return terms


# How close a word has to be to count as the same word misspelt. 0.72 keeps
# `mooon`/`moon` (0.89) and `fether`/`feather` (0.92) while dropping
# `nothing`/`thinking` (0.67), which shares trigrams and nothing else.
FUZZY_THRESHOLD = 0.72


def _close_enough(words: list[str], track: dict) -> bool:
    """Whether a fuzzy candidate is a plausible misspelling or just noise.

    Trigram matching alone answers "shares some letters", which is far too
    generous: `zzzzznothing` shares `ing` with half the library. Comparing
    whole words is what separates a typo from a coincidence, and over the
    handful of rows a fuzzy match returns it costs nothing.
    """
    haystack = " ".join(
        (track["track_name"], track["artist_name"], track["album_name"])
    ).lower()
    candidates = haystack.split()
    return any(
        SequenceMatcher(None, word.lower(), candidate).ratio() >= FUZZY_THRESHOLD
        for word in words
        for candidate in candidates
    )


def _match(
    conn: sqlite3.Connection, terms: set[str], limit: int
) -> list[dict]:
    """Run one FTS match over a set of alternatives, best-ranked first.

    The match is its own subquery so ranking happens inside FTS and the outer
    select reads plain `tracks` columns; joining the virtual table directly
    makes every shared column name ambiguous.
    """
    expression = " OR ".join(_fts_term(term) for term in sorted(terms))
    rows = conn.execute(
        f"SELECT {TRACK_FIELDS}"
        "  FROM tracks"
        "  JOIN (SELECT rowid AS match_rowid, bm25(tracks_fts) AS rank"
        "          FROM tracks_fts WHERE tracks_fts MATCH ?) AS matched"
        "    ON matched.match_rowid = tracks.track_rowid"
        " WHERE tracks.status IN ('done', 'missing_file')"
        " ORDER BY matched.rank, tracks.artist_name, tracks.track_no"
        " LIMIT ?",
        (expression, limit),
    ).fetchall()
    return [_track(row) for row in rows]


def search(
    conn: sqlite3.Connection, query: str, limit: int = 60
) -> list[dict]:
    """Search the library — never the catalog.

    The catalog's `tracks(name)` is unindexed across 24.9 M rows, so a global
    track-name search there is a full scan of a 17 GB file. Catalog navigation
    is artist-first and belongs to slice 3. This is 723 rows.

    Trigram tokenization gives substring matching, so a fragment from the
    middle of a word finds it. Each word is also matched on its own, so one
    misspelt word among several does not lose the result — the correctly typed
    ones still hit, and ranking sorts out the rest.

    The `norm_*` columns carry `normalize_title()` output, which is how
    `talking to the moon` finds `Talking to the Moon - Acoustic Piano`. They
    are index entries. Nothing displays them.
    """
    query = query.strip()
    if not query:
        return []

    # Trigram indexes cannot match anything shorter than three characters.
    words = [word for word in query.split() if len(word) >= 3]
    phrases = {phrase for phrase in (query, normalize_title(query))
               if len(phrase) >= 3}

    # Staged, narrowest first. Matching the whole phrase and every word at once
    # means a common word drags in the entire library: `talking to the moon`
    # would return everything containing `the`, correctly ranked but sixty rows
    # deep. Each stage runs only when the one before it found nothing.
    for stage in (
        phrases,             # the phrase as typed, and normalized
        {*phrases, *words},  # any single word
    ):
        if not stage:
            continue
        found = _match(conn, stage, limit)
        if found:
            return found

    # Nothing matched literally. Try the query as a misspelling — trigrams to
    # find candidates, whole-word similarity to throw out the coincidences.
    fuzzy_words = words or [query]
    fuzzy = _fuzzy_terms(fuzzy_words)
    if fuzzy:
        candidates = _match(conn, fuzzy, limit)
        close = [
            track for track in candidates if _close_enough(fuzzy_words, track)
        ]
        if close:
            return close

    if not phrases:
        # Short query: fall back to a prefix scan. Over 723 rows this is
        # instant, and returning nothing for "u2" would be absurd.
        like = f"%{query}%"
        rows = conn.execute(
            f"SELECT {TRACK_FIELDS} FROM tracks"
            " WHERE status IN ('done', 'missing_file')"
            "   AND (track_name LIKE ? OR artist_name LIKE ? OR album_name LIKE ?)"
            " ORDER BY artist_name, album_name, track_no"
            " LIMIT ?",
            (like, like, like, limit),
        ).fetchall()
        return [_track(row) for row in rows]

    return []


def tracks_by_rowid(
    conn: sqlite3.Connection, rowids: list[int]
) -> list[dict]:
    """Tracks for a list of ids, returned in the order asked for.

    The queue is stored as ids; the client needs whole tracks. Order comes from
    the caller's list, not from the database — a queue is an ordering, and
    `WHERE ... IN` does not preserve one.
    """
    if not rowids:
        return []
    placeholders = ",".join("?" * len(rowids))
    rows = conn.execute(
        f"SELECT {TRACK_FIELDS} FROM tracks WHERE track_rowid IN ({placeholders})",
        rowids,
    ).fetchall()
    found = {row["track_rowid"]: _track(row) for row in rows}
    # A track deleted since the queue was saved simply drops out.
    return [found[rowid] for rowid in rowids if rowid in found]


def track_paths(conn: sqlite3.Connection, track_rowid: int) -> dict | None:
    """On-disk locations for a track.

    Kept out of `TRACK_FIELDS` on purpose: absolute paths on the server are
    nobody's business over HTTP, and the client never needs them — it addresses
    audio, art, and lyrics by track id.
    """
    row = conn.execute(
        "SELECT track_rowid, file_path, lyrics_path, art_path, status"
        "  FROM tracks WHERE track_rowid = ?",
        (track_rowid,),
    ).fetchone()
    return dict(row) if row else None


def track(conn: sqlite3.Connection, track_rowid: int) -> dict | None:
    row = conn.execute(
        f"SELECT {TRACK_FIELDS} FROM tracks WHERE track_rowid = ?",
        (track_rowid,),
    ).fetchone()
    return _track(row) if row else None


def find_track(conn: sqlite3.Connection, track_name: str, artist_name: str) -> dict | None:
    row = conn.execute(
        f"SELECT {TRACK_FIELDS} FROM tracks WHERE status = 'done'"
        " AND track_name = ? COLLATE NOCASE AND artist_name = ? COLLATE NOCASE"
        " ORDER BY track_rowid LIMIT 1",
        (track_name, artist_name),
    ).fetchone()
    return _track(row) if row else None


def health(conn: sqlite3.Connection) -> dict:
    """What the library actually contains.

    Reachable before the SPA is built, so the server can be verified on its
    own — and useful afterwards as the answer to "did the import see the
    tracks I just downloaded?".
    """
    by_status = {
        row["status"]: row["n"]
        for row in conn.execute(
            "SELECT status, COUNT(*) AS n FROM tracks GROUP BY status"
        )
    }
    playable = by_status.get("done", 0)
    row = conn.execute(
        "SELECT COUNT(DISTINCT album_rowid)  AS albums,"
        "       COUNT(DISTINCT artist_name)  AS artists,"
        "       COALESCE(SUM(duration_ms),0) AS runtime_ms,"
        "       SUM(art_path IS NOT NULL)    AS with_art,"
        "       SUM(lyrics_path IS NOT NULL) AS with_lyrics"
        "  FROM tracks WHERE status = 'done'"
    ).fetchone()

    return {
        "schema_version": current_version(conn),
        "tracks": {
            "playable": playable,
            "missing_file": by_status.get("missing_file", 0),
            "error": by_status.get("error", 0),
            "total": sum(by_status.values()),
        },
        "albums": row["albums"],
        "artists": row["artists"],
        "runtime_hours": round(row["runtime_ms"] / 3_600_000, 1),
        "ratings": rating_counts(conn),
        "art_coverage": row["with_art"] or 0,
        # Per album, because that is the unit art is actually fetched in: 87
        # lookups, not 723. `checked` minus `with_art` is the set that has no
        # cover anywhere, which is a different thing from not having looked.
        "art": art_coverage(conn),
        "lyrics_coverage": row["with_lyrics"] or 0,
        "plays": conn.execute("SELECT COUNT(*) FROM plays").fetchone()[0],
    }


def artist_name_sets(conn: sqlite3.Connection) -> dict[int, frozenset[str]]:
    """`{track_rowid: {normalized artist names}}` for every playable track.

    The recommender's artist-spacing rule needs the whole mapping at once,
    since it re-checks it for every candidate on every pick. Reads
    `artist_names` (the JSON array), not the `" & "`-joined display string --
    a collaboration must share a member with any of its co-credited artists,
    not just match the whole credit line verbatim. Names are stripped and
    casefolded so "Pink Sweat$" and "pink sweat$" collide. A row whose
    `artist_names` fails to parse falls back to `[artist_name]` rather than
    raising: one malformed row must not break radio.
    """
    rows = conn.execute(
        "SELECT track_rowid, artist_name, artist_names FROM tracks"
        " WHERE status = 'done'"
    ).fetchall()
    result: dict[int, frozenset[str]] = {}
    for row in rows:
        try:
            names = json.loads(row["artist_names"])
        except (TypeError, ValueError):
            names = [row["artist_name"]]
        result[row["track_rowid"]] = frozenset(
            name.strip().casefold() for name in names
        )
    return result


def playable_rowids(conn: sqlite3.Connection) -> set[int]:
    """Every track that can actually be played.

    The recommender needs this as its own set, not as a by-product of
    whichever embedding table it happens to read first: a track can carry a
    content vector, a taste vector, or a full embeddings row independently,
    and keying off one of them silently drops the tracks that only have the
    others.
    """
    rows = conn.execute(
        "SELECT track_rowid FROM tracks WHERE status = 'done'"
    ).fetchall()
    return {row["track_rowid"] for row in rows}


def track_names(conn: sqlite3.Connection) -> dict[int, str]:
    """`{track_rowid: track_name}` for every playable track -- the title half
    of the arc engine's version key, read once per request."""
    return {
        row["track_rowid"]: row["track_name"] or ""
        for row in conn.execute(
            "SELECT track_rowid, track_name FROM tracks WHERE status = 'done'"
        )
    }


def vector_fingerprint(conn: sqlite3.Connection) -> tuple:
    """A cheap value that changes whenever any vector `load_vectors()` reads
    could have changed: a row count and newest write stamp per vector table,
    plus the content statistics' stamp, and the playable set's size.

    Keyed on the vectors' own stamps, not on any owning row's, because three
    other writers rewrite them underneath a long-lived API process: the
    embed batch, the content batch, and the nightly taste retrain (which
    replaces every taste vector in a new, unaligned ALS basis).
    """
    return tuple(
        tuple(conn.execute(sql).fetchone())
        for sql in (
            "SELECT COUNT(*), MAX(indexed_at) FROM embeddings",
            "SELECT COUNT(*), MAX(embedded_at) FROM content_embeddings",
            "SELECT COUNT(*), MAX(trained_at) FROM taste_embeddings",
            "SELECT MAX(computed_at) FROM content_stats",
            "SELECT COUNT(*) FROM tracks WHERE status = 'done'",
        )
    )
