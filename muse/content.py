"""The MERT content leg: the seam over `universal_reco`, the batch that
fills the work queue, and the corpus statistics the leg is normalized by.

The seam is the same idea as `muse.embed`'s encoder protocols and
`muse.taste`'s `TasteTrainer`, with one difference: what it hides is a
subprocess, not a model object. `universal_reco` needs `torchaudio` and
`torchcodec`, which muse's `embed` extra does not have, and declares
`requires-python >=3.10` against muse's `>=3.14` -- importing it in-process
is not merely a decoupling violation, the dependencies are not there.

`StubContentEmbedder` is what the default test run uses. It exercises the
work queue, the per-track commit, the error path, and the statistics
recompute without `universal_reco` existing at all -- the same rule every
other heavy leg in this codebase follows.
"""

import hashlib
import json
import logging
import random
import shutil
import sqlite3
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .embed import l2_normalize, pack_vector
from .library import content as library_content

log = logging.getLogger("muse.content")

CONTENT_EMBED = "content_embed"

# MERT-v1-95M's hidden size, and therefore the width of every vector this
# leg stores. Not L2-normalized, unlike audio_vec and lyrics_vec: the
# normalization this leg needs is centering, which has to happen before any
# rescale, so a vector normalized at write time would have already lost the
# information the correction operates on.
CONTENT_DIM = 768

MERT_MODEL = "m-a-p/MERT-v1-95M"
STUB_CONTENT_MODEL = "stub-content"

# How many tracks the corpus-statistics recompute samples to build its
# centered-cosine distribution. All pairs of 500 tracks is 124,750 dot
# products over 768 dimensions -- roughly 26s of pure Python, extrapolated
# from a 4.2s benchmark at 19,900 pairs, which is affordable once at the end
# of a batch measured in hours. muse's core has no numpy to make this cheap,
# so the quadratic growth has to be capped somewhere.
#
# 500 rather than 200 because the anchors are measurably noisy at small
# samples. Measuring 199 real tracks (see the design spec) and splitting
# them into disjoint halves of ~100 gave anchors that disagreed by 0.070 on
# `sim_lo` and 0.051 on `sim_hi` -- around +/-0.05 of sampling noise on a
# distribution only ~1.0 wide. That noise falls roughly as 1/sqrt(n), so a
# cap above this library's ~723 playable tracks would remove it entirely
# for now; 500 keeps most of that benefit while bounding the cost if the
# library grows an order of magnitude (7,000 tracks uncapped would be ~24M
# pairs, well over an hour).
#
# The mean, unlike the pairwise pass, is not capped by this constant -- it
# is one pass over every stored vector, not a pair over the sample, and
# stays linear (benchmarked at 11.4s over 50,000 tracks: the dominant cost
# at that scale, but still a single pass, so there is no quadratic blowup to
# cap it against).
STATS_SAMPLE_TRACKS = 500

# Fixed, so the same corpus always produces the same anchors -- a nightly
# batch that shifted the leg's calibration at random would make yesterday's
# scores incomparable with today's for no reason.
STATS_SAMPLE_SEED = 0

# Percentiles of the centered-cosine distribution that become the [0, 1]
# endpoints. Not min/max: a single outlier pair would otherwise set the
# scale for the whole library.
STATS_LOW_PERCENTILE = 1
STATS_HIGH_PERCENTILE = 99

# Fewer than this many vectors and `recompute_stats` refuses to write a
# stats row at all -- a necessary floor, not a sufficient one (see the
# outcome check at the bottom of `recompute_stats`). With exactly two
# vectors the corpus mean sits precisely between them, so the two centered
# vectors are exact negatives of one another: every pair scores -1.0, which
# is one point, not a distribution, and no percentile method can pull two
# different anchors out of it. Three is the fewest vectors that can produce
# two pairwise similarities that might differ.
MIN_STATS_TRACKS = 3

# How much of the library must be represented in `content_stats` before the
# leg is allowed to score anything, as a fraction of playable tracks.
#
# Null-leg renormalization is fair when a leg's absence is *random*. Content
# absence is not: `library.content.pending()` orders by `track_rowid`, so a
# partial batch has embedded a prefix, and switching the leg on mid-batch
# would make it a systematic boost for low-rowid tracks -- which on this
# library means early imports, not better matches. The gate is therefore
# library-wide: the leg is dark for everything or live for everything, and a
# partial rollout is invisible rather than skewed.
#
# It reads `content_stats.track_count` rather than the count of stored
# vectors, because the anchors are the fragile part. Vectors commit per
# track; the statistics are rewritten only at the end of a batch, so a run
# that dies at hour ten leaves seven hundred good vectors normalized against
# anchors measured over twenty-two tracks. `track_count` is how many tracks
# the anchors actually saw, and that is the number worth trusting.
CONTENT_MIN_COVERAGE = 0.9



