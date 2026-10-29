"""The ML worker: one JSON object per stdin line in, one per stdout line out.
Models load lazily from `<weights>/<key>`; torch is imported only when a
handler first constructs a model, never at module import."""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Callable, TextIO

PROTOCOL = 1


def serve(
    stdin: TextIO,
    stdout: TextIO,
    handlers: dict[str, Callable[[dict], object]],
) -> None:
    def emit(payload: dict) -> None:
        stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        stdout.flush()

    for line in stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except ValueError as error:
            emit({"id": None, "error": f"bad request: {error}"})
            continue
        if not isinstance(request, dict):
            emit({"id": None, "error": "bad request: not a JSON object"})
            continue
        rid = request.get("id")
        if not isinstance(rid, int) or isinstance(rid, bool):
            emit({"id": None, "error": "bad request: id must be an integer"})
            continue
        op = request.get("op")
        if op == "hello":
            emit({"id": rid, "ok": {"protocol": PROTOCOL, "ops": sorted(handlers)}})
            continue
        handler = handlers.get(op) if isinstance(op, str) else None
        if handler is None:
            emit({"id": rid, "error": f"unknown op {op}"})
            continue
        try:
            result = handler(request)
        except Exception as error:
            emit({"id": rid, "error": f"{type(error).__name__}: {error}"})
            continue
        emit({"id": rid, "ok": result})


def real_handlers(weights: Path) -> dict[str, Callable[[dict], object]]:
    cache: dict[str, object] = {}
    threads_set = False

    def src(key: str) -> Path | None:
        return weights / key if (weights / key).is_dir() else None

    def get(key: str, build: Callable[[], object]):
        nonlocal threads_set
        if key not in cache:
            if not threads_set:
                import torch

                torch.set_num_threads(max(1, (os.cpu_count() or 2) // 2))
                threads_set = True
            cache[key] = build()
        return cache[key]

    def clap():
        from .embed import ClapAudioEncoder

        return get("clap", lambda: ClapAudioEncoder(source=src("clap")))

    def minilm():
        from .embed import MiniLmLyricsEncoder

        return get("minilm", lambda: MiniLmLyricsEncoder(source=src("minilm")))

    def audio_va():
        from .embed import MerAudioVAEncoder

        return get("audio_va", lambda: MerAudioVAEncoder(source=src("audio_va")))

    def lyrics_va():
        from .embed import TextVAEncoder

        return get("lyrics_va", lambda: TextVAEncoder(source=src("lyrics_va")))

    def mert():
        from .mert import MertEncoder

        return get("mert", lambda: MertEncoder(source=src("mert")))

    def laya(args: dict):
        if not (weights / "laya").is_dir():
            raise RuntimeError("Laya weights missing")

        def build():
            import laya as laya_pkg

            return laya_pkg.load(str(weights / "laya"), device="cpu")

        return get("laya", build).system_one(args["state"], args["questions"])

    return {
        "clap_audio": lambda a: clap().encode(Path(a["path"])),
        "clap_text": lambda a: clap().encode_text(a["text"]),
        "minilm": lambda a: minilm().encode(a["text"]),
        "audio_va": lambda a: list(audio_va().encode(Path(a["path"]))),
        "lyrics_va": lambda a: list(lyrics_va().encode(a["text"])),
        "mert": lambda a: mert().encode(Path(a["path"])),
        "laya": laya,
    }


def _seize_stdout_for_the_protocol() -> TextIO:
    """Point fd 1 at stderr and return a private file on the original stdout,
    so stray prints from model code (a Python swap of sys.stdout would miss
    C extensions and direct fd writes) can never corrupt the protocol."""
    protocol_fd = os.dup(1)
    os.dup2(2, 1)
    return os.fdopen(protocol_fd, "w", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Muse ML worker (JSON lines)")
    parser.add_argument("--weights", type=Path, required=True)
    args = parser.parse_args(argv)

    proto = _seize_stdout_for_the_protocol()
    try:
        serve(sys.stdin, proto, real_handlers(args.weights))
    finally:
        proto.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
