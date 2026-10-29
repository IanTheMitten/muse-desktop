import dataclasses
import os

import pytest

from muse.library import overrides
from muse.library.queries import search
from muse.scanner import scan
from muse.tags import FileTags
from tests.conftest import insert_track, track_row


def ft(title="Song", artist="Artist", album="Album", album_artist="Artist",
       year=None):
    return FileTags(title=title, artist=artist, album=album, album_artist=album_artist,
                    track_no=1, disc_no=1, isrc=None, duration_ms=1000, year=year)


class Env:
    def __init__(self, tmp_path, conn):
        self.conn = conn
        self.music = tmp_path / "music"
        self.music.mkdir()
        self.lyrics = tmp_path / "lyrics"
        self.art = tmp_path / "art"
        self.lyrics.mkdir()
        self.art.mkdir()
        self.tags = {}
        self.pics = {}

    def file(self, name, data=b"abc"):
        p = self.music / name
        p.write_bytes(data)
        return p

    def scan(self, folders=None):
        return scan(
            self.conn, folders or [self.music],
            lyrics_root=self.lyrics, art_root=self.art,
            read_tags=lambda p: self.tags.get(p.name),
            embedded_picture=lambda p: self.pics.get(p.name),
            now=1000,
        )

    def rows(self):
        return self.conn.execute("SELECT * FROM tracks ORDER BY track_rowid").fetchall()


def env(tmp_path, conn):
    return Env(tmp_path, conn)


def test_write_track_name(conn):
    insert_track(conn, 1)
    assert overrides.write(conn, [1], {"track_name": "New"}) == 1
    assert track_row(conn, 1)["track_name"] == "New"
    assert overrides.get(conn, 1) == {"track_name": "New"}
    assert [r["track_rowid"] for r in search(conn, "New")] == [1]


def test_write_artist_name(conn):
    insert_track(conn, 1)
    overrides.write(conn, [1], {"artist_name": "B"})
    row = track_row(conn, 1)
    assert row["artist_names"] == '["B"]'
    assert row["album_rowid"] == 20


def test_write_album_name_joins_existing_album(tmp_path, conn):
    e = env(tmp_path, conn)
    for n in ("a.mp3", "b.mp3", "c.mp3"):
        e.file(n)
    e.tags["a.mp3"] = ft(title="A", album="X")
    e.tags["b.mp3"] = ft(title="B", album="X")
    e.tags["c.mp3"] = ft(title="C", album="Other")
    e.scan()
    a, b, c = (r["track_rowid"] for r in e.rows())
    conn.execute("UPDATE tracks SET art_path = '/art/c.jpg' WHERE track_rowid = ?", (c,))
    overrides.write(conn, [a, b], {"album_name": "Other"})
    rows = {r["track_rowid"]: r for r in e.rows()}
    for rid in (a, b):
        assert rows[rid]["album_rowid"] == rows[c]["album_rowid"]
        assert rows[rid]["art_path"] == "/art/c.jpg"


def test_write_ints(conn):
    insert_track(conn, 1)
    overrides.write(conn, [1], {"track_no": 7, "year": 1999})
    row = track_row(conn, 1)
    assert (row["track_no"], row["year"]) == (7, 1999)
    got = overrides.get(conn, 1)
    assert got == {"track_no": 7, "year": 1999}
    assert all(isinstance(v, int) for v in got.values())


def test_write_rejects_bad_input(conn):
    insert_track(conn, 1)
    with pytest.raises(ValueError):
        overrides.write(conn, [1], {"bogus": "x"})
    with pytest.raises(ValueError):
        overrides.write(conn, [1], {})


def test_override_survives_rescan(tmp_path, conn):
    e = env(tmp_path, conn)
    e.file("a.mp3")
    e.tags["a.mp3"] = ft()
    e.scan()
    rid = e.rows()[0]["track_rowid"]
    overrides.write(conn, [rid], {"track_name": "Mine"})
    e.file("a.mp3", b"abcdef")
    e.tags["a.mp3"] = dataclasses.replace(ft(title="File"), track_no=5)
    e.scan()
    (row,) = e.rows()
    assert row["track_name"] == "Mine"
    assert row["track_no"] == 5


def test_move_keeps_override(tmp_path, conn):
    e = env(tmp_path, conn)
    e.file("a.mp3")
    e.tags["a.mp3"] = e.tags["b.mp3"] = ft()
    e.scan()
    rid = e.rows()[0]["track_rowid"]
    overrides.write(conn, [rid], {"track_name": "Mine"})
    os.rename(e.music / "a.mp3", e.music / "b.mp3")
    r = e.scan()
    (row,) = e.rows()
    assert r.moved == 1
    assert row["track_rowid"] == rid
    assert row["track_name"] == "Mine"


def test_clear_restores_file_title(tmp_path, conn):
    e = env(tmp_path, conn)
    e.file("a.mp3")
    e.tags["a.mp3"] = ft(title="File")
    e.scan()
    rid = e.rows()[0]["track_rowid"]
    overrides.write(conn, [rid], {"track_name": "Mine"})
    assert overrides.clear(conn, [rid]) == 1
    assert overrides.get(conn, rid) == {}
    assert e.rows()[0]["file_mtime"] is None
    e.scan()
    assert e.rows()[0]["track_name"] == "File"


def test_apply(conn):
    insert_track(conn, 1)
    fields = {"track_name": "T", "album_name": "A", "album_key": "band\x1falbum"}
    assert overrides.apply(conn, 1, dict(fields)) == fields
    overrides.write(conn, [1], {"album_name": "Other"})
    out = overrides.apply(conn, 1, dict(fields))
    assert out["album_name"] == "Other"
    assert out["album_key"] == "band\x1fother"