class ContentEmbedder(Protocol):
    model_name: str

    def embed_many(
        self, items: list[tuple[int, str]]
    ) -> Iterator[tuple[int, list[float] | None, str | None]]:
        """Yield `(track_rowid, vector, error)` per item as results become
        available -- a stream, not a list, because the caller commits each
        result as it arrives and a list would mean losing everything if the
        run were killed before it was complete. Exactly one of `vector` and
        `error` is non-None."""
        ...


def _hash_vector(seed: str, dim: int = CONTENT_DIM) -> list[float]:
    """Deterministic: the same seed always produces the same vector. Same
    approach as `muse.embed._hash_vector`, reimplemented here for the same
    reason `muse.taste` reimplements it -- so this module's only dependency
    on `muse.embed` is its public vector helpers, not a private one."""
    values: list[float] = []
    counter = 0
    while len(values) < dim:
        digest = hashlib.sha256(f"{seed}:{counter}".encode()).digest()
        for offset in range(0, len(digest) - 3, 4):
            if len(values) >= dim:
                break
            n = int.from_bytes(digest[offset : offset + 4], "big")
            values.append((n / 0xFFFFFFFF) * 2 - 1)
        counter += 1
    return l2_normalize(values[:dim])


class StubContentEmbedder:
    """No subprocess, no model, no audio decode. Default embedder for tests
    and for any batch that has not opted into the real one -- deterministic
    per path, the same way `StubAudioEncoder` is."""

    model_name = STUB_CONTENT_MODEL

    def embed_many(
        self, items: list[tuple[int, str]]
    ) -> Iterator[tuple[int, list[float] | None, str | None]]:
        for track_rowid, path in items:
            yield track_rowid, _hash_vector(path), None


# Where `universal_reco` lives relative to this file, and how to run its
# batch CLI in its own virtualenv. `uv run --project` is what picks that
# venv up without this process knowing anything about where it is.
UNIVERSAL_RECO_ROOT = Path(__file__).resolve().parent.parent / "universal_reco"


def _uv_executable() -> str:
    """An absolute path to `uv`, not the bare name.

    A systemd *user* unit inherits the user manager's PATH, which is the
    login default and does not include `~/.local/bin` -- where uv installs
    itself. `deploy/muse-content-embed.service` spells its own ExecStart as
    `%h/.local/bin/uv` for exactly that reason, but that only fixes the
    parent: this list is what the parent then execs, and a bare "uv" here
    raised `FileNotFoundError: [Errno 2] ... 'uv'` before the batch embedded
    a single track. The nightly timer could never have worked.

    `shutil.which` first, so a uv installed anywhere on an interactive
    PATH still wins; the install default second; the bare name last, so the
    failure is still the same recognisable one rather than a new error
    about a path that was only ever a guess.
    """
    found = shutil.which("uv")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "uv"
    return str(fallback) if fallback.exists() else "uv"


DEFAULT_COMMAND = [
    _uv_executable(), "run", "--project", str(UNIVERSAL_RECO_ROOT),
    "python", "-m", "content_embed.batch",
]


