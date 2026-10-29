"""Similarity functions the recommender's legs are built from.

`rank()` itself (ticket 05) fuses these into one score; this module is just
the per-leg math, pure and dependency-free so it is testable without a
database or a model.

Two legs deviate from a plain cosine, in different ways. `emotion_sim` is
the odd one out on *metric*: VA is a bounded plane where proximity of two
points is the meaningful relationship (Russell's circumplex model), not the
angle between them from the origin -- two points can point in opposite
directions from (0, 0) while sitting right next to each other (e.g. barely
positive vs. barely negative valence at the same low arousal), which cosine
would score as maximally dissimilar and Euclidean distance correctly scores
as close. `content_sim` keeps cosine as the metric but deviates on either
side of it: it corrects the input vectors (mean-centering, to undo MERT's
anisotropy) before the cosine, and rescales the result (through measured
corpus percentiles) after. Every other leg (CLAP, feature, lyrics) compares
high-dimensional L2-normalized vectors by plain cosine.
"""

import math

# The farthest two points on the [-1, 1] x [-1, 1] VA plane can be from each
# other -- opposite corners -- used to scale a raw distance into [0, 1].
_MAX_VA_DISTANCE = math.dist((-1.0, -1.0), (1.0, 1.0))


# --- Fusion weights -------------------------------------------------------
#
# Five weighted terms, spelled as ADR 0003 spells them. These numbers are
# *traceable, not measured*. They are derived from PRD §553's four-leg vector
# (w_clap 0.35, w_feat 0.15, w_lyr 0.30, w_fb 0.20) by two substitutions:
# w_clap becomes w_audio and now covers both audio legs (ADR 0004), and w_fb
# becomes w_taste, inherited wholesale because ADR 0003 replaced the
# feedback_score scalar with the taste embedding. w_emo is new and funded out
# of w_lyr, since emotion partly derives from the same lyrics.
#
# Nothing has tested whether they are good. The offline harness now exists
# (`muse/reco_eval.py`, O8) and ADR 0008 records its baseline, but at 126
# played-through transitions one transition is worth 0.008 of hit@k, so
# tuning five weights against it would fit noise. Do not adjust these
# without a run that beats the recorded baseline by more than that.
DEFAULT_WEIGHTS: dict[str, float] = {
    "w_audio": 0.35,
    "w_feat": 0.15,
    "w_lyr": 0.25,
    "w_emo": 0.05,
    "w_taste": 0.20,
}

DEFAULT_WEIGHTS_SUM = sum(DEFAULT_WEIGHTS.values())

# How the one audio budget divides between the two audio legs. This is a
# split of `w_audio`, never an addition to it -- see `audio_term` and
# ADR 0004's first Binding clause. CLAP takes the larger share because it is
# the validated leg; MERT content is the new one.
AUDIO_SPLIT: dict[str, float] = {"a_clap": 0.60, "a_content": 0.40}


def renormalize(
    weights: dict[str, float], available
) -> dict[str, float]:
    """The weights over `available`, rescaled to sum to 1.

    This is the null-leg contract as arithmetic. A leg that is null for a
    candidate drops out and the rest scale up proportionally, which is what
    keeps an instrumental (no lyrics), a post-freeze track (no Spotify
    features), and a track below `taste.MIN_PLAY_EVENTS` (no taste vector)
    scoreable on the evidence that does exist instead of being excluded for
    the evidence that does not.

    Proportional, so a leg dropping out never changes which of the survivors
    matters more -- only how much the whole set is worth.

    Returns an **empty mapping** when nothing survives: no available legs, or
    every surviving weight zeroed by a request override (story 38). That is
    the documented degenerate case rather than a ZeroDivisionError, and the
    caller scores such a candidate 0.0 and records why in `reasons`. A name in
    `available` with no matching weight contributes nothing rather than
    raising, because `available` names legs and this mapping names weights;
    the two are allowed to drift.
    """
    kept = {name: weights[name] for name in available if name in weights}
    total = sum(kept.values())
    if total <= 0.0:
        return {}
    return {name: weight / total for name, weight in kept.items()}


