"""Playlists: named, ordered, fixed track lists.

Not queries -- a playlist keeps the tracks it was saved with, so a taste
retrain never reshuffles one. A track appearing twice is allowed, which is why
`position` and not `track_rowid` is the key. Deleting a track drops it from
every playlist (cascade) and leaves a gap in positions, which order alone
tolerates.
"""

import sqlite3
import time

from .queries import TRACK_FIELDS, _track

COVERS = 4


def create(
    conn: sqlite3.Connection,
    name: str,
    track_rowids: list[int],
    *,
    now: int | None = None,
) -> int:
    """Raises sqlite3.IntegrityError on an unknown track, leaving nothing behind."""
    at = now if now is not None else int(time.time())
    conn.execute("BEGIN")
    try:
        pid = conn.execute(
            "INSERT INTO playlists (name, created_at, updated_at) VALUES (?, ?, ?)",
            (name, at, at),
        ).lastrowid
        conn.executemany(
            "INSERT INTO playlist_tracks (playlist_id, position, track_rowid)"
            " VALUES (?, ?, ?)",
            [(pid, i, t) for i, t in enumerate(track_rowids)],
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return pid


def _covers(conn: sqlite3.Connection, playlist_id: int) -> list[int]:
    """First track with art from each of up to four distinct albums."""
    seen: dict[int, int] = {}
    for row in conn.execute(
        "SELECT t.track_rowid, t.album_rowid FROM playlist_tracks pt"
        " JOIN tracks t ON t.track_rowid = pt.track_rowid"
        " WHERE pt.playlist_id = ? AND t.art_path IS NOT NULL"
        " ORDER BY pt.position", (playlist_id,)
    ):
        seen.setdefault(row["album_rowid"], row["track_rowid"])
        if len(seen) == COVERS:
            break
    return list(seen.values())


def summaries(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT p.id, p.name, p.created_at, p.updated_at,"
        "       COUNT(pt.position) AS track_count,"
        "       COALESCE(SUM(t.duration_ms), 0) AS runtime_ms"
        " FROM playlists p"
        " LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id"
        " LEFT JOIN tracks t ON t.track_rowid = pt.track_rowid"
        " GROUP BY p.id ORDER BY p.updated_at DESC, p.id DESC"
    ).fetchall()
    return [{**dict(r), "cover_track_rowids": _covers(conn, r["id"])} for r in rows]


def get(conn: sqlite3.Connection, playlist_id: int) -> dict | None:
    head = next((s for s in summaries(conn) if s["id"] == playlist_id), None)
    if head is None:
        return None
    # TRACK_FIELDS has bare column names, so join through a subquery on tracks.
    rows = conn.execute(
        f"SELECT {TRACK_FIELDS} FROM tracks"
        " JOIN (SELECT position, track_rowid AS pt_rowid FROM playlist_tracks"
        "       WHERE playlist_id = ?) ON pt_rowid = tracks.track_rowid"
        " ORDER BY position", (playlist_id,)
    ).fetchall()
    return {**head, "tracks": [_track(r) for r in rows]}


def rename(
    conn: sqlite3.Connection, playlist_id: int, name: str, *, now: int | None = None
) -> bool:
    at = now if now is not None else int(time.time())
    return conn.execute(
        "UPDATE playlists SET name = ?, updated_at = ? WHERE id = ?",
        (name, at, playlist_id),
    ).rowcount > 0


def delete(conn: sqlite3.Connection, playlist_id: int) -> bool:
    return conn.execute(
        "DELETE FROM playlists WHERE id = ?", (playlist_id,)
    ).rowcount > 0
