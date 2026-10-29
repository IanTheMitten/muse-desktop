"""The impure half of the recommender: everything `muse.reco` may not touch.

`reco.py` is pure so that story 44's learned scorer can replace `rank()`
without disturbing anything else. That purity has to be paid for somewhere,
and this is where: loading vectors, reading corpus statistics, evaluating the
content leg's coverage gate, and choosing a candidate set. What comes out is
plain data, which `rank()` then fuses.

The whole library lives in memory during a request. At this scale that is a
couple of megabytes and a handful of small dot products -- there is no ANN
index in this system and adding one would be machinery bought for a library
ten times this size.
"""

import random
import sqlite3
import time

from . import content, reco
from .library import content as library_content
from .library import embeddings as library_embeddings
from .library import offers as library_offers
from .library import plays as library_plays
from .library import queries as library_queries
from .library import ratings as library_ratings
from .library import taste as library_taste

__all__ = ["K_PER_LEG", "apply_ratings", "calibration_for",
           "clear_calibration_cache", "content_stats_for",
           "load_vectors", "radio_next", "rank_candidates",
           "recommend_for",
           "retrieve"]


# How many candidates each leg contributes before the union.
#
# O4 settles this at 30 against the PRD's original 50. On a library this
# size, top-50 puts 7% of everything into each leg's slice and lets the union
# approach a fifth of the library -- at which point the re-rank weights are
# averaging over so many candidates that they stop distinguishing anything.
# Small libraries need less recall than the number suggests.
K_PER_LEG = 30

# Which legs contribute candidates. Emotion is deliberately absent: it is two
# dimensions, so its "top 30" is mostly ties and it adds almost no recall the
# other legs do not already provide. It still *scores* in `rank()` -- being a
# poor way to find candidates is not the same as being a poor way to rank
# them.
RETRIEVAL_LEGS = ("audio_vec", "feature_vec", "lyrics_vec", "content_vec",
                  "taste_vec")


def load_vectors(conn: sqlite3.Connection) -> dict[int, dict]:
    """Every playable track's vectors, merged into one mapping per track.

    Three tables with three lifecycles -- `embeddings` is written by the embed
    batch, `content_embeddings` by a separate program in a separate
    virtualenv, `taste_embeddings` wholesale by the nightly retrain -- so a
    track can carry any subset of the six legs. Absent ones are None, which
    is exactly what `rank()` reads as a null leg.

    The key set is the **playable tracks**, not the rows of whichever table
    is read first. An earlier version built its keys from `embeddings` and
    merged the other two onto that, which meant a track present in
    `content_embeddings` but absent from `embeddings` vanished entirely. On
    the real library -- 97 content vectors and the CLAP batch never run --
    that discarded every content vector there was and returned an empty
    mapping. The unit tests all created an `embeddings` row first, so none of
    them saw it.
    """
    playable = library_queries.playable_rowids(conn)
    merged: dict[int, dict] = {}

    for rowid, entry in library_embeddings.vectors(conn).items():
        if rowid in playable:
            merged[rowid] = entry

    for source, key in ((library_content.vectors(conn), "content_vec"),
                        (library_taste.vectors(conn), "taste_vec")):
        for rowid, vector in source.items():
            if rowid not in playable:
                continue
            merged.setdefault(rowid, {})[key] = vector

    for entry in merged.values():
        for key in ("audio_vec", "feature_vec", "lyrics_vec", "emotion_vec",
                    "content_vec", "taste_vec"):
            entry.setdefault(key, None)
    return merged


def content_stats_for(conn: sqlite3.Connection) -> dict | None:
    """The corpus statistics to hand `rank()`, or None when the leg is gated.

    This is the single place the gate is turned into an argument. `rank()`
    reads None as "no normalization available", which is the same thing it
    reads for a library that has never run a batch -- so there is one
    condition, not a boolean that could disagree with a statistics row.
    """
    if not content.content_available(conn):
        return None
    return library_content.stats(conn)


# --- Calibration (ADR 0008) ----------------------------------------------
#
# Random track pairs sampled per leg to fit its CDF knots. 2,000 pairs puts
# a knot's sampling error around +/-0.01 in calibrated units, and costs well
# under a second of pure Python on this library -- paid once per change to
# the vectors, not per request, because of the cache below.
CALIBRATION_PAIRS = 2000

# Fixed, for the same reason as `content.STATS_SAMPLE_SEED`: the same corpus
# must always calibrate the same way, or scores drift between restarts.
CALIBRATION_SEED = 0

