"""Offline evaluation of the recommender against real listening (PRD O8).

Every pair of consecutive plays in one session is a transition: the first
track is the seed, the second is what was actually played next. Each one is
replayed through the same retrieve-then-fuse path `recommend_for` runs
(`recommend.rank_candidates`), and the harness records where the real next
track landed. Ranking is over the full retrieval union rather than a sampled
negative set -- sampled metrics can reorder which of two recommenders is
better (Krichene & Rendle, KDD 2020) -- and a target the union never
retrieved is a miss, because that is what a listener would have got.

Read the numbers with three caveats, all printed with them:

- **Taste leaks.** The taste leg is implicit-ALS over play adjacency, trained
  on the very transitions replayed here. Every variant is therefore also
  reported with `w_taste = 0`; that column is the honest one.
- **Exposure bias.** A track radio queued was chosen by the current
  recommender. Plays tagged `radio`/`recommend` are left out of
  `accepted_user_chosen`. Radio picks restored after a reload were tagged
  `queue` until `player_state.radio_added` existed (ADR 0008), so a few
  older ones may still be counted as the listener's.
- **Only fusion is measured.** Ratings, freshness, and artist spacing are
  post-fusion constraints that depend on the moment of the request; they
  are left out so that a run is reproducible.

Run with `uv run python -m muse.reco_eval`. Read-only: it opens the library
with `mode=ro` and records no offers.
"""

import argparse
import sqlite3
import sys

from . import recommend
from .config import Config
from .library import plays as library_plays

# Two plays further apart than this are different sessions; what followed a
# long silence was not a response to the track before it.
SESSION_GAP_MS = 30 * 60 * 1000

K_VALUES = (1, 5, 10, 20)

# Sources set by the recommender rather than the listener.
RECOMMENDER_SOURCES = frozenset({"radio", "recommend"})


def transitions(conn: sqlite3.Connection, *,
                max_gap_ms: int = SESSION_GAP_MS) -> list[dict]:
    """Consecutive (seed, target) pairs from `plays`, oldest first."""
    out = []
    spans = library_plays.closed_spans(conn)
    for prev, nxt in zip(spans, spans[1:]):
        if prev["ended_reason"] in (None, "lost"):
            continue
        if nxt["started_at"] - prev["ended_at"] > max_gap_ms:
            continue
        if prev["track_rowid"] == nxt["track_rowid"]:
            continue
        out.append({
            "seed": prev["track_rowid"],
            "target": nxt["track_rowid"],
            "outcome": library_plays.classify_play(
                nxt["reached_ms"], nxt["duration_ms"], nxt["ended_reason"]),
            "source": nxt["source"],
        })
    return out


def _empty(k_values) -> dict:
    return {"n": 0, "retrieved": 0.0, "mrr": 0.0,
            **{f"hit@{k}": 0.0 for k in k_values}}


def _add(group: dict, rank: int | None, retrieved: bool, k_values) -> None:
    group["n"] += 1
    group["retrieved"] += retrieved
    group["mrr"] += 1.0 / rank if rank else 0.0
    for k in k_values:
        group[f"hit@{k}"] += 1.0 if rank and rank <= k else 0.0


def _mean(group: dict) -> dict:
    n = group["n"]
    return {key: (value if key == "n" or n == 0 else value / n)
            for key, value in group.items()}


