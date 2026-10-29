"""The entrypoint's job bookkeeping — the same contract
`tests/test_jobs_taste_retrain.py` covers for the nightly retrain, since
this follows that module exactly -- plus the two things that module does
not need: an advisory lock against overlapping hours-long runs, and an
error message that survives a mid-batch death.
"""

import fcntl
import os

from muse import content, content_embed_batch
from muse.library import connect
from muse.library import jobs as job_rows
from tests.conftest import insert_track


class BrokenEmbedder:
    model_name = "broken"

    def embed_many(self, items):
        raise RuntimeError("content_embed.batch exited with exit code 1")
        yield  # pragma: no cover - makes this a generator


def test_a_run_is_recorded_as_a_done_job(conn):
    insert_track(conn, 1)
    job = content_embed_batch.run(conn, content.StubContentEmbedder())
    assert job["kind"] == content.CONTENT_EMBED
    assert job["state"] == job_rows.DONE
    assert job["progress"] == 1.0
    assert "1 embedded" in job["message"]


def test_a_failed_run_is_recorded_as_an_error_job(conn):
    insert_track(conn, 1)
    job = content_embed_batch.run(conn, BrokenEmbedder())
    assert job["state"] == job_rows.ERROR
    assert "exit code 1" in job["message"]


class OneSuccessThenBrokenEmbedder:
    """Unlike `BrokenEmbedder`, which dies before yielding anything and so
    can only ever exercise the zero-succeeded case, this dies after one
    real result -- the only way to tell "died at track 1 of 2" apart from
    "died before starting" in the recorded message."""

    model_name = "half-broken"

    def embed_many(self, items):
        first_id, _path = items[0]
        yield first_id, [0.0, 0.0], None
        raise RuntimeError("content_embed.batch exited with exit code 1")


def test_a_mid_batch_failure_records_how_far_it_got(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)

    job = content_embed_batch.run(conn, OneSuccessThenBrokenEmbedder())

    assert job["state"] == job_rows.ERROR
    assert "1 of 2 embedded before failure" in job["message"]
    assert "exit code 1" in job["message"]


def test_a_run_left_behind_by_a_dead_process_is_abandoned_first(conn):
    """This is a systemd oneshot: nothing non-terminal can predate this
    process except a crashed prior run, since this process did not exist a
    moment ago to have started one."""
    stale = job_rows.create(conn, content.CONTENT_EMBED)
    job_rows.update(conn, stale, state=job_rows.RUNNING)
    content_embed_batch.run(conn, content.StubContentEmbedder())
    assert job_rows.get(conn, stale)["state"] in job_rows.TERMINAL


def test_the_queue_size_is_recorded_as_the_job_payload_at_creation(conn):
    """This only pins the payload `run` writes at `job_rows.create` time --
    it says nothing about progress moving *during* the run, despite what
    this test was earlier named. See
    `test_progress_advances_mid_run_not_just_at_the_end` below for that."""
    insert_track(conn, 1)
    insert_track(conn, 2)
    job = content_embed_batch.run(conn, content.StubContentEmbedder())
    assert job["payload"] == {"tracks": 2}


class ProgressSpyEmbedder:
    """Reads the job row back from the database before yielding each
    result, so the caller can see exactly what `progress` was mid-run --
    not just at the end, when `run` unconditionally sets it to 1.0 and a
    dead `on_progress` wiring could no longer be told apart from a live
    one."""

    model_name = "spy"

    def __init__(self, conn):
        self._conn = conn
        self.progress_before_each_item: list[float] = []

    def embed_many(self, items):
        for track_rowid, _path in items:
            job = job_rows.last(self._conn, content.CONTENT_EMBED)
            self.progress_before_each_item.append(job["progress"])
            yield track_rowid, [0.0, 0.0], None


