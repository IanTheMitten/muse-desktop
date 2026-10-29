"""The browse surface behind the Add tab, and the retry behind a failure.

These endpoints exist because the client had no way to reach a download: it
needs a `*_rowid` to POST a job with, and only the mirror has one. Everything
tested here is about telling the truth on the way to that tap -- what the
library already holds, which editions are the same release, and what a failure
leaves behind.
"""

import time
import pytest

from fastapi.testclient import TestClient

from muse.api import create_app
from muse.catalog import EmptyCatalog
from muse.config import Config
from muse.library import jobs as job_rows


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("muse.api._open_catalog", lambda config: EmptyCatalog())
    config = Config(
        root=tmp_path, library_db=tmp_path / "library.sqlite3",
        manifest=tmp_path / "manifest.json", catalog=tmp_path / "catalog.sqlite3",
        audio_features=tmp_path / "features.sqlite3",
        music_root=tmp_path / "MUSIC", art_root=tmp_path / "art",
        web_dist=tmp_path / "web", backup_dir=tmp_path / "backups",
        backup_target=None, port=8080, host="127.0.0.1",
        art_backfill=False, embed_batch=False, content_embed=False,
        catalog_freeze="2025-11-13",
    )
    app = create_app(config)
    with TestClient(app) as test_client:
        yield test_client, app


def test_retry_creates_a_new_job_and_leaves_the_failure_readable(client):
    """The failed row is the record of one attempt. Retrying must not rewrite
    it, or the only evidence of what went wrong is destroyed."""
    test_client, app = client
    conn = app.state.conn
    failed = job_rows.create(conn, "download", {"track_rowid": 100})
    job_rows.update(conn, failed, state=job_rows.ERROR, message="yt-dlp said no")

    response = test_client.post(f"/api/jobs/{failed}/retry")

    assert response.status_code == 200
    retry_id = response.json()["job_ids"][0]
    assert response.json()["retry_of"] == failed
    assert retry_id != failed

    original = job_rows.get(conn, failed)
    assert original["state"] == "error"
    assert original["message"] == "yt-dlp said no"

    fresh = job_rows.get(conn, retry_id)
    # Queued or already running: the worker is a live consumer of the same
    # queue, so a retry can be picked up before this line runs. What matters
    # is that the new row is not born terminal.
    assert fresh["state"] in ("queued", "running")
    assert fresh["payload"] == {"track_rowid": 100}

    # the worker thread must not outlive the fixture's connection
    for _ in range(200):
        if job_rows.get(conn, retry_id)["state"] in ("done", "error"):
            break
        time.sleep(0.05)
    else:
        pytest.fail("retry job still running; teardown would race the worker")


def test_retry_refuses_a_job_that_has_not_failed(client):
    test_client, app = client
    running = job_rows.create(app.state.conn, "download", {"track_rowid": 100})
    job_rows.update(app.state.conn, running, state=job_rows.RUNNING)

    assert test_client.post(f"/api/jobs/{running}/retry").status_code == 409
    assert test_client.post("/api/jobs/9999/retry").status_code == 404
