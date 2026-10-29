"""`run_embed_batch`: the orchestration that turns the pending queue and a
lyrics-hash mismatch into actual `embeddings` rows.

Everything here runs against the stub encoders — no torch, same as the rest
of the default test run (PRD seam 7). A counting wrapper around the stub
stands in for "was the model actually invoked", which is what proves
resumability and the lyrics-only re-embed path are not just returning cached
rows by accident.
"""

from pathlib import Path

import pytest

from muse import embed
from muse.library import embeddings as library_embeddings
from tests.conftest import insert_track


class CountingAudioEncoder:
    model_name = embed.STUB_AUDIO_MODEL

    def __init__(self):
        self.calls: list[Path] = []
        self._inner = embed.StubAudioEncoder()

    def encode(self, audio_path):
        self.calls.append(audio_path)
        return self._inner.encode(audio_path)


class CountingLyricsEncoder:
    model_name = embed.STUB_LYRICS_MODEL

    def __init__(self):
        self.calls: list[str] = []
        self._inner = embed.StubLyricsEncoder()

    def encode(self, text):
        self.calls.append(text)
        return self._inner.encode(text)


def _with_lyrics(conn, tmp_path, rowid, text, *, file_path="MUSIC/x.m4a"):
    insert_track(conn, rowid, status="done")
    path = tmp_path / f"{rowid}.lyrics.txt"
    path.write_text(text, encoding="utf-8")
    conn.execute(
        "UPDATE tracks SET lyrics_path = ?, file_path = ? WHERE track_rowid = ?",
        (str(path), file_path, rowid),
    )


def _stock_features(conn, rowid):
    conn.execute(
        "INSERT INTO track_features (track_rowid, danceability, energy,"
        " loudness, speechiness, acousticness, instrumentalness, liveness,"
        " valence, tempo, key, mode, time_signature, checked_at)"
        " VALUES (?, 0.5,0.5,-8.0,0.05,0.2,0.0,0.1,0.5,120.0,0,1,4, 0)",
        (rowid,),
    )


def test_embeds_audio_va_and_lyrics_va_for_a_fully_measured_track(conn, tmp_path):
    _with_lyrics(conn, tmp_path, 1, "[Chorus]\nHello world")
    _stock_features(conn, 1)

    embed.run_embed_batch(conn, embed.StubAudioEncoder(), embed.StubLyricsEncoder())

    row = conn.execute("SELECT * FROM embeddings WHERE track_rowid = 1").fetchone()
    audio_va = embed.unpack_vector(row["audio_va"])
    lyrics_va = embed.unpack_vector(row["lyrics_va"])
    emotion_vec = embed.unpack_vector(row["emotion_vec"])
    assert len(audio_va) == 2
    assert len(lyrics_va) == 2
    # Two float32 round trips (pack audio_va/lyrics_va, unpack, fuse, pack
    # again) vs. one here -- exact equality would just be asserting rounding
    # error, so this compares within float32 precision instead.
    expected = embed.fuse_emotion_vec(tuple(audio_va), tuple(lyrics_va))
    assert emotion_vec == pytest.approx(list(expected), abs=1e-6)
    assert row["audio_va_model"] == embed.STUB_AUDIO_VA_MODEL
    assert row["lyrics_va_model"] == embed.STUB_LYRICS_VA_MODEL


def test_an_instrumental_still_gets_an_emotion_vec_falling_back_to_audio_va(conn, tmp_path):
    insert_track(conn, 1, status="done")
    conn.execute(
        "UPDATE tracks SET file_path = 'MUSIC/1.m4a' WHERE track_rowid = 1"
    )

    embed.run_embed_batch(conn, embed.StubAudioEncoder(), embed.StubLyricsEncoder())

    row = conn.execute("SELECT * FROM embeddings WHERE track_rowid = 1").fetchone()
    assert row["lyrics_va"] is None
    audio_va = embed.unpack_vector(row["audio_va"])
    emotion_vec = embed.unpack_vector(row["emotion_vec"])
    assert emotion_vec == audio_va


