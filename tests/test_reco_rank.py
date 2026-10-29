"""`rank()` -- the fusion seam, pure.

Story 44's learned scorer has to be a drop-in alternative implementation of
this function, which is only possible while it stays free of the database,
the config, and any model. The purity test below is the executable form of
that constraint; the rest is the fusion arithmetic and the `reasons` payload
story 37 needs.
"""

import inspect

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


STATS = {"mean_vec": [0.0, 0.0, 0.0], "sim_lo": -1.0, "sim_hi": 1.0}


# --- purity ---------------------------------------------------------------

def test_reco_module_imports_no_sqlite3():
    """The seam stays droppable only while it is pure. Asserted rather than
    left to a comment: a future edit that reaches for a Connection would
    otherwise pass every other test in this file."""
    source = inspect.getsource(reco)
    assert "import sqlite3" not in source
    assert "Connection" not in source


def test_rank_exposes_no_independent_audio_weights():
    """ADR 0004's first Binding clause at the signature level: there is one
    w_audio, and no way for a caller to weight CLAP and MERT separately."""
    params = inspect.signature(reco.rank).parameters
    assert "w_clap" not in params
    assert "w_content" not in params


# --- fusion ---------------------------------------------------------------

def test_returns_one_result_per_candidate():
    out = reco.rank(vecs(audio=[1.0, 0.0]), [candidate(1, audio=[1.0, 0.0])])
    assert len(out) == 1
    assert out[0]["track_rowid"] == 1


def test_sorted_by_score_descending():
    query = vecs(audio=[1.0, 0.0])
    out = reco.rank(query, [
        candidate(1, audio=[0.0, 1.0]),   # orthogonal
        candidate(2, audio=[1.0, 0.0]),   # identical
    ])
    assert [r["track_rowid"] for r in out] == [2, 1]


def test_identical_audio_scores_one_when_audio_is_the_only_leg():
    """One leg available means it carries the whole renormalized weight."""
    out = reco.rank(vecs(audio=[1.0, 0.0]), [candidate(1, audio=[1.0, 0.0])])
    assert out[0]["score"] == pytest.approx(1.0)


def test_candidate_with_no_legs_scores_zero_without_raising():
    """The degenerate case `renormalize` documents: nothing comparable at
    all. Scored 0.0 and explained, not excluded and not an exception."""
    out = reco.rank(vecs(audio=[1.0, 0.0]), [candidate(1)])
    assert out[0]["score"] == 0.0
    assert out[0]["reasons"]["available"] == []


def test_query_missing_a_leg_makes_that_leg_unavailable():
    """Both sides need a vector. A seed with no lyrics contributes no lyrics
    evidence even about a candidate that has some."""
    out = reco.rank(vecs(audio=[1.0, 0.0]), [candidate(1, audio=[1.0, 0.0],
                                                       lyrics=[1.0, 0.0])])
    assert "w_lyr" not in out[0]["reasons"]["available"]


def test_weights_are_overridable():
    """Story 38 -- ask for more like this sound."""
    query = vecs(audio=[1.0, 0.0], lyrics=[1.0, 0.0])
    cand = [candidate(1, audio=[0.0, 1.0], lyrics=[1.0, 0.0])]
    sound = reco.rank(query, cand, weights={"w_audio": 1.0, "w_lyr": 0.0})
    mood = reco.rank(query, cand, weights={"w_audio": 0.0, "w_lyr": 1.0})
    assert sound[0]["score"] == pytest.approx(0.0)
    assert mood[0]["score"] == pytest.approx(1.0)


# --- the content leg's gate ----------------------------------------------

def test_content_leg_is_dark_without_stats():
    """No `content_stats` row is the same thing as the leg being unavailable,
    and it is also how the caller expresses the coverage gate."""
    query = vecs(audio=[1.0, 0.0], content=[1.0, 0.0, 0.0])
    out = reco.rank(query, [candidate(1, audio=[1.0, 0.0],
                                      content=[1.0, 0.0, 0.0])])
    assert out[0]["reasons"]["content_used"] is False
    assert out[0]["reasons"]["null_legs"].get("content") == "content_gated"


def test_content_used_when_stats_are_supplied():
    query = vecs(audio=[1.0, 0.0], content=[1.0, 0.0, 0.0])
    out = reco.rank(query, [candidate(1, audio=[1.0, 0.0],
                                      content=[1.0, 0.0, 0.0])],
                    content_stats=STATS)
    assert out[0]["reasons"]["content_used"] is True


def test_audio_reverts_to_clap_when_content_is_missing():
    """ADR 0004 clause 2, observed through rank() rather than through
    audio_term directly."""
    query = vecs(audio=[1.0, 0.0], content=[1.0, 0.0, 0.0])
    out = reco.rank(query, [candidate(1, audio=[1.0, 0.0])],
                    content_stats=STATS)
    r = out[0]["reasons"]
    assert r["content_used"] is False
    assert r["audio_split"] == {"a_clap": 1.0, "a_content": 0.0}
    assert r["legs"]["w_audio"]["sim"] == pytest.approx(1.0)


