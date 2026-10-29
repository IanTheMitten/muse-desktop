"""Freezing the normalization bounds writes 13 rows in one statement. If that
statement fails partway through SQLite's own execution, the table must hold
none of them -- never a partial set that a later call mistakes for "already
frozen" and never recomputes. (A prior version wrote one row per statement in
a BEGIN/COMMIT bracket; that both left a real window for a partial write on
interruption and could collide with another writer's own BEGIN/COMMIT
bracket on the same shared connection -- see `frozen_bounds`'s comment.)
"""

import pytest

from muse.library import features
from tests.conftest import insert_track


def _stock(conn, rowid, tempo):
    insert_track(conn, rowid, duration_ms=180_000)
    conn.execute(
        "INSERT INTO track_features (track_rowid, danceability, energy,"
        " loudness, speechiness, acousticness, instrumentalness, liveness,"
        " valence, tempo, key, mode, time_signature, checked_at)"
        " VALUES (?, 0.5,0.5,-8.0,0.05,0.2,0.0,0.1,0.5,?,0,1,4, 0)",
        (rowid, tempo),
    )


def test_an_interrupted_freeze_leaves_no_partial_bounds(conn, monkeypatch):
    _stock(conn, 1, tempo=90.0)
    _stock(conn, 2, tempo=150.0)

    real_execute = type(conn).execute

    def flaky_execute(self, sql, parameters=()):
        if "INSERT INTO feature_norm_bounds" in sql:
            raise RuntimeError("simulated failure mid-freeze")
        return real_execute(self, sql, parameters)

    monkeypatch.setattr(type(conn), "execute", flaky_execute)

    with pytest.raises(RuntimeError):
        features.frozen_bounds(conn)

    monkeypatch.setattr(type(conn), "execute", real_execute)
    rows = conn.execute("SELECT COUNT(*) AS n FROM feature_norm_bounds").fetchone()
    assert rows["n"] == 0
