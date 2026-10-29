"""How many times the recommender has offered each track, and when last.

An *offer* is the recommender returning a track as a candidate. O2 decays the
freshness boost to zero after three offers, on the reasoning that a track you
keep declining should stop being pushed -- which needs the count of times it
was pushed, a number nothing in the system had.

Not `plays`: ADR 0001 keeps that table to physical fact, and "we suggested
this" is not one. Not a column on `tracks`: that table says what the library
is, not what the recommender has been doing lately.

Sole reader and writer of `track_offers`, the same shape `library/content.py`
has over its two tables. The count is derived and disposable -- see the
migration's comment and `test_table_is_safe_to_clear`.
"""

import sqlite3
import time
from collections.abc import Iterable

__all__ = ["counts", "record_offers"]


def _now() -> int:
    return int(time.time())


def record_offers(
    conn: sqlite3.Connection,
    rowids: Iterable[int],
    *,
    now: int | None = None,
) -> None:
    """Count one offer for each track, creating rows as needed.

    `executemany` over one `INSERT ... ON CONFLICT` rather than a read then a
    write: the increment has to be atomic against a concurrent reader, and
    `offer_count = offer_count + 1` inside the upsert is the only form that
    is. `connect()` opens autocommit (`isolation_level=None`, see `db.py`), so
    each row is durable as it lands.

    An empty iterable is a no-op rather than an error. The caller is a
    recommender that legitimately returns nothing when the relaxation ladder
    has run out, and making that case raise would push a branch into every
    call site to say the same thing.
    """
    at = now if now is not None else _now()
    rows = [(rowid, at) for rowid in rowids]
    if not rows:
        return
    conn.executemany(
        "INSERT INTO track_offers (track_rowid, offer_count, last_offered)"
        " VALUES (?, 1, ?)"
        " ON CONFLICT(track_rowid) DO UPDATE SET"
        "   offer_count = offer_count + 1,"
        "   last_offered = excluded.last_offered",
        rows,
    )


def counts(conn: sqlite3.Connection) -> dict[int, int]:
    """`{track_rowid: offer_count}` for every track ever offered.

    A track that has never been offered is **absent**, not present at 0 --
    there is no difference between the two, and allowing both spellings would
    invite a caller to test for one and miss the other. Callers read this with
    `.get(rowid, 0)`.
    """
    rows = conn.execute(
        "SELECT track_rowid, offer_count FROM track_offers"
    ).fetchall()
    return {row["track_rowid"]: row["offer_count"] for row in rows}
