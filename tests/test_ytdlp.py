import hashlib
import subprocess
import sys

import pytest
import requests

from muse.remux import NO_M4A
from muse.ytdlp import (
    INSTALL_FAILED,
    MISSING,
    YTDLP_INSTALL,
    asset_name,
    binary_path,
    download,
    install,
    probe,
    update,
)

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX script")

BASE = "http://dl.test/yt"
BODY = b"#!fake"


class FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, sha: str):
        self.sha = sha
        self.urls = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        if url == f"{BASE}/SHA2-256SUMS":
            text = f"{self.sha}  yt-dlp_linux\n{'0' * 64}  yt-dlp.exe\n"
            return FakeResponse(text.encode())
        if url == f"{BASE}/yt-dlp_linux":
            return FakeResponse(BODY)
        raise AssertionError(url)


class RaisingSession:
    def get(self, url, timeout=None):
        raise requests.ConnectionError("down")


def test_constant():
    assert YTDLP_INSTALL == "ytdlp_install"


def test_asset_name():
    assert asset_name("darwin") == "yt-dlp_macos"
    assert asset_name("win32") == "yt-dlp.exe"
    assert asset_name("linux") == "yt-dlp_linux"


def test_binary_path(tmp_path):
    assert binary_path(tmp_path, "win32") == tmp_path / "bin" / "yt-dlp.exe"
    assert binary_path(tmp_path, "linux") == tmp_path / "bin" / "yt-dlp"


def test_install_matching_hash(tmp_path):
    session = FakeSession(hashlib.sha256(BODY).hexdigest())
    path = install(tmp_path, BASE, session=session, platform="linux")
    assert path == tmp_path / "bin" / "yt-dlp"
    assert path.read_bytes() == BODY
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o777 == 0o755
    assert list((tmp_path / "bin").glob("*.part")) == []
    assert session.urls == [f"{BASE}/SHA2-256SUMS", f"{BASE}/yt-dlp_linux"]


def test_install_mismatched_hash(tmp_path):
    session = FakeSession("f" * 64)
    with pytest.raises(RuntimeError) as err:
        install(tmp_path, BASE, session=session, platform="linux")
    assert str(err.value) == INSTALL_FAILED
    bin_dir = tmp_path / "bin"
    assert not bin_dir.exists() or list(bin_dir.iterdir()) == []


def test_install_empty_base_url(tmp_path):
    session = FakeSession("f" * 64)
    with pytest.raises(RuntimeError) as err:
        install(tmp_path, "", session=session, platform="linux")
    assert str(err.value) == INSTALL_FAILED
    assert session.urls == []


def test_install_connection_error(tmp_path):
    with pytest.raises(RuntimeError) as err:
        install(tmp_path, BASE, session=RaisingSession(), platform="linux")
    assert str(err.value) == INSTALL_FAILED


SCRIPT = f"""#!{sys.executable}
import json, sys
a = sys.argv[1:]
if "-U" in a:
    sys.exit(1)
if "--no-simulate" in a:
    t = a[a.index("-o") + 1]
    open(t.replace("%(ext)s", "m4a"), "wb").write(b"x")
    print(json.dumps({{"_type": "playlist", "entries": [{{"id": "abc", "duration": 200.0}}]}}))
else:
    print(json.dumps({{"_type": "playlist", "entries": [{{"id": "abc", "title": "T", "thumbnail": "http://x/t.jpg"}}]}}))
"""


@pytest.fixture
def fake(tmp_path):
    path = tmp_path / "bin" / "yt-dlp"
    path.parent.mkdir()
    path.write_text(SCRIPT)
    path.chmod(0o755)
    return path


@posix_only
def test_update_false(fake, tmp_path):
    assert update(fake) is False
    assert update(tmp_path / "missing") is False


@posix_only
def test_probe(fake):
    assert probe(fake, "ytsearch1:a - b") == {
        "id": "abc",
        "title": "T",
        "thumbnail": "http://x/t.jpg",
    }


@posix_only
def test_probe_missing(tmp_path):
    assert probe(tmp_path / "missing", "q") is None


@posix_only
def test_download(fake, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    assert download(fake, "q", out, "7") == {"id": "abc", "duration": 200.0}
    assert (out / "7.m4a").exists()


@posix_only
def test_download_missing(tmp_path):
    with pytest.raises(RuntimeError) as err:
        download(tmp_path / "missing", "q", tmp_path, "7")
    assert str(err.value) == MISSING


def _run_returning(stderr, calls=None):
    def run(args, **kwargs):
        if calls is not None:
            calls.append(args)
        return subprocess.CompletedProcess(args, 1, "", stderr)

    return run


def test_download_no_m4a(tmp_path):
    binary = tmp_path / "yt-dlp"
    binary.touch()
    run = _run_returning(
        "ERROR: [youtube] x: Requested format is not available. Use --list-formats"
    )
    with pytest.raises(RuntimeError) as err:
        download(binary, "q", tmp_path, "7", run=run)
    assert str(err.value) == NO_M4A


def test_download_error_text(tmp_path):
    binary = tmp_path / "yt-dlp"
    binary.touch()
    with pytest.raises(RuntimeError) as err:
        download(binary, "q", tmp_path, "7", run=_run_returning("ERROR: boom\n"))
    assert str(err.value) == "ERROR: boom"


def test_download_args(tmp_path):
    binary = tmp_path / "yt-dlp"
    binary.touch()
    calls = []
    with pytest.raises(RuntimeError):
        download(binary, "q", tmp_path, "7", run=_run_returning("ERROR: boom\n", calls))
    args = calls[0]
    assert args[:4] == [str(binary), "--ignore-config", "--no-warnings", "--no-playlist"]
    i = args.index("-f")
    assert args[i + 1] == "bestaudio[ext=m4a]"
    for flag in ("--force-overwrites", "-J", "--no-simulate"):
        assert flag in args
    assert args[-1] == "q"
