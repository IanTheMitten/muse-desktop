import asyncio
import hashlib

import pytest

from muse import ytdlp
from muse.ingest import DownloadResult
from muse.jobs import (
    DOWNLOAD, JobBus, run_download_job, run_download_worker, run_ytdlp_install)
from muse.library import jobs as job_rows


@pytest.mark.asyncio
async def test_run_download_job_updates_job_row_on_success(conn):
    bus = JobBus()
    bus.attach(asyncio.get_running_loop())
    job_id = job_rows.create(conn, DOWNLOAD, {"track_rowid": 100})

    def fake_run_download(conn_, **kwargs):
        return DownloadResult(status="done", message="ok", track_rowid=100)

    result = await run_download_job(conn, job_id, fake_run_download, {})
    assert result["state"] == job_rows.DONE
    row = job_rows.get(conn, job_id)
    assert row["state"] == job_rows.DONE


@pytest.mark.asyncio
async def test_run_download_job_marks_error_on_failed_result(conn):
    bus = JobBus()
    bus.attach(asyncio.get_running_loop())
    job_id = job_rows.create(conn, DOWNLOAD, {"track_rowid": 100})

    def fake_run_download(conn_, **kwargs):
        return DownloadResult(status="error", message="no video found")

    result = await run_download_job(conn, job_id, fake_run_download, {})
    assert result["state"] == job_rows.ERROR
    assert result["message"] == "no video found"


@pytest.mark.asyncio
async def test_run_download_job_marks_error_on_exception(conn):
    job_id = job_rows.create(conn, DOWNLOAD, {"track_rowid": 100})

    def raising_run_download(conn_, **kwargs):
        raise RuntimeError("boom")

    result = await run_download_job(conn, job_id, raising_run_download, {})
    assert result["state"] == job_rows.ERROR
    assert "boom" in result["message"]


# --- the content-embed hand-off ------------------------------------------
#
# MERT after a download is deliberately per *drain*, not per job: a whole
# record arrives as one `download` job per track, and the model load costs
# more than the embedding does.


async def _drain(queue: asyncio.Queue, worker: asyncio.Task) -> None:
    """Let the worker empty the queue, then stop it.

    The worker is an endless loop that parks on `queue.get()`, so the test
    has to end it rather than await it. `sleep(0.05)` after the queue empties
    is what gives the loop its one further pass -- the pass that reaches the
    drained-queue check.
    """
    for _ in range(200):
        if queue.empty():
            break
        await asyncio.sleep(0)
    await asyncio.sleep(0.05)
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_content_backfill_runs_once_per_drain_not_once_per_download(conn):
    bus = JobBus()
    bus.attach(asyncio.get_running_loop())
    queue: asyncio.Queue = asyncio.Queue()
    for track_rowid in (100, 101, 102):
        queue.put_nowait(job_rows.create(conn, DOWNLOAD, {"track_rowid": track_rowid}))

    calls = []

    async def content_backfill():
        calls.append(queue.qsize())

    def fake_run_download(conn_, **kwargs):
        return DownloadResult(status="done", message="ok", track_rowid=100)

    worker = asyncio.create_task(
        run_download_worker(conn, queue, fake_run_download, lambda payload: {},
                            bus, content_backfill)
    )
    await _drain(queue, worker)

    # Once, after the last of the three — not three times.
    assert calls == [0]


@pytest.mark.asyncio
async def test_content_backfill_does_not_run_when_every_download_failed(conn):
    bus = JobBus()
    bus.attach(asyncio.get_running_loop())
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(job_rows.create(conn, DOWNLOAD, {"track_rowid": 100}))

    calls = []

    async def content_backfill():
        calls.append(True)

    def fake_run_download(conn_, **kwargs):
        return DownloadResult(status="error", message="no video found")

    worker = asyncio.create_task(
        run_download_worker(conn, queue, fake_run_download, lambda payload: {},
                            bus, content_backfill)
    )
    await _drain(queue, worker)

    # Nothing new landed on disk, so there is nothing new to embed.
    assert calls == []


@pytest.mark.asyncio
async def test_worker_pauses_between_downloads_but_not_after_the_last(conn):
    # A bulk queue (a whole discography) fired back to back is what gets an
    # IP rate-limited by YouTube. The gap goes *between* downloads only: a
    # single download, or the last of a batch, starts nothing after it.
    bus = JobBus()
    bus.attach(asyncio.get_running_loop())
    queue: asyncio.Queue = asyncio.Queue()
    for track_rowid in (100, 101, 102):
        queue.put_nowait(job_rows.create(conn, DOWNLOAD, {"track_rowid": track_rowid}))

    events = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds):
        events.append(("sleep", seconds))
        await real_sleep(0)

    def fake_run_download(conn_, **kwargs):
        events.append(("download",))
        return DownloadResult(status="done", message="ok", track_rowid=100)

    worker = asyncio.create_task(
        run_download_worker(conn, queue, fake_run_download, lambda payload: {},
                            bus, gap_seconds=10.0, sleep=fake_sleep,
                            jitter=lambda: 0.25)
    )
    await _drain(queue, worker)

    assert [e[0] for e in events] == [
        "download", "sleep", "download", "sleep", "download"]
    # gap scaled by (1 + jitter): randomised so the spacing is not a fixed beat
    assert all(e[1] == 12.5 for e in events if e[0] == "sleep")


