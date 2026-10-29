"""The batch CLI's line protocol.

`embed` is monkeypatched throughout: this tests framing, ordering, and
per-track error isolation, none of which need a real model. `test_model.py`
is where the model itself is covered.
"""

import io
import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

from content_embed import batch

_UNIVERSAL_RECO_ROOT = str(Path(__file__).resolve().parents[1])
_PACKAGE_DIR = Path(_UNIVERSAL_RECO_ROOT) / "content_embed"


def run(lines: list[dict], embedder) -> list[dict]:
    stdin = io.StringIO("".join(json.dumps(line) + "\n" for line in lines))
    stdout = io.StringIO()
    batch.run(stdin, stdout, embed=embedder)
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def test_emits_one_vector_line_per_input():
    results = run(
        [{"id": 7, "path": "/a.mp3"}, {"id": 9, "path": "/b.mp3"}],
        lambda path: np.array([1.0, 2.0, 3.0], dtype=np.float32),
    )
    assert [r["id"] for r in results] == [7, 9]
    assert results[0]["vec"] == [1.0, 2.0, 3.0]
    assert "error" not in results[0]


def test_one_bad_track_becomes_an_error_line_not_a_crash():
    def embedder(path):
        if path == "/bad.mp3":
            raise ValueError("No audio samples decoded from /bad.mp3")
        return np.array([0.5], dtype=np.float32)

    results = run(
        [
            {"id": 1, "path": "/good.mp3"},
            {"id": 2, "path": "/bad.mp3"},
            {"id": 3, "path": "/good.mp3"},
        ],
        embedder,
    )
    assert [r["id"] for r in results] == [1, 2, 3]
    assert results[1]["error"] == "No audio samples decoded from /bad.mp3"
    assert "vec" not in results[1]
    # The run continues past the failure.
    assert results[2]["vec"] == [0.5]


def test_blank_lines_are_skipped():
    stdin = io.StringIO('{"id": 1, "path": "/a.mp3"}\n\n')
    stdout = io.StringIO()
    batch.run(stdin, stdout, embed=lambda path: np.array([1.0], dtype=np.float32))
    assert len(stdout.getvalue().splitlines()) == 1


def test_a_malformed_line_is_reported_and_does_not_stop_the_run():
    stdin = io.StringIO('not json\n{"id": 2, "path": "/a.mp3"}\n')
    stdout = io.StringIO()
    batch.run(stdin, stdout, embed=lambda path: np.array([1.0], dtype=np.float32))
    results = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert results[0]["id"] is None
    assert "error" in results[0]
    assert results[1]["id"] == 2


def test_a_missing_path_reports_the_id_it_could_have_reported():
    """Unlike genuinely unparseable JSON, the id was right there in the
    parsed object -- losing it here would make the result uncorrelatable on
    the muse side, which commits per line keyed by id."""
    stdin = io.StringIO('{"id": 42}\n')
    stdout = io.StringIO()
    batch.run(stdin, stdout, embed=lambda path: np.array([1.0], dtype=np.float32))
    results = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert results == [{"id": 42, "error": "request missing 'path'"}]


def test_a_missing_id_is_a_bad_request_not_a_successful_embed():
    """A vector with no id attached is unattributable to any track on the
    muse side, which correlates results by id -- so this must be an error
    line, not a `vec` line with `id: null`."""
    stdin = io.StringIO('{"path": "/a.mp3"}\n')
    stdout = io.StringIO()
    batch.run(stdin, stdout, embed=lambda path: np.array([1.0], dtype=np.float32))
    results = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert results == [{"id": None, "error": "request missing 'id'"}]


@pytest.mark.parametrize(
    "bad_line",
    [
        pytest.param("not json", id="unparseable-text"),
        pytest.param("[1, 2, 3]", id="json-array"),
        pytest.param("42", id="json-scalar-number"),
        pytest.param('"foo"', id="json-scalar-string"),
        pytest.param("null", id="json-null"),
        pytest.param('{"path": "/a.mp3"}', id="object-missing-id"),
        pytest.param('{"id": 42}', id="object-missing-path"),
    ],
)
def test_every_shape_of_bad_line_reports_an_error_and_the_run_continues(bad_line):
    """The invariant the whole one-process-per-batch design rests on: every
    input line produces exactly one output line, and no input -- however
    malformed -- can end the process. `4bd07f8` broke this for non-dict
    JSON (`[1,2,3]`, `42`, `null`, ...) by calling `.get`/`in` on whatever
    `json.loads` returned without checking its shape first; that failure
    mode doesn't announce itself, it silently drops every line after the
    bad one, which is why every shape is pinned here rather than just the
    ones that happen to already be dicts."""
    stdin = io.StringIO(bad_line + '\n{"id": 7, "path": "/a.mp3"}\n')
    stdout = io.StringIO()
    batch.run(stdin, stdout, embed=lambda path: np.array([1.0], dtype=np.float32))
    lines = stdout.getvalue().splitlines()
    assert len(lines) == 2, "a bad line must not swallow the good line after it"
    first, second = (json.loads(line) for line in lines)
    assert "error" in first
    assert "vec" not in first
    assert second == {"id": 7, "vec": [1.0]}


