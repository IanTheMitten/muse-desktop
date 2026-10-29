"""Where LRCLIB sync results are recorded, and the sync batch's work queue.

One row per track, written once a track has been asked about at all — same
shape as `library.embeddings`: absence of a row is the work queue, not a
sentinel value inside one. `state` is one of `synced`, `plain`,
`declared_instrumental`, `not_found`, or `manual` (the state a listener's
hand-fixed row is marked with; see `lyrics_state`'s big comment in
`muse/library/schema.py` for why automation must never touch it).
"""

import sqlite3
import time


def _now() -> int:
    return int(time.time())


def pending(conn: sqlite3.Connection) -> list[dict]:
    """Playable tracks with no `lyrics_state` row at all: the resumable sync
    batch's work queue.

    Deliberately just "no row yet" — not "no row, or a row older than N
    days" — because that keeps the query, and the manual guard it implies,
    trivial: a track already decided (including `manual`) already has a row
    and is therefore never in this set, so a caller that only ever iterates
    `pending()` cannot query LRCLIB about a manual correction even by
    accident. A full re-run to pick up new library additions once decided
    tracks should be reconsidered (a library-wide reindex, or new mirror
    data) is a separate, deliberate re-invocation — like the embed batch's
    lyrics-hash recheck pass — and is not what this function is for.
    """
    rows = conn.execute(
        "SELECT t.track_rowid, t.artist_name, t.album_name, t.track_name,"
        "       t.duration_ms, t.is_instrumental, t.file_path"
        "  FROM tracks t LEFT JOIN lyrics_state ls USING (track_rowid)"
        " WHERE t.status = 'done' AND ls.track_rowid IS NULL"
        " ORDER BY t.track_rowid"
    ).fetchall()
    return [dict(row) for row in rows]


def set_lyrics_path_if_absent(
    conn: sqlite3.Connection, track_rowid: int, lyrics_path: str
) -> None:
    """Point `tracks.lyrics_path` at a `.lyrics.txt` this batch just wrote,
    for a track the manifest never recorded one for.

    `IS NULL` in the WHERE clause, not an unconditional write: this only
    fills a gap, it never overrides a path the manifest (or a future
    editor) already set. Safe to call on every write regardless of whether
    there was a gap -- same shape as `library.art.record_cover`'s write to
    `tracks.art_path`, which this mirrors for the same reason: without a
    stored path, `read_lyrics`/the embed batch's lyrics-changed pass have no
    way to find the file this batch just wrote, so the fetch would be
    invisible to everything downstream of it. `lyrics_path` is in
    `importer.APP_OWNED` for the same reason `art_path` is -- a later
    manifest re-import must not erase what this call just set.
    """
    conn.execute(
        "UPDATE tracks SET lyrics_path = ?"
        " WHERE track_rowid = ? AND lyrics_path IS NULL",
        (lyrics_path, track_rowid),
    )
    conn.commit()


def get_state(conn: sqlite3.Connection, track_rowid: int) -> str | None:
    """The current `state` for one track, or None if it has never been
    checked. What `sync_batch` re-verifies before every LRCLIB call as a
    second guard against ever querying a `manual` track."""
    row = conn.execute(
        "SELECT state FROM lyrics_state WHERE track_rowid = ?", (track_rowid,)
    ).fetchone()
    return row["state"] if row else None


def get_offset_ms(conn: sqlite3.Connection, track_rowid: int) -> int:
    """The stored nudge (stories 68, 69), or 0 for a track never checked.
    0 is also the column's own default for a track that *has* a row and has
    never been nudged, so this never has to distinguish the two -- a synced
    view with no correction on file should render as if there were none."""
    row = conn.execute(
        "SELECT lrc_offset_ms FROM lyrics_state WHERE track_rowid = ?",
        (track_rowid,),
    ).fetchone()
    return row["lrc_offset_ms"] if row else 0


def set_offset(conn: sqlite3.Connection, track_rowid: int, offset_ms: int) -> bool:
    """Apply a nudge to a track that has already been through the sync
    batch. Returns whether a row existed to update.

    Requires an existing row rather than creating one on demand: the offset
    control only makes sense on a track the batch has actually timed, and a
    track with no row has no `.lrc` for an offset to apply to.
    """
    cursor = conn.execute(
        "UPDATE lyrics_state SET lrc_offset_ms = ? WHERE track_rowid = ?",
        (offset_ms, track_rowid),
    )
    conn.commit()
    return cursor.rowcount > 0


def upsert(
    conn: sqlite3.Connection,
    track_rowid: int,
    *,
    state: str,
    synced: bool,
    checked_at: int,
    lrc_offset_ms: int | None = None,
) -> None:
    """Write one track's sync result.

    `lrc_offset_ms` defaults to None, which means "leave it alone" — a new
    row gets the column's own default of 0, and an existing row keeps
    whatever a listener last nudged it to. Without this, a routine re-run of
    the batch (picking up a manual lyrics edit that cleared `synced`, say)
    would silently reset every offset correction anyone had ever made.
    """
    if lrc_offset_ms is None:
        existing = conn.execute(
            "SELECT lrc_offset_ms FROM lyrics_state WHERE track_rowid = ?",
            (track_rowid,),
        ).fetchone()
        lrc_offset_ms = existing["lrc_offset_ms"] if existing else 0

    conn.execute(
        "INSERT INTO lyrics_state"
        " (track_rowid, state, synced, lrc_offset_ms, checked_at)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(track_rowid) DO UPDATE SET"
        "   state = excluded.state,"
        "   synced = excluded.synced,"
        "   lrc_offset_ms = excluded.lrc_offset_ms,"
        "   checked_at = excluded.checked_at",
        (track_rowid, state, int(synced), lrc_offset_ms, checked_at),
    )
    conn.commit()
