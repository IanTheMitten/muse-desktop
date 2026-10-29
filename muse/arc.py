"""`muse/arc.py` -- the arc engine's pure half
(`docs/specs/2026-10-17-arc-engine.md`, ADR 0009).

The darkness curve, the filters, the subject vector and the version key,
over plain values. Nothing here opens a database: `muse/arc_engine.py`
loads and hands in. Same seam `muse/reco.py` holds, asserted by
`tests/test_arc_purity.py`.

Every tunable lives in the block below, so tuning the arc changes numbers,
not code.
"""

import math
import re
from typing import NamedTuple

# --- tunables -----------------------------------------------------------------

HOLD_FRAC = 0.4          # share of the span held at the start darkness (D4)
END_TARGET = 0.25        # `overcome` on the outlook ladder: through it, looking back
STEP_CAP = 0.15          # largest drop between consecutive targets
RADIO_HORIZON = 30       # songs a radio takes to lift; it holds after
DARK_WINDOW = 0.15       # how far a pick's darkness may sit from the target
DARK_WINDOW_WIDE = 0.3   # the ladder's first rung
THEME_FLOOR = 0.5        # subject similarity to the anchor; ~random-pair p75 (0.53)
GLORIFY_MAX = 0.5        # never picked at or above this in a lifting session
HEAVY_SLACK = 0.1        # how far heavy content may rise in a lifting session
FILLER_EVERY = 4         # at most one untagged pick in this many
TAG_K = 30               # theme-pool size per pick
BEAM_WIDTH = 8           # partial paths kept per step
STEP_POOL = 40           # cheapest survivors ranked per beam state
ALPHA = 20.0             # cost weight on squared distance from the darkness target
BETA = 1.0               # cost weight on theme dissimilarity to the anchor
GAMMA = 0.5              # cost weight on lost continuity with the previous track
DELTA = 1.0              # cost weight on the gap to the asked-for sound band
RELAX_COST = 0.5         # per relaxed rung name: a path that needed the ladder loses to an equal one that did not
FILLER_COST = 0.5        # an untagged pick's cost in place of the darkness and theme terms
SHAPE_HOLD = {"steady": 0.0, "gradual": HOLD_FRAC, "late": 0.7}  # share of the span held per shape
SOUND_BANDS = {"calm": (-1.0, 0.25), "mid": (0.25, 0.57), "energetic": (0.57, 1.0)}  # arousal range per sound
LIMIT_CEILING = {"anything": None, "no_explicit": 0.5, "clean": 0.2}  # highest heavy content per limit


# --- curve --------------------------------------------------------------------

def curve(d0: float, span: int, *, lift: bool = True, end: float = END_TARGET,
          hold: float = HOLD_FRAC) -> list[float]:
    """Target darkness for each of `span` positions: hold at `d0` for the
    first `hold` of the span, then ease (smoothstep) down to `end`, never
    dropping more than `STEP_CAP` in one step -- the cap wins over landing
    exactly on a short span. Flat at `d0` without lift, or when the seed is
    already at or below `end`."""
    if span <= 0:
        return []
    if not lift or d0 <= end:
        return [d0] * span
    hold = max(1, round(hold * span))
    turn = span - hold
    out = [d0] * hold
    for j in range(1, turn + 1):
        x = j / turn
        eased = x * x * (3 - 2 * x)
        value = d0 + (end - d0) * eased
        out.append(max(value, out[-1] - STEP_CAP))
    return out


def target(c: list[float], k: int) -> float:
    """The target at position `k`; past the span the last value holds, which
    is what lets a radio keep going after its horizon."""
    return c[min(k, len(c) - 1)]


def progress(c: list[float], k: int) -> float:
    """How far along the curve position `k` is, 0 at the start and 1 at the
    end; 0.0 for an empty or flat curve."""
    if not c or c[0] == c[-1]:
        return 0.0
    return min(1.0, max(0.0, (c[0] - target(c, k)) / (c[0] - c[-1])))


def lifting(c: list[float]) -> bool:
    """A lifting session is one whose curve ends below where it starts."""
    return bool(c) and c[-1] < c[0]


