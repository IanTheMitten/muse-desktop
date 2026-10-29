import asyncio

from muse import jobs
from muse.library import jobs as job_rows
from muse import tags as _tags


def _run(conn, folders, tmp_path, **kw):
    return asyncio.run(
        jobs.run_scan_job(
            conn, folders, jobs.JobBus(),
            lyrics_root=tmp_path / "l", art_root=tmp_path / "a", **kw,
        )
    )


def test_scan_job_done(conn, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "a.mp3").write_bytes(b"x")
    fake = lambda p: _tags.FileTags(title="T", artist="A", album="B", album_artist=None, track_no=1, disc_no=1, isrc=None, duration_ms=1000)
    row = _run(conn, [lib], tmp_path, read_tags=fake)
    assert row["state"] == "done"
    assert row["message"] == "1 added, 0 moved, 0 missing"


def test_scan_job_missing_folder(conn, tmp_path):
    assert _run(conn, [tmp_path / "nope"], tmp_path) is None
    assert conn.execute("select count(*) from jobs").fetchone()[0] == 0


def test_scan_job_already_active(conn, tmp_path):
    (tmp_path / "lib").mkdir()
    job_rows.create(conn, jobs.SCAN, {})
    assert _run(conn, [tmp_path / "lib"], tmp_path) is None
