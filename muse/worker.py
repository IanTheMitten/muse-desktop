"""Sidecar-side client for the ML worker process. No torch: the models live in
the worker, one JSON line per request on stdin, one reply line on stdout."""
from __future__ import annotations

import json
import os
import queue
import shlex
import subprocess
import threading
from pathlib import Path
from typing import Iterator

from .content import MERT_MODEL
from .embed import AUDIO_VA_MODEL, CLAP_MODEL, LYRICS_VA_MODEL, MINILM_MODEL

LAYA_MODEL = "laya-muse-v4"
MAX_STARTS = 2


class WorkerError(RuntimeError):
    """The worker died, hung or would not start; the request has no answer."""


class WorkerReplyError(Exception):
    """The worker answered this request with an error and stays up."""


def worker_command(models_dir: Path, override: str | None = None) -> list[str]:
    if override:
        return shlex.split(override)
    exe = "muse-worker.exe" if os.name == "nt" else "muse-worker"
    return [str(models_dir / "worker" / "muse-worker" / exe), "--weights", str(models_dir / "weights")]


class WorkerClient:
    def __init__(self, command: list[str], *, idle_seconds: float = 120.0,
                 timeout_seconds: float = 600.0):
        self._command = command
        self._idle = idle_seconds
        self._timeout = timeout_seconds
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._replies: queue.Queue = queue.Queue()
        self._timer: threading.Timer | None = None
        self._starts = 0
        self._next_id = 0

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def begin(self) -> None:
        """Start a session: a crashed worker may be relaunched twice more."""
        self._starts = 0

    def _spawn(self) -> None:
        if self._starts >= MAX_STARTS:
            raise WorkerError("Smart features stopped working; they will retry next time")
        self._starts += 1
        threads = str(max(1, (os.cpu_count() or 2) // 2))
        env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
               "PYTHONIOENCODING": "utf-8", "OMP_NUM_THREADS": threads,
               "MKL_NUM_THREADS": threads, "OPENBLAS_NUM_THREADS": threads}
        try:
            proc = subprocess.Popen(
                self._command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
                text=True, encoding="utf-8", bufsize=1, env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError as e:
            raise WorkerError(f"Could not start the worker: {e}") from e
        replies: queue.Queue = queue.Queue()

        def read() -> None:
            for line in proc.stdout:
                replies.put(line)
            replies.put(None)

        threading.Thread(target=read, daemon=True).start()
        self._proc, self._replies = proc, replies

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            proc.kill()
            proc.wait()

    def request(self, op: str, **args) -> object:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            try:
                if not self.running:
                    self._kill()
                    self._spawn()
                self._next_id += 1
                rid = self._next_id
                try:
                    self._proc.stdin.write(json.dumps({"id": rid, "op": op, **args}) + "\n")
                    self._proc.stdin.flush()
                except OSError as e:  # BrokenPipeError included
                    self._kill()
                    raise WorkerError(f"The worker went away: {e}") from e
                while True:
                    try:
                        line = self._replies.get(timeout=self._timeout)
                    except queue.Empty:
                        self._kill()
                        raise WorkerError("The worker stopped answering") from None
                    if line is None:
                        self._kill()
                        raise WorkerError("The worker exited")
                    try:
                        reply = json.loads(line)
                    except ValueError:
                        continue
                    if reply.get("id") != rid and reply.get("id") is not None:
                        continue
                    if "error" in reply:
                        raise WorkerReplyError(reply["error"])
                    return reply.get("ok")
            finally:
                if self.running:
                    self._timer = threading.Timer(self._idle, self.stop)
                    self._timer.daemon = True
                    self._timer.start()

    def stop(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            proc, self._proc = self._proc, None
            if proc is None:
                return
            try:
                proc.stdin.close()
                proc.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
                proc.wait()


class WorkerAudioEncoder:
    model_name = CLAP_MODEL

    def __init__(self, client: WorkerClient):
        self._c = client

    def encode(self, audio_path: Path) -> list[float]:
        return self._c.request("clap_audio", path=str(audio_path))

    def encode_text(self, text: str) -> list[float]:
        return self._c.request("clap_text", text=text)


class WorkerLyricsEncoder:
    model_name = MINILM_MODEL

    def __init__(self, client: WorkerClient):
        self._c = client

    def encode(self, text: str) -> list[float]:
        return self._c.request("minilm", text=text)


class WorkerAudioVAEncoder:
    model_name = AUDIO_VA_MODEL

    def __init__(self, client: WorkerClient):
        self._c = client

    def encode(self, audio_path: Path) -> tuple[float, float]:
        return tuple(self._c.request("audio_va", path=str(audio_path)))


class WorkerLyricsVAEncoder:
    model_name = LYRICS_VA_MODEL

    def __init__(self, client: WorkerClient):
        self._c = client

    def encode(self, text: str) -> tuple[float, float]:
        return tuple(self._c.request("lyrics_va", text=text))


class WorkerContentEmbedder:
    model_name = MERT_MODEL

    def __init__(self, client: WorkerClient):
        self._c = client

    def embed_many(self, items: list[tuple[int, str]]) -> Iterator[tuple[int, list[float] | None, str | None]]:
        for rowid, path in items:
            try:
                yield rowid, self._c.request("mert", path=str(path)), None
            except WorkerReplyError as e:
                yield rowid, None, str(e)


def worker_ask(client: WorkerClient):
    """Same shape as `lyric_tag_batch.backend_ask`, answered by the worker's Laya."""
    def ask(state, groups):
        questions = {g: {"type": "choice", "instructions": instructions, "criteria": labels}
                     for g, (instructions, labels) in groups.items()}
        answers = client.request("laya", state=state, questions=questions)
        return {g: answers[g]["probabilities"] for g in groups}
    return ask
