from muse.config import Config


def test_catalog_freeze_defaults_to_the_measured_cutoff(monkeypatch):
    monkeypatch.delenv("MUSE_CATALOG_FREEZE", raising=False)
    assert Config.from_env().catalog_freeze == "2025-11-13"


def test_catalog_freeze_is_overridable(monkeypatch):
    monkeypatch.setenv("MUSE_CATALOG_FREEZE", "2026-01-01")
    assert Config.from_env().catalog_freeze == "2026-01-01"


def test_paths_default_under_the_data_dir(tmp_path, monkeypatch):
    for name in ("MUSE_LIBRARY_DB", "MUSE_MUSIC_ROOT", "MUSE_ART_ROOT",
                 "MUSE_LYRICS_ROOT", "MUSE_SETTINGS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MUSE_DATA_DIR", str(tmp_path))

    config = Config.from_env()

    assert config.library_db == tmp_path / "library.sqlite3"
    assert config.lyrics_root == tmp_path / "lyrics"


def test_port_defaults_to_zero_and_token_to_none(monkeypatch):
    monkeypatch.delenv("MUSE_PORT", raising=False)
    monkeypatch.delenv("MUSE_TOKEN", raising=False)

    config = Config.from_env()

    assert config.port == 0
    assert config.token is None


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
