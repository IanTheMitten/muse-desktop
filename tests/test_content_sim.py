"""`reco.content_sim` — pure, no database, no model, no config.

Corpus statistics arrive as arguments. That is not an aesthetic choice: it
is what keeps `muse/reco.py` testable with hand-built lists, which its own
module docstring commits it to.
"""

import pytest

from muse.reco import content_sim

MEAN = [0.0, 0.0]
ANCHORS = {"lo": -1.0, "hi": 1.0}


def test_identical_vectors_score_at_the_top_of_the_range():
    assert content_sim([1.0, 0.0], [1.0, 0.0], mean=MEAN, **ANCHORS) == 1.0


def test_opposite_vectors_score_at_the_bottom():
    assert content_sim([1.0, 0.0], [-1.0, 0.0], mean=MEAN, **ANCHORS) == 0.0


def test_orthogonal_vectors_land_midway():
    assert content_sim([1.0, 0.0], [0.0, 1.0], mean=MEAN, **ANCHORS) == 0.5


def test_the_mean_is_subtracted_before_comparing():
    """Two vectors that a raw cosine calls nearly identical are pulled apart
    once the component they share is removed — this is the whole point of
    the leg's normalization."""
    a, b = [10.0, 1.0], [10.0, -1.0]
    shared_mean = [10.0, 0.0]
    raw = content_sim(a, b, mean=[0.0, 0.0], lo=-1.0, hi=1.0)
    centered = content_sim(a, b, mean=shared_mean, lo=-1.0, hi=1.0)
    assert raw > 0.98
    assert centered == 0.0


def test_scores_are_clamped_into_the_unit_interval():
    """Anchors are percentiles, so real vectors do fall outside them — a
    score below `lo` or above `hi` is expected, not an error."""
    tight = {"lo": 0.4, "hi": 0.6}
    assert content_sim([1.0, 0.0], [1.0, 0.0], mean=MEAN, **tight) == 1.0
    assert content_sim([1.0, 0.0], [-1.0, 0.0], mean=MEAN, **tight) == 0.0


def test_a_degenerate_anchor_range_is_rejected():
    """`hi == lo` would divide by zero. A corpus that produced it is broken
    upstream, and returning a silent 0.5 would hide that."""
    with pytest.raises(ValueError):
        content_sim([1.0, 0.0], [0.0, 1.0], mean=MEAN, lo=0.5, hi=0.5)


def test_an_inverted_anchor_range_is_rejected():
    """If `lo` and `hi` ever arrive swapped, the clamp alone would not save
    it: it would return a well-formed but inverted score -- identical
    vectors at 0.0, opposite vectors at 1.0 -- with nothing raising. That is
    worse than an empty range, which at least crashes loudly."""
    with pytest.raises(ValueError):
        content_sim([1.0, 0.0], [1.0, 0.0], mean=MEAN, lo=0.6, hi=0.2)


def test_a_mean_of_the_wrong_length_is_rejected():
    """`mean` is a persisted statistic written by a separate batch job, not
    a vector from this call site -- if a model change ever widens the
    embeddings before the stats job catches up, `zip`'s silent truncation
    would otherwise return a plausible-looking score computed from only
    part of each vector."""
    with pytest.raises(ValueError):
        content_sim([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], mean=[0.0, 0.0], lo=-1.0, hi=1.0)


def test_a_zero_length_centered_vector_scores_neither_high_nor_low():
    """A track sitting exactly on the corpus mean has no direction left to
    compare. `cosine_sim` answers 0.0 for that, and the affine map takes it
    from there — no exception, no special case."""
    assert content_sim([1.0, 1.0], [2.0, 2.0], mean=[1.0, 1.0], **ANCHORS) == 0.5
