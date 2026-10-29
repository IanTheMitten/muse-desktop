"""The taste leg: a per-track vector learned from the owner's own listening
history, replacing `feedback_score` as a `rank()` leg (ADR 0003 / ticket 04).

Unlike `muse.embed`'s per-track, source-triggered batch, this is a
whole-library snapshot recomputed on a nightly schedule
(`muse.taste_retrain`) and replaced wholesale by
`muse.library.taste.replace_all` -- there is no `taste_vec IS NULL`
resumable work queue the way `embeddings.audio_vec` is one, because a
single retrain always recomputes every playable track at once.

The training input is co-occurrence, not content: two different tracks
played back to back, within `SESSION_GAP_SECONDS` of each other, count as
one interaction, tallied across the whole `plays` history.
`feedback_score`'s event weights already capture how much a listener liked
a track; this captures which tracks it keeps company with, which is exactly
what no per-track content signal (CLAP, Spotify features, lyrics, emotion)
can express (ADR 0003).

`TasteTrainer` is the same kind of seam `muse.embed`'s encoder protocols
are: a real `ImplicitAlsTrainer` (needs `numpy` and the `implicit` package,
imported only inside its own `__init__`) and a deterministic
`StubTasteTrainer` every test but the slow ones runs against.
"""

import array
import hashlib
import sqlite3
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from .embed import l2_normalize, pack_vector
from .library import taste as library_taste
from .library.plays import UNKNOWN, classify_play

TASTE_RETRAIN = "taste_retrain"

TASTE_DIM = 32

# A track needs at least this many classified play events (the same
# `ended_reason IS NOT NULL` population `feedback_score` sums over) before
# its taste vector is trusted -- below it, `taste_vec` stays NULL and
# `rank()`'s weight renormalizes across the remaining legs, the same
# null-leg contract `feature_vec` already has for instrumentals and
# post-freeze tracks.
MIN_PLAY_EVENTS = 3

# Two plays count as "played in sequence" (ADR 0003) if they are less than
# half an hour apart -- long enough to survive a bathroom-break pause,
# short enough that two plays either side of a full work day are not
# treated as one listening session.
SESSION_GAP_SECONDS = 1800

STUB_TASTE_MODEL = "stub-als"
IMPLICIT_ALS_MODEL = "implicit-als"


class TasteTrainer(Protocol):
    model_name: str

    def fit(
        self, interactions: list[tuple[int, int, float]], track_rowids: list[int]
    ) -> dict[int, list[float]]:
        """An L2-normalized, TASTE_DIM-wide vector for each of `track_rowids`
        the trainer can place -- a trainer may return fewer entries than it
        was given if it cannot place every track, but never more."""
        ...


def _hash_vector(seed: str, dim: int = TASTE_DIM) -> list[float]:
    """Deterministic: the same seed always produces the same vector. Same
    approach as `muse.embed._hash_vector`, reimplemented here so this
    module's only dependency on `muse.embed` is its public vector-packing
    helpers, not a private one.

    Quantized to float32 before returning -- the same precision
    `pack_vector` commits a trainer's output to on storage -- so a caller
    that fits once and a caller that fits-then-reads-back-from-storage see
    bit-identical numbers, rather than the float64 arithmetic above and its
    float32-rounded stored copy quietly drifting apart in the last few
    decimal digits."""
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
    return list(array.array("f", l2_normalize(values[:dim])))


class StubTasteTrainer:
    """No numpy, no `implicit`, no matrix to fit. Default trainer for tests
    and for any retrain that has not opted into the real one -- ignores
    `interactions` entirely, since the point is to exercise everything
    downstream of the co-occurrence math (the threshold gate, the wholesale
    replace, the health coverage stat) without depending on that math being
    correct."""

    model_name = STUB_TASTE_MODEL

    def fit(
        self, interactions: list[tuple[int, int, float]], track_rowids: list[int]
    ) -> dict[int, list[float]]:
        return {rowid: _hash_vector(f"track:{rowid}") for rowid in track_rowids}


