"""Settings and scan routes, and the launch chain, through the real lifespan."""

import time

from fastapi.testclient import TestClient

from muse.api import create_app
from muse.tags import FileTags
from tests.conftest import make_config


def _tags(path):
    return FileTags(title="Fake Song", artist="Artist", album="Album",
                    album_artist="Artist", track_no=1, disc_no=1, isrc=None,
                    duration_ms=1000)


def _client(tmp_path):
    return TestClient(create_app(make_config(tmp_path), read_tags=_tags))


def test_default_settings_and_no_scan_job(tmp_path):
    with _client(tmp_path) as client:
        assert client.get("/api/settings").json() == {
            "music_folders": [], "downloader_unlocked": False, "folder_status": []}
        assert "scan" not in [j["kind"] for j in client.get("/api/jobs").json()]


def test_rejects_bad_folders(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b").mkdir()
    with _client(tmp_path) as client:
        put = lambda folders: client.put(
            "/api/settings/music_folders", json={"folders": folders})
        assert put(["rel/path"]).status_code == 400
        assert put([str(tmp_path / "nope")]).status_code == 400
        assert put([str(tmp_path / "a"), str(tmp_path / "a" / "b")]).status_code == 400


def test_valid_folder_is_saved_and_scanned(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    (music / "song.mp3").write_bytes(b"abc")
    with _client(tmp_path) as client:
        r = client.put("/api/settings/music_folders", json={"folders": [str(music)]})
        assert r.status_code == 200
        assert (tmp_path / "settings.json").exists()
        deadline = time.time() + 10
        names = []
        while time.time() < deadline and "Fake Song" not in names:
            time.sleep(0.1)
            names = [s["track_name"] for s in client.get("/api/library/songs").json()]
        assert "Fake Song" in names
        assert client.get("/api/settings").json()["folder_status"] == [
            {"path": str(music.resolve()), "present": True, "songs": 1}]


def test_post_scan_accepted(tmp_path):
    with _client(tmp_path) as client:
        assert client.post("/api/scan").status_code == 202
