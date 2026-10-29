"""The MERT content leg: the embedder protocol, the batch that fills the
work queue, and the corpus statistics the leg is normalized by.

The real embedder runs in the ML worker (`muse/worker.py`, `muse/mert.py`),
not in this process; `StubContentEmbedder` is what the default test run uses.
It exercises the work queue, the per-track commit, the error path, and the
statistics recompute without any model -- the same rule every other heavy leg
in this codebase follows.
"""

import hashlib
import logging
import random
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
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
    """No worker, no model, no audio decode. Default embedder for tests
    and for any batch that has not opted into the real one -- deterministic
    per path, the same way `StubAudioEncoder` is."""

    model_name = STUB_CONTENT_MODEL

    def embed_many(
        self, items: list[tuple[int, str]]
    ) -> Iterator[tuple[int, list[float] | None, str | None]]:
        for track_rowid, path in items:
            yield track_rowid, _hash_vector(path), None


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