_CALIBRATED_LEGS = ("audio_vec", "feature_vec", "lyrics_vec", "emotion_vec",
                    "taste_vec", "content_vec")

_calibration_cache: dict = {}


def clear_calibration_cache() -> None:
    _calibration_cache.clear()


def _pair_sim(leg: str, a, b, content_stats: dict | None) -> float:
    if leg == "emotion_vec":
        return reco.emotion_sim(tuple(a), tuple(b))
    if leg == "content_vec":
        return reco.content_sim(list(a), list(b), mean=content_stats["mean_vec"],
                                lo=content_stats["sim_lo"],
                                hi=content_stats["sim_hi"])
    return reco.cosine_sim(list(a), list(b))


def _fit_leg(vectors: dict[int, dict], leg: str,
             content_stats: dict | None) -> list[float] | None:
    if leg == "content_vec" and content_stats is None:
        return None
    have = sorted(rowid for rowid, entry in vectors.items()
                  if entry.get(leg) is not None)
    n = len(have)
    if n * (n - 1) // 2 <= CALIBRATION_PAIRS:
        pairs = [(have[i], have[j]) for i in range(n) for j in range(i + 1, n)]
    else:
        rng = random.Random(CALIBRATION_SEED)
        pairs = [tuple(rng.sample(have, 2)) for _ in range(CALIBRATION_PAIRS)]
    sims = [_pair_sim(leg, vectors[a][leg], vectors[b][leg], content_stats)
            for a, b in pairs]
    return reco.fit_calibration(sims)


def calibration_for(conn: sqlite3.Connection, vectors: dict[int, dict],
                    content_stats: dict | None) -> dict[str, list[float] | None]:
    """Each leg's CDF knots over random pairs of this library's tracks.

    Cached on `vector_fingerprint`, which moves whenever any of the three
    vector tables or the content statistics change. A leg too sparse to fit
    (fewer than `reco.MIN_CALIBRATION_PAIRS` pairs) maps to None and stays
    on its raw scale; so does content while its coverage gate is closed.
    """
    # The database file is part of the key: two libraries can share a
    # fingerprint (same counts, same second), and must not share knots.
    database = conn.execute("PRAGMA database_list").fetchone()[2]
    key = (database, library_queries.vector_fingerprint(conn))
    cached = _calibration_cache.get("entry")
    if cached is not None and cached[0] == key:
        return cached[1]
    calibration = {leg: _fit_leg(vectors, leg, content_stats)
                   for leg in _CALIBRATED_LEGS}
    _calibration_cache["entry"] = (key, calibration)
    return calibration


def _top_k(vectors: dict[int, dict], seed: int | dict, leg: str, k: int) -> set[int]:
    """The k nearest tracks to the seed on one leg.

    `seed` is either a track rowid -- today's shape -- or a prepared query
    dict. A dict seed is no track, so nothing is excluded by identity; an
    int seed still excludes itself by rowid as before.

    Returns nothing when the *seed* lacks the leg. An instrumental has no
    lyrics vector, and the alternative -- comparing against a zero vector, or
    falling back to some arbitrary ordering -- would fill the candidate set
    with tracks selected by nothing at all. The other legs keep their full k
    rather than growing to compensate, so a seed with fewer legs gets a
    smaller, not a noisier, candidate set.
    """
    if isinstance(seed, dict):
        seed_rowid = None
        seed_vec = seed.get(leg)
    else:
        seed_rowid = seed
        seed_vec = vectors.get(seed_rowid, {}).get(leg)
    if seed_vec is None:
        return set()

    scored = []
    for rowid, entry in vectors.items():
        if rowid == seed_rowid:
            continue
        other = entry.get(leg)
        if other is None:
            continue
        if leg == "emotion_vec":
            sim = reco.emotion_sim(tuple(seed_vec), tuple(other))
        else:
            sim = reco.cosine_sim(list(seed_vec), list(other))
        scored.append((sim, rowid))

    # Sorted by similarity, then by rowid, so a tie resolves the same way
    # every time rather than by dictionary order.
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return {rowid for _, rowid in scored[:k]}


