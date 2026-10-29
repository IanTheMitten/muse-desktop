"""Library-wide batch embedding over a line protocol.

`python -m content_embed.batch` reads one JSON object per line from stdin --
`{"id": <int>, "path": "<audio file>"}` -- and writes one JSON object per line
to stdout, in input order: `{"id": <int>, "vec": [float, ...]}` on success,
`{"id": <int>, "error": "<message>"}` on failure.

The point of a long-lived process rather than one invocation per track is
`model._load_model()`'s module-global cache: the model loads once and every
track after the first pays only for inference.

`id` is an opaque correlation token. This module knows nothing about what it
identifies -- keeping it that way is what lets `muse` call this without either
project importing the other.

Every input line produces exactly one output line, and no input can end the
process. `embed()` raises loudly on unreadable audio by design, and a batch
over a real library will meet a corrupt file sooner or later; ending the run
there would throw away every track after it. The same holds for a malformed
*return value* (the wrong vector shape, say) and for a malformed *request
line* -- unparseable JSON, JSON that isn't an object, or an object missing
`id`/`path` -- all of these become one error line and the run continues.

Both ends of this pipe are Python's own `json`, so a `NaN`/`Infinity`
component round-trips exactly rather than needing to be sanitized here.

stdout belongs to this protocol and nothing else. `content_embed.model`
loads MERT via `AutoModel.from_pretrained(..., trust_remote_code=True)`,
which runs the checkpoint's own code from the HF Hub -- and that code
`print()`s straight to stdout the moment `embed()` is first called
(verified: MERT-v1-95M's own third-party loader prints a stray warning
about an optional dependency, unprompted). `main()` seizes the real stdout
file descriptor for the protocol before the model -- or anything it
imports -- is ever touched, and redirects fd 1 itself to stderr, so any
future write to it from any layer lands in the journal instead of
corrupting a protocol line. See `_seize_stdout_for_the_protocol`.

Getting that ordering right needs one more piece outside this file:
`python -m content_embed.batch` imports the `content_embed` *package*
first, running `content_embed/__init__.py`, before this module's own code
ever runs. A plain `from .model import embed` there -- the obvious way to
expose the public `content_embed.embed` API -- would have pulled in the
whole numpy/torch/torchaudio/transformers chain ahead of `main()`'s fd
swap regardless of anything done here. `__init__.py` resolves `embed`
lazily (PEP 562 module `__getattr__`) for exactly that reason.
"""

import argparse
import json
import os
import sys
from typing import Callable, TextIO


def run(stdin: TextIO, stdout: TextIO, *, embed: Callable | None = None) -> None:
    """Read work lines, write result lines. `embed` is injected so the line
    protocol is testable without loading a 95M-parameter model.

    When `embed` is omitted (the CLI's own use, via `main`), the real model
    is imported here -- not at module import time -- because importing
    `content_embed.model` pulls in torch/transformers/torchaudio, and
    `main` needs those imports to happen only after it has already secured
    fd 1 for the protocol. Importing this module as a library (as the tests
    do) must never trigger that import or the fd swap as a side effect --
    though this deferral alone is not sufficient for the CLI path, since
    `python -m content_embed.batch` imports the `content_embed` package
    (running `__init__.py`) before this module's code runs at all; see the
    module docstring above and `content_embed/__init__.py` for the other
    half of that ordering.
    """
    if embed is None:
        from .model import embed

    for line in stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                # Valid JSON, wrong shape -- `[1,2,3]`, `42`, `"foo"`, `null`
                # all parse without error, and `.get`/`in` below assume a
                # dict. Reject the shape here, inside the same guarded
                # region, rather than let it raise past this function.
                raise TypeError(f"expected a JSON object, got {type(request).__name__}")
        except Exception as error:
            # Genuinely unparseable, or parseable but not an object: there
            # is no id to recover either way, so `None` is the honest answer
            # here -- unlike the `"path"`/`"id"`-missing cases below, where
            # the id (if any) was right there in the parsed object.
            _emit(stdout, {"id": None, "error": f"bad request line: {error}"})
            continue

        track_id = request.get("id")
        if track_id is None:
            # A missing (or null) id is a bad request, not a successful
            # embed -- the muse side correlates results by id, so a vector
            # with no id attached would be unattributable to any track.
            _emit(stdout, {"id": None, "error": "request missing 'id'"})
            continue
        if "path" not in request:
            _emit(stdout, {"id": track_id, "error": "request missing 'path'"})
            continue
        path = request["path"]

        try:
            vector = embed(path)
            vec = [float(v) for v in vector]
        except Exception as error:
            # Covers both a raised exception from `embed()` and a bad
            # return value (e.g. the wrong shape) that fails to convert --
            # either way it is a line, not a crash.
            _emit(stdout, {"id": track_id, "error": str(error)})
            continue

        _emit(stdout, {"id": track_id, "vec": vec})


def _emit(stdout: TextIO, payload: dict) -> None:
    """One line, flushed immediately -- the reader on the other end commits
    per line, so a buffered write would cost it every track in the buffer if
    the process were killed."""
    stdout.write(json.dumps(payload) + "\n")
    stdout.flush()


def _seize_stdout_for_the_protocol() -> TextIO:
    """Take fd 1 for the JSON line protocol before anything else can write
    to it, and return the file object `_emit` should use from then on.

    Done at the file-descriptor level (`os.dup`/`os.dup2`), not by
    reassigning `sys.stdout`: a Python-level swap only intercepts writes
    made through the `sys.stdout` object, and misses anything that writes
    to fd 1 directly -- a C extension, or, concretely, the
    `trust_remote_code=True` model code this process is about to load,
    whose stray `print()` is the reason this function exists.

    A private duplicate of the original fd 1 is opened first, then fd 1
    itself is pointed at wherever fd 2 (stderr) already points. After that,
    every future write to fd 1 -- from `print()`, from third-party code,
    from anywhere -- lands in the journal, and the only path left to real
    stdout is the file object this function returns.

    Not restored on return: the caller is the CLI entry point, which runs
    to process exit, so there is no "later" state to put fd 1 back into.
    """
    protocol_fd = os.dup(1)
    os.dup2(2, 1)
    return os.fdopen(protocol_fd, "w")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Embed audio files named on stdin; emit vectors on stdout"
    )
    parser.parse_args(argv)

    protocol_stdout = _seize_stdout_for_the_protocol()
    try:
        run(sys.stdin, protocol_stdout)
    finally:
        protocol_stdout.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
