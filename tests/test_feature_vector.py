"""The raw 13-dim reading behind the embed batch's feature_vec (ticket 01),
and the normalization bounds it is scaled against.

Frozen, not recomputed: the bounds are captured once, the first time there is
anything measured to capture them from, and never rescaled after -- adding one
more track must not silently redefine what an already-stored feature_vec
means (ADR 0003 / PRD open question O3).
"""

from muse.library import features
from tests.conftest import insert_track


def _stock(conn, rowid, **overrides):
    values = {
        "danceability": 0.5, "energy": 0.5, "loudness": -8.0,
        "speechiness": 0.05, "acousticness": 0.2, "instrumentalness": 0.0,
        "liveness": 0.1, "valence": 0.5, "tempo": 120.0, "key": 0, "mode": 1,
        "time_signature": 4,
    }
    duration_ms = overrides.pop("duration_ms", 180_000)
    values.update(overrides)
    insert_track(conn, rowid, duration_ms=duration_ms)
    conn.execute(
        "INSERT INTO track_features (track_rowid, danceability, energy,"
        " loudness, speechiness, acousticness, instrumentalness, liveness,"
        " valence, tempo, key, mode, time_signature, checked_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
        (
            rowid, values["danceability"], values["energy"], values["loudness"],
            values["speechiness"], values["acousticness"],
            values["instrumentalness"], values["liveness"], values["valence"],
            values["tempo"], values["key"], values["mode"],
            values["time_signature"],
        ),
    )


def test_raw_feature_values_is_none_when_never_measured(conn):
    insert_track(conn, 1)  # no track_features row at all

    assert features.raw_feature_values(conn, 1) is None


def test_raw_feature_values_is_none_on_a_null_response_row(conn):
    insert_track(conn, 1)
    conn.execute(
        "INSERT INTO track_features (track_rowid, checked_at) VALUES (1, 0)"
    )

    assert features.raw_feature_values(conn, 1) is None


def test_raw_feature_values_returns_the_thirteen_in_order(conn):
    _stock(conn, 1, danceability=0.7, energy=0.6, loudness=-5.0,
           speechiness=0.1, acousticness=0.3, instrumentalness=0.0,
           liveness=0.2, valence=0.9, tempo=140.0, key=7, mode=0,
           time_signature=3)

    assert features.raw_feature_values(conn, 1) == (
        0.7, 0.6, -5.0, 0.1, 0.3, 0.0, 0.2, 0.9, 140.0, 7, 0, 3, 180_000,
    )
    assert features.FEATURE_VEC_COLUMNS == (
        "danceability", "energy", "loudness", "speechiness", "acousticness",
        "instrumentalness", "liveness", "valence", "tempo", "key", "mode",
        "time_signature", "duration_ms",
    )


def test_frozen_bounds_is_empty_before_anything_is_measured(conn):
    insert_track(conn, 1)

    assert features.frozen_bounds(conn) == {}


def test_frozen_bounds_captures_min_and_max_across_measured_tracks(conn):
    _stock(conn, 1, tempo=90.0)
    _stock(conn, 2, tempo=150.0)

    bounds = features.frozen_bounds(conn)

    assert bounds["tempo"] == (90.0, 150.0)


def test_frozen_bounds_does_not_rescale_once_set(conn):
    _stock(conn, 1, tempo=90.0)
    _stock(conn, 2, tempo=150.0)
    first = features.frozen_bounds(conn)

    _stock(conn, 3, tempo=300.0)  # a wild outlier added after the freeze
    second = features.frozen_bounds(conn)

    assert second == first
    assert second["tempo"] == (90.0, 150.0)
