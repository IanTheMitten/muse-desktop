"""`feedback_score`: one time-decayed number per track over `plays` and
`ratings`.

The PRD originally specified this as a `rank()` leg (`w_fb * feedback_score`);
ADR 0003 repurposes it as the taste embedding's sole training input instead
(ticket 04) -- one event-weighting scheme, not two. Pure over what's already
in the database: `now` is always injected, never read from the wall clock,
matching the PRD's silent-failure-mode testing plan.

`skip`/`full_listen` classification is `muse.library.plays.classify_play`,
reused rather than reimplemented, so the 15% early-skip threshold has
exactly one definition in the codebase (ADR 0001: `plays` records physical
fact, the classification is a pure function over it, never stored).

`classify_play` treats any span reaching 85%+ of a track's duration as
FULL_LISTEN regardless of `ended_reason` -- an explicit `user_next` at 90%
scores here exactly like a natural `track_end`, and can even earn the
replay bonus below. That is `classify_play`'s existing, deliberate
approximation ("close enough to finished" beats "how it ended"), inherited
here rather than re-litigated.
"""

import math
import sqlite3

from .library.plays import FULL_LISTEN, UNKNOWN, classify_play
from .library.ratings import DOWN

# PRD: feedback(track) = clamp(sum(weight(event) * exp(-lambda * days_since)), -3, 3) / 3
SKIP_EARLY_WEIGHT = -1.5
SKIP_LATE_WEIGHT = -0.4
FULL_LISTEN_WEIGHT = 0.5
REPLAY_WEIGHT = 1.5
THUMBS_DOWN_WEIGHT = -3.0

# Matches classify_play's own SKIP_RATIO -- this module has no PARTIAL
# category of its own, so classify_play's SKIP and PARTIAL both fold into
# one of these two buckets by the same 15% line classify_play already uses.
SKIP_EARLY_RATIO = 0.15

DECAY_LAMBDA = 0.1  # ~7-day half-life
CLAMP = 3.0

_SECONDS_PER_DAY = 86_400


def feedback_score(conn: sqlite3.Connection, track_rowid: int, *, now: int) -> float:
    """Time-decayed sum over this track's play and rating history, clamped
    to [-3, 3] then normalized to [-1, 1]. Zero for a track with no events at
    all -- cold start is not a special case for a caller of this function.

    `now` is unix seconds -- `plays.started_at`/`ended_at` are stored in
    milliseconds (finer-grained heartbeat bookkeeping) and are converted
    here; `ratings.created_at` is already seconds.
    """
    total = 0.0

    plays = conn.execute(
        "SELECT started_at, ended_at, reached_ms, duration_ms, ended_reason"
        " FROM plays WHERE track_rowid = ? AND ended_reason IS NOT NULL",
        (track_rowid,),
    ).fetchall()
    for play in plays:
        weight = _play_weight(conn, track_rowid, play)
        if weight is None:
            continue
        days_since = (now - play["ended_at"] / 1000) / _SECONDS_PER_DAY
        total += weight * math.exp(-DECAY_LAMBDA * days_since)

    rating = conn.execute(
        "SELECT created_at FROM ratings WHERE track_rowid = ? AND rating = ?",
        (track_rowid, DOWN),
    ).fetchone()
    if rating is not None:
        days_since = (now - rating["created_at"]) / _SECONDS_PER_DAY
        total += THUMBS_DOWN_WEIGHT * math.exp(-DECAY_LAMBDA * days_since)

    clamped = max(-CLAMP, min(CLAMP, total))
    return clamped / CLAMP


def _play_weight(conn: sqlite3.Connection, track_rowid: int, play) -> float | None:
    """None for a play that carries no evidence of intent (a `lost` span, an
    open span, a track of unknown duration) -- excluded from the sum
    entirely rather than guessed at, same as `classify_play` itself."""
    category = classify_play(play["reached_ms"], play["duration_ms"], play["ended_reason"])
    if category == UNKNOWN:
        return None
    if category == FULL_LISTEN:
        if _is_replay(conn, track_rowid, play["started_at"]):
            return REPLAY_WEIGHT
        return FULL_LISTEN_WEIGHT
    ratio = (play["reached_ms"] or 0) / play["duration_ms"]
    return SKIP_EARLY_WEIGHT if ratio < SKIP_EARLY_RATIO else SKIP_LATE_WEIGHT


def _is_replay(conn: sqlite3.Connection, track_rowid: int, started_at: int) -> bool:
    """A full listen counts as a replay when this same track was also the
    very last thing played, immediately before it -- pressing play again on
    a song that just finished is direct evidence of "I want to hear that
    again", not just an ordinary listen. The PRD's weight table gives no
    other derivation for `replay` (only the skip split is specified), so
    this is this module's own judgment call, not a resolved spec."""
    # `id DESC` breaks a tie between two plays (of different tracks) that
    # share the same millisecond `started_at` -- without it, which one
    # counts as "immediately before" is left to SQLite's query plan rather
    # than a defined rule. `id` is autoincrement, so among tied timestamps it
    # recovers true insertion order.
    row = conn.execute(
        "SELECT track_rowid FROM plays WHERE started_at < ?"
        " ORDER BY started_at DESC, id DESC LIMIT 1",
        (started_at,),
    ).fetchone()
    return row is not None and row["track_rowid"] == track_rowid
