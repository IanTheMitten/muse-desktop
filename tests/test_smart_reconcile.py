import json

import pytest
import requests

import muse.smart as smart
from muse.smart import installed, reconcile

TRIPLE = "aarch64-apple-darwin"


def _entry(path):
    return {"path": path, "url": "https://x/" + path, "sha256": "0" * 64, "size": 1}


def _manifest(version, weights):
    return {
        "version": version,
        "weights": [_entry(w) for w in weights],
        "workers": {TRIPLE: _entry(f"muse-worker-{TRIPLE}.zip")},
    }


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(requests, "Session", boom)
    monkeypatch.setattr(smart, "download", boom)


def _disk(models, weights, worker=True):
    for w in weights:
        (models / w).parent.mkdir(parents=True, exist_ok=True)
        (models / w).write_bytes(b"x")
    if worker:
        exe = models / "worker" / "muse-worker" / ("muse-worker.exe" if smart.os.name == "nt" else "muse-worker")
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"x")


def _record(models, version):
    models.mkdir(parents=True, exist_ok=True)
    (models / "installed.json").write_text(json.dumps({"manifest_version": version, "triple": TRIPLE}))


def _version(models):
    return json.loads((models / "installed.json").read_text())["manifest_version"]


def test_upgrade_all_present(tmp_path):
    new = _manifest(3, ["weights/mert/a.bin", "weights/clap/b.bin"])
    _disk(tmp_path, ["weights/mert/a.bin", "weights/clap/b.bin", "weights/minilm/m.bin"])
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "keep.txt").write_text("k")
    _record(tmp_path, 2)
    assert reconcile(tmp_path, new, TRIPLE) is True
    assert _version(tmp_path) == 3
    assert installed(tmp_path, new)
    assert not (tmp_path / "weights" / "minilm").exists()
    assert (tmp_path / "weights" / "mert" / "a.bin").exists()
    assert (tmp_path / "weights" / "clap" / "b.bin").exists()
    assert (tmp_path / "worker" / "muse-worker").exists()
    assert (tmp_path / "other" / "keep.txt").exists()


def test_missing_file_changes_nothing(tmp_path):
    new = _manifest(3, ["weights/mert/a.bin", "weights/clap/b.bin"])
    _disk(tmp_path, ["weights/mert/a.bin", "weights/minilm/m.bin"])
    _record(tmp_path, 2)
    assert reconcile(tmp_path, new, TRIPLE) is False
    assert _version(tmp_path) == 2
    assert (tmp_path / "weights" / "minilm" / "m.bin").exists()


def test_missing_worker_changes_nothing(tmp_path):
    new = _manifest(3, ["weights/mert/a.bin"])
    _disk(tmp_path, ["weights/mert/a.bin", "weights/minilm/m.bin"], worker=False)
    _record(tmp_path, 2)
    assert reconcile(tmp_path, new, TRIPLE) is False
    assert _version(tmp_path) == 2
    assert (tmp_path / "weights" / "minilm" / "m.bin").exists()


def test_already_current(tmp_path):
    new = _manifest(3, ["weights/mert/a.bin"])
    _disk(tmp_path, ["weights/mert/a.bin", "weights/minilm/m.bin"])
    _record(tmp_path, 3)
    assert reconcile(tmp_path, new, TRIPLE) is False
    assert (tmp_path / "weights" / "minilm" / "m.bin").exists()


def test_no_installed_json(tmp_path):
    new = _manifest(3, ["weights/mert/a.bin"])
    _disk(tmp_path, ["weights/mert/a.bin"])
    assert reconcile(tmp_path, new, TRIPLE) is False
    assert not (tmp_path / "installed.json").exists()


def test_unsupported_triple(tmp_path):
    new = _manifest(3, ["weights/mert/a.bin"])
    _disk(tmp_path, ["weights/mert/a.bin"])
    _record(tmp_path, 2)
    assert reconcile(tmp_path, new, "riscv-unknown") is False
    assert reconcile(tmp_path, new, None) is False
    assert _version(tmp_path) == 2
