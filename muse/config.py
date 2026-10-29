"""Where everything lives.

Defaults are the layout this project already has, so the server starts with no
arguments. Every path is overridable by environment variable, which is what
lets the systemd unit in ticket 10 point at the same files without hardcoding a
home directory into the code.
"""

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _path(env: str, default: str) -> Path:
    value = os.environ.get(env)
    return Path(value) if value else PROJECT_ROOT / default


@dataclass(frozen=True, slots=True)
class Config:
    # What the manifest's relative paths are relative to. Never the process's
    # working directory: a server started from a subdirectory would otherwise
    # find no audio at all and import the whole library as missing.
    root: Path
    library_db: Path
    manifest: Path
    catalog: Path
    # The second mirror: what every track sounds like. Optional in the same way
    # the catalog is — without it the dashboard's mood surfaces are empty and
    # everything else works.
    audio_features: Path
    # The date the catalog scrape stopped, measured from `fetched_at` across
    # tracks/albums/artists. Anything released after it has no row in the
    # mirror at all, so it is the boundary the MusicBrainz discovery path
    # exists on either side of. A date this load-bearing does not belong
    # inline in a query.
    catalog_freeze: str
    music_root: Path
    art_root: Path
    web_dist: Path
    # Where nightly snapshots land, and where they are copied to. Outside the
    # project on purpose: a backup inside the thing being backed up is not one.
    backup_dir: Path
    backup_target: str | None
    port: int
    host: str | None  # None means "resolve the tailnet address"
    # Whether to reach MusicBrainz for missing covers at startup. On by
    # default: it is a one-time ninety seconds and it is the only way art ever
    # appears. Off is for tests and for working without a network.
    art_backfill: bool
    # Whether to run the embed batch at startup. Off by default, unlike
    # art_backfill: it is a projected ~14 minutes over the whole library and
    # its real encoders need torch, transformers, and sentence-transformers,
    # none of which are core dependencies. Turning it on assumes those are
    # installed.
    embed_batch: bool
    # Whether this machine is expected to keep the content-embed leg
    # populated. This does not switch the batch on or off -- unlike
    # embed_batch, nothing in the app runs the content batch at startup or
    # anywhere else; `python -m muse.content_embed_batch` runs on its own
    # systemd timer regardless of this flag. It exists purely so the deploy
    # units and the health surface can say whether the leg *should* be
    # populated here. Off by default for a stronger version of the reason
    # embed_batch defaults off: MERT over the whole library is measured at
    # roughly 42-54 seconds per track on CPU, i.e. ~8-11 hours for the
    # ~723-track library, against CLAP's ~14 minutes, and it needs a second
    # virtualenv (`uv sync --project universal_reco`) that lives outside
    # this project's own dependencies.
    content_embed: bool
    # System One endpoints. Laya is the prompt parse's backend (`laya_*`).
    # `curator_*` still names the Jev settings the lyric tag batch uses via
    # `muse/systemone.py` -- the curator is retired (ADR 0009), the names stayed.
    laya_url: str = "http://127.0.0.1:8000"
    laya_model: str = "multilingual"
    laya_timeout_ms: int = 10000
    curator_jev_url: str = "https://api.typesafe.ai"
    curator_model: str | None = None
    curator_timeout_ms: int = 8000
    # Seconds between consecutive downloads (scaled up to +50% at random),
    # so a bulk queue does not hit YouTube back to back. See
    # `jobs.run_download_worker`. 0 here so a hand-built Config (every test)
    # runs its queue at once; the running app gets 12 from `from_env`.
    download_gap_seconds: float = 0.0
    chart_base_url: str = "https://rss.marketingtools.apple.com"
    chart_storefront: str = "us"
    # The API key is deliberately absent from Config: it is read from
    # JEV_API_KEY or TYPESAFE_API_KEY at request time by muse/systemone.py,
    # never stored on an object that /api/health could serialise.

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            root=Path(os.environ.get("MUSE_ROOT") or PROJECT_ROOT),
            library_db=_path("MUSE_LIBRARY_DB", "library.sqlite3"),
            manifest=_path("MUSE_MANIFEST", "download_manifest.json"),
            catalog=_path("MUSE_CATALOG", "spotify_clean.sqlite3"),
            audio_features=_path(
                "MUSE_AUDIO_FEATURES", "spotify_clean_audio_features.sqlite3"
            ),
            music_root=_path("MUSE_MUSIC_ROOT", "MUSIC"),
            art_root=_path("MUSE_ART_ROOT", "assets/art"),
            web_dist=_path("MUSE_WEB_DIST", "web/dist"),
            backup_dir=Path(
                os.environ.get("MUSE_BACKUP_DIR")
                or Path.home() / "muse-backups"
            ),
            backup_target=os.environ.get("MUSE_BACKUP_TARGET") or None,
            port=int(os.environ.get("MUSE_PORT", "8080")),
            host=os.environ.get("MUSE_HOST") or None,
            art_backfill=os.environ.get("MUSE_ART_BACKFILL", "1") != "0",
            embed_batch=os.environ.get("MUSE_EMBED_BATCH", "0") != "0",
            content_embed=os.environ.get("MUSE_CONTENT_EMBED", "0") != "0",
            catalog_freeze=os.environ.get("MUSE_CATALOG_FREEZE", "2025-11-13"),
            curator_jev_url=os.environ.get(
                "MUSE_CURATOR_JEV_URL", "https://api.typesafe.ai"
            ),
            laya_url=os.environ.get("MUSE_LAYA_URL", "http://127.0.0.1:8000"),
            laya_model=os.environ.get("MUSE_LAYA_MODEL", "multilingual"),
            laya_timeout_ms=int(os.environ.get("MUSE_LAYA_TIMEOUT_MS", "10000")),
            curator_model=os.environ.get("MUSE_CURATOR_MODEL") or None,
            curator_timeout_ms=int(os.environ.get("MUSE_CURATOR_TIMEOUT_MS", "8000")),
            download_gap_seconds=float(os.environ.get("MUSE_DOWNLOAD_GAP_S", "12")),
            chart_base_url=os.environ.get(
                "MUSE_CHART_URL", "https://rss.marketingtools.apple.com"),
            chart_storefront=os.environ.get("MUSE_CHART_STOREFRONT", "us"),
        )
