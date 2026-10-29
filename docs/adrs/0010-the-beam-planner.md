# ADR 0010: The beam planner

- **Status:** Accepted
- **Date:** 2026-10-18
- **Amends:** `docs/adrs/0009-the-arc-engine.md`
- **Spec:** `docs/specs/2026-10-17-prompt-arc.md`

## Context

ADR 0009 picked each track greedily and rejected plan-then-fill because it
could not serve an open-ended radio. It named plan-then-fill as the next step
if greedy playlists ran dry late. They do: the same-day greedy baseline
exhausts 0.073 of seeds before `n`
(`docs/notes/2026-10-18-beam-planner-eval.md`). The hard darkness and theme
filters are what make sessions run dry, and greedy cannot trade a slightly
worse pick now for a usable path later.

## Decision

**A beam search replaces the greedy pick loop.** `arc_engine.plan` keeps
`BEAM_WIDTH` (8) partial paths per step. Width 1 is the greedy loop, so
`arc_eval` compares the two by a constant. There is one code path.

**Darkness and theme become cost.** Each pick costs `pick_cost` in
`muse/arc.py`: `ALPHA` times the squared distance from the darkness target,
`BETA` times theme dissimilarity to the anchor, `GAMMA` times lost
continuity, and `DELTA` times the gap to the asked-for sound band. They sit
inside outer bounds (`DARK_WINDOW_WIDE`, half of `THEME_FLOOR`). The darkness
and theme rungs leave the relaxation ladder; what remains is filler,
freshness and artist spacing. Thumbs-down, version dedupe, the glorifying and
heavy-content rules and the limits ceiling stay hard and are never relaxed.
An untagged pick takes `FILLER_COST` in place of the darkness and theme
terms.

**The anchor blends from start to goal.** The theme anchor moves from the
start subject to the goal subject with the curve's own progress. Start equal
to goal reduces to the fixed anchor of ADR 0009.

**Radio plans a receding horizon.** A radio call plans `max(2n, 8)` picks
from `pos` and returns the first `n`. Nothing of the plan is kept, so the API
stays stateless; the next call replans from the real `recent`.

**A relaxed rung costs.** Each relaxed rung name adds `RELAX_COST` (0.5) to
the pick, so a path that needed the ladder loses to an equal one that did
not.

**The beam keeps lanes by filler count.** Equal-cost permutations of
interchangeable filler picks otherwise fill all eight slots and crowd out
paths that spaced their fillers. `beam_search` fills the beam round-robin
across groups, cheapest group first, one group per number of fillers placed.

**`POST /api/arc` takes a request spec.** The planner reads a validated
request spec (`muse/request_spec.py`) with a start, a goal, a shape, a sound
and limits. The reply carries the spec back, and a radio sends it again
with `pos`, `recent` and `exclude`.

**Weights.** A grid search over ALPHA 20, 40, 80 and GAMMA 0.5, 1.0, 2.0
(150 seeds, width 8) chose ALPHA 20.0, GAMMA 0.5 (BETA and DELTA stay 1.0):
the lowest `exhausted_rate` (0.013) with `mean_theme_sim` 0.771, above the
0.721 baseline.

## Results

Against the same-day greedy baseline on all 631 seeds, width 8:

| | greedy | beam, width 8 |
|---|---|---|
| `exhausted_rate` | 0.073 | 0.025 |
| `mean_target_error` | 0.083 | 0.044 |
| `mean_theme_sim` | 0.721 | 0.763 |
| `duplicate_rate` | 0.000 | 0.000 |
| `latency_p95` | n/a | 2.356 s |

Width 1 exhausts 0.086, worse than greedy, with `latency_p95` 1.413 s. The
weights do not move latency; the beam width does.

**Owner decision, 2026-10-18:** the `latency_p95` limit is relaxed from 2.0 s
to 2.5 s. Width 8 measured 2.356 s, which passes the relaxed limit.

## Consequences

**Binding.**

- `muse/arc.py` stays pure; the beam search and cost live there, the
  database reads in `muse/arc_engine.py`.
- Every tunable lives in the constant block in `arc.py`.
- `relaxed` no longer names darkness or theme. A soft miss stays visible in
  the reasons' `target`, `darkness` and `theme_sim`.
- `plays` (ADR 0001) and the Cadence surface (ADR 0007) are untouched.

**Good.**

- Fewer sessions run dry, and arcs track their darkness target closer.
- Greedy is the width-1 case, so the comparison stays one flag.

**Bad.**

- A planning call takes about 2 s at p95 at width 8, against 1.4 s at width 1.
- A blended anchor may sit where no real track does; `arc_eval` reports theme
  similarity per position to catch it.
- A track outside a step's `STEP_POOL` short list cannot win on continuity
  alone.
