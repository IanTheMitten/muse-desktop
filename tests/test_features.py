"""Audio features: materialising the mirror, and the mood bands built on it.

The seam worth testing is the copy: the 29 GB mirror is asked once per track,
ever, and a track it has nothing for must be remembered as asked rather than
re-asked at every startup — the same decision-cache property the art backfill
has.
"""

from muse.library import features
from tests.conftest import FakeFeatures, insert_track, make_features


def test_band_of_agrees_with_the_sql_it_ships_with(conn):
    """Two expressions of one rule. A grid, so drift shows up as a failure
    rather than as a dashboard whose proportions do not match its mixes."""
    grid = [(v / 10, e / 10) for v in range(11) for e in range(11)]
    for rowid, (valence, energy) in enumerate(grid, start=1):
        insert_track(conn, rowid)
        conn.execute(
            "INSERT INTO track_features (track_rowid, valence, energy,"
            " checked_at) VALUES (?,?,?,0)",
            (rowid, valence, energy),
        )

    for band in features.BANDS:
        in_sql = {
            row[0]
            for row in conn.execute(
                f"SELECT track_rowid FROM track_features WHERE {features.BAND_SQL[band]}"
            )
        }
        in_python = {
            rowid
            for rowid, (valence, energy) in enumerate(grid, start=1)
            if features.band_of(valence, energy) == band
        }
        assert in_sql == in_python, band


def test_sync_stores_what_the_mirror_knows(conn):
    insert_track(conn, 1, track_id="aaa")
    lookup = FakeFeatures({"aaa": make_features("aaa", valence=0.8, energy=0.7)})

    result = features.sync_features(conn, lookup)

    assert result.asked == 1
    assert result.stored == 1
    assert result.without == 0
    row = conn.execute("SELECT * FROM track_features WHERE track_rowid = 1").fetchone()
    assert row["valence"] == 0.8
    assert row["energy"] == 0.7


def test_sync_stores_the_recommender_only_columns_too(conn):
    """`speechiness`, `liveness`, `key`, `time_signature` feed the embed
    batch's 13-dim feature_vec (ticket 01) and nothing in slice 1 reads them,
    but sync must not silently drop what the mirror sent."""
    insert_track(conn, 1, track_id="aaa")
    lookup = FakeFeatures(
        {"aaa": make_features(
            "aaa", speechiness=0.15, liveness=0.3, key=7, time_signature=4,
        )}
    )

    features.sync_features(conn, lookup)

    row = conn.execute("SELECT * FROM track_features WHERE track_rowid = 1").fetchone()
    assert row["speechiness"] == 0.15
    assert row["liveness"] == 0.3
    assert row["key"] == 7
    assert row["time_signature"] == 4


def test_a_track_the_mirror_lacks_is_remembered_as_asked(conn):
    insert_track(conn, 1, track_id="aaa")
    lookup = FakeFeatures({})

    first = features.sync_features(conn, lookup)
    second = features.sync_features(conn, lookup)

    assert first.without == 1
    # The row exists, with nothing in it: "asked, and there is none".
    row = conn.execute("SELECT * FROM track_features WHERE track_rowid = 1").fetchone()
    assert row is not None
    assert row["valence"] is None
    # And the second run asked about nothing at all.
    assert second.asked == 0
    assert lookup.asked_for == ["aaa"]


def test_sync_skips_tracks_it_already_has(conn):
    insert_track(conn, 1, track_id="aaa")
    insert_track(conn, 2, track_id="bbb")
    lookup = FakeFeatures(
        {"aaa": make_features("aaa"), "bbb": make_features("bbb")}
    )

    features.sync_features(conn, lookup)
    insert_track(conn, 3, track_id="ccc")
    lookup.rows["ccc"] = make_features("ccc")
    again = features.sync_features(conn, lookup)

    assert again.asked == 1
    assert lookup.asked_for == ["aaa", "bbb", "ccc"]


def test_a_track_with_no_spotify_id_is_never_asked_about(conn):
    """Post-freeze tracks carry no `track_id`. There is nothing to look up, and
    inventing a row for them would claim the mirror had been consulted."""
    insert_track(conn, 1, track_id=None)

    result = features.sync_features(conn, FakeFeatures({}))

    assert result.asked == 0
    assert conn.execute("SELECT COUNT(*) FROM track_features").fetchone()[0] == 0


def _stock(conn, rowid, valence, energy, **kwargs):
    insert_track(conn, rowid, **kwargs)
    conn.execute(
        "INSERT INTO track_features (track_rowid, valence, energy,"
        " danceability, acousticness, tempo, checked_at)"
        " VALUES (?,?,?,0.5,0.2,120,0)",
        (rowid, valence, energy),
    )


