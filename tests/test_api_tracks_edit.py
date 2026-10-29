import pytest
from fastapi.testclient import TestClient

from muse.api import create_app
from tests.conftest import insert_track, make_config


@pytest.fixture
def client(tmp_path):
    config = make_config(tmp_path)
    app = create_app(config)
    with TestClient(app) as c:
        insert_track(app.state.conn, 1, track_name="One", added_at=100)
        insert_track(app.state.conn, 2, track_name="Two", added_at=200)
        app.state.conn.commit()
        c.config = config
        yield c


def names(c, **params):
    return [s["track_name"] for s in c.get("/api/library/songs", params=params).json()]


def test_details_edit_reset_and_errors(client):
    r = client.patch("/api/tracks/details", json={"track_rowids": [1], "values": {"track_name": " Uno "}})
    assert r.status_code == 200
    assert "Uno" in names(client)
    assert client.patch("/api/tracks/details", json={"track_rowids": [99], "values": {"track_name": "x"}}).status_code == 404
    assert client.patch("/api/tracks/details", json={"track_rowids": [1], "values": {}}).status_code == 422
    assert client.patch("/api/tracks/details", json={"track_rowids": [1], "values": {"track_no": 0}}).status_code == 422
    assert client.post("/api/tracks/details/reset", json={"track_rowids": [1]}).status_code == 200


def test_remove_and_restore(client):
    assert client.post("/api/tracks/remove", json={"track_rowids": [1]}).json()["removed"] == [1]
    assert "One" not in names(client)
    assert [t["track_rowid"] for t in client.get("/api/library/removed").json()] == [1]
    assert client.post("/api/tracks/restore", json={"track_rowids": [1]}).status_code == 200
    assert client.get("/api/library/removed").json() == []
    assert "One" in names(client)


def test_sort(client):
    assert names(client, sort="added", order="desc") == ["Two", "One"]
    assert client.get("/api/library/songs", params={"sort": "bogus"}).status_code == 422


def test_duplicates(client):
    insert_track(client.app.state.conn, 3, track_name="One", added_at=300)
    client.app.state.conn.commit()
    groups = client.get("/api/library/duplicates").json()["groups"]
    assert len(groups) == 1 and len(groups[0]) == 2


def test_folder_containing_data_dir_refused(client):
    parent = client.config.data_dir.parent
    r = client.put("/api/settings/music_folders", json={"folders": [str(parent)]})
    assert r.status_code == 400
    assert "contains the Muse data folder" in r.json()["detail"]
