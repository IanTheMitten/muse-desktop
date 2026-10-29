"""Offline arc harness: 12-song playlists from every dark tagged seed,
read-only (`docs/specs/2026-10-17-arc-engine.md`, "Measurement").

    uv run python -m muse.arc_eval

Reports how closely playlists follow their curve, how well they hold the
theme, how often the ladder relaxes, and the duplicate and exhausted rates.
Its first run is the baseline for tuning `muse/arc.py`'s constants.
"""

import argparse
import random
import sqlite3
import statistics
import sys
import time

from . import arc, arc_engine, request_spec
from .config import Config
from .library import queries as library_queries

MIN_START = 0.5
RANDOM_PAIRS = 3000
RUNGS = ("filler", "freshness", "artist_spacing")


def _mood(start_subject, d0, goal_subject, d1, **over):
    return request_spec.check({
        "version": 1, "kind": "mood",
        "start": {"subject": start_subject, "darkness": d0},
        "goal": {"subject": goal_subject, "darkness": d1},
        "shape": "gradual", "limits": "anything", "sound": "any", **over,
    })


PROMPT_SPECS = [
    ("breakup_to_moving_on", _mood(
        {"theme_broad:love": 0.9, "detail_love:breakup": 0.8}, 0.75,
        {"theme_broad:love": 0.9, "detail_love:moving_on": 0.8}, 0.25)),
    ("grief_lift", _mood(
        {"theme_broad:loss": 0.9, "detail_loss:fresh_grief": 0.7}, 0.9, None, 0.25)),
    ("anxiety_to_recovery", _mood(
        {"theme_broad:struggle": 0.9, "detail_struggle:anxiety": 0.8}, 0.75,
        {"theme_broad:struggle": 0.9, "detail_struggle:recovery": 0.8}, 0.25)),
    ("party_stay_energetic", _mood(
        {"theme_broad:celebration": 0.9, "detail_celebration:party_night": 0.7}, 0.0,
        None, None, sound="energetic")),
    ("lament_to_praise", _mood(
        {"theme_broad:faith": 0.9, "detail_faith_posture:lament_doubt": 0.8}, 0.75,
        {"theme_broad:faith": 0.9, "detail_faith_posture:adoration": 0.8}, 0.25)),
    ("late_night_drive", request_spec.check({
        "version": 1, "kind": "off_topic",
        "start": {"subject": {"setting:night": 0.8, "setting:road": 0.7}, "darkness": None},
        "goal": {"subject": None, "darkness": None},
        "shape": "gradual", "limits": "anything", "sound": "any",
    })),
]


def run(conn: sqlite3.Connection, *, n: int = 12, min_start: float = MIN_START,
        now: int | None = None, width: int = arc.BEAM_WIDTH,
        sample: int | None = None) -> dict:
    tags = arc_engine.tag_index(conn)
    names = library_queries.track_names(conn)
    artists = library_queries.artist_name_sets(conn)
    seeds = sorted(r for r, e in tags.items() if e["darkness"] > min_start)

    if sample is not None and sample < len(seeds):
        seeds = sorted(random.Random(0).sample(seeds, sample))

    latencies = []
    errors, sims, fillers, picks = [], [], 0, 0
    duplicates = exhausted = 0
    relaxed = {rung: 0 for rung in RUNGS}
    used_seeds = 0
    for seed in seeds:
        started = time.perf_counter()
        block = arc_engine.arc_block(conn, seed, n=n, now=now, record=False, width=width)
        latencies.append(time.perf_counter() - started)
        if block is None:
            continue
        used_seeds += 1
        exhausted += block["exhausted"]
        rows = [seed] + [r["track_rowid"] for r in block["results"]]
        if _has_duplicate(rows, names, artists, tags):
            duplicates += 1
        for result in block["results"]:
            picks += 1
            a = result["reasons"]["arc"]
            for rung in result["reasons"]["relaxed"]:
                relaxed[rung] += 1
            if a is None:
                continue
            if a["filler"]:
                fillers += 1
                continue
            errors.append(abs(a["darkness"] - a["target"]))
            sims.append(a["theme_sim"])

    subjects = [e["subject"] for e in tags.values()]
    rng = random.Random(0)
    pair_sims = [arc.theme_sim(*rng.sample(subjects, 2))
                 for _ in range(RANDOM_PAIRS)] if len(subjects) >= 2 else []

    def mean(xs):
        return statistics.fmean(xs) if xs else 0.0

    def p95(xs):
        if len(xs) < 2:
            return max(xs) if xs else 0.0
        return statistics.quantiles(xs, n=20)[18]

    return {
        "seeds": used_seeds,
        "picks": picks,
        "mean_target_error": mean(errors),
        "mean_theme_sim": mean(sims),
        "filler_share": fillers / picks if picks else 0.0,
        "duplicate_rate": duplicates / used_seeds if used_seeds else 0.0,
        "exhausted_rate": exhausted / used_seeds if used_seeds else 0.0,
        "relax_rate": {rung: count / picks if picks else 0.0
                       for rung, count in relaxed.items()},
        "latency_p50": statistics.median(latencies) if latencies else 0.0,
        "latency_p95": p95(latencies),
        "random_subject_sim_quantiles": (
            [round(q, 3) for q in statistics.quantiles(pair_sims, n=4)]
            if len(pair_sims) >= 2 else []),
    }


