# ADR 0001: The plays table stores physical fact, never interpretation

- **Status:** Accepted
- **Date:** 2026-08-10
- **Slice:** 1 (Player)
- **Related:** `docs/prds/2026-08-06-muse-player.md`, `docs/superpowers/specs/2026-08-06-spec-slice-1-player.md`

## Context

The whole point of slice 1 is to manufacture the training data that slices 2
and 4 consume. The recommender's quality is bounded by the quality of the
listening history it learns from, and that history is the only data in this
system that cannot be regenerated. The catalog mirrors can be re-derived, the
audio can be re-downloaded, embeddings can be recomputed — but a play that
happened in August and was recorded wrongly is gone.

The obvious design is to write the interpretation directly: a `skips` table, or
a `plays.was_skip` boolean set at write time by comparing `reached_ms` against
some threshold. This is what most listening-history schemas do.

The problem is that the threshold is a guess. "A skip is under 30 seconds" is
an invented number. So is 20%. So is any rule that tries to separate a genuine
rejection from a track cut short because the phone rang. Those thresholds will
be wrong, and they will need to be retuned once there is enough real history to
see what the distributions actually look like — which is precisely the moment
when re-deriving them from history becomes impossible, because history has
already been reduced to the answers the old threshold produced.

## Decision

`plays` records only what physically happened, at the resolution the player can
actually observe:

- `started_at`, `ended_at` — wall clock
- `reached_ms` — furthest position reached, monotonic, so a seek backwards
  never lowers it
- `duration_ms` — track length as of that play, denormalised so a later
  metadata correction cannot retroactively change what a past play meant
- `seek_count` — how much the listener fidgeted
- `ended_reason` — one of `track_end`, `user_next`, `user_prev`,
  `new_selection`, `pause_timeout`, `lost`
- `source` — where the play came from

There is no `was_skip`, no `is_full_listen`, no `replay` flag, no rating
implied by any of it.

`skip` / `full_listen` / `replay` are a **pure derivation** over those columns:

```
classify_play(reached_ms, duration_ms, ended_reason) -> str
```

The function has no I/O and no state. It is tested in slice 1 even though
nothing consumes it until slice 2, because writing it now proves the schema is
capable of producing the derivation at all.

`prev_track_rowid` and `session_id` are deliberately omitted for the same
reason: both are derivable from `started_at` ordering, and storing a derived
value freezes the definition of "session" at whatever gap threshold seemed
right on day one.

## Consequences

**Good.** Thresholds can be retuned at any point and the entire history
recomputed under the new rule. The classification lives in one pure function
that costs nothing to test and nothing to change. `ended_reason` distinguishes
"user pressed next" from "the browser tab died", so slice 2 can decide those
mean different things without slice 1 having had to guess.

**Bad.** Every consumer must call `classify_play()` rather than reading a
column, so "count my skips this month" is a computation, not a `WHERE` clause.
At the scale of one listener this does not matter; if it ever does, the answer
is a materialised view that can be dropped and rebuilt, never a stored column.

**Open.** What a `lost` span counts for is recorded but not decided. Slice 1
captures the reason faithfully; slice 2 decides whether an interrupted play is
evidence of anything. This is exactly the kind of question this ADR exists to
keep answerable.

**Binding.** No migration may add an interpretive column to `plays`, and no
code path may destroy or roll up play rows. This is the one table in the system
with no backup upstream of it.
