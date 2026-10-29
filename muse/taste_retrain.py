"""Nightly taste-embedding retrain: `python -m muse.taste_retrain`.

Runs as a `taste_retrain` job at launch when the last retrain is over a day
old, or standalone with `uv run python -m muse.taste_retrain`. It opens its
own connection with `library.connect`, the same one the API does, and goes
through the same `library.jobs` bookkeeping every other job kind uses, so
`/api/health` and `/api/jobs` see this run the same way they see any other.
There is no live SSE progress for it -- `muse.jobs.JobBus` only exists
inside the long-lived API process, and a nightly batch has no browser
attached to publish to anyway.

Run it directly: `python -m muse.taste_retrain`.
"""

import argparse
import logging
import sys

from . import library
from .config import Config
from .library import jobs as job_rows
from .taste import TASTE_RETRAIN, ImplicitAlsTrainer, TasteTrainer, run_retrain

log = logging.getLogger("muse.taste_retrain")


def run(conn, trainer: TasteTrainer | None = None) -> dict:
    """Run one retrain and record it as a `taste_retrain` job.

    Reaps any `taste_retrain` job left `running` or `queued` by a process
    that died before creating its own -- a fresh process invocation (this
    is a one-shot process, never a long-lived loop) is exactly the situation
    `abandon_running_of_kind` exists for: anything non-terminal from before
    this call can only be a crashed prior run, not a live one, since this
    process did not exist a moment ago to have started it.
    """
    job_rows.abandon_running_of_kind(conn, TASTE_RETRAIN)
    job_id = job_rows.create(conn, TASTE_RETRAIN)
    job_rows.update(
        conn, job_id, state=job_rows.RUNNING, message="Retraining taste embeddings"
    )
    try:
        result = run_retrain(conn, trainer or ImplicitAlsTrainer())
    except Exception as error:
        log.exception("Taste retrain failed")
        job_rows.update(conn, job_id, state=job_rows.ERROR, message=str(error))
        return job_rows.get(conn, job_id)

    summary = (
        f"{result.trained} trained, {result.skipped_below_threshold} below "
        f"threshold, {result.considered} considered"
    )
    job_rows.update(conn, job_id, state=job_rows.DONE, progress=1.0, message=summary)
    log.info("Taste retrain: %s", summary)
    return job_rows.get(conn, job_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nightly taste-embedding retrain")
    parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    config = Config.from_env()
    if not config.library_db.exists():
        log.error("No library at %s; nothing to retrain", config.library_db)
        return 1

    conn = library.connect(config.library_db)
    try:
        result = run(conn)
    finally:
        conn.close()
    # A retrain that errored is worth a non-zero exit so `systemctl status`
    # shows it, the same convention every job uses for a failure.
    return 0 if result["state"] == job_rows.DONE else 2


if __name__ == "__main__":
    sys.exit(main())
