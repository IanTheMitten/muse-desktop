"""Running background work, and telling the browser about it live.

The database side of a job lives in `library.jobs`. This is the process side:
what actually runs the work, and the fan-out that turns its progress into
Server-Sent Events.

SSE rather than polling, and rather than a WebSocket: progress is one-way and
low-volume, so one long-lived GET is the whole protocol. Slice 1 has a single
job kind; the stream exists now because slice 3's downloads are the reason it
needs to.
"""

import asyncio
import logging
import random
from collections.abc import Callable
from pathlib import Path

from . import art as art_module
from . import content_embed_batch
from . import embed as embed_module
from . import lyrics_lrclib as lyrics_lrclib_module
from . import scanner
from .ingest import DownloadResult
from .library import art as library_art
from .library import embeddings as library_embeddings
from .library import jobs as job_rows
from .library import lyrics_state as library_lyrics_state

log = logging.getLogger("muse.jobs")

ART_BACKFILL = "art_backfill"
DOWNLOAD = "download"
SCAN = "scan"

# Enough room that a subscriber which stops reading is dropped rather than
# stalling the job that is publishing to it.
QUEUE_SIZE = 64


class JobBus:
    """Fan-out of job events to whoever is watching.

    Nothing here is durable. A browser that missed an event gets the current
    state on its next connection, because every subscriber is handed the latest
    snapshot the moment it subscribes — which is also what makes a page opened
    mid-run show a progress bar instead of nothing.
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.latest: dict | None = None

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.add(queue)
        if self.latest is not None:
            queue.put_nowait(self.latest)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: dict) -> None:
        self.latest = event
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A client that stopped reading is a client that has gone. It
                # must never be able to slow down the job itself.
                self._subscribers.discard(queue)

    def publish_threadsafe(self, event: dict) -> None:
        """Publish from the worker thread the backfill runs on."""
        loop = self._loop
        if loop is None:
            self.publish(event)
            return
        loop.call_soon_threadsafe(self.publish, event)


def _event(job: dict | None) -> dict:
    if job is None:
        return {"kind": None, "state": "idle"}
    return {
        "id": job["id"],
        "kind": job["kind"],
        "state": job["state"],
        "progress": job["progress"],
        "message": job["message"],
        "payload": job["payload"],
    }


async def run_art_backfill(
    conn,
    provider: art_module.ArtProvider,
    art_root: Path,
    bus: JobBus,
) -> dict | None:
    """Fill in every missing cover, once.

    Returns the finished job row, or None when there was nothing to do — which
    is the normal case on every start after the first, because the decision
    cache makes the work disappear rather than repeat.
    """
    if job_rows.active(conn, ART_BACKFILL):
        log.info("Art backfill already running; not starting a second")
        return None

    pending = library_art.albums_needing_art(conn)
    if not pending:
        return None

    job_id = job_rows.create(conn, ART_BACKFILL, {"albums": len(pending)})
    job_rows.update(
        conn,
        job_id,
        state=job_rows.RUNNING,
        message=f"Looking up {len(pending)} albums",
    )
    bus.publish(_event(job_rows.get(conn, job_id)))

    def on_progress(done: int, total: int, message: str) -> None:
        job_rows.update(conn, job_id, progress=done / total, message=message)
        bus.publish_threadsafe(
            {
                "id": job_id,
                "kind": ART_BACKFILL,
                "state": job_rows.RUNNING,
                "progress": done / total,
                "message": message,
                "payload": {"albums": total, "done": done},
            }
        )

    try:
        result = await asyncio.to_thread(
            art_module.backfill,
            conn,
            provider,
            art_root,
            on_progress=on_progress,
        )
    except asyncio.CancelledError:
        job_rows.update(
            conn, job_id, state=job_rows.CANCELLED, message="Server stopped"
        )
        bus.publish(_event(job_rows.get(conn, job_id)))
        raise
    except Exception as error:
        log.exception("Art backfill failed")
        job_rows.update(conn, job_id, state=job_rows.ERROR, message=str(error))
        bus.publish(_event(job_rows.get(conn, job_id)))
        return job_rows.get(conn, job_id)

    summary = (
        f"{result.found} covers found, {result.missing} albums have none"
        + (f", {result.failed} failed" if result.failed else "")
    )
    job_rows.update(
        conn, job_id, state=job_rows.DONE, progress=1.0, message=summary
    )
    log.info("Art backfill: %s", summary)
    finished = job_rows.get(conn, job_id)
    bus.publish(_event(finished))
    return finished


def _optional_encoder(factory, what: str, *, unavailable=None):
    """Build an optional encoder, or None if it cannot be built.

    Degrade in quality, never in availability (principle 4). A model that has
    disappeared from its host is exactly the "slow or dead external service"
    that principle names, and the leg it feeds is one the recommender already
    knows how to score without.
    """
    if factory is None:
        return None
    try:
        return factory()
    except Exception as error:
        log.warning("%s encoder unavailable, leg will be null: %s", what, error)
        # `unavailable`, not None: run_embed_batch reads None as "use the
        # stub", which would write hash-derived coordinates into a real
        # library and make a dead model indistinguishable from a measurement.
        return unavailable() if unavailable is not None else None


async def run_embed_backfill(
    conn,
    audio_encoder_factory: Callable[[], embed_module.AudioEncoder],
    lyrics_encoder_factory: Callable[[], embed_module.LyricsEncoder],
    bus: JobBus,
    audio_va_encoder_factory: Callable[[], embed_module.AudioVAEncoder] | None = None,
    lyrics_va_encoder_factory: Callable[[], embed_module.LyricsVAEncoder] | None = None,
) -> dict | None:
    """Embed every pending track, then check every already-embedded track's
    lyrics for a manual edit.

    Takes factories rather than encoder instances, and calls them from
    inside the worker thread the batch itself runs on. `ClapAudioEncoder()`
    alone is ~70 s of model load; building it on the caller's thread (the
    event loop, in production) would stall every concurrent request for
    that whole window, which is exactly what running this in the background
    is supposed to prevent. The VA factories are optional -- omitted, the
    batch falls back to the VA stubs the same way `run_embed_batch` already
    does, so every existing caller of this function keeps working unchanged.

    Always runs, even with an empty pending queue — unlike the art backfill,
    a run with nothing new to embed is not necessarily a no-op: it is also
    the only place a `lyrics_hash` mismatch from a manual edit gets noticed
    and re-embedded (story 56). Returns the finished job row, or None if a
    batch is already running.
    """
    if job_rows.active(conn, embed_module.EMBED):
        log.info("Embed batch already running; not starting a second")
        return None

    pending = library_embeddings.pending(conn)
    job_id = job_rows.create(conn, embed_module.EMBED, {"tracks": len(pending)})
    job_rows.update(
        conn,
        job_id,
        state=job_rows.RUNNING,
        message=f"Embedding {len(pending)} track(s)",
    )
    bus.publish(_event(job_rows.get(conn, job_id)))

    def on_progress(done: int, total: int, message: str) -> None:
        job_rows.update(conn, job_id, progress=done / total, message=message)
        bus.publish_threadsafe(
            {
                "id": job_id,
                "kind": embed_module.EMBED,
                "state": job_rows.RUNNING,
                "progress": done / total,
                "message": message,
                "payload": {"tracks": total, "done": done},
            }
        )

    def _work():
        # Built here, inside the worker thread — never by the caller.
        audio_encoder = audio_encoder_factory()
        lyrics_encoder = lyrics_encoder_factory()
        # The VA encoders are optional and are allowed to be unavailable.
        # `audio_vec` and `lyrics_vec` are not: a run that cannot embed audio
        # has nothing to fall back on, so those stay unguarded and a failure
        # there still fails the batch rather than reporting work it did not
        # do. Emotion is a leg the null-leg contract already covers.
        #
        # Not hypothetical: MerAudioVAEncoder named a model id that does not
        # exist (audeering/wav2vec2-large-robust-12-ffn1024-msp-dim -- a
        # typo for the real ...-12-ft-emotion-msp-dim, not a takedown), so
        # every construction 401'd, and before this guard that one
        # unreachable external model failed the whole batch and left CLAP,
        # lyrics, and features unwritten too.
        audio_va_encoder = _optional_encoder(
            audio_va_encoder_factory, "audio-VA",
            unavailable=embed_module.NullAudioVAEncoder,
        )
        lyrics_va_encoder = _optional_encoder(
            lyrics_va_encoder_factory, "lyrics-VA",
        )
        return embed_module.run_embed_batch(
            conn, audio_encoder, lyrics_encoder, audio_va_encoder,
            lyrics_va_encoder, on_progress=on_progress,
        )

    try:
        result = await asyncio.to_thread(_work)
    except asyncio.CancelledError:
        job_rows.update(
            conn, job_id, state=job_rows.CANCELLED, message="Server stopped"
        )
        bus.publish(_event(job_rows.get(conn, job_id)))
        raise
    except Exception as error:
        log.exception("Embed batch failed")
        job_rows.update(conn, job_id, state=job_rows.ERROR, message=str(error))
        bus.publish(_event(job_rows.get(conn, job_id)))
        return job_rows.get(conn, job_id)

    summary = (
        f"{result.embedded} embedded, {result.lyrics_reembedded} lyrics"
        f" re-embedded"
        + (f", {result.failed} failed" if result.failed else "")
    )
    job_rows.update(
        conn, job_id, state=job_rows.DONE, progress=1.0, message=summary
    )
    log.info("Embed batch: %s", summary)
    finished = job_rows.get(conn, job_id)
    bus.publish(_event(finished))
    return finished


async def run_content_backfill(conn, library_db: Path) -> dict | None:
    """Embed any track with no MERT content vector, right after a download.

    The nightly `muse-content-embed.timer` already picks new tracks up, so
    this exists only to shorten the wait from "tonight" to "as soon as the
    download queue drains". It is deliberately called from
    `run_download_worker` and not from `ingest.run_download`: the model load
    dominates a one-track run, and embedding after *each* of ten queued
    downloads would pay that load ten times for work one run does once.

    Delegates wholesale to `content_embed_batch.run` rather than calling
    `content.run_content_batch` itself, so an in-process run and a timer run
    leave identical `jobs` rows and recompute the corpus statistics the same
    way. The lock is what keeps the two apart; a run that cannot take it is
    a no-op and returns None, because the tracks it would have embedded stay
    on the queue either way.

    Returns None rather than raising when `universal_reco` is missing or
    broken -- `SubprocessContentEmbedder` raises a `RuntimeError` naming the
    exit code, `content_embed_batch.run` converts that into an `error` job
    row, and neither is a reason to take down a download worker that did its
    own job correctly.
    """
    def _work():
        with content_embed_batch.exclusive(library_db) as acquired:
            if not acquired:
                return None
            return content_embed_batch.run(conn)

    try:
        return await asyncio.to_thread(_work)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("Content embed after download failed")
        return None


async def run_lyrics_sync_backfill(
    conn,
    bus: JobBus,
    *,
    lyrics_root: Path,
    lrclib_search: Callable[..., list[dict]] = lyrics_lrclib_module.search_lrclib,
    rate_limit_seconds: float = 0.35,
) -> dict | None:
    """Sync-fetch lyrics for every playable track LRCLIB has not been asked
    about yet (slice 4, PRD "Synced lyrics").

    Same shape as `run_embed_backfill`: guarded by the one-running-job-of-
    this-kind check, a `jobs` row so `/api/health`/`/api/jobs` see it like
    any other job, and progress on the bus. `lrclib_search` is injected the
    same way the encoder factories are for the embed batch — the default is
    the real network call, and only `muse/lyrics_sync.py` ever runs with
    that default; every test supplies a fake so nothing here reaches the
    network.

    Unlike the embed batch, an empty pending queue really is a no-op: there
    is no lyrics-changed recheck pass here (that is a deliberate, separate
    re-invocation — see `library.lyrics_state.pending`'s docstring), so a
    track already decided is simply not visited again by this function.
    Returns the finished job row, or None if a batch of this kind is
    already running.
    """
    if job_rows.active(conn, lyrics_lrclib_module.LYRICS_SYNC):
        log.info("Lyrics sync batch already running; not starting a second")
        return None

    pending = library_lyrics_state.pending(conn)
    job_id = job_rows.create(
        conn, lyrics_lrclib_module.LYRICS_SYNC, {"tracks": len(pending)}
    )
    job_rows.update(
        conn,
        job_id,
        state=job_rows.RUNNING,
        message=f"Syncing lyrics for {len(pending)} track(s)",
    )
    bus.publish(_event(job_rows.get(conn, job_id)))

    def on_progress(done: int, total: int, message: str) -> None:
        job_rows.update(conn, job_id, progress=done / total, message=message)
        bus.publish_threadsafe(
            {
                "id": job_id,
                "kind": lyrics_lrclib_module.LYRICS_SYNC,
                "state": job_rows.RUNNING,
                "progress": done / total,
                "message": message,
                "payload": {"tracks": total, "done": done},
            }
        )

    try:
        result = await asyncio.to_thread(
            lyrics_lrclib_module.sync_batch,
            conn,
            lrclib_search,
            lyrics_root=lyrics_root,
            rate_limit_seconds=rate_limit_seconds,
            on_progress=on_progress,
        )
    except asyncio.CancelledError:
        job_rows.update(
            conn, job_id, state=job_rows.CANCELLED, message="Server stopped"
        )
        bus.publish(_event(job_rows.get(conn, job_id)))
        raise
    except Exception as error:
        log.exception("Lyrics sync batch failed")
        job_rows.update(conn, job_id, state=job_rows.ERROR, message=str(error))
        bus.publish(_event(job_rows.get(conn, job_id)))
        return job_rows.get(conn, job_id)

    summary = (
        f"{result.synced} synced, {result.plain} plain, "
        f"{result.declared_instrumental} instrumental, "
        f"{result.not_found} not found"
        + (f", {result.failed} failed" if result.failed else "")
    )
    job_rows.update(
        conn, job_id, state=job_rows.DONE, progress=1.0, message=summary
    )
    log.info("Lyrics sync batch: %s", summary)
    finished = job_rows.get(conn, job_id)
    bus.publish(_event(finished))
    return finished


async def run_scan_job(
    conn,
    folders: list[Path],
    bus: JobBus,
    *,
    lyrics_root: Path,
    art_root: Path,
    read_tags=None,
) -> dict | None:
    """Scan the library folders into the database, once.

    Returns None without a row when a scan is already running or no folder
    or dropped song exists (an unplugged drive is not an error).
    """
    if job_rows.active(conn, SCAN):
        log.info("Scan already running; not starting a second")
        return None
    if not any(f.exists() for f in folders):
        return None

    job_id = job_rows.create(conn, SCAN, {"folders": len(folders)})
    job_rows.update(
        conn, job_id, state=job_rows.RUNNING, message="Scanning"
    )
    bus.publish(_event(job_rows.get(conn, job_id)))

    def on_progress(done: int, total: int, message: str) -> None:
        progress = done / total if total else 0.0
        text = f"Scanning · {done} of {total}"
        job_rows.update(conn, job_id, progress=progress, message=text)
        bus.publish_threadsafe(
            {
                "id": job_id,
                "kind": SCAN,
                "state": job_rows.RUNNING,
                "progress": progress,
                "message": text,
                "payload": {"done": done, "total": total},
            }
        )

    kwargs = {} if read_tags is None else {"read_tags": read_tags}
    try:
        r = await asyncio.to_thread(
            scanner.scan,
            conn,
            folders,
            lyrics_root=lyrics_root,
            art_root=art_root,
            on_progress=on_progress,
            **kwargs,
        )
    except asyncio.CancelledError:
        job_rows.update(
            conn, job_id, state=job_rows.CANCELLED, message="Server stopped"
        )
        bus.publish(_event(job_rows.get(conn, job_id)))
        raise
    except Exception as error:
        log.exception("Scan failed")
        job_rows.update(conn, job_id, state=job_rows.ERROR, message=str(error))
        bus.publish(_event(job_rows.get(conn, job_id)))
        return job_rows.get(conn, job_id)

    summary = f"{r.added} added, {r.moved} moved, {r.missing} missing" + (
        f", {r.unreadable} unreadable" if r.unreadable else ""
    )
    job_rows.update(
        conn, job_id, state=job_rows.DONE, progress=1.0, message=summary
    )
    log.info("Scan: %s", summary)
    finished = job_rows.get(conn, job_id)
    bus.publish(_event(finished))
    return finished


async def run_download_job(
    conn,
    job_id: int,
    run_download_fn,
    run_download_kwargs: dict,
    bus: JobBus | None = None,
) -> dict:
    """Run one download job to completion. Mirrors run_art_backfill's
    create-is-already-done -> update(running) -> work -> update(done|error)
    -> bus.publish shape, so /api/jobs/stream needs no changes to carry
    'download' events. `run_download_fn` is `ingest.run_download`, injected
    so tests substitute a fake without touching yt-dlp/mutagen/Genius.
    """
    job_rows.update(conn, job_id, state=job_rows.RUNNING,
                      message="Downloading")
    if bus is not None:
        bus.publish(_event(job_rows.get(conn, job_id)))

    try:
        result: DownloadResult = await asyncio.to_thread(
            run_download_fn, conn, **run_download_kwargs
        )
    except Exception as error:
        log.exception("Download job %d failed", job_id)
        job_rows.update(conn, job_id, state=job_rows.ERROR, message=str(error))
        finished = job_rows.get(conn, job_id)
        if bus is not None:
            bus.publish(_event(finished))
        return finished

    if result.status == "done":
        job_rows.update(conn, job_id, state=job_rows.DONE, progress=1.0,
                          message=result.message)
    else:
        job_rows.update(conn, job_id, state=job_rows.ERROR, message=result.message)
    finished = job_rows.get(conn, job_id)
    if bus is not None:
        bus.publish(_event(finished))
    return finished


async def run_download_worker(
    conn,
    queue: asyncio.Queue,
    run_download_fn,
    payload_to_kwargs,
    bus: JobBus,
    content_backfill=None,
    *,
    gap_seconds: float = 0.0,
    sleep=asyncio.sleep,
    jitter=lambda: random.random() * 0.5,
) -> None:
    """The one background consumer of `download` jobs, for the app's
    lifetime. Sequential by construction: a single consumer of a single
    queue means never two `run_download_fn` calls in flight at once
    (PRD story 94: one download at a time).

    `gap_seconds` spaces consecutive downloads apart. One at a time is not
    enough on its own: a whole discography queued at once still hits YouTube
    back to back, which is what gets an address rate-limited or bot-checked.
    The gap is scaled by `1 + jitter()` (0-50% by default) so the spacing is
    not a fixed beat, and it is only taken when another job is already
    waiting -- a lone download, or the last of a batch, starts nothing after
    it and so waits for nothing.

    `content_backfill`, when given, runs once the queue is empty and at
    least one download in this drain succeeded -- not once per job. A whole
    record downloads as one `mb_release` fan-out of per-track `download`
    jobs, and the MERT model load costs more than embedding the tracks
    does, so per-job would pay it once per track of that record. Default
    None keeps every existing caller (and the default test run, which has
    no `universal_reco`) on exactly the old behaviour.
    """
    embed_pending = False
    while True:
        if content_backfill is not None and embed_pending and queue.empty():
            # Checked before the blocking `get()`, so a drained queue
            # triggers this rather than waiting for a download that may
            # never be asked for.
            embed_pending = False
            await content_backfill()
        job_id = await queue.get()
        try:
            job = job_rows.get(conn, job_id)
            if job is None or job["state"] != job_rows.QUEUED:
                continue  # cancelled, or already handled by a prior sweep
            kwargs = payload_to_kwargs(job["payload"])
            finished = await run_download_job(
                conn, job_id, run_download_fn, kwargs, bus
            )
            if finished["state"] == job_rows.DONE:
                embed_pending = True
            if gap_seconds > 0 and not queue.empty():
                await sleep(gap_seconds * (1 + jitter()))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Download worker: unexpected error on job %d", job_id)
