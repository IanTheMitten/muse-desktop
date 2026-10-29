"""The HTTP surface, and the process that serves it.

One uvicorn process: this is a single-user app and story 5 asks for a single
command. Startup migrates the library and imports the manifest, so a downloader
run that added tracks needs no coordination beyond a restart.
"""

import asyncio
import json
import logging
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

# `Path` here would shadow `pathlib.Path`, which this module uses far more
# often, so the path-parameter declarer comes in under its own name.
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi import Path as PathParam
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import embed as embed_module
from . import library
from .art import ChainedArtProvider, CoverArtArchive, ItunesArt, YtdlpThumbnailArt
from .catalog import EmptyCatalog, SqliteCatalog
from .config import Config
from . import arc_engine, charts, discover, nearest, reco, recommend, request_spec
from .content import CONTENT_EMBED
from .contract import CONTRACT_VERSION, ContractVersion
from .embed import ClapAudioEncoder, MerAudioVAEncoder, MiniLmLyricsEncoder, TextVAEncoder
from .features import EmptyFeatures, SqliteFeatures
from .feedback import feedback_score
from .ingest import TieredMetadataProvider, YtDlpAudioSource, build_genius_client, run_download
from .jobs import (
    ART_BACKFILL,
    DOWNLOAD,
    JobBus,
    run_art_backfill,
    run_content_backfill,
    run_download_job,
    run_download_worker,
    run_embed_backfill,
)
from .library import art as library_art
from .library import content as library_content
from .library import embeddings as library_embeddings
from .library import emotion as library_emotion
from .library import features as library_features
from .library import insights
from .library import jobs as job_rows
from .library import lyrics_state as library_lyrics_state
from .library import playlists as library_playlists
from .library import queries
from .library import taste as library_taste
from .library import tracks as library_tracks
from .lrc import parse_lrc
from .lyrics import read_lyrics
from .musicbrainz import MusicBrainzClient
from .streaming import ByteRange, Unsatisfiable, parse_range, read_range
from .taste import TASTE_RETRAIN

# .m4a. Safari is happier with audio/mp4 than with audio/x-m4a.
AUDIO_MEDIA_TYPE = "audio/mp4"

# What a MusicBrainz id looks like, enforced on the path parameters that take
# one. Two different id spaces sit at the same position on
# `/api/discover/artists/{...}` -- a catalog rowid before `/mb`, an MBID
# before `/releases` -- so without this, `/api/discover/artists/5/releases`
# is a valid request that browses MusicBrainz for an artist called "5" and
# comes back empty. A 422 naming the parameter beats a silent empty list.
MBID_PATTERN = r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$"

# What an `mb` job payload must actually carry. `recording_mbid` is what the
# MusicBrainz metadata tier keys on; the other four mirror, exactly, the
# columns `tracks` declares NOT NULL.
#
# That mirroring is the point, and it is why this is one named constant
# rather than four inline conditions. Today a payload missing `artist_name`
# fails loudly at the schema before anything is downloaded, so this check
# looks redundant -- but nothing at the route said the route depended on
# that, and the dependency is three tables away from whoever would break it.
# Relax one of those columns in a future migration, for a local-file import
# or a partial row, and the same payload stops failing: the MusicBrainz tier
# answers with `artist_name=None`, the row writes, and yt-dlp is asked for
# "None - Guest Spot". A loud pre-download failure quietly becomes a wrong
# download. This list is where that migration fails instead, in one
# reviewable line, next to a note saying what it mirrors.
#
# It costs a client nothing: `/api/discover/release-groups/{mbid}/tracks`
# returns every one of these on every row, precisely so a track can be
# queued as it stands, so a payload missing one is a client bug and better
# found at development time than in a job row read later.
MB_REQUIRED_FIELDS = (
    "recording_mbid",
    "artist_name",
    "artist_names",
    "album_name",
    "track_name",
)


class ArcPlan(BaseModel):
    """A posted request spec plus the stateless position of the session."""

    spec: dict
    mode: str = "playlist"
    n: int = 12
    pos: int = 0
    recent: list[int] = []
    exclude: list[int] = []


class StateWrite(BaseModel):
    """A player-state write from one device.

    `client_state` is the load-bearing field. Both devices heartbeat every ten
    seconds, so without it an idle background tab would keep overwriting the
    position of the device actually playing — and the failure is silent, just a
    position that drifts backwards for no reason.

    There is deliberately no client timestamp here. The stored `updated_at`
    comes from the server clock, because two devices whose clocks disagree
    would otherwise let the one running fast win every conflict forever, and a
    listener cannot see or fix that.
    """

    queue: list[int]
    queue_index: int = 0
    position_ms: int = 0
    client_state: str = Field(description="'playing' | 'paused' | 'idle'")
    shuffle: bool = False
    repeat_mode: str = "off"
    # Non-null means radio is on, and names the track this radio session
    # started from; null means off. It rides the same guarded heartbeat as
    # `shuffle` and `repeat_mode` so radio follows the listener from the phone
    # to the laptop the way the other two modes already do.
    radio_seed: int | None = None
    # The queued rowids radio put there, so a play of one is tagged
    # `source='radio'` after a reload or on the other device too.
    radio_added: list[int] = Field(default_factory=list)
    updated_by: str | None = None


# Play spans. The reason a span ended is recorded exactly as it happened and
# never interpreted here -- see ADR 0001.
ENDED_REASONS = frozenset(
    {
        "track_end",
        "user_next",
        "user_prev",
        "new_selection",
        "pause_timeout",
        "lost",
    }
)


class PlayOpen(BaseModel):
    track_rowid: int
    duration_ms: int
    source: str | None = None


class PlayBeat(BaseModel):
    position_ms: int
    seek_count: int | None = None


class PlayClose(BaseModel):
    ended_reason: str
    position_ms: int | None = None

    @field_validator("ended_reason")
    @classmethod
    def known_reason(cls, value: str) -> str:
        """An unknown reason is worse than no reason: it lands in the one table
        that cannot be regenerated and quietly poisons slice 2's training
        pairs."""
        if value not in ENDED_REASONS:
            raise ValueError(f"unknown ended_reason {value!r}")
        return value

class RatingWrite(BaseModel):
    """A thumbs up, a thumbs down, or a retraction.

    `Literal` rather than an int: a stray 5 landing in the one explicit signal
    the recommender has would be silent, permanent, and impossible to tell from
    a deliberate answer later.
    """

    track_rowid: int
    rating: Literal[-1, 1] | None


