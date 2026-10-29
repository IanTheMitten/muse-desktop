import itertools

import numpy as np
import pytest

from muse.ctc_align import forced_align


def test_basic_frames():
    lp = np.full((6, 3), np.log(0.05), dtype=np.float32)
    lp[:, 0] = np.log(0.9)
    for t in (1, 2):
        lp[t, 0] = np.log(0.05)
        lp[t, 1] = np.log(0.9)
    lp[4, 0] = np.log(0.05)
    lp[4, 2] = np.log(0.9)
    frames, _ = forced_align(lp, [1, 2])
    assert frames == [1, 4]


def test_repeat_needs_blank():
    lp = np.log(np.full((3, 2), 0.5, dtype=np.float32))
    frames, _ = forced_align(lp, [1, 1])
    assert frames == [0, 2]


def test_too_few_frames():
    lp = np.log(np.full((2, 2), 0.5, dtype=np.float32))
    with pytest.raises(ValueError):
        forced_align(lp, [1, 1])


def test_empty_and_blank_target():
    lp = np.log(np.full((3, 2), 0.5, dtype=np.float32))
    with pytest.raises(ValueError):
        forced_align(lp, [])
    with pytest.raises(ValueError):
        forced_align(lp, [0])


def _brute(lp, targets, blank=0):
    T = lp.shape[0]
    ext = [blank]
    for t in targets:
        ext += [t, blank]
    S = len(ext)
    best = -np.inf
    for path in itertools.product(range(S), repeat=T):
        if path[0] > 1 or path[-1] < S - 2:
            continue
        ok = True
        for a, b in zip(path, path[1:]):
            d = b - a
            if d not in (0, 1, 2) or (d == 2 and (ext[b] == blank or ext[b] == ext[a])):
                ok = False
                break
        if ok:
            best = max(best, sum(float(lp[t, ext[s]]) for t, s in enumerate(path)))
    return best


@pytest.mark.parametrize("seed", range(20))
def test_matches_brute_force(seed):
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(6, 4))
    lp = (x - np.log(np.exp(x).sum(axis=1, keepdims=True))).astype(np.float32)
    targets = [int(v) for v in rng.integers(1, 4, size=2)]
    _, score = forced_align(lp, targets)
    assert score == pytest.approx(_brute(lp, targets), abs=1e-5)
