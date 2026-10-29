"""`library.content` against a real migrated temp-file database.

`conn` is tests/conftest.py's real migrated SQLite fixture and
`insert_track` its helper — the same pair every other table test uses.
`insert_track` writes `file_path = "MUSIC/<rowid>.m4a"` for a `done` track
and NULL for any other status, which is exactly the distinction `pending`
turns on.
"""

from muse.embed import pack_vector, unpack_vector
from muse.library import content as library_content
from tests.conftest import insert_track


def test_pending_returns_playable_tracks_with_no_vector(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    assert [row["track_rowid"] for row in library_content.pending(conn)] == [1, 2]
    assert library_content.pending(conn)[0]["file_path"] == "MUSIC/1.m4a"


def test_pending_excludes_a_track_that_already_has_a_vector(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    library_content.upsert(conn, 1, content_vec=pack_vector([0.5]), model="m", now=10)
    assert [row["track_rowid"] for row in library_content.pending(conn)] == [2]


def test_pending_excludes_tracks_that_are_not_playable(conn):
    insert_track(conn, 5, status="error")
    assert library_content.pending(conn) == []


def test_pending_includes_a_row_with_a_null_vector(conn):
    """A row can exist with `content_vec` still NULL -- e.g. a caller that
    recorded a failed embed rather than leaving no row at all. It must stay
    in the queue: `pending`'s predicate is "no row *or* a NULL vector," and
    the batch itself never writes this state, so nothing else exercises it."""
    insert_track(conn, 1)
    library_content.upsert(conn, 1, content_vec=None, model=None, now=10)
    assert [row["track_rowid"] for row in library_content.pending(conn)] == [1]


def test_upsert_replaces_an_existing_vector(conn):
    insert_track(conn, 1)
    library_content.upsert(conn, 1, content_vec=pack_vector([0.5]), model="a", now=10)
    # 0.25, not 0.9: both round-trip through float32, but only a power-of-two
    # fraction is guaranteed to compare equal to its own literal afterward.
    library_content.upsert(conn, 1, content_vec=pack_vector([0.25]), model="b", now=20)
    row = conn.execute(
        "SELECT * FROM content_embeddings WHERE track_rowid = 1"
    ).fetchone()
    assert unpack_vector(row["content_vec"]) == [0.25]
    assert row["model"] == "b"
    assert row["embedded_at"] == 20


def test_vectors_returns_every_stored_vector_unpacked(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    library_content.upsert(conn, 1, content_vec=pack_vector([1.0, 0.0]), model="m", now=1)
    library_content.upsert(conn, 2, content_vec=pack_vector([0.0, 1.0]), model="m", now=1)
    assert library_content.vectors(conn) == {1: [1.0, 0.0], 2: [0.0, 1.0]}


def test_replace_stats_overwrites_the_single_row(conn):
    library_content.replace_stats(
        conn, mean_vec=pack_vector([0.1]), sim_lo=-0.2, sim_hi=0.8,
        track_count=3, now=100,
    )
    # 0.25, not 0.2: it round-trips exactly through pack_vector's float32
    # storage, whereas 0.2 does not compare equal to its own literal after.
    library_content.replace_stats(
        conn, mean_vec=pack_vector([0.25]), sim_lo=-0.3, sim_hi=0.9,
        track_count=4, now=200,
    )
    assert conn.execute("SELECT COUNT(*) FROM content_stats").fetchone()[0] == 1
    stats = library_content.stats(conn)
    assert stats["mean_vec"] == [0.25]
    assert stats["sim_lo"] == -0.3
    assert stats["track_count"] == 4
    assert stats["computed_at"] == 200


def test_stats_is_none_before_any_batch_has_run(conn):
    assert library_content.stats(conn) is None


def test_coverage_counts_embedded_against_playable(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    library_content.upsert(conn, 1, content_vec=pack_vector([0.5]), model="m", now=1)
    assert library_content.coverage(conn) == {
        "playable": 2, "embedded": 1, "track_count": None, "computed_at": None,
    }


def test_coverage_ignores_a_stale_row_for_a_track_no_longer_playable(conn):
    """A track that becomes unplayable after being embedded keeps its old
    content_embeddings row (nothing here ever deletes one), but coverage
    joins against the *current* playable set so a stale row never inflates
    the numerator."""
    insert_track(conn, 1, status="error")
    library_content.upsert(conn, 1, content_vec=pack_vector([0.5]), model="m", now=100)
    assert library_content.coverage(conn) == {
        "playable": 0, "embedded": 0, "track_count": None, "computed_at": None,
    }


def test_coverage_reports_the_stats_row_when_one_exists(conn):
    insert_track(conn, 1)
    library_content.upsert(conn, 1, content_vec=pack_vector([0.5]), model="m", now=1)
    library_content.replace_stats(
        conn, mean_vec=pack_vector([0.5]), sim_lo=0.0, sim_hi=1.0,
        track_count=1, now=42,
    )
    assert library_content.coverage(conn) == {
        "playable": 1, "embedded": 1, "track_count": 1, "computed_at": 42,
    }
