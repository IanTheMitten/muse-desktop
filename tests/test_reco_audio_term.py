"""ADR 0004's two Binding clauses, as executable assertions.

Both ways of violating them produce a well-formed score in [0, 1]. A test
that only checks the output is a number, or is in range, or merely differs
from the previous value, passes against both violations -- so every test here
asserts the *specific* quantity the clause fixes.
"""

import pytest

from muse import reco

W_AUDIO = reco.DEFAULT_WEIGHTS["w_audio"]
A_CLAP = reco.AUDIO_SPLIT["a_clap"]
A_CONTENT = reco.AUDIO_SPLIT["a_content"]


def test_both_legs_present_splits_the_budget():
    got = reco.audio_term(0.8, 0.4)
    assert got == pytest.approx(A_CLAP * 0.8 + A_CONTENT * 0.4)


def test_audio_term_is_bounded_by_its_inputs():
    """A weighted mean of the two legs, never a sum of them. If the split
    were treated as two independent weights this would exceed 1.0."""
    assert reco.audio_term(1.0, 1.0) == pytest.approx(1.0)


def test_content_absent_reverts_to_clap_exactly():
    """ADR 0004, Binding clause 2. Content's share goes to CLAP, so the audio
    term is clap_sim itself -- not a_clap * clap_sim, which is the number a
    naive implementation produces and which quietly shrinks audio's influence
    to 60% of w_audio on exactly the tracks where CLAP is the only audio
    signal available."""
    assert reco.audio_term(0.8, None) == pytest.approx(0.8)


def test_content_absent_keeps_the_audio_budget_whole():
    """The same clause stated as the property that motivates it: with content
    missing, the audio term is still worth exactly w_audio -- no more, and
    crucially no less."""
    contribution = W_AUDIO * reco.audio_term(0.8, None)
    assert contribution == pytest.approx(W_AUDIO * 0.8)


def test_content_absence_does_not_renormalize_outward():
    """The violation this clause exists to forbid: treating a missing content
    leg as an absent *leg* and redistributing its share across the other four.
    That is the null-leg contract applied one level too deep."""
    available = ("w_audio", "w_feat", "w_lyr", "w_emo", "w_taste")
    weights = reco.renormalize(reco.DEFAULT_WEIGHTS, available)
    assert weights["w_audio"] == pytest.approx(W_AUDIO)


def test_clap_absent_content_present_keeps_the_budget_whole():
    """The mirror case. Rare -- CLAP is embedded first -- but the audio term
    is worth w_audio whenever *any* audio evidence exists, so content supplies
    all of it rather than 40% of it."""
    assert reco.audio_term(None, 0.5) == pytest.approx(0.5)


def test_both_audio_legs_absent_drops_the_term():
    """Only here does audio become an absent leg. None is the signal to the
    caller to leave "w_audio" out of `available`, after which the ordinary
    null-leg renormalization applies to the other four."""
    assert reco.audio_term(None, None) is None


def test_both_absent_renormalizes_the_other_four():
    available = ("w_feat", "w_lyr", "w_emo", "w_taste")
    weights = reco.renormalize(reco.DEFAULT_WEIGHTS, available)
    assert sum(weights.values()) == pytest.approx(1.0)
    assert "w_audio" not in weights


def test_no_independent_clap_or_content_weight_exists():
    """ADR 0004, Binding clause 1: one audio term with an internal split, not
    two legs with independent weights. The split lives in AUDIO_SPLIT, which
    is not a member of the weight vector callers can override."""
    assert "w_clap" not in reco.DEFAULT_WEIGHTS
    assert "w_content" not in reco.DEFAULT_WEIGHTS
    assert not set(reco.AUDIO_SPLIT) & set(reco.DEFAULT_WEIGHTS)


def test_split_is_a_division_not_an_addition():
    """If a_clap and a_content ever stop summing to 1, every score silently
    changes scale and nothing else in the system notices."""
    assert A_CLAP + A_CONTENT == pytest.approx(1.0)
