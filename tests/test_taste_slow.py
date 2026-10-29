"""`ImplicitAlsTrainer` against the real `implicit` package.

Skipped unless `implicit` is installed (the `taste` extra) and run
explicitly with `pytest -m slow` — never part of the default run, and never
a dependency of it, mirroring `test_embed_slow.py`'s convention for the
`embed` extra.
"""

import pytest

pytest.importorskip("implicit")

from muse.taste import TASTE_DIM, ImplicitAlsTrainer  # noqa: E402  (after the importorskip guard)

pytestmark = pytest.mark.slow


def test_implicit_als_trainer_produces_a_vector_per_track():
    trainer = ImplicitAlsTrainer(factors=TASTE_DIM, iterations=2)
    interactions = [(1, 2, 3.0), (2, 3, 1.0)]

    vectors = trainer.fit(interactions, [1, 2, 3])

    assert set(vectors) == {1, 2, 3}
    assert all(len(vector) == TASTE_DIM for vector in vectors.values())
