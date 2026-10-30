"""The align job runner and the timing writer (red until Task 12)."""

import pytest

from muse.align_text import build_lrc
from muse.jobs import JobBus, run_align_job
from muse.lyrics import lyrics_files
from muse.library import jobs as job_rows
from muse.library import lyrics_state
from muse.library.lyrics_edit import write_timings
from muse.worker import WorkerReplyError
from tests.conftest import insert_track

LINES = ["one", "two", "three"]
SECONDS = [1.0, 2.5, 4.0]
MS = [1000, 2500, 4000]


def _track(rowid):
    return {"artist_name": "A", "track_name": f"T{rowid}", "album_name": "B"}


def _item(rowid):
    return {"track_rowid": rowid, "file_path": f"/x/{rowid}.m4a", "lines": LINES,
            **_track(rowid)}


async def _run(conn, tmp_path, rowids, align, skipped_synced=0):
    for rowid in rowids:
        insert_track(conn, rowid)
    job_id = job_rows.create(conn, "align", {
        "track_rowids": rowids, "total": len(rowids), "done": 0,
        "aligned": [], "failed": [], "skipped_synced": skipped_synced})
    await run_align_job(conn, JobBus(), job_id, [_item(r) for r in rowids], align,
                        lyrics_root=tmp_path / "lyrics")
    return job_rows.get(conn, job_id)


def good(path, lines):
    return {"starts": SECONDS}


def unusable(path, lines):
    return {"starts": [None, None, None]}


def boom(path, lines):
    raise WorkerReplyError("no")


async def test_two_good_tracks_are_written_and_marked_aligned(conn, tmp_path):
    for rowid in (1, 2):
        insert_track(conn, rowid)
        lyrics_state.upsert(conn, rowid, state="lrclib", synced=False,
                            checked_at=0, lrc_offset_ms=250)
    job_id = job_rows.create(conn, "align", {
        "track_rowids": [1, 2], "total": 2, "done": 0,
        "aligned": [], "failed": [], "skipped_synced": 0})
    await run_align_job(conn, JobBus(), job_id, [_item(1), _item(2)], good,
                        lyrics_root=tmp_path / "lyrics")
    for rowid in (1, 2):
        lrc = lyrics_files(tmp_path / "lyrics", rowid)[1]
        assert lrc.read_text(encoding="utf-8") == build_lrc(LINES, MS, _track(rowid))
        row = conn.execute(
            "SELECT state, synced, lrc_offset_ms FROM lyrics_state WHERE track_rowid = ?",
            (rowid,)).fetchone()
        assert (row["state"], row["synced"], row["lrc_offset_ms"]) == ("aligned", 1, 0)
    job = job_rows.get(conn, job_id)
    assert job["state"] == "done"
    assert job["payload"]["aligned"] == [1, 2]
    assert job["message"] == "Aligned 2 of 2"


async def test_unusable_and_raising_tracks_fail(conn, tmp_path):
    def align(path, lines):
        return unusable(path, lines) if path == "/x/1.m4a" else boom(path, lines)

    job = await _run(conn, tmp_path, [1, 2], align)
    for rowid in (1, 2):
        assert not lyrics_files(tmp_path / "lyrics", rowid)[1].exists()
    assert job["payload"]["failed"] == [1, 2]
    assert job["message"] == "Aligned 0 of 2, couldn't align 2"


async def test_unexpected_error_fails_that_track_only(conn, tmp_path):
    def align(path, lines):
        if path == "/x/1.m4a":
            raise KeyError("starts")
        return good(path, lines)

    job = await _run(conn, tmp_path, [1, 2], align)
    assert job["state"] == "done"
    assert job["payload"]["failed"] == [1]
    assert job["payload"]["aligned"] == [2]
    assert job["message"] == "Aligned 1 of 2, couldn't align 1"


async def test_skipped_synced_is_appended(conn, tmp_path):
    job = await _run(conn, tmp_path, [1, 2], good, skipped_synced=3)
    assert job["message"] == "Aligned 2 of 2, skipped 3 already synced"


async def test_single_failure_message(conn, tmp_path):
    job = await _run(conn, tmp_path, [1], boom)
    assert job["message"] == "Couldn't align these lyrics"


async def test_single_success_message(conn, tmp_path):
    job = await _run(conn, tmp_path, [1], good)
    assert job["message"] == "Aligned"


def test_write_timings_manual_writes_file_and_row(conn, tmp_path):
    insert_track(conn, 1)
    root = tmp_path / "lyrics"
    root.mkdir()
    write_timings(conn, 1, LINES, MS, lyrics_root=root, state="manual", track=_track(1))
    assert lyrics_files(root, 1)[1].read_text(encoding="utf-8") == build_lrc(
        LINES, MS, _track(1))
    row = conn.execute(
        "SELECT state, synced FROM lyrics_state WHERE track_rowid = 1").fetchone()
    assert (row["state"], row["synced"]) == ("manual", 1)
