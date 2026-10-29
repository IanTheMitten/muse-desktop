import os

import pytest

from muse.library.queries import search
from muse.scanner import scan
from muse.tags import FileTags


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


def test_new_file_inserts_row(tmp_path, conn):
    e = env(tmp_path, conn)
    p = e.file("a.mp3")
    e.tags["a.mp3"] = ft()
    r = e.scan()
    (row,) = e.rows()
    assert r.added == 1
    assert (row["origin"], row["metadata_tier"], row["status"], row["format"]) == (
        "local", "tags", "done", "mp3")
    assert row["file_path"] == str(p) and os.path.isabs(row["file_path"])
    assert row["file_size"] == p.stat().st_size
    assert row["file_mtime"] == int(p.stat().st_mtime)
    assert row["track_rowid"] >= 1_000_000_000
    assert row["added_at"] == 1000
    assert row["artist_names"] == '["Artist"]'


def test_year_stored(tmp_path, conn):
    e = env(tmp_path, conn)
    e.file("a.mp3")
    e.tags["a.mp3"] = ft(year=2001)
    e.scan()
    (row,) = e.rows()
    assert row["year"] == 2001


def test_missing_tags_fall_back(tmp_path, conn):
    e = env(tmp_path, conn)
    e.file("stem name.mp3")
    e.tags["stem name.mp3"] = ft(title=None, artist=None, album=None, album_artist=None)
    e.scan()
    (row,) = e.rows()
    assert row["track_name"] == "stem name"
    assert row["artist_name"] == "Unknown artist"
    assert row["album_name"] == "Unknown album"


def test_same_album_shares_rowid(tmp_path, conn):
    e = env(tmp_path, conn)
    for n in ("a.mp3", "b.mp3", "c.mp3"):
        e.file(n)
    e.tags["a.mp3"] = ft(album_artist="Band", album="Rec")
    e.tags["b.mp3"] = ft(album_artist="BAND", album="rec")
    e.tags["c.mp3"] = ft(album_artist="Band", album="Other")
    e.scan()
    a, b, c = (r["album_rowid"] for r in e.rows())
    assert a == b and a != c


def test_rescan_unchanged(tmp_path, conn):
    e = env(tmp_path, conn)
    e.file("a.mp3")
    e.tags["a.mp3"] = ft()
    e.scan()
    before = [dict(r) for r in e.rows()]
    r = e.scan()
    assert r.unchanged == 1 and r.added == 0 and r.updated == 0
    assert [dict(x) for x in e.rows()] == before


def test_changed_size_updates(tmp_path, conn):
    e = env(tmp_path, conn)
    e.file("a.mp3")
    e.tags["a.mp3"] = ft()
    e.scan()
    rid = e.rows()[0]["track_rowid"]
    e.file("a.mp3", b"abcdef")
    e.tags["a.mp3"] = ft(title="New")
    r = e.scan()
    (row,) = e.rows()
    assert r.updated == 1
    assert row["track_rowid"] == rid and row["track_name"] == "New"


def test_rename_keeps_identity(tmp_path, conn):
    e = env(tmp_path, conn)
    e.file("a.mp3")
    e.tags["a.mp3"] = e.tags["b.mp3"] = ft()
    e.scan()
    rid = e.rows()[0]["track_rowid"]
    conn.execute(
        "INSERT INTO plays (track_rowid, started_at, reached_ms, duration_ms, heartbeat_at)"
        " VALUES (?, 1, 1, 1, 1)", (rid,))
    os.rename(e.music / "a.mp3", e.music / "b.mp3")
    r = e.scan()
    (row,) = e.rows()
    assert r.moved == 1
    assert row["track_rowid"] == rid
    assert row["file_path"] == str(e.music / "b.mp3")
    assert conn.execute("SELECT track_rowid FROM plays").fetchone()[0] == rid


def test_deleted_file_is_missing(tmp_path, conn):
    e = env(tmp_path, conn)
    e.file("a.mp3")
    e.file("b.mp3")
    e.tags["a.mp3"] = ft()
    e.tags["b.mp3"] = ft(title="B")
    e.scan()
    (e.music / "a.mp3").unlink()
    r = e.scan()
    assert r.missing == 1
    assert sorted(x["status"] for x in e.rows()) == ["done", "missing_file"]


def test_absent_folder_marks_missing_then_restores(tmp_path, conn):
    e = env(tmp_path, conn)
    e.file("a.mp3")
    e.tags["a.mp3"] = ft()
    e.scan()
    ids = [r["track_rowid"] for r in e.rows()]
    gone = tmp_path / "gone"
    e.music.rename(gone)
    e.scan()
    assert [r["status"] for r in e.rows()] == ["missing_file"]
    gone.rename(e.music)
    e.scan()
    assert [r["status"] for r in e.rows()] == ["done"]
    assert [r["track_rowid"] for r in e.rows()] == ids


