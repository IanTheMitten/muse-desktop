"""`taste_retrain.run`: the `jobs`-machinery wrapper around
`muse.taste.run_retrain`, exercised the way `tests/test_jobs_embed.py`
exercises `run_embed_backfill` -- job state transitions, and (ticket 04's
own checklist item) that a bad or partial retrain never corrupts the
previous snapshot.
"""

from muse.library import jobs as job_rows
from muse.taste import MIN_PLAY_EVENTS, StubTasteTrainer, TASTE_RETRAIN
from muse.taste_retrain import run
from muse.library import close_span, open_span
from tests.conftest import insert_track


def _play(conn, track_rowid, *, started_at):
    play_id = open_span(conn, track_rowid, duration_ms=180_000, now_ms=started_at)
    close_span(conn, play_id, "track_end", position_ms=180_000, now_ms=started_at + 180_000)


def _trained_track(conn, rowid):
    insert_track(conn, rowid)
    for i in range(MIN_PLAY_EVENTS):
        _play(conn, rowid, started_at=i * 200_000)


def test_run_records_a_done_job_and_writes_the_snapshot(conn):
    _trained_track(conn, 1)

    result = run(conn, trainer=StubTasteTrainer())

    assert result["kind"] == TASTE_RETRAIN
    assert result["state"] == job_rows.DONE
    row = conn.execute(
        "SELECT taste_vec FROM taste_embeddings WHERE track_rowid = 1"
    ).fetchone()
    assert row["taste_vec"] is not None


def test_a_failing_trainer_marks_the_job_errored_and_leaves_the_snapshot(conn):
    _trained_track(conn, 1)
    run(conn, trainer=StubTasteTrainer())
    before = conn.execute(
        "SELECT trained_at FROM taste_embeddings WHERE track_rowid = 1"
    ).fetchone()["trained_at"]

    class ExplodingTrainer:
        model_name = "exploding"

        def fit(self, interactions, track_rowids):
            raise RuntimeError("simulated ALS failure")

    result = run(conn, trainer=ExplodingTrainer())

    assert result["state"] == job_rows.ERROR
    after = conn.execute(
        "SELECT trained_at FROM taste_embeddings WHERE track_rowid = 1"
    ).fetchone()["trained_at"]
    assert after == before  # the previous snapshot is untouched


def test_run_reaps_a_stale_running_job_of_its_own_kind_before_starting(conn):
    stale = job_rows.create(conn, TASTE_RETRAIN)
    job_rows.update(conn, stale, state=job_rows.RUNNING)

    result = run(conn, trainer=StubTasteTrainer())

    assert job_rows.get(conn, stale)["state"] == job_rows.ERROR
    assert result["id"] != stale
    assert result["state"] == job_rows.DONE
