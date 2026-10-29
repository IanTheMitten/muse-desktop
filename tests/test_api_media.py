import pytest
from fastapi.testclient import TestClient

from muse.api import create_app
from muse.library import art as library_art
from tests.conftest import insert_track, make_config

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 20
LRC = "[00:04.00] first\n[00:08.00] second\n"


@pytest.fixture
def client(tmp_path):
    config = make_config(tmp_path)
    app = create_app(config)
    with TestClient(app) as c:
        insert_track(app.state.conn, 1)
        app.state.conn.commit()
        c.app_, c.config = app, config
        yield c


def put(c, url, body, ctype="image/png"):
    return c.put(url, content=body, headers={"content-type": ctype})


def test_album_art_upload(client):
    assert put(client, "/api/albums/20/art", PNG).json() == {"ok": True}
    r = client.get("/api/albums/20/art")
    assert r.content == PNG and r.headers["cache-control"] == "no-cache"
    conn = client.app_.state.conn
    assert 20 not in [a["album_rowid"] for a in library_art.albums_needing_art(conn)]
    assert conn.execute("SELECT source FROM album_art WHERE album_rowid = 20").fetchone()[0] == "manual"
    assert put(client, "/api/albums/99/art", PNG).status_code == 404


def test_upload_rejections(client):
    assert put(client, "/api/albums/20/art", b"hi", "text/plain").status_code == 415
    assert put(client, "/api/albums/20/art", b"").status_code == 400
    assert put(client, "/api/albums/20/art", b"x" * (10 * 1024 * 1024 + 1)).status_code == 413


def test_artist_image_and_hide(client):
    assert put(client, "/api/artists/Some Artist/image", PNG).status_code == 200
    assert client.get("/api/artists/Some Artist/image").content == PNG
    assert put(client, "/api/artists/Nobody/image", PNG).status_code == 404
    assert client.get("/api/library/artists").json()[0]["has_image"] is True
    r = client.put("/api/artists/Some Artist/hidden", json={"hidden": True})
    assert r.json() == {"artist_name": "Some Artist", "hidden": True}
    assert client.get("/api/library/artists").json() == []
    assert [a["artist_name"] for a in client.get("/api/library/hidden-artists").json()] == ["Some Artist"]
    client.put("/api/artists/Some Artist/hidden", json={"hidden": False})
    assert len(client.get("/api/library/artists").json()) == 1


def test_lyrics_edit_and_timings(client, tmp_path):
    lyrics = tmp_path / "lyrics"
    lyrics.mkdir(exist_ok=True)
    (lyrics / "1.lrc").write_text(LRC, encoding="utf-8")
    assert client.put("/api/tracks/1/lyrics", json={"text": "new words"}).status_code == 200
    assert client.put("/api/tracks/99/lyrics", json={"text": "x"}).status_code == 404
    out = client.get("/api/tracks/1/lyrics/synced").json()
    assert out["synced"] is False and out["plain"] == "new words" and out["lrc_stale"] is True
    assert client.post("/api/tracks/1/lyrics/timings", json={"action": "keep"}).status_code == 200
    out = client.get("/api/tracks/1/lyrics/synced").json()
    assert out["synced"] is True and out["lrc_stale"] is False
    assert client.post("/api/tracks/1/lyrics/timings", json={"action": "keep"}).status_code == 404
    assert client.post("/api/tracks/1/lyrics/timings", json={"action": "drop"}).status_code == 200
    assert not (lyrics / "1.lrc").exists()
    assert client.post("/api/tracks/1/lyrics/timings", json={"action": "drop"}).status_code == 404


def test_playlist_append(client):
    pid = client.post("/api/playlists", json={"name": "P", "track_rowids": [1]}).json()["id"]
    assert client.post(f"/api/playlists/{pid}/tracks", json={"track_rowids": [1]}).status_code == 200
    assert client.post("/api/playlists/999/tracks", json={"track_rowids": [1]}).status_code == 404
    assert client.post(f"/api/playlists/{pid}/tracks", json={"track_rowids": [77]}).status_code == 422
