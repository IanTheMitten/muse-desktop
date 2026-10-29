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
        conn, 1, audio_vec=b"x" * 4, feature_vec=None, lyrics_vec=None,
        audio_model="stub", lyrics_model=None, lyrics_hash=None,
        audio_va=b"v" * 4, audio_va_model="stub-va",
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


def test_pending_includes_a_row_with_content_vectors_but_no_audio_va(conn):
    """A row can be content-embedded (ticket 01) with no emotion leg at all
    (ticket 02, or any caller of `upsert` that didn't pass the VA columns).
    That row must stay reachable through the main queue -- it is the only
    path that ever computes `audio_va`, since the lyrics-changed pass only
    fuses `emotion_vec` from an `audio_va` that already exists."""
    insert_track(conn, 1)
    embeddings.upsert(
        conn, 1, audio_vec=b"a" * 4, feature_vec=None, lyrics_vec=None,
        audio_model="clap", lyrics_model=None, lyrics_hash=None,
    )

    pending = {row["track_rowid"] for row in embeddings.pending(conn)}

    assert pending == {1}


def test_upsert_writes_all_three_vectors_and_is_idempotent(conn):
    insert_track(conn, 1)

    embeddings.upsert(
        conn, 1, audio_vec=b"a" * 4, feature_vec=b"f" * 4, lyrics_vec=b"l" * 4,
        audio_model="clap", lyrics_model="minilm", lyrics_hash="deadbeef",
        now=100,
    )
    row = conn.execute("SELECT * FROM embeddings WHERE track_rowid = 1").fetchone()

    assert row["audio_vec"] == b"a" * 4
    assert row["feature_vec"] == b"f" * 4
    assert row["lyrics_vec"] == b"l" * 4
    assert row["audio_model"] == "clap"
    assert row["lyrics_model"] == "minilm"
    assert row["lyrics_hash"] == "deadbeef"
    assert row["indexed_at"] == 100

    # Re-running (e.g. the batch resuming) replaces cleanly rather than
    # erroring or duplicating.
    embeddings.upsert(
        conn, 1, audio_vec=b"a" * 4, feature_vec=None, lyrics_vec=None,
        audio_model="clap", lyrics_model=None, lyrics_hash=None, now=200,
    )
    row = conn.execute("SELECT * FROM embeddings WHERE track_rowid = 1").fetchone()
    assert row["feature_vec"] is None
    assert row["indexed_at"] == 200


def test_update_lyrics_leaves_audio_and_feature_vectors_untouched(conn):
    """A manual lyrics edit (story 56) re-embeds only the lyrics leg -- the
    whole point of `lyrics_hash` is to avoid a full rescan."""
    insert_track(conn, 1)
    embeddings.upsert(
        conn, 1, audio_vec=b"a" * 4, feature_vec=b"f" * 4, lyrics_vec=b"old" * 4,
        audio_model="clap", lyrics_model="minilm", lyrics_hash="old-hash",
        audio_va=b"av" * 4, lyrics_va=b"lv-old" * 4, emotion_vec=b"ev-old" * 4,
        audio_va_model="mer", lyrics_va_model="textva",
        now=100,
    )

    embeddings.update_lyrics(
        conn, 1, lyrics_vec=b"new" * 4, lyrics_model="minilm",
        lyrics_hash="new-hash", lyrics_va=b"lv-new" * 4,
        emotion_vec=b"ev-new" * 4, lyrics_va_model="textva",
        now=200,
    )
    row = conn.execute("SELECT * FROM embeddings WHERE track_rowid = 1").fetchone()

    assert row["audio_vec"] == b"a" * 4
    assert row["feature_vec"] == b"f" * 4
    assert row["lyrics_vec"] == b"new" * 4
    assert row["lyrics_hash"] == "new-hash"
    assert row["indexed_at"] == 200
    # The emotion leg is lyrics-derived too -- a lyrics edit recomputes it --
    # but audio_va is untouched, same as audio_vec.
    assert row["audio_va"] == b"av" * 4
    assert row["lyrics_va"] == b"lv-new" * 4
    assert row["emotion_vec"] == b"ev-new" * 4