def test_band_summary_counts_only_playable_tracks_that_have_features(conn):
    _stock(conn, 1, 0.8, 0.8)                       # bright
    _stock(conn, 2, 0.8, 0.2)                       # warm
    _stock(conn, 3, 0.9, 0.9, status="missing_file")  # bright, but gone
    insert_track(conn, 4)                            # playable, no features

    summary = {band["key"]: band for band in features.band_summary(conn)}

    assert [band["key"] for band in features.band_summary(conn)] == list(features.BANDS)
    assert summary["bright"]["tracks"] == 1
    assert summary["warm"]["tracks"] == 1
    assert summary["low"]["tracks"] == 0
    assert summary["bright"]["runtime_ms"] == 180_000


def test_band_tracks_returns_that_band_only_and_never_a_missing_file(conn):
    _stock(conn, 1, 0.9, 0.9)
    _stock(conn, 2, 0.1, 0.1)
    _stock(conn, 3, 0.95, 0.95, status="missing_file")

    tracks = features.band_tracks(conn, "bright")

    assert [track["track_rowid"] for track in tracks] == [1]
    assert tracks[0]["playable"] is True


def test_band_tracks_rejects_an_unknown_band(conn):
    assert features.band_tracks(conn, "nonsense") == []


def test_neighbours_are_ordered_by_distance_and_exclude_the_seed(conn):
    _stock(conn, 1, 0.50, 0.50)                       # the seed
    _stock(conn, 2, 0.52, 0.51, album_rowid=21)       # nearest
    _stock(conn, 3, 0.70, 0.70, album_rowid=22)       # further
    _stock(conn, 4, 0.05, 0.95, album_rowid=23)       # furthest

    found = [track["track_rowid"] for track in features.neighbours(conn, 1, limit=3)]

    assert found == [2, 3, 4]


def test_neighbours_take_one_track_per_album(conn):
    """Ten tracks off the album already playing is not a recommendation."""
    _stock(conn, 1, 0.50, 0.50, album_rowid=20)
    _stock(conn, 2, 0.51, 0.50, album_rowid=21)
    _stock(conn, 3, 0.52, 0.50, album_rowid=21)
    _stock(conn, 4, 0.60, 0.50, album_rowid=22)

    found = [track["track_rowid"] for track in features.neighbours(conn, 1, limit=3)]

    assert found == [2, 4]


def test_neighbours_of_a_track_with_no_features_are_empty(conn):
    insert_track(conn, 1)
    _stock(conn, 2, 0.5, 0.5, album_rowid=21)

    assert features.neighbours(conn, 1) == []


def test_map_points_carry_their_band(conn):
    _stock(conn, 1, 0.9, 0.9)
    insert_track(conn, 2)  # no features: not a point

    points = features.map_points(conn)

    assert points == [
        {"track_rowid": 1, "valence": 0.9, "energy": 0.9, "band": "bright"}
    ]


def test_columns_added_by_a_later_migration_are_backfilled(conn):
    """`sync_features` skips any track that already has a `track_features`
    row, which is what makes it cheap to run at every startup. It also means
    a row written before migration 005 added speechiness, liveness, key, and
    time_signature keeps those four NULL forever.

    That is the real state of this library: 775 rows sharing one `checked_at`,
    754 carrying valence, none carrying the four later columns -- while the
    mirror has all four populated across 178 million rows. It failed the
    embed batch outright until `measured_bounds` was taught to check every
    aggregate, and it keeps the feature leg null until these are filled.

    The re-ask predicate is precise: an old column present with all four new
    ones absent cannot describe a track the mirror has nothing for, because
    the mirror stores every field together or none of them."""
    insert_track(conn, 1, track_id="t1")
    conn.execute(
        "INSERT INTO track_features (track_rowid, valence, energy,"
        " danceability, acousticness, instrumentalness, tempo, loudness,"
        " mode, checked_at) VALUES (1, 0.5, 0.6, 0.7, 0.1, 0.0, 120.0,"
        " -6.0, 1, 100)"
    )
    lookup = FakeFeatures({"t1": make_features("t1")})

    features.sync_features(conn, lookup)

    row = conn.execute(
        "SELECT speechiness, liveness, key, time_signature FROM"
        " track_features WHERE track_rowid = 1"
    ).fetchone()
    assert row["speechiness"] is not None
    assert row["time_signature"] is not None


def test_a_track_the_mirror_has_nothing_for_is_not_re_asked_forever(conn):
    """The counter-case that keeps the predicate from re-asking about the
    same unanswerable tracks at every startup: an all-NULL row is a real
    answer -- asked, and there is nothing -- and must stay skipped."""
    insert_track(conn, 1, track_id="t1")
    conn.execute(
        "INSERT INTO track_features (track_rowid, checked_at) VALUES (1, 100)"
    )
    assert features.sync_features(conn, FakeFeatures({})).asked == 0


def test_a_fully_synced_row_is_not_re_asked(conn):
    """The ordinary idempotence this function is built on."""
    insert_track(conn, 1, track_id="t1")
    features.sync_features(conn, FakeFeatures({"t1": make_features("t1")}))
    assert features.sync_features(conn, FakeFeatures({"t1": make_features("t1")})).asked == 0
