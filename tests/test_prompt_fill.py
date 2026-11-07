"""`run_prompt_fill` and the launch chain's prompt cache fill."""

import asyncio
import time

from fastapi.testclient import TestClient

from muse import embed
from muse.api import create_app
from muse.jobs import run_prompt_fill
from tests.conftest import insert_track, make_config


class CountingEncoder(embed.StubAudioEncoder):
    calls = 0

    def encode_text(self, text):
        self.calls += 1
        return super().encode_text(text)


class RaisingEncoder(embed.StubAudioEncoder):
    def encode_text(self, text):
        raise RuntimeError("no worker")


def _rows(conn):
    return conn.execute("SELECT COUNT(*) FROM prompt_vectors").fetchone()[0]


def test_fill_then_skip(conn):
    encoder = CountingEncoder()
    assert run_prompt_fill(conn, lambda: encoder) is True
    assert _rows(conn) == 2
    assert encoder.calls == 2
    assert run_prompt_fill(conn, lambda: encoder) is False
    assert encoder.calls == 2


def test_fill_failure_writes_nothing(conn):
    assert run_prompt_fill(conn, RaisingEncoder) is False
    assert _rows(conn) == 0


def test_chain_fills_prompts_and_reports_energy_ready(tmp_path):
    app = create_app(make_config(tmp_path, embed_batch=True),
                     audio_encoder=embed.StubAudioEncoder())

    async def drive() -> int:
        async with app.router.lifespan_context(app):
            conn = app.state.conn
            insert_track(conn, 1, status="done")
            conn.execute("UPDATE tracks SET file_path = 'MUSIC/1.m4a' WHERE track_rowid = 1")
            for _ in range(100):
                await asyncio.sleep(0.02)
                if _rows(conn) == 2:
                    break
            return _rows(conn)

    assert asyncio.run(drive()) == 2


def test_smart_energy_ready_after_fill(tmp_path):
    app = create_app(make_config(tmp_path, embed_batch=True),
                     audio_encoder=embed.StubAudioEncoder())
    with TestClient(app) as client:
        conn = app.state.conn
        insert_track(conn, 1, status="done")
        conn.execute("UPDATE tracks SET file_path = 'MUSIC/1.m4a' WHERE track_rowid = 1")
        client.post("/api/scan")
        for _ in range(100):
            if client.get("/api/smart").json()["energy_ready"]:
                break
            time.sleep(0.02)
        assert client.get("/api/smart").json()["energy_ready"] is True
