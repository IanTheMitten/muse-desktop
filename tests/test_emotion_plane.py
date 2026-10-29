"""The mood plane drawn from our own emotion vectors.

The point of the module under test is that a band is defined relative to this
library rather than against an absolute threshold, so these tests are mostly
about that relativity being real and being honest about its cost.
"""

from muse.embed import pack_vector
from muse.library import emotion, insights

from tests.conftest import insert_track


def _measured(conn, rowid, valence, arousal, **track):
    insert_track(conn, rowid, **track)
    conn.execute(
        "INSERT INTO embeddings (track_rowid, emotion_vec, indexed_at)"
        " VALUES (?, ?, 0)",
        (rowid, pack_vector([valence, arousal])),
    )


def test_the_split_is_the_library_s_own_median(conn):
    """Four points clustered high on both axes are still split into quadrants,
    because the centre moves to them. Against a fixed zero they would all be
    'bright' and the map would be one corner."""
    for i, (valence, arousal) in enumerate(
        [(0.5, 0.6), (0.7, 0.8), (0.55, 0.9), (0.9, 0.65)], start=1
    ):
        _measured(conn, i, valence, arousal)

    centre = emotion.medians(conn)
    assert 0.5 < centre["valence"] < 0.9
    assert 0.6 < centre["arousal"] < 0.9

    bands = {row["track_rowid"]: row["band"] for row in emotion.map_points(conn)}
    assert len(set(bands.values())) == 4, (
        "a library clustered in one corner should still fill four quadrants"
    )


def test_bands_land_where_the_quadrant_names_say(conn):
    _measured(conn, 1, -1.0, -1.0)
    _measured(conn, 2, 1.0, 1.0)
    _measured(conn, 3, -1.0, 1.0)
    _measured(conn, 4, 1.0, -1.0)

    bands = {row["track_rowid"]: row["band"] for row in emotion.map_points(conn)}
    assert bands == {1: "low", 2: "bright", 3: "intense", 4: "warm"}


def test_coordinates_come_back_raw_with_the_centre_beside_them(conn):
    """The endpoint does not pre-normalise. A client that only gets normalised
    coordinates cannot check them against anything."""
    _measured(conn, 1, -0.42, 0.31)

    point = emotion.map_points(conn)[0]
    assert point["valence"] == -0.42
    assert point["arousal"] == 0.31
    assert emotion.medians(conn)["valence"] == -0.42


def test_unplayable_tracks_are_not_on_a_map_whose_quadrants_are_play_buttons(conn):
    _measured(conn, 1, 0.5, 0.5)
    _measured(conn, 2, -0.5, -0.5, status="missing_file")

    assert [p["track_rowid"] for p in emotion.map_points(conn)] == [1]
    assert emotion.coverage(conn) == {"playable": 1, "measured": 1}


def test_a_band_with_nothing_in_it_is_still_reported(conn):
    _measured(conn, 1, 0.5, 0.5)

    summary = {band["key"]: band["tracks"] for band in emotion.band_summary(conn)}
    assert set(summary) == {"bright", "warm", "low", "intense"}
    assert sum(summary.values()) == 1


def test_band_summary_counts_and_runtime_agree_with_the_map(conn):
    for i, (valence, arousal) in enumerate(
        [(-1, 1), (-0.9, 0.9), (1, -1), (0.9, -0.9)], start=1
    ):
        _measured(conn, i, valence, arousal, duration_ms=60_000)

    summary = {b["key"]: b for b in emotion.band_summary(conn)}
    points = emotion.map_points(conn)
    for key, band in summary.items():
        expected = [p for p in points if p["band"] == key]
        assert band["tracks"] == len(expected)
        assert band["runtime_ms"] == 60_000 * len(expected)


def test_a_mix_only_contains_its_own_band(conn):
    for i, (valence, arousal) in enumerate(
        [(-1, 1), (-0.9, 0.95), (1, -1), (0.95, -0.9)], start=1
    ):
        _measured(conn, i, valence, arousal)

    bands = {p["track_rowid"]: p["band"] for p in emotion.map_points(conn)}
    for band in ("bright", "warm", "low", "intense"):
        mix = emotion.band_tracks(conn, band, limit=10)
        assert all(bands[t["track_rowid"]] == band for t in mix)


def test_the_dashboard_uses_the_mirror_when_nothing_is_measured_here(conn):
    """Degrade in quality, never in availability: with no emotion vectors the
    plane falls back to the Spotify features rather than going blank."""
    insert_track(conn, 1)

    board = insights.dashboard(conn)
    assert board["stats"]["map_source"] == "features"
    assert board["stats"]["map_centre"] is None


def test_the_dashboard_prefers_our_own_vectors_when_they_exist(conn):
    _measured(conn, 1, 0.2, -0.3)

    board = insights.dashboard(conn)
    assert board["stats"]["map_source"] == "emotion"
    assert board["stats"]["map_centre"]["measured"] == 1
    assert board["stats"]["map"][0]["arousal"] == -0.3
