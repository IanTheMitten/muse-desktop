"""Remove, trash and restore tracks (Decision 10).

A tombstone is `status = 'removed'` plus `removed_at`, so every
status-filtered reader drops it; plays and ratings stay. Rows are never deleted.
"""

import sqlite3
import time
from pathlib import Path

from send2trash import send2trash

from .queries import TRACK_FIELDS, _track


def remove(
    conn: sqlite3.Connection,
    track_rowids: list[int],
    *,
    trash: bool,
    now: int | None = None,
    send=send2trash,
) -> dict:
    at = now if now is not None else int(time.time())
    removed, failed = [], []
    for rowid in track_rowids:
        row = conn.execute(
            "SELECT file_path, status FROM tracks WHERE track_rowid = ?", (rowid,)
        ).fetchone()
        if row is None or row["status"] == "removed":
            failed.append({"track_rowid": rowid, "error": f"No track {rowid}"})
            continue
        if trash and row["file_path"] and Path(row["file_path"]).exists():
            try:
                send(row["file_path"])
            except OSError as e:
                failed.append({"track_rowid": rowid, "error": str(e)})
                continue
        conn.execute(
            "UPDATE tracks SET status = 'removed', removed_at = ? WHERE track_rowid = ?",
            (at, rowid))
        removed.append(rowid)
    return {"removed": removed, "failed": failed}


def restore(conn: sqlite3.Connection, track_rowids: list[int]) -> int:
    marks = ",".join("?" * len(track_rowids))
    return conn.execute(
        "UPDATE tracks SET status = 'missing_file', removed_at = NULL, file_mtime = NULL"
        f" WHERE track_rowid IN ({marks}) AND status = 'removed'",
        track_rowids,
    ).rowcount


def removed(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        f"SELECT {TRACK_FIELDS}, removed_at FROM tracks WHERE status = 'removed'"
        " ORDER BY removed_at DESC, track_rowid"
    ).fetchall()
    return [_track(r) for r in rows]
