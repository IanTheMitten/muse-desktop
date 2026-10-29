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


def test_stale_scan_row_is_reaped_at_launch(tmp_path):
    from muse.library import db, jobs as job_rows
    from muse import settings
    conn = db.connect(tmp_path / "library.sqlite3")
    job_id = job_rows.create(conn, "scan", {})
    job_rows.update(conn, job_id, state=job_rows.RUNNING)
    conn.close()
    music = tmp_path / "music"
    music.mkdir()
    (music / "song.mp3").write_bytes(b"abc")
    settings.save(tmp_path / "settings.json",
                  {"music_folders": [str(music.resolve())], "downloader_unlocked": False})
    with _client(tmp_path) as client:
        deadline = time.time() + 10
        names = []
        while time.time() < deadline and "Fake Song" not in names:
            time.sleep(0.1)
            names = [s["track_name"] for s in client.get("/api/library/songs").json()]
        assert "Fake Song" in names


def test_shutdown_mid_scan_cancels_the_job(tmp_path):
    from muse.library import db, jobs as job_rows
    music = tmp_path / "music"
    music.mkdir()
    for i in range(5):
        (music / f"s{i}.mp3").write_bytes(b"abc%d" % i)

    def slow_tags(path):
        time.sleep(0.3)
        return _tags(path)

    app = create_app(make_config(tmp_path), read_tags=slow_tags)
    with TestClient(app) as client:
        client.put("/api/settings/music_folders", json={"folders": [str(music)]})
        deadline = time.time() + 5
        while time.time() < deadline and not any(
                j["kind"] == "scan" and j["state"] == "running"
                for j in client.get("/api/jobs").json()):
            time.sleep(0.05)
    conn = db.connect(tmp_path / "library.sqlite3")
    assert job_rows.last(conn, "scan")["state"] == job_rows.CANCELLED
    conn.close()
