import asyncio

from muse.api import create_app
from muse.config import Config


def test_catalog_freeze_defaults_to_the_measured_cutoff(monkeypatch):
    monkeypatch.delenv("MUSE_CATALOG_FREEZE", raising=False)
    assert Config.from_env().catalog_freeze == "2025-11-13"


def test_catalog_freeze_is_overridable(monkeypatch):
    monkeypatch.setenv("MUSE_CATALOG_FREEZE", "2026-01-01")
    assert Config.from_env().catalog_freeze == "2026-01-01"


def test_the_freeze_date_reaches_the_client_through_health(tmp_path, monkeypatch):
    """The whole chain, because until now there wasn't one.

    `MUSE_CATALOG_FREEZE` was inert: nothing in `muse/` read
    `Config.catalog_freeze`, and the browse screen kept its own copy of the
    date in TypeScript. `/api/health` is where the server publishes it, and
    the client reads it from there and hands it straight back as the `since`
    that decides which of an artist's releases the mirror is missing -- so a
    server started against a differently frozen mirror moves the boundary on
    the screen instead of quietly disagreeing with it.
    """
    for name, value in {
        "MUSE_CATALOG_FREEZE": "2026-01-01",
        "MUSE_ROOT": str(tmp_path),
        "MUSE_LIBRARY_DB": str(tmp_path / "library.sqlite3"),
        "MUSE_MANIFEST": str(tmp_path / "manifest.json"),
        "MUSE_CATALOG": str(tmp_path / "catalog.sqlite3"),
        "MUSE_AUDIO_FEATURES": str(tmp_path / "features.sqlite3"),
        "MUSE_MUSIC_ROOT": str(tmp_path / "MUSIC"),
        "MUSE_ART_ROOT": str(tmp_path / "art"),
        "MUSE_WEB_DIST": str(tmp_path / "web"),
        "MUSE_BACKUP_DIR": str(tmp_path / "backups"),
        "MUSE_ART_BACKFILL": "0",
    }.items():
        monkeypatch.setenv(name, value)

    app = create_app(Config.from_env())

    async def drive():
        async with app.router.lifespan_context(app):
            [route] = [
                r for r in app.routes if getattr(r, "path", None) == "/api/health"
            ]
            return route.endpoint()

    assert asyncio.run(drive())["catalog_freeze"] == "2026-01-01"


def test_system_one_config_defaults(monkeypatch):
    for name in ("MUSE_CURATOR_JEV_URL", "MUSE_CURATOR_MODEL",
                 "MUSE_CURATOR_TIMEOUT_MS", "MUSE_LAYA_URL", "MUSE_LAYA_MODEL",
                 "MUSE_LAYA_TIMEOUT_MS"):
        monkeypatch.delenv(name, raising=False)

    config = Config.from_env()

    assert config.curator_jev_url == "https://api.typesafe.ai"
    assert config.laya_url == "http://127.0.0.1:8000"
    assert config.laya_model == "multilingual"
    assert config.laya_timeout_ms == 10000
    assert config.curator_model is None
    assert config.curator_timeout_ms == 8000


def test_system_one_config_is_overridable(monkeypatch):
    monkeypatch.setenv("MUSE_CURATOR_MODEL", "jev-1.13.0")
    monkeypatch.setenv("MUSE_LAYA_MODEL", "english")
    monkeypatch.setenv("MUSE_CURATOR_TIMEOUT_MS", "2000")

    config = Config.from_env()

    assert config.curator_model == "jev-1.13.0"
    assert config.laya_model == "english"
    assert config.curator_timeout_ms == 2000