def test_editing_lyrics_recomputes_lyrics_va_and_emotion_vec_but_not_audio_va(conn, tmp_path):
    _with_lyrics(conn, tmp_path, 1, "Hello world")
    embed.run_embed_batch(conn, embed.StubAudioEncoder(), embed.StubLyricsEncoder())
    before = conn.execute(
        "SELECT audio_va, lyrics_va FROM embeddings WHERE track_rowid = 1"
    ).fetchone()

    path = Path(conn.execute("SELECT lyrics_path FROM tracks WHERE track_rowid = 1").fetchone()["lyrics_path"])
    path.write_text("A brand new verse", encoding="utf-8")

    result = embed.run_embed_batch(conn, embed.StubAudioEncoder(), embed.StubLyricsEncoder())

    assert result.lyrics_reembedded == 1
    after = conn.execute(
        "SELECT audio_va, lyrics_va, emotion_vec FROM embeddings WHERE track_rowid = 1"
    ).fetchone()
    assert after["audio_va"] == before["audio_va"]
    assert after["lyrics_va"] != before["lyrics_va"]
    audio_va = embed.unpack_vector(after["audio_va"])
    lyrics_va = embed.unpack_vector(after["lyrics_va"])
    emotion_vec = embed.unpack_vector(after["emotion_vec"])
    expected = embed.fuse_emotion_vec(tuple(audio_va), tuple(lyrics_va))
    assert emotion_vec == pytest.approx(list(expected), abs=1e-6)


def test_embeds_audio_feature_and_lyrics_for_a_fully_measured_track(conn, tmp_path):
    _with_lyrics(conn, tmp_path, 1, "[Chorus]\nHello world")
    _stock_features(conn, 1)
    # A second track gives the bounds something non-degenerate to freeze
    # against.
    _with_lyrics(conn, tmp_path, 2, "Some other song")
    _stock_features(conn, 2)

    result = embed.run_embed_batch(conn, embed.StubAudioEncoder(), embed.StubLyricsEncoder())

    assert result.considered == 2
    assert result.embedded == 2
    assert result.failed == 0

    row = conn.execute("SELECT * FROM embeddings WHERE track_rowid = 1").fetchone()
    assert embed.unpack_vector(row["audio_vec"]) is not None
    assert len(embed.unpack_vector(row["audio_vec"])) == embed.AUDIO_DIM
    assert len(embed.unpack_vector(row["feature_vec"])) == 13
    assert len(embed.unpack_vector(row["lyrics_vec"])) == embed.LYRICS_DIM
    assert row["audio_model"] == embed.STUB_AUDIO_MODEL
    assert row["lyrics_model"] == embed.STUB_LYRICS_MODEL
    assert row["lyrics_hash"] == embed.lyrics_hash("Hello world")
    assert row["indexed_at"] is not None


def test_a_track_with_no_lyrics_still_gets_a_row_with_null_lyrics_vec(conn, tmp_path):
    insert_track(conn, 1, status="done")
    conn.execute(
        "UPDATE tracks SET file_path = 'MUSIC/1.m4a' WHERE track_rowid = 1"
    )

    result = embed.run_embed_batch(conn, embed.StubAudioEncoder(), embed.StubLyricsEncoder())

    assert result.embedded == 1
    row = conn.execute("SELECT * FROM embeddings WHERE track_rowid = 1").fetchone()
    assert row is not None
    assert embed.unpack_vector(row["audio_vec"]) is not None
    assert row["lyrics_vec"] is None
    assert row["lyrics_hash"] is None


def test_a_null_response_track_still_gets_a_row_with_null_feature_vec(conn, tmp_path):
    """The checklist's own wording: a track with a null-response features
    reading is not excluded from the embeddings table -- it gets a row with a
    NULL feature_vec, same as any other unmeasured leg."""
    insert_track(conn, 1, status="done")
    conn.execute(
        "UPDATE tracks SET file_path = 'MUSIC/1.m4a' WHERE track_rowid = 1"
    )
    # null_response: a track_features row whose measurements are all NULL.
    conn.execute(
        "INSERT INTO track_features (track_rowid, checked_at) VALUES (1, 0)"
    )

    result = embed.run_embed_batch(conn, embed.StubAudioEncoder(), embed.StubLyricsEncoder())

    assert result.embedded == 1
    row = conn.execute("SELECT * FROM embeddings WHERE track_rowid = 1").fetchone()
    assert row is not None
    assert embed.unpack_vector(row["audio_vec"]) is not None
    assert row["feature_vec"] is None