class PlaylistWrite(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    track_rowids: list[int] = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name is blank")
        return value


class PlaylistRename(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    _strip = field_validator("name")(PlaylistWrite._strip.__func__)


class LyricsOffsetWrite(BaseModel):
    """Stories 68/69's nudge. No bound: a systematically-off `.lrc` is a
    per-track correction the listener makes by feel, not a value this API
    has grounds to second-guess."""

    offset_ms: int


log = logging.getLogger("muse")

UNBUILT_SPA = """<!doctype html>
<title>Muse</title>
<body style="font-family: system-ui; padding: 3rem; line-height: 1.6">
<h1>Muse is running</h1>
<p>The web app has not been built yet. Run <code>npm install &amp;&amp; npm run
build</code> in <code>web/</code>, then reload.</p>
<p>The API is live: <a href="/api/health">/api/health</a></p>
</body>
"""


SWEEP_INTERVAL_SECONDS = 30


async def _sweep_forever(conn) -> None:
    """Close spans that stopped reporting.

    The phone died, the laptop lid closed, the tab was killed. Those spans
    would otherwise stay open forever and, worse, look like enormous listens.
    They close as `lost` — its own reason, because it is the one closure with
    no intent behind it, and slice 2 needs to be able to exclude it rather than
    guess.
    """
    while True:
        try:
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
            closed = library.sweep_stale(conn)
            if closed:
                log.info("Swept %d stale play span(s) as lost", closed)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A sweeper that dies takes the history's integrity with it, and
            # silently. Log and keep ticking.
            log.exception("Play-span sweep failed")


def _open_catalog(config: Config):
    """The mirrors are optional at startup.

    Without them the import lands rows with no duration or ISRC rather than
    failing, which keeps the app runnable on a machine that does not carry
    47 GB of SQLite.
    """
    if not config.catalog.exists():
        log.warning("Catalog mirror not found at %s; importing without it",
                    config.catalog)
        return EmptyCatalog()
    return SqliteCatalog(config.catalog)


def _ytdlp_thumbnail_search(artist_name: str, album_name: str) -> str | None:
    """Real `thumbnail_url` callback for `YtdlpThumbnailArt`: one yt-dlp
    search per album. Only ever called from inside `ChainedArtProvider
    .cover()`, which both `run_download`'s art step and `art_backfill`
    already wrap in try/except — a machine without the `ingest` extra
    installed just gets no cover from this tier, not a crash. `YoutubeDL`
    is `muse.ingest`'s already-guarded name (None when yt-dlp isn't
    installed), not a fresh unguarded import here.
    """
    from .ingest import YoutubeDL
    if YoutubeDL is None:
        return None
    try:
        with YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(
                f"ytsearch1:{artist_name} - {album_name}", download=False
            )
        entry = info["entries"][0] if isinstance(info, dict) and info.get("entries") else info
        return entry.get("thumbnail") if isinstance(entry, dict) else None
    except Exception:
        return None


def _ytdlp_probe_info(query_or_url: str) -> dict | None:
    """Real `ytdlp_info` callback for `TieredMetadataProvider`'s last-resort
    tier: one yt-dlp metadata probe, no download. This is what lets a
    `POST /api/jobs` payload with no `track_rowid` (an explicit `url`, or
    free-text artist/track) resolve at all -- without it, that tier's
    `if self._ytdlp_info is not None` guard is always False and every such
    payload fails to resolve. Same guarded-`YoutubeDL`/broad-except shape
    as `_ytdlp_thumbnail_search`, for the same reason: a machine without
    the `ingest` extra gets no result from this tier, not a crash.
    """
    from .ingest import YoutubeDL
    if YoutubeDL is None:
        return None
    try:
        with YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(query_or_url, download=False)
        if isinstance(info, dict) and info.get("entries"):
            return info["entries"][0]
        return info
    except Exception:
        return None


def _open_features(config: Config):
    """The features mirror is optional for the same reason the catalog is.

    Without it the dashboard's mood surfaces are empty and everything else —
    browsing, search, playback, history — is untouched. Degrade in quality,
    never in availability.
    """
    if not config.audio_features.exists():
        log.warning(
            "Audio-features mirror not found at %s; mood surfaces will be empty",
            config.audio_features,
        )
        return EmptyFeatures()
    return SqliteFeatures(config.audio_features)


def _release_for_group(mb, release_group_mbid: str) -> dict | None:
    """A release group, collapsed to the one release a download should use.

    Two rate-limited MusicBrainz requests in a fixed order -- pick the
    pressing, then read its tracklist -- because the second needs the first's
    answer. `None` when either half fails, since `discover` reports every
    failure the same way and the caller has nothing to show either way.

    Module-level rather than inline in the two callers because they reach it
    from different sides of the event loop: the browse route is already on
    FastAPI's threadpool, while `POST /api/jobs` is a coroutine and has to
    hand these two blocking, sleeping calls to `asyncio.to_thread`.
    """
    release_mbid = discover.pick_release_mbid(mb, release_group_mbid)
    if release_mbid is None:
        return None
    return discover.release_tracks(mb, release_mbid)


def _rowid_list(raw: str) -> list[int]:
    """Comma-separated track rowids from a query parameter, order preserved.

    A malformed entry is skipped rather than raising: this carries the
    client's playback history, and a recommendation that fails outright
    because one queue entry was mangled would trade quality for availability
    in the direction principle 4 forbids.
    """
    out = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


def _rowid_set(raw: str) -> set[int]:
    return set(_rowid_list(raw))


def _with_tracks(conn: sqlite3.Connection, results: list[dict]) -> list[dict]:
    """Attach the whole track to each scored result.

    Same reason the player state hydrates its queue: a bare rowid costs the
    client one round trip per row before it can draw anything, and a radio
    block is twenty of them. One batched lookup instead of N.

    `tracks_by_rowid` drops a rowid it cannot find, so a result whose track
    vanished between scoring and hydration is dropped too -- a scored row with
    a null track is something every caller would have to special-case, and
    there is nothing to render for it anyway.
    """
    rowids = [result["track_rowid"] for result in results]
    found = {track["track_rowid"]: track
             for track in queries.tracks_by_rowid(conn, rowids)}
    return [{**result, "track": found[result["track_rowid"]]}
            for result in results if result["track_rowid"] in found]


def create_app(
    config: Config | None = None,
    art_provider=None,
    mb=None,
    audio_encoder=None,
    lyrics_encoder=None,
    audio_va_encoder=None,
    lyrics_va_encoder=None,
    metadata_provider=None,
    audio_source=None,
    genius_client=None,
    chart_fetch=None,
) -> FastAPI:
    """`art_provider` is injectable so tests never reach MusicBrainz. `mb` is
    the one MusicBrainz client this process gets, injectable for the same
    reason and resolved in the lifespan below.
    `audio_encoder`/`lyrics_encoder`/`audio_va_encoder`/`lyrics_va_encoder`
    are injectable the same way, so tests can exercise `config.embed_batch`
    without loading CLAP, MiniLM, or the emotion models; all default to the
    real thing. `metadata_provider`/`audio_source`/`genius_client` are
    injectable the same way, so tests exercise `POST /api/jobs` without
    touching yt-dlp, MusicBrainz, or Genius."""
    config = config or Config.from_env()
    bus = JobBus()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = library.connect(config.library_db)
        app.state.conn = conn
        app.state.config = config
        app.state.bus = bus
        # One MusicBrainz client for the whole process, shared by everything
        # that talks to the service: the art backfill, run_download's art
        # step, the download worker's metadata tier, and the discovery
        # routes. `RateLimiter` holds its lock across the sleep, so the
        # 1 req/s floor MusicBrainz asks for is true across threads -- but
        # only within one instance. Two clients here would each honour a
        # floor of their own and jointly ask for twice what we promised;
        # `MusicBrainzClient`'s own docstring says as much.
        #
        # Injectable the same way `art_provider` is, and for the same
        # reason: tests must never reach the network.
        resolved_mb = mb or MusicBrainzClient()
        app.state.mb = resolved_mb
        loop = asyncio.get_running_loop()
        bus.attach(loop)

        version = library.current_version(conn)
        log.info("Library at %s, schema version %d", config.library_db, version)

        if config.manifest.exists():
            catalog = _open_catalog(config)
            result = library.import_manifest(
                library.load_manifest(config.manifest),
                catalog,
                conn,
                root=config.root,
            )
            log.info(
                "Imported manifest: %d new, %d updated, %s",
                result.inserted,
                result.updated,
                result.by_status,
            )
        else:
            log.warning("No manifest at %s; nothing to import", config.manifest)

        # Collaborations an older download stored as one joined artist name.
        repaired = library_tracks.repair_joined_credits(conn, _open_catalog(config))
        if repaired:
            log.info("Split %d joined artist credit(s)", repaired)

        # What the library sounds like, copied out of the mirror once per
        # track, ever. First start spends about a second on 723 lookups; every
        # start after that asks about nothing, because a track the mirror had
        # no answer for is remembered as asked.
        sync = library_features.sync_features(conn, _open_features(config))
        if sync.asked:
            log.info(
                "Audio features: asked about %d track(s), stored %d, %d not in "
                "the mirror",
                sync.asked,
                sync.stored,
                sync.without,
            )

        # A job left `running` by a killed process would otherwise block the
        # next one forever. art_backfill/embed are idempotent full sweeps,
        # so reaping their queued rows too (abandon_running_of_kind's normal
        # behavior) is harmless -- the next run just re-derives from
        # scratch. download is different: a queued-but-not-started job is
        # exactly as safe to run after a restart as before one, so its
        # queued rows are deliberately left alone here and re-fed to the
        # worker below instead. Only its running rows (interrupted
        # mid-pipeline, not resumable partway through) are reaped.
        abandoned = (
            job_rows.abandon_running_of_kind(conn, ART_BACKFILL)
            + job_rows.abandon_running_of_kind(conn, embed_module.EMBED)
        )
        for stale_job_id in job_rows.running_ids_of_kind(conn, DOWNLOAD):
            job_rows.update(conn, stale_job_id, state=job_rows.ERROR,
                              message="Interrupted by a restart")
            abandoned += 1
        if abandoned:
            log.info("Closed %d job(s) interrupted by a restart", abandoned)

        sweeper = asyncio.create_task(_sweep_forever(conn))

        # Home's two album shelves cost ~5 s the first time (album centroids,
        # then two catalog artist searches over the 17.8 GB mirror) and ~0.2 s
        # after. Pay it here, off the event loop, so the first person to open
        # Home does not.
        def _warm_home_shelves() -> None:
            try:
                now_ms = int(time.time() * 1000)
                nearest.for_fans_of(conn, now_ms)
                nearest.recommended_albums(conn, now_ms, _open_catalog(config))
            except Exception:
                log.exception("Home shelf warm-up failed")

        warm_home = asyncio.create_task(asyncio.to_thread(_warm_home_shelves))

        # Cover art, in the background, never in a request. The first start
        # spends ninety seconds on it; every start after that finds nothing to
        # do, because the decision cache remembers the albums that have no art
        # as firmly as the ones that do. MusicBrainz, then iTunes; never yt-dlp
        # at startup (Decision 5 C).
        # One instance: one 3 s floor between iTunes requests for the whole process.
        itunes_art = ItunesArt()
        backfill = None
        if config.art_backfill:
            backfill = asyncio.create_task(
                run_art_backfill(
                    conn,
                    art_provider or ChainedArtProvider(
                        [CoverArtArchive(mb=resolved_mb), itunes_art]
                    ),
                    config.art_root,
                    bus,
                )
            )

        # The embed batch, in the background, never in a request -- same
        # reasoning as the art backfill. Off by default (config.embed_batch):
        # unlike art, this can run to ~14 minutes over the whole library and
        # the real encoders need optional, heavy dependencies.
        #
        # Factories, not instances: `ClapAudioEncoder()` alone is ~70 s of
        # model load, and `run_embed_backfill` already runs the batch itself
        # inside `asyncio.to_thread`. Constructing the encoder here instead
        # of there would block this event loop for that whole load -- every
        # concurrent request stalls -- defeating the point of backgrounding
        # it at all.
        def _audio_encoder():
            return audio_encoder or ClapAudioEncoder()

        def _lyrics_encoder():
            return lyrics_encoder or MiniLmLyricsEncoder()

        def _audio_va_encoder():
            return audio_va_encoder or MerAudioVAEncoder()

        def _lyrics_va_encoder():
            return lyrics_va_encoder or TextVAEncoder()

        embed_task = None
        if config.embed_batch:
            embed_task = asyncio.create_task(
                run_embed_backfill(
                    conn, _audio_encoder, _lyrics_encoder, bus,
                    _audio_va_encoder, _lyrics_va_encoder,
                )
            )

        # The download worker: one consumer for the app's lifetime, fed by
        # POST /api/jobs below and by whatever this restart found still
        # queued.
        download_queue: asyncio.Queue = asyncio.Queue()
        app.state.download_queue = download_queue

        resolved_metadata_provider = metadata_provider or TieredMetadataProvider(
            catalog=_open_catalog(config), mb=resolved_mb,
            ytdlp_info=_ytdlp_probe_info,
        )
        resolved_audio_source = audio_source or YtDlpAudioSource()
        resolved_genius_client = (
            genius_client if genius_client is not None else build_genius_client()
        )
        # The same injected art_provider serves both art_backfill (above)
        # and run_download's art step when the caller passes one explicitly
        # (tests). Production: backfill is MusicBrainz then iTunes, with no
        # yt-dlp at startup (Decision 5 C); a download adds the yt-dlp
        # thumbnail last. The shared `itunes_art` keeps one request floor.
        resolved_art_provider = art_provider or ChainedArtProvider([
            CoverArtArchive(mb=resolved_mb),
            itunes_art,
            YtdlpThumbnailArt(session=None, thumbnail_url=_ytdlp_thumbnail_search),
        ])

        def _payload_to_kwargs(payload: dict) -> dict:
            return {
                "payload": payload,
                "metadata_provider": resolved_metadata_provider,
                "audio_source": resolved_audio_source,
                "genius_client": resolved_genius_client,
                "music_root": config.music_root,
                "art_root": config.art_root,
                "art_provider": resolved_art_provider,
                "embed_backfill": lambda: asyncio.run_coroutine_threadsafe(
                    run_embed_backfill(
                        conn, _audio_encoder, _lyrics_encoder, bus,
                        _audio_va_encoder, _lyrics_va_encoder,
                    ),
                    loop,
                ).result(),
            }

        # MERT over freshly downloaded tracks, once the download queue
        # drains. Behind `config.content_embed` (MUSE_CONTENT_EMBED) because
        # that flag already means "this host is the one that runs the
        # content batch" -- /api/health reports it as `expected_on_this_
        # host` -- and a host without `universal_reco/.venv` must not shell
        # out to a batch that can only fail. The nightly timer stays the
        # backstop on either kind of host.
        content_backfill = None
        if config.content_embed:
            async def content_backfill():
                return await run_content_backfill(conn, config.library_db)

        download_worker = asyncio.create_task(
            run_download_worker(conn, download_queue, run_download,
                                  _payload_to_kwargs, bus, content_backfill,
                                  gap_seconds=config.download_gap_seconds)
        )
        for queued_job_id in job_rows.queued_ids_of_kind(conn, DOWNLOAD):
            download_queue.put_nowait(queued_job_id)

        try:
            yield
        finally:
            sweeper.cancel()
            if backfill is not None:
                backfill.cancel()
            if embed_task is not None:
                embed_task.cancel()
            download_worker.cancel()
            # Cancelling a to_thread task leaves its thread running, and it
            # would query the connection closed on the next line (segfault).
            await warm_home
            conn.close()

    app = FastAPI(title="Muse", lifespan=lifespan)
    app.state.chart = charts.ChartCache(
        chart_fetch or charts.default_fetch, config.chart_base_url, config.chart_storefront)

    @app.get("/api/charts/top")
    def charts_top() -> dict:
        songs = app.state.chart.songs() or []
        return {"songs": [
            {**s, "track": queries.find_track(app.state.conn, s["name"], s["artist_name"])}
            for s in songs
        ]}

    @app.get("/api/health")
    def health() -> dict:
        """Everything true about this deployment rather than this library.

        `catalog_freeze` rides here because it is exactly that: a fact about
        the mirror this server was started against, env-overridable, and the
        one date the whole post-freeze browse pivots on. The client needs it
        twice -- as the `since` it passes to `/api/discover/artists/{mbid}/
        releases`, and as the sentence that says when the mirror stopped --
        and a second copy of the date in TypeScript is a copy nothing keeps
        in step with `MUSE_CATALOG_FREEZE`.
        """
        last_retrain = job_rows.last(app.state.conn, TASTE_RETRAIN, state=job_rows.DONE)
        last_content_batch = job_rows.last(
            app.state.conn, CONTENT_EMBED, state=job_rows.DONE
        )
        last_content_run = job_rows.last(app.state.conn, CONTENT_EMBED)
        return {
            **queries.health(app.state.conn),
            "catalog_freeze": app.state.config.catalog_freeze,
            "embeddings": library_embeddings.coverage(app.state.conn),
            "emotion": library_embeddings.emotion_coverage(app.state.conn),
            "taste": {
                **library_taste.coverage(app.state.conn),
                "last_retrain_at": last_retrain["updated_at"] if last_retrain else None,
            },
            "content": {
                **library_content.coverage(app.state.conn),
                "expected_on_this_host": app.state.config.content_embed,
                "last_batch_at": (
                    last_content_batch["updated_at"] if last_content_batch else None
                ),
                "last_run_at": (
                    last_content_run["updated_at"] if last_content_run else None
                ),
                "last_run_state": (
                    last_content_run["state"] if last_content_run else None
                ),
            },
        }

    @app.get("/api/contract")
    def contract() -> ContractVersion:
        """The Cadence contract version (docs/contracts/cadence.md).

        Cadence reads a 404 here as the pre-contract baseline.
        """
        return ContractVersion(version=CONTRACT_VERSION)

    @app.get("/api/dashboard")
    def dashboard() -> dict:
        """Everything the home screen shows, in one request.

        One payload rather than six: this is the first screen, and six round
        trips over a tailnet is six chances to look slow.
        """
        conn = app.state.conn
        now_ms = int(time.time() * 1000)
        return {
            **insights.dashboard(conn),
            "for_fans_of": nearest.for_fans_of(conn, now_ms),
            "recommended_albums": nearest.recommended_albums(
                conn, now_ms, _open_catalog(app.state.config)
            ),
        }

    @app.get("/api/mixes/{band}")
    def mix(band: str) -> dict:
        """A mood mix: that band, shuffled, capped.

        Nothing is stored — asking again gives a different cut of the same
        band. That is the whole contract, and it is why there is no DELETE.
        """
        if band not in library_features.BAND_SQL:
            raise HTTPException(404, f"No mix {band!r}")
        conn = app.state.conn
        # Same rule as the dashboard's map: our own emotion vectors when there
        # are any, the Spotify mirror's features when there are not. A mix has
        # to come from the same plane the map drew, or tapping a quadrant plays
        # a different set from the one under the finger.
        if library_emotion.coverage(conn)["measured"]:
            tracks = library_emotion.band_tracks(
                conn, band, library_features.MIX_LIMIT
            )
        else:
            tracks = library_features.band_tracks(conn, band)
        return {
            "band": band,
            "label": library_features.BAND_LABELS[band],
            "tracks": tracks,
        }

    @app.get("/api/library/artists")
    def artists() -> list[dict]:
        return queries.artists(app.state.conn)

    @app.get("/api/library/albums")
    def library_albums() -> list[dict]:
        """Every album at once, for the browse gallery.

        The client holds the whole set and filters it locally, so this is asked
        for on open and not again.
        """
        return queries.albums(app.state.conn)

    @app.get("/api/library/songs")
    def library_songs() -> list[dict]:
        """Every owned song by title, for the Songs tab with an empty search."""
        return queries.songs(app.state.conn)

    @app.get("/api/library/artists/{artist_name}/albums")
    def artist_albums(artist_name: str) -> dict:
        albums = queries.albums_by_artist(app.state.conn, artist_name)
        if not albums:
            raise HTTPException(404, f"No albums for {artist_name!r}")
        return {"artist_name": artist_name, "albums": albums}

    @app.get("/api/library/albums/{album_rowid}/tracks")
    def album_tracks(album_rowid: int) -> dict:
        album = queries.album(app.state.conn, album_rowid)
        if album is None:
            raise HTTPException(404, f"No album {album_rowid}")
        return {**album, "tracks": queries.album_tracks(app.state.conn, album_rowid)}

    @app.get("/api/library/search")
    def search(q: str = "", limit: int = 60) -> dict:
        return {"q": q, "results": queries.search(app.state.conn, q, limit)}

    @app.get("/api/library/search/vibe")
    def vibe_search(q: str = "", n: int = 20) -> dict:
        """Find tracks by a text description of a sound (story 45).

        CLAP is a joint text-audio model, so the query embeds into the same
        space the stored `audio_vec`s live in and the whole feature is one
        encode call plus one pass over a matrix already in memory. PRD §504
        names this as the reason CLAP was chosen over MERT -- MERT is
        audio-only and could not serve this route at all.

        **This deliberately does not go through `rank()`.** There is no seed
        track, so there is no `query_vec_set`: a text query has no lyrics
        vector, no feature vector, and no emotion coordinate of its own, and
        fusing would mean inventing query-side values for five legs. A
        single-leg search is the honest shape.

        It is also the only route in this app that needs torch. If the
        `embed` extra is not installed it returns 503 rather than failing at
        import, which keeps the rest of the app working on a machine that
        cannot run CLAP -- degrade in quality, never in availability. The
        results are exploratory: §504's own spot-check found text-to-audio
        retrieval visibly noisier than audio-to-audio.
        """
        if not q.strip():
            return {"q": q, "results": []}

        encoder = getattr(app.state, "text_encoder", None)
        if encoder is None:
            try:
                encoder = ClapAudioEncoder()
            except ImportError as exc:
                raise HTTPException(
                    503,
                    "Vibe search needs the 'embed' extra (uv sync --extra"
                    f" embed): {exc}",
                ) from exc
            app.state.text_encoder = encoder

        query = encoder.encode_text(q)
        scored = []
        for rowid, entry in library.embeddings.vectors(app.state.conn).items():
            audio = entry.get("audio_vec")
            if audio is None:
                continue
            scored.append((reco.cosine_sim(query, audio), rowid))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return {
            "q": q,
            "results": _with_tracks(
                app.state.conn,
                [{"track_rowid": rowid, "score": score}
                 for score, rowid in scored[:n]],
            ),
        }

    # --- discovery browse ---------------------------------------------------
    #
    # Every route here is a plain `def`, unlike most of what follows. FastAPI runs a sync handler on
    # its threadpool; an `async def` would hold the event loop through
    # `RateLimiter`'s 1.1 s sleep and stall playback, the job stream and
    # every other request for the duration.

    @app.get("/api/discover/artists")
    def discover_artists(q: str, limit: int = discover.ARTIST_SEARCH_LIMIT) -> list[dict]:
        """Search MusicBrainz for an artist: the only browse search, with the
        owned count casefolded like the old catalog count.

        The score rides along so the client can say how sure the match is.
        """
        found = discover.search_artists(app.state.mb, q, limit=limit)
        if found is None:
            raise HTTPException(502, "MusicBrainz did not answer")
        owned: dict[str, int] = {}
        for name, count in app.state.conn.execute(
            "SELECT artist_name, COUNT(*) FROM tracks"
            " WHERE status IN ('done', 'missing_file')"
            " GROUP BY artist_name"
        ):
            owned[name.casefold()] = owned.get(name.casefold(), 0) + count
        # shortcut: tracks carry no artist mbid, so same-named artists are told
        # apart only by MusicBrainz's order -- the best-scored one gets the
        # count. Upgrade if tracks ever store an artist mbid.
        return [
            {**artist, "in_library": owned.pop(artist["name"].casefold(), 0)}
            for artist in found
        ]

    @app.get("/api/discover/artists/{artist_mbid}/releases")
    def discover_releases(
        artist_mbid: str = PathParam(pattern=MBID_PATTERN),
        kind: Literal["album", "single"] = "album",
    ) -> list[dict]:
        """This artist's release groups of one `kind`, newest first.

        `appears_on` is dropped; the page cap on the browse walk remains.

        The empty list and the 502 are different answers and the screen words
        them differently: `[]` means MusicBrainz answered and this artist has
        no such releases, 502 means it did not answer at all.
        """
        found = discover.release_groups(
            app.state.mb, artist_mbid, release_type=discover.RELEASE_TYPES[kind]
        )
        if found is None:
            raise HTTPException(
                502, f"MusicBrainz did not answer for {artist_mbid}"
            )
        return found

    @app.get("/api/discover/release-groups/{release_group_mbid}/tracks")
    def discover_release_group_tracks(
        release_group_mbid: str = PathParam(pattern=MBID_PATTERN),
    ) -> dict:
        """One release group's tracklist, each row flagged with what we hold.

        A group holds every pressing of one record, so the pressing is chosen
        here rather than offered -- see `pick_release_mbid`. The 502 is the
        honest answer for both halves of that: with no release and no
        tracklist there is nothing to render, and MusicBrainz not answering is
        not this library's failure.

        `in_library` uses the same identity rule as the download's dedupe
        (`find_held`). Only actual holdings count: a failed attempt leaves a
        row behind, and reading that as owned would hide the one track the
        person came back for.

        **Each track row is self-sufficient**, carrying `release_mbid` and
        `album_name` as well as its own artist credit, even though both also
        sit at the top level for the screen's header. That duplication is the
        point: it means a client queues a track by posting the row it already
        has, with no merge. Handing back release-level and track-level halves
        would make every client compose them, and `{...track, ...release}` --
        the natural way round to write it -- silently restores the
        compilation bug `create_job`'s merge order is pinned against, in a
        language that test cannot reach.
        """
        release = _release_for_group(app.state.mb, release_group_mbid)
        if release is None:
            raise HTTPException(
                502, f"MusicBrainz did not answer for {release_group_mbid}"
            )
        return {
            **release,
            "tracks": [
                {
                    # Release-level first, the track's own second -- the same
                    # order, for the same reason, as `create_job`'s
                    # `mb_release` branch. `release_tracks` already gave this
                    # track its own artist credit; the release's must not
                    # overwrite it.
                    "release_mbid": release["release_mbid"],
                    "album_name": release["album_name"],
                    **track,
                    "in_library": library_tracks.find_held(
                        app.state.conn,
                        isrc=track["isrc"],
                        recording_mbid=track["recording_mbid"],
                        artist_name=track["artist_name"],
                        track_name=track["track_name"],
                        duration_ms=track["duration_ms"],
                        statuses=("done", "missing_file"),
                    ) is not None,
                }
                for track in release["tracks"]
            ],
        }

    @app.get("/api/tracks/{track_rowid}")
    def track(track_rowid: int) -> dict:
        found = queries.track(app.state.conn, track_rowid)
        if found is None:
            raise HTTPException(404, f"No track {track_rowid}")
        return found

    @app.get("/api/recommend/{track_rowid}")
    def recommend_next(
        track_rowid: int,
        n: int = 20,
        exclude: str = "",
        recent: str = "",
        w_audio: float | None = None,
        w_feat: float | None = None,
        w_lyr: float | None = None,
        w_emo: float | None = None,
        w_taste: float | None = None,
        mood: str = "",
    ) -> dict:
        """Tracks to play after this one, best first.

        The results are a sequence rather than a bare ranked list: artist
        spacing holds across the block, so story 35's radio is this call with
        a large `n` and story 28's play-next is the same call with `n=1`.

        There is no radio endpoint and no server-side session. The client
        owns the queue, so the moving query point is `track_rowid` and the
        rolling exclusion set is `exclude` -- which keeps this route
        stateless and keeps a second source of truth about what is playing
        from existing.

        The weight names are ADR 0003's. PRD §625 spells them `w_clap` and
        `w_fb`; both are stale -- `w_clap` names one audio leg where ADR 0004
        now binds two into a shared budget, and `w_fb` names the
        `feedback_score` ADR 0003 replaced. There is deliberately no `w_clap`
        or `w_content` here: ADR 0004's first Binding clause makes the two
        audio legs one term with an internal split.
        """
        overrides = {
            "w_audio": w_audio, "w_feat": w_feat, "w_lyr": w_lyr,
            "w_emo": w_emo, "w_taste": w_taste,
        }
        given = {name: value for name, value in overrides.items()
                 if value is not None}
        weights = {**reco.DEFAULT_WEIGHTS, **given} if given else None

        seed: int | dict = track_rowid
        if mood:
            try:
                v, a = (float(part) for part in mood.split(","))
            except ValueError:
                raise HTTPException(422, "mood must be 'valence,arousal'")
            if not (-1 <= v <= 1 and -1 <= a <= 1):
                raise HTTPException(422, "mood values must be in [-1, 1]")
            vectors = recommend.load_vectors(app.state.conn)
            if track_rowid not in vectors:
                raise HTTPException(404, f"No recommendable track {track_rowid}")
            target = recommend.mood_target(vectors, v, a)
            if target is not None:
                seed = {**vectors[track_rowid], "emotion_vec": list(target)}

        results = recommend.recommend_for(
            app.state.conn, seed, n=n,
            exclude=_rowid_set(exclude) | ({track_rowid} if mood else set()),
            recent=_rowid_list(recent), weights=weights,
        )
        if results is None:
            raise HTTPException(404, f"No recommendable track {track_rowid}")
        return {
            "seed": track_rowid,
            "results": _with_tracks(app.state.conn, results),
        }

    @app.get("/api/arc/{seed_rowid}")
    def arc_route(
        seed_rowid: int,
        mode: str = "playlist",
        n: int = 12,
        pos: int = 0,
        lift: int = 1,
        exclude: str = "",
        recent: str = "",
    ) -> dict:
        """A playlist or a radio block along the seed's arc
        (`docs/specs/2026-10-17-arc-engine.md` D7, ADR 0009).

        Stateless, like `/api/recommend`: the client owns the queue, and a
        radio moves on by sending `pos` (arc picks already placed) with
        `recent` (most-recent-first; its head is the previous track) and
        `exclude`. The curve is recomputed from `pos` on every call.
        """
        if mode not in {"playlist", "radio"}:
            raise HTTPException(422, f"mode={mode!r} must be 'playlist' or 'radio'")
        if not (1 <= n <= 100):
            raise HTTPException(422, f"n={n} must be between 1 and 100")
        if pos < 0:
            raise HTTPException(422, f"pos={pos} must not be negative")
        if lift not in (0, 1):
            raise HTTPException(422, f"lift={lift} must be 0 or 1")
        conn = app.state.conn
        block = arc_engine.arc_block(
            conn, seed_rowid, mode=mode, n=n, pos=pos, lift=bool(lift),
            exclude=_rowid_set(exclude), recent=_rowid_list(recent),
        )
        if block is None:
            raise HTTPException(404, f"No recommendable track {seed_rowid}")
        return {
            "seed": seed_rowid,
            "mode": mode,
            "curve": block["curve"],
            "arc": block["arc"],
            "reason": block["reason"],
            "exhausted": block["exhausted"],
            "results": _with_tracks(conn, block["results"]),
        }

    @app.post("/api/arc")
    def arc_plan_route(body: ArcPlan) -> dict:
        """Plan from a posted request spec (docs/plans/2026-10-18-prompt-arc.md).

        The request spec round-trips through the client: the reply carries
        the validated spec, and a radio sends it back with `pos`, `recent`
        and `exclude` for its next block. No server-side session (ADR 0003).
        """
        if body.mode not in {"playlist", "radio"}:
            raise HTTPException(422, f"mode={body.mode!r} must be 'playlist' or 'radio'")
        if not (1 <= body.n <= 100):
            raise HTTPException(422, f"n={body.n} must be between 1 and 100")
        if body.pos < 0:
            raise HTTPException(422, f"pos={body.pos} must not be negative")
        try:
            spec = request_spec.check(body.spec)
        except request_spec.SpecError as error:
            raise HTTPException(422, f"spec.{error.field}: {error}")
        conn = app.state.conn
        block = arc_engine.plan(
            conn, spec, mode=body.mode, n=body.n, pos=body.pos,
            exclude=set(body.exclude), recent=list(body.recent),
        )
        if block is None:
            raise HTTPException(404, f"No recommendable track {spec['seed']}")
        return {
            "spec": spec,
            "mode": body.mode,
            "curve": block["curve"],
            "arc": block["arc"],
            "reason": block["reason"],
            "exhausted": block["exhausted"],
            "results": _with_tracks(conn, block["results"]),
        }

    @app.get("/api/player/state")
    def player_state() -> dict:
        state = library.read_state(app.state.conn)
        # Hydrated, because the client needs whole tracks and the alternative
        # is a second round trip on every app open.
        return {
            **state,
            "queue": queries.tracks_by_rowid(app.state.conn, state["queue"]),
        }

    @app.put("/api/player/state")
    def put_player_state(write: StateWrite) -> dict:
        conn = app.state.conn
        accepted = library.write_state(
            conn,
            queue=write.queue,
            queue_index=write.queue_index,
            position_ms=write.position_ms,
            client_state=write.client_state,
            shuffle=int(write.shuffle),
            repeat_mode=write.repeat_mode,
            radio_seed=write.radio_seed,
            updated_by=write.updated_by,
            radio_added=write.radio_added,
        )
        state = library.read_state(conn)
        return {
            "accepted": accepted,
            **state,
            "queue": queries.tracks_by_rowid(conn, state["queue"]),
        }

    @app.post("/api/plays", status_code=201)
    def open_play(open: PlayOpen) -> dict:
        play_id = library.open_span(
            app.state.conn,
            track_rowid=open.track_rowid,
            duration_ms=open.duration_ms,
            source=open.source,
        )
        return {"id": play_id}

    @app.patch("/api/plays/{play_id}")
    def beat_play(play_id: int, beat: PlayBeat) -> dict:
        library.heartbeat(
            app.state.conn,
            play_id,
            position_ms=beat.position_ms,
            seek_count=beat.seek_count,
        )
        return {"ok": True}

    @app.post("/api/plays/{play_id}/close")
    def close_play(play_id: int, close: PlayClose) -> dict:
        library.close_span(
            app.state.conn,
            play_id,
            ended_reason=close.ended_reason,
            position_ms=close.position_ms,
        )
        return {"ok": True}

    # --- cover art --------------------------------------------------------
    #
    # Art is a file on disk by the time anyone asks for it. Nothing here can
    # reach the network: a request that could would make a slow MusicBrainz
    # into a slow album page.

    def _art_response(path: str | None, tag: str) -> FileResponse:
        if not path or not Path(path).exists():
            # The client knows from `has_art` and renders its placeholder, so
            # this is the rare case of a cover deleted from under us rather
            # than the normal absence of one.
            raise HTTPException(404, "No cover art")
        return FileResponse(
            path,
            headers={
                # Art for an album is fetched once and never changes. A year
                # is not an exaggeration.
                "cache-control": "public, max-age=31536000, immutable",
                "etag": f'"{tag}"',
            },
        )

    @app.get("/api/albums/{album_rowid}/art")
    def album_art(album_rowid: int) -> FileResponse:
        path = library_art.album_art_path(app.state.conn, album_rowid)
        return _art_response(path, f"album-{album_rowid}")

    @app.get("/api/tracks/{track_rowid}/art")
    def track_art(track_rowid: int) -> FileResponse:
        found = queries.track_paths(app.state.conn, track_rowid)
        if found is None:
            raise HTTPException(404, f"No track {track_rowid}")
        return _art_response(found["art_path"], f"track-{track_rowid}")

    @app.get("/api/playlists")
    def list_playlists() -> list[dict]:
        return library_playlists.summaries(app.state.conn)

    @app.post("/api/playlists", status_code=201)
    def create_playlist(write: PlaylistWrite) -> dict:
        try:
            pid = library_playlists.create(app.state.conn, write.name, write.track_rowids)
        except sqlite3.IntegrityError:
            raise HTTPException(422, "Unknown track in playlist")
        return library_playlists.get(app.state.conn, pid)

    @app.get("/api/playlists/{playlist_id}")
    def get_playlist(playlist_id: int) -> dict:
        found = library_playlists.get(app.state.conn, playlist_id)
        if found is None:
            raise HTTPException(404, f"No playlist {playlist_id}")
        return found

    @app.patch("/api/playlists/{playlist_id}")
    def rename_playlist(playlist_id: int, write: PlaylistRename) -> dict:
        if not library_playlists.rename(app.state.conn, playlist_id, write.name):
            raise HTTPException(404, f"No playlist {playlist_id}")
        return library_playlists.get(app.state.conn, playlist_id)

    @app.delete("/api/playlists/{playlist_id}", status_code=204)
    def delete_playlist(playlist_id: int) -> Response:
        if not library_playlists.delete(app.state.conn, playlist_id):
            raise HTTPException(404, f"No playlist {playlist_id}")
        return Response(status_code=204)

    @app.post("/api/ratings")
    def rate(write: RatingWrite) -> dict:
        """The only explicit signal in the system.

        `rating: null` clears. Deciding that a second tap on the same thumb
        means "clear" belongs to the client, which is the only thing that knows
        what was tapped — the server just records the answer.
        """
        conn = app.state.conn
        if queries.track(conn, write.track_rowid) is None:
            raise HTTPException(404, f"No track {write.track_rowid}")
        rating = library.set_rating(conn, write.track_rowid, write.rating)
        return {"track_rowid": write.track_rowid, "rating": rating}

    @app.get("/api/tracks/{track_rowid}/feedback")
    def track_feedback(track_rowid: int) -> dict:
        """Debug surface for `feedback_score` (ticket 03) -- inspectable
        before anything downstream (the taste embedding, ticket 04) consumes
        it, so a wrong weight or a decay bug shows up here first."""
        conn = app.state.conn
        if queries.track(conn, track_rowid) is None:
            raise HTTPException(404, f"No track {track_rowid}")
        return {
            "track_rowid": track_rowid,
            "feedback_score": feedback_score(conn, track_rowid, now=int(time.time())),
        }

    # --- lyrics -----------------------------------------------------------

    @app.get("/api/tracks/{track_rowid}/lyrics", response_class=PlainTextResponse)
    def track_lyrics(track_rowid: int) -> str:
        """Plain text, read-only. The compact now-playing panel's route --
        no timings, no parsed lines, no offset. `/lyrics/synced` below is the
        richer shape for the full-screen view; this one stays exactly as it
        was before that existed, since a listener glancing at the words
        while doing something else has no use for either.
        """
        found = queries.track_paths(app.state.conn, track_rowid)
        if found is None:
            raise HTTPException(404, f"No track {track_rowid}")
        text = read_lyrics(found["lyrics_path"])
        if text is None:
            raise HTTPException(404, "No lyrics for that track")
        return text

    @app.get("/api/tracks/{track_rowid}/lyrics/synced")
    def track_lyrics_synced(track_rowid: int) -> dict:
        """The full-screen lyrics view's route (stories 59-77).

        `synced` decides which of `lines`/`plain` is populated -- never
        both, since O10 makes the `.lrc` the display authority whenever
        timings exist at all, and a view holding two texts that might
        disagree is exactly the drift O10 exists to prevent. `lines` is
        already sorted and already offset-free; `offset_ms` is applied by
        the client at render time (stories 68, 69), never baked into a
        timestamp here, so a later nudge needs no re-fetch.

        A track with an `.lrc` that fails to parse into any line at all
        (empty, or nothing but metadata tags) falls back to the unsynced
        shape rather than returning an empty `lines` array a client would
        have to special-case identically to "no timings".
        """
        found = queries.track_paths(app.state.conn, track_rowid)
        if found is None:
            raise HTTPException(404, f"No track {track_rowid}")
        plain = read_lyrics(found["lyrics_path"])
        if plain is None:
            raise HTTPException(404, "No lyrics for that track")

        lines = None
        if found["file_path"]:
            lrc_path = Path(found["file_path"]).with_suffix(".lrc")
            try:
                lrc_text = lrc_path.read_text(encoding="utf-8")
            except OSError:
                lrc_text = None
            if lrc_text is not None:
                parsed = parse_lrc(lrc_text)
                if parsed:
                    lines = [{"ms": line.ms, "text": line.text} for line in parsed]

        offset_ms = library_lyrics_state.get_offset_ms(app.state.conn, track_rowid)
        return {
            "synced": lines is not None,
            "lines": lines,
            "plain": None if lines is not None else plain,
            "offset_ms": offset_ms,
        }

    @app.patch("/api/tracks/{track_rowid}/lyrics/offset")
    def track_lyrics_offset(track_rowid: int, write: LyricsOffsetWrite) -> dict:
        """The nudge (stories 68, 69): applied at render time only, never
        rewritten into the `.lrc` file, so resetting it is free and the file
        on disk never drifts from what LRCLIB returned. 404s on a track the
        sync batch has never timed -- an offset on a file that does not
        exist has nothing to correct.
        """
        ok = library_lyrics_state.set_offset(
            app.state.conn, track_rowid, write.offset_ms
        )
        if not ok:
            raise HTTPException(404, f"No synced lyrics for track {track_rowid}")
        return {"offset_ms": write.offset_ms}

    # --- jobs -------------------------------------------------------------

    @app.get("/api/jobs")
    def list_jobs(limit: int = 20) -> list[dict]:
        return job_rows.recent(app.state.conn, limit)

    @app.post("/api/jobs")
    async def create_job(payload: dict):
        """Queue a download. `payload` is one of:
        `{"kind": "url", "url": str, "artist_name": str,
        "track_name": str}` for an explicit source (PRD story 86), or the two
        MusicBrainz kinds behind the discovery browse -- `{"kind": "mb", ...}`
        for one track and `{"kind": "mb_release", "release_group_mbid": str}`
        for a whole record.

        Answers `{"job_ids": [...], "skipped": [...]}`. `skipped` is empty for
        every kind but `mb_release`, which is the only one that builds
        payloads out of data the caller never saw, and it is always present so
        that no client reads it conditionally.
        """
        conn = app.state.conn
        catalog = _open_catalog(app.state.config)
        kind = payload.get("kind")
        # What was asked for and will not be downloaded. Only `mb_release`
        # ever fills this, but every kind returns the key, so no client has
        # to branch on whether it is there before reading it.
        skipped: list[dict] = []

        if kind == "mb":
            # This branch resolves nothing. The browse screen fetched the
            # release in order to render it, so every fact is already on the
            # payload, and asking again would cost one rate-limited request
            # per track for what is in hand -- there is no disk cache behind
            # the client. `TieredMetadataProvider`'s MusicBrainz tier reads
            # exactly these keys and does no I/O either.
            #
            # Copied key by key rather than passed through: what the client
            # sends is not what goes on a job. In particular a `track_rowid`
            # must never survive into an `mb` payload -- a payload carrying
            # both that and a `recording_mbid` writes an incoherent row
            # (`metadata_tier='musicbrainz'` on `origin='catalog'`) the
            # moment the catalog misses on the rowid. This allowlist is the
            # only reason that shape is unreachable.
            #
            # `MB_REQUIRED_FIELDS` (above) says which keys must actually be
            # there and why. `.get()` on everything else is deliberate:
            # MusicBrainz often has no ISRC, and a single has no track number
            # worth the name, so those are legitimately null. Missing
            # `recording_mbid` is the one that would be silent -- every
            # MusicBrainz field lands None, the MB tier declines, and the job
            # falls through to yt-dlp, which downloads *something*, tags it
            # off a YouTube upload and records `metadata_tier='ytdlp'` on a
            # track asked for by name from MusicBrainz.
            missing = [
                field for field in MB_REQUIRED_FIELDS if not payload.get(field)
            ]
            if missing:
                raise HTTPException(
                    400,
                    f"An mb job needs {', '.join(missing)}."
                    " recording_mbid is what the MusicBrainz metadata tier"
                    " keys on, and without it the download falls through to"
                    " yt-dlp and quietly returns whatever YouTube had; the"
                    " rest are the columns `tracks` declares NOT NULL."
                    " Every one of them is on each row that"
                    " /api/discover/release-groups/{mbid}/tracks returns.",
                )
            track_payloads = [{
                key: payload.get(key) for key in (
                    "recording_mbid", "release_mbid", "artist_name",
                    "artist_names", "album_name", "track_name", "track_no",
                    "disc_no", "total_tracks", "duration_ms", "isrc",
                )
            }]
        elif kind == "mb_release":
            # Two rate-limited MusicBrainz requests, handed to a thread. This
            # handler is a coroutine, and `RateLimiter` sleeps on the wall
            # clock for up to 1.1 s per call -- run inline, the whole app
            # stops for two seconds every time someone queues a record.
            release = await asyncio.to_thread(
                _release_for_group, app.state.mb, payload["release_group_mbid"]
            )
            if release is None:
                raise HTTPException(
                    502,
                    "MusicBrainz did not answer for "
                    f"{payload['release_group_mbid']}",
                )
            # Release-level fields first, per-track fields second, so the
            # track's own keys win the merge. `release_tracks` gives every
            # track its own artist credit and only falls back to the
            # release's when MusicBrainz has none -- spreading the release
            # last would overwrite that and stamp every track on a
            # compilation with the release artist. PRD line 456 records that
            # `fix_manifest_artists.py` had to exist because the old
            # downloader did exactly this.
            #
            # `release_date` is not spread: nothing on a job payload reads it,
            # and no `track_rowid` appears here for the reason the `mb` branch
            # above spells out.
            built = [
                {
                    "release_mbid": release["release_mbid"],
                    "album_name": release["album_name"],
                    "artist_name": release["artist_name"],
                    "artist_names": release["artist_names"],
                    **track,
                }
                for track in release["tracks"]
            ]
            # The same guard the `mb` branch applies, on the same fields, for
            # the same reason -- these two kinds build payloads for one
            # `run_download`, and a release lacking an artist credit was
            # refused through one and written as `artist_name=""` through the
            # other. Checked after the merge, because the merge is where a
            # track's own empty credit would win over the release's.
            #
            # A hole here is MusicBrainz's, not the caller's: the request
            # named a release that exists. So the tracks that are whole are
            # queued and the rest come back in `skipped` for the screen to
            # say out loud, rather than one untitled track refusing a record.
            # Only `track_name` is ever a per-track hole -- `release_tracks`
            # falls a track's artist credit back to the release's, so an
            # artist or album hole is release-wide and takes every track with
            # it, which lands on the refusal below.
            track_payloads = []
            for built_payload in built:
                missing = [
                    field for field in MB_REQUIRED_FIELDS
                    if not built_payload.get(field)
                ]
                if missing:
                    skipped.append({
                        "recording_mbid": built_payload.get("recording_mbid"),
                        "track_name": built_payload.get("track_name") or "",
                        "track_no": built_payload.get("track_no"),
                        "disc_no": built_payload.get("disc_no"),
                        "missing": missing,
                    })
                    continue
                track_payloads.append(built_payload)

            if not track_payloads:
                # Not a 400: the request is well formed and names a real
                # release. Not a 502 either: MusicBrainz answered. `{"job_ids":
                # []}` was the third option and the worst one -- a request that
                # queued nothing, said nothing, and looked like success.
                holes = sorted({
                    field for row in skipped for field in row["missing"]
                })
                raise HTTPException(
                    422,
                    "MusicBrainz has this release but nothing that can be"
                    " downloaded from it: "
                    + (
                        "it lists no tracks."
                        if not built
                        else f"every track is missing {', '.join(holes)}."
                    ),
                )
        elif kind == "url":
            track_payloads = [{
                "url": payload["url"], "artist_name": payload["artist_name"],
                "track_name": payload["track_name"],
                "album_name": payload.get("album_name", "Singles"),
            }]
        else:
            raise HTTPException(400, f"Unknown job kind: {kind}")

        if kind in ("mb", "mb_release"):
            # Keyed read, never a search (Decision 1). The `mb` allowlist is
            # why a client-sent `track_id` never survives to here.
            for track_payload in track_payloads:
                isrc = track_payload.get("isrc")
                track_id = catalog.track_id_for_isrc(isrc) if isrc else None
                if track_id is not None:
                    track_payload["track_id"] = track_id

        job_ids = []
        for track_payload in track_payloads:
            job_id = job_rows.create(conn, DOWNLOAD, track_payload)
            job_ids.append(job_id)
            app.state.download_queue.put_nowait(job_id)
        return {"job_ids": job_ids, "skipped": skipped}

    @app.post("/api/jobs/{job_id}/retry")
    async def retry_job(job_id: int) -> dict:
        """Queue a failed download again (story 85).

        This creates a *new* job carrying the old one's payload rather than
        resetting the old one. `job_rows.update` refuses to move a terminal
        state, on purpose: a job row is the record of one attempt, and
        rewriting a failure into a success would destroy the only evidence of
        what went wrong. Two rows is the honest shape -- the failure stays
        readable and the retry is its own attempt.
        """
        conn = app.state.conn
        job = job_rows.get(conn, job_id)
        if job is None:
            raise HTTPException(404, f"No job {job_id}")
        if job["kind"] != DOWNLOAD:
            raise HTTPException(400, f"Cannot retry a {job['kind']} job")
        if job["state"] != job_rows.ERROR:
            raise HTTPException(
                409, f"Job {job_id} is {job['state']}, not failed"
            )

        retry_id = job_rows.create(conn, DOWNLOAD, job["payload"])
        app.state.download_queue.put_nowait(retry_id)
        return {"job_ids": [retry_id], "retry_of": job_id}

    # Registered before /api/jobs/{job_id}, which would otherwise try to read
    # "stream" as an integer and 422.
    @app.get("/api/jobs/stream")
    async def jobs_stream(request: Request) -> StreamingResponse:
        """Live job progress, one long-lived connection.

        A new subscriber is handed the latest event immediately, so a page
        opened halfway through the backfill shows the bar where it actually is
        rather than waiting up to a second for the next album.
        """
        async def events():
            queue = bus.subscribe()
            try:
                yield ": connected\n\n"
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=20)
                    except TimeoutError:
                        # A comment line, purely to keep phones and any proxy
                        # in between from deciding an idle stream is a dead one.
                        yield ": keepalive\n\n"
                        continue
                    yield f"data: {json.dumps(event)}\n\n"
            finally:
                bus.unsubscribe(queue)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "cache-control": "no-store",
                "connection": "keep-alive",
                "x-accel-buffering": "no",
            },
        )

    @app.get("/api/jobs/{job_id}")
    def one_job(job_id: int) -> dict:
        job = job_rows.get(app.state.conn, job_id)
        if job is None:
            raise HTTPException(404, f"No job {job_id}")
        return job

    @app.get("/api/tracks/{track_rowid}/stream")
    @app.head("/api/tracks/{track_rowid}/stream")
    def stream(track_rowid: int, request: Request) -> Response:
        found = queries.track_paths(app.state.conn, track_rowid)
        if found is None:
            raise HTTPException(404, f"No track {track_rowid}")
        if not found["file_path"]:
            raise HTTPException(404, "That track was never downloaded")

        path = Path(found["file_path"])
        if not path.exists():
            # The import marks these `missing_file`, but a file can vanish
            # between startup and a tap.
            raise HTTPException(404, "The audio file is gone")

        size = path.stat().st_size
        headers = {
            "accept-ranges": "bytes",
            # Safari will re-request ranges of the same track constantly; a
            # stable validator keeps it from refetching what it already has.
            "etag": f'"{track_rowid}-{size}-{int(path.stat().st_mtime)}"',
            "cache-control": "private, max-age=86400",
        }

        try:
            byte_range = parse_range(request.headers.get("range"), size)
        except Unsatisfiable:
            return Response(
                status_code=416,
                headers={**headers, "content-range": f"bytes */{size}"},
            )

        if byte_range is None:
            return StreamingResponse(
                read_range(path, ByteRange(0, size - 1)),
                media_type=AUDIO_MEDIA_TYPE,
                headers={**headers, "content-length": str(size)},
            )

        return StreamingResponse(
            read_range(path, byte_range),
            status_code=206,
            media_type=AUDIO_MEDIA_TYPE,
            headers={
                **headers,
                "content-range": byte_range.content_range(size),
                "content-length": str(byte_range.length),
            },
        )

    # Registered last, so nothing here can shadow /api.
    index = config.web_dist / "index.html"
    if index.exists():
        app.mount(
            "/assets",
            StaticFiles(directory=config.web_dist / "assets"),
            name="assets",
        )

        # The SPA owns its own routes. Any non-API path returns the shell and
        # lets the router decide, so a deep link pasted into the phone works
        # and a reload on /artists does not 404.
        @app.get("/{_path:path}", response_class=HTMLResponse)
        def spa(_path: str) -> str:
            # /api is not the SPA's to answer. A request that reaches here
            # under it matched no route above, and handing back the shell
            # turns "this endpoint does not exist" into `200 text/html`,
            # which the client reads as `Unexpected token '<'` on whatever
            # screen happened to ask. That is what a server left running
            # across a deploy looks like from the browser, and it names
            # neither the path nor the fact that it is missing.
            if _path == "api" or _path.startswith("api/"):
                raise HTTPException(404, f"No route /{_path}")
            return index.read_text(encoding="utf-8")
    else:
        @app.get("/", response_class=HTMLResponse)
        def unbuilt() -> str:
            return UNBUILT_SPA

    return app
