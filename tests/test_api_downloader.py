"""The downloader lock: Decision 4's 404s, the unlock/lock routes, the downloads
scan root and the settings view. Nothing here reaches the network."""

import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from muse import settings
from muse.api import create_app
from muse.embed import (
    StubAudioEncoder,
    StubAudioVAEncoder,
    StubLyricsEncoder,
    StubLyricsVAEncoder,
)
from muse.library import jobs as job_rows
from muse.tags import FileTags
from muse.ytdlp import binary_path
from tests.conftest import make_config, unlock


class FakeArtProvider:
    def cover(self, album):
        return None


class NoAnswerMB:
    def get(self, path, params):
        return None


def _tags(path):
    return FileTags(title="Fake Song", artist="Artist", album="Album",
                    album_artist="Artist", track_no=1, disc_no=1, isrc=None,
                    duration_ms=1000)


def _app(config, **kw):
    return create_app(
        config, art_provider=FakeArtProvider(), read_tags=_tags,
        audio_encoder=StubAudioEncoder(), lyrics_encoder=StubLyricsEncoder(),
        audio_va_encoder=StubAudioVAEncoder(),
        lyrics_va_encoder=StubLyricsVAEncoder(), **kw)


def _kinds(app):
    return [r["kind"] for r in app.state.conn.execute("SELECT kind FROM jobs")]


def _is_unlocked(config):
    return settings.load(config.settings_path)["downloader_unlocked"]


def _uuid():
    return str(uuid.uuid4())


def test_download_job_kinds_404_when_locked(tmp_path):
    app = _app(make_config(tmp_path))
    with TestClient(app) as client:
        for body in (
            {"kind": "mb", "artist_name": "A", "track_name": "T",
             "recording_mbid": _uuid()},
            {"kind": "mb_release", "release_group_mbid": _uuid()},
            {"kind": "url", "url": "https://youtube.com/watch?v=x",
             "artist_name": "A", "track_name": "T"},
        ):
            assert client.post("/api/jobs", json=body).status_code == 404, body


def test_retry_of_error_download_404_when_locked(tmp_path):
    app = _app(make_config(tmp_path))
    with TestClient(app) as client:
        conn = app.state.conn
        failed = job_rows.create(conn, "download", {"track_rowid": 100})
        job_rows.update(conn, failed, state=job_rows.ERROR, message="no")
        assert client.post(f"/api/jobs/{failed}/retry").status_code == 404


def _discover_paths():
    return ["/api/discover/artists?q=x",
            f"/api/discover/artists/{_uuid()}/releases",
            f"/api/discover/release-groups/{_uuid()}/tracks"]


def test_discover_404_when_locked_and_open_when_unlocked(tmp_path):
    with TestClient(_app(make_config(tmp_path))) as client:
        for path in _discover_paths():
            assert client.get(path).status_code == 404, path
    unlocked = make_config(tmp_path / "u")
    unlock(unlocked)
    with TestClient(_app(unlocked, mb=NoAnswerMB())) as client:
        for path in _discover_paths():
            assert client.get(path).status_code != 404, path


def test_wrong_password_is_403_and_stays_locked(tmp_path):
    config = make_config(tmp_path)
    app = _app(config)
    with TestClient(app) as client:
        r = client.post("/api/settings/downloader", json={"password": "nope"})
        assert r.status_code == 403
        assert not _is_unlocked(config)
        assert "ytdlp_install" not in _kinds(app)


def test_right_password_unlocks_and_starts_install(tmp_path):
    config = make_config(tmp_path)
    app = _app(config)
    with TestClient(app) as client:
        r = client.post("/api/settings/downloader", json={"password": "download-enjoy"})
        assert r.status_code == 200
        assert r.json()["downloader_unlocked"] is True
        assert _is_unlocked(config) is True
        assert "ytdlp_install" in _kinds(app)
        assert config.music_root.is_dir()