def audio_term(
    clap_sim: float | None,
    content_sim: float | None,
    *,
    split: dict[str, float] | None = None,
) -> float | None:
    """The one audio-content term, worth `w_audio` whenever any audio
    evidence exists. Returns None only when neither audio leg does.

    ADR 0004 binds this shape and both of its clauses are easy to violate
    while still returning a plausible number in [0, 1]:

    *One term, not two.* CLAP and MERT divide a single budget internally
    rather than carrying independent weights. `split` is a division of
    `w_audio`, never an addition to it, which is why its two shares sum to 1
    and why neither share appears in `DEFAULT_WEIGHTS` for a caller to
    override.

    *Content's share reverts to CLAP when content is missing.* Not
    `a_clap * clap_sim` -- that is the natural-looking expression, and it
    shrinks the audio term to 60% of `w_audio` on exactly the tracks where
    CLAP is the only audio signal there is, undoing the first clause. Not
    outward renormalization across the other four legs either, for the same
    reason: audio would end up worth less than `w_audio` precisely where it
    should be worth all of it. The term stays whole with one leg supplying
    it.

    The mirror case -- CLAP absent, content present -- is not something the
    embed order produces today, since `audio_vec` is written before any
    content batch runs. It is handled symmetrically anyway: the clause's
    reasoning is about the audio budget staying whole, and that reasoning
    does not depend on which leg is the survivor.
    """
    shares = split if split is not None else AUDIO_SPLIT
    if clap_sim is None and content_sim is None:
        return None
    if content_sim is None:
        return clap_sim
    if clap_sim is None:
        return content_sim
    return shares["a_clap"] * clap_sim + shares["a_content"] * content_sim


def cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity, in [-1, 1]. 0.0 for a zero vector on either side --
    there is no direction to compare, so "no similarity" is the honest
    answer, not an error."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def emotion_sim(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Euclidean distance between two VA points, converted to a [0, 1]
    similarity -- not cosine (see module docstring). 1.0 for identical
    points, 0.0 for the two farthest-apart points the plane allows."""
    distance = math.dist(a, b)
    return max(0.0, 1.0 - distance / _MAX_VA_DISTANCE)


def content_sim(
    a: list[float],
    b: list[float],
    *,
    mean: list[float],
    lo: float,
    hi: float,
) -> float:
    """Similarity between two raw MERT content vectors, in [0, 1].

    Unlike CLAP's `audio_vec`, MERT's output is stored raw and is strongly
    anisotropic: every vector shares a large mean component, which drags
    every raw cosine into a narrow band (0.91-0.98 on this library). A leg
    with a 0.07-wide range contributes nothing to a fusion sum whose other
    legs span most of [0, 1], no matter what weight it carries.

    So `mean` -- the corpus mean vector -- is subtracted from both sides
    before the cosine, which removes the shared component rather than
    compensating for it afterwards, and the result is rescaled through `lo`
    and `hi`, two measured percentiles of this corpus's own centered-cosine
    distribution. Both come from `library.content.stats`, passed in by the
    caller: this module stays pure (see the module docstring), so it never
    reads them itself.

    Scores outside the anchors are clamped, not an error -- percentile
    anchors are meant to be exceeded by the tails.

    Two things raise instead of degrading, because both mean the *stored
    statistics* are corrupt rather than merely absent -- the leg's contract
    is that it is absent (and left out of the fusion sum by the caller) when
    the corpus has no statistics row at all, which is a normal, handled
    state; a `ValueError` here means the row exists but cannot be trusted.
    A `hi <= lo` range is not just degenerate (division by zero) but, if
    `hi < lo`, would silently score identical vectors at 0.0 and opposite
    ones at 1.0 -- the clamp does not catch an inverted range, it just
    clamps the inverted value, so this is checked explicitly rather than
    left to blow up arithmetically. `mean` is also length-checked against
    `a` and `b`: unlike `cosine_sim`'s `a`/`b`, which are two live vectors
    from the same call site and agree by construction, `mean` is a
    separately persisted statistic written by its own batch job on its own
    cadence -- exactly the seam where a model change can widen the stored
    vectors before the stats job catches up, and `zip`'s silent truncation
    would turn that drift into a plausible-looking wrong score instead of
    an error.
    """
    if hi <= lo:
        raise ValueError("content_sim needs a non-degenerate anchor range")
    if not (len(a) == len(b) == len(mean)):
        raise ValueError(
            "content_sim needs equal-length vectors, got"
            f" len(a)={len(a)}, len(b)={len(b)}, len(mean)={len(mean)}"
        )
    centered_a = [x - m for x, m in zip(a, mean)]
    centered_b = [y - m for y, m in zip(b, mean)]
    scaled = (cosine_sim(centered_a, centered_b) - lo) / (hi - lo)
    return min(1.0, max(0.0, scaled))


# --- Calibration ------------------------------------------------------------
#
# ADR 0008. Each leg's raw similarity lives on its own scale: on this library
# a random pair scores ~0.87 on features and emotion, ~0.44 on lyrics, ~0.0
# on taste. `rank()` averages whichever legs a candidate has, so with raw
# scores the *set* of legs present moved the score by itself -- a post-freeze
# track lost a leg that hands everyone 0.87, an instrumental shed one that
# hands everyone 0.44. Mapping every leg through its own empirical CDF, fitted
# over random track pairs, puts a typical pair at 0.5 on every leg, so a null
# leg dropping out of the renormalized average no longer biases the result.

# Quantile knots stored per leg: percentiles 0, 1, ..., 100.
CALIBRATION_KNOTS = 101

# Fewer pairs than this and the percentiles are sampling noise; the leg is
# left on its raw scale instead.
MIN_CALIBRATION_PAIRS = 100


def fit_calibration(sims: list[float]) -> list[float] | None:
    """Quantile knots of a leg's similarity distribution, or None when the
    sample is too small to trust."""
    if len(sims) < MIN_CALIBRATION_PAIRS:
        return None
    ordered = sorted(sims)
    last = len(ordered) - 1
    steps = CALIBRATION_KNOTS - 1
    return [ordered[round(i * last / steps)] for i in range(CALIBRATION_KNOTS)]


def calibrate(sim: float, knots: list[float]) -> float:
    """`sim`'s position in the fitted distribution, in [0, 1], interpolated
    linearly between knots. A value equal to a run of tied knots maps to the
    middle of that run, so a leg with many identical scores does not divide
    by zero or snap to one end."""
    steps = len(knots) - 1
    if sim < knots[0]:
        return 0.0
    if sim > knots[-1]:
        return 1.0
    tied = [i for i, k in enumerate(knots) if k == sim]
    if tied:
        return (tied[0] + tied[-1]) / 2 / steps

    # Strictly between two knots: find the bracketing pair and interpolate.
    lo, hi = 0, steps
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if knots[mid] < sim:
            lo = mid
        else:
            hi = mid
    frac = (sim - knots[lo]) / (knots[hi] - knots[lo])
    return (lo + frac) / steps


def _calibrated(sim: float | None, key: str, calibration: dict | None) -> float | None:
    if sim is None or not calibration or calibration.get(key) is None:
        return sim
    return calibrate(sim, calibration[key])


# Which query/candidate key each weight compares, for the four legs that are
# a plain cosine or a plain distance. Audio is absent because it is two legs
# behind one weight (see `audio_term`).
_LEG_VECTORS: dict[str, str] = {
    "w_feat": "feature_vec",
    "w_lyr": "lyrics_vec",
    "w_emo": "emotion_vec",
    "w_taste": "taste_vec",
}

# Why a leg is null, in the vocabulary the UI shows. These name steady states
# of the library, not failures: an instrumental has no lyrics, a post-freeze
# track has no Spotify features, and a track below `taste.MIN_PLAY_EVENTS`
# has no taste vector because three plays is not yet a taste.
_NULL_REASONS: dict[str, str] = {
    "w_feat": "no_features",
    "w_lyr": "no_lyrics",
    "w_emo": "no_emotion",
    "w_taste": "below_play_threshold",
}


def _leg_sim(name: str, query_vec, candidate_vec) -> float | None:
    """One leg's similarity, or None when either side has no vector."""
    if query_vec is None or candidate_vec is None:
        return None
    if name == "w_emo":
        return emotion_sim(tuple(query_vec), tuple(candidate_vec))
    return cosine_sim(list(query_vec), list(candidate_vec))


