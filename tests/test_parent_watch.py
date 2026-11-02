import os
import subprocess
import sys

import pytest


def _launch(tmp_path, watch: bool) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if k != "MUSE_PORT"}
    env["MUSE_DATA_DIR"] = str(tmp_path / "data")
    env["MUSE_TOKEN"] = "parent-watch-token"
    env.pop("MUSE_PARENT_WATCH", None)
    if watch:
        env["MUSE_PARENT_WATCH"] = "1"
    p = subprocess.Popen(
        [sys.executable, "-m", "muse"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
    )
    for _ in range(20):
        if p.stdout.readline().startswith("MUSE_LISTENING"):
            return p
    p.kill()
    pytest.fail("server never announced MUSE_LISTENING")


def test_exits_when_stdin_closes(tmp_path):
    p = _launch(tmp_path, watch=True)
    p.stdin.close()
    assert p.wait(timeout=20) == 0


def test_numpy_loads_while_watching_stdin():
    # A blocking read on the stdin pipe hung numpy's first import on Windows
    # (OpenBLAS's runtime waits on the pipe holding the loader lock).
    code = (
        "import threading, time\n"
        "from muse.__main__ import _wait_for_stdin_eof\n"
        "threading.Thread(target=_wait_for_stdin_eof, daemon=True).start()\n"
        "time.sleep(0.5)\n"
        "import numpy\n"
        "print('loaded', flush=True)\n"
    )
    p = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    try:
        p.wait(timeout=30)  # stdin stays open: the parent is still alive
    except subprocess.TimeoutExpired:
        p.kill()
        pytest.fail("numpy import hung while stdin was watched")
    finally:
        p.stdin.close()
    assert "loaded" in p.stdout.read()


def test_keeps_running_without_the_flag(tmp_path):
    p = _launch(tmp_path, watch=False)
    p.stdin.close()
    with pytest.raises(subprocess.TimeoutExpired):
        p.wait(timeout=3)
    p.terminate()
    p.wait(timeout=20)
