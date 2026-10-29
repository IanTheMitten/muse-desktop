"""The lyric tag batch: linking, tagging, failure handling, and the one-time
import (docs/specs/2026-10-12-lyric-tag-store.md D4-D6). No network: `ask`
is a fake throughout."""

import fcntl
import json
import os

from muse import lyric_tag_batch as B
from muse import lyric_tags as L
from muse.library import embeddings as library_embeddings
from muse.library import jobs as job_rows
from muse.library import lyric_tags as store
from muse.worker import WorkerReplyError
from tests.conftest import insert_track

TEXT = "\n".join(["we sing it loud tonight", "hands up to the sky now",
                  "nothing gonna stop us", "this is where we belong"] * 2
                 + [f"distinct verse line number {i} goes" for i in range(14)])
V = L.TAXONOMY_VERSION


def _track(conn, tmp_path, rowid, text=TEXT, mbid=None, ehash="E"):
    insert_track(conn, rowid)
    path = tmp_path / f"{rowid}.lyrics.txt"
    path.write_text(text, encoding="utf-8")
    conn.execute("UPDATE tracks SET lyrics_path = ?, recording_mbid = ? WHERE track_rowid = ?",
                 (str(path), mbid, rowid))
    library_embeddings.upsert(conn, rowid, audio_vec=b"a" * 4, feature_vec=None,
                              lyrics_vec=b"l" * 4, audio_model="clap",
                              lyrics_model="minilm", lyrics_hash=ehash)
    return path


def ask(state, groups):
    return {g: {k: 1.0 / len(labels) for k in labels} for g, (_, labels) in groups.items()}


def test_track_lines_prefers_the_sibling_lrc(tmp_path):
    path = tmp_path / "x.lyrics.txt"
    path.write_text("plain words only here")
    assert B.track_lines(str(path)) == [(None, "plain words only here")]
    (tmp_path / "x.lrc").write_text("[00:01.00] timed words here")
    assert B.track_lines(str(path)) == [(1000, "timed words here")]


def test_run_tags_pending_and_stamps_the_embeddings_hash(conn, tmp_path):
    _track(conn, tmp_path, 1, ehash="E1")
    job = B.run(conn, ask, "jev-test")
    assert job["kind"] == B.LYRIC_TAG and job["state"] == job_rows.DONE
    assert "1 tagged" in job["message"]
    assert store.pending(conn, V) == []
    stored = store.find_set(conn, track_rowid=1)
    assert (stored["lyrics_hash"], stored["model"], stored["taxonomy_version"]) == ("E1", "jev-test", V)
    assert stored["text_hash"] == L.text_hash(L.lines_from_text(TEXT))
    assert "song" in store.read(conn, [1])[1]


def test_a_lyricless_file_becomes_no_lines(conn, tmp_path):
    _track(conn, tmp_path, 1, text="[Chorus]\n(yeah)\nOh oh oh")
    job = B.run(conn, ask, "jev-test")
    assert "1 without lyric lines" in job["message"]
    assert store.find_set(conn, track_rowid=1)["state"] == "no_lines"
    assert store.pending(conn, V) == []


def test_one_failing_song_does_not_stop_the_batch(conn, tmp_path):
    _track(conn, tmp_path, 1)
    _track(conn, tmp_path, 2, text=TEXT + "\nunique last line for two")

    def flaky(state, groups):
        if "unique last line for two" in state.get("lyrics", ""):
            raise WorkerReplyError("boom")
        return ask(state, groups)

    job = B.run(conn, flaky, "jev-test")
    assert job["state"] == job_rows.DONE and "1 failed" in job["message"]
    assert store.pending(conn, V) == [2]


def test_an_outage_ends_the_run_as_error(conn, tmp_path):
    for rid in range(1, B.MAX_CONSECUTIVE_FAILURES + 3):
        _track(conn, tmp_path, rid)

    def down(state, groups):
        raise WorkerReplyError("down")

    job = B.run(conn, down, "jev-test")
    assert job["state"] == job_rows.ERROR
    assert "in a row" in job["message"]


def test_run_reaps_an_orphaned_job(conn):
    orphan = job_rows.create(conn, B.LYRIC_TAG)
    job_rows.update(conn, orphan, state=job_rows.RUNNING)
    B.run(conn, ask, "jev-test")
    assert job_rows.get(conn, orphan)["state"] == job_rows.ERROR


def _unlinked(conn, mbid, text_hash):
    return store.write_set(conn, track_rowid=None, recording_mbid=mbid, state="tagged",
                           text_hash=text_hash, lyrics_hash=None, model="m",
                           tags={"song": {"mode": {"story": 1.0}}}, taxonomy_version=V)