class SubprocessContentEmbedder:
    """The real embedder: one long-lived `content_embed.batch` process for
    the whole run.

    One process per batch, not per track, because
    `content_embed.model._load_model()` caches the model in a module global
    precisely because loading it is expensive -- a process per track would
    pay that cost every time.

    Work goes in on stdin and results come back on stdout, one JSON line
    each. Nothing about muse crosses that boundary: `id` is an opaque token
    on the other side, which is what lets `universal_reco` keep its own
    dependencies, its own Python version floor, and its own lockfile.

    `process.stdout.readline()` below has no timeout: a child that neither
    responds nor exits blocks this method forever. That is a deliberate
    deferral, not an oversight -- a `selectors`-based read timeout is real
    complexity for a failure mode an external guard already handles better.
    The backstop is a `RuntimeMaxSec` on the systemd unit that runs the
    nightly batch (`deploy/muse-content-embed.service`), not anything in
    this class.
    """

    model_name = MERT_MODEL

    def __init__(self, command: list[str] | None = None):
        self._command = command or DEFAULT_COMMAND

    def embed_many(
        self, items: list[tuple[int, str]]
    ) -> Iterator[tuple[int, list[float] | None, str | None]]:
        process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",  # not the process locale -- systemd user
            # units set no LANG, and this is a personal music library where
            # non-ASCII artist and track names are ordinary, not an edge case.
            bufsize=1,
        )
        try:
            answered = 0
            for track_rowid, path in items:
                try:
                    process.stdin.write(
                        json.dumps({"id": track_rowid, "path": path}) + "\n"
                    )
                    process.stdin.flush()
                except OSError as error:
                    # The pipe breaking under a write is the realistic way
                    # this process dies -- it can be OOM-killed hours into a
                    # batch, not just fail to start. Converting to the same
                    # RuntimeError the post-loop check raises means a caller
                    # can't tell "died before any output" from "died mid-
                    # batch" except by exit code, which is the only
                    # difference that matters. Everything yielded before
                    # this iteration has already reached the caller and,
                    # per run_content_batch, already been committed -- this
                    # only ends the run, it doesn't unwind it.
                    code = process.wait()
                    if code != 0:
                        raise RuntimeError(
                            f"content_embed.batch exited with exit code {code}"
                        ) from error
                    # A clean (0) exit discovered here rather than through
                    # the `readline()` EOF below is the same "answered
                    # fewer than it was sent" case that check handles, just
                    # reached by a race between this write and the child's
                    # own exit -- not a crash, so fall through to it rather
                    # than raising on an exit code that says nothing failed.
                    break
                line = process.stdout.readline()
                if not line:
                    break

                try:
                    result = json.loads(line)
                    response_id = result.get("id")
                    if "error" not in result:
                        vector = result["vec"]
                except (
                    json.JSONDecodeError, AttributeError, KeyError, TypeError,
                ) as error:
                    # Uncorrelated to any track -- there is no id to trust
                    # here, since the line itself didn't parse into the
                    # shape that would carry one. The line is the whole
                    # diagnostic.
                    raise RuntimeError(
                        "content_embed.batch produced a malformed response"
                        f" line: {line!r}"
                    ) from error

                if response_id != track_rowid:
                    # `content_embed/model.py` loads MERT with
                    # `trust_remote_code=True`, which runs third-party code
                    # that can print() to stdout at any time. One stray
                    # line desynchronizes every response after it by one --
                    # silently, since the ids would still all "arrive," just
                    # attached to the wrong track. Nothing else would ever
                    # notice; `run_content_batch` commits by whatever id
                    # comes back.
                    raise RuntimeError(
                        f"expected a response for track {track_rowid}, got"
                        f" {response_id!r}"
                    )

                answered += 1
                if "error" in result:
                    yield response_id, None, result["error"]
                else:
                    yield response_id, vector, None

            if answered < len(items):
                # A clean exit does not mean every request got an answer --
                # a child that silently truncates every run would
                # under-process the library every night with no other
                # signal. The remainder stays queued (run_content_batch only
                # ever upserts what it received), so this is a warning, not
                # a raise.
                log.warning(
                    "content_embed.batch answered %d of %d requests before"
                    " exiting", answered, len(items),
                )

            try:
                process.stdin.close()
            except OSError:
                # Reaching here after the `break` above means stdin already
                # broke under a write that turned out to be a clean (0)
                # exit -- closing our end again can still raise if that
                # write left data unflushed, and the fd closes underneath
                # regardless of whether the flush inside close() succeeds.
                pass
            code = process.wait()
            if code != 0:
                raise RuntimeError(
                    f"content_embed.batch exited with exit code {code}"
                )
        finally:
            # Close both streams defensively before kill. On the
            # broken-pipe path above, stdin was never explicitly closed --
            # its TextIOWrapper still holds a live finalizer, and its
            # attempt to flush during garbage collection is what produces a
            # spurious "Exception ignored while finalizing" line in the
            # logs otherwise. The same reasoning applies to stdout (e.g. on
            # the GeneratorExit path, if the caller abandons iteration
            # mid-batch, having never reached this close()).
            for stream in (process.stdin, process.stdout):
                try:
                    stream.close()
                except OSError:
                    pass
            if process.poll() is None:
                process.kill()
                process.wait()


@dataclass(slots=True)
class ContentBatchResult:
    """`attempted` is the queue size at the start of the run, snapshotted
    once -- always `embedded + failed` by the time the run returns. Kept
    as its own field (rather than left implicit) because a caller wants
    the queue size even when it is zero, without adding two numbers to
    find that out."""

    attempted: int = 0
    embedded: int = 0
    failed: int = 0


