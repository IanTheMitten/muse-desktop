"""`SubprocessContentEmbedder` against a fake CLI.

The fake is a few lines of Python emitting canned JSON lines, so the
driver's framing, ordering, and error handling are covered with neither
`universal_reco` nor its virtualenv present. The real CLI's own behaviour is
`universal_reco/tests/test_batch.py`'s job.
"""

import subprocess
import sys
import textwrap
import time

import pytest

import muse.content as content_module
from muse.content import SubprocessContentEmbedder

ECHO_CLI = textwrap.dedent(
    """
    import json, sys
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        if request["path"].endswith("bad.mp3"):
            print(json.dumps({"id": request["id"], "error": "cannot decode"}))
        else:
            print(json.dumps({"id": request["id"], "vec": [1.0, 2.0]}))
        sys.stdout.flush()
    """
)


def _embedder(script: str, tmp_path) -> SubprocessContentEmbedder:
    path = tmp_path / "fake_cli.py"
    path.write_text(script)
    return SubprocessContentEmbedder(command=[sys.executable, str(path)])


# Records each id it receives to a marker file *before* replying, so a test
# can observe how much work the subprocess has actually done by the time the
# caller has consumed only one result -- final ordering alone (as in
# `test_results_stream_rather_than_arriving_all_at_once` above) can't tell a
# lockstep implementation from one that drained the whole batch first, since
# both produce the same list once fully consumed.
COUNTING_CLI = textwrap.dedent(
    """
    import json, sys
    from pathlib import Path
    marker = Path(sys.argv[1])
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        with marker.open("a") as fh:
            fh.write(str(request["id"]) + "\\n")
            fh.flush()
        print(json.dumps({"id": request["id"], "vec": [1.0, 2.0]}))
        sys.stdout.flush()
    """
)


def test_vectors_come_back_keyed_by_the_ids_that_were_sent(tmp_path):
    embedder = _embedder(ECHO_CLI, tmp_path)
    results = list(embedder.embed_many([(4, "/a.mp3"), (9, "/b.mp3")]))
    assert results == [(4, [1.0, 2.0], None), (9, [1.0, 2.0], None)]


def test_an_error_line_becomes_an_error_result(tmp_path):
    embedder = _embedder(ECHO_CLI, tmp_path)
    results = list(embedder.embed_many([(1, "/bad.mp3"), (2, "/ok.mp3")]))
    assert results[0] == (1, None, "cannot decode")
    assert results[1] == (2, [1.0, 2.0], None)


def test_results_are_yielded_one_at_a_time_in_request_order(tmp_path):
    """Consuming `embed_many` item by item returns each track's own vector,
    in the order the work was submitted.

    This does *not* prove streaming, despite what its earlier name claimed:
    `embed_many` contains a `yield`, so a buffering implementation
    (`results = [...]; yield from results`) satisfies every assertion here.
    `test_only_one_request_is_sent_before_the_first_result_is_yielded` below
    is what pins that. This one is kept for the ordering and per-item value
    correctness it does check, which that test deliberately does not."""
    embedder = _embedder(ECHO_CLI, tmp_path)
    stream = embedder.embed_many([(i, "/a.mp3") for i in range(3)])
    first = next(stream)
    assert first[0] == 0
    assert list(stream) == [(1, [1.0, 2.0], None), (2, [1.0, 2.0], None)]


def test_only_one_request_is_sent_before_the_first_result_is_yielded(tmp_path):
    """Non-vacuous version of the streaming test above: a buffering
    implementation (`results = [...]; yield from results`) would have pumped
    every request into the subprocess -- and the fake would have recorded
    every id -- before the caller ever sees a result. Consuming only the
    first result and then checking the marker file is what tells lockstep
    apart from buffered; checking the final list order (as above) cannot."""
    script = tmp_path / "counting_cli.py"
    script.write_text(COUNTING_CLI)
    marker = tmp_path / "received.txt"
    embedder = SubprocessContentEmbedder(
        command=[sys.executable, str(script), str(marker)]
    )
    stream = embedder.embed_many([(0, "/a.mp3"), (1, "/b.mp3"), (2, "/c.mp3")])
    first = next(stream)
    assert first == (0, [1.0, 2.0], None)
    assert marker.read_text().splitlines() == ["0"]


