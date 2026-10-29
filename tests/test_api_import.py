"""POST /api/library/import: dropped folders and songs."""

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


def _drop(client, *paths):
    return client.post("/api/library/import", json={"paths": [str(p) for p in paths]})


def test_folder_drop(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    with _client(tmp_path) as client:
        assert _drop(client, music).json()["folders_added"] == 1
        assert client.get("/api/settings").json()["music_folders"] == [str(music.resolve())]


def test_song_outside_folders_is_scanned(tmp_path):
    song = tmp_path / "loose" / "song.mp3"
    song.parent.mkdir()
    song.write_bytes(b"abc")
    with _client(tmp_path) as client:
        assert _drop(client, song).json()["songs_added"] == 1
        assert client.get("/api/settings").json()["music_files"] == [str(song.resolve())]
        deadline = time.time() + 10
        names = []
        while time.time() < deadline and "Fake Song" not in names:
            time.sleep(0.1)
            names = [s["track_name"] for s in client.get("/api/library/songs").json()]
        assert "Fake Song" in names


def test_song_inside_listed_folder_is_already(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    song = music / "song.mp3"
    song.write_bytes(b"abc")
    with _client(tmp_path) as client:
        _drop(client, music)
        assert _drop(client, song).json()["already"] == 1
        assert client.get("/api/settings").json()["music_files"] == []


def test_same_folder_twice(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    with _client(tmp_path) as client:
        _drop(client, music)
        assert _drop(client, music).json()["already"] == 1


def test_non_audio_and_relative_ignored(tmp_path):
    txt = tmp_path / "a.txt"
    txt.write_text("x")
    with _client(tmp_path) as client:
        r = client.post("/api/library/import", json={"paths": [str(txt), "rel/x.mp3"]})
        assert r.json()["ignored"] == 2


def test_folder_containing_data_dir_rejected(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    cfg = make_config(data)
    with TestClient(create_app(cfg, read_tags=_tags)) as client:
        assert _drop(client, tmp_path).status_code == 400


def test_scan_marks_deleted_listed_file_missing(tmp_path):
    from muse import scanner
    from muse.library import db
    conn = db.connect(tmp_path / "library.sqlite3")
    song = tmp_path / "song.mp3"
    song.write_bytes(b"abc")
    kw = dict(lyrics_root=tmp_path / "lyrics", art_root=tmp_path / "art", read_tags=_tags)
    scanner.scan(conn, [song], **kw)
    assert conn.execute("SELECT status FROM tracks").fetchone()[0] == "done"
    song.unlink()
    scanner.scan(conn, [song], **kw)
    assert conn.execute("SELECT status FROM tracks").fetchone()[0] == "missing_file"


def test_parent_folder_replaces_listed_child(tmp_path):
    parent = tmp_path / "music"
    child = parent / "rock"
    child.mkdir(parents=True)
    with _client(tmp_path) as client:
        _drop(client, child)
        assert _drop(client, parent).json()["folders_added"] == 1
        assert client.get("/api/settings").json()["music_folders"] == [str(parent.resolve())]


def test_parent_drop_with_song_in_same_gesture(tmp_path):
    parent = tmp_path / "music"
    (parent / "rock").mkdir(parents=True)
    song = tmp_path / "loose" / "song.mp3"
    song.parent.mkdir()
    song.write_bytes(b"abc")
    with _client(tmp_path) as client:
        _drop(client, parent / "rock")
        r = _drop(client, parent, song)
        assert r.status_code == 200
        assert r.json()["folders_added"] == 1 and r.json()["songs_added"] == 1
        s = client.get("/api/settings").json()
        assert s["music_folders"] == [str(parent.resolve())]
        assert s["music_files"] == [str(song.resolve())]


def test_new_folder_prunes_dropped_songs_inside_it(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    song = music / "song.mp3"
    song.write_bytes(b"abc")
    with _client(tmp_path) as client:
        _drop(client, song)
        assert client.get("/api/settings").json()["music_files"] == [str(song.resolve())]
        _drop(client, music)
        assert client.get("/api/settings").json()["music_files"] == []


def test_put_folders_prunes_music_files(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    song = music / "song.mp3"
    song.write_bytes(b"abc")
    with _client(tmp_path) as client:
        _drop(client, song)
        r = client.put("/api/settings/music_folders", json={"folders": [str(music)]})
        assert r.status_code == 200
        assert client.get("/api/settings").json()["music_files"] == []
