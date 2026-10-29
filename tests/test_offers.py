"""`track_offers` and its accessor module.

An *offer* is the recommender returning a track as a candidate. It is not a
play, and O2's freshness decay needs it: a track pushed three times and
declined every time should stop being pushed. `plays` cannot hold this --
ADR 0001 keeps that table to physical fact, and "we suggested it" is not one.
"""

import sqlite3

from muse.library import offers, schema
from tests.conftest import insert_track


def test_track_offers_is_migration_ten():
    """Index + 1 is the version a migration produces, and a shipped migration
    never moves. Pinned by index rather than by `LATEST_VERSION`: that grows
    every time the ladder does, and this test is about where the offer-count
    table sits, not about how long the ladder has become."""
    assert schema.MIGRATIONS[9] is schema._m010_offer_counts


def test_migration_creates_track_offers(conn):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='track_offers'"
    ).fetchone()
    assert row is not None


def test_first_offer_records_one(conn):
    insert_track(conn, 1)
    offers.record_offers(conn, [1], now=1000)
    assert offers.counts(conn) == {1: 1}


def test_second_offer_increments(conn):
    insert_track(conn, 1)
    offers.record_offers(conn, [1], now=1000)
    offers.record_offers(conn, [1], now=2000)
    assert offers.counts(conn) == {1: 2}


def test_last_offered_advances(conn):
    insert_track(conn, 1)
    offers.record_offers(conn, [1], now=1000)
    offers.record_offers(conn, [1], now=2000)
    row = conn.execute(
        "SELECT last_offered FROM track_offers WHERE track_rowid = 1"
    ).fetchone()
    assert row["last_offered"] == 2000


def test_never_offered_track_is_absent_not_zero(conn):
    """Absent, not 0. A caller distinguishing "never offered" from "offered
    zero times" would be drawing a distinction that does not exist, so only
    one of the two representations is allowed to appear."""
    insert_track(conn, 1)
    insert_track(conn, 2)
    offers.record_offers(conn, [1], now=1000)
    assert offers.counts(conn) == {1: 1}


def test_records_many_in_one_call(conn):
    for rowid in (1, 2, 3):
        insert_track(conn, rowid)
    offers.record_offers(conn, [1, 2, 3], now=1000)
    assert offers.counts(conn) == {1: 1, 2: 1, 3: 1}


def test_empty_offer_list_is_a_noop(conn):
    offers.record_offers(conn, [], now=1000)
    assert offers.counts(conn) == {}


def test_table_is_safe_to_clear(conn):
    """The spec promises this table can be reset -- it holds a derived
    convenience count, not physical fact, so clearing it costs a few sessions
    of ranking quality and nothing that cannot be rebuilt by using the app.
    Asserted rather than claimed: after a wipe the module still works and
    every track reads as never-offered."""
    insert_track(conn, 1)
    offers.record_offers(conn, [1], now=1000)
    conn.execute("DELETE FROM track_offers")
    assert offers.counts(conn) == {}
    offers.record_offers(conn, [1], now=3000)
    assert offers.counts(conn) == {1: 1}


def test_offer_for_unknown_track_is_rejected(conn):
    """`track_rowid` is a real foreign key, so a count can never outlive the
    track it counts."""
    try:
        offers.record_offers(conn, [999], now=1000)
    except sqlite3.IntegrityError:
        return
    raise AssertionError("expected a foreign-key violation")