def test_coverage_counts_embedded_against_playable(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    insert_track(conn, 3, status="error")  # never counted as playable
    embeddings.upsert(
        conn, 1, audio_vec=b"a" * 4, feature_vec=None, lyrics_vec=None,
        audio_model="clap", lyrics_model=None, lyrics_hash=None,
    )

    result = embeddings.coverage(conn)

    assert result == {"playable": 2, "embedded": 1}


def test_upsert_writes_the_emotion_columns(conn):
    insert_track(conn, 1)

    embeddings.upsert(
        conn, 1, audio_vec=b"a" * 4, feature_vec=None, lyrics_vec=None,
        audio_model="clap", lyrics_model=None, lyrics_hash=None,
        audio_va=b"av" * 4, lyrics_va=b"lv" * 4, emotion_vec=b"ev" * 4,
        audio_va_model="mer", lyrics_va_model="textva",
    )
    row = conn.execute("SELECT * FROM embeddings WHERE track_rowid = 1").fetchone()

    assert row["audio_va"] == b"av" * 4
    assert row["lyrics_va"] == b"lv" * 4
    assert row["emotion_vec"] == b"ev" * 4
    assert row["audio_va_model"] == "mer"
    assert row["lyrics_va_model"] == "textva"


def test_emotion_coverage_counts_a_non_null_emotion_vec_against_playable(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    insert_track(conn, 3, status="error")  # never counted as playable
    embeddings.upsert(
        conn, 1, audio_vec=b"a" * 4, feature_vec=None, lyrics_vec=None,
        audio_model="clap", lyrics_model=None, lyrics_hash=None,
        audio_va=b"av" * 4, emotion_vec=b"ev" * 4, audio_va_model="mer",
    )
    # track 2 is fully content-embedded but has no emotion leg yet.
    embeddings.upsert(
        conn, 2, audio_vec=b"a" * 4, feature_vec=None, lyrics_vec=None,
        audio_model="clap", lyrics_model=None, lyrics_hash=None,
    )

    result = embeddings.emotion_coverage(conn)

    assert result == {"playable": 2, "embedded": 1}


def test_lyrics_hash_by_track_reads_the_stored_hash_for_already_embedded_tracks(conn):
    insert_track(conn, 1)
    embeddings.upsert(
        conn, 1, audio_vec=b"a" * 4, feature_vec=None, lyrics_vec=b"l" * 4,
        audio_model="clap", lyrics_model="minilm", lyrics_hash="abc123",
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


def test_a_track_whose_va_was_attempted_and_unavailable_is_not_requeued(conn):
    """`pending` re-queues a track with a NULL `audio_va` so a row that never
    got the emotion columns has a path back into the leg. When the audio-VA
    model is gone that NULL is permanent, so every track qualifies and the
    whole library re-embeds on every restart -- ~50 minutes of CLAP that was
    already correct, burned each time.

    `audio_va_model` distinguishes the two: NULL means never asked, a value
    means asked. "Asked, and there is nothing" is a real answer, the same way
    `track_features` treats a row the mirror had no numbers for."""
    insert_track(conn, 1)
    embeddings.upsert(
        conn, 1, audio_vec=b"x", feature_vec=None, lyrics_vec=None,
        audio_model="stub", lyrics_model=None, lyrics_hash=None,
        audio_va=None, audio_va_model="unavailable",
    )
    assert [row["track_rowid"] for row in embeddings.pending(conn)] == []


def test_a_track_never_asked_about_va_is_still_requeued(conn):
    """The case the clause exists for, which must keep working: a row
    written by a caller that passed no emotion columns at all."""
    insert_track(conn, 1)
    embeddings.upsert(
        conn, 1, audio_vec=b"x", feature_vec=None, lyrics_vec=None,
        audio_model="stub", lyrics_model=None, lyrics_hash=None,
    )
    assert [row["track_rowid"] for row in embeddings.pending(conn)] == [1]
