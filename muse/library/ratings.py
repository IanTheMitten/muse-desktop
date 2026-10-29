"""Thumbs up and thumbs down.

The only explicit signal in the system. Everything else slice 2 learns from is
inferred from behaviour, which makes a small number of unambiguous statements
disproportionately valuable: a thumbs-down on a track played all the way to the
end is information no play span can express.

One rating per track, replaceable, stamped with when it was given. Unlike
`plays`, this is a statement of *present* opinion rather than a record of a
physical event — changing your mind replaces the old answer instead of
appending to it, which is why the table is keyed by track and why ADR 0001's
never-destroy rule does not extend to it.
"""

import sqlite3
import time

UP = 1
DOWN = -1
VALID = (UP, DOWN)


def set_rating(
    conn: sqlite3.Connection,
    track_rowid: int,
    rating: int | None,
    *,
    now: int | None = None,
) -> int | None:
    """Record, replace, or clear a rating. Returns what the track now carries.

    `None` clears. Passing the rating the track already has does *not* clear it
    — flipping and clearing are different intentions, and deciding one from the
    other belongs to whatever is doing the tapping, not here.
    """
    if rating is None:
        conn.execute("DELETE FROM ratings WHERE track_rowid = ?", (track_rowid,))
        return None

    if rating not in VALID:
        raise ValueError(f"rating must be one of {VALID}, not {rating!r}")

    at = now if now is not None else int(time.time())
    conn.execute(
        "INSERT INTO ratings (track_rowid, rating, created_at) VALUES (?, ?, ?)"
        " ON CONFLICT(track_rowid) DO UPDATE SET"
        "   rating = excluded.rating,"
        "   created_at = excluded.created_at",
        (track_rowid, rating, at),
    )
    return rating


def get_rating(conn: sqlite3.Connection, track_rowid: int) -> int | None:
    row = conn.execute(
        "SELECT rating FROM ratings WHERE track_rowid = ?", (track_rowid,)
    ).fetchone()
    return row["rating"] if row else None


def counts(conn: sqlite3.Connection) -> dict:
    """Up and down totals, for the health endpoint.

    Nothing in slice 1 consumes ratings; this is the only way to see from
    outside the database that the button is actually recording anything.
    """
    row = conn.execute(
        "SELECT SUM(rating = 1) AS up, SUM(rating = -1) AS down FROM ratings"
    ).fetchone()
    return {"up": row["up"] or 0, "down": row["down"] or 0}


def all_ratings(conn: sqlite3.Connection) -> dict[int, int]:
    """Every explicit rating, keyed by track. The recommender applies these
    as a constraint over a whole candidate set, so it wants them in one read
    rather than one query per candidate."""
    rows = conn.execute("SELECT track_rowid, rating FROM ratings").fetchall()
    return {row["track_rowid"]: row["rating"] for row in rows}