def test_link_keeps_same_lyrics_and_drops_different(conn, tmp_path):
    _unlinked(conn, "mb-a", L.text_hash(L.lines_from_text(TEXT)))
    _unlinked(conn, "mb-b", "different")
    _track(conn, tmp_path, 1, mbid="mb-a", ehash="EA")
    _track(conn, tmp_path, 2, mbid="mb-b")
    assert B.link_pending(conn) == {"linked": 1, "dropped": 1}
    assert store.find_set(conn, track_rowid=1)["lyrics_hash"] == "EA"
    assert store.find_set(conn, recording_mbid="mb-b") is None
    assert store.pending(conn, V) == [2]


def test_run_links_before_tagging(conn, tmp_path):
    _unlinked(conn, "mb-a", L.text_hash(L.lines_from_text(TEXT)))
    _track(conn, tmp_path, 1, mbid="mb-a")
    calls = []

    def counting(state, groups):
        calls.append(1)
        return ask(state, groups)

    job = B.run(conn, counting, "jev-test")
    assert calls == [] and "1 linked" in job["message"]


def _file(directory, key, lines, *, state="tagged", mbid=None, version=V):
    data = {"key": key, "recording_mbid": mbid, "lyrics_hash": L.text_hash(lines),
            "taxonomy_version": version, "state": state,
            "song_level": {"mode": {"story": 1.0}},
            "answers": {"start": {"outlook": {"despair": 1.0}}}}
    if state == "no_lines":
        data.pop("song_level"); data.pop("answers")
    (directory / f"{key}.json").write_text(json.dumps(data))


def test_import_links_lib_files_holds_mb_files_and_is_idempotent(conn, tmp_path):
    src = tmp_path / "tags"
    src.mkdir()
    _track(conn, tmp_path, 1, ehash="E1")
    lines = L.lines_from_text(TEXT)
    _file(src, "lib-1", lines)
    _file(src, "mb-zzz", lines, mbid="zzz")
    _file(src, "lib-999", lines)                 # no such track
    _file(src, "mb-old", lines, mbid="old", version=2)  # wrong taxonomy

    assert B.import_dir(conn, src) == {"imported": 2, "skipped": 2, "linked": 0, "dropped": 0}
    assert store.read(conn, [1])[1] == {"song": {"mode": {"story": 1.0}},
                                        "start": {"outlook": {"despair": 1.0}}}
    assert store.find_set(conn, track_rowid=1)["lyrics_hash"] == "E1"
    assert store.find_set(conn, recording_mbid="zzz")["track_rowid"] is None
    assert B.import_dir(conn, src)["imported"] == 0


def test_import_skips_a_lib_file_whose_lyrics_changed(conn, tmp_path):
    src = tmp_path / "tags"
    src.mkdir()
    _track(conn, tmp_path, 1)
    _file(src, "lib-1", L.lines_from_text("some other lyrics entirely here"))
    assert B.import_dir(conn, src)["skipped"] == 1
    assert store.pending(conn, V) == [1]


def test_import_of_a_no_lines_file(conn, tmp_path):
    src = tmp_path / "tags"
    src.mkdir()
    _track(conn, tmp_path, 1, text="(yeah)\nOh oh oh")
    _file(src, "lib-1", L.lines_from_text("(yeah)\nOh oh oh"), state="no_lines")
    assert B.import_dir(conn, src)["imported"] == 1
    assert store.find_set(conn, track_rowid=1)["state"] == "no_lines"
    assert store.pending(conn, V) == []


def test_import_links_an_mb_file_whose_track_already_landed(conn, tmp_path):
    src = tmp_path / "tags"
    src.mkdir()
    _track(conn, tmp_path, 1, mbid="zzz", ehash="EZ")
    _file(src, "mb-zzz", L.lines_from_text(TEXT), mbid="zzz")
    assert B.import_dir(conn, src) == {"imported": 1, "skipped": 0, "linked": 1, "dropped": 0}
    assert store.find_set(conn, track_rowid=1)["lyrics_hash"] == "EZ"


def test_main_exits_3_when_the_lock_is_held(monkeypatch, tmp_path):
    db = tmp_path / "library.sqlite3"
    monkeypatch.setenv("MUSE_LIBRARY_DB", str(db))
    from muse.library import connect
    connect(db).close()
    fd = os.open(B.lock_path(db), os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert B.main([]) == B.LOCK_HELD_EXIT
    finally:
        os.close(fd)
