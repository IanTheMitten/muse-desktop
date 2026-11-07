"""The `embeddings` table: the embed batch's work queue and coverage count.

`embeddings.audio_vec IS NULL` is the resumable queue (ADR 0003 / ticket 01):
a killed batch restarts into whatever this still returns rather than from the
top. A second, separate queue catches a lyrics file edited after its track was
already embedded -- caught by comparing a freshly computed hash against the
stored `lyrics_hash`, not by re-scanning everything.
"""

import time

from muse.library import embeddings
from tests.conftest import insert_track


def test_pending_lists_every_done_track_with_no_embeddings_row(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)

    pending = {row["track_rowid"] for row in embeddings.pending(conn)}

    assert pending == {1, 2}


def test_pending_excludes_a_fully_embedded_track(conn):
    insert_track(conn, 1)
    embeddings.upsert(
        conn, 1, audio_vec=b"x" * 4, feature_vec=None,
        audio_model="stub", lyrics_hash=None,
    )
    insert_track(conn, 2)

    pending = {row["track_rowid"] for row in embeddings.pending(conn)}

    assert pending == {2}


def test_pending_includes_a_row_left_by_a_killed_batch(conn):
    """A row can exist with every vector still NULL -- e.g. a run interrupted
    right after the row was created. It must stay in the queue."""
    insert_track(conn, 1)
    conn.execute(
        "INSERT INTO embeddings (track_rowid, indexed_at) VALUES (1, 0)"
    )

    pending = {row["track_rowid"] for row in embeddings.pending(conn)}

    assert pending == {1}


def test_pending_never_includes_an_unplayable_track(conn):
    insert_track(conn, 1, status="error")

    assert embeddings.pending(conn) == []


def test_upsert_writes_both_vectors_and_is_idempotent(conn):
    insert_track(conn, 1)

    embeddings.upsert(
        conn, 1, audio_vec=b"a" * 4, feature_vec=b"f" * 4,
        audio_model="clap", lyrics_hash="deadbeef",
        now=100,
    )
    row = conn.execute("SELECT * FROM embeddings WHERE track_rowid = 1").fetchone()

    assert row["audio_vec"] == b"a" * 4
    assert row["feature_vec"] == b"f" * 4
    assert row["audio_model"] == "clap"
    assert row["lyrics_hash"] == "deadbeef"
    assert row["indexed_at"] == 100

    # Re-running (e.g. the batch resuming) replaces cleanly rather than
    # erroring or duplicating.
    embeddings.upsert(
        conn, 1, audio_vec=b"a" * 4, feature_vec=None,
        audio_model="clap", lyrics_hash=None, now=200,
    )
    row = conn.execute("SELECT * FROM embeddings WHERE track_rowid = 1").fetchone()
    assert row["feature_vec"] is None
    assert row["indexed_at"] == 200


def test_update_lyrics_hash_leaves_the_vectors_untouched(conn):
    """A manual lyrics edit (story 56) refreshes only the fingerprint."""
    insert_track(conn, 1)
    embeddings.upsert(
        conn, 1, audio_vec=b"a" * 4, feature_vec=b"f" * 4,
        audio_model="clap", lyrics_hash="old-hash", now=100,
    )

    embeddings.update_lyrics_hash(conn, 1, lyrics_hash="new-hash", now=200)
    row = conn.execute("SELECT * FROM embeddings WHERE track_rowid = 1").fetchone()

    assert row["audio_vec"] == b"a" * 4
    assert row["feature_vec"] == b"f" * 4
    assert row["lyrics_hash"] == "new-hash"
    assert row["lyrics_vec"] is None
    assert row["indexed_at"] == 200


def test_coverage_counts_embedded_against_playable(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    insert_track(conn, 3, status="error")  # never counted as playable
    embeddings.upsert(
        conn, 1, audio_vec=b"a" * 4, feature_vec=None,
        audio_model="clap", lyrics_hash=None,
    )

    result = embeddings.coverage(conn)

    assert result == {"playable": 2, "embedded": 1}


def test_lyrics_hash_by_track_reads_the_stored_hash_for_already_embedded_tracks(conn):
    insert_track(conn, 1)
    embeddings.upsert(
        conn, 1, audio_vec=b"a" * 4, feature_vec=None,
        audio_model="clap", lyrics_hash="abc123",
    )
    insert_track(conn, 2)  # never embedded: not in the result

    hashes = embeddings.lyrics_hash_by_track(conn)

    assert hashes == {1: "abc123"}


def test_bounds_are_not_frozen_while_a_column_has_no_data(conn):
    """`frozen_bounds` documents itself as empty "until the first track
    anywhere has a full 13-value reading", but `measured_bounds` only checked
    whether the *first* aggregate was NULL. On the real library four columns
    -- speechiness, liveness, key, time_signature -- are NULL for all 775
    rows while valence is populated, so the guard passed, and the insert of
    NULL min/max hit the NOT NULL constraint and failed the entire embed
    batch at zero tracks.

    Incomplete bounds must stay unfrozen. Freezing a partial set would be
    worse than crashing: `normalize_feature_vector` needs every column, so
    the feature leg would be permanently null and the missing columns could
    never be captured once their data arrived."""
    from muse.library import features as library_features

    insert_track(conn, 1)
    conn.execute(
        "INSERT INTO track_features (track_rowid, valence, energy,"
        " danceability, acousticness, instrumentalness, tempo, loudness,"
        " mode, checked_at, speechiness, liveness, key, time_signature)"
        " VALUES (1, 0.5, 0.5, 0.5, 0.5, 0.5, 120.0, -6.0, 1, 0,"
        "         NULL, NULL, NULL, NULL)"
    )
    assert library_features.measured_bounds(conn) == {}
    assert library_features.frozen_bounds(conn) == {}
    assert conn.execute(
        "SELECT COUNT(*) FROM feature_norm_bounds"
    ).fetchone()[0] == 0
