"""The dashboard payload.

Written against the state this library is actually in: eighteen plays and no
ratings. The cold start is the default case here, not an edge case, so most of
these tests are about what the surface says when it does not yet know anything
about the listener.
"""

import time

from muse.library import insights, state
from tests.conftest import insert_track

DAY_MS = 86_400_000


def stock(conn, rowid, valence=0.5, energy=0.5, **kwargs):
    insert_track(conn, rowid, **kwargs)
    conn.execute(
        "INSERT INTO track_features (track_rowid, valence, energy,"
        " danceability, acousticness, tempo, checked_at)"
        " VALUES (?,?,?,0.5,0.2,120,0)",
        (rowid, valence, energy),
    )
    return rowid


def play(conn, track_rowid, started_at, reached_ms=120_000, duration_ms=180_000):
    conn.execute(
        "INSERT INTO plays (track_rowid, started_at, ended_at, reached_ms,"
        " duration_ms, ended_reason, source, heartbeat_at)"
        " VALUES (?,?,?,?,?, 'track_end', 'queue', ?)",
        (track_rowid, started_at, started_at + reached_ms, reached_ms,
         duration_ms, started_at + reached_ms),
    )


def test_a_library_nobody_has_played_yet(conn):
    stock(conn, 1, 0.8, 0.8)

    board = insights.dashboard(conn)

    assert board["resume"] is None
    assert board["sounds_like"]["seed"] is None
    assert board["sounds_like"]["tracks"] == []
    assert board["stats"]["top_artists"] is None
    assert [day["minutes"] for day in board["stats"]["week"]] == [0] * 7
    assert len(board["mixes"]) == 4


def test_mixes_carry_their_share_of_the_measured_library(conn):
    stock(conn, 1, 0.9, 0.9)
    stock(conn, 2, 0.9, 0.9)
    stock(conn, 3, 0.1, 0.1)
    insert_track(conn, 4)  # unmeasured: not part of any share

    shares = {mix["key"]: mix["share"] for mix in insights.dashboard(conn)["mixes"]}

    # Two of the three *measured* tracks, not two of the four rows: the
    # unmeasured track is outside what the bands can speak for.
    assert shares["bright"] == 0.6667
    assert shares["low"] == 0.3333
    assert shares["warm"] == 0
    assert round(sum(shares.values())) == 1


def test_resume_is_whatever_the_queue_is_pointing_at(conn):
    stock(conn, 1)
    stock(conn, 2, album_rowid=21)
    state.write_state(conn, [1, 2], queue_index=1, position_ms=42_000,
                      client_state="playing")

    resume = insights.dashboard(conn)["resume"]

    assert resume["track"]["track_rowid"] == 2
    assert resume["position_ms"] == 42_000


def test_a_queue_pointing_past_its_end_does_not_resume(conn):
    """A queue whose tracks were deleted from the library still has a length."""
    stock(conn, 1)
    state.write_state(conn, [1], queue_index=5, position_ms=0,
                      client_state="playing")

    assert insights.dashboard(conn)["resume"] is None


def test_sounds_like_is_seeded_by_the_last_thing_played(conn):
    now = int(time.time() * 1000)
    stock(conn, 1, 0.50, 0.50)
    stock(conn, 2, 0.52, 0.51, album_rowid=21)
    stock(conn, 3, 0.05, 0.95, album_rowid=22)
    play(conn, 3, now - 2 * DAY_MS)
    play(conn, 1, now - DAY_MS)

    sounds_like = insights.dashboard(conn, now_ms=now)["sounds_like"]

    assert sounds_like["seed"]["track_rowid"] == 1
    assert [t["track_rowid"] for t in sounds_like["tracks"]] == [2, 3]


def test_an_unmeasured_last_play_falls_back_to_one_that_was_measured(conn):
    """18 tracks in this library have no features. Landing on one of them is
    not a reason to show nothing."""
    now = int(time.time() * 1000)
    stock(conn, 1, 0.5, 0.5)
    stock(conn, 2, 0.5, 0.5, album_rowid=21)
    insert_track(conn, 3, album_rowid=22)  # measured by nobody
    play(conn, 1, now - DAY_MS)
    play(conn, 3, now - 1000)

    seed = insights.dashboard(conn, now_ms=now)["sounds_like"]["seed"]

    assert seed["track_rowid"] == 1


def test_the_week_buckets_the_last_seven_local_days(conn):
    now = int(time.time() * 1000)
    stock(conn, 1)
    play(conn, 1, now - 2 * DAY_MS, reached_ms=600_000, duration_ms=600_000)
    play(conn, 1, now - 30 * DAY_MS, reached_ms=600_000, duration_ms=600_000)

    week = insights.dashboard(conn, now_ms=now)["stats"]["week"]

    assert len(week) == 7
    assert week[-1]["minutes"] == 0          # today
    assert week[4]["minutes"] == 10          # two days ago
    assert sum(day["minutes"] for day in week) == 10  # the old play is outside


def test_a_play_counts_only_the_part_that_was_heard(conn):
    """`reached_ms` can exceed the track when a listener scrubs backwards and
    forwards; minutes listened is not the same as time on the clock."""
    now = int(time.time() * 1000)
    stock(conn, 1)
    play(conn, 1, now - DAY_MS, reached_ms=900_000, duration_ms=300_000)

    week = insights.dashboard(conn, now_ms=now)["stats"]["week"]

    assert week[5]["minutes"] == 5


def test_never_played_albums_exclude_anything_with_a_play(conn):
    now = int(time.time() * 1000)
    stock(conn, 1, album_rowid=20, album_name="Heard")
    stock(conn, 2, album_rowid=21, album_name="Unheard")
    play(conn, 1, now - DAY_MS)

    albums = insights.dashboard(conn, now_ms=now)["never_played"]

    assert [album["album_name"] for album in albums] == ["Unheard"]


def test_forgotten_is_oldest_first_and_only_looks_back_past_a_month(conn):
    now = int(time.time() * 1000)
    stock(conn, 1, album_rowid=20)
    stock(conn, 2, album_rowid=21)
    stock(conn, 3, album_rowid=22)
    play(conn, 1, now - 200 * DAY_MS)
    play(conn, 2, now - 60 * DAY_MS)
    play(conn, 3, now - 2 * DAY_MS)

    forgotten = insights.dashboard(conn, now_ms=now)["forgotten"]

    assert [track["track_rowid"] for track in forgotten] == [1, 2]


def test_top_artists_stay_hidden_until_there_is_history_worth_ranking(conn):
    now = int(time.time() * 1000)
    stock(conn, 1, artist_name="One")
    for index in range(insights.TOP_ARTISTS_MIN_PLAYS - 1):
        play(conn, 1, now - index * 3600_000)

    assert insights.dashboard(conn, now_ms=now)["stats"]["top_artists"] is None

    play(conn, 1, now - 1000)
    top = insights.dashboard(conn, now_ms=now)["stats"]["top_artists"]

    assert top[0]["artist_name"] == "One"
    assert top[0]["plays"] == insights.TOP_ARTISTS_MIN_PLAYS


def test_the_map_and_the_counts_agree_about_what_is_measured(conn):
    stock(conn, 1, 0.9, 0.9)
    insert_track(conn, 2)

    stats = insights.dashboard(conn)["stats"]

    assert stats["measured"] == {"playable": 2, "measured": 1}
    assert len(stats["map"]) == 1
