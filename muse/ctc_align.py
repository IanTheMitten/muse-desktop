"""Numpy CTC forced alignment (Viterbi), replacing a C++ extension we cannot install."""
import numpy as np


def forced_align(
    log_probs: np.ndarray, targets: list[int], blank: int = 0
) -> tuple[list[int], float]:
    """Best CTC path for `targets`; returns each target's first frame and the path score."""
    if not targets:
        raise ValueError("Empty transcript")
    if any(t == blank for t in targets):
        raise ValueError("Target equals blank")
    T = log_probs.shape[0]
    L = len(targets)
    S = 2 * L + 1
    ext = np.full(S, blank, dtype=np.int64)
    ext[1::2] = targets
    # skip from s-2 allowed only onto a non-blank that differs from ext[s-2]
    skip = np.zeros(S, dtype=bool)
    skip[2:] = (ext[2:] != blank) & (ext[2:] != ext[:-2])

    neg = -np.inf
    alpha = np.full(S, neg, dtype=np.float64)
    alpha[0] = log_probs[0, ext[0]]
    if S > 1:
        alpha[1] = log_probs[0, ext[1]]
    back = np.zeros((T, S), dtype=np.int8)
    for t in range(1, T):
        cand = np.full((3, S), neg, dtype=np.float64)
        cand[0] = alpha
        cand[1, 1:] = alpha[:-1]
        cand[2, 2:] = np.where(skip[2:], alpha[:-2], neg)
        choice = cand.argmax(axis=0)
        back[t] = choice
        alpha = cand[choice, np.arange(S)] + log_probs[t, ext]

    s = S - 1 if alpha[S - 1] >= alpha[S - 2] else S - 2
    score = float(alpha[s])
    if score == neg:
        raise ValueError("Too few frames for the text")
    states = [0] * T
    for t in range(T - 1, -1, -1):
        states[t] = s
        s -= int(back[t, s])
    frames = [-1] * L
    for t, st in enumerate(states):
        if st % 2 == 1 and frames[st // 2] < 0:
            frames[st // 2] = t
    return frames, score
