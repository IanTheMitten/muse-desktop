"""The `taste_embeddings` table: the nightly retrain's wholesale snapshot
and the health endpoint's coverage count.

`replace_all` writes every given row in one `INSERT ... ON CONFLICT DO
UPDATE` statement, not a `BEGIN`/`DELETE`/`INSERT`/`COMMIT` bracket --
`muse.library.features.frozen_bounds` already discovered that a multi-call
bracket on `SharedConnection` (which locks one `execute()` call at a time,
not a sequence of them) can collide with another writer's own bracket on
the same shared connection. A single statement is atomic under that
per-call lock and under SQLite itself, so a bad retrain either overwrites
every given row or, on any failure, none of them.
"""

import sqlite3

import pytest

from muse.library import taste
from tests.conftest import insert_track


def test_replace_all_writes_every_row(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)

    taste.replace_all(
        conn,
        [(1, b"v" * 4, "stub-als"), (2, None, None)],
        now=1000,
    )

    rows = {
        row["track_rowid"]: dict(row)
        for row in conn.execute("SELECT * FROM taste_embeddings")
    }
    assert rows[1]["taste_vec"] == b"v" * 4
    assert rows[1]["model"] == "stub-als"
    assert rows[1]["trained_at"] == 1000
    assert rows[2]["taste_vec"] is None
    assert rows[2]["model"] is None


def test_replace_all_overwrites_a_previous_snapshot(conn):
    insert_track(conn, 1)
    taste.replace_all(conn, [(1, b"old" * 4, "stub-als")], now=100)

    taste.replace_all(conn, [(1, b"new" * 4, "implicit-als")], now=200)

    row = conn.execute(
        "SELECT * FROM taste_embeddings WHERE track_rowid = 1"
    ).fetchone()
    assert row["taste_vec"] == b"new" * 4
    assert row["model"] == "implicit-als"
    assert row["trained_at"] == 200


def test_replace_all_does_nothing_for_an_empty_snapshot(conn):
    insert_track(conn, 1)
    taste.replace_all(conn, [(1, b"v" * 4, "stub-als")], now=100)

    taste.replace_all(conn, [], now=200)

    # An empty `rows` means "nothing new to write", not "wipe everything" --
    # the caller (`muse.taste.run_retrain`) always passes every playable
    # track, so an empty list only ever means an empty library.
    row = conn.execute(
        "SELECT * FROM taste_embeddings WHERE track_rowid = 1"
    ).fetchone()
    assert row["taste_vec"] == b"v" * 4


def test_a_failing_replace_leaves_the_previous_snapshot_untouched(conn):
    insert_track(conn, 1)
    taste.replace_all(conn, [(1, b"old" * 4, "stub-als")], now=100)

    # track_rowid 999 does not exist -- the foreign key on
    # taste_embeddings.track_rowid rejects the whole statement, not just
    # that one row, because it is one multi-row INSERT.
    with pytest.raises(sqlite3.IntegrityError):
        taste.replace_all(
            conn, [(1, b"new" * 4, "stub-als"), (999, b"x" * 4, "stub-als")],
            now=200,
        )

    row = conn.execute(
        "SELECT * FROM taste_embeddings WHERE track_rowid = 1"
    ).fetchone()
    assert row["taste_vec"] == b"old" * 4
    assert row["trained_at"] == 100


def test_coverage_counts_non_null_taste_vec_against_playable(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    insert_track(conn, 3, status="error")  # never counted as playable
    taste.replace_all(
        conn, [(1, b"v" * 4, "stub-als"), (2, None, None)], now=100
    )

    assert taste.coverage(conn) == {"playable": 2, "embedded": 1}


def test_coverage_ignores_a_stale_row_for_a_track_no_longer_playable(conn):
    """A track that becomes unplayable after being trained on keeps its old
    taste_embeddings row (replace_all only ever touches rows it is given),
    but coverage joins against the *current* playable set so a stale row
    never inflates the numerator."""
    insert_track(conn, 1, status="error")
    taste.replace_all(conn, [(1, b"v" * 4, "stub-als")], now=100)

    assert taste.coverage(conn) == {"playable": 0, "embedded": 0}