def test_a_subprocess_that_dies_raises_rather_than_ending_quietly(tmp_path):
    """A crashed embedder must not look like a completed batch — that would
    mark the job done with most of the library still unembedded."""
    embedder = _embedder("import sys; sys.exit(3)", tmp_path)
    with pytest.raises(RuntimeError, match="exit code 3"):
        list(embedder.embed_many([(1, "/a.mp3")]))


def test_a_subprocess_that_dies_mid_batch_raises_the_same_diagnosable_error(
    tmp_path,
):
    """The realistic death: the real CLI can be OOM-killed hours into a
    multi-hour batch, after replying to some tracks but not all. The pipe
    breaking under the *next* write must not surface as a bare
    `BrokenPipeError` -- it has to become the same diagnosable `RuntimeError`
    the "dies before any output" case above raises, carrying the real exit
    code, and the result already yielded must still have reached the
    caller (it was already committed by `run_content_batch` and must not be
    lost)."""
    script = tmp_path / "dies_after_one.py"
    script.write_text(
        textwrap.dedent(
            """
            import json, sys
            line = sys.stdin.readline()
            request = json.loads(line)
            print(json.dumps({"id": request["id"], "vec": [1.0, 2.0]}))
            sys.stdout.flush()
            sys.exit(7)
            """
        )
    )
    embedder = SubprocessContentEmbedder(command=[sys.executable, str(script)])
    stream = embedder.embed_many([(1, "/a.mp3"), (2, "/b.mp3")])
    first = next(stream)
    assert first == (1, [1.0, 2.0], None)
    # Give the child time to have actually exited before the next write --
    # without this, the race can instead resolve as the next readline()
    # seeing EOF, which was already handled before this fix. Sleeping here
    # is what pins the write-failure path specifically.
    time.sleep(0.3)
    with pytest.raises(RuntimeError, match="exit code 7"):
        list(stream)


def test_the_model_name_is_the_real_checkpoint(tmp_path):
    embedder = _embedder(ECHO_CLI, tmp_path)
    assert embedder.model_name == "m-a-p/MERT-v1-95M"


def test_a_malformed_response_line_becomes_a_diagnosable_runtimeerror(tmp_path):
    """Before this guard, `json.loads` on a non-JSON line raised a bare
    `JSONDecodeError` uncorrelated to any track, breaking the diagnosable-
    error pattern the write-failure and exit-code paths already follow.
    Verified against the unfixed code in a standalone harness (not this
    file, to avoid touching the shared module mid-edit): the old class
    let `JSONDecodeError` escape directly; this asserts it is wrapped."""
    embedder = _embedder(
        "import sys\nsys.stdin.readline()\nprint('not json at all')\n"
        "sys.stdout.flush()\n",
        tmp_path,
    )
    with pytest.raises(RuntimeError, match="malformed response line"):
        list(embedder.embed_many([(1, "/a.mp3")]))


def test_a_response_missing_vec_becomes_a_diagnosable_runtimeerror(tmp_path):
    """Same defect, different shape: a well-formed JSON object missing
    `vec` raised a bare `KeyError` before this guard. Verified against the
    unfixed code the same way as the malformed-JSON test above."""
    embedder = _embedder(
        textwrap.dedent(
            """
            import json, sys
            line = sys.stdin.readline()
            request = json.loads(line)
            print(json.dumps({"id": request["id"]}))
            sys.stdout.flush()
            """
        ),
        tmp_path,
    )
    with pytest.raises(RuntimeError, match="malformed response line"):
        list(embedder.embed_many([(1, "/a.mp3")]))


def test_a_mismatched_response_id_raises_instead_of_corrupting_the_library(
    tmp_path,
):
    """The one that matters most: without this guard, a response whose `id`
    does not match the track it was sent for is accepted silently and
    yielded as-is -- `run_content_batch` commits by whatever id comes back,
    so a desynchronized stream (e.g. MERT's `trust_remote_code=True`
    printing a stray line to stdout) writes a vector under the wrong track,
    with no exception and no log. Verified against the unfixed code in a
    standalone harness: it returned `[(101, [1.0], None)]` for a request
    sent as id 1 -- silent corruption, not a crash. This asserts a loud
    RuntimeError instead."""
    embedder = _embedder(
        textwrap.dedent(
            """
            import json, sys
            line = sys.stdin.readline()
            request = json.loads(line)
            print(json.dumps({"id": request["id"] + 100, "vec": [1.0]}))
            sys.stdout.flush()
            """
        ),
        tmp_path,
    )
    with pytest.raises(RuntimeError, match="expected a response for track 1"):
        list(embedder.embed_many([(1, "/a.mp3")]))


