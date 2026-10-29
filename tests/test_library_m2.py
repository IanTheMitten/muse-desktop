import pytest

from muse.library import artists as lib_artists
from muse.library import queries
from tests.conftest import insert_track

PLAY = ("INSERT INTO plays (track_rowid, started_at, reached_ms, duration_ms, heartbeat_at)"
        " VALUES (?, 0, 0, 1000, 0)")


def _ids(out):
    return [t["track_rowid"] for t in out]


def _seed(conn):
    insert_track(conn, 1, track_name="b", added_at=20, duration_ms=200)
    insert_track(conn, 2, track_name="a", added_at=10, duration_ms=100)
    insert_track(conn, 3, track_name="c", added_at=30, duration_ms=None)
    conn.execute("UPDATE tracks SET year = 1999 WHERE track_rowid = 1")
    conn.execute("UPDATE tracks SET year = 2005 WHERE track_rowid = 2")
    conn.execute(PLAY, (1,))
    conn.execute(PLAY, (1,))
    conn.execute(PLAY, (2,))


def test_sorts_with_nulls_last(conn):
    _seed(conn)
    assert _ids(queries.songs(conn)) == [2, 1, 3]
    assert _ids(queries.songs(conn, "title", "desc")) == [3, 1, 2]
    assert _ids(queries.songs(conn, "added")) == [2, 1, 3]
    assert _ids(queries.songs(conn, "added", "desc")) == [3, 1, 2]
    assert _ids(queries.songs(conn, "plays")) == [3, 2, 1]
    assert _ids(queries.songs(conn, "plays", "desc")) == [1, 2, 3]
    assert _ids(queries.songs(conn, "year")) == [1, 2, 3]
    assert _ids(queries.songs(conn, "year", "desc")) == [2, 1, 3]
    assert _ids(queries.songs(conn, "duration")) == [2, 1, 3]
    assert _ids(queries.songs(conn, "duration", "desc")) == [1, 2, 3]
    assert queries.songs(conn)[1]["year"] == 1999


def test_bad_sort_raises(conn):
    with pytest.raises(ValueError):
        queries.songs(conn, "nope")
    with pytest.raises(ValueError):
        queries.songs(conn, "title", "sideways")


def test_duplicates(conn):
    insert_track(conn, 1, track_name="Song", artist_name="A", duration_ms=180_000)
    insert_track(conn, 2, track_name="song", artist_name="a", duration_ms=181_500)
    insert_track(conn, 3, track_name="Song", artist_name="A", duration_ms=184_000)
    insert_track(conn, 4, track_name="Song", artist_name="B", duration_ms=180_000)
    insert_track(conn, 5, track_name="Song", artist_name="A", duration_ms=None)
    assert [_ids(c) for c in queries.duplicates(conn)] == [[1, 2]]


def test_hidden_artist_and_image(conn):
    insert_track(conn, 1, artist_name="Gone", album_rowid=1)
    insert_track(conn, 2, artist_name="Kept", album_rowid=2)
    lib_artists.hide(conn, "Gone", True)
    assert [a["artist_name"] for a in queries.artists(conn)] == ["Kept"]
    assert len(queries.songs(conn)) == 2
    assert {a["album_rowid"] for a in queries.albums(conn)} == {1, 2}
    assert [h["artist_name"] for h in lib_artists.hidden(conn)] == ["Gone"]
    lib_artists.hide(conn, "Gone", False)
    assert len(queries.artists(conn)) == 2
    assert queries.artists(conn)[0]["has_image"] is False
    lib_artists.set_image(conn, "Kept", "x.jpg")
    kept = [a for a in queries.artists(conn) if a["artist_name"] == "Kept"][0]
    assert kept["has_image"] is True
    assert lib_artists.image_path(conn, "Kept") == "x.jpg"
    assert lib_artists.image_path(conn, "Gone") is None


def test_tracks_by_rowid_drops_removed(conn):
    insert_track(conn, 1)
    insert_track(conn, 2, status="removed")
    assert _ids(queries.tracks_by_rowid(conn, [2, 1])) == [1]