def test_progress_advances_mid_run_not_just_at_the_end(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    embedder = ProgressSpyEmbedder(conn)

    job = content_embed_batch.run(conn, embedder)

    # Before the first item: still 0 (nothing committed yet). Before the
    # second: 0.5 -- proof the first item's on_progress callback already
    # reached the row, not just that the final update did.
    assert embedder.progress_before_each_item == [0.0, 0.5]
    assert job["state"] == job_rows.DONE
    assert job["progress"] == 1.0


def test_progress_logging_is_throttled_not_per_track(conn, caplog):
    """A run over many tracks must leave a readable trail in the journal --
    the fallback that still works even in the scenario the lock tests below
    exist to prevent, where the job row's own progress silently stops being
    updated -- without flooding it with one line per track."""
    track_count = 150
    for rowid in range(1, track_count + 1):
        insert_track(conn, rowid)

    with caplog.at_level("INFO", logger="muse.content_embed_batch"):
        content_embed_batch.run(conn, content.StubContentEmbedder())

    progress_lines = [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("Content embed batch:")
    ]
    assert 1 < len(progress_lines) < track_count // 10


# --- main()'s advisory lock ------------------------------------------------
#
# These go through `main()`, not `run()`: the lock lives in `main()` on
# purpose (`run(conn, embedder)` stays a plain, filesystem-side-effect-free
# call the tests above can use directly), so it can only be observed by
# driving the real entrypoint against a real `MUSE_LIBRARY_DB`.
#
# `content_embed_batch.lock_path` is imported rather than recomputed here,
# so a renamed lock-file suffix in production can't drift out of sync with
# what these tests check while both stay green.


def test_the_lock_is_held_for_the_duration_of_the_run_and_released_after(
    monkeypatch, tmp_path
):
    """Acquiring the lock and dropping it again before doing any real work
    would pass an "acquire, then check it's free afterward" test without
    excluding a concurrent run for the hours that matter. The only way to
    tell "held throughout" apart from "acquired and immediately released"
    is to probe the lock's state from inside the run itself, so this
    monkeypatches `run` with a wrapper that checks the lock before
    delegating to the real one."""
    library_db = tmp_path / "library.sqlite3"
    monkeypatch.setenv("MUSE_LIBRARY_DB", str(library_db))
    connect(library_db).close()  # create + migrate before main() checks it exists

    probe_path = content_embed_batch.lock_path(library_db)
    observed = {}
    real_run = content_embed_batch.run

    def probing_run(conn, embedder=None):
        probe_fd = os.open(probe_path, os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(probe_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            observed["held_during_run"] = False
            fcntl.flock(probe_fd, fcntl.LOCK_UN)
        except BlockingIOError:
            observed["held_during_run"] = True
        finally:
            os.close(probe_fd)
        return real_run(conn, content.StubContentEmbedder())

    monkeypatch.setattr(content_embed_batch, "run", probing_run)

    exit_code = content_embed_batch.main([])

    assert observed["held_during_run"] is True
    assert exit_code == 0

    # Released after: a fresh descriptor can now take the same lock, the
    # same thing a second process would do next.
    lock_fd = os.open(probe_path, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)


def test_a_run_while_the_lock_is_held_exits_distinctly_without_touching_jobs(
    monkeypatch, tmp_path
):
    """A second, overlapping invocation -- the timer firing again before an
    hours-long prior run finished, or a manual run overlapping the timer --
    must not flip a live job to `error` out from under it, and must not
    create a job row of its own either: the loser should never get far
    enough to touch the table at all."""
    library_db = tmp_path / "library.sqlite3"
    monkeypatch.setenv("MUSE_LIBRARY_DB", str(library_db))
    conn = connect(library_db)
    stale = job_rows.create(conn, content.CONTENT_EMBED)
    job_rows.update(conn, stale, state=job_rows.RUNNING)
    conn.close()

    # Hold the lock ourselves, standing in for the still-running prior
    # process -- a fresh file descriptor, the same way a separate process
    # would hold it (flock is per open-file-description, not per process,
    # so this is a faithful stand-in even from inside one test process).
    holder_fd = os.open(
        content_embed_batch.lock_path(library_db), os.O_CREAT | os.O_RDWR
    )
    fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        exit_code = content_embed_batch.main([])
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)

    assert exit_code == content_embed_batch.LOCK_HELD_EXIT

    conn = connect(library_db)
    try:
        assert job_rows.get(conn, stale)["state"] == job_rows.RUNNING
        assert len(job_rows.recent(conn)) == 1  # no new job row was created
    finally:
        conn.close()
