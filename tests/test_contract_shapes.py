"""Characterization of the response fields on the Cadence surface
(`docs/contracts/cadence.md`).

Test-after on purpose: the behaviour already exists, and the OpenAPI snapshot
cannot see response fields, so this file is the contract for them. Extra fields
are always allowed; only the fields asserted here may not change.
"""

import pytest
from fastapi.testclient import TestClient

from muse.api import create_app
from muse.config import Config
from muse.embed import pack_vector
from muse.library import embeddings as library_embeddings
from muse.library.importer import import_manifest
from tests.conftest import FakeCatalog, make_facts, make_manifest, make_record

AUDIO_BYTES = bytes(range(256)) * 4


class FakeTextEncoder:
    """Stands in for CLAP's text tower. Never imports torch."""

    def encode_text(self, text):
        return [1.0, 0.0] if "bright" in text else [0.0, 1.0]


@pytest.fixture
def client(tmp_path):
    config = Config(
        root=tmp_path, library_db=tmp_path / "library.sqlite3",
        manifest=tmp_path / "manifest.json", catalog=tmp_path / "catalog.sqlite3",
        audio_features=tmp_path / "features.sqlite3",
        music_root=tmp_path / "MUSIC", art_root=tmp_path / "art",
        web_dist=tmp_path / "web", backup_dir=tmp_path / "backups",
        backup_target=None, port=8080, host="127.0.0.1",
        art_backfill=False, embed_batch=False, content_embed=False,
        catalog_freeze="2025-11-13",
    )
    audio = tmp_path / "MUSIC" / "1.m4a"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(AUDIO_BYTES)
    app = create_app(config)
    with TestClient(app) as c:
        import_manifest(
            make_manifest(make_record(
                1, file_path=str(audio), track_name="Pray",
                artist_name="Justin Bieber", artist_names=["Justin Bieber"])),
            FakeCatalog({1: make_facts(1, duration_ms=213_463)}),
            app.state.conn,
        )
        library_embeddings.upsert(
            app.state.conn, 1, audio_vec=pack_vector([1.0, 0.0]),
            feature_vec=None, lyrics_vec=None, audio_model="stub",
            lyrics_model=None, lyrics_hash=None)
        app.state.text_encoder = FakeTextEncoder()
        yield c, app


def assert_track_fields(track):
    assert isinstance(track["track_rowid"], int)
    assert isinstance(track["track_name"], str)
    assert isinstance(track["artist_name"], str)
    assert isinstance(track["artist_names"], list)
    assert all(isinstance(n, str) for n in track["artist_names"])
    assert track["duration_ms"] is None or isinstance(track["duration_ms"], int)
    assert isinstance(track["playable"], bool)


def open_play(c):
    r = c.post("/api/plays", json={
        "track_rowid": 1, "duration_ms": 213463, "source": "cadence"})
    assert r.status_code == 201
    return r.json()["id"]


def test_search_result_fields(client):
    c, _ = client
    r = c.get("/api/library/search?q=Pray&limit=5")
    assert r.status_code == 200
    track = r.json()["results"][0]
    assert_track_fields(track)
    assert track["playable"] is True


def test_vibe_result_fields(client):
    c, _ = client
    r = c.get("/api/library/search/vibe?q=bright&n=5")
    assert r.status_code == 200
    hit = r.json()["results"][0]
    assert isinstance(hit["track_rowid"], int)
    assert isinstance(hit["score"], float)
    assert_track_fields(hit["track"])


def test_vibe_without_embed_extra_is_503(client, monkeypatch):
    import muse.api

    c, app = client
    del app.state.text_encoder

    def no_extra():
        raise ImportError("No module named 'torch'")

    monkeypatch.setattr(muse.api, "ClapAudioEncoder", no_extra)
    assert c.get("/api/library/search/vibe?q=bright&n=5").status_code == 503


def test_open_play(client):
    c, app = client
    r = c.post("/api/plays", json={
        "track_rowid": 1, "duration_ms": 213463, "source": "cadence"})
    assert r.status_code == 201
    play_id = r.json()["id"]
    assert isinstance(play_id, int)
    row = app.state.conn.execute(
        "SELECT source FROM plays WHERE id=?", (play_id,)).fetchone()
    assert row[0] == "cadence"


def test_heartbeat(client):
    c, _ = client
    play_id = open_play(c)
    r = c.patch(f"/api/plays/{play_id}", json={"position_ms": 30000})
    assert r.status_code == 200


def test_close_play(client):
    c, app = client
    play_id = open_play(c)
    r = c.post(f"/api/plays/{play_id}/close", json={
        "ended_reason": "pause_timeout", "position_ms": 30000})
    assert r.status_code == 200
    row = app.state.conn.execute(
        "SELECT ended_reason, reached_ms FROM plays WHERE id=?",
        (play_id,)).fetchone()
    assert row[0] == "pause_timeout"
    assert row[1] == 30000


def test_close_with_unknown_reason_is_422(client):
    c, _ = client
    play_id = open_play(c)
    r = c.post(f"/api/plays/{play_id}/close", json={
        "ended_reason": "stopped", "position_ms": 30000})
    assert r.status_code == 422


def test_ratings(client):
    c, _ = client
    assert c.post("/api/ratings", json={
        "track_rowid": 1, "rating": 1}).status_code == 200
    assert c.post("/api/ratings", json={
        "track_rowid": 1, "rating": None}).status_code == 200
    assert c.post("/api/ratings", json={
        "track_rowid": 1, "rating": 5}).status_code == 422
    assert c.post("/api/ratings", json={
        "track_rowid": 999, "rating": 1}).status_code == 404


def test_stream(client):
    c, _ = client
    r = c.get("/api/tracks/1/stream")
    assert r.status_code == 200
    assert r.content == AUDIO_BYTES
    assert r.headers["content-type"].startswith("audio/")
    ranged = c.get("/api/tracks/1/stream", headers={"Range": "bytes=0-9"})
    assert ranged.status_code == 206
    assert len(ranged.content) == 10
    assert c.get("/api/tracks/999/stream").status_code == 404