# --- subject vector -----------------------------------------------------------

# The groups that measure mood rather than subject. With them in, the theme
# filter would hold darkness steady and fight the lift: `outlook` *is*
# darkness, and emotion and stance move with it (spec D3, measured
# 2026-10-17: random-pair median similarity 0.55 with them, 0.25 without).
MOOD_GROUPS = frozenset({"outlook", "emotion", "stance"})
SEVERITY_PREFIX = "sev_"


def subject_vector(vec: dict[str, float]) -> dict[str, float]:
    """`lyric_tags.tag_vector()` without the mood groups: what a song is
    about, not how dark it is."""
    out = {}
    for key, weight in vec.items():
        group = key.split(":", 1)[0]
        if group in MOOD_GROUPS or group.startswith(SEVERITY_PREFIX):
            continue
        out[key] = weight
    return out


def theme_sim(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity of two sparse vectors; 0.0 when either is empty."""
    norm_a = math.sqrt(sum(x * x for x in a.values()))
    norm_b = math.sqrt(sum(x * x for x in b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    dot = sum(x * b.get(key, 0.0) for key, x in a.items())
    return dot / (norm_a * norm_b)


def blend(start: dict[str, float], goal: dict[str, float] | None, s: float) -> dict[str, float]:
    """The anchor `s` of the way from `start` to `goal`, both normalised
    first; `start` itself when there is no distinct goal."""
    if goal is None or goal == start:
        return start
    def unit(v: dict[str, float]) -> dict[str, float]:
        norm = math.sqrt(sum(x * x for x in v.values()))
        return {key: x / norm for key, x in v.items()} if norm else {}
    a, b = unit(start), unit(goal)
    mix = {key: (1 - s) * a.get(key, 0.0) + s * b.get(key, 0.0) for key in a.keys() | b.keys()}
    return unit(mix)


# --- filters ------------------------------------------------------------------

def darkness_ok(darkness: float, tgt: float, window: float) -> bool:
    return abs(darkness - tgt) <= window + 1e-12


def glorify_ok(glorifying: float, *, lifting: bool) -> bool:
    """A song that makes harm look fun is never picked on the way up."""
    return not lifting or glorifying < GLORIFY_MAX


def heavy_ok(heavy: float, prev_heavy: float | None, *, lifting: bool) -> bool:
    """Heavy content steps down on the way up: it may not rise past the last
    tagged pick's by more than `HEAVY_SLACK`."""
    if not lifting or prev_heavy is None:
        return True
    return heavy <= prev_heavy + HEAVY_SLACK + 1e-12


def filler_ok(history: list[bool]) -> bool:
    """`history` is most-recent-first, True for an untagged pick. A filler
    may not follow another within `FILLER_EVERY` picks."""
    return not any(history[: FILLER_EVERY - 1])


def sound_gap(arousal: float | None, sound: str) -> float:
    """Distance from `arousal` to the asked-for band; 0 when unknown or `any`."""
    band = SOUND_BANDS.get(sound)
    if arousal is None or band is None:
        return 0.0
    low, high = band
    return max(0.0, low - arousal, arousal - high)


def limits_ok(heavy: float | None, limits: str) -> bool:
    """Under a content limit an untagged track (no heavy score) fails."""
    ceiling = LIMIT_CEILING[limits]
    if ceiling is None:
        return True
    return heavy is not None and heavy <= ceiling + 1e-12


def in_bounds(darkness: float, tgt: float | None, sim: float, *, theme_bound: bool) -> bool:
    """The outer bounds of a tagged pick, never relaxed."""
    if tgt is not None and abs(darkness - tgt) > DARK_WINDOW_WIDE + 1e-12:
        return False
    return not theme_bound or sim >= THEME_FLOOR / 2 - 1e-12


def pick_cost(*, arc_on: bool, filler: bool, darkness: float | None, tgt: float | None,
              sim: float | None, continuity: float, gap: float) -> float:
    """One pick's cost; lower is better. `arc_on` is a session with a curve
    or an anchor, where a filler takes `FILLER_COST` for the first two terms."""
    cost = GAMMA * (1.0 - continuity) + DELTA * gap
    if not arc_on:
        return cost
    if filler:
        return cost + FILLER_COST
    if tgt is not None and darkness is not None:
        cost += ALPHA * (darkness - tgt) ** 2
    if sim is not None:
        cost += BETA * (1.0 - sim)
    return cost


def beam_search(root, n: int, width: int, expand, *, group=None) -> tuple[list, bool]:
    """Keep the `width` cheapest partial paths for `n` steps. `expand(state,
    k)` returns `(cost, item, next_state)` children. Returns the cheapest
    path's items and whether it fell short of `n`.

    With `group(next_state)`, the beam is filled round-robin across groups
    (cheapest group first) instead of purely by cost: lanes keep paths that
    spent a rationed resource differently, because equal-cost permutations of
    interchangeable picks otherwise fill the beam."""
    beams = [(0.0, (), root)]
    for k in range(n):
        children = [(total + cost, items + (item,), nxt)
                    for total, items, state in beams
                    for cost, item, nxt in expand(state, k)]
        if not children:
            break
        children.sort(key=lambda b: b[0])
        if group is None:
            beams = children[:width]
        else:
            buckets: dict = {}
            for child in children:
                buckets.setdefault(group(child[2]), []).append(child)
            lanes = list(buckets.values())
            kept = []
            for i in range(max(len(lane) for lane in lanes)):
                for lane in lanes:
                    if i < len(lane) and len(kept) < width:
                        kept.append(lane[i])
            kept.sort(key=lambda b: b[0])
            beams = kept
    best = min(beams, key=lambda b: b[0])
    return list(best[1]), len(best[1]) < n


# --- version key (D6) ---------------------------------------------------------

_MARKERS = r"(live|remaster\w*|version|edit(?:ed|ion)?|acoustic|demo|mono|feat\.?|ft\.)(?![a-z])"
_BRACKETED = re.compile(r"\s*[\(\[][^\)\]]*\b" + _MARKERS + r"[^\)\]]*[\)\]]", re.IGNORECASE)
_DASHED = re.compile(r"\s+-\s+[^-]*\b" + _MARKERS + r".*$", re.IGNORECASE)
_FEAT = re.compile(r"\s+(feat\.?|ft\.)\s.*$", re.IGNORECASE)


def title_key(title: str) -> str:
    """A title with live/remaster/version/edit/acoustic/demo/mono/feat
    markers removed, lowercased, whitespace collapsed."""
    t = _BRACKETED.sub("", title)
    t = _DASHED.sub("", t)
    t = _FEAT.sub("", t)
    return " ".join(t.lower().split())


def same_version(key: str, artists: frozenset[str], text_hash: str | None,
                 seen_titles: dict[str, set[str]], seen_hashes: set[str]) -> bool:
    """Already in the session: same title key with a shared artist, or the
    same lyrics (`text_hash`) whoever sings them."""
    if text_hash is not None and text_hash in seen_hashes:
        return True
    return bool(artists & seen_titles.get(key, set()))


# --- relaxation ladder (D5) ---------------------------------------------------

class Rung(NamedTuple):
    relaxed: tuple[str, ...]  # names reported in `reasons["relaxed"]`
    filler_cap: bool          # enforce `filler_ok`
    freshness: bool           # keep the freshness boost in the order
    relax_spacing: bool       # artist spacing 3 -> 1


# Darkness and theme are cost now, not rungs. Never on the ladder: thumbs-down,
# version dedupe (and with it the exclude set), the glorifying rule, the
# heavy-content rule, the limits ceiling, the outer bounds.
LADDER = (
    Rung((), True, True, False),
    Rung(("filler",), False, True, False),
    Rung(("filler", "freshness"), False, False, False),
    Rung(("filler", "freshness", "artist_spacing"), False, False, True),
)

# An untagged seed has no curve and no anchor: only today's rungs apply.
PLAIN_LADDER = (
    Rung((), False, True, False),
    Rung(("freshness",), False, False, False),
    Rung(("freshness", "artist_spacing"), False, False, True),
)
