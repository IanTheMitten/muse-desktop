"""Where the nightly taste-retrain snapshot is stored (ADR 0003 / ticket 04).

One row per playable track, overwritten wholesale by `replace_all` every
retrain -- never a per-row upsert from anywhere else, and never a
`BEGIN`/`DELETE`/`INSERT`/`COMMIT` bracket either: `SharedConnection` locks
one `execute()` call at a time, not a sequence of them (see
`muse.library.features.frozen_bounds`'s docstring for the same hazard
discovered there), so a multi-call bracket here could interleave with
another writer's own bracket on the same shared connection mid-replace.
`replace_all` instead does the whole write in one multi-row
`INSERT ... ON CONFLICT DO UPDATE` statement, which is atomic both under
that per-call lock and under SQLite itself.

`taste_vec` is NULL for a track below `muse.taste.MIN_PLAY_EVENTS` -- not a
missing row, same as a NULL `feature_vec` on an otherwise-complete
`embeddings` row is a real answer (post-freeze, no catalog data) and not
missing work.
"""

import sqlite3
import time

from ..embed import unpack_vector


def _now() -> int:
    return int(time.time())


def replace_all(
    conn: sqlite3.Connection,
    rows: list[tuple[int, bytes | None, str | None]],
    *,
    now: int | None = None,
) -> None:
    """Overwrite every given track's taste row in one statement.

    `rows` is `(track_rowid, taste_vec, model)` triples and is expected to
    cover every currently playable track, one entry each -- `muse.taste.
    run_retrain` is the only caller and always passes the whole playable
    set, with `taste_vec=None` for a track below the minimum play-event
    threshold. A track_rowid missing from `rows` simply keeps whatever row
    it already had (see `coverage`, which joins against the current
    playable set so a stale row from a track that later became unplayable
    can never inflate the coverage count).

    One `INSERT ... ON CONFLICT DO UPDATE` covering every row: SQLite
    aborts the whole statement on the first constraint failure, so a bad
    retrain (e.g. a stray track_rowid the foreign key rejects) leaves every
    row exactly as it was -- there is no window where the table is half old,
    half new.
    """
    if not rows:
        return
    at = now if now is not None else _now()
    placeholders = ", ".join(["(?, ?, ?, ?)"] * len(rows))
    values = [
        value
        for track_rowid, taste_vec, model in rows
        for value in (track_rowid, taste_vec, model, at)
    ]
    conn.execute(
        "INSERT INTO taste_embeddings (track_rowid, taste_vec, model, trained_at)"
        f" VALUES {placeholders}"
        " ON CONFLICT(track_rowid) DO UPDATE SET"
        "   taste_vec = excluded.taste_vec,"
        "   model = excluded.model,"
        "   trained_at = excluded.trained_at",
        values,
    )


def coverage(conn: sqlite3.Connection) -> dict:
    """Tracks with a non-null taste embedding vs. tracks playable, for the
    health endpoint -- same shape as `library.embeddings.coverage`. Joined
    against the current playable set, not just `taste_embeddings` alone, so
    a stale row for a track that became unplayable after its last retrain
    never counts."""
    row = conn.execute(
        "SELECT COUNT(*) AS playable FROM tracks WHERE status = 'done'"
    ).fetchone()
    embedded = conn.execute(
        "SELECT COUNT(*) FROM taste_embeddings te"
        "  JOIN tracks t USING (track_rowid)"
        " WHERE t.status = 'done' AND te.taste_vec IS NOT NULL"
    ).fetchone()[0]
    return {"playable": row["playable"], "embedded": embedded}


def vectors(conn: sqlite3.Connection) -> dict[int, list[float]]:
    """Every taste vector, unpacked. A track below `taste.MIN_PLAY_EVENTS`
    has a row with a NULL `taste_vec` and is absent here -- three plays is
    not yet a taste, and the null-leg contract is what covers it."""
    rows = conn.execute(
        "SELECT track_rowid, taste_vec FROM taste_embeddings"
        " WHERE taste_vec IS NOT NULL"
        " ORDER BY track_rowid"
    ).fetchall()
    return {row["track_rowid"]: unpack_vector(row["taste_vec"]) for row in rows}
