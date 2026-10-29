"""`DEFAULT_WEIGHTS`, `AUDIO_SPLIT`, and null-leg renormalization.

The null-leg contract is what makes an instrumental (no lyrics), a
post-freeze track (no Spotify features), and a barely-played track (no taste
vector) recommendable rather than excluded. It lives here as arithmetic, so
it can be tested without a database, a model, or a candidate.
"""

import pytest

from muse import reco

ALL_LEGS = ("w_audio", "w_feat", "w_lyr", "w_emo", "w_taste")


def test_default_weights_use_adr_0003_names():
    """ADR 0003 spells the five weights w_audio/w_feat/w_lyr/w_emo/w_taste.
    A rename here is a rename of the API's query parameters too."""
    assert set(reco.DEFAULT_WEIGHTS) == set(ALL_LEGS)


def test_default_weights_sum_to_one():
    assert reco.DEFAULT_WEIGHTS_SUM == pytest.approx(1.0)


def test_audio_split_sums_to_one():
    """The two audio legs divide one budget; they never add to it."""
    assert sum(reco.AUDIO_SPLIT.values()) == pytest.approx(1.0)


def test_all_legs_available_leaves_weights_unchanged(): 
    out = reco.renormalize(reco.DEFAULT_WEIGHTS, ALL_LEGS)
    assert out == pytest.approx(reco.DEFAULT_WEIGHTS)


def test_renormalized_weights_sum_to_one():
    out = reco.renormalize(reco.DEFAULT_WEIGHTS, ("w_audio", "w_feat"))
    assert sum(out.values()) == pytest.approx(1.0)


def test_absent_leg_is_not_in_the_result():
    out = reco.renormalize(reco.DEFAULT_WEIGHTS, ("w_audio", "w_feat"))
    assert set(out) == {"w_audio", "w_feat"}


def test_redistribution_is_proportional():
    """The surviving legs keep their ratios to each other. A leg dropping out
    must not change which of the remaining legs matters more."""
    weights = {"a": 0.5, "b": 0.3, "c": 0.2}
    out = reco.renormalize(weights, ("b", "c"))
    assert out["b"] / out["c"] == pytest.approx(0.3 / 0.2)
    assert out == pytest.approx({"b": 0.6, "c": 0.4})


def test_single_available_leg_takes_all_the_weight():
    out = reco.renormalize(reco.DEFAULT_WEIGHTS, ("w_lyr",))
    assert out == pytest.approx({"w_lyr": 1.0})


def test_no_available_legs_returns_empty_not_zero_division():
    """A candidate with no comparable leg at all. The documented outcome is an
    empty mapping -- the caller scores it 0.0 and says why in `reasons` --
    rather than a ZeroDivisionError from dividing by a zero total."""
    assert reco.renormalize(reco.DEFAULT_WEIGHTS, ()) == {}


def test_weights_of_zero_do_not_divide_by_zero():
    """A caller may legitimately zero a leg via a request override (story 38).
    Every weight zeroed is the same degenerate case as no legs available."""
    assert reco.renormalize({"a": 0.0, "b": 0.0}, ("a", "b")) == {}


def test_unknown_available_name_is_ignored():
    """`available` names legs, not weights; a name with no weight contributes
    nothing rather than a KeyError."""
    out = reco.renormalize({"a": 0.5, "b": 0.5}, ("a", "nonexistent"))
    assert out == pytest.approx({"a": 1.0})


def test_renormalize_does_not_mutate_its_input():
    weights = dict(reco.DEFAULT_WEIGHTS)
    reco.renormalize(weights, ("w_audio",))
    assert weights == reco.DEFAULT_WEIGHTS
