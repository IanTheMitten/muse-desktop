"""settings.json in the data dir: the folder list and the downloader switch."""

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULTS = {"music_folders": [], "music_files": [], "downloader_unlocked": False}


def load(path: Path) -> dict:
    out = {k: list(v) if isinstance(v, list) else v for k, v in DEFAULTS.items()}
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return out
    except (OSError, ValueError):
        log.warning("unreadable settings file %s; using defaults", path)
        return out
    if not isinstance(data, dict):
        log.warning("settings file %s is not an object; using defaults", path)
        return out
    out.update(data)
    return out


def save(path: Path, settings: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, indent=2))
    os.replace(tmp, path)


def music_folders(path: Path) -> list[Path]:
    return [Path(p) for p in load(path)["music_folders"]]


def music_files(path: Path) -> list[Path]:
    return [Path(p) for p in load(path)["music_files"]]