def run_content_batch(
    conn: sqlite3.Connection,
    embedder: ContentEmbedder,
    *,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> ContentBatchResult:
    """Embed every track on the queue, then recompute the corpus statistics.

    Each result is committed as it arrives, so a kill mid-batch costs one
    track and the rest stay on the queue for the next run. A track that
    failed is left off the table entirely rather than stored with a NULL
    vector -- both leave it on the queue, and not writing a row keeps the
    "no row means never tried" reading true.
    """
    pending = library_content.pending(conn)
    result = ContentBatchResult(attempted=len(pending))
    if not pending:
        return result

    items = [(row["track_rowid"], row["file_path"]) for row in pending]
    paths_by_id = dict(items)
    done = 0
    for track_rowid, vector, error in embedder.embed_many(items):
        done += 1
        if error is not None:
            log.warning(
                "Content embed failed for track %s (%s): %s",
                track_rowid, paths_by_id.get(track_rowid), error,
            )
            result.failed += 1
        else:
            library_content.upsert(
                conn,
                track_rowid,
                content_vec=pack_vector(vector),
                model=embedder.model_name,
            )
            result.embedded += 1
        if on_progress is not None:
            on_progress(done, len(items), f"{done}/{len(items)} embedded")

    recompute_stats(conn)
    return result


def recompute_stats(conn: sqlite3.Connection) -> None:
    """Recompute the corpus mean and the two percentile anchors from every
    stored vector.

    `recompute_stats` is the only writer of the values `reco.content_sim`
    validates -- that function raises on `hi <= lo`, so this one must never
    be able to persist such a row. Two guards make that airtight rather than
    merely probable:

    - A count floor (`MIN_STATS_TRACKS`): below it there literally are not
      enough pairwise similarities for two percentile ranks to land on
      different values (see the constant's comment for the n=2 algebra).
    - An outcome check on the computed anchors: the count floor is
      necessary but not sufficient -- a corpus that happens to be
      equidistant in every pair (or, degenerately, identical) still
      collapses `sim_lo` and `sim_hi` onto the same value however many
      vectors went in.

    Either guard failing leaves `content_stats` untouched, which is the same
    "leg unavailable" state as before any batch has run -- the caller
    renormalizes weights across the remaining legs rather than hitting a
    `ValueError` mid-ranking.
    """
    vectors = library_content.vectors(conn)
    if len(vectors) < MIN_STATS_TRACKS:
        return

    items = list(vectors.items())
    width = len(items[0][1])
    for track_rowid, vector in items[1:]:
        if len(vector) != width:
            raise ValueError(
                f"content vector for track {track_rowid} has width"
                f" {len(vector)}, expected {width} (from track"
                f" {items[0][0]}) -- a mixed-width corpus means a model"
                " change ran mid-migration"
            )

    values = [vector for _, vector in items]
    mean = [sum(vector[i] for vector in values) / len(values) for i in range(width)]

    sample = values
    if len(sample) > STATS_SAMPLE_TRACKS:
        sample = random.Random(STATS_SAMPLE_SEED).sample(sample, STATS_SAMPLE_TRACKS)
    centered = [[v - m for v, m in zip(vector, mean)] for vector in sample]

    from .reco import cosine_sim

    similarities = sorted(
        cosine_sim(centered[i], centered[j])
        for i in range(len(centered))
        for j in range(i + 1, len(centered))
    )

    sim_lo = _percentile(similarities, STATS_LOW_PERCENTILE)
    sim_hi = _percentile(similarities, STATS_HIGH_PERCENTILE)
    if sim_hi <= sim_lo:
        log.info(
            "Content corpus of %d vectors cannot yet calibrate the leg"
            " (sim_lo == sim_hi == %r) -- leaving it unavailable",
            len(vectors), sim_lo,
        )
        return

    library_content.replace_stats(
        conn,
        mean_vec=pack_vector(mean),
        sim_lo=sim_lo,
        sim_hi=sim_hi,
        track_count=len(vectors),
    )


def _percentile(sorted_values: list[float], percentile: int) -> float:
    """Nearest-rank percentile over an already-sorted list. Good enough for
    a calibration anchor, and it avoids pulling in a statistics dependency
    for two numbers."""
    index = round((percentile / 100) * (len(sorted_values) - 1))
    return sorted_values[max(0, min(len(sorted_values) - 1, index))]


def content_available(conn) -> bool:
    """Whether the content leg may score at all, for the whole library.

    The caller turns this into `rank()`'s `content_stats` argument: True means
    read the row and pass it, False means pass None. One boolean, one place,
    so nothing downstream can hold a different opinion about whether the leg
    is live.
    """
    coverage = library_content.coverage(conn)
    playable = coverage["playable"]
    counted = coverage["track_count"]
    if not playable or not counted:
        return False
    return counted / playable >= CONTENT_MIN_COVERAGE
