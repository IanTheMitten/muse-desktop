"""`cosine_sim`: the per-leg similarity math `rank()` fuses. Pure, no
database, no model.
"""

from muse import reco


def test_cosine_sim_of_identical_vectors_is_one():
    assert reco.cosine_sim([1.0, 0.0], [1.0, 0.0]) == 1.0


def test_cosine_sim_of_opposite_vectors_is_minus_one():
    assert reco.cosine_sim([1.0, 0.0], [-1.0, 0.0]) == -1.0


def test_cosine_sim_of_a_zero_vector_is_zero():
    assert reco.cosine_sim([0.0, 0.0], [1.0, 0.0]) == 0.0
