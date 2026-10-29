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


def test_keeps_running_without_the_flag(tmp_path):
    p = _launch(tmp_path, watch=False)
    p.stdin.close()
    with pytest.raises(subprocess.TimeoutExpired):
        p.wait(timeout=3)
    p.terminate()
    p.wait(timeout=20)
