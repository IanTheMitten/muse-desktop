"""Library-wide MERT content embed: `python -m muse.content_embed_batch`.

Standalone, like `muse.taste_retrain`: run it with
`uv run python -m muse.content_embed_batch`. MERT runs in the ML worker. It
opens its own connection with `library.connect`, the same one the API does,
and goes through the same `library.jobs` bookkeeping every other job kind uses, so
`/api/health` and `/api/jobs` see this run the same way they see any other.

There is no live SSE progress for it, for the same reason the taste retrain
has none: `muse.jobs.JobBus` only exists inside the long-lived API process,
and a standalone batch has no browser attached to publish to. The
jobs table is the uniform surface; the bus is not.

Run it directly: `python -m muse.content_embed_batch`.
"""

import argparse
try:
    import fcntl
except ImportError:  # shortcut: Windows has no flock, so the batch lock is a no-op there; upgrade to msvcrt.locking if a second writer ever appears
    fcntl = None
import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from . import library
from .config import Config
from .content import (
    CONTENT_EMBED,
    ContentEmbedder,
    run_content_batch,
)
from .library import content as library_content
from .library import jobs as job_rows
from .worker import WorkerClient, WorkerContentEmbedder, worker_command

log = logging.getLogger("muse.content_embed_batch")

# Distinct from the "the batch ran and errored" exit (2) below, so a script
# -- or a human reading the exit code -- can tell "nothing happened
# because a prior run is still going" apart from "a run happened and
# failed".
LOCK_HELD_EXIT = 3


def lock_path(library_db: Path) -> Path:
    """Next to the library database, not under /tmp: the lock has to travel
    with the deployment the same way the database itself does, rather than
    depend on tmpdir semantics or the working directory the batch happens
    to be launched from.

    Public (no underscore): a deploy unit or an operator clearing a stale
    lock by hand needs to know where it lives, the same way `LOCK_HELD_EXIT`
    is public for a script checking the exit code. Tests import this rather
    than recomputing the suffix, so a renamed suffix can't drift the two out
    of sync while both stay green.
    """
    return library_db.with_name(library_db.name + ".content_embed.lock")


def _try_acquire_lock(path: Path) -> int | None:
    """An OS-level mutex between overlapping runs of this one batch.

    `taste_retrain`'s reasoning for calling `abandon_running_of_kind`
    unconditionally -- a fresh oneshot process cannot have a live prior run
    of its own, so anything non-terminal already in the table can only be a
    crash -- holds there because that retrain finishes in minutes. It does
    not hold here: this batch runs for hours, long enough for a
    `Persistent=true` timer to fire again before a prior run finishes, or
    for a manual run to overlap the timer. Without a lock, the new
    process's `abandon_running_of_kind` would flip the still-running prior
    job's row to `error` before any liveness check; `job_rows.update` then
    silently no-ops every subsequent call against that now-terminal row, so
    the real outcome of the hours-long run is never recorded; and both
    processes would read `library_content.pending()` with no claim between
    them and dispatch overlapping embedders -- two writers against
    `library.sqlite3`, which this codebase is built around having exactly
    one of.

    Returns an open file descriptor holding the lock, or None if another
    process already holds it. The caller owns the fd and must close it
    (which also releases the lock) when the run ends.
    """
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        if fcntl:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # The one outcome that means "someone else holds it": `LOCK_NB`
        # contention raises `BlockingIOError` (confirmed empirically --
        # EAGAIN/EWOULDBLOCK, errno 11 on Linux), a narrower catch than
        # `OSError` on purpose. Something like `ENOLCK` (locking
        # unavailable on this filesystem) is a different `OSError` and
        # must not be swallowed into the same "already running" message --
        # that would send an operator hunting for a process that doesn't
        # exist instead of the filesystem problem that's actually there.
        os.close(fd)
        return None
    except OSError:
        # Not contention -- let it propagate as the real error it is, but
        # still close the fd we opened so the failure doesn't also leak
        # one.
        os.close(fd)
        raise
    return fd


