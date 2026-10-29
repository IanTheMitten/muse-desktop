"""`muse/arc_engine.py` -- the arc engine's impure half
(`docs/specs/2026-10-17-arc-engine.md` D5-D9, ADR 0009).

Loads what `muse/arc.py` may not touch -- vectors, tag scores, ratings,
offers, names -- and plans with a beam search over `arc.pick_cost`. A
playlist is one call; a radio is the same call repeated with `pos` moved
on, planning a receding horizon and returning only the first picks. The
client owns the queue, so nothing about a session is stored here.
"""

import sqlite3
import time

from . import arc, lyric_tags, reco, recommend, request_spec
from .library import embeddings as library_embeddings
from .library import lyric_tags as library_lyric_tags
from .library import offers as library_offers
from .library import queries as library_queries
from .library import ratings as library_ratings

# Theme comes from tags, so the lyrics leg is out of both retrieval and
# scoring here (D3). `/api/recommend` keeps it.
ARC_LEGS = tuple(leg for leg in recommend.RETRIEVAL_LEGS if leg != "lyrics_vec")
ARC_WEIGHTS = {**reco.DEFAULT_WEIGHTS, "w_lyr": 0.0}

UNTAGGED_SEED = "The seed has no lyric tags, so there is no arc to follow."

_tag_cache: dict = {}


def clear_tag_cache() -> None:
    _tag_cache.clear()


def tag_index(conn: sqlite3.Connection) -> dict[int, dict]:
    """`{track_rowid: {darkness, heavy, glorifying, subject, text_hash}}` for
    every linked set with tags. A `no_lines` set is absent, which makes its
    track filler, same as a track with no set.

    Reading and deriving costs ~1.5 s on the live library, so the result is
    cached on `lyric_tags.stamp()` plus the database path (two libraries can
    share a stamp)."""
    database = conn.execute("PRAGMA database_list").fetchone()[2]
    key = (database, library_lyric_tags.stamp(conn))
    cached = _tag_cache.get("entry")
    if cached is not None and cached[0] == key:
        return cached[1]
    hashes = library_lyric_tags.text_hashes(conn)
    index: dict[int, dict] = {}
    for rowid, tags in library_lyric_tags.read(conn).items():
        scores = lyric_tags.scores(tags)
        if scores is None:
            continue
        index[rowid] = {
            "darkness": scores["mood_darkness"],
            "heavy": scores["heavy_content"],
            "glorifying": scores["glorifying"],
            "subject": arc.subject_vector(lyric_tags.tag_vector(tags)),
            "text_hash": hashes.get(rowid),
        }
    _tag_cache["entry"] = (key, index)
    return index


def arc_block(
    conn: sqlite3.Connection,
    seed: int,
    *,
    mode: str = "playlist",
    n: int = 12,
    pos: int = 0,
    lift: bool = True,
    exclude: set[int] | None = None,
    recent: list[int] | None = None,
    now: int | None = None,
    record: bool = True,
    width: int = arc.BEAM_WIDTH,
) -> dict | None:
    """`n` picks along the seed's arc, or None when the seed has no vectors.

    `pos` is how many arc picks the session has already placed; `recent` is
    most-recent-first and its head is the previous track when `pos > 0`.
    `record=False` skips writing offers, for the read-only harness.

    Returns `{results, curve, arc, reason, exhausted}`; see spec D7.
    """
    return plan(
        conn, request_spec.seed_spec(seed, lift=lift), mode=mode, n=n, pos=pos,
        exclude=exclude, recent=recent, now=now, record=record, width=width,
    )