def test_a_malformed_vector_becomes_an_error_line_not_a_crash():
    """The docstring's "a failing track is a line, not an exit" guarantee
    has to cover a bad return value from `embed`, not just a raised
    exception -- a (1, 768) array instead of (768,) fails to convert to
    scalars, and that failure must not take down the rest of the batch."""
    results = run(
        [{"id": 1, "path": "/bad-shape.mp3"}, {"id": 2, "path": "/ok.mp3"}],
        lambda path: (
            np.zeros((1, 768))
            if path == "/bad-shape.mp3"
            else np.array([1.0], dtype=np.float32)
        ),
    )
    assert results[0]["id"] == 1
    assert "error" in results[0]
    assert "vec" not in results[0]
    assert results[1] == {"id": 2, "vec": [1.0]}


def test_each_line_is_flushed_before_the_next_track_is_processed():
    """The whole "a kill mid-batch costs one track" architecture rests on
    `_emit` flushing per line -- pin the observable property rather than
    the mechanism, so an implementation that buffered and wrote once at the
    end would fail this."""
    stdin = io.StringIO(
        '{"id": 1, "path": "/a.mp3"}\n{"id": 2, "path": "/b.mp3"}\n'
    )
    stdout = io.StringIO()
    seen_before_second = []

    def embedder(path):
        if path == "/b.mp3":
            seen_before_second.append(stdout.getvalue())
        return np.array([1.0], dtype=np.float32)

    batch.run(stdin, stdout, embed=embedder)
    assert len(seen_before_second) == 1
    assert json.loads(seen_before_second[0].strip())["id"] == 1


_NOISY_CLI = textwrap.dedent(
    f"""
    import sys
    sys.path.insert(0, {_UNIVERSAL_RECO_ROOT!r})
    import numpy as np
    import content_embed.model as model

    def _noisy_embed(path):
        # Stands in for `trust_remote_code=True` model code writing to
        # stdout mid-embed -- verified against the real MERT weights,
        # which print an unrelated warning to stdout the moment `embed()`
        # first loads the model (see tests/test_content_slow.py on the
        # muse side, which caught this against the real thing).
        print("WARNING: something from third-party code", flush=True)
        return np.array([1.0, 2.0], dtype=np.float32)

    model.embed = _noisy_embed
    from content_embed import batch
    sys.exit(batch.main([]))
    """
)


def test_third_party_stdout_noise_does_not_corrupt_the_protocol(tmp_path):
    """A real subprocess, not `io.StringIO`: the fd-level swap in `main`/
    `_seize_stdout_for_the_protocol` only does anything against a real fd
    1, which the `io.StringIO`-based `run()` calls above never exercise.

    `content_embed.model.embed` is monkeypatched, before `content_embed
    .batch` is even imported, to `print()` before returning a vector --
    the property under test is that a protocol line survives arbitrary
    third-party noise, not any particular warning text.
    """
    script = tmp_path / "noisy_cli.py"
    script.write_text(_NOISY_CLI)

    result = subprocess.run(
        [sys.executable, str(script)],
        input='{"id": 1, "path": "/a.mp3"}\n',
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 1, f"stdout must be clean JSON only: {result.stdout!r}"
    assert json.loads(lines[0]) == {"id": 1, "vec": [1.0, 2.0]}

    # The noise isn't lost -- it lands on stderr, where an operator can
    # still see it.
    assert "WARNING: something from third-party code" in result.stderr


def test_noise_at_model_import_time_does_not_reach_the_protocol_either(tmp_path):
    """The other half of the fd-swap fix: a `print(..., flush=True)` at the
    *top* of `model.py`, not inside `embed()`.

    This is the shape that actually broke `edfbbe5` --
    `content_embed/__init__.py` used to be a plain `from .model import
    embed`, and `python -m content_embed.batch` imports the *package*
    (running `__init__.py`) before `batch.py`'s own `main()` ever runs, so
    that import happened ahead of the fd swap regardless of anything
    deferred inside `batch.py` itself. The test above can't reach this: its
    noise fires inside the injected `embed`, which only runs after `main`
    has already swapped fd 1.

    Runs the *real* `__init__.py` and `batch.py` -- copied, not
    reimplemented, so a regression in either file's import-time laziness
    is actually caught -- against a stand-in `model.py`, via a real
    `python -m content_embed.batch` invocation so the package-import
    ordering is the real ordering, not a simulation of it.
    """
    fake_package = tmp_path / "content_embed"
    fake_package.mkdir()
    shutil.copy(_PACKAGE_DIR / "__init__.py", fake_package / "__init__.py")
    shutil.copy(_PACKAGE_DIR / "batch.py", fake_package / "batch.py")
    (fake_package / "model.py").write_text(
        textwrap.dedent(
            """
            import numpy as np

            print("WARNING: noise at model import time", flush=True)

            def embed(path):
                return np.array([1.0, 2.0], dtype=np.float32)
            """
        )
    )

    result = subprocess.run(
        [sys.executable, "-m", "content_embed.batch"],
        input='{"id": 1, "path": "/a.mp3"}\n',
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 1, f"stdout must be clean JSON only: {result.stdout!r}"
    assert json.loads(lines[0]) == {"id": 1, "vec": [1.0, 2.0]}
    assert "WARNING: noise at model import time" in result.stderr
