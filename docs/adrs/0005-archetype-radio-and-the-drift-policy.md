# ADR 0005: Archetype radio and the drift policy

- **Status:** Superseded by ADR 0009 (2026-10-17)
- **Date:** 2026-09-21
- **Slice:** 2 (Recommender)
- **Related:** `docs/adrs/0003-emotion-and-taste-legs-in-the-recommender.md`,
  `docs/adrs/0004-content-embed-leg-and-the-shared-audio-budget.md`,
  `docs/specs/2026-09-21-radio-drift-policy.md`

## Context

ADR 0003 fixed the radio model: no server-side session, a moving query point
the client re-seeds one track at a time through `GET /api/recommend/{track_rowid}`.
That model has no notion of a *destination* — every leg of `rank()` compares
against wherever the query point already sits, and nothing about it changes on
purpose over the course of a session.

Two statements from the owner ask for exactly that kind of change, for two
different kinds of list:

- Situational lists ("what do you play in the car / on a plane") should stay
  close to what was asked for — similar, fresh, not wandering.
- Emotional lists ("what do you play when you are sad") should drift, on
  purpose, toward a better mood, gradually enough that the listener isn't
  fixated on any one song along the way.

Both kinds are authored the same way: a fixed, upgradeable set of questions,
each answered with a list of tracks (the seed set). This ADR is the record for
the decisions that make that run, drawn from
`docs/specs/2026-09-21-radio-drift-policy.md`'s D1–D12. It is a new document,
not an edit to ADR 0003 or ADR 0004, because both are accepted and immutable
once shipped and this slice does not reopen either.

Out of scope here, and not described as existing anywhere in this ADR: the
onboarding UI that will let a listener answer the questions, the archetype
*similarity profile* (a stored per-track column of archetype similarities),
the LLM curator, and rebuilding a block mid-listen on a skip. Each is deferred
to its own spec.

## Decision

**The client sends a recipe, not a vector (D1).** The query point has to
survive a round trip somehow, since there is no server-side session to hold
it. The rejected alternative was the client holding the actual query vector as
floats and posting it back each request. A float blob is opaque: it can't be
logged usefully, replayed from a log line to reproduce a bad block, or
rejected as nonsense by the server before it does anything with it. A second
rejected alternative — the server inferring the whole session from play
history alone, with the client sending only the archetype id — was dropped
because a paused-and-resumed session then behaves unpredictably: nothing pins
down *when* the session the history belongs to actually started. The chosen
shape is a small, readable recipe (archetype id, plus the fields D9 adds)
that the server rebuilds the query point from, deterministically, every call.

