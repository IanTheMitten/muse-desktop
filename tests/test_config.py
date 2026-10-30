from muse.config import Config


def test_update_check_defaults_on_and_env_turns_it_off(monkeypatch):
    monkeypatch.delenv("MUSE_UPDATE_CHECK", raising=False)
    assert Config.from_env().update_check is True
    monkeypatch.setenv("MUSE_UPDATE_CHECK", "0")
    assert Config.from_env().update_check is False


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


def test_models_dir_and_worker_cmd(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MUSE_MODELS_DIR", raising=False)
    monkeypatch.delenv("MUSE_WORKER_CMD", raising=False)

    config = Config.from_env()

    assert config.models_dir == tmp_path / "models"
    assert config.worker_cmd is None

    monkeypatch.setenv("MUSE_MODELS_DIR", str(tmp_path / "m"))
    monkeypatch.setenv("MUSE_WORKER_CMD", "python -m muse.worker_main")

    config = Config.from_env()

    assert config.models_dir == tmp_path / "m"
    assert config.worker_cmd == "python -m muse.worker_main"
