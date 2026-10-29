"""Remove / restore / trash (muse/library/removal.py) and the playlist tombstone rule."""

from muse.library import playlists, queries, removal
from tests.conftest import insert_track
from tests.test_scanner import env, ft


def status(conn, rowid):
    return conn.execute(
        "SELECT status FROM tracks WHERE track_rowid = ?", (rowid,)).fetchone()[0]


def test_tombstone_hides_everywhere_keeps_plays(conn):
    insert_track(conn, 1, track_name="Findme")
    insert_track(conn, 2)
    pid = playlists.create(conn, "P", [1, 2])
    conn.execute(
        "INSERT INTO plays (track_rowid, started_at, ended_reason, reached_ms, duration_ms, heartbeat_at) VALUES (1, 1, 'track_end', 0, 1000, 1)")
    assert queries.search(conn, "Findme")
    res = removal.remove(conn, [1], trash=False, now=500)
    assert res == {"removed": [1], "failed": []}
    assert status(conn, 1) == "removed"
    assert conn.execute("SELECT removed_at FROM tracks WHERE track_rowid = 1").fetchone()[0] == 500
    assert 1 not in queries.playable_rowids(conn)
    assert 1 not in {t["track_rowid"] for t in queries.songs(conn)}
    assert not queries.search(conn, "Findme")
    p = playlists.get(conn, pid)
    assert [t["track_rowid"] for t in p["tracks"]] == [2]
    assert p["track_count"] == 1
    assert [s["track_count"] for s in playlists.summaries(conn)] == [1]
    assert conn.execute("SELECT COUNT(*) FROM plays WHERE track_rowid = 1").fetchone()[0] == 1
    assert [t["track_rowid"] for t in removal.removed(conn)] == [1]


def test_restore_returns_to_playlist_as_missing_file(conn):
    insert_track(conn, 1)
    pid = playlists.create(conn, "P", [1])
    removal.remove(conn, [1], trash=False)
    assert removal.restore(conn, [1]) == 1
    assert status(conn, 1) == "missing_file"
    assert [t["track_rowid"] for t in playlists.get(conn, pid)["tracks"]] == [1]
    assert removal.restore(conn, [1]) == 0


def test_trash_sends_path_then_tombstones(conn, tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    insert_track(conn, 1, file_path=str(f))
    sent = []
    res = removal.remove(conn, [1], trash=True, send=sent.append)
    assert sent == [str(f)] and res["removed"] == [1]
    assert status(conn, 1) == "removed"


def test_trash_oserror_leaves_track(conn, tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    insert_track(conn, 1, file_path=str(f))

    def busy(path):
        raise OSError("busy")

    res = removal.remove(conn, [1], trash=True, send=busy)
    assert res == {"removed": [], "failed": [{"track_rowid": 1, "error": "busy"}]}
    assert status(conn, 1) == "done"


def test_trash_missing_file_just_tombstones(conn, tmp_path):
    insert_track(conn, 1, file_path=str(tmp_path / "gone.mp3"))
    sent = []
    res = removal.remove(conn, [1], trash=True, send=sent.append)
    assert sent == [] and res["removed"] == [1]


def test_unknown_or_already_removed_fails(conn):
    insert_track(conn, 1)
    removal.remove(conn, [1], trash=False)
    res = removal.remove(conn, [1, 99], trash=False)
    assert [f["error"] for f in res["failed"]] == ["No track 1", "No track 99"]


def test_scan_after_restore_flips_to_done(tmp_path, conn):
    e = env(tmp_path, conn)
    e.file("a.mp3")
    e.tags["a.mp3"] = ft()
    e.scan()
    (row,) = e.rows()
    rid = row["track_rowid"]
    removal.remove(conn, [rid], trash=False)
    assert e.scan().skipped_removed == 1
    removal.restore(conn, [rid])
    e.scan()
    assert status(conn, rid) == "done"


def test_append_keeps_order(conn):
    for i in (1, 2, 3):
        insert_track(conn, i)
    pid = playlists.create(conn, "P", [1, 2], now=1)
    assert playlists.append(conn, pid, [3, 1], now=9) is True
    assert [t["track_rowid"] for t in playlists.get(conn, pid)["tracks"]] == [1, 2, 3, 1]
    assert playlists.get(conn, pid)["updated_at"] == 9
    assert playlists.append(conn, 999, [1]) is False
