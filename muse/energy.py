"""Pure energy-axis math: CLAP zero-shot energy from two prompt vectors.

See the spec Decision "How CLAP energy is computed and stored"
(docs/specs/2026-11-07-arc-radio-and-model-retirement.md). Imports only
`math`; the read path that loads vectors is the impure half.
"""

import math

CALM = "calm"
ENERGETIC = "energetic"
CALM_PROMPT = "a calm, relaxing song"
ENERGETIC_PROMPT = "an upbeat, high-energy song"
GATE_RHO = 0.5


def raw_energy(audio_vec: list[float], calm: list[float], energetic: list[float]) -> float:
    """dot(audio, energetic) - dot(audio, calm)."""
    return sum(a * e for a, e in zip(audio_vec, energetic)) - sum(
        a * c for a, c in zip(audio_vec, calm)
    )


def _ranks(values: list[float]) -> list[float]:
    """0-based ranks, ties share their average rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def to_unit(raw: dict[int, float]) -> dict[int, float]:
    """Rank-normalise raw energies onto [-1, 1]."""
    n = len(raw)
    if n == 0:
        return {}
    if n == 1:
        return {k: 0.0 for k in raw}
    keys = list(raw)
    ranks = _ranks([raw[k] for k in keys])
    return {k: 2 * r / (n - 1) - 1 for k, r in zip(keys, ranks)}


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rho; None when under two points or either side is constant."""
    if len(xs) < 2 or len(ys) < 2:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx == 0 or vy == 0:
        return None
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    return cov / math.sqrt(vx * vy)