def test_a_killed_batch_resumes_from_the_null_audio_vec_queue(conn, tmp_path):
    _with_lyrics(conn, tmp_path, 1, "Hello world")
    _with_lyrics(conn, tmp_path, 2, "Goodbye world")
    audio = CountingAudioEncoder()

    embed.run_embed_batch(conn, audio, embed.StubLyricsEncoder())
    assert sorted(str(p) for p in audio.calls) == ["MUSIC/x.m4a"] * 2

    # Simulate a run interrupted right after track 2's row was created but
    # before its audio_vec was written.
    conn.execute(
        "UPDATE embeddings SET audio_vec = NULL WHERE track_rowid = 2"
    )
    audio.calls.clear()

    result = embed.run_embed_batch(conn, audio, embed.StubLyricsEncoder())

    assert result.considered == 1
    assert len(audio.calls) == 1


def test_an_already_embedded_track_is_not_reprocessed(conn, tmp_path):
    _with_lyrics(conn, tmp_path, 1, "Hello world")
    audio = CountingAudioEncoder()
    lyrics = CountingLyricsEncoder()
    embed.run_embed_batch(conn, audio, lyrics)
    audio.calls.clear()
    lyrics.calls.clear()

    result = embed.run_embed_batch(conn, audio, lyrics)

    assert result.considered == 0
    assert audio.calls == []
    assert lyrics.calls == []


def test_editing_the_lyrics_file_triggers_a_lyrics_only_reembed(conn, tmp_path):
    _with_lyrics(conn, tmp_path, 1, "Hello world")
    audio = CountingAudioEncoder()
    embed.run_embed_batch(conn, audio, embed.StubLyricsEncoder())
    before = conn.execute("SELECT audio_vec, feature_vec FROM embeddings WHERE track_rowid = 1").fetchone()
    audio.calls.clear()

    path = Path(conn.execute("SELECT lyrics_path FROM tracks WHERE track_rowid = 1").fetchone()["lyrics_path"])
    path.write_text("A brand new verse", encoding="utf-8")

    result = embed.run_embed_batch(conn, audio, embed.StubLyricsEncoder())

    assert result.lyrics_reembedded == 1
    assert audio.calls == []  # the audio leg was never touched

    after = conn.execute("SELECT audio_vec, feature_vec, lyrics_vec, lyrics_hash FROM embeddings WHERE track_rowid = 1").fetchone()
    assert after["audio_vec"] == before["audio_vec"]
    assert after["feature_vec"] == before["feature_vec"]
    assert after["lyrics_hash"] == embed.lyrics_hash("A brand new verse")


def test_unchanged_lyrics_are_not_reembedded_on_a_later_run(conn, tmp_path):
    _with_lyrics(conn, tmp_path, 1, "Hello world")
    lyrics = CountingLyricsEncoder()
    embed.run_embed_batch(conn, embed.StubAudioEncoder(), lyrics)
    lyrics.calls.clear()

    result = embed.run_embed_batch(conn, embed.StubAudioEncoder(), lyrics)

    assert result.lyrics_reembedded == 0
    assert lyrics.calls == []


def test_a_track_that_raises_is_counted_failed_and_does_not_stop_the_batch(conn, tmp_path):
    _with_lyrics(conn, tmp_path, 1, "Hello world")
    _with_lyrics(conn, tmp_path, 2, "Goodbye world")

    class FlakyAudioEncoder:
        model_name = "flaky"

        def encode(self, audio_path):
            raise RuntimeError("decode failed")

    result = embed.run_embed_batch(conn, FlakyAudioEncoder(), embed.StubLyricsEncoder())

    assert result.failed == 2
    assert result.embedded == 0
    assert library_embeddings.coverage(conn) == {"playable": 2, "embedded": 0}
