"""`feedback_score`: one time-decayed number per track over `plays` and
`ratings`, repurposed by ADR 0003 as the taste embedding's sole training
input (ticket 04) rather than a `rank()` leg in its own right.

Pure over what's already in the database -- `now` is always injected, never
read from the wall clock, matching the PRD's silent-failure-mode testing
plan. `skip`/`full_listen` classification is `muse.library.plays.
classify_play`, reused rather than reimplemented, so the 15% position
threshold has exactly one definition in the codebase.
"""

import math

import pytest

from muse.feedback import feedback_score
from muse.library import close_span, open_span, set_rating
from tests.conftest import insert_track

DAY = 86_400


def _play(conn, track_rowid, *, started_at, duration_ms, reached_ms,
          ended_reason):
    play_id = open_span(conn, track_rowid, duration_ms=duration_ms, now_ms=started_at)
    close_span(
        conn, play_id, ended_reason, position_ms=reached_ms,
        now_ms=started_at + reached_ms,
    )
    return play_id


def test_a_track_with_no_events_scores_zero(conn):
    insert_track(conn, 1)

    assert feedback_score(conn, 1, now=1_000_000) == 0.0


def test_a_full_listen_scores_positive(conn):
    insert_track(conn, 1)
    _play(
        conn, 1, started_at=0, duration_ms=180_000, reached_ms=180_000,
        ended_reason="track_end",
    )

    # now == the play's end (180_000 ms = 180 s): zero decay, so the raw
    # weight (0.5) divided by the clamp (3.0) is exact.
    assert feedback_score(conn, 1, now=180) == 0.5 / 3.0


def test_an_early_skip_scores_more_negatively_than_a_late_skip(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    _play(
        conn, 1, started_at=0, duration_ms=200_000, reached_ms=10_000,  # 5%
        ended_reason="user_next",
    )
    _play(
        conn, 2, started_at=0, duration_ms=200_000, reached_ms=100_000,  # 50%
        ended_reason="user_next",
    )

    early = feedback_score(conn, 1, now=1000)
    late = feedback_score(conn, 2, now=1000)

    assert early < late < 0


def test_recent_feedback_counts_more_than_old_feedback(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    # Identical early skip on both tracks, but track 2's happened 30 days
    # before `now` and track 1's happened right at `now`.
    _play(
        conn, 1, started_at=30 * DAY * 1000, duration_ms=200_000,
        reached_ms=1_000, ended_reason="user_next",
    )
    _play(
        conn, 2, started_at=0, duration_ms=200_000, reached_ms=1_000,
        ended_reason="user_next",
    )

    recent = feedback_score(conn, 1, now=30 * DAY)
    old = feedback_score(conn, 2, now=30 * DAY)

    assert recent < old  # more negative: the recent skip has decayed less


def test_a_lost_play_is_excluded_entirely(conn):
    insert_track(conn, 1)
    _play(
        conn, 1, started_at=0, duration_ms=200_000, reached_ms=5_000,
        ended_reason="lost",
    )

    assert feedback_score(conn, 1, now=0) == 0.0


def test_thumbs_down_folds_in_at_its_own_weight(conn):
    insert_track(conn, 1)
    set_rating(conn, 1, -1, now=0)

    # now == the rating's timestamp: zero decay. -3.0 clamped to -3.0,
    # normalized to exactly -1.0.
    assert feedback_score(conn, 1, now=0) == -1.0


def test_thumbs_up_contributes_nothing(conn):
    """Neither the PRD's weight table nor this ticket assigns thumbs_up a
    weight -- only thumbs_down folds into the decayed sum. This is a
    deliberate scope boundary (PRD open question O6 is about something else:
    whether an explicit rating should additionally clamp the derived score),
    not an oversight."""
    insert_track(conn, 1)
    set_rating(conn, 1, 1, now=0)

    assert feedback_score(conn, 1, now=0) == 0.0


def test_a_replay_scores_higher_than_a_lone_full_listen(conn):
    insert_track(conn, 1)  # replayed
    insert_track(conn, 2)  # played once

    _play(
        conn, 1, started_at=0, duration_ms=180_000, reached_ms=180_000,
        ended_reason="track_end",
    )
    _play(
        conn, 1, started_at=180_000, duration_ms=180_000, reached_ms=180_000,
        ended_reason="track_end",
    )
    _play(
        conn, 2, started_at=0, duration_ms=180_000, reached_ms=180_000,
        ended_reason="track_end",
    )

    replayed = feedback_score(conn, 1, now=360)
    once = feedback_score(conn, 2, now=360)

    assert replayed > once


def test_replay_tie_break_on_identical_started_at_is_deterministic(conn):
    """Two plays (different tracks) can share the same millisecond
    `started_at` -- multi-device playback, or backfilled history. Whichever
    one counts as "immediately before" a later play must be a defined rule
    (most recent insertion order, via `id DESC`), not left to SQLite's query
    plan -- otherwise this test's own expected value would be nondeterministic."""
    insert_track(conn, 1)
    insert_track(conn, 2)
    # Both tied at started_at=0; track 2's play is inserted second, so it has
    # the higher id and wins the tie-break.
    _play(
        conn, 1, started_at=0, duration_ms=180_000, reached_ms=180_000,
        ended_reason="track_end",
    )
    _play(
        conn, 2, started_at=0, duration_ms=180_000, reached_ms=180_000,
        ended_reason="track_end",
    )
    # Track 1 played again right after: the tie-break winner (track 2, not
    # track 1's own first play) is what "immediately preceded" it, so this
    # is an ordinary full listen, not a replay.
    _play(
        conn, 1, started_at=180_000, duration_ms=180_000, reached_ms=180_000,
        ended_reason="track_end",
    )

    score = feedback_score(conn, 1, now=360)

    first_play_decay = math.exp(-0.1 * (180 / DAY))
    expected = (0.5 * first_play_decay + 0.5 * 1.0) / 3.0
    assert score == pytest.approx(expected, abs=1e-9)


def test_score_is_clamped_before_normalizing(conn):
    insert_track(conn, 1)
    # Five early skips close together: raw sum is close to 5 * -1.5 = -7.5,
    # far past the -3 clamp regardless of the small decay differences.
    for i in range(5):
        _play(
            conn, 1, started_at=i * 1000, duration_ms=200_000,
            reached_ms=1_000, ended_reason="user_next",
        )

    assert feedback_score(conn, 1, now=10) == -1.0
