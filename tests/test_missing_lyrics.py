"""The missing-lyrics list, the "no words" mark and their routes."""

import pytest
from fastapi import HTTPException

from muse.library import lyrics_state
from muse.api import LyricsWrite
from tests.conftest import insert_track
from tests.conftest import make_config
from tests.test_api_recommend import app as _app, route  # noqa: F401


@pytest.fixture
def app(_app, tmp_path):
    _app.state.config = make_config(tmp_path)
    return _app


def put_route(app, path):
    for candidate in app.routes:
        if getattr(candidate, "path", None) == path and "PUT" in candidate.methods:
            return candidate.endpoint
    raise AssertionError(f"no PUT route {path}")


def _state(conn, rowid, state="not_found"):
    lyrics_state.upsert(conn, rowid, state=state, synced=False, checked_at=1)


def _seed(conn):
    insert_track(conn, 1, artist_name="B", album_name="X", track_name="t")
    insert_track(conn, 2, artist_name="A", album_name="Z", track_name="t")
    insert_track(conn, 3, artist_name="A", album_name="Y", track_name="t")
    for rowid in (1, 2, 3):
        _state(conn, rowid)
    insert_track(conn, 4)
    _state(conn, 4)
    conn.execute("UPDATE tracks SET lyrics_path = 'x.txt' WHERE track_rowid = 4")
    insert_track(conn, 5)
    _state(conn, 5)
    conn.execute("UPDATE tracks SET is_instrumental = 1 WHERE track_rowid = 5")
    insert_track(conn, 6)
    _state(conn, 6, "plain")
    insert_track(conn, 7)
    insert_track(conn, 8, status="missing_file")
    _state(conn, 8)
    conn.commit()


def test_missing_filters_and_orders(conn):
    _seed(conn)
    assert lyrics_state.missing(conn) == [3, 2, 1]


def test_mark_no_words(conn):
    _seed(conn)
    assert lyrics_state.mark_no_words(conn, 3, now=5) is True
    assert lyrics_state.missing(conn) == [2, 1]
    assert lyrics_state.get_state(conn, 3) == "declared_instrumental"
    assert lyrics_state.mark_no_words(conn, 999) is False


def test_routes_and_dashboard(app):
    conn = app.state.conn
    _seed(conn)
    body = route(app, "/api/library/lyrics/missing")()
    assert body["count"] == 3
    assert [t["track_rowid"] for t in body["tracks"]] == [3, 2, 1]
    assert route(app, "/api/dashboard")()["missing_lyrics"] == 3
    no_words = route(app, "/api/tracks/{track_rowid}/lyrics/no-words")
    assert no_words(3) == {"ok": True}
    assert route(app, "/api/library/lyrics/missing")()["count"] == 2
    with pytest.raises(HTTPException) as err:
        no_words(999)
    assert err.value.status_code == 404


def test_saving_lyrics_removes_it(app):
    conn = app.state.conn
    _seed(conn)
    put_route(app, "/api/tracks/{track_rowid}/lyrics")(1, LyricsWrite(text="la la"))
    assert 1 not in lyrics_state.missing(conn)
