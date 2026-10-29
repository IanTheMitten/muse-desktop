"""Thumbs up and thumbs down.

A button that does not respond is a loud failure. What is tested here is the
quiet kind, and it matters more than usual because this is the *only* explicit
signal slice 2 gets: a rating that lands on the wrong track, or a value that is
neither +1 nor -1, is indistinguishable from a deliberate answer six months
from now when it is being trained on.
"""

import pytest
from conftest import FakeCatalog, make_manifest, make_record

from muse.library import get_rating, import_manifest, set_rating
from muse.library.queries import album_tracks, health, track, tracks_by_rowid
from muse.library.ratings import counts


def load(conn, *rowids):
    import_manifest(
        make_manifest(*[make_record(rowid) for rowid in rowids]),
        FakeCatalog(),
        conn,
    )
    return conn


def test_a_rating_is_recorded_and_read_back(conn):
    load(conn, 1)
    assert set_rating(conn, 1, 1) == 1
    assert get_rating(conn, 1) == 1


def test_an_unrated_track_has_no_rating(conn):
    load(conn, 1)
    assert get_rating(conn, 1) is None


def test_changing_your_mind_replaces_rather_than_duplicates(conn):
    """The table is keyed by track, so a second insert must update. A duplicate
    row would be invisible here and ambiguous to whatever trains on it."""
    load(conn, 1)
    set_rating(conn, 1, 1)
    set_rating(conn, 1, -1)

    assert get_rating(conn, 1) == -1
    assert conn.execute("SELECT COUNT(*) FROM ratings").fetchone()[0] == 1


def test_a_rating_can_be_taken_back(conn):
    load(conn, 1)
    set_rating(conn, 1, -1)

    assert set_rating(conn, 1, None) is None
    assert get_rating(conn, 1) is None
    assert conn.execute("SELECT COUNT(*) FROM ratings").fetchone()[0] == 0


def test_the_timestamp_moves_with_the_current_answer(conn):
    """One rating per track, stamped with when it was given — so a mind changed
    today must not still be dated to last year."""
    load(conn, 1)
    set_rating(conn, 1, 1, now=1000)
    set_rating(conn, 1, -1, now=2000)

    stamp = conn.execute(
        "SELECT created_at FROM ratings WHERE track_rowid = 1"
    ).fetchone()[0]
    assert stamp == 2000


@pytest.mark.parametrize("bad", [0, 5, -2, 2])
def test_a_value_that_is_not_a_thumb_is_refused(conn, bad):
    """Silent, permanent, and impossible to tell from a real answer later."""
    load(conn, 1)
    with pytest.raises(ValueError):
        set_rating(conn, 1, bad)
    assert get_rating(conn, 1) is None


def test_rating_a_track_that_does_not_exist_fails(conn):
    """`ratings` has a foreign key for exactly this: a rating pointing at
    nothing is a row slice 2 can never join and never notices dropping."""
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        set_rating(conn, 999_999, 1)


def test_ratings_do_not_leak_between_tracks(conn):
    load(conn, 1, 2)
    set_rating(conn, 1, 1)

    assert get_rating(conn, 2) is None


# --- what the client sees -------------------------------------------------


def test_a_rating_rides_along_with_the_track(conn):
    """Visible everywhere a track is, without a second request."""
    load(conn, 1)
    set_rating(conn, 1, -1)

    assert track(conn, 1)["rating"] == -1


def test_an_unrated_track_reports_null_rather_than_omitting_the_field(conn):
    load(conn, 1)
    assert track(conn, 1)["rating"] is None


def test_the_rating_reaches_the_hydrated_queue(conn):
    """This is what makes a rating given on the phone show up on the laptop:
    the queue is stored as ids and hydrated server-side on the other device."""
    load(conn, 1, 2)
    set_rating(conn, 2, 1)

    queue = tracks_by_rowid(conn, [1, 2])
    assert [entry["rating"] for entry in queue] == [None, 1]


def test_album_listings_carry_ratings_too(conn):
    load(conn, 1, 2)
    set_rating(conn, 1, 1)

    assert {t["track_rowid"]: t["rating"] for t in album_tracks(conn, 20)} == {
        1: 1,
        2: None,
    }


def test_health_counts_both_thumbs(conn):
    """Nothing in slice 1 consumes ratings, so this is the only way to see from
    outside the database that the button records anything at all."""
    load(conn, 1, 2)
    set_rating(conn, 1, 1)
    set_rating(conn, 2, -1)

    assert counts(conn) == {"up": 1, "down": 1}
    assert health(conn)["ratings"] == {"up": 1, "down": 1}
