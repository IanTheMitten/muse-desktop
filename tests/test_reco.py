"""`cosine_sim` and `emotion_sim`: the per-leg similarity math `rank()`
(ticket 05) will fuse. Pure, no database, no model.

ADR 0003 is explicit that `emotion_sim` must be Euclidean-distance-based,
not cosine, because VA is a bounded plane where proximity of two points is
the meaningful relationship (the circumplex model) -- unlike the other legs'
high-dimensional, L2-normalized, angle-compared vectors. These tests exist
to prove that distinction is real, not just asserted in a docstring.
"""

from muse import reco


def test_cosine_sim_of_identical_vectors_is_one():
    assert reco.cosine_sim([1.0, 0.0], [1.0, 0.0]) == 1.0


def test_cosine_sim_of_opposite_vectors_is_minus_one():
    assert reco.cosine_sim([1.0, 0.0], [-1.0, 0.0]) == -1.0


def test_cosine_sim_of_a_zero_vector_is_zero():
    assert reco.cosine_sim([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_emotion_sim_of_identical_points_is_one():
    assert reco.emotion_sim((0.3, -0.2), (0.3, -0.2)) == 1.0


def test_emotion_sim_of_the_farthest_possible_points_is_zero():
    assert reco.emotion_sim((-1.0, -1.0), (1.0, 1.0)) == 0.0


def test_emotion_sim_diverges_from_cosine_for_same_direction_different_magnitude():
    """Two points on the same ray from the origin: cosine says "identical
    direction", maximally similar. Euclidean distance correctly says they
    are far apart on the VA plane -- a faint mood and an intense one are not
    the same mood."""
    near_origin = (0.1, 0.1)
    far_out = (0.9, 0.9)

    assert reco.cosine_sim(list(near_origin), list(far_out)) == 1.0
    assert reco.emotion_sim(near_origin, far_out) < 0.7


def test_emotion_sim_diverges_from_cosine_for_opposite_direction_same_neighbourhood():
    """Two points that sit right next to each other near the origin but on
    opposite sides of it: cosine calls this maximal dissimilarity (opposite
    vectors). Euclidean distance correctly calls it two very similar, mildly
    ambivalent moods."""
    barely_positive = (0.05, 0.05)
    barely_negative = (-0.05, -0.05)

    assert reco.cosine_sim(list(barely_positive), list(barely_negative)) < -0.999
    assert reco.emotion_sim(barely_positive, barely_negative) > 0.9
