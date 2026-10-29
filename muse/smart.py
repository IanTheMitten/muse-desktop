"""Smart-features installer: hash-pinned manifest, resumable downloads into models/."""

import hashlib
import json
import os
import platform
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests

MANIFEST_PATH = Path(__file__).with_name("smart_manifest.json")
SMART_INSTALL = "smart_install"
CHUNK = 1 << 20


class Unsupported(Exception):
    pass


class HashMismatch(Exception):
    pass


class NotEnoughSpace(Exception):
    pass


@dataclass(frozen=True)
class ManifestFile:
    path: str
    url: str
    sha256: str
    size: int


def host_triple() -> str | None:
    machine = platform.machine().lower()
    return {
        ("darwin", "arm64"): "aarch64-apple-darwin",
        ("darwin", "x86_64"): "x86_64-apple-darwin",
        ("win32", "amd64"): "x86_64-pc-windows-msvc",
        ("win32", "x86_64"): "x86_64-pc-windows-msvc",
        ("linux", "x86_64"): "x86_64-unknown-linux-gnu",
    }.get((sys.platform, machine))


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    return json.loads(path.read_text())


def files_for(manifest: dict, triple: str | None) -> list[ManifestFile]:
    worker = manifest["workers"].get(triple) if triple else None
    if worker is None:
        raise Unsupported(f"No worker for {triple}")
    return [ManifestFile(**e) for e in [*manifest["weights"], worker]]


def installed(models_dir: Path, manifest: dict | None = None) -> bool:
    manifest = manifest or load_manifest()
    try:
        done = json.loads((models_dir / "installed.json").read_text())
    except (OSError, ValueError):
        return False
    return isinstance(done, dict) and done.get("manifest_version") == manifest["version"]


def _missing(models_dir: Path, files: list[ManifestFile]) -> list[ManifestFile]:
    return [f for f in files if not (models_dir / f.path).exists()]


def status(models_dir: Path, manifest: dict, triple: str | None) -> dict:
    if installed(models_dir, manifest):
        return {"state": "installed", "download_bytes": 0}
    try:
        files = files_for(manifest, triple)
    except Unsupported:
        return {"state": "unsupported", "download_bytes": 0}
    return {
        "state": "not_installed",
        "download_bytes": sum(f.size for f in _missing(models_dir, files)),
    }


def download(file: ManifestFile, models_dir: Path, *, session=None, on_bytes=None) -> Path:
    final = models_dir / file.path
    if final.exists():
        return final
    part = Path(str(final) + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    have = part.stat().st_size if part.exists() else 0
    if have > file.size:
        part.unlink()
        have = 0
    digest = hashlib.sha256()
    if have:
        with open(part, "rb") as fh:
            for block in iter(lambda: fh.read(CHUNK), b""):
                digest.update(block)
    if have < file.size:
        headers = {"Range": f"bytes={have}-"} if have else {}
        with (session or requests.Session()).get(file.url, headers=headers, stream=True, timeout=60) as r:
            r.raise_for_status()
            if have and r.status_code != 206:  # server ignored Range: restart
                have, digest = 0, hashlib.sha256()
            with open(part, "ab" if have else "wb") as out:
                for chunk in r.iter_content(chunk_size=CHUNK):
                    out.write(chunk)
                    digest.update(chunk)
                    if on_bytes:
                        on_bytes(len(chunk))
    if digest.hexdigest() != file.sha256:
        part.unlink(missing_ok=True)
        raise HashMismatch(f"Downloaded file failed its check: {file.path}")
    os.replace(part, final)
    return final


def _extract(zip_path: Path, dest: Path) -> None:
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            target = z.extract(info, dest)
            mode = (info.external_attr >> 16) & 0o777
            if mode:
                os.chmod(target, mode)


def install(
    models_dir: Path,
    manifest: dict,
    triple: str,
    *,
    session=None,
    on_progress=None,
    disk_free=shutil.disk_usage,
) -> None:
    files = files_for(manifest, triple)
    worker = files[-1]
    models_dir.mkdir(parents=True, exist_ok=True)
    todo = _missing(models_dir, files)
    needed = sum(f.size for f in todo) + 3 * worker.size
    if disk_free(models_dir).free < needed:
        raise NotEnoughSpace(f"Not enough disk space: needs {needed / 1e9:.1f} GB")
    total = sum(f.size for f in files)
    done = total - sum(f.size for f in todo)
    if on_progress:
        on_progress(done, total)

    def bump(n: int) -> None:
        nonlocal done
        done += n
        if on_progress:
            on_progress(min(done, total), total)

    for f in todo:
        before = done
        download(f, models_dir, session=session, on_bytes=bump)
        done = before + f.size  # resumed bytes were never streamed through bump
        if on_progress:
            on_progress(done, total)
    zip_path = models_dir / worker.path
    if zip_path.exists():
        _extract(zip_path, models_dir / "worker")
        zip_path.unlink()
    tmp = models_dir / "installed.json.tmp"
    tmp.write_text(json.dumps({"manifest_version": manifest["version"], "triple": triple}))
    os.replace(tmp, models_dir / "installed.json")
