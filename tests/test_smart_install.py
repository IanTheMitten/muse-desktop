import hashlib
import io
import json
import os
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import muse.smart as smart
from muse.smart import (
    HashMismatch,
    ManifestFile,
    NotEnoughSpace,
    Unsupported,
    download,
    files_for,
    host_triple,
    install,
    installed,
    load_manifest,
    status,
)

TRIPLE = "aarch64-apple-darwin"
BIG = 1_000_000_000_000


class FakeResponse:
    def __init__(self, status_code, body: bytes):
        self.status_code = status_code
        self.body = body

    def iter_content(self, chunk_size=1024):
        for i in range(0, len(self.body), chunk_size):
            yield self.body[i : i + chunk_size]

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, files: dict, honor_range=True):
        self.files = files
        self.honor_range = honor_range
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, headers=None, stream=True, timeout=60):
        headers = dict(headers or {})
        self.calls.append((url, headers))
        body = self.files[url]
        rng = headers.get("Range")
        if rng and self.honor_range:
            start = int(re.match(r"bytes=(\d+)-", rng).group(1))
            return FakeResponse(206, body[start:])
        return FakeResponse(200, body)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _entry(path, url, body):
    return {"path": path, "url": url, "sha256": _sha(body), "size": len(body)}


def _worker_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = zipfile.ZipInfo("muse-worker/muse-worker")
        info.external_attr = 0o755 << 16
        z.writestr(info, b"#!/bin/sh\n")
        z.writestr("muse-worker/_internal/lib.txt", b"lib")
    return buf.getvalue()


def _build():
    bodies = {
        "https://x/a.bin": b"a" * 3000,
        "https://x/b.bin": b"b" * 2000,
        "https://x/worker.zip": _worker_zip(),
    }
    manifest = {
        "version": 1,
        "weights": [
            _entry("weights/mert/a.bin", "https://x/a.bin", bodies["https://x/a.bin"]),
            _entry("weights/clap/b.bin", "https://x/b.bin", bodies["https://x/b.bin"]),
        ],
        "workers": {
            TRIPLE: _entry(
                f"muse-worker-{TRIPLE}.zip",
                "https://x/worker.zip",
                bodies["https://x/worker.zip"],
            )
        },
    }
    return manifest, bodies


def _mf(entry) -> ManifestFile:
    return ManifestFile(**entry)


def test_fresh_download_writes_file_no_part(tmp_path):
    manifest, bodies = _build()
    f = _mf(manifest["weights"][0])
    out = download(f, tmp_path, session=FakeSession(bodies))
    assert out.read_bytes() == bodies[f.url]
    assert out == tmp_path / f.path
    assert not Path(str(out) + ".part").exists()


def test_resume_sends_range_and_completes(tmp_path):
    manifest, bodies = _build()
    f = _mf(manifest["weights"][0])
    half = f.size // 2
    part = tmp_path / (f.path + ".part")
    part.parent.mkdir(parents=True)
    part.write_bytes(bodies[f.url][:half])
    session = FakeSession(bodies)
    out = download(f, tmp_path, session=session)
    assert session.calls[0][1]["Range"] == f"bytes={half}-"
    assert out.read_bytes() == bodies[f.url]


def test_server_ignoring_range_still_correct(tmp_path):
    manifest, bodies = _build()
    f = _mf(manifest["weights"][0])
    part = tmp_path / (f.path + ".part")
    part.parent.mkdir(parents=True)
    part.write_bytes(bodies[f.url][:100])
    out = download(f, tmp_path, session=FakeSession(bodies, honor_range=False))
    assert out.read_bytes() == bodies[f.url]


def test_wrong_sha256_raises_and_cleans_up(tmp_path):
    manifest, bodies = _build()
    bad = _mf({**manifest["weights"][0], "sha256": "0" * 64})
    with pytest.raises(HashMismatch):
        download(bad, tmp_path, session=FakeSession(bodies))
    assert not (tmp_path / bad.path).exists()
    assert not (tmp_path / (bad.path + ".part")).exists()


def test_existing_final_file_makes_no_request(tmp_path):
    manifest, bodies = _build()
    f = _mf(manifest["weights"][0])
    final = tmp_path / f.path
    final.parent.mkdir(parents=True)
    final.write_bytes(bodies[f.url])
    session = FakeSession(bodies)
    download(f, tmp_path, session=session)
    assert session.calls == []


def test_install_not_enough_space(tmp_path):
    manifest, bodies = _build()
    session = FakeSession(bodies)
    with pytest.raises(NotEnoughSpace, match=r"Not enough disk space: needs \d+\.\d GB"):
        install(
            tmp_path,
            manifest,
            TRIPLE,
            session=session,
            disk_free=lambda p: SimpleNamespace(free=10),
        )
    assert session.calls == []


def test_full_install(tmp_path):
    manifest, bodies = _build()
    progress = []
    install(
        tmp_path,
        manifest,
        TRIPLE,
        session=FakeSession(bodies),
        on_progress=lambda done, total: progress.append((done, total)),
        disk_free=lambda p: SimpleNamespace(free=BIG),
    )
    assert (tmp_path / "weights/mert/a.bin").read_bytes() == bodies["https://x/a.bin"]
    assert (tmp_path / "weights/clap/b.bin").read_bytes() == bodies["https://x/b.bin"]
    exe = tmp_path / "worker/muse-worker/muse-worker"
    assert exe.is_file()
    if os.name != "nt":
        assert os.access(exe, os.X_OK)
    assert (tmp_path / "worker/muse-worker/_internal/lib.txt").read_bytes() == b"lib"
    assert not (tmp_path / f"muse-worker-{TRIPLE}.zip").exists()
    assert json.loads((tmp_path / "installed.json").read_text()) == {
        "manifest_version": 1,
        "triple": TRIPLE,
    }
    assert installed(tmp_path, manifest) is True
    assert installed(tmp_path, {**manifest, "version": 2}) is False
    assert progress and progress[-1][0] == progress[-1][1]


def test_files_for_unsupported_triple():
    manifest, _ = _build()
    with pytest.raises(Unsupported):
        files_for(manifest, "x86_64-apple-darwin")


def test_status_unsupported_and_download_bytes(tmp_path):
    manifest, _ = _build()
    assert status(tmp_path, manifest, None)["state"] == "unsupported"
    st = status(tmp_path, manifest, TRIPLE)
    assert st["state"] == "not_installed"
    total = sum(e["size"] for e in manifest["weights"]) + manifest["workers"][TRIPLE]["size"]
    assert st["download_bytes"] == total


def test_host_triple_known_or_none():
    assert host_triple() in {
        "aarch64-apple-darwin",
        "x86_64-apple-darwin",
        "x86_64-pc-windows-msvc",
        "x86_64-unknown-linux-gnu",
        None,
    }


def test_shipped_manifest_shape():
    m = load_manifest()
    assert "version" in m
    assert isinstance(m["workers"], dict)
    assert m["weights"]
    for w in m["weights"]:
        assert w["path"].startswith("weights/")
        assert re.fullmatch(r"[0-9a-f]{64}", w["sha256"])
        assert isinstance(w["size"], int)


def test_signature_constants():
    assert smart.SMART_INSTALL == "smart_install"
    assert smart.MANIFEST_PATH.name == "smart_manifest.json"
