# ADR 0006: The radio curator

- **Status:** Superseded by ADR 0009 (2026-10-17)
- **Date:** 2026-09-24
- **Slice:** 2 (Recommender)
- **Related:** `docs/adrs/0005-archetype-radio-and-the-drift-policy.md`,
  `docs/adrs/0003-emotion-and-taste-legs-in-the-recommender.md`,
  `docs/specs/2026-09-21-curator.md`

## Context

ADR 0005 built the archetype query point and its drift, and explicitly
deferred "the LLM curator" and "the archetype similarity profile" to their
own spec. `docs/specs/2026-09-21-curator.md` is that spec: a stage,
`muse/curator.py`, that sits after `recommend.radio_block()` and picks
twelve tracks in order from the fifty it retrieves, by asking a System One
decision model — Jev (hosted) or Laya (local), both reached through
`muse/systemone.py`'s one HTTP client — a `score` question per candidate.
This ADR is the record for two of that spec's decisions, the two a later
reader would otherwise undo without knowing why. It is not a restatement of
the spec's other decisions (D1, D3–D5, D7–D11), each of which stands on its
own.

## Decision

**Sequential rounds over one batched pass (D2).** The curator scores one
candidate track at a time, across twelve round trips per block, each round's
`state` carrying the prefix already chosen. The rejected alternative — one
call, fifty `score` questions, take the top twelve — is cheaper and is what
TypeSafe's own batching guidance recommends, but it cannot see the prefix:
it scores every candidate against the same, un-updated state, so it cannot
stop four flat `sad`-plus-`night` songs from landing back to back the way a
round that has already placed the first three can. The chosen shape costs
twelve round trips instead of one — survivable behind the existing prefetch
of block N+1 on both backends, including CPU-only Laya — in exchange for
`score` returning a full ranking each round, so a low-confidence round
degrades to the next-best candidate rather than to nothing.

**A stored profile table over per-request computation (D6).** `archetype_profiles`
holds one row per track per archetype — a score that is `reco.rank()` with
the archetype centroid as the query and the song as the only candidate —
computed by a backfill job and read, not recomputed, when the curator builds
a round's `state`. Computing the same two columns in play for each block's
fifty candidates, with no migration and nothing to invalidate, was the
recommendation; the owner chose the stored table over it, for readers that
do not exist yet in this codebase — browse-by-archetype, the recall script,
onboarding — that can read the same table the curator does instead of each
growing its own per-request version of the same math. The stored table buys
a real invalidation rule as its cost: a row goes stale on either of two
independent signals, a `seed_hash` mismatch (a seed added to the archetype)
or a `taste_stamp` mismatch (`MAX(trained_at)` across `taste_embeddings`,
which the nightly retrain rewrites wholesale into a fresh, unaligned ALS
basis, staling every column in the table at once rather than one).

## Consequences

**Binding.** `plays` is untouched by any of this — block-level logging lands
in its own `curator_blocks` table, keeping ADR 0001's rule that `plays`
holds physical fact intact. `muse/reco.py` and `muse/drift.py` keep the
purity contract ADR 0005 already binds them to: the curator opens no new
seam in either, calling `rank()` and the drift machinery as they already
stand rather than reaching past them for a connection. A curation win is a
win over ordering and never over recall — the curator can only reorder and
drop from the fifty candidates `radio_block()` retrieves, so a song
retrieval never returns cannot be curated in, no matter how the two
decisions above are tuned later.

**Good.** The sequential loop makes the mood-bending behaviour S6 asked for
possible at all — a batched pass structurally cannot see its own prefix.
The stored profile table gives every future per-archetype consumer, not
just the curator, one place to read a track's fit against an archetype
without recomputing it.

**Bad.** Twelve round trips per block is real latency and, on Jev, real
cost, paid on every block rather than once. The profile table is a second
piece of per-track state that can go stale in two independent ways and now
needs a real invalidation rule (`archetype_profiles.seed_hash`,
`archetype_profiles.taste_stamp`) to be trusted — a rule the cheaper,
per-request alternative would not have needed at all.