def test_a_non_ascii_path_round_trips_through_the_subprocess(tmp_path):
    """A round-trip, not a regression test for the encoding fix: this
    process's own locale is already UTF-8 (`C.UTF-8` on this host), so this
    passes identically whether or not `encoding="utf-8"` is passed to
    `Popen` -- deleting it does not make this fail. It cannot catch a
    locale regression on a UTF-8 host, because the parent side's decoding
    is governed by the parent process's own locale, not anything the test
    can vary through the child.

    `encoding="utf-8"` on the `Popen` call is what actually removes the
    ambiguity -- this test documents the expectation (non-ASCII artist and
    track names are ordinary in a personal music library, and the systemd
    user units that run the real batch set no `LANG`) rather than
    enforcing it."""
    echoes_path = textwrap.dedent(
        """
        import json, sys
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            request = json.loads(line)
            print(json.dumps(
                {"id": request["id"], "vec": [1.0], "path": request["path"]}
            ))
            sys.stdout.flush()
        """
    )
    embedder = _embedder(echoes_path, tmp_path)
    non_ascii_path = "/music/Sigur Rós/Ágætis byrjun/Svefn-g-englar.flac"
    results = list(embedder.embed_many([(1, non_ascii_path)]))
    assert results == [(1, [1.0], None)]


def test_abandoning_the_generator_mid_stream_still_reaps_the_child(
    tmp_path, monkeypatch
):
    """If the caller stops iterating early -- a cancelled batch, or the
    generator simply going out of scope -- the child must not be left
    running or become a zombie. `GeneratorExit` drives execution through
    the same `finally` a normal completion or a raised error would, and
    this is the one path no existing test sends anything through."""
    captured = []
    real_popen = subprocess.Popen

    def spy_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        captured.append(process)
        return process

    monkeypatch.setattr(content_module.subprocess, "Popen", spy_popen)

    embedder = _embedder(ECHO_CLI, tmp_path)
    stream = embedder.embed_many([(i, "/a.mp3") for i in range(5)])
    next(stream)
    stream.close()

    [process] = captured
    # If `finally` had not killed and waited on the child, it would still
    # be alive, blocked reading stdin -- `poll()` would report None rather
    # than a real (negative, signal-based) return code.
    assert process.poll() is not None
    # This alone doesn't pin the stdout-close half of `finally`: kill() +
    # wait() already reap the child without it, so this assertion passes
    # even with that close deleted. Assert it directly.
    assert process.stdout.closed is True


def test_a_short_clean_exit_logs_a_warning_naming_the_shortfall(
    tmp_path, caplog
):
    """A child that exits 0 having answered fewer requests than it was
    sent is not an error `run_content_batch` needs to raise on -- the
    unanswered tracks simply stay queued -- but a child that systematically
    truncates every run would under-process the library every night with
    no other signal. This is a warning, not a raise.

    Whether this particular child's early exit is discovered through the
    next write failing or through `readline()` returning EOF is a race this
    test doesn't control -- both paths converge on the same warning rather
    than a raise precisely because the exit code is 0 either way."""
    embedder = _embedder(
        textwrap.dedent(
            """
            import json, sys
            line = sys.stdin.readline()
            request = json.loads(line)
            print(json.dumps({"id": request["id"], "vec": [1.0]}))
            sys.stdout.flush()
            # exits 0 without reading the second request
            """
        ),
        tmp_path,
    )
    with caplog.at_level("WARNING", logger="muse.content"):
        results = list(embedder.embed_many([(1, "/a.mp3"), (2, "/b.mp3")]))
    assert results == [(1, [1.0], None)]
    assert any(
        "answered 1 of 2 requests" in record.message for record in caplog.records
    )
