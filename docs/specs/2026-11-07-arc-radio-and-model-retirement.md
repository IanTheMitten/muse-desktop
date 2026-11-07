# Technical spec - Arc radio and the retirement of MiniLM and the emotion models

> Status: in progress. Feed to /autopilot-plan once ready - it verifies this line and refuses a spec that isn't "ready for planning".

## Source

No PRD. The goal as the owner stated it on 2026-11-07, in conversation:

- Radio should run on the arc engine (Laya lyric tags for theme and darkness),
  not on `/api/recommend` and its MiniLM lyrics leg. ADR 0009 already took the
  lyrics leg off the arc path; the UI's Radio never moved.
- MiniLM leaves the app, and so do both emotion models (audeering wav2vec2
  `audio_va`, j-hartmann distilroberta `lyrics_va`), because Laya's tags carry
  the lyric emotion and mood.
- The README (PR #19) is then updated to describe one Laya-based path.

Constraints carried from the repo: the torch rule (only the worker imports
ML libraries), `muse/reco.py` and `muse/arc.py` stay pure, `plays` is never
touched (ADR 0001), migrations are append-only, and recommendation changes
are measured before and after on the owner's library (CLAUDE.md).

## Architecture

After this change there is one recommendation engine in the UI's path:

- **Radio** (`web/src/player/PlayerContext.tsx`) asks `arc_engine` for its next
  picks: `GET /api/arc/{radio_seed}?mode=radio&pos=..&recent=..&exclude=..&lift=..`
  with no mood, `POST /api/arc` (`mode: "radio"`, spec from `toArcSpec`) with
  one. The seed is the session's `radio_seed`; `pos` counts arc picks already
  placed and is persisted with the player state.
- **Make a playlist** already uses `POST /api/arc` (`mode: "playlist"`); it is
  unchanged except that its `sound` now reads CLAP energy.
- **`/api/recommend`** stays as a debug and `reco_eval` surface with three legs
  (audio CLAP+MERT, features, taste). No UI caller.
- **Legs left in `rank()`:** `w_audio` 0.35, `w_feat` 0.15, `w_taste` 0.20.
  `w_lyr` and `w_emo` are deleted, not zeroed. `renormalize` already rescales
  over the legs present.
- **Energy** (the mood picker's calm / mid / energetic, the arc's `sound`
  cost) comes from CLAP: each track's existing `audio_vec` scored against two
  cached text-prompt vectors, turned into a library percentile.
- **Worker ops after:** `hello`, `clap_audio`, `clap_text`, `mert`, `laya`,
  `align`. The `minilm`, `audio_va` and `lyrics_va` ops, their encoders and
  their weights are removed.
- **Embed batch** keeps CLAP `audio_vec`, `feature_vec` and the
  `lyrics_hash` write (lyric-tag staleness depends on it); it stops encoding
  lyrics and emotion.

## Decisions

### Decision: What drives Radio
- **Status:** Accepted
- **Context:** The UI's Radio calls `/api/recommend` (MiniLM lyrics leg 0.25). The arc engine's radio mode exists (`GET /api/arc/{seed}?mode=radio`) but has no UI caller. The owner wants one Laya-based engine.
- **Options considered:** (a) switch Radio to arc radio mode; (b) keep `/recommend` for Radio and only update the README.
- **Decision:** (a). The owner picked it.
- **Consequences:** ADR 0009 recorded hit@10 0.111 with the lyrics leg against 0.095 without it on the 2026-10-10 replay, so Radio's continuity may measure slightly worse; the quality gate below decides whether that is acceptable. A seed with no lyric tags (instrumentals) already gets plain sonic continuity (`PLAIN_LADDER`) with `reason` set, so no fallback to `/recommend` is needed.

### Decision: Radio session semantics
- **Status:** Accepted
- **Context:** The arc curve is recomputed from `pos`; the previous track is `recent[0]` only when `pos > 0` (`arc_engine.py:176`). Anchoring on the queue tail with `pos=0` would restart the curve every call.
- **Options considered:** (a) seed = `radio_seed`, client keeps an incrementing `pos`, persisted in `player_state`; (b) seed = queue tail, `pos=0` each call.
- **Decision:** (a). A new session (new `radio_seed`) resets `pos` to 0. Each accepted pick increments `pos` by one. The mood re-rank (8 lookahead picks) uses the same seed and the current `pos`.
- **Consequences:** `player_state` gains `radio_pos INTEGER NOT NULL DEFAULT 0` and `radio_lift INTEGER NOT NULL DEFAULT 0` in migration 23; `muse/library/state.py` and the UI's state sync carry both.

### Decision: Radio arc shape
- **Status:** Accepted
- **Context:** The engine defaults to `lift=1` (drift toward darkness 0.25 over `RADIO_HORIZON` 30). Today's Radio has no curve.
- **Options considered:** steady (lift off); lift by default; steady plus a Lift toggle.
- **Decision:** Steady by default (`lift=0`) with a Lift toggle on the Muse page, beside the mood picker. The owner picked it. The toggle is stored as `radio_lift` and sent on every radio call.
- **Consequences:** One new control (a labelled switch, keyboard-operable, state carried by text and not colour alone, per PRODUCT.md accessibility).

### Decision: What `/api/recommend` becomes
- **Status:** Accepted
- **Context:** After the switch nothing in the UI calls it; `reco_eval` still measures through it.
- **Options considered:** keep and slim; retire.
- **Decision:** Keep, slimmed: drop the `w_lyr` and `w_emo` query params and the `mood=` param. `mood_target` in `muse/recommend.py` is deleted. The owner picked it.
- **Consequences:** `reco_eval` keeps working with three legs; its `_no_lyrics` variant is deleted.

### Decision: Energy from CLAP
- **Status:** Accepted
- **Context:** The mood picker's `sound` and the arc's `sound_gap` read `audio_va` arousal. Laya tags describe the words, not how energetic the audio is, and instrumentals have no tags.
- **Options considered:** CLAP zero-shot; keep `audio_va`; derive from tag emotion labels; drop the axis.
- **Decision:** CLAP zero-shot. The owner picked it.
- **Consequences:** Works on every track with an `audio_vec`, instrumentals included. Quality depends on prompt wording; the energy check in the quality gate guards it.

### Decision: How CLAP energy is computed and stored
- **Status:** Accepted
- **Context:** The arc reads arousal per track at plan time (`library_embeddings.audio_arousal`). `SOUND_BANDS` in `muse/arc.py` is on a [-1, 1] scale with cut points 0.25 and 0.57 tuned to `audio_va`.
- **Options considered:** (a) cache the two prompt vectors, compute per track at read time, rank-normalise; (b) a per-track energy column filled at embed time.
- **Decision:** (a). Prompts: `"calm, quiet, gentle music"` and `"energetic, loud, intense music"` (constants in `muse/energy.py`, a new pure module). The two text vectors are fetched once through the worker's `clap_text` op and cached in a new `prompt_vectors(audio_model, name, vec)` table (migration 23), refetched when `audio_model` changes. Raw energy is `dot(audio_vec, energetic) - dot(audio_vec, calm)`; the read path turns it into a library percentile `p` and returns `2p - 1`, so energy stays on [-1, 1]. `SOUND_BANDS` becomes even thirds: calm (-1, -1/3), mid (-1/3, 1/3), energetic (1/3, 1). The owner accepted the recommendation.
- **Consequences:** No backfill pass; changing the prompts needs no re-embed. A library with no cached prompt vectors (worker absent) returns no energy, and `sound_gap` already treats unknown as 0. The percentile is relative to the owner's own library, which matches how the picker is used.

### Decision: Stored data and dead code
- **Status:** Accepted
- **Context:** Migrations are append-only. SQLite in the bundled Python supports `DROP COLUMN` (3.35+). `muse/library/emotion.py`, `insights.py`'s emotion map and `/api/mixes/{band}` read `emotion_vec`; the UI renders none of them.
- **Options considered:** drop the columns and the dead code; leave the columns dormant.
- **Decision:** Migration 23 drops `lyrics_vec`, `lyrics_model`, `audio_va`, `lyrics_va`, `emotion_vec`, `audio_va_model`, `lyrics_va_model` from `embeddings` and keeps `lyrics_hash`. `library/emotion.py`, `/api/mixes`, the insights emotion map, `mixes` and `stats.map` in the home payload, and their UI types are deleted. The owner accepted the recommendation.
- **Consequences:** Irreversible, but the models are gone, so the data could not be refreshed. `embeddings.pending()` must stop gating on `audio_va` and gate on `audio_vec IS NULL` (or a stale `audio_model`), or every new track re-embeds on every launch. The calibration cache key (`vector_fingerprint`) must change with the migration so cached knots for removed legs are not reused.

### Decision: Retired weights on disk
- **Status:** Accepted
- **Context:** About 1.08 GB (`weights/minilm`, `weights/audio_va`, `weights/lyrics_va`) stays on upgraded installs.
- **Options considered:** prune on upgrade; leave.
- **Decision:** When `muse/smart.py` installs a manifest with a higher `version`, it deletes folders directly under `models/weights/` that the new manifest does not list. It never deletes outside `models/weights/`. The manifest `version` is bumped.
- **Consequences:** Needs a test that a folder outside `weights/` and a listed folder both survive.

### Decision: Quality gate
- **Status:** Accepted
- **Context:** No library exists on the development machine; the harnesses need the owner's library on the server.
- **Options considered:** energy check plus `arc_eval`; `arc_eval` only; listening only.
- **Decision:** Two numeric gates, run by the owner on the server:
  1. Before any column is dropped: a one-off read-only script (`scripts/energy_check.py`) computes CLAP energy and Spearman-correlates it with the stored `audio_va` arousal. Gate: rho of at least 0.5.
  2. `uv run python -m muse.arc_eval` on the current main and on the branch. Gate: theme hold and curve error no worse than the baseline run.
  `uv run python -m muse.reco_eval` runs on both too and is reported, not gating.
- **Consequences:** The plan must order the energy check before migration 23 ships, and the branch must not merge until the owner posts both results.

### Decision: Worker compatibility
- **Status:** Assumed - not confirmed
- **Context:** Removing ops changes the worker's `hello` op list. A released worker with extra ops is harmless; the sidecar simply stops calling them.
- **Options considered:** remove the ops from source now and ship the next worker build without them; keep them in the worker until a later release.
- **Decision:** Remove from source now. No compatibility shim: the sidecar only calls ops it still uses, so an older worker keeps working.
- **Consequences:** CI (`package.yml`) stops fetching MiniLM and stops smoke-testing the `minilm` op. `sentence-transformers` leaves `pyproject.toml` (MiniLM is its only user per `uv.lock`).

## Data flow

1. **Radio start:** the listener starts Radio from a song. The client sets `radio_seed`, `radio_pos = 0`, keeps `radio_lift`, and persists them through the player-state sync.
2. **Radio fill:** when the queue runs low, the client calls `GET /api/arc/{radio_seed}?mode=radio&n=1&pos=<radio_pos>&recent=<last 10 played>&exclude=<seen, last 300>&lift=<0|1>`. With a mood set it calls `POST /api/arc` with `{spec: toArcSpec(seed, mood), mode: "radio", n, pos, recent, exclude}`. It appends `results[0]`, stores its `reasons`, and increments `radio_pos`.
3. **Arc planning (server, synchronous):** `arc_engine.arc_block` plans `max(2n, 8)` steps by beam search over `arc.pick_cost` with weights `w_audio`, `w_feat`, `w_taste`; darkness and theme come from Laya tags; `sound` reads CLAP energy.
4. **Energy read (server, synchronous):** `library_embeddings.audio_energy` loads `audio_vec` rows and the cached prompt vectors, computes raw energy in pure code (`muse/energy.py`), ranks, maps to [-1, 1]. Cached per `vector_fingerprint` like calibration.
5. **Prompt cache fill (async, launch chain):** after the embed backfill, if `prompt_vectors` has no row for the current `audio_model` and the worker is installed, the chain calls `clap_text` twice and stores the vectors. Runs under `chain_lock`.
6. **Embed batch (async, launch chain):** encodes CLAP `audio_vec`, `feature_vec`, MERT content, and recomputes `lyrics_hash` when lyrics change. It no longer calls `minilm`, `audio_va` or `lyrics_va`.
7. **Lyric tagging:** unchanged, still keyed on `embeddings.lyrics_hash`.
8. **Smart-features install:** a higher manifest `version` installs the new list and prunes unlisted folders under `models/weights/`.

## Error handling

- **Seed has no tags:** `200` with `reason = UNTAGGED_SEED` and plain sonic picks. The UI shows a quiet note ("This song has no lyric tags, so Radio follows its sound.") in the Up next area instead of nothing.
- **Seed has no vectors:** `404`, as today. The UI's existing "no embedding yet" copy becomes "Muse hasn't listened to this song yet."
- **Pool runs dry:** fewer than `n` results with `exhausted: true`. The UI shows the existing "Nothing left" state. The arc never relaxes the exclude set, so this happens sooner than with `/recommend`; the client does not retry with a smaller exclude set.
- **Unknown relaxation rung:** the UI's `Relaxation` type gains `filler`, loses `exclusion`, and an unknown rung renders no note rather than "undefined".
- **No prompt vectors** (worker absent or `clap_text` failed): energy is unknown, `sound` is ignored (gap 0), and the mood picker's energy row is hidden with the reason shown in Settings > Smart features. A `clap_text` failure is logged and retried at the next launch.
- **Migration 23 on a DB without the columns** (fresh install): drop only the columns that exist.
- **Prune failure** (file in use on Windows): log, leave the folder, retry at the next install.

## Testing strategy

- **Pure unit tests (red first):** `muse/energy.py` (raw score, percentile map, ties, a single track, an empty library), `arc.SOUND_BANDS` thirds, `reco.rank` with three legs, the `renormalize` contract unchanged.
- **DB tests** on the real temp `library.sqlite3` (`tests/conftest.py`): migration 23 (columns gone, `lyrics_hash` kept, `player_state.radio_pos` and `radio_lift` present, `prompt_vectors` created, idempotent on a fresh DB), `embeddings.pending()` with no `audio_va`, `audio_energy` with and without cached prompts, the `lyrics_hash` write without MiniLM, `state.py` round-trip of `radio_pos`/`radio_lift`.
- **API tests:** `/api/arc` radio mode with `lift=0` and `lift=1`, `/api/recommend` without `w_lyr`/`w_emo`/`mood` (422 on the removed params is not required; they are simply ignored), `/api/mixes` gone (404), the prompt-cache chain step with a stub worker.
- **Smart installer:** prune deletes unlisted folders under `weights/` only.
- **Worker:** `hello` op list; `tests/test_worker_main.py` and the slow model tests drop MiniLM and VA.
- **Web (vitest):** the radio fill sends `pos`/`lift`/`recent`/`exclude` and increments `pos`; `filler` and unknown rungs; the untagged-seed note.
- **E2E (Playwright):** start Radio on the demo-style fixture, see Up next fill, toggle Lift.
- **Out of scope here:** recommendation quality, which only the owner's server can measure (see the quality gate).
- **Regression:** the full `uv run pytest` and `npm test` stay green at every phase boundary.

## Tech stack

No new dependency. Python 3.12 + FastAPI sidecar, SQLite (migration 23 uses `ALTER TABLE ... DROP COLUMN`), React 19 UI, the existing worker with CLAP and Laya. `sentence-transformers` is removed from `pyproject.toml`.

## Open questions

- Deferred: exact UI copy for the untagged-seed note and the Lift switch label; design-brain's copy pass can settle it at plan time without changing behaviour.
- Deferred: the final prompt wording for CLAP energy; the energy check on the owner's server may call for a revision, which is a constant change in `muse/energy.py`.

## Provenance

- user-picked: what drives Radio; radio arc shape (steady plus Lift toggle); what `/api/recommend` becomes; energy from CLAP; process (spec, then autopilot-plan, then autopilot)
- recommended-and-accepted: radio session semantics; how CLAP energy is computed and stored; stored data and dead code; retired weights on disk; quality gate
- assumed: worker compatibility (remove ops from source now, no shim)
- overrides: none
- follow-ups outside this spec: README PR #19 gets one Laya-based diagram once this lands; ADRs 0003, 0004, 0008 and 0009 get superseding notes; CONTEXT.md glossary entries (encoder protocol, emotion vec, VA, mood target) and CLAUDE.md are updated in the same plan.
