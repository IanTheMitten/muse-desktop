"""`muse.taste`'s pure logic: co-occurrence pairs from `plays`, the
per-track play-event threshold, and `run_retrain`'s orchestration --
tested against `StubTasteTrainer` throughout, the same way the embed batch
is tested against the stub encoders (`tests/test_embed_pure.py`). None of
this imports numpy or `implicit`.
"""

import pytest

from muse.embed import unpack_vector
from muse.taste import (
    MIN_PLAY_EVENTS,
    SESSION_GAP_SECONDS,
    StubTasteTrainer,
    build_interactions,
    play_event_counts,
    run_retrain,
)
from muse.library import close_span, open_span
from tests.conftest import insert_track


def _play(conn, track_rowid, *, started_at, ended_reason="track_end",
          duration_ms=180_000, reached_ms=180_000):
    play_id = open_span(conn, track_rowid, duration_ms=duration_ms, now_ms=started_at)
    close_span(
        conn, play_id, ended_reason, position_ms=reached_ms,
        now_ms=started_at + reached_ms,
    )


def test_play_event_counts_counts_only_closed_spans(conn):
    insert_track(conn, 1)
    _play(conn, 1, started_at=0)
    _play(conn, 1, started_at=200_000)
    open_span(conn, 1, duration_ms=180_000, now_ms=400_000)  # still open

    assert play_event_counts(conn) == {1: 2}


def test_build_interactions_pairs_tracks_played_in_sequence(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    _play(conn, 1, started_at=0)                    # ends at 180_000
    _play(conn, 2, started_at=180_000)               # starts immediately after

    pairs = build_interactions(conn)

    assert pairs == [(1, 2, 1.0)]


def test_build_interactions_ignores_a_gap_beyond_the_session_window(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    _play(conn, 1, started_at=0)                              # ends at 180_000
    _play(conn, 2, started_at=180_000 + 3_600_000)             # an hour later

    assert build_interactions(conn) == []


def test_build_interactions_ignores_a_replay_of_the_same_track(conn):
    insert_track(conn, 1)
    _play(conn, 1, started_at=0)
    _play(conn, 1, started_at=180_000)

    assert build_interactions(conn) == []


def test_build_interactions_weights_a_repeated_pair_higher(conn):
    """Two separate (1 then 2) episodes, far enough apart that the gap
    between episode 1's track 2 and episode 2's track 1 exceeds the session
    window -- so only the two forward (1 -> 2) adjacencies count, not a
    spurious reverse (2 -> 1) one from butting the episodes together."""
    insert_track(conn, 1)
    insert_track(conn, 2)
    _play(conn, 1, started_at=0)                     # ends at 180_000
    _play(conn, 2, started_at=180_000)                # ends at 360_000
    far_later = 360_000 + (SESSION_GAP_SECONDS + 1) * 1000
    _play(conn, 1, started_at=far_later)              # ends at far_later + 180_000
    _play(conn, 2, started_at=far_later + 180_000)

    pairs = build_interactions(conn)

    assert pairs == [(1, 2, 2.0)]


def test_build_interactions_excludes_unclosed_or_lost_spans(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    _play(conn, 1, started_at=0, ended_reason="lost")
    _play(conn, 2, started_at=180_000)

    assert build_interactions(conn) == []


def test_run_retrain_nulls_a_track_below_the_threshold(conn):
    insert_track(conn, 1)
    for i in range(MIN_PLAY_EVENTS - 1):
        _play(conn, 1, started_at=i * 200_000)

    result = run_retrain(conn, StubTasteTrainer(), now=1000)

    row = conn.execute(
        "SELECT taste_vec FROM taste_embeddings WHERE track_rowid = 1"
    ).fetchone()
    assert row["taste_vec"] is None
    assert result.considered == 1
    assert result.trained == 0
    assert result.skipped_below_threshold == 1


def test_run_retrain_trains_a_track_at_the_threshold(conn):
    insert_track(conn, 1)
    for i in range(MIN_PLAY_EVENTS):
        _play(conn, 1, started_at=i * 200_000)

    result = run_retrain(conn, StubTasteTrainer(), now=1000)

    row = conn.execute(
        "SELECT taste_vec, model, trained_at FROM taste_embeddings"
        " WHERE track_rowid = 1"
    ).fetchone()
    assert row["taste_vec"] is not None
    assert unpack_vector(row["taste_vec"]) == StubTasteTrainer().fit([], [1])[1]
    assert row["model"] == "stub-als"
    assert row["trained_at"] == 1000
    assert result.trained == 1


def test_run_retrain_covers_every_playable_track_every_run(conn):
    insert_track(conn, 1)
    insert_track(conn, 2, status="error")  # not playable: excluded

    result = run_retrain(conn, StubTasteTrainer(), now=1000)

    assert result.considered == 1
    rows = {r["track_rowid"] for r in conn.execute("SELECT track_rowid FROM taste_embeddings")}
    assert rows == {1}


def test_run_retrain_replaces_the_previous_snapshot(conn):
    insert_track(conn, 1)
    for i in range(MIN_PLAY_EVENTS):
        _play(conn, 1, started_at=i * 200_000)
    run_retrain(conn, StubTasteTrainer(), now=1000)
    first_vec = conn.execute(
        "SELECT taste_vec FROM taste_embeddings WHERE track_rowid = 1"
    ).fetchone()["taste_vec"]

    run_retrain(conn, StubTasteTrainer(), now=2000)

    row = conn.execute(
        "SELECT taste_vec, trained_at FROM taste_embeddings WHERE track_rowid = 1"
    ).fetchone()
    # Same deterministic stub, same input -> same vector; what changed is the
    # timestamp, proving a second run actually rewrote the row rather than
    # leaving the first snapshot in place untouched.
    assert row["taste_vec"] == first_vec
    assert row["trained_at"] == 2000


def test_a_raising_trainer_leaves_the_previous_snapshot_untouched(conn):
    insert_track(conn, 1)
    for i in range(MIN_PLAY_EVENTS):
        _play(conn, 1, started_at=i * 200_000)
    run_retrain(conn, StubTasteTrainer(), now=1000)

    class ExplodingTrainer:
        model_name = "exploding"

        def fit(self, interactions, track_rowids):
            raise RuntimeError("simulated ALS failure")

    with pytest.raises(RuntimeError):
        run_retrain(conn, ExplodingTrainer(), now=2000)

    row = conn.execute(
        "SELECT trained_at FROM taste_embeddings WHERE track_rowid = 1"
    ).fetchone()
    assert row["trained_at"] == 1000  # untouched by the failed run
