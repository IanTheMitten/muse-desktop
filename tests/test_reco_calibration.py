"""Per-leg calibration: every leg's similarity mapped through its own
empirical CDF before fusion (ADR 0008).

The point is a property, not a formula: once calibrated, a leg's similarity
between two *random* tracks sits at 0.5 whichever leg it is, so dropping a
null leg out of the renormalized average no longer moves the score by itself.
"""

import pytest

from muse import reco


def vecs(audio=None, feature=None, lyrics=None, emotion=None, taste=None,
         content=None):
    return {
        "audio_vec": audio, "feature_vec": feature, "lyrics_vec": lyrics,
        "emotion_vec": emotion, "taste_vec": taste, "content_vec": content,
    }


def candidate(rowid, **kw):
    return {"track_rowid": rowid, **vecs(**kw)}


# --- fit_calibration / calibrate --------------------------------------------

def test_fit_returns_sorted_knots_spanning_the_sample():
    sims = [0.9, 0.1, 0.5, 0.3, 0.7] * reco.MIN_CALIBRATION_PAIRS
    knots = reco.fit_calibration(sims)
    assert len(knots) == reco.CALIBRATION_KNOTS
    assert knots == sorted(knots)
    assert knots[0] == 0.1
    assert knots[-1] == 0.9


def test_fit_refuses_too_small_a_sample():
    """Below the floor the quantiles are noise; the caller must leave the
    leg uncalibrated rather than trust them."""
    assert reco.fit_calibration([0.5] * (reco.MIN_CALIBRATION_PAIRS - 1)) is None


def test_calibrate_maps_the_median_to_one_half():
    sims = [i / 100 for i in range(101)]
    knots = reco.fit_calibration(sims)
    assert reco.calibrate(0.5, knots) == pytest.approx(0.5)


def test_calibrate_clamps_outside_the_sample():
    knots = reco.fit_calibration([i / 100 for i in range(101)])
    assert reco.calibrate(-3.0, knots) == 0.0
    assert reco.calibrate(3.0, knots) == 1.0


def test_calibrate_is_monotone():
    knots = reco.fit_calibration([(i / 100) ** 3 for i in range(101)])
    outs = [reco.calibrate(x / 50, knots) for x in range(51)]
    assert outs == sorted(outs)


def test_calibrate_uniformizes_a_skewed_leg():
    """A leg whose raw scores bunch near the top (features, emotion on this
    library) comes out evenly spread: a random pair scores 0.5, not 0.86."""
    sims = [0.7 + 0.3 * (i / 1000) ** 0.3 for i in range(1001)]
    knots = reco.fit_calibration(sims)
    median_raw = sorted(sims)[500]
    assert reco.calibrate(median_raw, knots) == pytest.approx(0.5, abs=0.01)


def test_calibrate_handles_tied_knots_without_dividing_by_zero():
    sims = [0.0] * 50 + [1.0] * 50 + [0.5]
    knots = reco.fit_calibration(sims)
    assert 0.0 <= reco.calibrate(0.0, knots) <= 1.0
    assert 0.0 <= reco.calibrate(1.0, knots) <= 1.0


# --- rank() with calibration ------------------------------------------------

def _uniform(lo, hi):
    return reco.fit_calibration([lo + (hi - lo) * i / 200 for i in range(201)])


def test_rank_without_calibration_is_unchanged():
    query = vecs(audio=[1.0, 0.0])
    cands = [candidate(1, audio=[1.0, 0.0])]
    assert reco.rank(query, cands) == reco.rank(query, cands, calibration=None)


def test_calibrated_sim_is_what_the_leg_reports():
    cal = {"feature_vec": _uniform(0.0, 1.0)}
    query = vecs(feature=[1.0, 0.0])
    out = reco.rank(query, [candidate(1, feature=[0.6, 0.8])], calibration=cal)
    leg = out[0]["reasons"]["legs"]["w_feat"]
    assert leg["sim"] == pytest.approx(0.6, abs=0.01)
    assert out[0]["reasons"]["calibrated"] is True


def test_missing_leg_no_longer_shifts_a_typical_score():
    """The bug this exists to fix. Two candidates equally typical on every
    leg they share; one also has a feature leg whose raw scores bunch high.
    Uncalibrated, the one with features wins on that alone; calibrated, a
    typical feature score is worth exactly what the other legs are."""
    feat_cal = reco.fit_calibration(
        [0.8 + 0.2 * i / 200 for i in range(201)])  # raw median 0.9
    lyr_cal = reco.fit_calibration(
        [0.0 + 1.0 * i / 200 for i in range(201)])  # raw median 0.5
    cal = {"feature_vec": feat_cal, "lyrics_vec": lyr_cal}

    q_lyr = [1.0, 0.0]
    typical_lyr = [0.5, 0.8660254]       # cosine 0.5 -> lyrics median
    q_feat = [1.0, 0.0]
    typical_feat = [0.9, 0.43588989]     # cosine 0.9 -> feature median
    query = vecs(lyrics=q_lyr, feature=q_feat)
    with_feat = candidate(1, lyrics=typical_lyr, feature=typical_feat)
    without = candidate(2, lyrics=typical_lyr)

    raw = {r["track_rowid"]: r["score"] for r in reco.rank(query, [with_feat, without])}
    assert raw[1] > raw[2] + 0.05

    fused = {r["track_rowid"]: r["score"]
             for r in reco.rank(query, [with_feat, without], calibration=cal)}
    assert fused[1] == pytest.approx(fused[2], abs=0.01)


def test_leg_without_knots_falls_back_to_raw():
    cal = {"lyrics_vec": _uniform(0.0, 1.0)}
    query = vecs(feature=[1.0, 0.0])
    out = reco.rank(query, [candidate(1, feature=[0.6, 0.8])], calibration=cal)
    assert out[0]["reasons"]["legs"]["w_feat"]["sim"] == pytest.approx(0.6)


def test_audio_legs_are_calibrated_before_the_split():
    """ADR 0004's one-budget audio term combines CLAP and content after each
    has been put on the common scale, not before."""
    cal = {"audio_vec": _uniform(0.5, 1.0)}
    query = vecs(audio=[1.0, 0.0])
    # raw cosine 0.75 -> midpoint of [0.5, 1.0] -> 0.5 calibrated
    cand = candidate(1, audio=[0.75, 0.66143783])
    out = reco.rank(query, [cand], calibration=cal)
    assert out[0]["reasons"]["legs"]["w_audio"]["sim"] == pytest.approx(0.5, abs=0.01)
