"""`create_app`'s embed-batch startup wiring: `config.embed_batch` actually
runs the batch in the background, the same way `config.art_backfill` already
does for cover art. No HTTP layer here (this repo has no `TestClient`
dependency yet) -- the app's lifespan is driven directly, which is enough to
prove the wiring itself works: the job gets created, the stub encoders run,
and `/api/health`'s new `embeddings` coverage would reflect it.
"""

import asyncio

from muse import embed
from muse.api import create_app
from tests.conftest import make_config
from muse.library import embeddings as library_embeddings
from tests.conftest import insert_track


def _config(tmp_path, **overrides):
    return make_config(tmp_path, **overrides)


def test_embed_batch_runs_at_startup_when_enabled(tmp_path):
    lyrics_path = tmp_path / "1.lyrics.txt"
    lyrics_path.write_text("Hello world", encoding="utf-8")

    config = _config(tmp_path, embed_batch=True)
    app = create_app(
        config,
        audio_encoder=embed.StubAudioEncoder(),
        lyrics_encoder=embed.StubLyricsEncoder(),
        audio_va_encoder=embed.StubAudioVAEncoder(),
        lyrics_va_encoder=embed.StubLyricsVAEncoder(),
    )

    async def drive() -> dict:
        async with app.router.lifespan_context(app):
            conn = app.state.conn
            insert_track(conn, 1, status="done")
            conn.execute(
                "UPDATE tracks SET file_path = 'MUSIC/1.m4a',"
                " lyrics_path = ? WHERE track_rowid = 1",
                (str(lyrics_path),),
            )

            for _ in range(100):
                await asyncio.sleep(0.02)
                row = conn.execute(
                    "SELECT audio_vec FROM embeddings WHERE track_rowid = 1"
                ).fetchone()
                if row is not None and row["audio_vec"] is not None:
                    break

            return library_embeddings.coverage(conn), library_embeddings.emotion_coverage(conn)

    coverage, emotion_coverage = asyncio.run(drive())

    assert coverage == {"playable": 1, "embedded": 1}
    assert emotion_coverage == {"playable": 1, "embedded": 1}


def test_embed_batch_does_not_run_when_disabled(tmp_path):
    config = _config(tmp_path, embed_batch=False)
    app = create_app(
        config,
        audio_encoder=embed.StubAudioEncoder(),
        lyrics_encoder=embed.StubLyricsEncoder(),
    )

    async def drive() -> None:
        async with app.router.lifespan_context(app):
            assert app.state.conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE kind = ?", (embed.EMBED,)
            ).fetchone()[0] == 0

    asyncio.run(drive())