def retrieve(
    vectors: dict[int, dict],
    seed: int | dict,
    *,
    k: int = K_PER_LEG,
    legs=RETRIEVAL_LEGS,
) -> set[int]:
    """The candidate set: top-k per leg, unioned, seed excluded.

    `seed` is a track rowid or a prepared query dict (see `_top_k`). A union
    rather than an intersection because the legs disagree about what
    similarity means, and that disagreement is the point -- a track that is
    the best lyrical match and a mediocre sonic one deserves to be scored,
    not filtered out before `rank()` ever sees it.
    """
    candidates: set[int] = set()
    for leg in legs:
        candidates |= _top_k(vectors, seed, leg, k)
    if not isinstance(seed, dict):
        candidates.discard(seed)
    return candidates


def rank_candidates(
    vectors: dict[int, dict],
    query: dict,
    candidates: set[int],
    *,
    weights: dict[str, float] | None = None,
    content_stats: dict | None = None,
    calibration: dict | None = None,
) -> list[dict]:
    """Fuse an already-retrieved candidate set, with no side effects: the
    part of `recommend_for` that `muse.reco_eval` replays against held-out
    transitions. Retrieval is the caller's so the harness can reuse one
    candidate set across every variant it scores; ratings, freshness, and
    spacing are left to the caller too."""
    return reco.rank(
        query,
        [{"track_rowid": rowid, **vectors[rowid]} for rowid in candidates],
        weights=weights,
        content_stats=content_stats,
        calibration=calibration,
    )


def apply_ratings(results: list[dict], ratings: dict[int, int]) -> list[dict]:
    """Apply explicit thumbs up/down as a constraint on already-fused scores.

    A rating is **not** a leg and not a term in the weighted sum. ADR 0003
    folded the old `feedback_score` weights -- thumbs-down included -- into
    the scalar that ALS factorizes during the nightly retrain, specifically
    so there would be one event-weighting scheme rather than two. A second
    weighted term here would reopen that.

    But that single path has a hole story 40 cares about. `taste_sim` is null
    below `taste.MIN_PLAY_EVENTS`, so on a track you have barely played an
    explicit rejection reaches the score through no leg at all -- and
    "reject something without having to skip it repeatedly" is exactly the
    barely-played case. So the rating applies *after* fusion, in the same
    category as artist spacing: a constraint that cannot be outvoted by a
    high similarity, and that works identically whichever legs were
    available.

    Thumbs-down clamps to zero and removes the track from radio. Thumbs-up
    floors at zero and does nothing else -- a floor, not a bonus. Explicit
    signal constrains; it does not accumulate.

    The honest residue: a rating on a well-played track now reaches the
    result twice, diffusely through ALS training and categorically here. That
    is accepted rather than solved. The training path is what ADR 0003
    requires, the categorical path is what story 40 requires, and suppressing
    a track the listener explicitly rejected is not a failure mode worth
    machinery to prevent.
    """
    for result in results:
        rating = ratings.get(result["track_rowid"])
        result["reasons"]["rating"] = rating
        result["reasons"]["radio_eligible"] = rating != -1
        if rating == -1:
            result["score"] = min(result["score"], 0.0)
        elif rating == 1:
            result["score"] = max(result["score"], 0.0)
    results.sort(key=lambda row: row["score"], reverse=True)
    return results


# --- Radio sequencing -----------------------------------------------------
#
# Three rules, all applied after scoring and none of them a weight. They
# encode a fact about library size rather than a taste judgment: 723 tracks
# across 117 artists is small enough that unconstrained nearest-neighbour
# retrieval returns six songs by the same artist and calls it a success, and
# the dominant failure mode is hearing the same twenty tracks forever rather
# than hearing something insufficiently similar. There is nothing here for
# story 44's learned scorer to discover, so it inherits them unchanged.

# The most a never-played track can gain. Additive and capped, not a
# multiplier: at launch every track qualifies and the boost is a harmless
# constant, but three months in a small set of never-played tracks would
# otherwise carry a permanent advantage regardless of fit.
#
# In calibrated-score units (ADR 0008). PRD O2 set 0.15 against raw scores;
# calibrated candidate sets are ~1.5x more spread out (median top-1 to
# top-20 gap 0.079 -> 0.120 over 150 seeds), so 0.23 keeps the same pull
# relative to fit rather than quietly weakening it by a third.
FRESHNESS_MAX = 0.23

# Offers, not plays. A track pushed this many times and declined every time
# has been answered, and should stop being pushed.
FRESHNESS_OFFER_LIMIT = 3

# No artist may repeat within this many tracks.
ARTIST_SPACING = 3