# --- reasons --------------------------------------------------------------

def test_reasons_carries_per_leg_contributions():
    """Story 37 -- see *why* a track was recommended. Every available leg
    reports its raw similarity, its renormalized weight, and their product,
    because a weight argument nobody can inspect is unfalsifiable."""
    query = vecs(audio=[1.0, 0.0], lyrics=[1.0, 0.0])
    out = reco.rank(query, [candidate(1, audio=[1.0, 0.0],
                                      lyrics=[0.0, 1.0])])
    leg = out[0]["reasons"]["legs"]["w_audio"]
    assert set(leg) == {"sim", "weight", "contribution"}
    assert leg["contribution"] == pytest.approx(leg["sim"] * leg["weight"])


def test_reasons_contributions_sum_to_the_score():
    query = vecs(audio=[1.0, 0.0], lyrics=[1.0, 0.0], feature=[1.0, 0.0])
    out = reco.rank(query, [candidate(1, audio=[0.5, 0.5], lyrics=[1.0, 0.0],
                                      feature=[0.0, 1.0])])
    r = out[0]
    total = sum(leg["contribution"] for leg in r["reasons"]["legs"].values())
    assert total == pytest.approx(r["score"])


def test_reasons_names_why_each_leg_is_null():
    query = vecs(audio=[1.0, 0.0], lyrics=[1.0, 0.0], taste=[1.0, 0.0])
    out = reco.rank(query, [candidate(1, audio=[1.0, 0.0])])
    null = out[0]["reasons"]["null_legs"]
    assert null["w_lyr"] == "no_lyrics"
    assert null["w_taste"] == "below_play_threshold"


def test_emotion_leg_uses_emotion_sim_not_cosine():
    """ADR 0003: VA is a bounded plane where proximity is the relationship,
    not the angle from the origin. Two points just either side of zero are
    close; cosine calls them opposite."""
    query = vecs(emotion=(0.01, 0.0))
    out = reco.rank(query, [candidate(1, emotion=(-0.01, 0.0))])
    assert out[0]["score"] > 0.9


# --- the content leg has to actually do something -------------------------
#
# These exist because a deliberate ADR 0004 violation -- promoting content to
# its own weighted leg -- passed every other test in this file. `renormalize`
# ignores a name it has no weight for, so the promoted leg was silently
# dropped and content stopped affecting the score at all, with nothing red.
# Asserting the audio term's *shape* is not enough; something must assert
# that the second audio leg changes an answer.

def test_content_changes_the_score():
    """Two candidates identical on every leg but content. If content is
    plumbed in name only, these tie."""
    query = vecs(audio=[1.0, 0.0], content=[1.0, 0.0, 0.0])
    out = reco.rank(query, [
        candidate(1, audio=[1.0, 0.0], content=[1.0, 0.0, 0.0]),
        candidate(2, audio=[1.0, 0.0], content=[-1.0, 0.0, 0.0]),
    ], content_stats=STATS)
    scores = {r["track_rowid"]: r["score"] for r in out}
    assert scores[1] > scores[2]


def test_content_moves_the_score_by_its_share_of_the_audio_budget():
    """The quantity, not just the direction. With CLAP identical on both
    sides, swapping content from a perfect match to a perfect mismatch has to
    move the audio term by exactly a_content -- which is what "an internal
    split of one budget" means numerically."""
    query = vecs(audio=[1.0, 0.0], content=[1.0, 0.0, 0.0])
    match = reco.rank(query, [candidate(1, audio=[1.0, 0.0],
                                        content=[1.0, 0.0, 0.0])],
                      content_stats=STATS)[0]
    mismatch = reco.rank(query, [candidate(1, audio=[1.0, 0.0],
                                           content=[-1.0, 0.0, 0.0])],
                         content_stats=STATS)[0]
    moved = (match["reasons"]["legs"]["w_audio"]["sim"]
             - mismatch["reasons"]["legs"]["w_audio"]["sim"])
    assert moved == pytest.approx(reco.AUDIO_SPLIT["a_content"])


def test_no_leg_outside_the_weight_vector_can_score():
    """The mechanism that hid the violation: a leg name with no weight is
    dropped by `renormalize` rather than raising. That leniency is deliberate
    -- but it means every scoring leg must appear in the weight vector, so
    assert that directly instead of relying on a leg to notice its own
    absence."""
    query = vecs(audio=[1.0, 0.0], feature=[1.0, 0.0], lyrics=[1.0, 0.0],
                 emotion=(0.5, 0.5), taste=[1.0, 0.0], content=[1.0, 0.0, 0.0])
    out = reco.rank(query, [candidate(1, audio=[1.0, 0.0], feature=[1.0, 0.0],
                                      lyrics=[1.0, 0.0], emotion=(0.5, 0.5),
                                      taste=[1.0, 0.0],
                                      content=[1.0, 0.0, 0.0])],
                    content_stats=STATS)
    assert set(out[0]["reasons"]["legs"]) <= set(reco.DEFAULT_WEIGHTS)
    assert set(out[0]["reasons"]["available"]) == set(reco.DEFAULT_WEIGHTS)
