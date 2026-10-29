"""Play-span integrity, against a real database.

`plays` is the only irreplaceable data in the system (ADR 0001). Every failure
here is silent: a span that loses its furthest position, or a closed span that
a late heartbeat reopens, produces a log that looks fine and trains the wrong
model.
"""

from conftest import insert_track

from muse.library import (
    close_span,
    heartbeat,
    open_span,
    read_state,
    sweep_stale,
    write_state,
)


def a_track(conn, rowid=1):
    return insert_track(conn, rowid)


def span(conn, play_id):
    return dict(conn.execute("SELECT * FROM plays WHERE id = ?", (play_id,)).fetchone())


def test_reached_ms_never_moves_backwards(conn):
    """A listener who rewinds to hear a bridge again must not look like a
    listener who skipped."""
    track = a_track(conn)
    play = open_span(conn, track, duration_ms=180_000, now_ms=1_000)

    heartbeat(conn, play, position_ms=90_000, now_ms=2_000)
    heartbeat(conn, play, position_ms=40_000, now_ms=3_000)  # seeked back

    assert span(conn, play)["reached_ms"] == 90_000


def test_a_closed_span_is_never_mutated(conn):
    """Append-only. A heartbeat that arrives after the close is a no-op, not a
    resurrection."""
    track = a_track(conn)
    play = open_span(conn, track, duration_ms=180_000, now_ms=1_000)
    heartbeat(conn, play, position_ms=50_000, now_ms=2_000)
    close_span(conn, play, "user_next", position_ms=50_000, now_ms=3_000)

    heartbeat(conn, play, position_ms=170_000, now_ms=4_000)

    row = span(conn, play)
    assert row["reached_ms"] == 50_000
    assert row["ended_at"] == 3_000
    assert row["ended_reason"] == "user_next"


def test_closing_twice_keeps_the_first_reason(conn):
    track = a_track(conn)
    play = open_span(conn, track, duration_ms=180_000, now_ms=1_000)
    close_span(conn, play, "track_end", now_ms=2_000)
    close_span(conn, play, "user_next", now_ms=3_000)

    assert span(conn, play)["ended_reason"] == "track_end"


def test_the_sweeper_closes_a_stale_span_as_lost(conn):
    track = a_track(conn)
    play = open_span(conn, track, duration_ms=180_000, now_ms=1_000)
    heartbeat(conn, play, position_ms=20_000, now_ms=10_000)

    closed = sweep_stale(conn, stale_after_ms=60_000, now_ms=120_000)

    row = span(conn, play)
    assert closed == 1
    assert row["ended_reason"] == "lost"
    # Ends where the evidence ends, not when the sweeper happened to notice.
    assert row["ended_at"] == 10_000


def test_the_sweeper_leaves_a_live_span_alone(conn):
    track = a_track(conn)
    play = open_span(conn, track, duration_ms=180_000, now_ms=1_000)
    heartbeat(conn, play, position_ms=20_000, now_ms=100_000)

    assert sweep_stale(conn, stale_after_ms=60_000, now_ms=120_000) == 0
    assert span(conn, play)["ended_at"] is None


def test_the_sweeper_leaves_closed_spans_alone(conn):
    track = a_track(conn)
    play = open_span(conn, track, duration_ms=180_000, now_ms=1_000)
    close_span(conn, play, "track_end", now_ms=2_000)

    assert sweep_stale(conn, stale_after_ms=60_000, now_ms=999_000) == 0
    assert span(conn, play)["ended_reason"] == "track_end"


def test_duration_is_frozen_at_play_time(conn):
    """Denormalized on purpose: a later metadata correction must not change what
    a past play meant."""
    track = a_track(conn)
    play = open_span(conn, track, duration_ms=180_000, now_ms=1_000)
    conn.execute("UPDATE tracks SET duration_ms = 999_000 WHERE track_rowid = ?",
                 (track,))

    assert span(conn, play)["duration_ms"] == 180_000


# --- player state, through the database ------------------------------------

def test_state_round_trips(conn):
    assert write_state(conn, [1, 2, 3], 1, 42_000, "playing", updated_at=1_000) is True

    state = read_state(conn)
    assert state["queue"] == [1, 2, 3]
    assert state["queue_index"] == 1
    assert state["position_ms"] == 42_000


def test_an_idle_tab_cannot_overwrite_the_playing_device(conn):
    write_state(conn, [1, 2, 3], 1, 42_000, "playing", updated_at=1_000)
    assert write_state(conn, [9], 0, 0, "paused", updated_at=9_000) is False

    assert read_state(conn)["position_ms"] == 42_000


def test_a_stale_write_does_not_land(conn):
    write_state(conn, [1], 0, 42_000, "playing", updated_at=5_000)
    assert write_state(conn, [1], 0, 1_000, "playing", updated_at=4_000) is False

    assert read_state(conn)["position_ms"] == 42_000


def test_default_state_before_anything_is_written(conn):
    state = read_state(conn)
    assert state["queue"] == []
    assert state["updated_at"] == 0


# --- player state, through the API -----------------------------------------

def _app(tmp_path):
    from muse.api import create_app
    from tests.conftest import make_config
    from muse.library import db

    application = create_app(make_config(tmp_path))
    application.state.conn = db.connect(tmp_path / "library.sqlite3")
    return application


def _route(app, path, method):
    for candidate in app.routes:
        if getattr(candidate, "path", None) == path and method in candidate.methods:
            return candidate.endpoint
    raise AssertionError(f"no {method} {path}")


def test_radio_seed_survives_the_round_trip(tmp_path):
    """Radio mode lives in the same guarded heartbeat as shuffle and repeat so
    it follows the listener between devices. A `radio_seed` the write drops on
    the floor makes radio a per-device setting instead."""
    from muse.api import StateWrite

    app = _app(tmp_path)
    _route(app, "/api/player/state", "PUT")(
        StateWrite(queue=[1, 2], queue_index=0, position_ms=0,
                   client_state="playing", radio_seed=7))

    assert _route(app, "/api/player/state", "GET")()["radio_seed"] == 7


def test_radio_off_stays_off(tmp_path):
    """Null means radio is off, and the default has to keep meaning that --
    a write that quietly invented a seed would turn radio on by itself."""
    from muse.api import StateWrite

    app = _app(tmp_path)
    _route(app, "/api/player/state", "PUT")(
        StateWrite(queue=[1], queue_index=0, position_ms=0,
                   client_state="playing"))

    assert _route(app, "/api/player/state", "GET")()["radio_seed"] is None


def test_radio_added_survives_the_round_trip(tmp_path):
    """Which queued tracks radio put there. Without it a reload, or opening
    the phone after the laptop, restores the queue but tags every radio pick
    `source='queue'` -- and the offline harness reads `source` to keep the
    recommender's own picks from flattering it."""
    from muse.api import StateWrite

    app = _app(tmp_path)
    _route(app, "/api/player/state", "PUT")(
        StateWrite(queue=[1, 2, 3], queue_index=0, position_ms=0,
                   client_state="playing", radio_seed=1, radio_added=[2, 3]))

    assert _route(app, "/api/player/state", "GET")()["radio_added"] == [2, 3]


def test_radio_added_defaults_to_empty(conn):
    assert read_state(conn)["radio_added"] == []
    write_state(conn, [1], 0, 0, "playing", updated_at=1_000)
    assert read_state(conn)["radio_added"] == []
