"""Saving edited lyrics (Decision 8): the text goes into the data dir's
`lyrics/`, never beside the audio, and three things derived from the old text
are invalidated -- the lyrics embedding, the lyric tags, and `lyrics_state`
(set to `manual`, unsynced, so the sync batch never re-queries it).

A stale `.lrc` rule: an existing `.lrc` no longer matches the edited text, so
the save reports it stale (`state = 'manual' AND synced = 0` with a `.lrc` on
disk). The listener then either keeps its timings or drops the file.
"""

import os
import sqlite3
import time
from pathlib import Path

from ..lyrics import lyrics_files
from . import lyrics_state


def save_text(
    conn: sqlite3.Connection,
    track_rowid: int,
    text: str,
    *,
    lyrics_root: Path,
    now: int | None = None,
) -> dict | None:
    if conn.execute(
        "SELECT 1 FROM tracks WHERE track_rowid = ?", (track_rowid,)
    ).fetchone() is None:
        return None
    txt, lrc = lyrics_files(lyrics_root, track_rowid)
    if not text.strip():
        txt.unlink(missing_ok=True)
        lrc.unlink(missing_ok=True)
        path = None
    else:
        lyrics_root.mkdir(parents=True, exist_ok=True)
        tmp = txt.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, txt)
        path = str(txt)
    conn.execute(
        "UPDATE tracks SET lyrics_path = ? WHERE track_rowid = ?", (path, track_rowid)
    )
    conn.execute(
        "UPDATE embeddings SET lyrics_vec = NULL, lyrics_hash = NULL,"
        " lyrics_model = NULL WHERE track_rowid = ?",
        (track_rowid,),
    )
    conn.execute("DELETE FROM lyric_tag_sets WHERE track_rowid = ?", (track_rowid,))
    # upsert commits, which also commits the writes above
    lyrics_state.upsert(
        conn, track_rowid, state="manual", synced=False,
        checked_at=int(time.time()) if now is None else now,
    )
    return {"lrc_stale": lrc.is_file()}


def is_stale(conn: sqlite3.Connection, track_rowid: int, *, lyrics_root: Path) -> bool:
    if not lyrics_files(lyrics_root, track_rowid)[1].is_file():
        return False
    return conn.execute(
        "SELECT 1 FROM lyrics_state"
        " WHERE track_rowid = ? AND state = 'manual' AND synced = 0",
        (track_rowid,),
    ).fetchone() is not None


def keep_timings(
    conn: sqlite3.Connection, track_rowid: int, *, lyrics_root: Path,
    now: int | None = None,
) -> bool:
    if not is_stale(conn, track_rowid, lyrics_root=lyrics_root):
        return False
    lyrics_state.upsert(
        conn, track_rowid, state="manual", synced=True,
        checked_at=int(time.time()) if now is None else now,
    )
    return True


def drop_timings(
    conn: sqlite3.Connection, track_rowid: int, *, lyrics_root: Path,
    now: int | None = None,
) -> bool:
    lrc = lyrics_files(lyrics_root, track_rowid)[1]
    if not lrc.is_file():
        return False
    lrc.unlink()
    lyrics_state.upsert(
        conn, track_rowid, state="manual", synced=False,
        checked_at=int(time.time()) if now is None else now,
    )
    return True
