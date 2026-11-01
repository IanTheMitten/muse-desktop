"""scripts/updater_manifest.py: the latest.json feed the shell's updater reads."""

import datetime
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "updater_manifest", Path(__file__).resolve().parent.parent / "scripts" / "updater_manifest.py"
)
updater_manifest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(updater_manifest)

NOW = datetime.datetime(2026, 11, 1, 9, 30, tzinfo=datetime.timezone.utc)

FILES = {
    "macos/Muse_0.1.5_aarch64.app.tar.gz": "sig-arm",
    "macos/Muse_0.1.5_x64.app.tar.gz": "sig-intel",
    "nsis/Muse_0.1.5_x64-setup.exe": "sig-win",
}


def _dist(tmp_path, files=FILES):
    for name, sig in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"bundle")
        (tmp_path / (name + ".sig")).write_text(sig + "\n")
    return str(tmp_path)


def test_every_platform_points_at_its_release_asset(tmp_path):
    data = updater_manifest.manifest(_dist(tmp_path), "v0.1.5", "o/r", NOW)
    base = "https://github.com/o/r/releases/download/v0.1.5/"
    assert data["version"] == "0.1.5"
    assert data["pub_date"] == "2026-11-01T09:30:00Z"
    assert data["platforms"] == {
        "darwin-aarch64": {"signature": "sig-arm", "url": base + "Muse_0.1.5_aarch64.app.tar.gz"},
        "darwin-x86_64": {"signature": "sig-intel", "url": base + "Muse_0.1.5_x64.app.tar.gz"},
        "windows-x86_64": {"signature": "sig-win", "url": base + "Muse_0.1.5_x64-setup.exe"},
    }


def test_a_missing_platform_fails_instead_of_publishing_a_partial_feed(tmp_path):
    files = {k: v for k, v in FILES.items() if "x64.app" not in k}
    with pytest.raises(SystemExit, match="darwin-x86_64"):
        updater_manifest.manifest(_dist(tmp_path, files), "v0.1.5", "o/r", NOW)


def test_a_missing_signature_fails(tmp_path):
    dist = _dist(tmp_path)
    (tmp_path / "nsis" / "Muse_0.1.5_x64-setup.exe.sig").unlink()
    with pytest.raises(SystemExit, match="signature"):
        updater_manifest.manifest(dist, "v0.1.5", "o/r", NOW)