def plan(
    conn: sqlite3.Connection,
    spec: dict,
    *,
    mode: str = "playlist",
    n: int = 12,
    pos: int = 0,
    exclude: set[int] | None = None,
    recent: list[int] | None = None,
    now: int | None = None,
    record: bool = True,
    width: int = arc.BEAM_WIDTH,
) -> dict | None:
    """`n` picks for a request spec, or None when its seed has no vectors.

    A beam search over `arc.pick_cost`; see the plan's "Cost and bounds" for
    what is cost and what is a hard rule. A radio plans `max(2 * n, 8)` steps
    and returns the first `n` (a receding horizon). The beam keeps one lane
    per filler count so paths that spent the filler ration differently
    survive, and each relaxed rung name adds `arc.RELAX_COST` to a pick.

    Returns `{results, curve, arc, reason, exhausted}`; see spec D7.
    """
    now = int(time.time()) if now is None else now
    exclude = set(exclude or ())
    recent = list(recent or ())

    vectors = recommend.load_vectors(conn)
    seed = spec["seed"]
    if seed is not None and seed not in vectors:
        return None
    tags = tag_index(conn)
    seed_tags = tags.get(seed) if seed is not None else None
    arousal = library_embeddings.audio_arousal(conn)

    start, goal = spec["start"], spec["goal"]
    d0 = start["darkness"] if start["darkness"] is not None else (
        seed_tags["darkness"] if seed_tags else None)
    d1 = goal["darkness"]
    a_start = start["subject"] if start["subject"] is not None else (
        seed_tags["subject"] if seed_tags else {})
    a_goal = goal["subject"] if goal["subject"] is not None else a_start

    plain = (seed is not None and seed_tags is None and d0 is None
             and not a_start and not goal["subject"])
    if plain:
        curve, arc_info, reason, ladder = [], None, UNTAGGED_SEED, arc.PLAIN_LADDER
    else:
        span = n if mode == "playlist" else arc.RADIO_HORIZON
        curve = [] if d0 is None else arc.curve(
            d0, span, lift=d1 is not None and d1 < d0,
            end=d1 if d1 is not None else arc.END_TARGET,
            hold=arc.SHAPE_HOLD[spec["shape"]],
        )
        arc_info = {"start": d0, "lift": d1 is not None, "lifting": arc.lifting(curve)}
        reason, ladder = None, arc.LADDER
    is_lifting = arc.lifting(curve)
    arc_on = bool(curve) or bool(a_start) or bool(a_goal)
    theme_bound = spec["kind"] != "off_topic"
    limits, sound, artist = spec["limits"], spec["sound"], spec["artist"]

    stats = recommend.content_stats_for(conn)
    calibration = recommend.calibration_for(conn, vectors, stats)
    ratings = library_ratings.all_ratings(conn)
    artists = library_queries.artist_name_sets(conn)
    names = library_queries.track_names(conn)
    fresh = recommend.fresh_tracks(conn, set(vectors), now=now)
    offer_counts = library_offers.counts(conn)

    seen_titles: dict[str, set[str]] = {}
    seen_hashes: set[str] = set()

    def remember(rowid: int, titles: dict, hashes: set) -> None:
        key = arc.title_key(names.get(rowid, ""))
        if key:
            titles[key] = titles.get(key, set()) | artists.get(rowid, frozenset())
        text_hash = tags.get(rowid, {}).get("text_hash")
        if text_hash:
            hashes.add(text_hash)

    for rowid in {*([seed] if seed is not None else []), *exclude, *recent}:
        remember(rowid, seen_titles, seen_hashes)

    prev0 = recent[0] if pos > 0 and recent and recent[0] in vectors else seed
    # Heavy content is held against the last *tagged* pick: a filler at the
    # head of `recent` has no heaviness of its own to hand on (D5).
    last_tagged = next((rowid for rowid in recent if rowid in tags), None) if pos > 0 else None
    last_heavy = (tags[last_tagged]["heavy"] if last_tagged is not None
                  else seed_tags["heavy"] if seed_tags else None)
    base_blocked = ({seed} if seed is not None else set()) | exclude
    artist_pool = ({rowid for rowid in vectors if artist in artists.get(rowid, frozenset())}
                   if artist else None)

    anchors: dict[float, tuple[dict, dict[int, float]]] = {}

    def anchor_at(k: int) -> tuple[dict, dict[int, float]]:
        s = round(arc.progress(curve, pos + k), 3)
        if s not in anchors:
            anchor = arc.blend(a_start, a_goal, s)
            anchors[s] = (anchor, {
                rowid: arc.theme_sim(anchor, entry["subject"])
                for rowid, entry in tags.items() if rowid in vectors
            } if anchor else {})
        return anchors[s]

    theme_orders: dict[int, list[int]] = {}

    def theme_order(k: int) -> list[int]:
        if k not in theme_orders:
            tgt = arc.target(curve, pos + k) if curve else None
            anchor, sims = anchor_at(k)
            if anchor:
                scored = [(-sim, rowid) for rowid, sim in sims.items()
                          if arc.in_bounds(tags[rowid]["darkness"], tgt, sim,
                                           theme_bound=theme_bound)]
            else:
                scored = [(-(recommend.freshness_boost(offer_counts.get(rowid, 0))
                             if rowid in fresh else 0.0), rowid)
                          for rowid in vectors
                          if rowid not in tags or arc.in_bounds(
                              tags[rowid]["darkness"], tgt, 0.0, theme_bound=False)]
            scored.sort()
            theme_orders[k] = [rowid for _, rowid in scored]
        return theme_orders[k]

    sonic_pools: dict[int, set[int]] = {}

    def sonic_pool(prev: int) -> set[int]:
        if prev not in sonic_pools:
            sonic_pools[prev] = recommend.retrieve(vectors, prev, legs=ARC_LEGS)
        return sonic_pools[prev]

    def expand(state, k):
        prev, path_recent, filler_history, heavy, titles, hashes, sonic = state
        blocked = base_blocked | set(path_recent)
        tgt = arc.target(curve, pos + k) if curve else None
        anchor, sims = anchor_at(k)

        if artist_pool is not None:
            pool = artist_pool - blocked
        else:
            theme_pool: set[int] = set()
            for rowid in theme_order(k):
                if len(theme_pool) >= arc.TAG_K:
                    break
                if rowid not in blocked:
                    theme_pool.add(rowid)
            pool = (theme_pool | (sonic_pool(sonic) if sonic is not None else set())) - blocked

        survivors = []
        for rowid in pool:
            if ratings.get(rowid) == -1:
                continue
            entry = tags.get(rowid)
            if arc.same_version(
                arc.title_key(names.get(rowid, "")), artists.get(rowid, frozenset()),
                entry["text_hash"] if entry else None, titles, hashes,
            ):
                continue
            if not arc.limits_ok(entry["heavy"] if entry else None, limits):
                continue
            if entry is not None:
                sim = sims.get(rowid) if anchor else None
                if not arc.in_bounds(entry["darkness"], tgt, sim if sim is not None else 1.0,
                                     theme_bound=theme_bound and bool(anchor)):
                    continue
                if not arc.glorify_ok(entry["glorifying"], lifting=is_lifting):
                    continue
                if not arc.heavy_ok(entry["heavy"], heavy, lifting=is_lifting):
                    continue
            survivors.append((arc.pick_cost(
                arc_on=arc_on, filler=entry is None,
                darkness=entry["darkness"] if entry else None, tgt=tgt,
                sim=sim if entry else None, continuity=1.0,
                gap=arc.sound_gap(arousal.get(rowid), sound),
            ), rowid))
        survivors.sort()
        keep = {rowid for _, rowid in survivors[: arc.STEP_POOL]}
        if not keep:
            return []

        if prev is not None:
            ranked = recommend.rank_candidates(
                vectors, vectors[prev], keep, weights=ARC_WEIGHTS,
                content_stats=stats, calibration=calibration,
            )
        else:
            ranked = [{"track_rowid": rowid, "score": 0.0, "reasons": {}}
                      for rowid in sorted(keep)]
        ranked = recommend.apply_ratings(ranked, ratings)
        ranked = recommend.apply_freshness(ranked, fresh=fresh, offer_counts=offer_counts)
        ranked = [r for r in ranked if r["reasons"].get("radio_eligible", True)]

        for rung in ladder:
            spacing = 0 if artist else 1 if rung.relax_spacing else recommend.ARTIST_SPACING
            eligible = recommend._eligible(
                ranked, artists=artists, recent=path_recent, exclude=set(),
                spacing=spacing, use_exclusion=False,
            )
            if rung.filler_cap and arc_on and not arc.filler_ok(filler_history):
                eligible = [r for r in eligible if r["track_rowid"] in tags]
            if not eligible:
                continue
            children = []
            for result in eligible:
                rowid = result["track_rowid"]
                entry = tags.get(rowid)
                reasons = result["reasons"]
                continuity = result["score"] - (0.0 if rung.freshness else reasons.get("freshness", 0.0))
                sim = sims.get(rowid) if entry and anchor else None
                cost = arc.pick_cost(
                    arc_on=arc_on, filler=entry is None,
                    darkness=entry["darkness"] if entry else None, tgt=tgt, sim=sim,
                    continuity=continuity if prev is not None else 0.0,
                    gap=arc.sound_gap(arousal.get(rowid), sound),
                ) + arc.RELAX_COST * len(rung.relaxed)
                children.append((cost, rowid, result, entry, sim, continuity))
            children.sort(key=lambda c: (c[0], c[1]))
            out = []
            for cost, rowid, result, entry, sim, continuity in children[:width]:
                pick = dict(result, reasons=dict(result["reasons"]))
                if not rung.freshness:
                    pick["score"] = continuity
                    pick["reasons"]["freshness"] = 0.0
                pick["reasons"]["relaxed"] = list(rung.relaxed)
                pick["reasons"]["arc"] = None if plain else {
                    "target": tgt,
                    "darkness": entry["darkness"] if entry else None,
                    "theme_sim": sim,
                    "filler": entry is None,
                }
                pick["reasons"]["cost"] = cost
                next_titles, next_hashes = dict(titles), set(hashes)
                remember(rowid, next_titles, next_hashes)
                out.append((cost, pick, (
                    rowid, [rowid, *path_recent], [entry is None, *filler_history],
                    entry["heavy"] if entry else heavy, next_titles, next_hashes,
                    sonic if sonic is not None else rowid,
                )))
            return out
        return []

    steps = n if mode == "playlist" else max(2 * n, 8)
    root = (prev0, recent, [rowid not in tags for rowid in recent],
            last_heavy, seen_titles, seen_hashes, prev0)
    results, _ = arc.beam_search(
        root, steps, width, expand, group=lambda state: sum(state[2]))
    results = results[:n]
    if record:
        library_offers.record_offers(conn, [r["track_rowid"] for r in results], now=now)
    return {
        "results": results,
        "curve": curve,
        "arc": arc_info,
        "reason": reason,
        "exhausted": len(results) < n,
    }