@pytest.mark.asyncio
async def test_worker_has_no_gap_by_default(conn):
    bus = JobBus()
    bus.attach(asyncio.get_running_loop())
    queue: asyncio.Queue = asyncio.Queue()
    for track_rowid in (100, 101):
        queue.put_nowait(job_rows.create(conn, DOWNLOAD, {"track_rowid": track_rowid}))
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    worker = asyncio.create_task(
        run_download_worker(
            conn, queue,
            lambda conn_, **kw: DownloadResult(status="done", message="ok", track_rowid=100),
            lambda payload: {}, bus, sleep=fake_sleep)
    )
    await _drain(queue, worker)
    assert slept == []


# --- before_drain: `yt-dlp -U` once per drain -----------------------------


def _ok_download(conn_, **kwargs):
    return DownloadResult(status="done", message="ok", track_rowid=100)


async def _run_with_hook(conn, queue, before_drain):
    bus = JobBus()
    bus.attach(asyncio.get_running_loop())
    worker = asyncio.create_task(
        run_download_worker(conn, queue, _ok_download, lambda payload: {},
                            bus, before_drain=before_drain)
    )
    await _drain(queue, worker)


@pytest.mark.asyncio
async def test_before_drain_runs_once_for_a_batch(conn):
    queue: asyncio.Queue = asyncio.Queue()
    for track_rowid in (100, 101, 102):
        queue.put_nowait(job_rows.create(conn, DOWNLOAD, {"track_rowid": track_rowid}))
    calls = []

    async def before_drain():
        calls.append(True)

    await _run_with_hook(conn, queue, before_drain)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_before_drain_runs_again_after_idle(conn):
    bus = JobBus()
    bus.attach(asyncio.get_running_loop())
    queue: asyncio.Queue = asyncio.Queue()
    calls = []

    async def before_drain():
        calls.append(True)

    worker = asyncio.create_task(
        run_download_worker(conn, queue, _ok_download, lambda payload: {},
                            bus, before_drain=before_drain)
    )
    queue.put_nowait(job_rows.create(conn, DOWNLOAD, {"track_rowid": 100}))
    for _ in range(200):
        await asyncio.sleep(0)
    queue.put_nowait(job_rows.create(conn, DOWNLOAD, {"track_rowid": 101}))
    await _drain(queue, worker)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_raising_before_drain_does_not_stop_the_job(conn):
    queue: asyncio.Queue = asyncio.Queue()
    job_id = job_rows.create(conn, DOWNLOAD, {"track_rowid": 100})
    queue.put_nowait(job_id)

    async def before_drain():
        raise RuntimeError("boom")

    await _run_with_hook(conn, queue, before_drain)
    assert job_rows.get(conn, job_id)["state"] == job_rows.DONE


# --- run_ytdlp_install ----------------------------------------------------


@pytest.mark.asyncio
async def test_ytdlp_install_job_errors_without_a_base_url(conn, tmp_path):
    bus = JobBus()
    bus.attach(asyncio.get_running_loop())
    finished = await run_ytdlp_install(conn, bus, tmp_path, "")
    assert finished["state"] == job_rows.ERROR
    assert finished["message"] == "Couldn't get the downloader"


class _Resp:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


class _Session:
    def __init__(self, base: str, sums: bytes, body: bytes):
        self.files = {f"{base}/SHA2-256SUMS": sums, f"{base}/{ytdlp.asset_name()}": body}

    def get(self, url, timeout=None):
        return _Resp(self.files[url])


@pytest.mark.asyncio
async def test_ytdlp_install_job_installs_the_binary(conn, tmp_path):
    bus = JobBus()
    bus.attach(asyncio.get_running_loop())
    body = b"#!fake"
    sums = f"{hashlib.sha256(body).hexdigest()}  {ytdlp.asset_name()}\n".encode()
    base = "https://example.test/dl"
    finished = await run_ytdlp_install(
        conn, bus, tmp_path, base, session=_Session(base, sums, body))
    assert finished["state"] == job_rows.DONE
    assert ytdlp.binary_path(tmp_path).exists()
