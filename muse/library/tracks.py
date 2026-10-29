"""Track-row writes `library/importer.py` doesn't provide: allocating a
local id for a non-catalog track, checking whether a track already exists
(idempotency), and the two-write lifecycle a download job needs — an
`error` placeholder the moment a job starts, a `done` update once the
pipeline finishes.

The placeholder-at-start write exists so a process killed mid-download
leaves a diagnosable `status='error'` row instead of an orphaned file and
no row at all — the same shape the 64 manifest error entries already have,
which is what makes retry (PRD story 85) a normal case rather than a
special one.
"""

import json
import sqlite3
import time

from ..titles import normalize_title, same_title

LOCAL_ROWID_FLOOR = 1_000_000_000


def _now() -> int:
    return int(time.time())


def next_local_rowid(conn: sqlite3.Connection) -> int:
    """The next id to hand a non-catalog track. `track_rowid ≥ 1e9` never
    collides with the catalog's own rowids (max 256,038,919 per the PRD)."""
    row = conn.execute(
        "SELECT MAX(track_rowid) AS m FROM tracks WHERE track_rowid >= ?",
        (LOCAL_ROWID_FLOOR,),
    ).fetchone()
    highest = row["m"]
    return LOCAL_ROWID_FLOOR if highest is None else highest + 1


def album_rowid_for_release(conn: sqlite3.Connection, release_mbid: str) -> int:
    """The `album_rowid` every track of this MusicBrainz release shares.

    A null `album_rowid` is not survivable: `queries.albums()` filters
    `album_rowid IS NOT NULL`, so the album would vanish from the gallery,
    and `queries.album()` addresses an album *by* rowid, so it would have no
    page to open. Allocating one keeps every existing consumer working
    rather than teaching each of them to tolerate a null.

    Keyed on `release_mbid` so the second track of a release reuses what the
    first one allocated. Deliberately no `status = 'done'` filter, unlike
    `find_done` -- `find_done` excludes error rows because a failed attempt
    is not a reason to skip a re-download, but this lookup must include
    them, because a failed sibling track on the same release should still
    land in the same album when it is retried, rather than splitting one
    release across two albums.

    The mirror's maximum album rowid is 58,591,025, so the same 1e9 floor
    separates these from catalog albums as cleanly as it does for tracks.
    """
    row = conn.execute(
        "SELECT album_rowid FROM tracks"
        " WHERE release_mbid = ? AND album_rowid IS NOT NULL"
        " LIMIT 1",
        (release_mbid,),
    ).fetchone()
    if row is not None:
        return row["album_rowid"]

    highest = conn.execute(
        "SELECT MAX(album_rowid) AS m FROM tracks WHERE album_rowid >= ?",
        (LOCAL_ROWID_FLOOR,),
    ).fetchone()["m"]
    return LOCAL_ROWID_FLOOR if highest is None else highest + 1


def find_done(
    conn: sqlite3.Connection,
    *,
    track_rowid: int | None,
    source_site_id: str | None,
    isrc: str | None,
    recording_mbid: str | None,
    artist_name: str | None = None,
    track_name: str | None = None,
    duration_ms: int | None = None,
) -> dict | None:
    """A `status='done'` row matching this identity, or None.

    Deliberately `status='done'` only — a `status='error'` row is not a
    reason to skip the pipeline, it's the track this job is retrying (PRD
    story 93 vs. story 85 are different checks on the same table). Tries
    `track_rowid` first (catalog-known payloads), then `source_site_id`
    (the yt-dlp video id — the dedup key for free-text/URL payloads, which
    carry no ISRC from yt-dlp's own info dict), then `isrc` as a last
    resort when metadata resolution found one but the payload didn't name
    a `track_rowid`, then `recording_mbid` last — the only key a
    MusicBrainz-discovered track is guaranteed to have before its download
    runs, and the one that keeps the same recording queued from the single
    and from the album counted as a single track rather than two. Last,
    artist + title + duration within 3 s (see `find_held`).
    """
    if track_rowid is not None:
        row = conn.execute(
            "SELECT * FROM tracks WHERE track_rowid = ? AND status = 'done'",
            (track_rowid,),
        ).fetchone()
        if row:
            return dict(row)
    if source_site_id is not None:
        row = conn.execute(
            "SELECT * FROM tracks WHERE source_site_id = ? AND status = 'done'",
            (source_site_id,),
        ).fetchone()
        if row:
            return dict(row)
    return find_held(
        conn, isrc=isrc, recording_mbid=recording_mbid,
        artist_name=artist_name, track_name=track_name, duration_ms=duration_ms,
    )


def find_held(
    conn: sqlite3.Connection,
    *,
    isrc: str | None,
    recording_mbid: str | None,
    artist_name: str | None,
    track_name: str | None,
    duration_ms: int | None,
    statuses: tuple[str, ...] = ("done",),
) -> dict | None:
    """A row in one of `statuses` matching this recording by ISRC, MusicBrainz
    recording id, or artist + title + duration within 3 s, or None."""
    marks = ",".join("?" * len(statuses))
    for column, value in (("isrc", isrc), ("recording_mbid", recording_mbid)):
        if value is not None:
            row = conn.execute(
                f"SELECT * FROM tracks WHERE {column} = ? AND status IN ({marks})",
                (value, *statuses),
            ).fetchone()
            if row:
                return dict(row)
    if artist_name and track_name and duration_ms is not None:
        rows = conn.execute(
            f"SELECT * FROM tracks WHERE status IN ({marks})"
            " AND duration_ms BETWEEN ? AND ? ORDER BY track_rowid",
            (*statuses, duration_ms - 3000, duration_ms + 3000),
        ).fetchall()
        for row in rows:
            if (row["artist_name"] or "").casefold() == artist_name.casefold() and same_title(
                row["track_name"], track_name
            ):
                return dict(row)
    return None