**The ramp is derived from the session on every request, not accumulated on
the client (D9).** This refines D1 rather than replacing it: the recipe
carries `since` — the session's start time, an integer timestamp — instead of
carrying the ramp value `t` itself. The rejected alternative was the more
obvious reading of D1: the client sends `t`, the server computes `t_next`,
the client stores and resends it next call. That pushes the actual advancing
rule into TypeScript, where the pure-Python test suite this project runs by
default (`tests/test_drift.py`) cannot exercise it at all, and an accumulated
value drifts out of true the moment a request is retried or a response is
dropped — nothing about the drop is self-correcting. Deriving `t` instead as
`f(archetype, session_start, plays)` — a pure function over the session's
`plays` rows since `since` — makes it idempotent and replayable: a play row
that hasn't posted yet simply isn't counted yet, and is picked up on the next
call with no double-counting, because nothing accumulates across calls at
all. The cost is one more query per block request (the session's play rows),
which the spec accepts as the price of the ramp being testable in Python
rather than trusted to run correctly in the client.

**An archetype has a centroid per leg, not one (D5).** A query point built
from a single space — the content leg alone was the obvious candidate, since
every seed track has a `content_vec` — leaves `lyrics_vec`, `feature_vec`,
`emotion_vec`, and the taste vector null on that point, and `rank()` reads a
null leg as absent. Every archetype session would then silently run the
five-or-six-leg fusion ADR 0003 and ADR 0004 built on exactly one leg, with
nothing surfacing that degradation anywhere. The other rejected alternative,
snapping the query point to the nearest real track, was dropped because it
makes drift move in jumps: a query point can then only ever sit where a song
already exists, which is a different kind of motion than a ramp is supposed
to produce. The decision is a centroid per leg the seed set actually carries
— the seed songs already have lyrics and taste vectors computed, so this
costs nothing beyond the mean itself. A leg absent from every seed (an
archetype built entirely from instrumentals has no lyrics centroid) stays
absent on the query point, which is the same null-leg contract `rank()`
already has for `feature_vec` and `taste_sim` — not a new case to add to it.

**Hold and lead are one mechanism, parameterised, not two code paths (D6).**
Situational lists (hold) and emotional lists (lead) are the same maths with
different constants: a cone — a minimum similarity, per leg — that the
query point may not exceed on any one step, measured against **the current
point on the path**, not the starting centroid, so the cone travels with the
ramp rather than staying pinned to where the session began. Similarity is
cosine for every leg except `emotion_vec`, which uses the bounded-VA-plane
distance similarity `muse.reco.emotion_sim` defines (ADR 0003), rescaled onto
the same [0, 1] range so one constant means one threshold across the fusion
rather than two units wearing the same name. The two are not the same
geometric tightness — `CONE_HOLD = 0.75` is a 41.4° cone on a cosine leg but
a 0.707-radius disc on the emotion leg — and D11 leaves that gap untuned.
`hold` is a ramp
step fixed at zero with a tight cone (`CONE_HOLD`); `lead` is a non-zero step
toward the archetype's `target_id` with a loose cone (`CONE_LEAD`). Two
rejected alternatives: a hard similarity filter on the candidate set, dropped
because it risks emptying the candidate pool on a library this size and
firing the relaxation ladder on every block; and freezing the query point
outright in hold mode, dropped because it forbids skips from steering a hold
session at all — and a car session, the canonical hold case, is exactly where
skipping is common. One mechanism means one set of tests and one place the
two behaviors can't drift apart from each other by accident.

**A new route beside the old one (D7).** `GET /api/radio/block` is new;
`GET /api/recommend/{track_rowid}` is untouched. The rejected alternative was
making the rowid path parameter on the existing route optional, so one route
served both track-seeded and archetype-seeded radio. That was dropped because
a 404 on that route already means one specific thing — an unknown or
unplayable `track_rowid` — and a 404 ambiguity on this route is a bug this
project has already paid to fix once; overloading the same status for "no
archetype either" reopens it. A second rejected alternative, the same new
route as a `POST`, was dropped because a growable request body buys nothing
for a call that reads only query parameters and writes nothing but the offers
row the existing route already writes. The two routes share `recommend_for()`
— which is generalised so its seed may be a rowid or a prepared query dict —
and the old route's behaviour must not change; `tests/test_radio_block.py`
asserts exactly that.

**Ramp steps are not a second event-weighting scheme (D12).** ADR 0003 is
explicit that there is exactly one scheme for turning a play span into a
weight: the skip/full_listen/replay/thumbs decay that trains the taste
embedding. A reader who sees the ramp move on "full listen" and "early skip"
could reasonably conclude a second, ramp-specific scheme was introduced
alongside it. It was not. The ramp calls `classify_play` —
**`muse/library/plays.py`** (re-exported at `muse/library/__init__.py:14`,
and what `muse/feedback.py` itself imports) — and maps its four existing
outcomes onto ramp steps:
`full_listen` advances `t` by `STEP_FULL`; `skip` (`classify_play`'s early-exit
class, `reached_ms / duration_ms < SKIP_RATIO`) retreats `t` by
`STEP_SKIP_EARLY`; `partial` (a later skip) leaves `t` unchanged; `unknown`
(a `lost` closure, an open span, or a track with no known duration — ADR 0001
again) is not counted at all, in either direction, and is not part of the
window the ramp derives `t` from. The two rejected alternatives were reading
`ended_reason` directly and defining the ramp's own skip/full-listen
thresholds (a second definition of "skip" in a codebase that exists in part
because ADR 0001 refuses to interpret a play span in two places), and reusing
`feedback_score`'s own numeric weights as ramp steps (coupling the ramp's feel
to weights tuned for training the taste embedding, a different job with no
reason to move together). `muse/drift.py` consumes `classify_play`'s output;
it does not reimplement or extend it.

## Held-out recall

Not yet measured. `scripts/archetype_recall.py` (D8) holds back three
user-sourced seeds per archetype and reports recall at 5, 10, and 20 for the
archetype query point against a plain track-seeded query point — run by hand
against the real library once archetypes exist to hold seeds back from. This
section is the place those numbers land when they do; until then, whether
archetype-seeded radio beats plain content-nearest radio is an open question,
not a claim this ADR makes.

## Consequences

**Good.** The recipe stays small, human-readable, and replayable from a log
line; a bad block can be diagnosed and reproduced the same way a bad
`rank()` call already is. The ramp is a pure function of data already in
`plays`, so it is self-correcting after a dropped response with no client-side
bookkeeping to get wrong. One cone mechanism, not two, means `hold` and `lead`
cannot silently diverge in behavior the way two independent code paths would
invite. Reusing `classify_play` keeps ADR 0003's one-scheme rule intact
instead of growing a second, parallel notion of "skip" for the ramp alone.

**Bad.** A block request costs one more query than a track-seeded
recommendation: the session's `plays` rows since `since`, read fresh on every
call. The feel constants (`SETTLE_SONGS`, `STEP_FULL`, `STEP_SKIP_EARLY`,
`ALPHA`, `BETA`, `CONE_HOLD`, `CONE_LEAD` — D11) are assumed, not fit; no
listening history exists yet to tune them against, and they are expected to
be wrong at their starting values. The held-out recall numbers above do not
exist yet, so the central question this feature is meant to answer —
whether an archetype centroid beats a plain content-nearest query point — is
still open.

**Binding.** `rank()` gains no new leg and no new event-weighting scheme from
this work; the ramp is a consumer of `classify_play`'s existing classes, never
a second definition of them. `muse/drift.py` holds `muse/reco.py`'s purity
contract — `math` only, no `Connection`, testable and replaceable without a
database — and a future change that reaches for one there breaks the seam
this ADR and ADR 0003 both depend on, even if the tests still happen to pass.
A `lead` archetype whose target is missing or hidden degrades to `hold` rather
than refusing a session the listener already started, matching this project's
general rule (`PRODUCT.md`, Design Principle 4) of degrading in quality, never
in availability.
