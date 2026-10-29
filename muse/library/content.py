"""Where the MERT content leg's vectors and corpus statistics live.

`content_vec IS NULL` -- or no row at all -- is the batch's entire work
queue, the same shape `embeddings.audio_vec IS NULL` already is: a batch
killed halfway through restarts into exactly the set it had not finished.

Separate from `embeddings` on purpose (see the migration's comment and the
spec): a different program, in a different virtualenv, on a different
trigger, writes this. One work queue per owner.

`content_stats` holds one row. It is rewritten wholesale at the end of every
batch and read once per ranking pass, never joined against per-track rows --
which is why it is a table of scalars rather than columns on
`content_embeddings`.

Unlike its siblings in this package, this module imports from `muse.embed`
(`unpack_vector`) rather than the other way around. `reco.content_sim` -- the
one thing that calls `stats()` -- takes a plain `mean` list, not a blob, and
staying pure (no sqlite3, no packing) is a harder constraint on `reco.py`
than it is here, so the unpacking happens on this side of that boundary
instead. Only the public helpers are imported, never a private one.
"""

import sqlite3
import time

from ..embed import unpack_vector

__all__ = [
    "coverage", "pending", "replace_stats", "stats", "upsert", "vectors",
]


def _now() -> int:
    return int(time.time())


def pending(conn: sqlite3.Connection) -> list[dict]:
    """Playable tracks with no content vector yet: the resumable batch's
    work queue. `file_path` comes along because the embedder needs it and a
    second lookup per track would buy nothing.

    `t.file_path IS NOT NULL` is defense-in-depth, not a live condition:
    `importer._resolve_status` is the sole writer of `tracks.status` and
    `tracks.file_path` today and always sets them together, so `status =
    'done'` already implies a non-NULL `file_path`. The clause stays because
    a future second writer of `tracks` (slice 3's in-app ingest) could
    weaken that invariant, and this queue should not depend on it holding
    forever."""
    rows = conn.execute(
        "SELECT t.track_rowid, t.file_path"
        "  FROM tracks t LEFT JOIN content_embeddings c USING (track_rowid)"
        " WHERE t.status = 'done' AND t.file_path IS NOT NULL"
        "   AND (c.track_rowid IS NULL OR c.content_vec IS NULL)"
        " ORDER BY t.track_rowid"
    ).fetchall()
    return [dict(row) for row in rows]


def upsert(
    conn: sqlite3.Connection,
    track_rowid: int,
    *,
    content_vec: bytes | None,
    model: str | None,
    now: int | None = None,
) -> None:
    """Store one track's vector, replacing whatever was there.

    One track at a time: the batch reads results from a subprocess line by
    line and writes each one as it arrives. `connect()` opens with
    `isolation_level=None` (autocommit, see `db.py`), so this single
    `execute()` is already durable the moment it returns -- that is what
    makes a kill mid-batch cost one track rather than all of them. The
    trailing `.commit()` is a no-op here; it is kept for symmetry with
    `embeddings.upsert`, not because it does work. (`taste.replace_all` is
    the other precedent for a single-statement write, and it omits the
    `.commit()` entirely -- either shape is correct under autocommit.)
    """
    at = now if now is not None else _now()
    conn.execute(
        "INSERT INTO content_embeddings (track_rowid, content_vec, model,"
        " embedded_at) VALUES (?, ?, ?, ?)"
        " ON CONFLICT(track_rowid) DO UPDATE SET"
        "   content_vec = excluded.content_vec,"
        "   model = excluded.model,"
        "   embedded_at = excluded.embedded_at",
        (track_rowid, content_vec, model, at),
    )
    conn.commit()


def vectors(conn: sqlite3.Connection) -> dict[int, list[float]]:
    """Every stored vector, unpacked. The corpus-statistics recompute needs
    all of them at once; nothing else should call this.

    Ordered by `track_rowid` so the recompute's fixed-seed sampling is
    deterministic by construction rather than by relying on SQLite's
    incidental full-scan order -- the caller's `STATS_SAMPLE_SEED` comment
    promises the same corpus always produces the same anchors, and that
    promise only holds if the input to `random.Random(...).sample(...)` is
    itself stable.
    """
    rows = conn.execute(
        "SELECT track_rowid, content_vec FROM content_embeddings"
        " WHERE content_vec IS NOT NULL"
        " ORDER BY track_rowid"
    ).fetchall()
    return {row["track_rowid"]: unpack_vector(row["content_vec"]) for row in rows}


def replace_stats(
    conn: sqlite3.Connection,
    *,
    mean_vec: bytes,
    sim_lo: float,
    sim_hi: float,
    track_count: int,
    now: int | None = None,
) -> None:
    """Overwrite the single statistics row. `id = 1` is enforced by a CHECK
    on the table, so there is never a second row to choose between."""
    at = now if now is not None else _now()
    conn.execute(
        "INSERT INTO content_stats (id, mean_vec, sim_lo, sim_hi,"
        " track_count, computed_at) VALUES (1, ?, ?, ?, ?, ?)"
        " ON CONFLICT(id) DO UPDATE SET"
        "   mean_vec = excluded.mean_vec,"
        "   sim_lo = excluded.sim_lo,"
        "   sim_hi = excluded.sim_hi,"
        "   track_count = excluded.track_count,"
        "   computed_at = excluded.computed_at",
        (mean_vec, sim_lo, sim_hi, track_count, at),
    )
    conn.commit()


def stats(conn: sqlite3.Connection) -> dict | None:
    """The corpus statistics `reco.content_sim` needs, with `mean_vec`
    already unpacked. None before any batch has run -- which is the same
    thing as the leg being unavailable."""
    row = conn.execute("SELECT * FROM content_stats WHERE id = 1").fetchone()
    if row is None:
        return None
    return {
        "mean_vec": unpack_vector(row["mean_vec"]),
        "sim_lo": row["sim_lo"],
        "sim_hi": row["sim_hi"],
        "track_count": row["track_count"],
        "computed_at": row["computed_at"],
    }


def coverage(conn: sqlite3.Connection) -> dict:
    """Tracks with a content vector vs. tracks playable, for the health
    endpoint -- same shape as `library.embeddings.coverage`, plus the size
    and age of the statistics the leg is currently normalizing against, so a
    mean computed over a third of the library is visible rather than
    implied."""
    playable = conn.execute(
        "SELECT COUNT(*) FROM tracks WHERE status = 'done'"
    ).fetchone()[0]
    embedded = conn.execute(
        "SELECT COUNT(*) FROM content_embeddings c JOIN tracks t"
        "  USING (track_rowid)"
        " WHERE t.status = 'done' AND c.content_vec IS NOT NULL"
    ).fetchone()[0]
    row = conn.execute(
        "SELECT track_count, computed_at FROM content_stats WHERE id = 1"
    ).fetchone()
    return {
        "playable": playable,
        "embedded": embedded,
        "track_count": row["track_count"] if row else None,
        "computed_at": row["computed_at"] if row else None,
    }
