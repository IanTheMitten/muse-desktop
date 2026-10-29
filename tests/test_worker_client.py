"""The worker client and adapters against a fake worker process."""
from __future__ import annotations

import os
import sys
import textwrap
import time
from pathlib import Path

import pytest

from muse import content, embed
from muse.worker import (
    LAYA_MODEL,
    WorkerAudioEncoder,
    WorkerAudioVAEncoder,
    WorkerClient,
    WorkerContentEmbedder,
    WorkerError,
    WorkerLyricsEncoder,
    WorkerLyricsVAEncoder,
    WorkerReplyError,
    worker_ask,
    worker_command,
)

FAKE = textwrap.dedent('''
    import json, os, sys, time
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "starts"), "a") as f:
        f.write("start\\n")
    for line in sys.stdin:
        req = json.loads(line)
        i, op = req["id"], req["op"]
        args = {k: v for k, v in req.items() if k not in ("id", "op")}
        ok = None
        if op == "echo":
            ok = args
        elif op == "pid":
            ok = os.getpid()
        elif op == "fail":
            print(json.dumps({"id": i, "error": "boom"}), flush=True)
            continue
        elif op == "die":
            os._exit(3)
        elif op == "hang":
            time.sleep(60)
        elif op in ("clap_audio", "clap_text"):
            ok = [0.1] * 512
        elif op == "minilm":
            ok = [0.1] * 384
        elif op in ("audio_va", "lyrics_va"):
            ok = [0.5, -0.5]
        elif op == "mert":
            if args["path"].endswith("bad.wav"):
                print(json.dumps({"id": i, "error": "bad file"}), flush=True)
                continue
            ok = [0.2] * 768
        elif op == "laya":
            ok = {g: {"probabilities": {"a": 0.7, "b": 0.3}} for g in args["questions"]}
        elif op == "env":
            ok = {"OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS"),
                  "HF_HUB_OFFLINE": os.environ.get("HF_HUB_OFFLINE")}
        print(json.dumps({"id": i, "ok": ok}), flush=True)
''')


@pytest.fixture
def make_client(tmp_path):
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE)
    clients = []

    def make(**kw):
        c = WorkerClient([sys.executable, str(script)], **kw)
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.stop()


def _starts(tmp_path) -> int:
    f = tmp_path / "starts"
    return len(f.read_text().splitlines()) if f.exists() else 0


def _gone(pid: int, within: float = 5.0) -> bool:
    end = time.monotonic() + within
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.05)
    return False


def test_request_echo(make_client):
    assert make_client().request("echo", x=1) == {"x": 1}


def test_reply_error_keeps_process(make_client):
    c = make_client()
    pid = c.request("pid")
    with pytest.raises(WorkerReplyError, match="boom"):
        c.request("fail")
    assert c.request("pid") == pid


def test_die_then_restart_with_new_pid(make_client):
    c = make_client()
    pid = c.request("pid")
    with pytest.raises(WorkerError):
        c.request("die")
    assert c.request("pid") != pid


def test_two_dies_block_third_until_begin(make_client, tmp_path):
    c = make_client()
    for _ in range(2):
        with pytest.raises(WorkerError):
            c.request("die")
    with pytest.raises(WorkerError):
        c.request("pid")
    assert _starts(tmp_path) == 2
    c.begin()
    c.request("pid")
    assert _starts(tmp_path) == 3


def test_hang_times_out_and_process_is_gone(make_client):
    c = make_client(timeout_seconds=1)
    pid = c.request("pid")
    t0 = time.monotonic()
    with pytest.raises(WorkerError):
        c.request("hang")
    assert time.monotonic() - t0 < 5
    assert _gone(pid)


def test_idle_stops_worker(make_client):
    c = make_client(idle_seconds=0.5)
    c.request("pid")
    assert c.running is True
    time.sleep(1.5)
    assert c.running is False


def test_stop_ends_process(make_client):
    c = make_client()
    pid = c.request("pid")
    c.stop()
    assert _gone(pid)


def test_worker_env(make_client):
    env = make_client().request("env")
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["OMP_NUM_THREADS"] == str(max(1, (os.cpu_count() or 2) // 2))


def test_lazy_start(make_client, tmp_path):
    c = make_client()
    assert _starts(tmp_path) == 0
    assert c.running is False


def test_audio_adapter(make_client):
    enc = WorkerAudioEncoder(make_client())
    assert len(enc.encode(Path("x.wav"))) == 512
    assert len(enc.encode_text("hi")) == 512


def test_lyrics_adapter(make_client):
    assert len(WorkerLyricsEncoder(make_client()).encode("hi")) == 384


def test_va_adapters_return_tuples(make_client):
    c = make_client()
    assert WorkerAudioVAEncoder(c).encode(Path("x.wav")) == (0.5, -0.5)
    assert WorkerLyricsVAEncoder(c).encode("hi") == (0.5, -0.5)


def test_content_embed_many(make_client):
    emb = WorkerContentEmbedder(make_client())
    got = list(emb.embed_many([(1, "a.wav"), (2, "bad.wav"), (3, "c.wav")]))
    assert got == [(1, [0.2] * 768, None), (2, None, "bad file"), (3, [0.2] * 768, None)]


def test_worker_ask(make_client):
    ask = worker_ask(make_client())
    got = ask({"text": "x"}, {"g": ("instr", {"a": "A", "b": "B"})})
    assert got == {"g": {"a": 0.7, "b": 0.3}}


def test_model_names():
    assert WorkerAudioEncoder.model_name == embed.CLAP_MODEL
    assert WorkerContentEmbedder.model_name == content.MERT_MODEL
    assert LAYA_MODEL == "laya-muse-v4"


def test_worker_command_default(tmp_path):
    cmd = worker_command(tmp_path / "models")
    assert cmd[-2:] == ["--weights", str(tmp_path / "models" / "weights")]
    exe = "muse-worker.exe" if os.name == "nt" else "muse-worker"
    assert Path(cmd[0]).parts[-2:] == ("muse-worker", exe)


def test_worker_command_override(tmp_path):
    assert worker_command(tmp_path, "uv run x --y") == ["uv", "run", "x", "--y"]