def freshness_boost(offer_count: int) -> float:
    """The additive boost for a track with no recent play, decayed by how
    many times it has already been offered. Zero at the limit."""
    if offer_count >= FRESHNESS_OFFER_LIMIT:
        return 0.0
    remaining = 1.0 - (offer_count / FRESHNESS_OFFER_LIMIT)
    return FRESHNESS_MAX * remaining


def apply_freshness(
    results: list[dict], *, fresh: set[int], offer_counts: dict[int, int]
) -> list[dict]:
    """Add the freshness boost to every candidate with no recent play.

    `fresh` is the set of track_rowids unplayed inside the window; the caller
    computes it, because "recent" is a fact about `plays` and this function
    is about arithmetic.
    """
    for result in results:
        rowid = result["track_rowid"]
        offers = offer_counts.get(rowid, 0)
        boost = freshness_boost(offers) if rowid in fresh else 0.0
        # Replace whatever boost this result is already carrying rather than
        # adding to it. `recommend_for` calls `radio_next` once per pick and
        # every call passes the same surviving result objects back in, so a
        # plain `+=` compounds: a candidate that survived eight rounds was
        # boosted eight times and came back scoring above 1.0, with the
        # ordering still looking right because every candidate compounded
        # equally. Idempotent, not frozen -- a changed offer count still
        # changes the boost, which is what keeps the decay working.
        result["score"] += boost - result["reasons"].get("freshness", 0.0)
        result["reasons"]["freshness"] = boost
        result["reasons"]["offer_count"] = offers
    results.sort(key=lambda row: row["score"], reverse=True)
    return results


def _eligible(
    results: list[dict], *, artists: dict[int, frozenset[str]],
    recent: list[int], exclude: set[int], spacing: int, use_exclusion: bool,
) -> list[dict]:
    """Candidates surviving artist spacing and the exclusion set.

    `radio_eligible` is checked here and is never relaxed by the ladder
    below: the ladder loosens constraints that encode library size, and an
    explicit thumbs-down is not one of those. Running dry is not a reason to
    resurface something the listener rejected.

    Spacing blocks on set intersection, not equality: a candidate sharing
    any artist with a recent track is blocked, so a featured-artist credit
    walks into the same wall as an exact repeat. An empty artist set blocks
    nothing and is blocked by nothing.
    """
    blocked_artists: frozenset[str] = frozenset().union(
        *(artists.get(rowid, frozenset()) for rowid in recent[:spacing])
    )
    out = []
    for result in results:
        rowid = result["track_rowid"]
        if not result["reasons"].get("radio_eligible", True):
            continue
        if use_exclusion and rowid in exclude:
            continue
        if artists.get(rowid, frozenset()) & blocked_artists:
            continue
        out.append(result)
    return out


def radio_next(
    results: list[dict], *, artists: dict[int, frozenset[str]],
    recent: list[int], exclude: set[int], fresh: set[int],
    offer_counts: dict[int, int],
) -> dict | None:
    """One next track, or None when nothing is playable at all.

    `recent` is most-recent-first: `recent[0]` is the track that just
    finished. Artist spacing reads the first `spacing` entries, so the order
    is load-bearing rather than incidental.

    Applies the freshness boost, then filters, then walks O5's relaxation
    ladder if the filters left nothing: drop freshness, then shrink artist
    spacing from three to one, then stop honouring the exclusion set. Each
    rung taken is named in `reasons["relaxed"]`, because a session that has
    quietly stopped spacing artists should say so rather than just sounding
    worse (design principle 3).

    **The first rung cannot add candidates**, and that is not an oversight.
    Freshness is an additive boost, not a filter, so dropping it changes the
    ordering and never the candidate set -- O5 lists it first because it is
    the cheapest thing to give up, not because it is the one that unblocks a
    dry session. It is implemented rather than skipped so the ladder matches
    the documented order, and so that a future change making freshness a
    filter finds the rung already in place.
    """
    scored_results = apply_freshness(results, fresh=fresh,
                                     offer_counts=offer_counts)

    ladder = [
        ([], ARTIST_SPACING, True),
        (["freshness"], ARTIST_SPACING, True),
        (["freshness", "artist_spacing"], 1, True),
        (["freshness", "artist_spacing", "exclusion"], 1, False),
    ]

    for relaxed, spacing, use_exclusion in ladder:
        if "freshness" in relaxed:
            for result in scored_results:
                result["score"] -= result["reasons"].get("freshness", 0.0)
                result["reasons"]["freshness"] = 0.0
            scored_results.sort(key=lambda row: row["score"], reverse=True)
        survivors = _eligible(scored_results, artists=artists, recent=recent,
                              exclude=exclude, spacing=spacing,
                              use_exclusion=use_exclusion)
        if survivors:
            pick = survivors[0]
            pick["reasons"]["relaxed"] = relaxed
            return pick
    return None