class ImplicitAlsTrainer:
    """Implicit-feedback ALS over the track-track co-occurrence matrix.

    `implicit`'s `AlternatingLeastSquares` is built to factor a user-item
    matrix into user vectors and item vectors; fed a square, symmetric
    co-occurrence matrix instead, the two factor matrices play the same
    role, and `item_factors` is taken as each track's taste vector -- the
    standard way to get an item-item embedding out of a library built for
    user-item factorization. There being exactly one listener here (ADR
    0003) is what makes a per-user vector useless and a per-track one the
    right output.
    """

    model_name = IMPLICIT_ALS_MODEL

    def __init__(self, factors: int = TASTE_DIM, iterations: int = 15):
        from implicit.als import AlternatingLeastSquares

        self._model = AlternatingLeastSquares(
            factors=factors, iterations=iterations, random_state=0
        )

    def fit(
        self, interactions: list[tuple[int, int, float]], track_rowids: list[int]
    ) -> dict[int, list[float]]:
        import numpy as np
        from scipy.sparse import csr_matrix

        if not track_rowids:
            return {}

        index_of = {rowid: i for i, rowid in enumerate(track_rowids)}
        rows, cols, data = [], [], []
        for a, b, weight in interactions:
            if a not in index_of or b not in index_of:
                continue
            ia, ib = index_of[a], index_of[b]
            rows += [ia, ib]
            cols += [ib, ia]
            data += [weight, weight]

        n = len(track_rowids)
        matrix = csr_matrix((data, (rows, cols)), shape=(n, n), dtype=np.float32)
        self._model.fit(matrix)
        factors = self._model.item_factors
        return {
            rowid: l2_normalize(factors[index_of[rowid]].tolist())
            for rowid in track_rowids
        }


def _classified_plays(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every play with real evidence of intent -- `classify_play() !=
    UNKNOWN`, the exact test `muse.feedback._play_weight` already applies
    per-play -- reused here rather than reimplemented, same reason
    `feedback_score` reuses it: a `lost` closure (the phone died) or a span
    whose duration was never known is not a listen, in either direction of
    a co-occurrence pair."""
    rows = conn.execute(
        "SELECT track_rowid, started_at, ended_at, reached_ms, duration_ms,"
        " ended_reason FROM plays WHERE ended_reason IS NOT NULL"
        " ORDER BY started_at, id"
    ).fetchall()
    return [
        row for row in rows
        if classify_play(row["reached_ms"], row["duration_ms"], row["ended_reason"])
        != UNKNOWN
    ]


def play_event_counts(conn: sqlite3.Connection) -> dict[int, int]:
    """Classified play events per track -- the same population
    `feedback_score` sums over -- used to gate the per-track
    minimum-play-event threshold."""
    counts: dict[int, int] = {}
    for row in _classified_plays(conn):
        counts[row["track_rowid"]] = counts.get(row["track_rowid"], 0) + 1
    return counts


def build_interactions(conn: sqlite3.Connection) -> list[tuple[int, int, float]]:
    """Co-occurrence pairs: two different tracks played back to back, within
    `SESSION_GAP_SECONDS` of each other, tallied across the whole `plays`
    history. Weighted by how many times the pair occurred -- a pair the
    listener repeats often is a stronger signal than one that happened once.
    """
    classified = _classified_plays(conn)

    counts: Counter[tuple[int, int]] = Counter()
    for previous, current in zip(classified, classified[1:]):
        if previous["track_rowid"] == current["track_rowid"]:
            continue
        gap_seconds = (current["started_at"] - previous["ended_at"]) / 1000
        if gap_seconds < 0 or gap_seconds > SESSION_GAP_SECONDS:
            continue
        pair = tuple(sorted((previous["track_rowid"], current["track_rowid"])))
        counts[pair] += 1

    return [(a, b, float(weight)) for (a, b), weight in counts.items()]


@dataclass(slots=True)
class RetrainResult:
    considered: int = 0
    trained: int = 0
    skipped_below_threshold: int = 0


def run_retrain(
    conn: sqlite3.Connection, trainer: TasteTrainer, *, now: int | None = None
) -> RetrainResult:
    """Recompute the whole-library taste snapshot and replace it wholesale.

    Every playable track gets a row: a packed vector for one at or above
    `MIN_PLAY_EVENTS`, NULL for one below it -- the same null-leg contract
    `feature_vec` already has. `library.taste.replace_all` is what makes
    the replace atomic; a trainer that raises here never reaches it, so the
    previous snapshot is untouched either way.
    """
    playable = [
        row["track_rowid"]
        for row in conn.execute("SELECT track_rowid FROM tracks WHERE status = 'done'")
    ]
    counts = play_event_counts(conn)
    eligible = [rowid for rowid in playable if counts.get(rowid, 0) >= MIN_PLAY_EVENTS]

    vectors = trainer.fit(build_interactions(conn), eligible) if eligible else {}

    rows = [
        (
            rowid,
            pack_vector(vectors[rowid]) if rowid in vectors else None,
            trainer.model_name if rowid in vectors else None,
        )
        for rowid in playable
    ]
    library_taste.replace_all(conn, rows, now=now)

    return RetrainResult(
        considered=len(playable),
        trained=len(vectors),
        skipped_below_threshold=len(playable) - len(vectors),
    )
