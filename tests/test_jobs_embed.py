"""`jobs.run_embed_backfill`: the SSE/job-bookkeeping wrapper around
`embed.run_embed_batch`.

The one behavior worth testing here and not already covered at the pure-batch
level (`tests/test_embed_batch.py`) is that this wrapper must not skip the
lyrics-changed pass just because there is nothing new to embed — a run
gated on an empty pending queue would silently stop noticing manual lyrics
edits on every normal startup after the first.
"""

import asyncio
import threading

from muse import embed
from muse.jobs import JobBus, run_embed_backfill
from tests.conftest import insert_track


def _with_lyrics(conn, tmp_path, rowid, text):
    insert_track(conn, rowid, status="done")
    path = tmp_path / f"{rowid}.lyrics.txt"
    path.write_text(text, encoding="utf-8")
    conn.execute(
        "UPDATE tracks SET lyrics_path = ?, file_path = 'MUSIC/x.m4a'"
        " WHERE track_rowid = ?",
        (str(path), rowid),
    )
    return path


def test_a_lyrics_edit_is_reembedded_even_with_nothing_new_pending(conn, tmp_path):
    lyrics_path = _with_lyrics(conn, tmp_path, 1, "Original lyrics")
    bus = JobBus()

    async def run():
        return await run_embed_backfill(
            conn, embed.StubAudioEncoder, embed.StubLyricsEncoder, bus
        )

    first = asyncio.run(run())
    assert first["state"] == "done"
    original_hash = conn.execute(
        "SELECT lyrics_hash FROM embeddings WHERE track_rowid = 1"
    ).fetchone()["lyrics_hash"]

    lyrics_path.write_text("A manually edited verse", encoding="utf-8")

    # Nothing is pending -- track 1 is already fully embedded -- so the only
    # thing this second run can find is the lyrics-hash mismatch.
    second = asyncio.run(run())

    assert second is not None
    assert second["state"] == "done"
    new_hash = conn.execute(
        "SELECT lyrics_hash FROM embeddings WHERE track_rowid = 1"
    ).fetchone()["lyrics_hash"]
    assert new_hash != original_hash
    assert new_hash == embed.lyrics_hash("A manually edited verse")


def test_encoders_are_built_off_the_calling_thread(conn, tmp_path):
    """`run_embed_backfill` takes factories, not instances, and must call
    them from inside the worker thread the batch itself runs on -- never
    from the caller. A real encoder's __init__ is ~70 s of model load; built
    on the caller's thread (the event loop, in production) it would block
    every concurrent request for that whole window."""
    _with_lyrics(conn, tmp_path, 1, "Hello world")
    caller_thread = threading.current_thread()
    build_threads: list[threading.Thread] = []

    def audio_factory():
        build_threads.append(threading.current_thread())
        return embed.StubAudioEncoder()

    def lyrics_factory():
        build_threads.append(threading.current_thread())
        return embed.StubLyricsEncoder()

    bus = JobBus()
    asyncio.run(run_embed_backfill(conn, audio_factory, lyrics_factory, bus))

    assert len(build_threads) == 2
    assert all(t is not caller_thread for t in build_threads)


def test_va_encoders_are_built_off_the_calling_thread_when_given(conn, tmp_path):
    """Same contract as the content encoders: a real MER/text-VA model's
    __init__ is not free either, and `run_embed_backfill` must build it in
    the worker thread whether or not a caller bothers to pass a VA factory
    at all."""
    _with_lyrics(conn, tmp_path, 1, "Hello world")
    caller_thread = threading.current_thread()
    build_threads: list[threading.Thread] = []

    def audio_va_factory():
        build_threads.append(threading.current_thread())
        return embed.StubAudioVAEncoder()

    def lyrics_va_factory():
        build_threads.append(threading.current_thread())
        return embed.StubLyricsVAEncoder()

    bus = JobBus()
    asyncio.run(
        run_embed_backfill(
            conn, embed.StubAudioEncoder, embed.StubLyricsEncoder, bus,
            audio_va_encoder_factory=audio_va_factory,
            lyrics_va_encoder_factory=lyrics_va_factory,
        )
    )

    assert len(build_threads) == 2
    assert all(t is not caller_thread for t in build_threads)