def claim_track_id(conn: sqlite3.Connection, track_id: str) -> dict | None:
    """The row holding this Spotify `track_id`, or None. Plan Assumption 1,
    refined: only a *local* failed attempt releases its id. A manifest row's id
    is rewritten onto it by the importer at every startup, so releasing it would
    collide on tracks.track_id then; its row is returned unchanged."""
    row = conn.execute("SELECT * FROM tracks WHERE track_id = ?", (track_id,)).fetchone()
    if row is None:
        return None
    if row["status"] == "error" and row["origin"] == "local":
        conn.execute("UPDATE tracks SET track_id = NULL WHERE track_rowid = ?", (row["track_rowid"],))
        conn.commit()
        return None
    return dict(row)


def write_pending(
    conn: sqlite3.Connection,
    *,
    track_rowid: int,
    origin: str,
    metadata_tier: str,
    artist_name: str,
    artist_names: list[str],
    album_name: str,
    track_name: str,
    source_site_id: str | None,
    isrc: str | None,
    track_id: str | None = None,
    album_rowid: int | None = None,
    artist_rowid: int | None = None,
    track_no: int | None = None,
    disc_no: int | None = None,
    duration_ms: int | None = None,
    recording_mbid: str | None = None,
    release_mbid: str | None = None,
) -> None:
    """Write (or overwrite, on retry) the `status='error'` placeholder a
    download job's pipeline runs against. `file_path`/`lyrics_path`/
    `art_path` are left NULL — `mark_done` fills them once the pipeline
    actually produces them. An upsert so a retry starts from a clean
    placeholder rather than accumulating stale fields from the failed
    attempt it's replacing.
    """
    now = _now()
    conn.execute(
        """
        INSERT INTO tracks (
            track_rowid, track_id, origin, metadata_tier, artist_rowid,
            album_rowid, artist_name, artist_names, album_name, track_name,
            track_no, disc_no, duration_ms, isrc, source_site_id,
            recording_mbid, release_mbid, status, error, added_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'error', NULL, ?)
        ON CONFLICT(track_rowid) DO UPDATE SET
            track_id = excluded.track_id,
            origin = excluded.origin,
            metadata_tier = excluded.metadata_tier,
            artist_rowid = excluded.artist_rowid,
            album_rowid = excluded.album_rowid,
            artist_name = excluded.artist_name,
            artist_names = excluded.artist_names,
            album_name = excluded.album_name,
            track_name = excluded.track_name,
            track_no = excluded.track_no,
            disc_no = excluded.disc_no,
            duration_ms = excluded.duration_ms,
            isrc = excluded.isrc,
            source_site_id = excluded.source_site_id,
            recording_mbid = excluded.recording_mbid,
            release_mbid = excluded.release_mbid,
            status = 'error',
            error = NULL
        """,
        (
            track_rowid, track_id, origin, metadata_tier, artist_rowid,
            album_rowid, artist_name, json.dumps(artist_names), album_name,
            track_name, track_no, disc_no, duration_ms, isrc, source_site_id,
            recording_mbid, release_mbid, now,
        ),
    )
    index_track(conn, track_rowid, track_name, artist_name, album_name)
    conn.commit()


def mark_error(conn: sqlite3.Connection, track_rowid: int, error: str) -> None:
    conn.execute(
        "UPDATE tracks SET status = 'error', error = ? WHERE track_rowid = ?",
        (error, track_rowid),
    )
    conn.commit()


def mark_done(
    conn: sqlite3.Connection,
    track_rowid: int,
    *,
    file_path: str,
    lyrics_path: str | None,
    art_path: str | None,
    source_site_id: str | None = None,
) -> None:
    """`source_site_id` is optional here because most payloads (catalog
    downloads) already had it written by `write_pending`. It's passed again
    when the id is only known after the audio fetch (yt-dlp's video id for
    free-text/URL payloads) -- COALESCE keeps the write_pending value when
    this call doesn't have a fresher one."""
    conn.execute(
        "UPDATE tracks SET status = 'done', error = NULL, file_path = ?,"
        " lyrics_path = COALESCE(?, lyrics_path),"
        " art_path = COALESCE(?, art_path),"
        " source_site_id = COALESCE(?, source_site_id) WHERE track_rowid = ?",
        (file_path, lyrics_path, art_path, source_site_id, track_rowid),
    )
    conn.commit()


# `06 - Some Song - Inst.m4a`. LRCLIB carries an explicit instrumental boolean
# and replaces this heuristic in slice 4.
_INSTRUMENTAL_MARKERS = (" - inst.", " - instrumental.")


def is_instrumental_path(file_path: str | None, track_name: str) -> bool:
    haystack = (file_path or track_name or "").lower()
    return any(marker in haystack for marker in _INSTRUMENTAL_MARKERS)


def index_track(
    conn: sqlite3.Connection,
    rowid: int,
    track_name: str,
    artist_name: str,
    album_name: str,
) -> None:
    """Refresh the FTS row for a track.

    Delete-then-insert rather than update, because `tracks_fts` is a standalone
    FTS5 table keyed by `rowid` and re-running the import must not accumulate
    duplicate index entries.

    The `norm_*` columns are search entries only. Nothing displays them.
    """
    conn.execute("DELETE FROM tracks_fts WHERE rowid = ?", (rowid,))
    conn.execute(
        "INSERT INTO tracks_fts"
        "  (rowid, track_name, artist_name, album_name,"
        "   norm_track_name, norm_artist_name, norm_album_name)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            rowid,
            track_name,
            artist_name,
            album_name,
            normalize_title(track_name),
            normalize_title(artist_name),
            normalize_title(album_name),
        ),
    )