# How long a track must go unplayed to count as fresh.
FRESHNESS_WINDOW_DAYS = 30
_SECONDS_PER_DAY = 86_400


def fresh_tracks(
    conn: sqlite3.Connection, known: set[int], *, now: int
) -> set[int]:
    """The tracks with no play inside the freshness window.

    Computed as the complement of what *was* played, because on a library
    this size most tracks have not been played in any given month and the
    played set is the smaller query.
    """
    since = now - FRESHNESS_WINDOW_DAYS * _SECONDS_PER_DAY
    return known - library_plays.played_since(conn, since)


def mood_target(vectors: dict[int, dict], valence: float,
                arousal: float) -> tuple[float, float] | None:
    """Map a mood in [-1, 1]^2 onto the library's own emotion range: 0 is the
    per-axis median, +1/-1 the 95th/5th percentile. None if no track has an
    emotion vec."""
    emotions = [v["emotion_vec"] for v in vectors.values()
                if v.get("emotion_vec") is not None]
    if not emotions:
        return None
    out = []
    for axis, x in enumerate((valence, arousal)):
        s = sorted(e[axis] for e in emotions)
        p05, p50, p95 = (s[int(p * (len(s) - 1))] for p in (.05, .5, .95))
        # Same line as p50 + x*(p50-p05), written so x=-1 lands exactly on p05.
        out.append(p50 + x * (p95 - p50) if x >= 0
                   else p05 + (1 + x) * (p50 - p05))
    return out[0], out[1]


def recommend_for(
    conn: sqlite3.Connection,
    seed: int | dict,
    *,
    n: int = 20,
    exclude: set[int] | None = None,
    recent: list[int] | None = None,
    weights: dict[str, float] | None = None,
    now: int | None = None,
) -> list[dict] | None:
    """`n` tracks to play after `seed`, best first, or None if a rowid seed
    has no vectors to compare from.

    `seed` is a track rowid -- today's shape, used by
    `GET /api/recommend/{rowid}` -- or a prepared query dict. A dict
    seed is no track, so the "seed has no vectors" guard, which only makes
    sense for a rowid, is skipped; the caller is responsible for handing in
    a non-empty dict.

    The results are a **sequence**, not just a ranked list: each pick is
    chosen with the previous picks counted as recently played, so artist
    spacing holds across the returned block and not merely against what the
    client already played. That is what makes story 35's "keep appending to
    the queue" one call rather than n, while story 28's "play next from this
    track" is the same call with n=1. One behaviour, both stories.

    `recent` is most-recent-first and comes from the client, which owns the
    queue -- this is why radio needs no endpoint and no server-side session.
    """
    now = int(time.time()) if now is None else now
    exclude = set(exclude or ())
    recent = list(recent or ())

    vectors = load_vectors(conn)
    if not isinstance(seed, dict) and seed not in vectors:
        return None
    query = seed if isinstance(seed, dict) else vectors[seed]

    stats = content_stats_for(conn)
    ranked = rank_candidates(
        vectors, query, retrieve(vectors, seed), weights=weights,
        content_stats=stats,
        calibration=calibration_for(conn, vectors, stats),
    )
    ranked = apply_ratings(ranked, library_ratings.all_ratings(conn))

    artists = library_queries.artist_name_sets(conn)
    fresh = fresh_tracks(conn, set(vectors), now=now)
    offer_counts = library_offers.counts(conn)

    picked: list[dict] = []
    remaining = list(ranked)
    for _ in range(n):
        pick = radio_next(remaining, artists=artists, recent=recent,
                          exclude=exclude, fresh=fresh,
                          offer_counts=offer_counts)
        if pick is None:
            break
        picked.append(pick)
        remaining = [r for r in remaining
                     if r["track_rowid"] != pick["track_rowid"]]
        recent.insert(0, pick["track_rowid"])
        exclude.add(pick["track_rowid"])

    # Offered, not played -- recording it here is what makes O2's decay
    # measure what it claims to measure.
    library_offers.record_offers(conn, [r["track_rowid"] for r in picked],
                                 now=now)
    return picked