def test_emotion_leg_is_populated_without_a_va_factory(conn, tmp_path):
    """No VA factories given -- the common case, matching every existing
    call site -- still produces an emotion leg via the stub, the same way
    omitting audio_va_encoder/lyrics_va_encoder from run_embed_batch does."""
    _with_lyrics(conn, tmp_path, 1, "Hello world")
    bus = JobBus()

    asyncio.run(
        run_embed_backfill(conn, embed.StubAudioEncoder, embed.StubLyricsEncoder, bus)
    )

    row = conn.execute(
        "SELECT emotion_vec FROM embeddings WHERE track_rowid = 1"
    ).fetchone()
    assert row["emotion_vec"] is not None


def test_returns_none_when_a_batch_is_already_running(conn, tmp_path):
    _with_lyrics(conn, tmp_path, 1, "Some lyrics")
    from muse.library import jobs as job_rows

    job_rows.create(conn, embed.EMBED)  # left "queued" by a prior run
    bus = JobBus()

    result = asyncio.run(
        run_embed_backfill(conn, embed.StubAudioEncoder, embed.StubLyricsEncoder, bus)
    )

    assert result is None


def test_an_unavailable_va_encoder_does_not_kill_the_batch(conn, tmp_path):
    """Emotion is an optional leg; failing to build its encoder must cost the
    emotion leg, not the whole run.

    Found on a real machine. `MerAudioVAEncoder` named
    `audeering/wav2vec2-large-robust-12-ffn1024-msp-dim`, an id that has
    never existed -- a typo for `...-12-ft-emotion-msp-dim`, not a model
    that was withdrawn -- and every encoder was constructed unguarded at the
    top of the worker, so one unreachable external model failed the batch
    outright and left CLAP, lyrics, and features unwritten too. Degrade in
    quality, never in availability (principle 4)."""
    _with_lyrics(conn, tmp_path, 1, "a line")

    def dead_va_encoder():
        raise OSError("model is not a valid model identifier listed on HF")

    job = asyncio.run(run_embed_backfill(
        conn, embed.StubAudioEncoder, embed.StubLyricsEncoder, JobBus(),
        dead_va_encoder, embed.StubLyricsVAEncoder,
    ))
    assert job["state"] == "done"
    row = conn.execute(
        "SELECT audio_vec, lyrics_vec, audio_va FROM embeddings"
        " WHERE track_rowid = 1"
    ).fetchone()
    assert row["audio_vec"] is not None
    assert row["lyrics_vec"] is not None
    assert row["audio_va"] is None


def test_a_dead_core_encoder_still_fails_the_batch(conn, tmp_path):
    """The degradation is deliberately narrow. CLAP is not optional -- a run
    that cannot embed audio has nothing to fall back to, and reporting `done`
    would claim work it did not do."""
    _with_lyrics(conn, tmp_path, 1, "a line")

    def dead_audio_encoder():
        raise OSError("no CLAP")

    job = asyncio.run(run_embed_backfill(
        conn, dead_audio_encoder, embed.StubLyricsEncoder, JobBus(),
    ))
    assert job["state"] == "error"


def test_an_instrumental_embeds_when_the_va_encoder_is_unavailable(conn, tmp_path):
    """The case the first version of this guard missed, and real data found.

    `test_an_unavailable_va_encoder_does_not_kill_the_batch` gives its track
    lyrics, so the lyrics-VA encoder still supplies a point and the fused
    emotion vector is never None. An instrumental has no lyrics either, so
    with the audio-VA model gone there is no VA evidence at all -- and the
    write path packed that None straight into `pack_vector`, failing 21 real
    tracks with `TypeError: 'NoneType' object is not iterable` after the
    batch had already reported them as work."""
    insert_track(conn, 1, status="done")
    conn.execute(
        "UPDATE tracks SET file_path = 'MUSIC/x.m4a' WHERE track_rowid = 1"
    )

    def dead_va_encoder():
        raise OSError("model is gone")

    job = asyncio.run(run_embed_backfill(
        conn, embed.StubAudioEncoder, embed.StubLyricsEncoder, JobBus(),
        dead_va_encoder, embed.StubLyricsVAEncoder,
    ))
    assert job["state"] == "done"
    assert "0 failed" in job["message"] or "failed" not in job["message"]
    row = conn.execute(
        "SELECT audio_vec, audio_va, emotion_vec FROM embeddings"
        " WHERE track_rowid = 1"
    ).fetchone()
    assert row["audio_vec"] is not None
    assert row["audio_va"] is None
    assert row["emotion_vec"] is None
