# ADR 0009: The arc engine

- **Status:** Accepted
- **Date:** 2026-10-17
- **Supersedes:** `docs/adrs/0005-archetype-radio-and-the-drift-policy.md`,
  `docs/adrs/0006-the-radio-curator.md`
- **Spec:** `docs/specs/2026-10-17-arc-engine.md`
- **Amended by:** docs/adrs/0010-the-beam-planner.md

## Context

The goal is a playlist and a radio that keep a theme steady while moving
along a narrative arc toward the light, with no duplicate songs. Three
mechanisms existed for parts of this, and none of them read the lyric tags
from piece 1:

- **Seed radio.** A chain walk that forgets its seed after one hop.
- **Archetype radio.** A centroid drift in embedding space between
  hand-seeded archetypes.
- **The curator.** A System One re-ranker that was never used live.

## Decision

**One tag-driven engine replaces all three.** The engine works in three
parts:

- **The seed's tags set the session.** The seed's subject vector (its tag
  vector without the mood groups, so theme and darkness stay separate) is
  a fixed theme anchor, and its `mood_darkness` is the starting point of a
  hold-then-turn darkness curve.
- **Each pick is greedy.** It is the best continuity score (`rank()`
  against the previous track) among candidates that pass hard filters:
  darkness within a window of the curve, theme similarity to the anchor,
  no glorifying or rising heavy content while lifting, and one version
  per song.
- **A relaxation ladder.** It loosens these filters in a fixed, reported
  order.

Archetypes, drift, the curator, archetype profiles and one-sentence themes
are removed.

Three alternatives were rejected:

- **A joint score** (continuity plus weighted theme plus arc error). It
  makes the arc soft: a strong sonic match can override it, so the curve
  overshoots or stalls. It also turns a constraint into evidence inside
  `rank()`. The taxonomy note had already placed the arc as a constraint
  in `recommend.py`.
- **Plan-then-fill** (beam search over the whole curve). It gives better
  playlists, but it cannot serve an open-ended radio, so there would be
  two code paths. It stays the next step if `arc_eval` shows greedy
  playlists running dry late.
- **Keeping archetypes as saved anchors.** Users bring their own libraries,
  so hand-seeded archetypes do not transfer. Piece 3's text entry takes
  their place.

**The lyrics leg leaves the arc path.** Theme comes from tags. Continuity
inside the engine runs with `w_lyr = 0`. `/api/recommend` keeps the leg.

**Skips do not move the arc.** A skip steers sound, through the exclude set
and continuity. The arc moves only with position. The listener's one arc
control is the `lift` toggle.

## Consequences

**Binding.**

- `muse/arc.py` is pure, the same contract `reco.py` and `drift.py` had.
  A `Connection` there breaks the seam even when the tests still pass.
- Every tunable lives in one constant block in `arc.py`.
- `plays` is untouched (ADR 0001).
- The Cadence surface is untouched (ADR 0007).

**Good.**

- One mechanism instead of three.
- The arc is read from what songs say, not from how close they sound to
  hand-picked seeds.
- Each pick explains itself: target, darkness, theme similarity, and the
  rungs relaxed.
- No per-pick LLM cost.
- The API is stateless, as radio already was.

**Bad.**

- Continuity loses the lyrics leg. A 2026-10-10 replay put hit@10 at 0.095
  without it, against 0.111 with it.
- About a quarter of playable tracks have no tags. Until lyrics coverage
  improves they can only be filler, so the engine has a smaller pool.
- Greedy picks can run dry late in a playlist.
- The engine depends on tag quality. Korean and Japanese tagging has not
  been piloted yet.
