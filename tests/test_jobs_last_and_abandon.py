"""Two small additions to `library.jobs` ticket 04 needs and no existing job
kind did: a kind-scoped stale-job reaper (`taste_retrain` runs as a
standalone process with no long-lived `abandon_running` call at startup the
way the API has) and a "most recent job of this kind" lookup (the health
endpoint's "last retrain time")."""

from muse.library import jobs


def test_last_returns_the_most_recent_job_of_a_kind(conn):
    jobs.create(conn, "taste_retrain", now=100)
    second = jobs.create(conn, "taste_retrain", now=200)
    jobs.create(conn, "embed", now=300)  # a different kind: not returned

    found = jobs.last(conn, "taste_retrain")

    assert found["id"] == second


def test_last_filters_by_state(conn):
    first = jobs.create(conn, "taste_retrain", now=100)
    jobs.update(conn, first, state=jobs.DONE, now=110)
    second = jobs.create(conn, "taste_retrain", now=200)
    jobs.update(conn, second, state=jobs.ERROR, now=210)

    found = jobs.last(conn, "taste_retrain", state=jobs.DONE)

    assert found["id"] == first


def test_last_returns_none_when_no_job_of_that_kind_exists(conn):
    assert jobs.last(conn, "taste_retrain") is None
    assert jobs.last(conn, "taste_retrain", state=jobs.DONE) is None


def test_abandon_running_of_kind_reaps_only_that_kind(conn):
    stale = jobs.create(conn, "taste_retrain", now=100)
    jobs.update(conn, stale, state=jobs.RUNNING, now=110)
    other_kind_running = jobs.create(conn, "embed", now=100)
    jobs.update(conn, other_kind_running, state=jobs.RUNNING, now=110)

    reaped = jobs.abandon_running_of_kind(conn, "taste_retrain", now=500)

    assert reaped == 1
    assert jobs.get(conn, stale)["state"] == jobs.ERROR
    assert jobs.get(conn, other_kind_running)["state"] == jobs.RUNNING


def test_abandon_running_of_kind_is_a_no_op_when_nothing_is_stale(conn):
    done = jobs.create(conn, "taste_retrain", now=100)
    jobs.update(conn, done, state=jobs.DONE, now=110)

    reaped = jobs.abandon_running_of_kind(conn, "taste_retrain")

    assert reaped == 0
    assert jobs.get(conn, done)["state"] == jobs.DONE


def test_running_ids_of_kind_lists_only_that_kind(conn):
    from muse.library import jobs as job_rows

    running_download = job_rows.create(conn, "download")
    job_rows.update(conn, running_download, state=job_rows.RUNNING)
    running_embed = job_rows.create(conn, "embed")
    job_rows.update(conn, running_embed, state=job_rows.RUNNING)
    queued_download = job_rows.create(conn, "download")  # state=queued already

    assert job_rows.running_ids_of_kind(conn, "download") == [running_download]


def test_queued_ids_of_kind_lists_only_that_kind_in_id_order(conn):
    from muse.library import jobs as job_rows

    first = job_rows.create(conn, "download")
    job_rows.create(conn, "embed")
    second = job_rows.create(conn, "download")

    assert job_rows.queued_ids_of_kind(conn, "download") == [first, second]


def test_running_and_queued_ids_of_kind_empty_when_none(conn):
    from muse.library import jobs as job_rows

    assert job_rows.running_ids_of_kind(conn, "download") == []
    assert job_rows.queued_ids_of_kind(conn, "download") == []