def test_removed_row_is_skipped(tmp_path, conn):
    e = env(tmp_path, conn)
    e.file("a.mp3")
    e.tags["a.mp3"] = ft()
    e.scan()
    conn.execute("UPDATE tracks SET removed_at = 5")
    r = e.scan()
    assert r.skipped_removed == 1
    assert len(e.rows()) == 1


def test_unreadable(tmp_path, conn):
    e = env(tmp_path, conn)
    e.file("a.mp3")
    r = e.scan()
    assert r.unreadable == 1 and e.rows() == []


def test_lrc_copied_in(tmp_path, conn):
    e = env(tmp_path, conn)
    e.file("song.mp3")
    (e.music / "song.lrc").write_text("[00:01.00]hello\n[00:02.50]world\n")
    e.tags["song.mp3"] = ft()
    e.scan()
    (row,) = e.rows()
    rid = row["track_rowid"]
    assert (e.lyrics / f"{rid}.lrc").read_text() == "[00:01.00]hello\n[00:02.50]world\n"
    txt = (e.lyrics / f"{rid}.txt").read_text()
    assert "hello" in txt and "world" in txt and "[00:" not in txt
    assert row["lyrics_path"] == str(e.lyrics / f"{rid}.txt")
    st = conn.execute("SELECT state FROM lyrics_state WHERE track_rowid=?", (rid,)).fetchone()
    assert st["state"] == "synced"


def test_txt_copied_in_plain(tmp_path, conn):
    e = env(tmp_path, conn)
    e.file("song.mp3")
    (e.music / "song.txt").write_text("just words")
    e.tags["song.mp3"] = ft()
    e.scan()
    (row,) = e.rows()
    rid = row["track_rowid"]
    assert (e.lyrics / f"{rid}.txt").read_text() == "just words"
    st = conn.execute("SELECT state FROM lyrics_state WHERE track_rowid=?", (rid,)).fetchone()
    assert st["state"] == "plain"


def test_embedded_art_written(tmp_path, conn):
    e = env(tmp_path, conn)
    e.file("a.mp3")
    e.tags["a.mp3"] = ft()
    e.pics["a.mp3"] = (b"img", ".png")
    e.scan()
    (row,) = e.rows()
    out = e.art / f"{row['album_rowid']}.png"
    assert out.read_bytes() == b"img"
    a = conn.execute("SELECT source FROM album_art WHERE album_rowid=?",
                     (row["album_rowid"],)).fetchone()
    assert a["source"] == "embedded"
    assert row["art_path"]


def test_hidden_and_non_audio_ignored(tmp_path, conn):
    e = env(tmp_path, conn)
    e.file(".song.mp3")
    e.file("cover.jpg")
    e.tags[".song.mp3"] = ft()
    e.tags["cover.jpg"] = ft()
    r = e.scan()
    assert e.rows() == [] and r.added == 0


def test_scanned_track_is_searchable(tmp_path, conn):
    e = env(tmp_path, conn)
    e.file("a.mp3")
    e.tags["a.mp3"] = ft()
    e.scan()
    assert any(t["track_name"] == "Song" for t in search(conn, "Song"))


def test_music_folder_untouched(tmp_path, conn):
    e = env(tmp_path, conn)
    e.file("a.mp3", b"one")
    e.file("b.mp3", b"two")
    (e.music / "a.lrc").write_text("[00:01.00]x")
    e.tags["a.mp3"] = ft()
    e.tags["b.mp3"] = ft(title="B")
    snap = lambda: (sorted(os.listdir(e.music)),
                    {n: (e.music / n).read_bytes() for n in sorted(os.listdir(e.music))})
    before = snap()
    e.scan()
    assert snap() == before


def test_dangling_symlink_does_not_abort(tmp_path, conn):
    e = env(tmp_path, conn)
    e.file("a.mp3")
    e.tags["a.mp3"] = ft()
    (e.music / "b.mp3").symlink_to(tmp_path / "nowhere.mp3")
    r = e.scan()
    assert r.added == 1 and r.unreadable == 1


def test_unreadable_lrc_keeps_row(tmp_path, conn):
    if os.geteuid() == 0:
        pytest.skip("root ignores file modes")
    e = env(tmp_path, conn)
    e.file("c.mp3")
    lrc = e.music / "c.lrc"
    lrc.write_text("[00:01.00]x")
    lrc.chmod(0)
    e.tags["c.mp3"] = ft()
    try:
        r = e.scan()
    finally:
        lrc.chmod(0o644)
    (row,) = e.rows()
    assert r.added == 1
    assert conn.execute("SELECT 1 FROM lyrics_state WHERE track_rowid=?",
                        (row["track_rowid"],)).fetchone() is None


def test_overlapping_folders_scan_once(tmp_path, conn):
    e = env(tmp_path, conn)
    (e.music / "sub").mkdir()
    (e.music / "sub" / "a.mp3").write_bytes(b"abc")
    e.tags["a.mp3"] = ft()
    r = e.scan([e.music, e.music / "sub"])
    assert r.added == 1 and len(e.rows()) == 1
