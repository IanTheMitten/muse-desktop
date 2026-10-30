"""Where everything lives.

Every path lives under one data dir (MUSE_DATA_DIR) and is env-overridable.
The server binds loopback on one port and may require a token.
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
    data_dir: Path
    library_db: Path
    music_root: Path
    art_root: Path
    lyrics_root: Path
    settings_path: Path
    web_dist: Path
    models_dir: Path
    port: int = 0
    host: str = "127.0.0.1"
    token: str | None = None
    # Whether to reach MusicBrainz for missing covers at startup. On by
    # default: it is a one-time ninety seconds and it is the only way art ever
    # appears. Off is for tests and for working without a network.
    art_backfill: bool = True
    lyrics_sync: bool = True
    # Run the ML steps without an installed worker (dev, with MUSE_WORKER_CMD).
    embed_batch: bool = False
    worker_cmd: str | None = None
    taste_retrain_at_launch: bool = True
    # The date the catalog scrape stopped; anything released after it has no
    # row in the mirror.
    catalog_freeze: str = "2025-11-13"
    # Seconds between consecutive downloads (scaled up to +50% at random),
    # so a bulk queue does not hit YouTube back to back. See
    # `jobs.run_download_worker`. 0 here so a hand-built Config (every test)
    # runs its queue at once; the running app gets 12 from `from_env`.
    download_gap_seconds: float = 0.0
    # Where the yt-dlp binary is fetched from. Empty means never fetch, which
    # is what every hand-built Config (every test) gets; `from_env` sets the
    # real release URL.
    ytdlp_release_url: str = ""
    chart_base_url: str = "https://rss.marketingtools.apple.com"
    chart_storefront: str = "us"
    # The API key is deliberately absent from Config: it is read from
    # JEV_API_KEY or TYPESAFE_API_KEY at request time by muse/systemone.py,
    # never stored on an object that /api/health could serialise.

    @classmethod
    def from_env(cls) -> "Config":
        data_dir = Path(os.environ.get("MUSE_DATA_DIR") or PROJECT_ROOT / "data")

        def store(env: str, name: str) -> Path:
            value = os.environ.get(env)
            return Path(value) if value else data_dir / name

        return cls(
            data_dir=data_dir,
            library_db=store("MUSE_LIBRARY_DB", "library.sqlite3"),
            music_root=store("MUSE_MUSIC_ROOT", "downloads"),
            art_root=store("MUSE_ART_ROOT", "art"),
            lyrics_root=store("MUSE_LYRICS_ROOT", "lyrics"),
            settings_path=store("MUSE_SETTINGS", "settings.json"),
            web_dist=_path("MUSE_WEB_DIST", "web/dist"),
            port=int(os.environ.get("MUSE_PORT", "0")),
            token=os.environ.get("MUSE_TOKEN") or None,
            art_backfill=os.environ.get("MUSE_ART_BACKFILL", "1") != "0",
            lyrics_sync=os.environ.get("MUSE_LYRICS_SYNC", "1") != "0",
            embed_batch=os.environ.get("MUSE_EMBED_BATCH", "0") != "0",
            models_dir=store("MUSE_MODELS_DIR", "models"),
            worker_cmd=os.environ.get("MUSE_WORKER_CMD") or None,
            taste_retrain_at_launch=os.environ.get("MUSE_TASTE_RETRAIN", "1") != "0",
            catalog_freeze=os.environ.get("MUSE_CATALOG_FREEZE", "2025-11-13"),
            download_gap_seconds=float(os.environ.get("MUSE_DOWNLOAD_GAP_S", "12")),
            ytdlp_release_url=os.environ.get(
                "MUSE_YTDLP_URL",
                "https://github.com/yt-dlp/yt-dlp/releases/latest/download"),
            chart_base_url=os.environ.get(
                "MUSE_CHART_URL", "https://rss.marketingtools.apple.com"),
            chart_storefront=os.environ.get("MUSE_CHART_STOREFRONT", "us"),
        )
