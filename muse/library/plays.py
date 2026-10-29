"""Play spans, and the classification `plays` deliberately does not store.

See ADR 0001. `plays` records physical fact. `skip` / `full_listen` / `partial`
are this pure function over those columns, so the thresholds below can be
retuned once there is real history to look at and the entire log recomputed
under the new rule.

Nothing consumes `classify_play()` until slice 2. It is written and tested now
because doing so is what proves the schema can produce the derivation at all,
and a schema found to be wrong after it holds irreplaceable data cannot be
fixed.
"""

import sqlite3
import time

SKIP_RATIO = 0.15
FULL_LISTEN_RATIO = 0.85

SKIP = "skip"
PARTIAL = "partial"
FULL_LISTEN = "full_listen"
UNKNOWN = "unknown"

# Closures with a user behind them. `lost` is not one of them.
INTENTIONAL_REASONS = frozenset(
    {"track_end", "user_next", "user_prev", "new_selection", "pause_timeout"}
)


def classify_play(
    reached_ms: int | None,
    duration_ms: int | None,
    ended_reason: str | None,
) -> str:
    """One of `skip`, `partial`, `full_listen`, `unknown`.

    `unknown` is returned whenever the span carries no evidence of intent: a
    `lost` closure (the phone died — the listener chose nothing), a span still
    open, or a track whose duration was never known. Slice 2 excludes these
    from training pairs rather than guessing at them.
    """
    if ended_reason is None or ended_reason == "lost":
        return UNKNOWN
    if not duration_ms or duration_ms <= 0:
        return UNKNOWN
    if ended_reason == "track_end":
        return FULL_LISTEN

    ratio = (reached_ms or 0) / duration_ms
    if ratio < SKIP_RATIO:
        return SKIP
    if ratio >= FULL_LISTEN_RATIO:
        return FULL_LISTEN
    return PARTIAL


def open_span(
    conn: sqlite3.Connection,
    track_rowid: int,
    duration_ms: int,
    source: str | None = None,
    now_ms: int | None = None,
) -> int:
    """Start a span. Returns its id."""
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    cursor = conn.execute(
        "INSERT INTO plays (track_rowid, started_at, reached_ms, duration_ms,"
        "                   source, heartbeat_at)"
        " VALUES (?, ?, 0, ?, ?, ?)",
        (track_rowid, now, duration_ms, source, now),
    )
    return cursor.lastrowid


def heartbeat(
    conn: sqlite3.Connection,
    play_id: int,
    position_ms: int,
    seek_count: int | None = None,
    now_ms: int | None = None,
) -> None:
    """Advance a span.

    `reached_ms` moves forward only. Seeking backwards must not shrink the
    furthest point reached, or a listener who rewinds to hear a bridge again
    looks like a listener who skipped.

    A closed span is never touched: the `ended_at IS NULL` guard makes a late
    heartbeat arriving after the close a no-op rather than a resurrection.
    """
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    conn.execute(
        "UPDATE plays"
        "   SET reached_ms = MAX(reached_ms, ?),"
        "       seek_count = COALESCE(?, seek_count),"
        "       heartbeat_at = ?"
        " WHERE id = ? AND ended_at IS NULL",
        (position_ms, seek_count, now, play_id),
    )


def close_span(
    conn: sqlite3.Connection,
    play_id: int,
    ended_reason: str,
    position_ms: int | None = None,
    now_ms: int | None = None,
) -> None:
    """Close a span with a reason. Closing an already-closed span does nothing."""
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    conn.execute(
        "UPDATE plays"
        "   SET ended_at = ?,"
        "       ended_reason = ?,"
        "       reached_ms = MAX(reached_ms, COALESCE(?, 0))"
        " WHERE id = ? AND ended_at IS NULL",
        (now, ended_reason, position_ms, play_id),
    )


def sweep_stale(
    conn: sqlite3.Connection,
    stale_after_ms: int = 60_000,
    now_ms: int | None = None,
) -> int:
    """Close spans with no recent heartbeat as `lost`. Returns how many.

    Runs on a 30-second tick against a 60-second threshold, so one missed
    heartbeat is tolerated and two are not.
    """
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    cursor = conn.execute(
        "UPDATE plays"
        "   SET ended_at = heartbeat_at, ended_reason = 'lost'"
        " WHERE ended_at IS NULL AND heartbeat_at < ?",
        (now - stale_after_ms,),
    )
    return cursor.rowcount


def session_plays(conn: sqlite3.Connection, since_ms: int) -> list[sqlite3.Row]:
    """Play rows at or after `since_ms`, oldest first.

    Callers that need the session's
    play *outcomes* in the order they happened, not merely which tracks were
    played -- unlike `played_since()`, which only needs the set. `started_at,
    id` breaks a tie between two rows sharing a timestamp the same way every
    time, matching the tie-break `_top_k` already uses elsewhere in this
    seam.

    Selects exactly the columns `classify_play()` and the nudge pairing need
    -- `reached_ms`, `duration_ms`, `ended_reason` to classify, `track_rowid`
    to look up the play's vectors -- rather than `SELECT *`, so a schema
    change to an unrelated column cannot silently change what this returns.

    `plays.started_at` is unix **milliseconds** (`muse/library/schema.py:55`,
    and the `now_ms` parameters on `open_span`/`close_span`/`sweep_stale`),
    so this argument is milliseconds too. The radio wire format carries
    `since` in unix seconds (`docs/specs/2026-09-21-radio-drift-policy.md:263`);
    the conversion belongs at the caller's seam, not here.
    """
    return conn.execute(
        "SELECT track_rowid, started_at, reached_ms, duration_ms,"
        "       ended_reason"
        " FROM plays WHERE started_at >= ? ORDER BY started_at, id",
        (since_ms,),
    ).fetchall()


def played_since(conn: sqlite3.Connection, since: int) -> set[int]:
    """Track rowids with a play that started at or after `since`.

    The freshness rule needs the complement of this -- tracks *not* played
    lately -- and asking for the smaller set is cheaper on a library where
    most tracks have not been played in any given month.
    """
    rows = conn.execute(
        "SELECT DISTINCT track_rowid FROM plays WHERE started_at >= ?",
        (since,),
    ).fetchall()
    return {row["track_rowid"] for row in rows}


def closed_spans(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every closed play span, oldest first -- the raw material for the
    offline harness's transitions (`muse.reco_eval`). Same ordering and
    tie-break as `session_plays()`."""
    return conn.execute(
        "SELECT track_rowid, started_at, ended_at, reached_ms, duration_ms,"
        "       ended_reason, source"
        " FROM plays WHERE ended_at IS NOT NULL ORDER BY started_at, id"
    ).fetchall()
