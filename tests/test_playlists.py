"""Playlist storage (muse/library/playlists.py) and its routes. Test-first."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from muse.api import create_app
from muse.config import Config
from muse.library import playlists
from tests.conftest import insert_track


def tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def rows(conn, pid):
    return [
        tuple(r) for r in conn.execute(
            "SELECT position, track_rowid FROM playlist_tracks"
            " WHERE playlist_id = ? ORDER BY position", (pid,))
    ]


def test_migrate_creates_playlist_tables(conn):
    assert {"playlists", "playlist_tracks"} <= tables(conn)


def test_create_keeps_order_and_duplicates(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    pid = playlists.create(conn, "Late", [1, 2, 1])
    assert rows(conn, pid) == [(0, 1), (1, 2), (2, 1)]


def test_unknown_track_raises_and_leaves_no_playlist(conn):
    insert_track(conn, 1)
    with pytest.raises(sqlite3.IntegrityError):
        playlists.create(conn, "Bad", [1, 999])
    assert conn.execute("SELECT COUNT(*) FROM playlists").fetchone()[0] == 0


def test_summaries_newest_updated_first_with_covers(conn):
    for rowid, album in [(1, 20), (2, 20), (3, 21), (4, 22), (5, 23), (6, 24), (7, 25)]:
        insert_track(conn, rowid, album_rowid=album, duration_ms=1000)
    insert_track(conn, 8, album_rowid=26, duration_ms=1000)  # no art
    conn.execute("UPDATE tracks SET art_path = 'a.jpg' WHERE track_rowid != 8")
    old = playlists.create(conn, "Old", [1], now=100)
    new = playlists.create(conn, "New", [1, 2, 3, 4, 5, 6, 7, 8], now=200)
    out = playlists.summaries(conn)
    assert [p["id"] for p in out] == [new, old]
    big = out[0]
    assert big["track_count"] == 8
    assert big["runtime_ms"] == 8000
    assert len(big["cover_track_rowids"]) == 4
    albums = [
        conn.execute("SELECT album_rowid FROM tracks WHERE track_rowid=?", (t,)).fetchone()[0]
        for t in big["cover_track_rowids"]
    ]
    assert len(set(albums)) == 4 and 8 not in big["cover_track_rowids"]
    assert big["created_at"] == 200 and big["updated_at"] == 200


def test_rename_bumps_updated_at(conn):
    insert_track(conn, 1)
    pid = playlists.create(conn, "A", [1], now=100)
    assert playlists.rename(conn, pid, "B", now=500) is True
    got = playlists.get(conn, pid)
    assert got["name"] == "B" and got["updated_at"] == 500


def test_delete_cascades_to_tracks(conn):
    insert_track(conn, 1)
    pid = playlists.create(conn, "A", [1])
    assert playlists.delete(conn, pid) is True
    assert rows(conn, pid) == []
    assert playlists.get(conn, pid) is None


def test_deleting_a_track_keeps_the_playlist(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    pid = playlists.create(conn, "A", [1, 2])
    conn.execute("DELETE FROM tracks WHERE track_rowid = 1")
    assert rows(conn, pid) == [(1, 2)]
    assert playlists.get(conn, pid) is not None


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
    app = create_app(config)
    with TestClient(app) as c:
        insert_track(app.state.conn, 1, track_name="One")
        insert_track(app.state.conn, 2, track_name="Two")
        app.state.conn.commit()
        yield c


def test_routes_create_validation(client):
    ok = client.post("/api/playlists", json={"name": "  Mix ", "track_rowids": [2, 1]})
    assert ok.status_code == 201
    assert ok.json()["name"] == "Mix"
    assert client.post("/api/playlists", json={"name": "", "track_rowids": [1]}).status_code == 422
    assert client.post("/api/playlists", json={"name": "x", "track_rowids": []}).status_code == 422
    assert client.post(
        "/api/playlists", json={"name": "x", "track_rowids": [1] * 101}).status_code == 422


def test_routes_lifecycle(client):
    pid = client.post("/api/playlists", json={"name": "Mix", "track_rowids": [2, 1]}).json()["id"]
    assert [p["id"] for p in client.get("/api/playlists").json()] == [pid]
    detail = client.get(f"/api/playlists/{pid}").json()
    assert [t["track_rowid"] for t in detail["tracks"]] == [2, 1]
    assert client.get("/api/playlists/9999").status_code == 404
    assert client.patch(f"/api/playlists/{pid}", json={"name": "New"}).status_code == 200
    assert client.get(f"/api/playlists/{pid}").json()["name"] == "New"
    assert client.delete(f"/api/playlists/{pid}").status_code == 204
    assert client.delete(f"/api/playlists/{pid}").status_code == 404
