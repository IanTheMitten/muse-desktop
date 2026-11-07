# Plan - Arc radio, the retirement of MiniLM and the emotion models, and the missing-lyrics notice

Spec: `docs/specs/2026-11-07-arc-radio-and-model-retirement.md` (handoff lint exit 0, one warn:
no PRD). Every Decision there is a premise, cited `spec:<line>`. Plus one owner addition made at
plan time (2026-11-07): a notice asking the listener to add lyrics for songs that have none.

Run with `/autopilot docs/plans/2026-11-07-arc-radio-and-model-retirement.md` in a Sonnet session,
on branch `spec/arc-radio-model-retirement` (forked from `main` at d50fc02).

**Merge gate (spec:106-114).** The branch does not merge until the owner posts both results from
the runbook Task 21 writes: `scripts/energy_check.py` rho >= 0.5, and `muse.arc_eval` theme hold
and curve error no worse than on `main`. Autopilot finishes the plan; the owner runs the gate.

## Assumptions (decided here)

1. **Owner override of spec:64-69 (Radio arc shape).** The owner answered at plan time: "lift
   should be the default and the user should disable it if they want to". So `radio_lift`
   defaults to 1, the Lift switch starts On, and migration 23 adds
   `radio_lift INTEGER NOT NULL DEFAULT 1` (spec:62 said default 0).
2. **Lift with a mood set.** POST `/api/arc` takes no `lift` (spec:126). Lift rides in the spec
   instead: `toArcSpec(p, seed, n, lift, energy)` with `lift` true sets
   `goal.darkness = Math.min(darkness, 0.25)` and `shape: "gradual"`; false keeps today's steady
   spec. Make a playlist calls it with `lift` false, so playlists are unchanged.
3. **Deferred copy (spec:162)** is settled in the Design sections below.
4. **Deferred prompt wording (spec:163)** stays as the spec's two prompts; a revision is a
   constant change in `muse/energy.py`.
5. **Energy unavailable (spec:140).** The mood map cannot drop one axis, so "hide the energy row"
   becomes: `toArcSpec` sends `sound: "any"` and the Muse page shows the energy note (Design: Muse).
   Readiness is `energy_ready` on `GET /api/smart`.
6. **Percentile definition.** Average rank with ties averaged, `p = rank0 / (n - 1)`, value
   `2p - 1`; one track maps to 0.0, none to `{}`.
7. **Current audio model** is the most common `embeddings.audio_model` among rows with
   `audio_vec IS NOT NULL`, ties broken by name ascending. Prompt vectors are cached per
   `audio_model`; energy reads only the current model's rows.