def prompt_sessions(conn: sqlite3.Connection, *, n: int = 12,
                    width: int = arc.BEAM_WIDTH) -> dict:
    out = {}
    for name, spec in PROMPT_SPECS:
        block = arc_engine.plan(conn, spec, n=n, record=False, width=width)
        if block is None:
            out[name] = {"results": 0, "exhausted": True}
            continue
        arcs = [r["reasons"]["arc"] for r in block["results"]]
        errors = [abs(a["darkness"] - a["target"]) for a in arcs
                  if a is not None and not a["filler"] and a["target"] is not None]
        out[name] = {
            "results": len(block["results"]),
            "exhausted": block["exhausted"],
            "mean_target_error": statistics.fmean(errors) if errors else 0.0,
            "theme_sim_by_position": [
                None if a is None or a["filler"] or a["theme_sim"] is None
                else round(a["theme_sim"], 3) for a in arcs],
        }
    return out


def _has_duplicate(rows, names, artists, tags) -> bool:
    seen_titles: dict[str, set[str]] = {}
    seen_hashes: set[str] = set()
    for rowid in rows:
        key = arc.title_key(names.get(rowid, ""))
        group = artists.get(rowid, frozenset())
        text_hash = tags.get(rowid, {}).get("text_hash")
        if arc.same_version(key, group, text_hash, seen_titles, seen_hashes):
            return True
        seen_titles.setdefault(key, set()).update(group)
        if text_hash:
            seen_hashes.add(text_hash)
    return False


def _fmt(v) -> str:
    return f"{v:.3f}" if isinstance(v, float) else str(v)


def report(result: dict, out=sys.stdout) -> None:
    for key, value in result.items():
        if isinstance(value, dict):
            print(f"{key}:", file=out)
            for sub, v in value.items():
                if isinstance(v, dict):
                    print(f"  {sub}:", file=out)
                    for k, x in v.items():
                        print(f"    {k:24}{_fmt(x)}", file=out)
                else:
                    print(f"  {sub:16}{_fmt(v):>8}", file=out)
        elif isinstance(value, list):
            print(f"{key:30}{value}", file=out)
        elif isinstance(value, float):
            print(f"{key:30}{value:8.3f}", file=out)
        else:
            print(f"{key:30}{value}", file=out)


def set_weights(text: str) -> None:
    values = [float(x) for x in text.split(",")]
    if len(values) != 4:
        raise SystemExit("--weights takes A,B,G,D")
    for name, value in zip(("ALPHA", "BETA", "GAMMA", "DELTA"), values):
        setattr(arc, name, value)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-n", type=int, default=12)
    parser.add_argument("--width", type=int, default=arc.BEAM_WIDTH)
    parser.add_argument("--sample", type=int, default=None)
    parser.add_argument("--weights", default=None,
                        help="ALPHA,BETA,GAMMA,DELTA for this run")
    args = parser.parse_args(argv)
    if args.weights:
        set_weights(args.weights)
    path = Config.from_env().library_db
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        report(run(conn, n=args.n, width=args.width, sample=args.sample))
        print("\nprompt sessions:")
        report(prompt_sessions(conn, n=args.n, width=args.width))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
