"""LRCLIB lyrics-sync batch: `python -m muse.lyrics_sync`.

Standalone, like `muse.taste_retrain` — a one-shot pass against the real
`library.sqlite3`, run by hand for now (CLAUDE.md: no systemd unit for this
yet, that is a separate decision). It opens its own connection with
`library.connect` and goes through the same `run_lyrics_sync_backfill` the
live API process would use, so a `lyrics_sync` job row lands the same way
regardless of which process ran it and `/api/health`/`/api/jobs` see it
uniformly. There is no live SSE progress for this invocation — `JobBus` here
is a throwaway instance with nothing subscribed to it, same reasoning as
`muse.taste_retrain`: a one-shot CLI run has no browser attached to publish
to.

Run it directly: `python -m muse.lyrics_sync`.
"""

import argparse
import asyncio
import logging
import sys

from . import library
from .config import Config
from .jobs import JobBus, run_lyrics_sync_backfill
from .library import jobs as job_rows
from .lyrics_lrclib import LYRICS_SYNC

log = logging.getLogger("muse.lyrics_sync")


def run(conn) -> dict:
    """Run one sync-fetch pass and record it as a `lyrics_sync` job.

    Reaps any `lyrics_sync` job left `running` or `queued` by a process that
    died before this one started — same reasoning as
    `muse.taste_retrain.run`: a fresh process invocation cannot itself be
    racing a job it just discovered non-terminal, since it did not exist a
    moment ago to have started one. Without this, one killed run would wedge
    every future invocation behind `run_lyrics_sync_backfill`'s
    already-running guard forever.
    """
    job_rows.abandon_running_of_kind(conn, LYRICS_SYNC)
    result = asyncio.run(run_lyrics_sync_backfill(conn, JobBus()))
    if result is None:
        # Unreachable in practice -- the reap above just cleared the only
        # thing that makes run_lyrics_sync_backfill refuse to start -- but
        # treated as an empty, successful pass rather than a crash if it
        # ever happens.
        return {"state": job_rows.DONE, "message": "nothing to do"}
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LRCLIB lyrics-sync batch")
    parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    config = Config.from_env()
    if not config.library_db.exists():
        log.error("No library at %s; nothing to sync", config.library_db)
        return 1

    conn = library.connect(config.library_db)
    try:
        result = run(conn)
    finally:
        conn.close()
    # A run that errored is worth a non-zero exit so `systemctl status` (once
    # this is wired to a timer) or an interactive run shows it, the same
    # convention `muse.taste_retrain` and `muse.backup` use.
    return 0 if result["state"] == job_rows.DONE else 2


if __name__ == "__main__":
    sys.exit(main())
