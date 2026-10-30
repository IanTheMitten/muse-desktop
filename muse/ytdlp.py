"""The app-managed yt-dlp standalone binary (spec Decision 1): fetched after
unlock into `<data dir>/bin`, checked against the release's SHA2-256SUMS,
updated with -U, run only as a subprocess. Nothing imports `yt_dlp`."""

import hashlib
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import requests

from .remux import NO_M4A

log = logging.getLogger(__name__)

YTDLP_INSTALL = "ytdlp_install"
MISSING = "Downloader missing"
INSTALL_FAILED = "Couldn't get the downloader"
SUMS = "SHA2-256SUMS"
ASSETS = {"darwin": "yt-dlp_macos", "win32": "yt-dlp.exe", "linux": "yt-dlp_linux"}
BASE_FLAGS = ["--ignore-config", "--no-warnings", "--no-playlist"]


def asset_name(platform: str = sys.platform) -> str:
    try:
        return ASSETS[platform]
    except KeyError:
        raise RuntimeError(INSTALL_FAILED) from None


def binary_path(data_dir: Path, platform: str = sys.platform) -> Path:
    return data_dir / "bin" / ("yt-dlp.exe" if platform == "win32" else "yt-dlp")


def install(data_dir, base_url, *, session=None, platform=sys.platform) -> Path:
    if not base_url:
        raise RuntimeError(INSTALL_FAILED)
    asset = asset_name(platform)
    target = binary_path(Path(data_dir), platform)
    part = target.with_name(target.name + ".part")
    get = (session or requests).get
    try:
        sums = get(f"{base_url}/{SUMS}", timeout=30)
        sums.raise_for_status()
        body = get(f"{base_url}/{asset}", timeout=300)
        body.raise_for_status()
        expected = None
        for line in sums.content.decode().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == asset:
                expected = parts[0]
        if expected is None or hashlib.sha256(body.content).hexdigest() != expected:
            raise RuntimeError(INSTALL_FAILED)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            part.write_bytes(body.content)
            os.chmod(part, 0o755)
            os.replace(part, target)
        finally:
            part.unlink(missing_ok=True)
    except (requests.RequestException, OSError) as error:
        raise RuntimeError(INSTALL_FAILED) from error
    return target


def _run(run, args, timeout):
    return run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _pick(info: dict) -> dict:
    entries = info.get("entries")
    return entries[0] if entries else info


def update(binary, *, run=subprocess.run) -> bool:
    if not Path(binary).exists():
        return False
    try:
        done = _run(run, [str(binary), "-U"], 120)
    except (OSError, subprocess.SubprocessError):
        log.warning("yt-dlp update could not run", exc_info=True)
        return False
    if done.returncode != 0:
        log.warning("yt-dlp update failed: %s", (done.stderr or "").strip()[-300:])
    return done.returncode == 0


def probe(binary, query, *, run=subprocess.run) -> dict | None:
    if not Path(binary).exists():
        return None
    try:
        done = _run(run, [str(binary), *BASE_FLAGS, "-J", query], 60)
        if done.returncode != 0:
            return None
        return _pick(json.loads(done.stdout))
    except Exception:
        return None


def download(binary, query, out_dir, base_name, *, run=subprocess.run) -> dict:
    if not Path(binary).exists():
        raise RuntimeError(MISSING)
    args = [
        str(binary),
        *BASE_FLAGS,
        "--no-progress",
        "--force-overwrites",
        "-f",
        "bestaudio[ext=m4a]",
        "-o",
        str(Path(out_dir) / f"{base_name}.%(ext)s"),
        "-J",
        "--no-simulate",
        query,
    ]
    done = _run(run, args, 900)
    if done.returncode != 0:
        if "Requested format is not available" in done.stderr:
            raise RuntimeError(NO_M4A)
        lines = [line for line in done.stderr.splitlines() if line.strip()]
        raise RuntimeError(lines[-1] if lines else f"yt-dlp exited {done.returncode}")
    return _pick(json.loads(done.stdout))
