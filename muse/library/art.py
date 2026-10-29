"""Where cover art is recorded.

The library is the only module that writes `library.sqlite3`, so the art
backfill asks here rather than reaching for the database itself.

Two tables carry two different facts. `album_art` records the *decision* — this
album was looked up, and here is what came back, including "nothing". `tracks`
carries the *display path*, denormalized onto every track of the album so the
browse queries stay one table wide. Both are written together.
"""

import sqlite3
import time


def _now() -> int:
    return int(time.time())


def albums_needing_art(conn: sqlite3.Connection) -> list[dict]:
    """Albums nobody has asked about yet.

    An album that came back empty is *not* returned again: `album_art` holds a
    row with a NULL path for it, and that is an answer. Re-asking would turn
    "fetched exactly once, ever" into "asked again on every restart".

    ISRCs come back with the album because they are how MusicBrainz is reached.
    Several are collected rather than one: an album's first track can be a
    feature credit or a bad scrape, and the second one then finds the release.

    Up to two `(track_name, duration_ms)` pairs come back too: two because a
    third track rarely rescues an album (`MAX_ISRCS` reasoning), and iTunes
    matches per track.

    `release_mbid` comes back for the same reason and is the shorter way in:
    a post-freeze album was downloaded from a MusicBrainz release, so its
    tracks carry that release's id and the cover can be asked for by key
    instead of walked to through an ISRC — which such a track usually has
    none of. Selecting it is not optional decoration: this query is the only
    feeder of `AlbumRef.from_row`, so a column missing here is a field that
    is always `None` on every `AlbumRef` the backfill ever builds.
    `MIN` because this is grouped by album, and it ignores NULLs — a catalog
    album, whose tracks all have none, still comes back with `None`.
    """
    rows = conn.execute(
        "SELECT t.album_rowid,"
        "       MIN(t.album_name)   AS album_name,"
        "       MIN(t.artist_name)  AS artist_name,"
        "       MIN(t.release_mbid) AS release_mbid,"
        "       COUNT(*)            AS track_count"
        "  FROM tracks AS t"
        "  LEFT JOIN album_art AS a ON a.album_rowid = t.album_rowid"
        " WHERE t.album_rowid IS NOT NULL"
        "   AND t.status IN ('done', 'missing_file')"
        "   AND a.album_rowid IS NULL"
        " GROUP BY t.album_rowid"
        " ORDER BY t.album_rowid"
    ).fetchall()

    albums = []
    for row in rows:
        isrcs = [
            found["isrc"]
            for found in conn.execute(
                "SELECT DISTINCT isrc FROM tracks"
                " WHERE album_rowid = ? AND isrc IS NOT NULL"
                " ORDER BY disc_no, track_no",
                (row["album_rowid"],),
            )
        ]
        tracks = [
            (found["track_name"], found["duration_ms"])
            for found in conn.execute(
                "SELECT track_name, duration_ms FROM tracks"
                " WHERE album_rowid = ? AND status IN ('done', 'missing_file')"
                " ORDER BY disc_no, track_no LIMIT 2",
                (row["album_rowid"],),
            )
        ]
        albums.append({**dict(row), "isrcs": isrcs, "tracks": tracks})
    return albums


def record_cover(
    conn: sqlite3.Connection,
    album_rowid: int,
    art_path: str,
    *,
    source: str,
    mbid: str | None = None,
    now: int | None = None,
) -> None:
    at = now if now is not None else _now()
    # Both statements or neither. The connection is in autocommit mode, so
    # without this a process killed between them would leave an album recorded
    # as having art whose tracks all say they have none — and the backfill,
    # seeing the `album_art` row, would never look at it again.
    conn.execute("BEGIN")
    try:
        conn.execute(
            "INSERT INTO album_art (album_rowid, art_path, source, mbid, checked_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(album_rowid) DO UPDATE SET"
            "   art_path = excluded.art_path,"
            "   source = excluded.source,"
            "   mbid = excluded.mbid,"
            "   checked_at = excluded.checked_at",
            (album_rowid, art_path, source, mbid, at),
        )
        conn.execute(
            "UPDATE tracks SET art_path = ? WHERE album_rowid = ?",
            (art_path, album_rowid),
        )
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def record_missing(
    conn: sqlite3.Connection, album_rowid: int, *, now: int | None = None
) -> None:
    """Record that this album was asked about and has no art.

    A row with a NULL path, not the absence of a row. The absence of a row means
    "not yet asked", and conflating the two is what would send the backfill back
    to MusicBrainz for every coverless album on every restart, forever.
    """
    at = now if now is not None else _now()
    conn.execute(
        "INSERT INTO album_art (album_rowid, art_path, source, mbid, checked_at)"
        " VALUES (?, NULL, NULL, NULL, ?)"
        " ON CONFLICT(album_rowid) DO UPDATE SET checked_at = excluded.checked_at",
        (album_rowid, at),
    )
    conn.commit()


def album_art_path(conn: sqlite3.Connection, album_rowid: int) -> str | None:
    row = conn.execute(
        "SELECT art_path FROM album_art WHERE album_rowid = ?", (album_rowid,)
    ).fetchone()
    return row["art_path"] if row else None


def art_coverage(conn: sqlite3.Connection) -> dict:
    """How much of the library has a cover, for the health endpoint."""
    row = conn.execute(
        "SELECT COUNT(DISTINCT album_rowid) AS albums,"
        "       COUNT(DISTINCT CASE WHEN art_path IS NOT NULL"
        "                           THEN album_rowid END) AS with_art"
        "  FROM tracks"
        " WHERE album_rowid IS NOT NULL AND status IN ('done', 'missing_file')"
    ).fetchone()
    checked = conn.execute(
        "SELECT COUNT(*) AS n,"
        "       SUM(art_path IS NULL) AS none_found"
        "  FROM album_art"
    ).fetchone()
    return {
        "albums": row["albums"],
        "with_art": row["with_art"],
        "checked": checked["n"],
        "none_found": checked["none_found"] or 0,
    }