8. **`embeddings.pending()`** gates on `audio_vec IS NULL` only (spec:97 offered "or a stale
   `audio_model`"; there is no notion of "current model" before the first embed, so not taken).
9. **Missing lyrics** = `tracks.status = 'done' AND tracks.lyrics_path IS NULL AND
   tracks.is_instrumental = 0 AND lyrics_state.state = 'not_found'` (owner: "lookup failed, not
   instrumental"). "Song has no words" upserts `lyrics_state.state = 'declared_instrumental'`
   (owner pick); adding lyrics already sets `manual` (`muse/library/lyrics_edit.py:54-57`).
10. **The list is its own view** at `/library/missing-lyrics`, reached from the Home card, a link
    in Library > Songs, and the Radio untagged note. A filter inside Library would tangle with its
    search and sort state.
11. **Dashboard** loses `mixes`, `stats.map`, `stats.map_source`, `stats.map_centre` and
    `stats.measured` (all mood-surface fields, spec:96) and gains `missing_lyrics: int`.
    `muse/library/features.py` band helpers stay (out of scope; still used by `sounds_like`).
12. **Manifest:** weights for `minilm`, `audio_va`, `lyrics_va` leave `muse/smart_manifest.json`,
    `version` goes 2 to 3, `workers` entries are untouched (old worker with extra ops is harmless,
    spec:120). A new worker release is the owner's step.
13. **`EmbedResult.lyrics_reembedded`** keeps its name (now counts `lyrics_hash` refreshes) to
    avoid test churn.

## Premises

- Radio uses `/api/recommend` via `getRecommendations` in two places — verified: `web/src/player/PlayerContext.tsx:501`, `web/src/player/PlayerContext.tsx:641`
- GET `/api/arc/{seed}` already takes `pos`, `lift` (default 1), `recent`, `exclude`; POST takes `spec, mode, n, pos, recent, exclude` — verified: `muse/api.py:1385-1393`, `muse/api.py:136-144`
- Arc reads arousal from `audio_va` — verified: `muse/arc_engine.py:126`, `muse/library/embeddings.py:230-241`
- `SOUND_BANDS` is `calm (-1,0.25) mid (0.25,0.57) energetic (0.57,1)` — verified: `muse/arc.py:39`
- Arc ladder rung names are `filler`, `freshness`, `artist_spacing` (no `exclusion`) — verified: `muse/arc.py:269-281`
- Latest migration is 22; `tests/test_m022_schema.py:22` pins `migrate(raw) == 22`; `tests/test_migrations.py:84-89` pins the embeddings column set — verified: `muse/library/schema.py:701-726`
- Bundled SQLite supports DROP COLUMN — verified: `uv run python -c "import sqlite3;print(sqlite3.sqlite_version)"` printed `3.53.1`; no index on embeddings columns (`grep -n "INDEX.*embeddings" muse/library/schema.py` empty)
- `embeddings.upsert` takes `lyrics_vec`/`lyrics_model`/VA kwargs and 14 test files call it with them — verified: `muse/library/embeddings.py:76-92`, `git grep -lE "embeddings\.upsert" tests`
- `create_app` takes `lyrics_encoder`, `audio_va_encoder`, `lyrics_va_encoder`; five test files pass them — verified: `muse/api.py:436-446`, `git grep -c "lyrics_encoder=" tests`
- Worker ops list pinned in `tests/test_worker_main.py:64` — verified
- `sentence-transformers` is used only by `muse` (embed extra) — verified: `uv.lock:570`, `uv.lock:599`
- CI fetches `minilm` and smoke-tests the `minilm` op — verified: `.github/workflows/package.yml:85-86`
- `/api/smart` body pinned by exact dict — verified: `tests/test_api_smart.py:101-102`
- `lyrics_edit.write` nulls `lyrics_vec`/`lyrics_model` — verified: `muse/library/lyrics_edit.py:47-51`
- Track carries `has_lyrics` and `is_instrumental`; `lyrics_state.state` values include `not_found`, `declared_instrumental` — verified: `muse/library/queries.py:25-27`, `muse/lyrics_lrclib.py:177-181`
- `npm test` is vitest over pure modules only; Playwright e2e boots `scripts/e2e_server.py` with no vectors — verified: `web/package.json:9`, `scripts/e2e_server.py:85-91`
- Raw read-only sqlite in harnesses is precedent — verified: `muse/reco_eval.py:200`
- Script import from tests is precedent — verified: `tests/test_updater_manifest.py:9-13`
- Baseline green: `uv run pytest` 1277 passed, 1 skipped; `npm test` 42 passed — verified: run 2026-11-07
- Design ledger says cohere with the existing Muse system, no rotation — verified: `.design-ledger.json` entry 2 `notes`

## Design: Muse
READ: Reading this as: a control addition to the Muse product screen for its one owner, plain record-wash language, leaning toward the existing glass panel-card system with zero new visual vocabulary beyond one switch.
DIALS: VARIANCE 3 / MOTION 3 / DENSITY 4
PALETTE: Restrained (existing tokens only): `--accent` (from cover, marks On), `--hairline` (switch track Off), `--text` (thumb Off), `--on-accent` (thumb On), `--text-2` (notes), `--glass` (panel).
TYPE: display Newsreader Variable (`--serif`, unchanged headings) / body Figtree Variable (`--font`)
SIGNATURE: the Lift switch: a 44x26 pill whose thumb slides 18px with `transform` only, the word On or Off printed beside it so state never rides on colour.
ANCHORS: Apple Music settings switch (pill track, thumb travel, label left of control); Plexamp "Sweet Fades" toggle (a one-line plain description under a playback switch); this app's `.from` row in `web/src/views/Muse.tsx` (placement rhythm inside the steer card).
VARIETY: Same product as ledger entry 2026-10-28 (`.design-ledger.json`); ledger note says cohere, so no rotation: component-scale addition, no axis changes.
CONTENT: Switch label `Lift`; state text `On` / `Off`; description `Radio drifts from heavier songs toward lighter ones over about 30 songs.` Untagged note `This song has no lyric tags, so Radio follows its sound.` plus, when the seed track has no lyrics, a link-button `Add lyrics` (opens `LyricsEditDialog` for the seed). 404 note `Muse hasn't listened to this song yet.` Energy note (when `energy_ready` is false) `Energy needs smart features, so Muse matches only how dark or bright a mood is.` with link `Settings` to `/settings`. Relaxation note prefix `Running low:` then joined rung copy (colon, never a dash). Settings licence line: `Uses MERT, free for non-commercial use only, plus CLAP, Laya, and lyric aligners (MMS-FA, free for non-commercial use only, and schufo).` Settings energy line under the status row: `Energy: ready` or `Energy: not measured yet`.
FORMAT: production
TARGET: React 19 + Vite at `web/src/views/Muse.tsx`, styles in `web/src/styles/muse.css`, Settings copy in `web/src/views/Settings.tsx`
CONSTRAINTS: WCAG AA; switch is a `<button role="switch" aria-checked>` with visible label, keyboard Space/Enter, `:focus-visible` outline `2px solid var(--text)` offset 4px, 44px min hit height; disabled state opacity .45; `prefers-reduced-motion` removes the thumb transition; no em dash in copy; no new colours or radii beyond `--r-pill`; works at 320px width.

## Design: Missing lyrics
READ: Reading this as: a quiet maintenance prompt plus a work list for the owner's own library, plain record-wash language, leaning toward the existing Home shelf and Library song-row system.
DIALS: VARIANCE 3 / MOTION 2 / DENSITY 5
PALETTE: Restrained (existing tokens only): `--glass` (card), `--hairline` (card edge), `--text`, `--text-2`, `--accent` (primary `Add lyrics` button only), `--on-accent`.
TYPE: display Newsreader Variable (card heading, view title via `Screen`) / body Figtree Variable
SIGNATURE: the Home card's count set in Newsreader at `--t-xl` ("23 songs") with the reason as one plain sentence under it; a fact, not an alert.
ANCHORS: Apple Music "Library > Songs" row (art, title, artist, trailing actions); Plexamp's quiet "missing metadata" hint (stated once, no badge, no red); this app's `.resume` card in `web/src/views/Home.tsx` (glass card rhythm).
VARIETY: Same product as ledger entry 2026-10-28; cohere, no rotation.
CONTENT: Home card (only when `missing_lyrics > 0`): eyebrow `Lyrics`; heading `{n} songs have no lyrics` (`1 song has no lyrics` when n is 1); body `Muse looked for their words and found none. Add them so Radio can follow what a song is about, not only how it sounds.`; primary link-button `Add lyrics` to `/library/missing-lyrics`. Library > Songs toolbar link (when n > 0): `{n} missing lyrics`. View title `Missing lyrics`, subtitle `{n} songs` / `1 song`; lede `Muse looked these up and found nothing. Paste the words, or mark a song as having none.`; each row: `TrackRow` with trailing `Add lyrics` (`btn`, opens `LyricsEditDialog`) and `No words` (`link-btn`, aria-label `Song has no words: {title}`); after No words a `role="status"` line `Marked {title} as having no words.`; empty state `Every song has lyrics or is marked as having none.` with link `Back to the library`; load error uses `Screen`'s `error`.
FORMAT: production
TARGET: React 19 + Vite: `web/src/views/Home.tsx`, `web/src/views/MissingLyrics.tsx` (new), `web/src/views/Library.tsx`, `web/src/App.tsx`, styles `web/src/styles/home.css` and `web/src/styles/library.css`
CONSTRAINTS: WCAG AA; all buttons 44px min height, visible `:focus-visible`; loading state uses `Screen loading`; no badge counts in the nav, no red, nothing exclaims (PRODUCT.md voice); no em dash; rows wrap actions under the title below 420px, no horizontal scroll at 320px; no animation added.

## Phase 1 - CLAP energy, pure, and the energy check

### Task 1: Write the energy module tests [test]
- [ ] status
**Objective:** `tests/test_energy.py` pins `muse/energy.py`'s math and purity, red against the current tree.
**Write-scope:** `tests/test_energy.py` (new)
**Read-context:** `tests/test_arc_purity.py:1-20`, `muse/reco.py:133-143`
**Anchors:** `def cosine_sim(a: list[float], b: list[float]) -> float:` @ `muse/reco.py`
**Steps:** 1. Import `muse.energy as energy` and `pytest`. 2. Cases: `raw_energy([1,0],[1,0],[0,1]) == -1.0`; `raw_energy([0,1],[1,0],[0,1]) == 1.0`; `to_unit({}) == {}`; `to_unit({7: 3.2}) == {7: 0.0}`; `to_unit({1: 0.1, 2: 0.5, 3: 0.9}) == {1: -1.0, 2: 0.0, 3: 1.0}`; ties: `to_unit({1: 0.2, 2: 0.2, 3: 0.9})` gives `{1: -0.5, 2: -0.5, 3: 1.0}` (approx); `spearman([1,2,3],[10,20,30]) == approx(1.0)`; `spearman([1,2,3],[3,2,1]) == approx(-1.0)`; `spearman([1],[1]) is None`; `spearman([1,1,1],[1,2,3]) is None`; constants `energy.CALM_PROMPT == "calm, quiet, gentle music"`, `energy.ENERGETIC_PROMPT == "energetic, loud, intense music"`, `energy.CALM == "calm"`, `energy.ENERGETIC == "energetic"`, `energy.GATE_RHO == 0.5`. 3. Purity: `inspect.getsource(energy)` contains no `sqlite3`, no `Connection`, and every `import` line is `import math`.
**New symbols:** none
**Verify:** `uv run pytest tests/test_energy.py` — expected: collection error `ModuleNotFoundError: No module named 'muse.energy'`.
**Forbidden:** no source files; no other test files.
**Depends:** none
**Why:** spec:146 asks pure tests red first; Task 2 turns them green.

### Task 2: Add the pure energy module
- [ ] status
**Objective:** `muse/energy.py` exists, imports only `math`, and Task 1's tests pass.
**Write-scope:** `muse/energy.py` (new)
**Read-context:** `tests/test_energy.py` (from Task 1), `docs/specs/2026-11-07-arc-radio-and-model-retirement.md:85-90`
**Anchors:** `def cosine_sim(a: list[float], b: list[float]) -> float:` @ `muse/reco.py`
**Steps:** 1. Module docstring cites spec Decision "How CLAP energy is computed and stored". 2. Constants exactly as Task 1 lists. 3. `raw_energy` = `dot(audio_vec, energetic) - dot(audio_vec, calm)`. 4. `_ranks(values)` returns 0-based average ranks (ties averaged). 5. `to_unit`: empty gives `{}`; one key gives `{k: 0.0}`; else `2 * rank / (n - 1) - 1` per key. 6. `spearman`: None when `len < 2` or either side has zero rank variance; else Pearson over `_ranks`.
**New symbols:** `CALM: str`, `ENERGETIC: str`, `CALM_PROMPT: str`, `ENERGETIC_PROMPT: str`, `GATE_RHO: float`, `def raw_energy(audio_vec: list[float], calm: list[float], energetic: list[float]) -> float`, `def to_unit(raw: dict[int, float]) -> dict[int, float]`, `def spearman(xs: list[float], ys: list[float]) -> float | None`
**Verify:** `uv run pytest tests/test_energy.py` — expected: all pass.
**Forbidden:** any import but `math`; no edits to tests.
**Depends:** Task 1
**Why:** Energy must stay pure like `muse/reco.py`/`muse/arc.py` so the read path is the only impure half.

### Task 3: Add the one-off energy check script
- [ ] status
**Objective:** `scripts/energy_check.py` prints CLAP-energy vs stored `audio_va` arousal Spearman rho on a pre-migration library, read-only.
**Write-scope:** `scripts/energy_check.py` (new), `tests/test_energy_check.py` (new)
**Read-context:** `muse/reco_eval.py:196-206`, `muse/worker.py:29-60`, `tests/test_updater_manifest.py:1-14`, `tests/conftest.py:88-95`
**Anchors:** `def worker_command(models_dir: Path, override: str | None = None) -> list[str]:` @ `muse/worker.py`
`def unpack_vector(blob: bytes | None) -> list[float] | None:` @ `muse/embed.py`
`def spearman(xs: list[float], ys: list[float]) -> float | None` @ `muse/energy.py` (from Task 2)
**Steps:** 1. `check(conn, encode_text) -> dict`: SQL `SELECT e.audio_vec, e.audio_va FROM embeddings e JOIN tracks t USING (track_rowid) WHERE t.status = 'done' AND e.audio_vec IS NOT NULL AND e.audio_va IS NOT NULL ORDER BY e.track_rowid`; calm/energetic = `encode_text(energy.CALM_PROMPT)`, `encode_text(energy.ENERGETIC_PROMPT)`; raw energy per row; arousal = `unpack_vector(audio_va)[1]`; return `{"n": n, "rho": energy.spearman(raws, arousals), "passed": rho is not None and rho >= energy.GATE_RHO}`. 2. `main(argv=None) -> int`: args `--db` (default `Config.from_env().library_db`) and `--worker-cmd` (default `os.environ.get("MUSE_WORKER_CMD")`); open `sqlite3.connect(f"file:{path}?mode=ro", uri=True)` with `row_factory = sqlite3.Row`; `WorkerClient(worker_command(Config.from_env().models_dir, args.worker_cmd))`, `begin()`, `encode_text = lambda t: client.request("clap_text", text=t)`; print `n=<n>`, `rho=<rho:.3f or none>`, then `PASS` or `FAIL (gate: rho >= 0.5)`; `client.stop()` and close in `finally`; return 0 on pass else 1. 3. Tests (import via `spec_from_file_location`): migrated temp DB (`muse.library.connect`), 4 tracks via `insert_track`, raw `INSERT INTO embeddings (track_rowid, audio_vec, audio_va, indexed_at)` with `pack_vector`; fake `encode_text` returning `[1,0]` for calm and `[0,1]` for energetic; audio vecs whose energy order matches arousal order gives `rho == approx(1.0)` and `passed`; reversed gives `rho == approx(-1.0)`, not passed; a row without `audio_va` is skipped (`n` counts only paired rows).
**New symbols:** `def check(conn, encode_text) -> dict`, `def main(argv=None) -> int`
**Verify:** `uv run pytest tests/test_energy_check.py` — expected: all pass.
**Forbidden:** no writes to the DB (read-only URI); no `muse.library.connect` in the script (it migrates); no new deps.
**Depends:** Task 2
**Why:** Gate 1 (spec:111) must run on the owner's library before migration 23 drops `audio_va`.

## Phase 2 - Take the lyrics and emotion legs out of the sidecar (columns still exist)

### Task 4: Cut the lyrics and emotion legs from the recommender
- [ ] status
**Objective:** `rank()` fuses three legs (`w_audio` 0.35, `w_feat` 0.15, `w_taste` 0.20); `/api/recommend` ignores `w_lyr`/`w_emo`/`mood`; `mood_target` and the `_no_lyrics` harness variant are gone.
**Write-scope:** `muse/reco.py`, `muse/recommend.py`, `muse/arc_engine.py`, `muse/reco_eval.py`, `muse/library/queries.py`, `muse/api.py`, `tests/test_reco.py`, `tests/test_reco_rank.py`, `tests/test_reco_weights.py`, `tests/test_reco_calibration.py`, `tests/test_reco_audio_term.py`, `tests/test_recommend.py`, `tests/test_recommend_calibration.py`, `tests/test_api_recommend.py`, `tests/test_reco_eval.py`, `tests/test_mood_target.py`
**Read-context:** `muse/reco.py:24-51`, `muse/reco.py:270-302`, `muse/recommend.py:44-130`, `muse/recommend.py:200-210`, `muse/recommend.py:473-489`, `muse/api.py:1318-1383`, `muse/arc_engine.py:22-25`, `muse/reco_eval.py:143-170`, `muse/library/queries.py:502-521`
**Anchors:** `DEFAULT_WEIGHTS: dict[str, float] = {` @ `muse/reco.py`
`def recommend_next(` @ `muse/api.py`
`ARC_WEIGHTS = {**reco.DEFAULT_WEIGHTS, "w_lyr": 0.0}` @ `muse/arc_engine.py`
`def _no_lyrics(weights: dict[str, float]) -> dict[str, float]:` @ `muse/reco_eval.py`
`def vector_fingerprint(conn: sqlite3.Connection) -> tuple:` @ `muse/library/queries.py`
**Steps:** 1. `reco.py`: delete `w_lyr`/`w_emo` from `DEFAULT_WEIGHTS`, `_LEG_VECTORS`, `_NULL_REASONS`; delete `emotion_sim`, `_MAX_VA_DISTANCE`, the `w_emo` branch in `_leg_sim`; rewrite the weights comment to say three legs remain (spec:35). 2. `recommend.py`: `RETRIEVAL_LEGS = ("audio_vec", "feature_vec", "content_vec", "taste_vec")`; drop `lyrics_vec`/`emotion_vec` from `load_vectors` defaults and `_CALIBRATED_LEGS`; drop the emotion branches in `_pair_sim` and `_top_k`; delete `mood_target`. 3. `arc_engine.py`: `ARC_LEGS = recommend.RETRIEVAL_LEGS`, `ARC_WEIGHTS = dict(reco.DEFAULT_WEIGHTS)`, fix the comment. 4. `api.py` `recommend_next`: remove params `w_lyr`, `w_emo`, `mood` and the mood block; seed is always `track_rowid`; exclude is `_rowid_set(exclude)`. 5. `reco_eval.py`: delete `_no_lyrics` and the `"calibrated, no lyrics"` variant. 6. `vector_fingerprint`: append `"SELECT MAX(version) FROM schema_version"` to the tuple (spec:97). 7. Tests: delete `tests/test_mood_target.py`; delete emotion_sim tests in `tests/test_reco.py`; drop `w_lyr`/`w_emo`/lyrics/emotion legs from rank, weight, calibration, audio-term tests (keep each test's intent on the remaining legs; `test_reco_weights` ALL_LEGS becomes three names); delete `test_run_includes_the_no_lyrics_variant`; remove mood-param tests from `test_api_recommend.py`, add one asserting `?w_lyr=1&mood=0,0` returns 200 and the same results as without; drop `_top_k(..., "lyrics_vec", ...)` and `loaded[1]["lyrics_vec"]` asserts in `test_recommend.py`. Leave `upsert(... lyrics_vec=...)` calls alone (Task 5 owns them).
**New symbols:** none
**Verify:** `uv run pytest` — expected: all pass, none of the edited files fail.
**Forbidden:** no `Connection` in `muse/reco.py`; don't touch `muse/library/embeddings.py` or `muse/embed.py`; don't change `audio_term` or `AUDIO_SPLIT`.
**Depends:** none
**Why:** spec:35, spec:71-76. Deleted, not zeroed, so `renormalize` covers only legs that exist.

### Task 5: Stop the embed batch encoding lyrics and emotion
- [ ] status
**Objective:** The embed batch writes `audio_vec`, `feature_vec`, `audio_model`, `lyrics_hash` only; `lyrics_hash` still refreshes on a lyrics change; nothing writes or reads `lyrics_vec`, `lyrics_model`, `lyrics_va`, `emotion_vec`, `audio_va_model`, `lyrics_va_model`.
**Write-scope:** `muse/embed.py`, `muse/library/embeddings.py`, `muse/jobs.py`, `muse/library/lyrics_edit.py`, `muse/api.py`, `tests/test_embed_batch.py`, `tests/test_embed_batch_efficiency.py`, `tests/test_embeddings_table.py`, `tests/test_jobs_embed.py`, `tests/test_lyrics_edit.py`, `tests/test_embed_startup_wiring.py`, `tests/test_embeddings_arousal.py`, `tests/test_api_recommend.py`, `tests/test_api_vibe.py`, `tests/test_arc_engine.py`, `tests/test_lyric_tag_batch.py`, `tests/test_lyric_tags_table.py`, `tests/test_nearest.py`, `tests/test_radio.py`, `tests/test_reco_eval.py`, `tests/test_recommend.py`, `tests/test_recommend_calibration.py`
**Read-context:** `muse/embed.py:625-819`, `muse/library/embeddings.py:27-241`, `muse/jobs.py:180-308`, `muse/api.py:545-562`, `muse/api.py:604-612`, `muse/api.py:733-744`, `muse/api.py:1008-1012`, `muse/library/lyrics_edit.py:44-51`, `tests/test_arc_engine.py:36-50`
**Anchors:** `def run_embed_batch(` @ `muse/embed.py`
`def pending(conn: sqlite3.Connection) -> list[dict]:` @ `muse/library/embeddings.py`
`def update_lyrics(` @ `muse/library/embeddings.py`
`async def run_embed_backfill(` @ `muse/jobs.py`
`def audio_arousal(conn: sqlite3.Connection) -> dict[int, float]:` @ `muse/library/embeddings.py`
**Steps:** 1. `library/embeddings.py`: `pending` WHERE `e.track_rowid IS NULL OR e.audio_vec IS NULL`; new `upsert` signature (below) writing only those columns plus `indexed_at`; replace `update_lyrics` with `update_lyrics_hash`; delete `emotion_coverage`; `vectors()` selects and returns only `audio_vec`, `feature_vec`; keep `audio_arousal` as is (Task 15 replaces it); rewrite the module docstring for the one-gate queue. 2. `embed.py`: `run_embed_batch(conn, audio_encoder, *, on_progress=None, should_stop=None)`; `_embed_one(conn, track, audio_encoder, bounds)` writes `lyrics_hash` of cleaned lyrics or None; the changed-lyrics pass calls `update_lyrics_hash` with the fresh hash (None when no lyrics); delete `_audio_va_by_track`. Leave encoder classes alone (Task 6). 3. `jobs.py`: `run_embed_backfill(conn, audio_encoder_factory, bus)`; delete `_optional_encoder` and the VA comment block. 4. `api.py`: delete `_lyrics_encoder`, `_audio_va_encoder`, `_lyrics_va_encoder` factories; both `run_embed_backfill` calls become `run_embed_backfill(conn, _audio_encoder, bus)`; drop the `"emotion"` key from `/api/health`. Keep the `create_app` params (Task 6 removes them). 5. `lyrics_edit.py`: `UPDATE embeddings SET lyrics_hash = NULL WHERE track_rowid = ?`. 6. Tests: remove `lyrics_vec=`, `lyrics_model=`, `audio_va=`, `lyrics_va=`, `emotion_vec=`, `*_va_model=` kwargs from every `upsert` call in Write-scope; where a test needs `audio_va` (`tests/test_arc_engine.py` `add_song`, `tests/test_embeddings_arousal.py`) write it with `conn.execute("UPDATE embeddings SET audio_va = ? WHERE track_rowid = ?", ...)` after `upsert`; delete tests of emotion fusion, VA encoders in the batch, `emotion_coverage`, `update_lyrics`; batch tests assert `lyrics_hash` refresh on a lyrics edit and that `lyrics_vec` stays NULL.
**New symbols:** `def upsert(conn: sqlite3.Connection, track_rowid: int, *, audio_vec: bytes | None, feature_vec: bytes | None, audio_model: str | None, lyrics_hash: str | None, now: int | None = None) -> None`; `def update_lyrics_hash(conn: sqlite3.Connection, track_rowid: int, *, lyrics_hash: str | None, now: int | None = None) -> None`; `def run_embed_batch(conn, audio_encoder: AudioEncoder, *, on_progress: Callable[[int, int, str], None] | None = None, should_stop: Callable[[], bool] | None = None) -> EmbedResult`; `async def run_embed_backfill(conn, audio_encoder_factory: Callable[[], embed_module.AudioEncoder], bus: JobBus) -> dict | None`
**Verify:** `uv run pytest` — expected: all pass.
**Forbidden:** no schema change (Task 13); don't delete encoder classes or `create_app` params; don't touch `muse/library/emotion.py`.
**Depends:** Task 4
**Why:** spec:44-46, spec:130. Lyric-tag staleness keys on `lyrics_hash`, so the hash write must survive MiniLM's removal.

### Task 6: Remove the retired encoders, worker ops and create_app params
- [ ] status
**Objective:** No source mentions MiniLM, the VA encoders or their stubs; the worker serves `align, clap_audio, clap_text, laya, mert`; `create_app` takes only `audio_encoder` among encoders.
**Write-scope:** `muse/embed.py`, `muse/worker.py`, `muse/worker_main.py`, `muse/api.py`, `tests/test_embed_pure.py`, `tests/test_embed_slow.py`, `tests/test_worker_client.py`, `tests/test_worker_main.py`, `tests/test_worker_main_slow.py`, `tests/test_worker_models_slow.py`, `tests/test_api_jobs.py`, `tests/test_api_discover.py`, `tests/test_api_downloader.py`, `tests/test_api_smart.py`, `tests/test_embed_startup_wiring.py`
**Read-context:** `muse/embed.py:35-235`, `muse/embed.py:323-572`, `muse/worker.py:144-185`, `muse/worker_main.py:84-148`, `muse/api.py:436-462`
**Anchors:** `class MiniLmLyricsEncoder:` @ `muse/embed.py`
`class WorkerLyricsEncoder:` @ `muse/worker.py`
`def real_handlers(weights: Path) -> dict[str, Callable[[dict], object]]:` @ `muse/worker_main.py`
`def create_app(` @ `muse/api.py`
**Steps:** 1. `embed.py`: delete `LYRICS_DIM`, `STUB_LYRICS_MODEL`, `MINILM_MODEL`, `STUB_AUDIO_VA_MODEL`, `STUB_LYRICS_VA_MODEL`, `AUDIO_VA_MODEL`, `LYRICS_VA_MODEL`, `EMOTION_AUDIO_WEIGHT`, `LyricsEncoder`, `StubLyricsEncoder`, `AudioVAEncoder`, `LyricsVAEncoder`, `_hash_va`, `NullAudioVAEncoder`, `StubAudioVAEncoder`, `StubLyricsVAEncoder`, `fuse_emotion_vec`, `MiniLmLyricsEncoder`, `AUDIO_VA_SAMPLE_RATE`, `AUDIO_VA_WINDOW_SECONDS`, `_build_audio_va_model`, `_to_va_axis`, `MerAudioVAEncoder`, `_EMOTION_TO_VA`, `TextVAEncoder`; keep `clean_lyrics_for_embedding` and `lyrics_hash`; add `StubAudioEncoder.encode_text(self, text: str) -> list[float]` returning `_hash_vector("text:" + text, AUDIO_DIM)`. 2. `worker.py`: delete the three Worker*Encoder classes and their imports. 3. `worker_main.py`: delete `minilm`, `audio_va`, `lyrics_va` builders and handler entries. 4. `api.py`: drop `lyrics_encoder`, `audio_va_encoder`, `lyrics_va_encoder` params, their imports, and the docstring mention. 5. Tests: remove those kwargs and imports; `tests/test_worker_main.py:64` expects `sorted(["align", "clap_audio", "clap_text", "mert", "laya"])`; delete MiniLM/VA cases from pure, slow and worker tests; add a pure test that `StubAudioEncoder().encode_text("x")` is 512 long and deterministic.
**New symbols:** `StubAudioEncoder.encode_text(self, text: str) -> list[float]`
**Verify:** `uv run pytest` — expected: all pass; `git grep -nE "MiniLm|minilm|AudioVA|LyricsVA|TextVA|lyrics_va|fuse_emotion" muse` — expected: no output.
**Forbidden:** no scripts, CI, pyproject or manifest edits (Tasks 7, 8); no torch import outside a handler.
**Depends:** Task 5
**Why:** spec:41-43, spec:116-121: removed from source, no shim, an older worker keeps working.

### Task 7: Drop MiniLM and VA from build scripts, CI and dependencies [mechanical]
- [ ] status
**Objective:** CI, freeze and preflight scripts no longer fetch or smoke-test MiniLM/VA, and `sentence-transformers` is gone from `pyproject.toml` and `uv.lock`.
**Write-scope:** `.github/workflows/package.yml`, `scripts/worker_smoke.py`, `scripts/worker_preflight.py`, `scripts/preflight_freeze_entry.py`, `scripts/fetch_weights.py`, `scripts/build_manifest.py`, `scripts/freeze_sidecar.py`, `pyproject.toml`, `uv.lock`
**Read-context:** `.github/workflows/package.yml:84-86`, `scripts/worker_smoke.py:1-40`, `scripts/worker_preflight.py:10-25`, `scripts/worker_preflight.py:105-145`, `scripts/build_manifest.py:15-31`, `scripts/build_manifest.py:95-106`, `pyproject.toml:20-30`
**Anchors:** `DIMS = {"mert": 768, "clap_audio": 512, "minilm": 384}` @ `scripts/worker_smoke.py`
`PINNED = {` @ `scripts/build_manifest.py`
**Steps:** 1. `package.yml:85`: `--keys mert,schufo,mms`; line 86: `--ops hello,mert,align`. 2. `worker_smoke.py`: drop `minilm` from `DIMS` and its `elif op == "minilm"` branch. 3. `worker_preflight.py`: delete the `minilm`, `audio_va`, `lyrics_va` model ids and their `timed(...)` blocks. 4. `preflight_freeze_entry.py`: delete `import sentence_transformers  # noqa: F401`. 5. `fetch_weights.py`: help example becomes `mert,clap`. 6. `build_manifest.py`: delete the three `PINNED` entries; write `"version": 3`. 7. `freeze_sidecar.py`: drop `"sentence_transformers"` from the exclude tuple. 8. `pyproject.toml`: delete the `"sentence-transformers==6.2.0",` line; reword the `slow` marker text to "real CLAP encoder". 9. Run `uv lock`.
**New symbols:** none
**Verify:** `uv lock --check && git grep -niE "minilm|sentence.transformers|audio_va|lyrics_va" .github scripts pyproject.toml uv.lock` — expected: lock check passes, grep prints nothing.
**Forbidden:** no edits under `muse/` or `tests/`; don't touch `muse/smart_manifest.json` (Task 8).
**Depends:** Task 6
**Why:** spec:121.

### Task 8: Prune unlisted weight folders on a manifest upgrade
- [ ] status
**Objective:** Installing a higher manifest `version` deletes folders directly under `models/weights/` the manifest does not list; the shipped manifest drops `minilm`, `audio_va`, `lyrics_va` and is version 3.
**Write-scope:** `muse/smart.py`, `muse/smart_manifest.json`, `tests/test_smart_install.py`
**Read-context:** `muse/smart.py:57-66`, `muse/smart.py:135-170`, `tests/test_smart_install.py:1-60`, `docs/specs/2026-11-07-arc-radio-and-model-retirement.md:99-104`
**Anchors:** `def install(` @ `muse/smart.py`
`def files_for(manifest: dict, triple: str | None) -> list[ManifestFile]:` @ `muse/smart.py`
**Steps:** 1. Add `prune(models_dir: Path, manifest: dict) -> list[str]`: listed = `{p.split("/")[1] for p in (e["path"] for e in manifest["weights"]) if p.startswith("weights/")}`; for each directory child of `models_dir / "weights"` not in listed, `shutil.rmtree`; on `OSError` log a warning (`logging.getLogger(__name__)`) and leave it; return removed names sorted. Never touch anything outside `models_dir / "weights"`. 2. In `install`, read the previous `installed.json` `manifest_version` before writing; after the new `installed.json` is written, call `prune` when the previous version exists and is lower than `manifest["version"]`. 3. Manifest: remove every `weights` entry whose path starts `weights/minilm/`, `weights/audio_va/`, `weights/lyrics_va/`; set `"version": 3`; leave `workers` untouched. Do it with a short Python one-off, not by hand. 4. Tests: upgrade from version 2 with folders `weights/clap`, `weights/minilm`, and a file `models/keep.txt` and folder `models/worker`: `minilm` gone, `clap`, `keep.txt`, `worker` survive; fresh install (no `installed.json`) prunes nothing; an `rmtree` raising `OSError` (monkeypatch) leaves the folder and the install still completes; the shipped manifest has version 3 and no `minilm`/`audio_va`/`lyrics_va` paths.
**New symbols:** `def prune(models_dir: Path, manifest: dict) -> list[str]`
**Verify:** `uv run pytest tests/test_smart_install.py tests/test_api_smart.py` — expected: all pass.
**Forbidden:** no deletes outside `models/weights/`; no new deps.
**Depends:** none
**Why:** spec:99-104: about 1.08 GB of retired weights stay on upgraded installs otherwise.

### Task 9: Delete the emotion plane, mixes and the dashboard map
- [ ] status
**Objective:** `muse/library/emotion.py`, `/api/mixes/{band}` and the dashboard's mood-surface fields are gone server and client side.
**Write-scope:** `muse/library/emotion.py`, `muse/library/insights.py`, `muse/api.py`, `web/src/api.ts`, `tests/test_emotion_plane.py`, `tests/test_dashboard.py`, `tests/test_dashboard_shelves.py`
**Read-context:** `muse/library/insights.py:15-25`, `muse/library/insights.py:255-305`, `muse/api.py:1048-1072`, `web/src/api.ts:373-478`
**Anchors:** `def dashboard(conn: sqlite3.Connection, now_ms: int | None = None) -> dict:` @ `muse/library/insights.py`
`@app.get("/api/mixes/{band}")` @ `muse/api.py`
`export type Dashboard = {` @ `web/src/api.ts`
**Steps:** 1. Delete `muse/library/emotion.py` and `tests/test_emotion_plane.py`. 2. `insights.dashboard`: drop the `plane` logic, `mixes`, `stats.measured`, `stats.map`, `stats.map_source`, `stats.map_centre`; remove the `emotion` import (keep `features` for `sounds_like`). 3. `api.py`: delete the `mix` route and the `library_emotion` import. 4. `web/src/api.ts`: delete `BandKey`, `BAND_ORDER`, `Mix`, the dashboard `MoodPoint`, `MapCentre`, `getMix`, and those `Dashboard` fields. 5. Tests: delete `test_mixes_carry_their_share_of_the_measured_library` and `test_the_map_and_the_counts_agree_about_what_is_measured`; drop `mixes` asserts in `tests/test_dashboard.py:48` and `tests/test_dashboard_shelves.py:46-47` (keep the other keys); add `test_mixes_route_is_gone` asserting `GET /api/mixes/bright` is 404 (in `tests/test_dashboard_shelves.py`, using its existing app/client helper).
**New symbols:** none
**Verify:** `uv run pytest && npm --prefix web run build` — expected: all pass, build exits 0.
**Forbidden:** don't touch `muse/library/features.py`; don't touch the player `MoodPoint` in `web/src/player/PlayerContext.tsx`.
**Depends:** none
**Why:** spec:96: nothing in the UI renders these and their data is dropped in migration 23.

### Task 10: Add the missing-lyrics list and "no words" endpoints
- [ ] status
**Objective:** `GET /api/library/lyrics/missing` lists songs whose lookup failed; `POST /api/tracks/{id}/lyrics/no-words` takes one off it; the dashboard carries `missing_lyrics`.
**Write-scope:** `muse/library/lyrics_state.py`, `muse/api.py`, `tests/test_missing_lyrics.py` (new)
**Read-context:** `muse/library/lyrics_state.py:19-41`, `muse/library/lyrics_state.py:106-141`, `muse/api.py:1032-1046`, `muse/api.py:1665-1690`, `muse/library/queries.py:348-360`, `tests/conftest.py:88-150`
**Anchors:** `def upsert(` @ `muse/library/lyrics_state.py`
`def dashboard() -> dict:` @ `muse/api.py`
`def tracks_by_rowid(` @ `muse/library/queries.py`
**Steps:** 1. `lyrics_state.missing(conn) -> list[int]`: rowids where `t.status = 'done' AND t.lyrics_path IS NULL AND t.is_instrumental = 0 AND ls.state = 'not_found'`, `ORDER BY t.artist_name, t.album_name, t.track_name, t.track_rowid`. 2. `lyrics_state.mark_no_words(conn, track_rowid, *, now=None) -> bool`: False when no such track; else `upsert(conn, track_rowid, state="declared_instrumental", synced=False, checked_at=now or int(time.time()))`, True. 3. Routes: `GET /api/library/lyrics/missing` returns `{"count": len(ids), "tracks": queries.tracks_by_rowid(conn, ids)}`; `POST /api/tracks/{track_rowid}/lyrics/no-words` returns `{"ok": True}` or 404 `No track {id}`; the dashboard route adds `"missing_lyrics": len(lyrics_state.missing(conn))`. 4. Tests on the temp DB: a not_found track is listed; one with `lyrics_path`, one `is_instrumental = 1`, one `state = 'plain'`, one with no row, one `status = 'missing_file'` are not; order is artist then album then title; `mark_no_words` removes it and returns False for an unknown rowid; route 404; dashboard `missing_lyrics` equals the list count; saving lyrics through `PUT /api/tracks/{id}/lyrics` removes it.
**New symbols:** `def missing(conn: sqlite3.Connection) -> list[int]`, `def mark_no_words(conn: sqlite3.Connection, track_rowid: int, *, now: int | None = None) -> bool`
**Verify:** `uv run pytest tests/test_missing_lyrics.py` — expected: all pass.
**Forbidden:** never write audio files; don't touch `muse/library/insights.py` (Task 9).
**Depends:** none
**Why:** Owner request: notify the listener to add lyrics where the automatic lookup found none (Assumption 9).

## Phase 3 - Migration 23, player state and the energy read path

### Task 11: Write migration 23, state and energy read tests [test]
- [ ] status
**Objective:** Red tests pin migration 23, the radio state round trip, the prompt-vector store, `audio_energy`, and the thirds `SOUND_BANDS`.
**Write-scope:** `tests/test_m023_schema.py` (new), `tests/test_prompt_vectors.py` (new), `tests/test_plays.py`, `tests/test_arc.py`, `tests/test_arc_engine.py`
**Read-context:** `tests/test_m022_schema.py:1-30`, `tests/test_plays.py:186-205`, `tests/test_arc.py:216-225`, `tests/test_arc_engine.py:355-364`, `muse/library/state.py:63-105`
**Anchors:** `def write_state(` @ `muse/library/state.py`
`def sound_gap(arousal: float | None, sound: str) -> float:` @ `muse/arc.py`
**Steps:** 1. `test_m023_schema.py`: build to 22 with `MIGRATIONS[:22]`, insert one embeddings row with every old column set, `migrate(raw) == 23`; embeddings columns `== {"track_rowid", "audio_vec", "feature_vec", "audio_model", "lyrics_hash", "indexed_at"}` and the row's `lyrics_hash` survives; `player_state` has `radio_pos` default 0 and `radio_lift` default 1; `prompt_vectors` columns `== {"audio_model", "name", "vec"}`; a fresh `migrate` on an empty DB reaches 23; `MIGRATIONS[22](raw)` on a DB already lacking the columns does not raise. 2. `test_plays.py`: `write_state(..., radio_pos=5, radio_lift=0)` then `read_state` returns 5 and 0; defaults are 0 and 1; `PUT /api/player/state` with `radio_pos`/`radio_lift` round-trips through `GET`. 3. `test_prompt_vectors.py` (module `muse.library.prompts`): `put`/`get` round trip; `current_audio_model` is the most common model with ties by name, None on empty; `library_embeddings.audio_energy(conn)` returns `{}` with no prompt rows, and with prompts `calm=[1,0]`, `energetic=[0,1]` and three tracks of model `stub` maps lowest raw to -1.0, highest to 1.0; a track of another model is absent; adding a prompt row changes the result (cache keyed on prompt stamp). 4. `test_arc.py`: `SOUND_BANDS == {"calm": (-1.0, -1/3), "mid": (-1/3, 1/3), "energetic": (1/3, 1.0)}` (approx); `sound_gap(0.0, "calm") == approx(1/3)`; `sound_gap(0.9, "calm") == approx(0.9 + 1/3)`; keep the None and `any` cases. 5. `test_arc_engine.py`: rewrite `test_plan_calm_sound_prefers_the_quiet_twin` to seed `prompt_vectors` (`put(conn, "stub", "calm", [1, 0])`, `put(conn, "stub", "energetic", [0, 1])`) and give the twins audio vecs `[0.8, 0.6]` (calm) and `[0.6, 0.8]` (loud) plus a third neutral track so the percentile spreads; assert the calm twin comes first; drop the `arousal` param from `add_song`.
**New symbols:** none
**Verify:** `uv run pytest tests/test_m023_schema.py tests/test_prompt_vectors.py tests/test_plays.py tests/test_arc.py tests/test_arc_engine.py` — expected: failures only, each naming migration 23, `radio_pos`/`radio_lift`, `muse.library.prompts`, `audio_energy`, or `SOUND_BANDS`.
**Forbidden:** no source edits.
**Depends:** Task 5, Task 9
**Why:** spec:147. Red first so Task 12 cannot quietly narrow the contract.

### Task 12: Ship migration 23, radio state, prompt store and CLAP energy
- [ ] status
**Objective:** Task 11's tests pass: columns dropped, `radio_pos`/`radio_lift` stored, prompt vectors cached, arc `sound` reads CLAP energy in even thirds.
**Write-scope:** `muse/library/schema.py`, `muse/library/state.py`, `muse/library/prompts.py` (new), `muse/library/embeddings.py`, `muse/arc.py`, `muse/arc_engine.py`, `muse/api.py`, `tests/test_m022_schema.py`, `tests/test_migrations.py`, `tests/test_embeddings_arousal.py`
**Read-context:** `muse/library/schema.py:685-726`, `muse/library/state.py:17-105`, `muse/api.py:159-190`, `muse/api.py:1473-1493`, `muse/library/embeddings.py:196-241`, `muse/arc_engine.py:120-130`, `muse/arc.py:39`, `muse/recommend.py:154-173`
**Anchors:** `def _m022_edits(conn: sqlite3.Connection) -> None:` @ `muse/library/schema.py`
`class StateWrite(BaseModel):` @ `muse/api.py`
`def audio_arousal(conn: sqlite3.Connection) -> dict[int, float]:` @ `muse/library/embeddings.py`
`def to_unit(raw: dict[int, float]) -> dict[int, float]` @ `muse/energy.py` (from Task 2)
**Steps:** 1. `_m023_arc_radio`: for each of `lyrics_vec, lyrics_model, audio_va, lyrics_va, emotion_vec, audio_va_model, lyrics_va_model` present in `PRAGMA table_info(embeddings)`, `ALTER TABLE embeddings DROP COLUMN <c>`; `ALTER TABLE player_state ADD COLUMN radio_pos INTEGER NOT NULL DEFAULT 0`; same for `radio_lift ... DEFAULT 1`; `CREATE TABLE prompt_vectors (audio_model TEXT NOT NULL, name TEXT NOT NULL, vec BLOB NOT NULL, PRIMARY KEY (audio_model, name))`; append to `MIGRATIONS`; docstring cites spec:96 and Assumption 1. 2. `state.py`: `DEFAULT_STATE` gains `radio_pos: 0`, `radio_lift: 1`; `write_state` gains `radio_pos: int = 0, radio_lift: int = 1` in the INSERT/UPSERT. `StateWrite` gains `radio_pos: int = 0`, `radio_lift: bool = True`; the PUT route passes `radio_pos=write.radio_pos, radio_lift=int(write.radio_lift)`. 3. `library/prompts.py`: `current_audio_model`, `get`, `put` (INSERT OR REPLACE then commit), `stamp` = `SELECT COUNT(*), MAX(rowid) FROM prompt_vectors`; pack/unpack via a function-level `from ..embed import pack_vector, unpack_vector`. 4. `embeddings.audio_energy(conn)`: model from `current_audio_model`; `{}` when None or either `energy.CALM`/`energy.ENERGETIC` prompt missing; `audio_vec` of playable (`status = 'done'`) rows with that model; `energy.to_unit` of `raw_energy`; cache in a module dict keyed `(database path, queries.vector_fingerprint(conn), prompts.stamp(conn), model)` (import `queries` inside the function); add `clear_energy_cache()`. Delete `audio_arousal` and `tests/test_embeddings_arousal.py`. 5. `arc.py`: `SOUND_BANDS = {"calm": (-1.0, -1 / 3), "mid": (-1 / 3, 1 / 3), "energetic": (1 / 3, 1.0)}  # CLAP energy thirds, spec Decision`; rename `sound_gap`'s param to `energy` (same position). 6. `arc_engine.py`: `arousal = library_embeddings.audio_energy(conn)`. 7. `tests/test_m022_schema.py:22` asserts `migrate(raw) == LATEST_VERSION`; `tests/test_migrations.py:84-89` expects the six-column set; `test_migration_11_...` stays (it runs `MIGRATIONS[:11]`).
**New symbols:** `def _m023_arc_radio(conn: sqlite3.Connection) -> None`; `def current_audio_model(conn: sqlite3.Connection) -> str | None`; `def get(conn: sqlite3.Connection, audio_model: str) -> dict[str, list[float]]`; `def put(conn: sqlite3.Connection, audio_model: str, name: str, vec: list[float]) -> None`; `def stamp(conn: sqlite3.Connection) -> tuple`; `def audio_energy(conn: sqlite3.Connection) -> dict[int, float]`; `def clear_energy_cache() -> None`
**Verify:** `uv run pytest` — expected: all pass, including every Task 11 test.
**Forbidden:** never edit `_m001`-`_m022`; no `Connection` or `sqlite3` in `muse/arc.py`; `plays` untouched.
**Depends:** Task 11
**Why:** spec:62, spec:85-97: one migration drops what the retired models wrote and adds what arc radio needs.

## Phase 4 - Fill the prompt cache, and the pure radio client

### Task 13: Fill the CLAP prompt cache in the launch chain and report energy readiness
- [ ] status
**Objective:** After the embed backfill the chain stores the two prompt vectors once per audio model; `GET /api/smart` adds `energy_ready`.
**Write-scope:** `muse/jobs.py`, `muse/api.py`, `tests/test_prompt_fill.py` (new), `tests/test_api_smart.py`
**Read-context:** `muse/api.py:545-552`, `muse/api.py:604-625`, `muse/api.py:964-973`, `tests/test_embed_startup_wiring.py:30-80`, `tests/test_api_smart.py:95-105`
**Anchors:** `async def run_embed_backfill(` @ `muse/jobs.py`
`def current_audio_model(conn: sqlite3.Connection) -> str | None` @ `muse/library/prompts.py` (from Task 12)
`StubAudioEncoder.encode_text(self, text: str) -> list[float]` @ `muse/embed.py` (from Task 6)
**Steps:** 1. `jobs.run_prompt_fill(conn, audio_encoder_factory) -> bool` (sync; the chain wraps it in `asyncio.to_thread`): `encoder = audio_encoder_factory()`; model = `encoder.model_name`; return False when `library_prompts.get(conn, model)` already has both `energy.CALM` and `energy.ENERGETIC`; else `put` each from `encoder.encode_text(energy.CALM_PROMPT)` / `(energy.ENERGETIC_PROMPT)` and return True; any exception logs `log.warning("CLAP prompt vectors unavailable: %s", error)` and returns False. 2. `run_chain`: right after the `run_embed_backfill` try block, `try: await asyncio.to_thread(run_prompt_fill, conn, _audio_encoder) except Exception: log.exception("Prompt fill failed")`, inside the same `embed_batch or smart.installed` branch. 3. `_smart_view` adds `"energy_ready"`, True when `current_audio_model(conn)` is not None and `get(conn, model)` has both names. 4. Tests: with a fake factory returning `StubAudioEncoder()` it writes two rows and returns True; second call returns False and does not call `encode_text` (counting fake); a fake whose `encode_text` raises returns False and writes nothing; `create_app(make_config(tmp_path, embed_batch=True), audio_encoder=StubAudioEncoder())` with one scanned or inserted track: after the launch chain completes (follow `tests/test_embed_startup_wiring.py`'s drive pattern) `prompt_vectors` has 2 rows and `/api/smart` `energy_ready` is True; `tests/test_api_smart.py:101` dict gains `"energy_ready": False`.
**New symbols:** `def run_prompt_fill(conn, audio_encoder_factory) -> bool`
**Verify:** `uv run pytest tests/test_prompt_fill.py tests/test_api_smart.py tests/test_embed_startup_wiring.py` — expected: all pass.
**Forbidden:** no worker call outside the chain; no request handler calls `encode_text` for prompts.
**Depends:** Task 12
**Why:** spec:129, spec:140: energy works with no backfill once two text vectors are cached.

### Task 14: Write the pure radio request and copy tests [test]
- [ ] status
**Objective:** `web/src/player/radio.test.ts` and `web/src/muse/mood.test.ts` pin the radio query builders, the relaxation note and `toArcSpec`'s lift/energy behaviour, red.
**Write-scope:** `web/src/player/radio.test.ts` (new), `web/src/muse/mood.test.ts` (new)
**Read-context:** `web/src/muse/mood.ts:45-61`, `web/src/player/queue.test.ts:1-20`, `web/src/api.ts:534-546`
**Anchors:** `export function toArcSpec(p: MoodPoint, seed: number | null, n: number): object {` @ `web/src/muse/mood.ts`
**Steps:** 1. `radio.test.ts` imports `{ radioPath, radioBody, relaxNote, UNTAGGED_NOTE, NO_VECTORS_NOTE }` from `./radio`. Cases: `radioPath({ seed: 7, n: 1, pos: 3, recent: [9, 8], exclude: [1, 2], lift: true })` equals `"/arc/7?mode=radio&n=1&pos=3&lift=1&recent=9%2C8&exclude=1%2C2"`; `lift: false` gives `lift=0`; empty `recent`/`exclude` omit those params; `radioBody({ spec: { a: 1 }, n: 8, pos: 2, recent: [9], exclude: [1] })` deep-equals `{ spec: { a: 1 }, mode: "radio", n: 8, pos: 2, recent: [9], exclude: [1] }`; `relaxNote([])` is null; `relaxNote(["filler"])` is `"Running low: played a song without lyric tags."`; `relaxNote(["filler", "freshness", "artist_spacing"])` is `"Running low: played a song without lyric tags, ignored freshness, allowed a repeated artist."`; `relaxNote(["bogus"])` is null; `relaxNote(["freshness", "bogus"])` is `"Running low: ignored freshness."`; `UNTAGGED_NOTE === "This song has no lyric tags, so Radio follows its sound."`; `NO_VECTORS_NOTE === "Muse hasn't listened to this song yet."`. 2. `mood.test.ts`: for `p = { v: -0.5, a: 0.9 }` (darkness 0.75): `toArcSpec(p, 3, 8)` has `shape "steady"`, `goal.darkness 0.75`, `sound "energetic"`; `toArcSpec(p, 3, 8, true)` has `shape "gradual"`, `goal.darkness 0.25`, `start.darkness 0.75`; `toArcSpec({ v: 0.8, a: 0 }, 3, 8, true).goal.darkness` is `0.1`; `toArcSpec(p, 3, 8, false, false).sound` is `"any"`.
**New symbols:** none
**Verify:** `npm --prefix web test` — expected: failures in the two new files only (missing `./radio` exports, `toArcSpec` lift/energy cases); the 42 existing tests still pass.
**Forbidden:** no source edits.
**Depends:** none
**Why:** vitest runs pure modules only (`web/package.json:9`), so the radio wiring's logic lives in a pure module Task 15 writes.

### Task 15: Add the pure radio module and the lift-aware arc spec
- [ ] status
**Objective:** `web/src/player/radio.ts` exists and `toArcSpec` takes `lift` and `energy`; Task 14's tests pass.
**Write-scope:** `web/src/player/radio.ts` (new), `web/src/muse/mood.ts`
**Read-context:** `web/src/player/radio.test.ts` (from Task 14), `web/src/muse/mood.test.ts` (from Task 14), `web/src/muse/mood.ts:45-61`
**Anchors:** `export function toArcSpec(p: MoodPoint, seed: number | null, n: number): object {` @ `web/src/muse/mood.ts`
**Steps:** 1. `radio.ts`: export `RadioQuery = { seed: number; n: number; pos: number; recent: number[]; exclude: number[]; lift: boolean }`; `radioPath(q)` builds `URLSearchParams` in order `mode, n, pos, lift, recent, exclude` (skip empty lists, join with `,`) and returns `/arc/${seed}?${params}`; `radioBody({ spec, n, pos, recent, exclude })` adds `mode: "radio"`; `RELAX_COPY: Record<string, string>` = `filler: "played a song without lyric tags"`, `freshness: "ignored freshness"`, `artist_spacing: "allowed a repeated artist"`; `relaxNote(rungs)` keeps known rungs in input order, null when none, else `Running low: ${known.join(", ")}.`; the two note constants. No imports. 2. `mood.ts`: `toArcSpec(p, seed, n, lift = false, energy = true)`: `goalDark = lift ? Math.min(darkness, 0.25) : darkness`, `shape: lift ? "gradual" : "steady"`, `sound: energy ? <current rule> : "any"`. `MakePlaylist` keeps calling with three args.
**New symbols:** `export type RadioQuery`, `export function radioPath(q: RadioQuery): string`, `export function radioBody(b: { spec: object; n: number; pos: number; recent: number[]; exclude: number[] }): object`, `export function relaxNote(rungs: string[]): string | null`, `export const UNTAGGED_NOTE: string`, `export const NO_VECTORS_NOTE: string`, `export function toArcSpec(p: MoodPoint, seed: number | null, n: number, lift = false, energy = true): object`
**Verify:** `npm --prefix web test && npm --prefix web run build` — expected: all vitest pass, build exits 0.
**Forbidden:** don't touch `PlayerContext.tsx` (Task 16); no em dash in copy.
**Depends:** Task 14
**Why:** spec:139, Assumption 2.

## Phase 5 - Wire Radio to the arc engine and build the surfaces

### Task 16: Drive Radio from the arc engine with pos and lift
- [ ] status
**Objective:** Radio fill and the mood re-pick call `/api/arc` (GET without mood, POST with), carry `radio_pos`/`radio_lift` through player state, and expose lift, untagged reason and energy readiness.
**Write-scope:** `web/src/player/PlayerContext.tsx`, `web/src/player/sync.ts`, `web/src/api.ts`
**Read-context:** `web/src/player/PlayerContext.tsx:80-160`, `web/src/player/PlayerContext.tsx:276-330`, `web/src/player/PlayerContext.tsx:456-690`, `web/src/player/PlayerContext.tsx:840-870`, `web/src/player/PlayerContext.tsx:955-975`, `web/src/player/sync.ts:12-30`, `web/src/api.ts:480-620`, `web/src/player/radio.ts` (from Task 15)
**Anchors:** `export function getRecommendations(` @ `web/src/api.ts`
`export type RemoteState = {` @ `web/src/player/sync.ts`
`export function radioPath(q: RadioQuery): string` @ `web/src/player/radio.ts` (from Task 15)
**Steps:** 1. `api.ts`: delete `LegName`, `LEG_ORDER`, `LEG_LABELS`, `LegContribution`, `NullReason`, `NULL_LEG_COPY`, `Relaxation`, `RELAXATION_COPY`, `Weights`, `DEFAULT_WEIGHTS`, `getRecommendations`; `Reasons` becomes `{ relaxed: string[]; arc: { target: number | null; darkness: number | null; theme_sim: number | null; filler: boolean } | null; cost: number; [key: string]: unknown }`; add `ArcPick = { track_rowid: number; score: number; reasons: Reasons; track: Track }`, `ArcBlock = { results: ArcPick[]; reason: string | null; exhausted: boolean }`, `getArcRadio(q: RadioQuery)` (GET via `get<ArcBlock>(radioPath(q))`), `postArcRadio(body)` (POST `/arc` via `send<ArcBlock>("POST", "/arc", radioBody(body))`); `SmartStatus` gains `energy_ready?: boolean`. 2. `sync.ts`: `RemoteState` gains `radio_pos: number; radio_lift: number`. 3. `PlayerContext.tsx`: state `radioPos` (number) and `radioLift` (boolean, default true), restored from `state.radio_pos` and `Boolean(state.radio_lift ?? 1)`; `playQueue` resets `radioPos` to 0 whenever it sets a new seed; push sends `radio_pos`, `radio_lift`. Fill: seed is `radioSeed` (not the queue tail); without mood `getArcRadio({ seed: radioSeed, n: 1, pos: radioPos, recent, exclude, lift: radioLift })`, with mood `postArcRadio({ spec: toArcSpec(mood, radioSeed, 1, radioLift, energyReady), n: 1, pos: radioPos, recent, exclude })`; on a pick append it, `setRadioPos(p => p + 1)`, note = `relaxNote(pick.reasons.relaxed)`; `radioReason` state = `"untagged"` when `block.reason` is non-null else null; empty results keep the existing "Nothing left" note and stall; 404 note `NO_VECTORS_NOTE`. Re-pick: `n: 8`, `pos: max(0, radioPos - stale.size)`, same seed; on success `setRadioPos(start + results.length)`. `energyReady` state loaded once on mount from `getSmartStatus()` (`Boolean(s.energy_ready)`, false on error). Expose `radioLift`, `setRadioLift`, `radioReason`, `energyReady` on `PlayerApi`, and add them to the `useMemo` deps. Update the story-35 comment block: radio now asks the arc engine, still stateless.
**New symbols:** `export type ArcPick`, `export type ArcBlock`, `export function getArcRadio(q: RadioQuery, signal?: AbortSignal): Promise<ArcBlock>`, `export function postArcRadio(body: { spec: object; n: number; pos: number; recent: number[]; exclude: number[] }): Promise<ArcBlock>`; `PlayerApi` fields `radioLift: boolean`, `setRadioLift: (on: boolean) => void`, `radioReason: "untagged" | null`, `energyReady: boolean`
**Verify:** `npm --prefix web test && npm --prefix web run build && git grep -n "getRecommendations\|/recommend/" web/src` — expected: tests pass, build exits 0, grep prints nothing.
**Forbidden:** no server edits; don't touch `Muse.tsx` (Task 17); keep two `<audio>` elements and the trim/stall logic as is.
**Depends:** Task 13, Task 15
**Why:** spec:50-62, spec:125-127: one Laya-based engine in the UI's path, pos persisted so the curve does not restart.

### Task 17: Add the Lift switch and Radio notes to the Muse page [design]
- [ ] status
**Objective:** The Muse page shows the Lift switch, the untagged-seed note with Add lyrics, the 404 and energy notes; Settings shows the new licence and energy lines.
**Write-scope:** `web/src/views/Muse.tsx`, `web/src/styles/muse.css`, `web/src/views/Settings.tsx`
**Read-context:** `web/src/views/Muse.tsx:1-79`, `web/src/styles/muse.css:20-51`, `web/src/views/Settings.tsx:280-325`, `web/src/components/LyricsEditDialog.tsx:1-20`, `PRODUCT.md:1-80`
**Anchors:** `export function Muse() {` @ `web/src/views/Muse.tsx`
`export function LyricsEditDialog({` @ `web/src/components/LyricsEditDialog.tsx`
**Steps:** 1. In the steer card, below `FeelPicker`, a `.lift` row: label `Lift`, a `<button type="button" role="switch" aria-checked={radioLift} aria-describedby="lift-d">` with a thumb span, the visible text `On`/`Off`, and `<p id="lift-d" className="note">` with the Design copy; click and Space/Enter call `setRadioLift(!radioLift)`. 2. When `!energyReady`, the energy note with a `Link` to `/settings` under the switch. 3. In Up next, when `radioReason === "untagged"`, a `.note` with `UNTAGGED_NOTE`; append an `Add lyrics` `link-btn` when `current` exists and `!current.has_lyrics`, opening `LyricsEditDialog` for the radio seed track (the `current` track when the seed is not in the queue). 4. `muse.css`: `.lift`, `.switch`, `.switch .thumb` per the Design section (transform-only, reduced-motion off). 5. `Settings.tsx`: replace the licence paragraph with the Design copy; under the status line, a `.folder-state` span `Energy: ready` or `Energy: not measured yet` from `useSmart().status?.energy_ready` when installed. 6. Run `node ~/.claude/skills/design-brain/scripts/preflight.mjs web/src/views/Muse.tsx web/src/styles/muse.css web/src/views/Settings.tsx` and fix what it flags.
**New symbols:** none
**Verify:** `npm --prefix web run build && node ~/.claude/skills/design-brain/scripts/preflight.mjs web/src/views/Muse.tsx web/src/styles/muse.css web/src/views/Settings.tsx` — expected: build exits 0, preflight reports no failures.
**Forbidden:** no new colours, fonts or radii; no em dash; don't touch `PlayerContext.tsx`.
**Depends:** Task 16
**Design:** Muse
**Why:** spec:68-69, spec:136-140.

### Task 18: Build the missing-lyrics Home card and list view [design]
- [ ] status
**Objective:** Home shows the missing-lyrics card when any exist; `/library/missing-lyrics` lists them with Add lyrics and No words; Library > Songs links to it.
**Write-scope:** `web/src/views/Home.tsx`, `web/src/views/MissingLyrics.tsx` (new), `web/src/views/Library.tsx`, `web/src/App.tsx`, `web/src/api.ts`, `web/src/styles/home.css`, `web/src/styles/library.css`
**Read-context:** `web/src/views/Home.tsx:100-140`, `web/src/views/Home.tsx:317-382`, `web/src/views/Library.tsx:240-262`, `web/src/views/Library.tsx:300-345`, `web/src/App.tsx:57-79`, `web/src/components/LyricsEditDialog.tsx:1-66`, `web/src/components/TrackRow.tsx:1-40`, `web/src/styles/home.css:1-15`
**Anchors:** `export type Dashboard = {` @ `web/src/api.ts`
`function Resume({` @ `web/src/views/Home.tsx`
`export function LyricsEditDialog({` @ `web/src/components/LyricsEditDialog.tsx`
**Steps:** 1. `api.ts`: `Dashboard` gains `missing_lyrics: number`; add `getMissingLyrics(signal)` (GET `/library/lyrics/missing`, `{ count: number; tracks: Track[] }`) and `markNoWords(rowid)` (POST `/tracks/${rowid}/lyrics/no-words`). 2. `Home.tsx`: after `Resume`, a `<section className="section" aria-labelledby="ml-h">` card per Design (only when `dash.missing_lyrics > 0`), primary `Link className="btn primary"` to `/library/missing-lyrics`. 3. `MissingLyrics.tsx`: `Screen` titled `Missing lyrics`; loads `getMissingLyrics`, refetches on `libraryVersion`; rows via `TrackRow` with `trailing` holding the two actions; Add lyrics opens `LyricsEditDialog`, `onSaved` refetches; No words calls `markNoWords`, drops the row, sets the status line; empty state per Design. 4. `App.tsx`: `<Route path="/library/missing-lyrics" element={<MissingLyrics />} />` above the `*` route. 5. `Library.tsx`: in the Songs toolbar, a `Link` `{n} missing lyrics` when count > 0 (fetch `getMissingLyrics` once when the Songs tab opens). 6. Styles per Design in `home.css` (`.ml-card`) and `library.css` (`.ml-actions`, wrap below 420px). 7. Run preflight on the changed TSX/CSS files.
**New symbols:** `export function getMissingLyrics(signal?: AbortSignal): Promise<{ count: number; tracks: Track[] }>`, `export function markNoWords(trackRowid: number): Promise<{ ok: boolean }>`, `export function MissingLyrics()`
**Verify:** `npm --prefix web run build && node ~/.claude/skills/design-brain/scripts/preflight.mjs web/src/views/Home.tsx web/src/views/MissingLyrics.tsx web/src/views/Library.tsx web/src/styles/home.css web/src/styles/library.css` — expected: build exits 0, preflight reports no failures.
**Forbidden:** no nav badge; no red; no em dash; don't touch `PlayerContext.tsx` or `Muse.tsx`.
**Depends:** Task 10, Task 16
**Design:** Missing lyrics
**Why:** Owner request (Assumptions 9-10): songs without lyrics get no Laya tags, so Radio can only follow their sound.

### Task 19: Cover Radio and missing lyrics end to end
- [ ] status
**Objective:** Playwright proves Radio fills Up next from the arc engine with the untagged note, Lift toggles, and the missing-lyrics card, list, No words and Add lyrics work.
**Write-scope:** `scripts/e2e_server.py`, `web/e2e/flows.spec.ts`
**Read-context:** `scripts/e2e_server.py:74-100`, `web/e2e/flows.spec.ts:1-75`, `web/e2e/app.ts:1-6`, `muse/scanner.py:121-131`, `muse/library/lyrics_state.py:106-141`
**Anchors:** `def scan(` @ `muse/scanner.py`
`def run_embed_batch(conn, audio_encoder: AudioEncoder, *, on_progress: Callable[[int, int, str], None] | None = None, should_stop: Callable[[], bool] | None = None) -> EmbedResult` @ `muse/embed.py` (from Task 5)
**Steps:** 1. `e2e_server.py`, before `muse_main()`: `conn = library.connect(data / "library.sqlite3")`; `scanner.scan(conn, [music], lyrics_root=data / "lyrics", art_root=data / "art")`; `embed.run_embed_batch(conn, embed.StubAudioEncoder())`; mark tracks titled `Song 02` and `Song 03` `lyrics_state` `not_found` via `lyrics_state.upsert(..., state="not_found", synced=False, checked_at=0)`; `conn.close()`. 2. New tests in the serial describe, placed before `edit details survives a reload` changes titles: (a) "radio follows the arc": open `/library` (Albums segment), click the `Album 00` cover link, play `First Light` from the album page (a two-track queue, so Radio has room to fill; a Songs-tab play queues all 12 and never asks), open `/muse`, expect `page.getByText(/ · Muse$/).first()` visible and the text `This song has no lyric tags, so Radio follows its sound.`; the `Lift` switch has `aria-checked="true"`, click it, expect `"false"`; (b) "missing lyrics": open `/`, expect `2 songs have no lyrics`, click `Add lyrics`, on `/library/missing-lyrics` click `Song has no words: Song 02`, expect `Marked Song 02 as having no words.` and `1 song` subtitle; Add lyrics for Song 03, fill `Lyrics` with `la la`, Save, expect `Every song has lyrics or is marked as having none.`. Adjust later tests only where a title they use was renamed.
**New symbols:** none
**Verify:** `npm --prefix web run build && npm --prefix web run e2e` — expected: all specs pass.
**Forbidden:** no network; no app source edits.
**Depends:** Task 17, Task 18
**Why:** spec:152.

## Phase 6 - Docs and the owner's gate

### Task 20: Update CONTEXT.md, CLAUDE.md and ADR notes [docs]
- [ ] status
**Objective:** The glossary, CLAUDE.md and ADRs 0003, 0004, 0008, 0009 describe one Laya-based path with three recommender legs and CLAP energy.
**Write-scope:** `CONTEXT.md`, `CLAUDE.md`, `docs/adrs/0003-emotion-and-taste-legs-in-the-recommender.md`, `docs/adrs/0004-content-embed-leg-and-the-shared-audio-budget.md`, `docs/adrs/0008-per-leg-calibration-and-the-offline-harness.md`, `docs/adrs/0009-the-arc-engine.md`
**Read-context:** `docs/specs/2026-11-07-arc-radio-and-model-retirement.md:22-46`, `docs/specs/2026-11-07-arc-radio-and-model-retirement.md:171`, `CONTEXT.md`, `CLAUDE.md`
**Anchors:** `Torch rule` @ `CLAUDE.md`
**Steps:** Audience: future maintainers and agents. 1. CLAUDE.md: worker ops list `hello`, `clap_audio`, `clap_text`, `mert`, `laya`, `align`; models line without MiniLM and the VA encoders; `embed` extra without sentence_transformers; recommender paragraph: legs audio (CLAP+MERT), features, taste; Radio is `GET /api/arc/{seed}` (`mode=radio`, `pos`, `lift`, `recent`, `exclude`) or `POST /api/arc` with a mood, wired into the UI, `radio_pos`/`radio_lift` in `player_state`; `/api/recommend` is a debug and `reco_eval` surface; energy is CLAP zero-shot (`muse/energy.py`, `prompt_vectors`); the missing-lyrics endpoints; add `muse/library/prompts.py` to the library module list and remove `emotion`. 2. CONTEXT.md: mark the encoder protocol, emotion vec, VA and mood target entries retired (one dated line each) and update the leg list wherever it names lyrics or emotion; add `energy` (CLAP zero-shot, library percentile on [-1, 1]) and `missing lyrics`. 3. Each ADR: a dated `Superseded in part (2026-11-07)` note at the top naming what changed and linking the spec; do not rewrite history. Verify every claim against code from Tasks 4-18.
**New symbols:** none
**Verify:** `git grep -niE "minilm|audio_va|lyrics_va|emotion_vec" CLAUDE.md CONTEXT.md` — expected: matches only inside text that says they were retired.
**Forbidden:** no README edits (PR #19 is separate); no code edits.
**Depends:** Task 19
**Why:** spec:171.

### Task 21: Write the owner's quality-gate runbook [docs]
- [ ] status
**Objective:** `docs/runbooks/2026-11-07-arc-radio-quality-gate.md` tells the owner exactly how to run both merge gates on the server and where to post the numbers.
**Write-scope:** `docs/runbooks/2026-11-07-arc-radio-quality-gate.md` (new)
**Read-context:** `docs/specs/2026-11-07-arc-radio-and-model-retirement.md:106-114`, `scripts/energy_check.py` (from Task 3), `muse/arc_eval.py:1-30`, `muse/reco_eval.py:196-206`
**Anchors:** `def main(argv=None) -> int` @ `scripts/energy_check.py` (from Task 3)
**Steps:** Audience: the owner on the server. Numbered steps: 1. Gate 1 before upgrading: copy the live `library.sqlite3` (still version 22) aside; from a branch checkout run `uv run python scripts/energy_check.py --db <copy>` with `MUSE_WORKER_CMD` or the installed worker; pass is `rho >= 0.5`; on fail, revise the prompts in `muse/energy.py` and rerun. 2. Gate 2: on `main`, `uv run python -m muse.arc_eval` against the live data dir, save output; copy the data dir, start the branch app once with `MUSE_DATA_DIR=<copy>` so migration 23 runs and the launch chain fills `prompt_vectors`, stop it, then `MUSE_DATA_DIR=<copy> uv run python -m muse.arc_eval`; pass is theme hold and curve error no worse than main. 3. Report-only: `uv run python -m muse.reco_eval` both sides. 4. Post all outputs on the PR; do not merge until both gates pass. Warn in plain prose that migration 23 drops columns irreversibly, so gate runs use copies.
**New symbols:** none
**Verify:** `test -f docs/runbooks/2026-11-07-arc-radio-quality-gate.md && grep -c "energy_check\|arc_eval\|reco_eval" docs/runbooks/2026-11-07-arc-radio-quality-gate.md` — expected: file exists, count >= 3.
**Forbidden:** no code edits.
**Depends:** Task 3
**Why:** spec:113-114: the branch must not merge until the owner posts both results.
