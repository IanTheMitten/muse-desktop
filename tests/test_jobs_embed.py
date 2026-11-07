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
            conn, embed.StubAudioEncoder, bus
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

    bus = JobBus()
    asyncio.run(run_embed_backfill(conn, audio_factory, bus))

    assert len(build_threads) == 1
    assert all(t is not caller_thread for t in build_threads)


def test_returns_none_when_a_batch_is_already_running(conn, tmp_path):
    _with_lyrics(conn, tmp_path, 1, "Some lyrics")
    from muse.library import jobs as job_rows

    job_rows.create(conn, embed.EMBED)  # left "queued" by a prior run
    bus = JobBus()

    result = asyncio.run(
        run_embed_backfill(conn, embed.StubAudioEncoder, bus)
    )

    assert result is None


def test_a_dead_core_encoder_still_fails_the_batch(conn, tmp_path):
    """The degradation is deliberately narrow. CLAP is not optional -- a run
    that cannot embed audio has nothing to fall back to, and reporting `done`
    would claim work it did not do."""
    _with_lyrics(conn, tmp_path, 1, "a line")

    def dead_audio_encoder():
        raise OSError("no CLAP")

    job = asyncio.run(run_embed_backfill(
        conn, dead_audio_encoder, JobBus(),
    ))
    assert job["state"] == "error"
