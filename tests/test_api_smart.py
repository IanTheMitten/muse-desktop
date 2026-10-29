"""`GET /api/smart`, `POST /api/smart/install`, and the launch chain's ML
steps run through a fake worker client. No network: the installer gets a fake
session, and `smart.load_manifest`/`host_triple` are patched."""

import hashlib
import io
import json
import time
import zipfile

from fastapi.testclient import TestClient

from muse import embed, smart
from muse.api import create_app
from tests.conftest import insert_track, make_config

TRIPLE = "aarch64-apple-darwin"
BODY = b"w" * 100
_zip = io.BytesIO()
with zipfile.ZipFile(_zip, "w") as _z:
    _z.writestr("muse-worker/muse-worker", b"#!/bin/sh\n")
ZIP = _zip.getvalue()
BODIES = {"https://x/a.bin": BODY, "https://x/w.zip": ZIP}


def _entry(path, url):
    body = BODIES[url]
    return {"path": path, "url": url, "sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}


MANIFEST = {
    "version": 1,
    "weights": [_entry("weights/mert/a.bin", "https://x/a.bin")],
    "workers": {TRIPLE: _entry(f"muse-worker-{TRIPLE}.zip", "https://x/w.zip")},
}


class FakeResponse:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def iter_content(self, chunk_size=1024):
        yield self.body

    def raise_for_status(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, hold=None):
        self.hold = hold

    def get(self, url, headers=None, stream=True, timeout=60):
        if self.hold is not None:
            self.hold.wait(5)
        return FakeResponse(BODIES[url])


class FakeWorker:
    def __init__(self):
        self.ops = []
        self.begun = 0

    def begin(self):
        self.begun += 1

    def stop(self):
        pass

    def request(self, op, **args):
        self.ops.append(op)
        raise RuntimeError(f"fake worker cannot do {op}")


def _app(tmp_path, monkeypatch, triple=TRIPLE, **overrides):
    monkeypatch.setattr(smart, "load_manifest", lambda *a, **k: MANIFEST)
    monkeypatch.setattr(smart, "host_triple", lambda: triple)
    return create_app(make_config(tmp_path, **overrides), worker_client=FakeWorker())


def _wait(client, state, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get("/api/smart").json()
        if body["job"] and body["job"]["state"] == state:
            return body
        time.sleep(0.02)
    raise AssertionError(f"smart job never reached {state}: {body}")


def test_get_not_installed(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as client:
        assert client.get("/api/smart").json() == {
            "state": "not_installed", "download_bytes": len(BODY) + len(ZIP), "job": None}


def test_get_unsupported(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch, triple=None)) as client:
        body = client.get("/api/smart").json()
        assert body["state"] == "unsupported"
        assert client.post("/api/smart/install").status_code == 409


def test_get_installed(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    config.models_dir.mkdir()
    (config.models_dir / "installed.json").write_text(
        json.dumps({"manifest_version": 1, "triple": TRIPLE}))
    with TestClient(_app(tmp_path, monkeypatch)) as client:
        assert client.get("/api/smart").json()["state"] == "installed"
        assert client.post("/api/smart/install").status_code == 409


def test_install_runs_to_done_and_a_second_post_conflicts(tmp_path, monkeypatch):
    import threading

    hold = threading.Event()
    app = _app(tmp_path, monkeypatch)
    app.state.smart_session = FakeSession(hold)
    with TestClient(app) as client:
        response = client.post("/api/smart/install")
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        assert client.post("/api/smart/install").status_code == 409
        hold.set()
        body = _wait(client, "done")
        assert body["job"]["id"] == job_id
        assert body["state"] == "installed"
        assert (tmp_path / "models" / "weights" / "mert" / "a.bin").read_bytes() == BODY


def test_install_failure_leaves_a_plain_error(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    app.state.smart_session = FakeSession()
    monkeypatch.setattr(smart, "install", lambda *a, **k: (_ for _ in ()).throw(
        smart.NotEnoughSpace("Not enough disk space: needs 3.6 GB")))
    with TestClient(app) as client:
        assert client.post("/api/smart/install").status_code == 202
        body = _wait(client, "error")
        assert body["job"]["message"] == "Not enough disk space: needs 3.6 GB"
        assert body["state"] == "not_installed"


def test_chain_runs_content_and_lyric_tag_steps_when_installed(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    config.models_dir.mkdir()
    (config.models_dir / "installed.json").write_text(
        json.dumps({"manifest_version": 1, "triple": TRIPLE}))
    lyrics = tmp_path / "1.lyrics.txt"
    lyrics.write_text("Hello world\nSecond line here", encoding="utf-8")
    worker = FakeWorker()
    monkeypatch.setattr(smart, "load_manifest", lambda *a, **k: MANIFEST)
    app = create_app(
        config, worker_client=worker,
        audio_encoder=embed.StubAudioEncoder(), lyrics_encoder=embed.StubLyricsEncoder(),
        audio_va_encoder=embed.StubAudioVAEncoder(), lyrics_va_encoder=embed.StubLyricsVAEncoder(),
    )
    with TestClient(app) as client:
        insert_track(app.state.conn, 1, status="done")
        app.state.conn.execute(
            "UPDATE tracks SET file_path = ?, lyrics_path = ? WHERE track_rowid = 1",
            (str(tmp_path / "1.m4a"), str(lyrics)))
        app.state.conn.commit()
        (tmp_path / "1.m4a").write_bytes(b"x")
        client.post("/api/scan")
        deadline = time.time() + 10
        while time.time() < deadline and not {"mert", "laya"} <= set(worker.ops):
            time.sleep(0.05)
    assert worker.begun >= 1
    assert "mert" in worker.ops
    assert "laya" in worker.ops