def rank(
    query: dict,
    candidates: list[dict],
    *,
    weights: dict[str, float] | None = None,
    content_stats: dict | None = None,
    split: dict[str, float] | None = None,
    calibration: dict[str, list[float] | None] | None = None,
) -> list[dict]:
    """Fuse the legs into one score per candidate, best first.

    `calibration` maps a vector key (`audio_vec`, `content_vec`, ...) to
    that leg's quantile knots from `fit_calibration`. Each leg present in it
    is scored through its CDF before fusion (ADR 0008); a leg absent from it,
    or the whole argument being None, keeps its raw similarity.

    `query` and each candidate are mappings of vectors -- `audio_vec`,
    `feature_vec`, `lyrics_vec`, `emotion_vec`, `taste_vec`, `content_vec` --
    already loaded by the caller, along with `content_stats` already read.
    Nothing here opens or knows about a database. That is what lets story
    44's learned scorer replace this function without touching anything else,
    and `tests/test_reco_rank.py` asserts it rather than trusting it.

    **`content_stats is None` is how the caller expresses the content leg's
    coverage gate.** Absent statistics and a gated leg are the same condition
    from this function's point of view -- there is no normalization to
    compare through -- so the caller withholding statistics is the whole
    mechanism, and no separate flag exists to get out of step with it.

    Radio's rules are deliberately not here. Artist spacing, the freshness
    boost, and the ratings floor/ceiling all apply *after* fusion, in
    `muse.recommend`, because they are constraints rather than evidence and
    must not be outvotable by a high similarity. `reasons` therefore carries
    the fusion story only; the caller extends it with what it added.

    PRD §553's signature had a `feedback_scores` argument. ADR 0003 replaced
    that scalar with the taste embedding, which arrives as `taste_vec` like
    any other leg -- reinstating it here would restore exactly the two
    event-weighting schemes that ADR folded into one.
    """
    weights = DEFAULT_WEIGHTS if weights is None else weights
    shares = split if split is not None else AUDIO_SPLIT
    results = []

    for candidate in candidates:
        sims: dict[str, float] = {}
        null_legs: dict[str, str] = {}

        clap = _calibrated(
            _leg_sim("w_audio", query.get("audio_vec"), candidate.get("audio_vec")),
            "audio_vec", calibration,
        )

        content = None
        if content_stats is None:
            null_legs["content"] = "content_gated"
        else:
            query_content = query.get("content_vec")
            candidate_content = candidate.get("content_vec")
            if query_content is None or candidate_content is None:
                null_legs["content"] = "no_content_vec"
            else:
                content = content_sim(
                    list(query_content), list(candidate_content),
                    mean=content_stats["mean_vec"],
                    lo=content_stats["sim_lo"], hi=content_stats["sim_hi"],
                )
                content = _calibrated(content, "content_vec", calibration)

        term = audio_term(clap, content, split=shares)
        if term is None:
            null_legs["w_audio"] = "no_audio"
            audio_split = None
        else:
            sims["w_audio"] = term
            # Report the split actually applied, so a reverted content share
            # is visible in the payload rather than inferred from an absence.
            if content is None:
                audio_split = {"a_clap": 1.0, "a_content": 0.0}
            elif clap is None:
                audio_split = {"a_clap": 0.0, "a_content": 1.0}
            else:
                audio_split = dict(shares)

        for name, key in _LEG_VECTORS.items():
            sim = _calibrated(_leg_sim(name, query.get(key), candidate.get(key)),
                              key, calibration)
            if sim is None:
                null_legs[name] = _NULL_REASONS[name]
            else:
                sims[name] = sim

        applied = renormalize(weights, sims.keys())
        legs = {
            name: {
                "sim": sims[name],
                "weight": applied[name],
                "contribution": sims[name] * applied[name],
            }
            for name in applied
        }
        score = sum(leg["contribution"] for leg in legs.values())

        results.append({
            "track_rowid": candidate["track_rowid"],
            "score": score,
            "reasons": {
                "legs": legs,
                "available": list(applied),
                "null_legs": null_legs,
                "audio_split": audio_split,
                "content_used": content is not None,
                "calibrated": bool(calibration),
            },
        })

    results.sort(key=lambda row: row["score"], reverse=True)
    return results