def test_unlock_with_binary_present_starts_no_install(tmp_path):
    config = make_config(tmp_path)
    binary = binary_path(config.data_dir)
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"x")
    app = _app(config)
    with TestClient(app) as client:
        r = client.post("/api/settings/downloader", json={"password": "download-enjoy"})
        assert r.status_code == 200
        assert "ytdlp_install" not in _kinds(app)


def test_lock_errors_queued_downloads(tmp_path):
    config = make_config(tmp_path)
    unlock(config)
    app = _app(config)
    with TestClient(app) as client:
        conn = app.state.conn
        ids = [job_rows.create(conn, "download", {"track_rowid": n}) for n in (1, 2)]
        r = client.delete("/api/settings/downloader")
        assert r.status_code == 200
        assert r.json()["downloader_unlocked"] is False
        for job_id in ids:
            job = job_rows.get(conn, job_id)
            assert job["state"] == "error"
            assert job["message"] == "Downloader locked"


def test_settings_downloads_and_downloader_views(tmp_path):
    locked = make_config(tmp_path / "l")
    with TestClient(_app(locked)) as client:
        body = client.get("/api/settings").json()
        assert body["downloads"] is None
        assert set(body["downloader"]) == {"installed", "install_state", "update_failed"}

    unlocked = make_config(tmp_path / "u")
    unlock(unlocked)
    with TestClient(_app(unlocked)) as client:
        assert client.get("/api/settings").json()["downloads"] == {
            "path": str(unlocked.music_root), "songs": 0}

    full = make_config(tmp_path / "f")
    (full.music_root / "A").mkdir(parents=True)
    (full.music_root / "A" / "x.mp3").write_bytes(b"abc")
    with TestClient(_app(full)) as client:
        assert client.get("/api/settings").json()["downloads"] is not None


def test_locked_downloads_folder_with_audio_is_scanned(tmp_path):
    config = make_config(tmp_path)
    song = config.music_root / "A" / "x.mp3"
    song.parent.mkdir(parents=True)
    song.write_bytes(b"abc")
    app = _app(config)
    with TestClient(app) as client:
        deadline = time.time() + 5
        rows = []
        while time.time() < deadline and not rows:
            time.sleep(0.1)
            rows = app.state.conn.execute(
                "SELECT 1 FROM tracks WHERE file_path = ?", (str(song),)).fetchall()
        assert rows


def test_locked_downloads_folder_with_only_incoming_is_not_scanned(tmp_path):
    config = make_config(tmp_path)
    stray = config.music_root / ".incoming" / "y.mp3"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"abc")
    app = _app(config)
    with TestClient(app) as client:
        time.sleep(1)
        assert app.state.conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 0


def test_install_route_locked_404_unlocked_202(tmp_path):
    with TestClient(_app(make_config(tmp_path / "l"))) as client:
        assert client.post("/api/downloader/install").status_code == 404
    unlocked = make_config(tmp_path / "u")
    unlock(unlocked)
    with TestClient(_app(unlocked)) as client:
        r = client.post("/api/downloader/install")
        assert r.status_code == 202
        assert "job_id" in r.json()


def test_stale_incoming_is_swept_at_startup(tmp_path):
    config = make_config(tmp_path)
    stale = config.music_root / ".incoming" / "stale.m4a"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"x")
    with TestClient(_app(config)):
        assert not stale.exists()


def test_install_without_release_url_ends_in_error(tmp_path):
    config = make_config(tmp_path, ytdlp_release_url="")
    unlock(config)
    with TestClient(_app(config)) as client:
        job_id = client.post("/api/downloader/install").json()["job_id"]
        deadline = time.time() + 5
        job = client.get(f"/api/jobs/{job_id}").json()
        while time.time() < deadline and job["state"] != "error":
            time.sleep(0.1)
            job = client.get(f"/api/jobs/{job_id}").json()
        assert job["state"] == "error"
        assert job["message"] == "Couldn't get the downloader"
