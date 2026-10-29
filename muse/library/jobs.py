"""Background work, and how far along it is.

One table, one row per run. Slice 1 has a single kind, `art_backfill`, but the
shape is the one slice 3's downloads need: a state, a fraction, and a message a
human can read.

A job row is a record of something that happened, so it is never deleted and
never rewritten into a different job — `state` moves forward only.
"""

import json
import sqlite3
import time

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
ERROR = "error"
CANCELLED = "cancelled"

# States from which nothing further happens.
TERMINAL = frozenset({DONE, ERROR, CANCELLED})


def _now() -> int:
    return int(time.time())


def _job(row: sqlite3.Row) -> dict:
    job = dict(row)
    job["payload"] = json.loads(job["payload"] or "{}")
    return job


def create(
    conn: sqlite3.Connection,
    kind: str,
    payload: dict | None = None,
    *,
    now: int | None = None,
) -> int:
    at = now if now is not None else _now()
    cursor = conn.execute(
        "INSERT INTO jobs (kind, payload, state, progress, message,"
        "                  created_at, updated_at)"
        " VALUES (?, ?, ?, 0, NULL, ?, ?)",
        (kind, json.dumps(payload or {}), QUEUED, at, at),
    )
    conn.commit()
    return int(cursor.lastrowid)


def update(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    state: str | None = None,
    progress: float | None = None,
    message: str | None = None,
    now: int | None = None,
) -> dict | None:
    """Advance a job. Only the fields given are touched.

    A job that has already finished is left alone: a late progress callback
    arriving after an error must not resurrect the run as `running`, which
    would leave a job that never ends.
    """
    existing = get(conn, job_id)
    if existing is None or existing["state"] in TERMINAL:
        return existing

    sets, values = [], []
    if state is not None:
        sets.append("state = ?")
        values.append(state)
    if progress is not None:
        sets.append("progress = ?")
        values.append(max(0.0, min(1.0, float(progress))))
    if message is not None:
        sets.append("message = ?")
        values.append(message)
    sets.append("updated_at = ?")
    values.append(now if now is not None else _now())
    values.append(job_id)

    conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", values)
    conn.commit()
    return get(conn, job_id)


def get(conn: sqlite3.Connection, job_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _job(row) if row else None


def recent(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_job(row) for row in rows]


def active(conn: sqlite3.Connection, kind: str) -> dict | None:
    """The unfinished job of this kind, if there is one.

    What stops two art backfills running at once and asking MusicBrainz for
    everything twice.
    """
    row = conn.execute(
        "SELECT * FROM jobs WHERE kind = ? AND state IN (?, ?)"
        " ORDER BY id DESC LIMIT 1",
        (kind, QUEUED, RUNNING),
    ).fetchone()
    return _job(row) if row else None


def abandon_running(conn: sqlite3.Connection) -> int:
    """Close out jobs left `running` by a process that died.

    Nothing resumes them — the work itself is idempotent and the next run
    simply picks up what is still missing. What matters is that a stale
    `running` row does not block the next start forever.
    """
    cursor = conn.execute(
        "UPDATE jobs SET state = ?, message = ?, updated_at = ?"
        " WHERE state IN (?, ?)",
        (ERROR, "Interrupted by a restart", _now(), QUEUED, RUNNING),
    )
    conn.commit()
    return cursor.rowcount


def last(
    conn: sqlite3.Connection, kind: str, *, state: str | None = None
) -> dict | None:
    """The most recent job of this kind, optionally filtered to one state --
    e.g. the health endpoint's "last successful retrain" stat."""
    if state is not None:
        row = conn.execute(
            "SELECT * FROM jobs WHERE kind = ? AND state = ?"
            " ORDER BY id DESC LIMIT 1",
            (kind, state),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM jobs WHERE kind = ? ORDER BY id DESC LIMIT 1",
            (kind,),
        ).fetchone()
    return _job(row) if row else None


def abandon_running_of_kind(
    conn: sqlite3.Connection, kind: str, *, now: int | None = None
) -> int:
    """Close out jobs of this one kind left `running` or `queued` by a
    process that died -- scoped, unlike `abandon_running`, so a standalone
    script (`muse.taste_retrain`) can reap its own stale runs at start
    without touching a job of a different kind that happens to be genuinely
    in flight in the long-lived API process at the same moment.
    """
    at = now if now is not None else _now()
    cursor = conn.execute(
        "UPDATE jobs SET state = ?, message = ?, updated_at = ?"
        " WHERE kind = ? AND state IN (?, ?)",
        (ERROR, "Interrupted by a restart", at, kind, QUEUED, RUNNING),
    )
    conn.commit()
    return cursor.rowcount


def running_ids_of_kind(conn: sqlite3.Connection, kind: str) -> list[int]:
    """Ids of this kind's `running` jobs — the ones a startup reap should
    close as interrupted, because a job killed mid-pipeline is not safely
    resumable partway through."""
    rows = conn.execute(
        "SELECT id FROM jobs WHERE kind = ? AND state = ? ORDER BY id",
        (kind, RUNNING),
    ).fetchall()
    return [row["id"] for row in rows]


def queued_ids_of_kind(conn: sqlite3.Connection, kind: str) -> list[int]:
    """Ids of this kind's `queued` jobs — never touched by any reap, since
    a job that never started is exactly as safe to run after a restart as
    before one. The caller feeds these back into whatever picks up new
    work (`jobs.py`'s download worker queue)."""
    rows = conn.execute(
        "SELECT id FROM jobs WHERE kind = ? AND state = ? ORDER BY id",
        (kind, QUEUED),
    ).fetchall()
    return [row["id"] for row in rows]