@contextmanager
def exclusive(library_db: Path):
    """Hold the batch lock for the body, or yield False if someone else has it.

    Public because this batch now has two callers, not one: the standalone
    oneshot below, and `jobs.run_content_backfill` inside the long-lived API
    process after a download. They are separate processes, so only an
    OS-level lock can keep them apart -- and `run()` calls
    `abandon_running_of_kind` unconditionally, which would flip a live
    nightly run's row to `error` if the in-process caller reached it while
    that run was still going (see `_try_acquire_lock`'s own docstring for
    the rest of that failure).

    The caller must check what it is handed. Yielding False rather than
    raising is deliberate: a download that lands during the hours-long
    first full run is an ordinary event, not a failure -- its track simply
    stays on the queue for tonight's timer.
    """
    fd = _try_acquire_lock(library_db_lock := lock_path(library_db))
    if fd is None:
        log.info("Content embed batch already running (lock held at %s)",
                 library_db_lock)
        yield False
        return
    try:
        yield True
    finally:
        if fcntl:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _progress_log_interval(total: int) -> int:
    """Every whole percent, or every 50 tracks, whichever is coarser -- so
    a run over many thousands of tracks leaves on the order of a hundred
    lines in `journalctl -f` rather than one per track, and a smaller run
    still gets a line at least every 50 rather than waiting for a percent
    that is itself hundreds of tracks wide."""
    return max(50, total // 100)


def run(conn, embedder: ContentEmbedder) -> dict:
    """Run one content-embed batch and record it as a `content_embed` job.

    Reaps any `content_embed` job left `running` or `queued` by a process
    that died before creating its own -- a fresh process invocation (this is
    a one-shot process, never a long-lived loop) is exactly the situation
    `abandon_running_of_kind` exists for. Safe to call directly, as the
    tests do, with no filesystem side effect of its own: the exclusion that
    actually matters -- keeping two of these processes from running at once
    -- lives in `main()`'s advisory lock, not here.
    """
    job_rows.abandon_running_of_kind(conn, CONTENT_EMBED)

    pending = library_content.pending(conn)
    job_id = job_rows.create(conn, CONTENT_EMBED, {"tracks": len(pending)})
    job_rows.update(
        conn,
        job_id,
        state=job_rows.RUNNING,
        message=f"Embedding {len(pending)} track(s)",
    )

    # Read by the `except` branch below if the batch dies mid-run -- without
    # it, a worker that died at track 9,000 of 10,000 and one that died
    # before doing anything look identical in the job's error message.
    last_progress = {"done": 0, "total": len(pending)}

    def on_progress(done: int, total: int, message: str) -> None:
        last_progress["done"] = done
        last_progress["total"] = total
        job_rows.update(conn, job_id, progress=done / total, message=message)
        # Throttled: the job row can silently stop advancing (see the two
        # processes racing `_try_acquire_lock` was written to prevent), so
        # the journal is the fallback that still shows the batch is alive
        # even when the table's story would be wrong -- but one line per
        # track would flood a multi-thousand-track run's log instead of
        # leaving a readable trail.
        if done == total or done % _progress_log_interval(total) == 0:
            log.info("Content embed batch: %s", message)

    try:
        result = run_content_batch(
            conn, embedder, on_progress=on_progress
        )
    except Exception as error:
        log.exception("Content embed batch failed")
        message = (
            f"{last_progress['done']} of {last_progress['total']} embedded"
            f" before failure: {error}"
        )
        job_rows.update(conn, job_id, state=job_rows.ERROR, message=message)
        return job_rows.get(conn, job_id)

    summary = f"{result.embedded} embedded" + (
        f", {result.failed} failed" if result.failed else ""
    )
    job_rows.update(conn, job_id, state=job_rows.DONE, progress=1.0, message=summary)
    log.info("Content embed batch: %s", summary)
    return job_rows.get(conn, job_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Library-wide MERT content-embedding batch"
    )
    parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    config = Config.from_env()
    if not config.library_db.exists():
        log.error("No library at %s; nothing to embed", config.library_db)
        return 1

    with exclusive(config.library_db) as acquired:
        if not acquired:
            # Deliberately does not touch the job table -- the whole point of
            # checking the lock before anything else is that the loser here
            # never gets far enough to call `abandon_running_of_kind` against
            # a job that, in fact, is still alive.
            return LOCK_HELD_EXIT
        conn = library.connect(config.library_db)
        client = WorkerClient(worker_command(config.models_dir, config.worker_cmd))
        try:
            result = run(conn, WorkerContentEmbedder(client))
        finally:
            client.stop()
            conn.close()

    # A batch that errored is worth a non-zero exit so a caller
    # sees it, the same convention `muse.taste_retrain` already uses.
    return 0 if result["state"] == job_rows.DONE else 2


if __name__ == "__main__":
    sys.exit(main())