def evaluate(
    vectors: dict[int, dict],
    trans: list[dict],
    *,
    k_values=K_VALUES,
    weights: dict[str, float] | None = None,
    content_stats: dict | None = None,
    calibration: dict | None = None,
    retrieved: dict[int, set[int]] | None = None,
) -> dict:
    """Hit rate at each k, MRR, and retrieval recall, per outcome group.

    `accepted` is a next track played through (`full_listen`); `skipped` is
    one abandoned early -- there a *lower* number is better. `unscorable`
    counts transitions where either track has no vectors.

    `retrieved` is an optional seed -> candidate-set cache, filled as it
    goes; pass the same dict to every variant so retrieval -- the costly
    part, and identical across variants -- runs once per seed.
    """
    retrieved = {} if retrieved is None else retrieved
    groups = {name: _empty(k_values)
              for name in ("accepted", "accepted_user_chosen", "skipped")}
    unscorable = 0
    for t in trans:
        seed, target = t["seed"], t["target"]
        if seed not in vectors or target not in vectors:
            unscorable += 1
            continue
        if seed not in retrieved:
            retrieved[seed] = recommend.retrieve(vectors, seed)
        ranked = recommend.rank_candidates(
            vectors, vectors[seed], retrieved[seed], weights=weights,
            content_stats=content_stats, calibration=calibration)
        order = [r["track_rowid"] for r in ranked]
        rank = order.index(target) + 1 if target in order else None
        found = rank is not None

        if t["outcome"] == library_plays.FULL_LISTEN:
            _add(groups["accepted"], rank, found, k_values)
            if t["source"] not in RECOMMENDER_SOURCES:
                _add(groups["accepted_user_chosen"], rank, found, k_values)
        elif t["outcome"] == library_plays.SKIP:
            _add(groups["skipped"], rank, found, k_values)

    result = {name: _mean(group) for name, group in groups.items()}
    result["unscorable"] = unscorable
    return result


def _no_taste(weights: dict[str, float]) -> dict[str, float]:
    return {**weights, "w_taste": 0.0}


def _no_lyrics(weights: dict[str, float]) -> dict[str, float]:
    return {**weights, "w_lyr": 0.0}


def run(conn: sqlite3.Connection) -> dict[str, dict]:
    """Every variant the report prints, keyed by name."""
    from . import reco

    vectors = recommend.load_vectors(conn)
    stats = recommend.content_stats_for(conn)
    calibration = recommend.calibration_for(conn, vectors, stats)
    trans = transitions(conn)
    retrieved: dict[int, set[int]] = {}
    variants = {
        "raw": dict(weights=reco.DEFAULT_WEIGHTS, calibration=None),
        "raw, no taste": dict(weights=_no_taste(reco.DEFAULT_WEIGHTS),
                              calibration=None),
        "calibrated": dict(weights=reco.DEFAULT_WEIGHTS,
                           calibration=calibration),
        "calibrated, no taste": dict(weights=_no_taste(reco.DEFAULT_WEIGHTS),
                                     calibration=calibration),
        # The arc engine's continuity (ADR 0009). Retrieval here still
        # includes the lyrics leg -- it is shared across variants -- so
        # this isolates the scoring cost of dropping it.
        "calibrated, no lyrics": dict(weights=_no_lyrics(reco.DEFAULT_WEIGHTS),
                                      calibration=calibration),
    }
    return {
        name: evaluate(vectors, trans, content_stats=stats,
                       retrieved=retrieved, **kwargs)
        for name, kwargs in variants.items()
    }


def _report(results: dict[str, dict], out=sys.stdout) -> None:
    metrics = ["n", "retrieved", "mrr"] + [f"hit@{k}" for k in K_VALUES]
    for group in ("accepted", "accepted_user_chosen", "skipped"):
        note = " (lower is better)" if group == "skipped" else ""
        print(f"\n{group}{note}", file=out)
        print(f"  {'variant':22}" + "".join(f"{m:>10}" for m in metrics),
              file=out)
        for name, result in results.items():
            row = result[group]
            cells = "".join(
                f"{row[m]:>10d}" if m == "n" else f"{row[m]:>10.3f}"
                for m in metrics)
            print(f"  {name:22}{cells}", file=out)
    first = next(iter(results.values()))
    print(f"\nunscorable transitions: {first['unscorable']}", file=out)
    print("taste-on rows leak: ALS trained on these same transitions.",
          file=out)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    path = Config.from_env().library_db
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        _report(run(conn))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
