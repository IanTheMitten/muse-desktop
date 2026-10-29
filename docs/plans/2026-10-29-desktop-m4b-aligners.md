# Plan - Muse desktop M4b: lyric auto-align (schufo + MMS-FA), language routing, Auto-align UI, tap-along

Source spec: `docs/specs/2026-10-29-desktop-app-fork.md` (Status: ready for planning; `spec-lint --handoff` exit 0, 1 warn no-source-prd). Decision 6 (`docs/specs/2026-10-29-desktop-app-fork.md:237`), Decision 8 (`:294`), Error handling "Aligner returns nothing usable" (`:608`), Testing strategy aligner route (`:639`) and slow tier (`:645`), Tech stack worker line (`:676`). Earlier plan: `docs/plans/2026-10-29-desktop-m4a-worker.md` (M4a: worker, installer, Laya; tasks 1-16 + 7a done, Task 17 CI tag run held for the owner).

Repo: `~/Projects/muse-desktop`. Base: branch `worktree-m4-plan` at 85327b2 (M4a, unmerged). This plan's branch is `worktree-m4b-plan` (worktree `.claude/worktrees/m4b-plan`). Python commands run from the repo root, npm through `npm --prefix web`. `docs/plans/` and `docs/specs/` never ship.

## Scope

M4b per the M4a plan's split (`docs/plans/2026-10-29-desktop-m4a-worker.md:9`): schufo + MMS-FA aligners in the ML worker, language routing, Auto-align UI. Plus, from the owner this round, a tap-along timing mode (the spec's failure text "Couldn't align, tap along instead" names a tool that does not exist yet).

**Owner answers (2026-10-29, this planning round):**
- Build tap-along: the lyrics panel gets a mode where the song plays and a press on Space (or the Tap button) stamps each line; it saves an `.lrc`. It is also the fallback when Auto-align fails.
- Tracks that already have current timings: a single-track Auto-align asks "Replace the current timings?"; a bulk Auto-align skips them and reports how many.

**Not in this plan:** a vocal split before aligning (Decision 6, v1 has none); word-level karaoke highlighting (lines only); Japanese kanji readings beyond what `uroman` gives (see Risks); a VAD threshold UI; re-running Auto-align automatically after a lyric edit; the downloader (M5); signing (spec Deferred).

## Assumptions (decided at planning)

- **Line timing only.** Both aligners produce word or token onsets; the app keeps the onset of each lyric line's first alignable token. The `.lrc` has one `[mm:ss.xx]` stamp per non-empty `.txt` line, in `.txt` order, under the same `[ar:]`/`[ti:]`/`[al:]` header `muse/lyrics_lrclib.py:122` writes.
- **Lines that get timed** = `lyric_lines(text)`: the `.txt` split on line breaks, each stripped, empty ones dropped. Square-bracket sections (`[Chorus]`) stay as lines but contribute no words, so they take the next timed line's start.
- **No ctc-forced-aligner package.** The PyPI name `ctc-forced-aligner` (1.0.2) is a different project (Deskpai, "DOSL" licence, no MMS); MahmoudAshraf97's package is GitHub-only, builds a pybind11 extension, and hard-requires `torchcodec`, which M4a removed. MMS-FA runs here as `transformers` `Wav2Vec2ForCTC` + `uroman` + a small numpy CTC Viterbi (`muse/ctc_align.py`), following that package's method (30 s windows, 2 s context, batch 4, a zero-logprob `<star>` column, `<star>` between lines). Spec Deferred "library choices inside the worker" (`:692`) covers this.
- **Offline g2p.** `g2p_en` calls `nltk.download` at import (network at runtime), so it is not a dependency. `muse/g2p.py` looks words up in the CMU dict file (`cmudict.dict`, first pronunciation, stress digits stripped) and ports `g2p_en` 2.1.0's numpy GRU `predict` (Apache-2.0) for words not in the dict, using its `checkpoint20.npz`. Both files are downloaded weights, hash-pinned.
- **Language detection** in the worker: `py3langid` 0.4.0 (BSD-3) `classify(text)`. **Route:** schufo when the detected code is `en` and at least 90% of the letters are ASCII; MMS-FA for everything else (so a Korean song with English lines goes to MMS-FA, whose `uroman` romanization leaves Latin text alone).
- **schufo** model code is vendored into `muse/schufo.py` from `schufo/lyrics-aligner` @ `72be8a14b9126c201e0b8920513764c44ccd3f9e` (MIT, copyright line kept). Only changes: `torch.stft(..., return_complex=True)` wrapped in `torch.view_as_real` (torch 2 refuses the old call), PyAV decode (`muse/audio_decode.py`) instead of librosa, the phoneme map as a literal dict instead of `files/phoneme2idx.pickle`, no VAD (`--vad-threshold 0`, the README's default).
- **Weights** (two new manifest keys; manifest `version` becomes 2, so an existing install shows the banner again and downloads only the 6 new files):
  - `schufo`, from GitHub raw at pinned commits: `weights/schufo/model_parameters.pth` (`https://raw.githubusercontent.com/schufo/lyrics-aligner/72be8a14b9126c201e0b8920513764c44ccd3f9e/model_parameters.pth`, 40357640 bytes, sha256 `cf907661b6e3c477b24cf61f0bdf2a08aa1cfdc1e326f112861559a81785469e`); `weights/schufo/cmudict.dict` (`https://raw.githubusercontent.com/cmusphinx/cmudict/74790861f652b15e4ac49015a90074ad62a27690/cmudict.dict`, 3618488, `81917843c7f44ce2b094ac63873c2c7a4cf802040792c455ba3ca406891c3d22`); `weights/schufo/checkpoint20.npz` (`https://raw.githubusercontent.com/Kyubyong/g2p/c6439c274c42b9724a7fee1dc07ca6a4c68a0538/g2p_en/checkpoint20.npz`, 3342298, `b8af35e4596d8dd5836dfd3fe9b2ba4f97b9c311efe8879544cbcfcbd566d8c6`).
  - `mms` = `MahmoudAshraf/mms-300m-1130-forced-aligner` @ `49402e9577b1158620820667c218cd494cc44486`: `config.json` (2076, `2af7f73073d212b0ef91d3c73839355ca504c24a6b361c58a9e4e64210376ba9`), `model.safetensors` (1261930388, `9aa5229a1af4d7714285cfa14bcaab6af2a39a9e810b475722b24975d5cb1dab`), `vocab.json` (286, `6b6f1632d08b489faf2c954eb70e71f39d0861053feb9ae40da59cfa8d0ef5bb`). No feature extractor or tokenizer class: raw 16 kHz mono goes straight to the model, as the reference package does.
- **Worker op** `align {path, lines}` -> `{"aligner": "schufo"|"mms", "lang": "<py3langid code>", "starts": [seconds or null, one per line]}`. `PROTOCOL` stays 1 (a new op, nothing changed).
- **Usable result** (`fill_starts`, the spec's "too few onsets"): at least half the lines got a start, and the filled, non-decreasing list has at least `max(min(3, n), ceil(n / 2))` distinct values. Otherwise no `.lrc` is written and the track counts as failed.
- **Job** kind `align`, one job per request, tracks in order, run as an asyncio task (not under the chain lock: a first-install embed backfill can take hours, and `WorkerClient`'s own lock already serializes worker requests). A second request while one runs gets 409. Startup reaps a `running` align job like the others.
- **lyrics_state after a write:** Auto-align writes `state='aligned'`, tap-along writes `state='manual'`; both `synced=1`, `lrc_offset_ms=0` (an old nudge belongs to the old timings). Any row keeps LRCLIB away (`muse/library/lyrics_state.py:20-40`), and a later text edit still marks the `.lrc` stale through `save_text`.
- **Tap-along** stamps `positionMs - 150` (clamped at 0 and at the previous stamp): taps land late by about a reaction time. The offset nudge corrects the rest.
- **Deferred spec items decided here:** offline g2p library (CMU dict + ported g2p_en GRU) and language detection (`py3langid`) (`:692`); MUSDB18 and its lyrics extension are both licensed `other-nc` on Zenodo (records 1117372 and 3989267), recorded in the README (`:689`).
- **Worker release.** Nothing is published yet (M4a Task 17 never ran), so the release stays `worker-v1` and is built from this plan's CI run (Task 18), which replaces M4a Task 17.

## Risks

- MMS-FA through `uroman` romanizes kanji with Chinese readings, so Japanese lines heavy in kanji may align poorly. Tap-along is the fallback; owner check 3 measures it.
- schufo was trained on MUSDB18 (Western pop/rock, English). Quality on K-pop English songs is unmeasured; owner check 3.
- MMS-FA weights are CC-BY-NC-4.0: the app stays free and non-commercial (Decision 6).

## Premises

- Spec passes handoff lint. Verified: `node ~/.claude/skills/spec-brainstorming/scripts/spec-lint.mjs --handoff docs/specs/2026-10-29-desktop-app-fork.md` (exit 0, 1 warn).
- Baseline on 85327b2: 1159 passed, 2 skipped, 8 deselected. Verified: `uv run pytest -p no:cacheprovider`.
- Free disk 22 GB. Verified: `df -h /home/i4nle3`.
- No tap-along tool exists: the lyrics panel has tap-to-seek and the long-press offset nudge only. Verified: `grep -rni "tap along" web/src muse` (no hit), `web/src/views/SyncedLyrics.tsx:408-480`.
- No global Space key binding in the web app. Verified: `grep -rn "keydown" web/src --include=*.tsx` (MiniWindow, SyncedLyrics Escape, NowPlayingSheet Escape and drag-cancel only).
- NowPlayingSheet closes on Escape unless `.lyrics-offset` is open (`web/src/player/NowPlayingSheet.tsx:67`); the queue drag blocks that with a capture listener and `stopImmediatePropagation` (`:414-422`).
- schufo repo @ 72be8a1: MIT; `model.py` `InformedOpenUnmix3(vocab_size=44)` returns `(x, alphas, scores)`; `STFT.forward` calls `torch.stft` without `return_complex`; `align.py` loads audio with librosa at 16 kHz, runs `optimal_alignment_path(scores)` (numpy DTW, `init=200`) and `compute_phoneme_onsets(path, hop_length=256, sampling_rate=16000)`; the phoneme map is the 44 entries listed in Task 7. Verified: clone at `$CLAUDE_JOB_DIR/tmp/ext/lyrics-aligner`, `align.py:1-260`, `model.py:159-520`, restricted unpickle of `files/phoneme2idx.pickle`.
- The three raw URLs return the pinned bytes and answer `Range` with 206. Verified: curl + sha256sum script.
- `g2p_en` 2.1.0 imports nltk and calls `nltk.download` at module import; its `predict` is numpy only over `checkpoint20.npz`. Verified: wheel `g2p_en/g2p.py:7-30,93-150`.
- MMS-FA repo @ 49402e9: `Wav2Vec2ForCTC`, `vocab_size` 31, vocab `<blank>` 0 plus lowercase letters and `'`, licence cc-by-nc-4.0; reference windowing in MahmoudAshraf97/ctc-forced-aligner @ 64293cc `alignment_utils.py:136-186`. Verified: HF API and clone.
- PyPI: `uroman` 1.3.1.1 (pure Python, needs `regex`), `py3langid` 0.4.0 (BSD-3, needs numpy>=2; the lock has numpy 2.5.2), `ctc-forced-aligner` 1.0.2 is the Deskpai project. Verified: PyPI JSON API, `grep -n 'name = "numpy"' -A1 uv.lock`.
- numpy is a core dependency, so pure-numpy modules test in the default run. Verified: `pyproject.toml:7-16`.
- Worker op list is pinned by `tests/test_worker_main.py:62`; handlers are built in `real_handlers` (`muse/worker_main.py:57-118`), models lazily through `get(key, build)`.
- `job_rows.update` takes `state`, `progress`, `message` only, no `payload` (`muse/library/jobs.py:53-61`); `job_rows.create(conn, kind, payload)` (`:35-50`).
- `app.state.worker` is set in the lifespan (`muse/api.py:511`); route tests call endpoints off `app.routes` without the lifespan and set `app.state.config`/`conn` themselves (`tests/test_api_lyrics_synced.py:28-35`).
- Startup reaping at `muse/api.py:528-534`; shutdown cancels `install_tasks` at `muse/api.py:741`; smart install route pattern (task set, `asyncio.sleep(0)`) at `muse/api.py:877-896`; synced route at `muse/api.py:1611-1656`.
- `save_text`, `is_stale`, `keep_timings`, `drop_timings` in `muse/library/lyrics_edit.py:19-95`; `lyrics_files` at `muse/lyrics.py:19`; `read_lyrics` at `muse/lyrics.py:26`.
- Manifest today: version 1, 40 weight files, `workers` `{}`. Verified: `uv run python -c "...len(m['weights'])"` printed `1 40 3563532653 {}`.
- CI worker job fetches `mert,minilm` and smokes `hello,mert,minilm` (`.github/workflows/package.yml:61-63`); freeze flags are `--onedir` + `--collect-submodules muse` (`scripts/freeze_worker.py:20-29`).
- Frontend: `TrackAction` union and `ITEMS` at `web/src/components/TrackMenu.tsx:6-15`; `SelectionBar` `ACTIONS` at `web/src/components/SelectionBar.tsx:5-11`; `useTrackActions` at `web/src/components/TrackActions.tsx:13`; `useSmart()` at `web/src/components/SmartBanner.tsx:26`; `Job.payload` type at `web/src/jobs/JobsContext.tsx:25`; job labels at `web/src/components/JobProgress.tsx:34-43`; `Dialog` at `web/src/components/Dialog.tsx:4`; no `Record<TrackAction, ...>` anywhere (`grep -rn "TrackAction" web/src`).

## Memory carried into briefs

- **Worktree sessions refuse compound shell commands** (`a && b`, `a; b`, loops, `$(...)`, `-C <dir>` on git) and sometimes any command whose URL or argument looks git-like: run each Verify step as one plain command; put anything with loops or URLs in a script file written with the Write tool.
- **Fresh worktree needs `npm --prefix web ci`** before any web build or test.
- **`python` is not on PATH:** `uv run python`. Torch-needing commands use `uv run --extra embed ...`.
- **Red-test batching:** a committed red test breaks full-suite runs of sibling tasks. Briefs in a round with a red `[test]` task run only their own test files; the phase gate runs the full suite.
- **TestClient hangs on `/api/jobs/stream`:** never open the SSE route in a test.
- **Smoke after startup/API changes:** `bash scripts/smoke.sh` must print `SMOKE-OK`.
- **Never `pkill -f`/`pgrep -f` with a pattern in the same command line.** Use `pgrep -x muse-worker`.
- **Freeze after the web build finishes,** never in parallel. **Disk is tight:** delete `build/worker-dist/` and `build/pyinstaller-worker/` after recording results.
- **Real laya `system_one` and MERT quirks** (M4a memory) are unrelated to this plan; do not touch `laya`/`mert` handlers.
- **UI built in M2-M4a was never exercised in a browser:** owner checks cover it.

## Owner checks after the run (cannot be automated here)

1. Approve and watch Task 18 (CI tag run, billed macOS minutes), then: make the repo public, create release `worker-v1` from that run's `muse-worker-aarch64-apple-darwin.zip` and `muse-worker-x86_64-pc-windows-msvc.zip`, run `uv run python scripts/manifest_add_worker.py <triple> <zip> <url>` for each, commit `muse/smart_manifest.json`, rebuild. This replaces M4a Task 17. Laya v4 upload (M4a owner check 2) still applies.
2. Smart features install shows the extra ~1.3 GB and the banner returns once on a machine that installed version 1.
3. Auto-align on 5 English and 5 Korean songs you know, plus 2 Japanese: note how many lines land within half a second; Tap along on one; Re-align after editing one line's words.
4. Tap along: Space stamps, Backspace undoes, Escape leaves without closing Now Playing, Save shows the synced view.
5. M4a owner checks 1-6 and M3's six owner checks are still open.

## Design: auto-align

READ: Auto-align controls for a private desktop music player's lyrics panel and track menus, leaning on the existing muse-player record-drenched glass system: quiet text-link actions under the lyrics, no new chrome.
DIALS: VARIANCE 2 / MOTION 1 / DENSITY 4
PALETTE: Existing tokens only (drenched by the record): `--text`, `--text-2`, `--hairline`, `--glass`, `--ink-raised`; `--accent` only on the dialog's primary button. No new colours or tokens.
TYPE: display Newsreader (existing lyric lines and dialog title `.serif.dlg-title`) / body Figtree Variable / utility Figtree tabular-nums
SIGNATURE: Status lives in the panel's existing `.lyrics-note` line: while the job runs, "Aligning to the song…" replaces the action links; on failure the same line reads "Couldn't align, tap along instead" with the Tap along link inline. One line of text that changes, no spinner.
ANCHORS: Apple Music lyrics (actions tucked under the lyrics as quiet text links), Plexamp (background work in a thin strip, never modal), Spotify (track context menu as a plain verb list).
VARIETY: system: muse-player (same product as the 2026-10-28 ledger entry; coherence, no rotation). Adds two menu items, link-button states and one confirm dialog to existing surfaces.
CONTENT: menu item "Auto-align lyrics"; selection bar "Auto-align"; panel links "Auto-align", "Re-align", "Tap along"; running note "Aligning to the song…"; failure note "Couldn't align, tap along instead"; confirm dialog title "Replace the current timings?", body "These lyrics already have timings. Auto-align makes new ones from the song.", buttons "Replace" (primary) and "Keep"; error dialog title "Auto-align", bodies "Auto-align needs smart features. Install them in Settings." / "Already aligning other songs. Try again when that finishes." / "Nothing to align: these songs have no lyrics." with one "Close" (primary); job strip label "Aligning lyrics". No em dash anywhere.
FORMAT: production
TARGET: existing React 19 + Vite app: `web/src/components/TrackMenu.tsx`, `web/src/components/SelectionBar.tsx`, `web/src/components/TrackActions.tsx`, `web/src/components/AlignDialog.tsx` (new, built on `web/src/components/Dialog.tsx`), `web/src/views/SyncedLyrics.tsx`, styles in `web/src/styles/np.css` and `web/src/styles/actions.css`.
CONSTRAINTS: WCAG AA; the status line is `role="status" aria-live="polite"`; state never by colour alone; no spinner or decorative motion (DESIGN.md "No decorative motion"); reuse `.link-btn`, `.btn`, `.btn.primary`, `.dlg-actions`, `Dialog`; Escape behaviour unchanged; preflight clean.

## Design: tap-along

READ: A tap-to-time mode inside the Now Playing lyrics panel of a desktop music player, leaning toward a rhythm-instrument feel on the existing muse-player tokens: the lyrics are the score, one big pill is the drum.
DIALS: VARIANCE 3 / MOTION 2 / DENSITY 3
PALETTE: Drenched by the record, existing tokens: cue line `--text` at opacity 1 with the existing accent dot (`.lyrics-line-mark`), stamped lines at opacity 0.32 with their stamp in `--text-2`, upcoming lines 0.55 (DESIGN.md §7 values); Tap pill `--accent` fill with `--on-accent` label; secondary controls `--hairline` fill with `--text`; panel surface unchanged.
TYPE: display Newsreader 600 at the lyric line size `clamp(1.5rem, 1.05rem + 1.9vw, 2.3rem)` / body Figtree Variable / utility Figtree 700 tabular-nums for stamps ("0:42.1") and the counter
SIGNATURE: The Tap pill, full panel width and 64px tall, sits under the score; each press stamps the cue line, its time appears before the line in tabular numerals, and the accent dot steps to the next line, with a 0.15s `scale(.97)` press on the pill, so timing feels played, not typed.
ANCHORS: Apple Music lyrics (current line full, others dimmed; reused as is), Ableton Live tap tempo (one big target that registers each press), Musixmatch sync mode (stamp line by line while the song plays).
VARIETY: system: muse-player (same product; coherence, no rotation). New component inside the existing lyrics panel.
CONTENT: eyebrow "Tap along"; counter "Line 3 of 38"; hint under the pill "Press Space or tap as each line starts"; buttons "Tap", "Undo", "Start over", "Cancel"; when every line is stamped the Tap pill becomes "Save timings" (primary) beside "Undo"; save failure in `.error`: "Couldn't save the timings." Entering the mode seeks to 0:00 and plays.
FORMAT: production
TARGET: `web/src/views/TapAlong.tsx` (new), rendered by `LyricsBody` in `web/src/views/SyncedLyrics.tsx`; state logic in `web/src/player/tapAlong.ts` (from Task 6); styles in `web/src/styles/np.css`.
CONSTRAINTS: WCAG AA; Tap pill 64px tall (>=44px); Space on a window `keydown` listener with `preventDefault`, ignored when focus is in an input, textarea or an open `dialog`; Backspace = Undo; Escape = Cancel via a capture listener with `stopImmediatePropagation` so Now Playing stays open (pattern `web/src/player/NowPlayingSheet.tsx:414-422`); visible focus; `prefers-reduced-motion: reduce` drops the press scale and makes the cue scroll instant; only transform and opacity animate; no new tokens.

## Phase 1: preflight and pure modules

Round note: Task 2 commits a red test. Tasks 4, 5 and 6 verify with their own test files only; the phase gate runs the full suite (`uv run pytest -p no:cacheprovider`, `npm --prefix web test`).

### Task 1: Preflight the aligner artifacts and add the worker deps
- [x] status
**Objective:** A recorded proof that schufo's weights load into its model under torch 2.15, MMS-FA loads through transformers 5.22 from three files, and `uroman` and `py3langid` work offline, with timings for a 240 s input.
**Write-scope:** `pyproject.toml`
`uv.lock`
`scripts/align_preflight.py` (new)
`docs/plans/2026-10-29-desktop-m4b-preflight.md` (new)
**Read-context:** `pyproject.toml:18-28`, `scripts/worker_preflight.py:1-40`, `muse/smart.py:30-36` (`ManifestFile`), `muse/smart.py:84-115` (`download`), this plan's Assumptions "Weights"
**Anchors:** `def decode_mono(` @ `muse/audio_decode.py`
`class ManifestFile:` @ `muse/smart.py`
**Steps:**
1. Run `df -BG --output=avail /home/i4nle3`; under 5G write the notes file with "Stopped: free disk first" and stop.
2. Add `"uroman==1.3.1.1"` and `"py3langid==0.4.0"` to the `embed` extra in `pyproject.toml`; run `uv lock`, then `uv sync --extra embed`.
3. Write `scripts/align_preflight.py` (docstring with its run command). It: (a) downloads the three `schufo` files with `smart.download(ManifestFile(path="weights/schufo/<name>", url=..., sha256=..., size=...), Path.home() / ".cache" / "muse-test-weights")` using the Assumptions values; (b) fetches `https://raw.githubusercontent.com/schufo/lyrics-aligner/72be8a14b9126c201e0b8920513764c44ccd3f9e/model.py` with `requests` into `build/schufo-src/model.py`, imports it with `importlib.util.spec_from_file_location`, replaces `STFT.forward` with a version calling `torch.view_as_real(torch.stft(..., return_complex=True))`, builds `InformedOpenUnmix3()`, loads `torch.load(path, map_location="cpu", weights_only=True)` with `strict=True` (if `weights_only=True` raises, record the error, retry with `weights_only=False`, and record that), runs it under `torch.no_grad()` on `tests/fixtures/smoke/tone.wav` (`decode_mono(path, 16000)`) with a 12-token sequence `[3,26,5,3,27,7,3,35,22,3,24,3]`, prints the `scores` shape, then times 240 s of `np.random.default_rng(0).standard_normal(240*16000)*0.1` with 1500 tokens (id 3 every 4th, else `5 + i % 39`); (c) `huggingface_hub.snapshot_download("MahmoudAshraf/mms-300m-1130-forced-aligner", revision="49402e9577b1158620820667c218cd494cc44486", allow_patterns=["config.json", "model.safetensors", "vocab.json"])`, `transformers.Wav2Vec2ForCTC.from_pretrained(dir).eval()`, prints logits shape on the tone, then times eight 34 s windows of the same noise in batches of 4; (d) `uroman.Uroman().romanize_string(s, lcode=c)` for `("사랑해 너를", "kor")`, `("愛してる", "jpn")`, `("hello", None)` with its init time; (e) `py3langid.classify` on `"I will always love you, baby"`, `"너를 사랑해 영원히"`, `"君を愛してる ずっと"`; (f) prints `PREFLIGHT-OK` last when every step passed.
4. Run it; write the notes file: one table row per check (result, seconds, peak RSS from `resource.getrusage`), the stft fix, the `weights_only` outcome, PyInstaller hints (`--collect-data uroman`, `--collect-data py3langid` expected), and a line `verdict: go` or `verdict: stop <reason>`.
5. Delete `build/schufo-src/`.
**New symbols:** none
**Verify:** `uv run --extra embed python scripts/align_preflight.py` — expected: last line `PREFLIGHT-OK`; the lines before it show logits last dim `31` and classify codes `en`, `ko`, `ja`.
**Forbidden:** no muse/ source edits; no weights outside `~/.cache/muse-test-weights` and the HF cache; no `pytorch_model.bin` download.
**Depends:** none
**Why:** Four later tasks build on these artifacts. A wrong load path found here costs minutes, not a phase.

### Task 2: Write the align_text tests (red) [test]
- [x] status
**Objective:** `tests/test_align_text.py` pins the pure alignment helpers and fails only because `muse.align_text` does not exist yet.
**Write-scope:** `tests/test_align_text.py` (new)
**Read-context:** `muse/lrc.py:34-80`, `muse/lyrics_lrclib.py:122-139`
**Anchors:** `def parse_lrc(text: str) -> list[LrcLine]:` @ `muse/lrc.py`; `muse/align_text.py` (from Task 3)
**Steps:**
1. Import `from muse.align_text import MMS, SCHUFO, build_lrc, fill_starts, iso3, lrc_stamp, lyric_lines, route, words`.
2. `lyric_lines("  a \n\n b\r\n[Chorus]\n")` == `["a", "b", "[Chorus]"]`; `lyric_lines("")` == `[]`.
3. `words`: `"Hello, World!"` -> `["hello", "world"]`; `"[Chorus]"` -> `[]`; `"Don’t stop (oh)"` -> `["don't", "stop", "oh"]`; `"24/7 love"` -> `["love"]`.
4. `route`: `("hello world", "en")` -> `SCHUFO`; `("사랑해 baby", "ko")` -> `MMS`; `("hello 사랑해요 사랑해요", "en")` -> `MMS`; `("hola mi amor", "es")` -> `MMS`; `("", "en")` -> `MMS`; `SCHUFO == "schufo"`, `MMS == "mms"`.
5. `iso3`: `"en"` -> `"eng"`, `"ko"` -> `"kor"`, `"ja"` -> `"jpn"`, `"xx"` -> `None`.
6. `fill_starts`: `[1.0, 2.0, 3.0]` -> `[1000, 2000, 3000]`; `[None, 2.0, 3.0, 4.0]` -> `[2000, 2000, 3000, 4000]`; `[1.0, 2.0, 3.0, None]` -> `[1000, 2000, 3000, 3000]`; `[1.0, 3.0, 2.0, 4.0]` -> `[1000, 3000, 3000, 4000]`; `[None] * 4` -> `None`; `[1.0, None, None, None]` -> `None`; `[5.0, 5.0, 5.0, 5.0, 6.0, 7.0]` -> `[5000, 5000, 5000, 5000, 6000, 7000]`; `[5.0] * 5 + [6.0]` -> `None`; `[]` -> `None`; `[1.5]` -> `[1500]`; `[0.0004]` -> `[0]`.
7. `lrc_stamp(61234)` == `"[01:01.23]"`; `lrc_stamp(0)` == `"[00:00.00]"`.
8. `build_lrc(["one", "two"], [1000, 61234], {"artist_name": "A", "track_name": "T", "album_name": None})` == `"[ar:A]\n[ti:T]\n[00:01.00]one\n[01:01.23]two\n"`; `parse_lrc` of it gives ms `[1000, 61230]` and texts `["one", "two"]`.
**New symbols:** none
**Verify:** `uv run pytest tests/test_align_text.py -p no:cacheprovider` — expected: collection error `ModuleNotFoundError: No module named 'muse.align_text'`.
**Forbidden:** no source edits; no other test files.
**Depends:** none
**Why:** These helpers are the contract the worker, the job and the UI share; test-first pins the gate rules before anyone tunes them.

### Task 3: Implement muse/align_text.py
- [x] status
**Objective:** The pure helpers exist and `tests/test_align_text.py` passes.
**Write-scope:** `muse/align_text.py` (new)
**Read-context:** `tests/test_align_text.py` (from Task 2), `muse/lyrics_lrclib.py:122-139`, `muse/lrc.py:34-40`
**Anchors:** `def build_lrc(candidate: dict, track: dict) -> str:` @ `muse/lyrics_lrclib.py`
**Steps:**
1. Module docstring: pure, no torch, shared by the worker and the sidecar; line timing only.
2. `lyric_lines`: `[s for s in (l.strip() for l in text.splitlines()) if s]`.
3. `words`: drop `\[[^\]]*\]` spans, lowercase, `’` to `'`, keep chars that are `isalnum()` or `'` or space, split on whitespace, keep tokens with at least one `isalpha()` char.
4. `route(text, lang)`: letters = `[c for c in text if c.isalpha()]`; `SCHUFO` when `lang == "en"` and letters and ASCII share >= 0.9, else `MMS`.
5. `iso3`: dict `en eng, ko kor, ja jpn, zh zho, es spa, fr fra, de deu, pt por, it ita, ru rus, th tha, vi vie, id ind, tl tgl`; `.get`.
6. `fill_starts(raw)`: empty or aligned count `< ceil(n / 2)` -> `None`; ms = `round(s * 1000)`; fill each `None` from the next value, trailing ones from the previous; running max; distinct `< max(min(3, n), ceil(n / 2))` -> `None`.
7. `lrc_stamp(ms)`: `f"[{m:02d}:{s:02d}.{cs:02d}]"` with centiseconds truncated.
8. `build_lrc(lines, starts_ms, track)`: header `[ar:]`, `[ti:]`, `[al:]` for truthy fields, same order as `muse/lyrics_lrclib.py:131-137`, then `stamp + line` per line, `"\n"`-joined with a trailing newline.
**New symbols:** `SCHUFO = "schufo"`; `MMS = "mms"`; `def lyric_lines(text: str) -> list[str]`; `def words(line: str) -> list[str]`; `def route(text: str, lang: str) -> str`; `def iso3(lang: str) -> str | None`; `def fill_starts(raw: list[float | None]) -> list[int] | None`; `def lrc_stamp(ms: int) -> str`; `def build_lrc(lines: list[str], starts_ms: list[int], track: dict) -> str`
**Verify:** `uv run pytest tests/test_align_text.py -p no:cacheprovider` — expected: all pass.
**Forbidden:** no imports beyond `math`, `re`; don't edit the test (a wrong test is a FIXTURE-DEFECT report).
**Depends:** Task 2
**Why:** One place decides which lines get timed, which aligner runs, and what counts as usable.

### Task 4: Add the numpy CTC forced aligner
- [x] status
**Objective:** `muse/ctc_align.py` finds the best CTC path for a target sequence and returns each target's first frame, proven against brute force.
**Write-scope:** `muse/ctc_align.py` (new)
`tests/test_ctc_align.py` (new)
**Read-context:** this brief only
**Anchors:** none (new module, numpy only)
**Steps:**
1. `forced_align(log_probs, targets, blank=0)`: `log_probs` float32 `(T, C)`; L = len(targets); extended labels `[blank, t0, blank, t1, ..., blank]` (S = 2L + 1). Viterbi: `alpha[0] = log_probs[0, ext[0]]`, `alpha[1] = log_probs[0, ext[1]]`, rest `-inf`; per frame t, each state takes the best of stay, from s-1, and from s-2 when `ext[s] != blank and ext[s] != ext[s-2]`; store the choice (0, 1, 2) in an `int8` `(T, S)` array; vectorize over S inside the frame loop. End at the better of the last two states; if both are `-inf`, raise `ValueError("Too few frames for the text")`. Backtrack; target i's frame = first t whose state is `2i + 1`. Return `(frames, score)`.
2. `ValueError("Empty transcript")` on empty targets; `ValueError` when a target equals `blank`.
3. Tests: (a) T=6, C=3, targets `[1, 2]`, log_probs `log(0.9)` on label 1 at frames 1-2, label 2 at frame 4, blank elsewhere, `log(0.05)` for the rest -> frames `[1, 4]`; (b) targets `[1, 1]` with T=3 needs a blank between: frames `[0, 2]`; (c) T=2, targets `[1, 1]` raises `ValueError`; (d) for seeds 0-19, random `log_softmax` T=6 C=4 targets of length 2 from `{1,2,3}`: returned score equals the brute-force max over all valid CTC paths (enumerate all `S^T` state sequences, keep valid ones) within `1e-5`.
**New symbols:** `def forced_align(log_probs: np.ndarray, targets: list[int], blank: int = 0) -> tuple[list[int], float]`
**Verify:** `uv run pytest tests/test_ctc_align.py -p no:cacheprovider` — expected: all pass.
**Forbidden:** no torch; no new deps; don't touch other modules.
**Depends:** none
**Why:** Replaces the C++ extension of a package we cannot install; small enough to own and test.

### Task 5: Add the offline g2p
- [x] status
**Objective:** `muse/g2p.py` turns an English word into stress-free ARPAbet phonemes from the CMU dict, falling back to g2p_en's GRU for unknown words.
**Write-scope:** `muse/g2p.py` (new)
`tests/test_g2p.py` (new)
**Read-context:** g2p_en's source at https://raw.githubusercontent.com/Kyubyong/g2p/c6439c274c42b9724a7fee1dc07ca6a4c68a0538/g2p_en/g2p.py lines 50-150 (fetch it with a script file written by the Write tool; read the G2p init phoneme and grapheme lists, grucell, gru, encode, predict)
**Anchors:** none (new module, numpy only)
**Steps:**
1. Header comment: GRU predict ported from g2p_en 2.1.0 (Kyubyong Park, Jongseok Kim), Apache-2.0; CMU dict (c) Carnegie Mellon University, BSD-style.
2. `__init__`: parse `cmudict_path` (UTF-8): skip blank lines and `;;;` comments, cut at `#`, word = first field; a `word(2)` variant is skipped; keep the first pronunciation only. Store graphemes/phonemes lists exactly as g2p_en. If `npz_path` is given, load it with `np.load` (allow_pickle False) into the same variables as g2p_en's `load_variables`.
3. `phonemes(word)`: lowercase; in dict -> its phonemes; else if npz loaded and word has a-z letters -> `predict` on the a-z/`'` chars; else `[]`. Strip trailing stress digits (`AH0` -> `AH`) in both paths; drop `<unk>`/`<pad>`/`</s>`.
4. Tests (tmp_path dict file `"hello HH AH0 L OW1\nhello(2) HH EH0 L OW1\n;;; c\nworld W ER1 L D # x\n"`): `phonemes("Hello")` == `["HH", "AH", "L", "OW"]`; `phonemes("world")` == `["W", "ER", "L", "D"]`; `phonemes("zzyzx")` == `[]` without npz; npz path `None` default. The predict path is covered by the slow test (Task 16).
**New symbols:** `class G2p:` with `def __init__(self, cmudict_path: Path, npz_path: Path | None = None)` and `def phonemes(self, word: str) -> list[str]`
**Verify:** `uv run pytest tests/test_g2p.py -p no:cacheprovider` — expected: all pass.
**Forbidden:** no nltk, no g2p_en dependency, no torch; no network at runtime.
**Depends:** none
**Why:** schufo needs phonemes and the README's manual CMU web step cannot run inside an app.

### Task 6: Add the tap-along state logic
- [x] status
**Objective:** `web/src/player/tapAlong.ts` holds the tap-along state as pure functions with vitest coverage.
**Write-scope:** `web/src/player/tapAlong.ts` (new)
`web/src/player/tapAlong.test.ts` (new)
**Read-context:** `web/src/player/queue.test.ts:1-30` (test style), `web/src/player/crossfade.ts` (pure-module style)
**Anchors:** none (new module)
**Steps:**
1. Run `npm --prefix web ci` first if `web/node_modules` is missing.
2. `TAP_LEAD_MS = 150`. `TapState = { stamps: number[]; total: number }`.
3. `startTap(total)` -> `{ stamps: [], total }`. `tap(state, positionMs)`: when `stamps.length >= total` return state unchanged; else push `Math.max(0, positionMs - TAP_LEAD_MS, last ?? 0)` (rounded). `undo(state)` drops the last stamp (no-op when empty). `isDone(state)` -> `stamps.length === total`. `cueIndex(state)` -> `stamps.length`.
4. Tests: start; three taps at 1000, 900, 5000 give `[850, 850, 4850]`; tap at 100 gives `[0]`; tap past total is a no-op; undo twice then tap; isDone; cueIndex.
**New symbols:** `export const TAP_LEAD_MS = 150`; `export type TapState = { stamps: number[]; total: number }`; `export function startTap(total: number): TapState`; `export function tap(state: TapState, positionMs: number): TapState`; `export function undo(state: TapState): TapState`; `export function isDone(state: TapState): boolean`; `export function cueIndex(state: TapState): number`
**Verify:** `npm --prefix web test` — expected: all files pass, including `tapAlong.test.ts`.
**Forbidden:** no React in this module; no new deps.
**Depends:** none
**Why:** Keeps the mode's rules testable outside the component, the same split as `queue.ts` and `crossfade.ts`.

## Phase 2: aligners in the worker, the align job and its API

Round note: Task 11 commits red tests. Tasks 7-10 and 12 verify with their own test files only; the phase gate runs the full suite.

### Task 7: Vendor schufo into the worker
- [x] status
**Objective:** `SchufoAligner(source).align(path, lines)` returns one start in seconds (or None) per lyric line.
**Write-scope:** `muse/schufo.py` (new)
**Read-context:** `docs/plans/2026-10-29-desktop-m4b-preflight.md` (from Task 1), schufo `model.py` and `align.py` at `https://raw.githubusercontent.com/schufo/lyrics-aligner/72be8a14b9126c201e0b8920513764c44ccd3f9e/` (fetch into `build/schufo-src/` with a script file; delete after), `muse/align_text.py` (from Task 3), `muse/g2p.py` (from Task 5)
**Anchors:** `def decode_mono(` @ `muse/audio_decode.py`; `def words(line: str) -> list[str]` @ `muse/align_text.py` (from Task 3); `class G2p:` @ `muse/g2p.py` (from Task 5)
**Steps:**
1. Header: vendored from schufo/lyrics-aligner @ 72be8a1, `Copyright (c) 2021 Kilian Schulze-Forster`, MIT licence text, paper citation line; list the changes from Assumptions.
2. Copy verbatim from `model.py`: `MatrixDiagonalIndexIterator`, `dtw_matrix` and what it calls, `STFT` (stft fix as recorded by Task 1), `Spectrogram`, `index2one_hot`, `InformedOpenUnmix3`. Copy from `align.py`: `compute_phoneme_onsets`, `accumulated_cost_numpy`, `optimal_alignment_path` (point `model.MatrixDiagonalIndexIterator` at the local class; drop the `print`). torch imports at module top are fine: only the worker imports this module.
3. `PHONEME2IDX` literal: `{'#': 0, '$': 1, '%': 2, '>': 3, '-': 4, 'AA': 5, 'AE': 6, 'AH': 7, 'AO': 8, 'AW': 9, 'AY': 10, 'B': 11, 'CH': 12, 'D': 13, 'DH': 14, 'EH': 15, 'ER': 16, 'EY': 17, 'F': 18, 'G': 19, 'HH': 20, 'IH': 21, 'IY': 22, 'JH': 23, 'K': 24, 'L': 25, 'M': 26, 'N': 27, 'NG': 28, 'OW': 29, 'OY': 30, 'P': 31, 'R': 32, 'S': 33, 'SH': 34, 'T': 35, 'TH': 36, 'UH': 37, 'UW': 38, 'V': 39, 'W': 40, 'Y': 41, 'Z': 42, 'ZH': 43}`.
4. `__init__`: model `InformedOpenUnmix3()`, state dict from `source / "model_parameters.pth"` (`map_location="cpu"`, `weights_only` per Task 1), `strict=True`, `.eval()`; `G2p(source / "cmudict.dict", source / "checkpoint20.npz")`.
5. `align`: tokens `['>']`; per line, per `words(line)`, phonemes from g2p filtered to `PHONEME2IDX`; skip empty words; append phonemes then `'>'`; remember each line's first phoneme index. No words at all -> `[None] * len(lines)`. Audio `decode_mono(path, 16000)`; forward under `torch.no_grad()` with `(audio[None, None, :], idx[None, :] as float32)`; `optimal_alignment_path(scores)`; `compute_phoneme_onsets(path, 256, 16000)`; line start = onset at its first phoneme index, else None.
**New symbols:** `PHONEME2IDX: dict[str, int]`; `class SchufoAligner:` with `def __init__(self, source: Path)` and `def align(self, audio_path: Path, lines: list[str]) -> list[float | None]`
**Verify:** `uv run --extra embed python -c "import muse.schufo as s; print(len(s.PHONEME2IDX), s.InformedOpenUnmix3().vocab_size)"` — expected: `44 44`.
**Forbidden:** no librosa, no pickle loading, no network at runtime; no edits outside `muse/schufo.py`.
**Depends:** Task 1, Task 3, Task 5
**Why:** The English aligner; vendoring keeps one pinned, patched copy instead of an unmaintained 2021 dependency.

### Task 8: Add the MMS-FA aligner
- [x] status
**Objective:** `MmsAligner(source).align(path, lines, lang3)` returns one start in seconds (or None) per lyric line.
**Write-scope:** `muse/mms_align.py` (new)
**Read-context:** `docs/plans/2026-10-29-desktop-m4b-preflight.md` (from Task 1), `muse/mert.py:1-60` (model-load style), `muse/ctc_align.py` (from Task 4), this plan's Assumptions "No ctc-forced-aligner package"
**Anchors:** `def decode_mono(` @ `muse/audio_decode.py`; `def forced_align(log_probs: np.ndarray, targets: list[int], blank: int = 0) -> tuple[list[int], float]` @ `muse/ctc_align.py` (from Task 4)
**Steps:**
1. Constants `MMS_MODEL`, `MMS_REVISION`, `WINDOW_S = 30`, `CONTEXT_S = 2`, `BATCH = 4`, `FRAME_S = 0.02` (stride 320 / 16000).
2. `__init__`: `Wav2Vec2ForCTC.from_pretrained(str(source))` when `source`, else `from_pretrained(MMS_MODEL, revision=MMS_REVISION)`; `.eval()`. Vocab from `source / "vocab.json"`, else `huggingface_hub.hf_hub_download(MMS_MODEL, "vocab.json", revision=MMS_REVISION)`; lowercase keys; `star = len(vocab)`; `blank = vocab["<blank>"]`. `self._uroman = uroman.Uroman()`.
3. Emissions: audio `decode_mono(path, 16000)` as a float32 tensor; shorter than one window -> one pass; else pad `(context, context + extension)`, `unfold(0, window + 2*context, window)`, batches of 4 under `torch.inference_mode()`, crop each to its window frames, flatten, drop the extension frames; `log_softmax(-1)`; append a zero column (star). numpy float32 `(T, len(vocab) + 1)`.
4. Text per line: `romanize_string(line, lcode=lang3)`, lowercase, `re.sub("[^a-z' ]", " ", ...)`, keep non-space chars present in the vocab. Targets `[star]` then per non-empty line its char ids plus `[star]`; remember each line's first target index. No targets -> `[None] * len(lines)`.
5. `frames, _ = forced_align(emissions, targets, blank)`; line start = `frames[i] * FRAME_S`, else None. `ValueError` from `forced_align` propagates (the worker returns it as an error reply).
**New symbols:** `MMS_MODEL = "MahmoudAshraf/mms-300m-1130-forced-aligner"`; `MMS_REVISION = "49402e9577b1158620820667c218cd494cc44486"`; `class MmsAligner:` with `def __init__(self, source: Path | None = None)` and `def align(self, audio_path: Path, lines: list[str], lang3: str | None) -> list[float | None]`
**Verify:** `uv run --extra embed python -c "import muse.mms_align as m; print(m.MMS_REVISION[:7], m.FRAME_S)"` — expected: `49402e9 0.02`.
**Forbidden:** no ctc-forced-aligner, torchaudio or torchcodec; no `pytorch_model.bin`; no edits outside `muse/mms_align.py`.
**Depends:** Task 1, Task 3, Task 4
**Why:** The aligner for every non-English song, including ko and ja.

### Task 9: Add the worker's align op
- [x] status
**Objective:** The worker answers `align {path, lines}` with the route it chose, the detected language and the per-line starts.
**Write-scope:** `muse/worker_main.py`
`tests/test_worker_main.py`
**Read-context:** `muse/worker_main.py:57-118`, `tests/test_worker_main.py:50-75`, `muse/align_text.py` (from Task 3)
**Anchors:** `def real_handlers(weights: Path) -> dict[str, Callable[[dict], object]]:` @ `muse/worker_main.py`; `def route(text: str, lang: str) -> str` @ `muse/align_text.py` (from Task 3); `def iso3(lang: str) -> str | None` @ `muse/align_text.py` (from Task 3)
**Steps:**
1. Module-level `detect_language(text)`: lazy `import py3langid`; return `py3langid.classify(text)[0]`.
2. In `real_handlers`: `schufo()` builds `SchufoAligner(weights / "schufo")` through `get("schufo", ...)`, raising `RuntimeError("Aligner weights missing")` when that dir is missing; `mms()` builds `MmsAligner(source=src("mms"))` through `get("mms", ...)`. Lazy imports inside, as the other builders.
3. Handler `align(args)`: `lines = args["lines"]`, `text = "\n".join(lines)`, `lang = detect_language(text)`, `aligner = route(text, lang)`; starts from `schufo().align(Path(args["path"]), lines)` or `mms().align(Path(args["path"]), lines, iso3(lang))`; return `{"aligner": aligner, "lang": lang, "starts": starts}`. Register it as `"align"`.
4. `tests/test_worker_main.py:62`: add `"align"` to the sorted list (the subprocess test's `torch` check stays `False`). New test: monkeypatch `muse.worker_main.detect_language` to return `"en"`, then `real_handlers(tmp_path)["align"]({"path": "x.wav", "lines": ["hello world"]})` raises `RuntimeError` matching `Aligner weights missing` (no `schufo` dir under `tmp_path`).
**New symbols:** `def detect_language(text: str) -> str`; worker op `align`
**Verify:** `uv run pytest tests/test_worker_main.py -p no:cacheprovider` — expected: all pass.
**Forbidden:** don't touch the laya or mert handlers; no torch import at module level; `PROTOCOL` stays 1.
**Depends:** Task 1, Task 3
**Why:** One op hides both aligners and the routing from the sidecar.

### Task 10: Pin the aligner weights and wire packaging
- [x] status
**Objective:** The manifest (version 2) carries the six aligner files, the frozen worker bundles uroman and py3langid data, and CI smokes the `align` op.
**Write-scope:** `scripts/build_manifest.py`
`muse/smart_manifest.json`
`scripts/freeze_worker.py`
`scripts/worker_smoke.py`
`.github/workflows/package.yml`
**Read-context:** `scripts/build_manifest.py:1-100`, `scripts/freeze_worker.py:13-30`, `scripts/worker_smoke.py:1-60`, `.github/workflows/package.yml:45-70`, `docs/plans/2026-10-29-desktop-m4b-preflight.md` (from Task 1)
**Anchors:** `def hf_entries(key: str, repo: str, rev: str, files: list[str]) -> list[dict]:` @ `scripts/build_manifest.py`
**Steps:**
1. `build_manifest.py`: add `"mms": ("MahmoudAshraf/mms-300m-1130-forced-aligner", "49402e9577b1158620820667c218cd494cc44486", ["config.json", "model.safetensors", "vocab.json"])` to `PINNED`; add a `RAW` list with the three `schufo` entries (path, url, sha256, size) exactly as in Assumptions, appended after the HF entries without downloading; write `"version": 2`.
2. Run `uv run python scripts/build_manifest.py` (network; reads `~/models/laya-muse-v4`).
3. `freeze_worker.py`: add `"--collect-data", "uroman"`, `"--collect-data", "py3langid"` plus any flag the preflight notes list; update the trailing comment.
4. `worker_smoke.py`: for op `align` send `path` = fixture and `lines = ["hello there", "how are you"]`; pass when `ok["aligner"] in ("schufo", "mms")` and `len(ok["starts"]) == 2`.
5. `package.yml` worker job: fetch keys `mert,minilm,schufo,mms`; smoke ops `hello,mert,minilm,align`.
**New symbols:** none
**Verify:** `uv run python -c "import json; m = json.load(open('muse/smart_manifest.json')); w = {e['path']: e for e in m['weights']}; print(m['version'], len(m['weights']), w['weights/mms/model.safetensors']['sha256'][:8], w['weights/schufo/cmudict.dict']['size'])"` — expected: `2 46 9aa5229a 3618488`.
**Forbidden:** no `pytorch_model.bin`; keep the existing `workers` object; don't edit `muse/smart.py`.
**Depends:** Task 1
**Why:** Installer, freeze and CI each need to know the new files; one task keeps them in step.

### Task 11: Write the align job and API tests (red) [test]
- [x] status
**Objective:** Two test files pin the align job runner, the timing writer and the three routes, failing only on the missing symbols.
**Write-scope:** `tests/test_align_job.py` (new)
`tests/test_api_align.py` (new)
**Read-context:** `tests/test_api_lyrics_synced.py:1-60` (route-call pattern, `_seed`), `tests/conftest.py:13-40,88-140`, `muse/library/lyrics_edit.py:19-95`, Task 12's New symbols
**Anchors:** `def insert_track(conn, rowid, *, track_id="auto", status="done", album_rowid=20,` @ `tests/conftest.py`; `def make_config(tmp_path, **overrides) -> Config:` @ `tests/conftest.py`; `async def run_align_job(conn, bus: JobBus, job_id: int, items: list[dict], align, *, lyrics_root: Path) -> dict` @ `muse/jobs.py` (from Task 12); `def write_timings(conn: sqlite3.Connection, track_rowid: int, lines: list[str], starts_ms: list[int], *, lyrics_root: Path, state: str, track: dict, now: int | None = None) -> None` @ `muse/library/lyrics_edit.py` (from Task 12)
**Steps:**
1. `test_align_job.py` (real `conn`, `JobBus()`, fake `align(path, lines)` returning `{"starts": ...}` or raising `WorkerReplyError`): items with keys `track_rowid, file_path, lines, artist_name, track_name, album_name`; job created with `job_rows.create(conn, "align", {"track_rowids": [...], "total": n, "done": 0, "aligned": [], "failed": [], "skipped_synced": 0})`. Cases: two good tracks -> both `.lrc` files equal `build_lrc(...)`, `lyrics_state` `state='aligned'`, `synced=1`, `lrc_offset_ms=0` (seed 250 first), job `done`, payload `aligned == [1, 2]`, message `"Aligned 2 of 2"`; one unusable (`[None, None]`) and one raising -> no `.lrc`, payload `failed`, message `"Aligned 0 of 2, couldn't align 2"`; `skipped_synced: 3` appends `", skipped 3 already synced"`; single item failing -> message `"Couldn't align these lyrics"`; single success -> `"Aligned"`. `write_timings(state="manual")` writes the file and the row.
2. `test_api_align.py` (route-call pattern; `make_config(tmp_path, embed_batch=True)`; set `app.state.worker` to a fake with `begin()` and `request(op, **args)`; await `asyncio.gather(*app.state.align_tasks)`): POST `/api/tracks/align` without smart (`embed_batch=False`, no `installed.json`) -> `HTTPException` 503 `"Auto-align needs smart features"`; active align job -> 409 `"Already aligning other songs"`; a track without lyrics -> `skipped` `[{"track_rowid": 1, "reason": "no_lyrics"}]` and `job_id` None; a track with a current `.lrc` -> reason `"synced"`; same with `replace=True` -> aligned; fake request receives `op == "align"`, `path` = the track's `file_path`, `lines` = `lyric_lines` of its text. PUT `/api/tracks/{track_rowid}/lyrics/lrc`: wrong count -> 400 `"Timings do not match the lyrics"`; decreasing -> 400 `"Timings must not go backwards"`; unknown track -> 404; good -> `{"ok": True}`, then GET `/lyrics/synced` has `synced` True and `timing_lines` equal to the text lines. GET synced on a plain track includes `timing_lines`.
**New symbols:** none
**Verify:** `uv run pytest tests/test_align_job.py tests/test_api_align.py -p no:cacheprovider` — expected: errors naming `run_align_job`, `write_timings` or the missing routes; nothing else.
**Forbidden:** no source edits; never open `/api/jobs/stream`.
**Depends:** Task 3
**Why:** The job and routes carry the owner's skip and replace rules; pin them before the code.

### Task 12: Implement the align job, the timing writer and the routes
- [ ] status
**Objective:** POST `/api/tracks/align`, PUT `/api/tracks/{track_rowid}/lyrics/lrc` and `timing_lines` on the synced route work, and Task 11's tests pass.
**Write-scope:** `muse/library/jobs.py`
`muse/library/lyrics_edit.py`
`muse/jobs.py`
`muse/api.py`
**Read-context:** `tests/test_align_job.py`, `tests/test_api_align.py` (from Task 11), `muse/jobs.py:300-350` (`run_smart_install` shape), `muse/api.py:528-534,741,877-896,1589-1656`, `muse/library/jobs.py:53-90`
**Anchors:** `def update(` @ `muse/library/jobs.py`; `def is_stale(conn: sqlite3.Connection, track_rowid: int, *, lyrics_root: Path) -> bool:` @ `muse/library/lyrics_edit.py`; `def track_paths(conn: sqlite3.Connection, track_rowid: int) -> dict | None:` @ `muse/library/queries.py`; `def track_lyrics_synced(track_rowid: int) -> dict:` @ `muse/api.py`; `def fill_starts(raw: list[float | None]) -> list[int] | None` @ `muse/align_text.py` (from Task 3)
**Steps:**
1. `job_rows.update`: add keyword `payload: dict | None = None`, written as JSON with the other fields (same finished-job guard).
2. `write_timings`: `build_lrc` to `lyrics_files(...)[1]` via `.tmp` + `os.replace`; `lyrics_state.upsert(state=state, synced=True, checked_at=now or time, lrc_offset_ms=0)`.
3. `muse/jobs.py`: `ALIGN = "align"`; `run_align_job` sets `running`, per item `await asyncio.to_thread(align, item["file_path"], item["lines"])`, `WorkerError`/`WorkerReplyError` or `fill_starts(...) is None` -> failed, else `write_timings(..., state="aligned", track=item)`; after each item `update(progress=done/total, payload=...)` and `bus.publish(_event(...))`; messages exactly as Task 11; `CancelledError` -> `cancelled` "Server stopped" and re-raise; ends `done`.
4. `muse/api.py`: models `AlignRequest(track_rowids: list[int] = Field(min_length=1, max_length=5000), replace: bool = False)` and `TappedTimings(starts_ms: list[int] = Field(max_length=5000))`; `align_tasks` set beside `install_tasks`, exposed as `app.state.align_tasks`, cancelled at shutdown; reap `ALIGN` at startup. POST `/api/tracks/align` (202): 503 unless `config.embed_batch or smart.installed(config.models_dir)`; 409 when `job_rows.active(conn, ALIGN)`; per id: `track_paths` None or file missing on disk -> reason `"missing_file"`; no `lyric_lines` -> `"no_lyrics"`; `.lrc` present and not `is_stale` and not `replace` -> `"synced"`; names from `queries.track`. No items -> `{"job_id": None, "skipped": skipped}`; else create the job, `app.state.worker.begin()`, task with `align = lambda path, lines: app.state.worker.request("align", path=path, lines=lines)`, return `{"job_id": id, "skipped": skipped}`. PUT `/lyrics/lrc`: 404 unknown track or no lines; 400 messages as Task 11; `write_timings(state="manual")`; `{"ok": True}`. Synced route: add `"timing_lines": lyric_lines(plain)`.
**New symbols:** `ALIGN = "align"`; `async def run_align_job(conn, bus: JobBus, job_id: int, items: list[dict], align, *, lyrics_root: Path) -> dict`; `def write_timings(conn: sqlite3.Connection, track_rowid: int, lines: list[str], starts_ms: list[int], *, lyrics_root: Path, state: str, track: dict, now: int | None = None) -> None`; `class AlignRequest(BaseModel)`; `class TappedTimings(BaseModel)`; routes `POST /api/tracks/align`, `PUT /api/tracks/{track_rowid}/lyrics/lrc`
**Verify:** `uv run pytest tests/test_align_job.py tests/test_api_align.py tests/test_api_lyrics_synced.py tests/test_lyrics_edit.py -p no:cacheprovider` — expected: all pass.
**Forbidden:** don't edit tests (report FIXTURE-DEFECT); don't take `chain_lock`; no torch imports in the sidecar.
**Depends:** Task 3, Task 11
**Why:** The sidecar owns every write to the data dir; the worker only measures.

## Phase 3: UI, slow tests, frozen worker, docs, CI

### Task 13: Auto-align entry points and dialog [design]
- [ ] status
**Objective:** "Auto-align lyrics" in the track menu and "Auto-align" in the selection bar start an align job, with the replace confirm and error dialog.
**Write-scope:** `web/src/api.ts`
`web/src/jobs/JobsContext.tsx`
`web/src/components/JobProgress.tsx`
`web/src/components/TrackMenu.tsx`
`web/src/components/SelectionBar.tsx`
`web/src/components/TrackActions.tsx`
`web/src/components/AlignDialog.tsx` (new)
`web/src/views/Settings.tsx`
`web/src/styles/actions.css`
**Read-context:** `web/src/api.ts:128-137,308-345`, `web/src/components/TrackActions.tsx:1-40`, `web/src/components/RemoveDialog.tsx:30-50`, `web/src/components/Dialog.tsx:4-30`, `web/src/components/JobProgress.tsx:30-45`, `web/src/views/Settings.tsx:280-290`, `DESIGN.md:240-262`
**Anchors:** `export function useTrackActions(onChanged: () => void): {` @ `web/src/components/TrackActions.tsx`
`export type TrackAction = "next" | "queue" | "playlist" | "details" | "lyrics" | "remove"` @ `web/src/components/TrackMenu.tsx`
`async function send<T>(method: string, path: string, body?: unknown): Promise<T> {` @ `web/src/api.ts`
`export type SyncedLyrics = {` @ `web/src/api.ts`
**Steps:**
1. `api.ts`: `AlignSkip = { track_rowid: number; reason: "no_lyrics" | "synced" | "missing_file" }`; `alignTracks(trackRowids, replace = false)` POST `/tracks/align` -> `{ job_id: number | null; skipped: AlignSkip[] }`; `saveTappedTimings(trackRowid, startsMs)` PUT `/tracks/${id}/lyrics/lrc`; `SyncedLyrics` gains `timing_lines: string[]`. Callers tell 503 and 409 apart with the existing `statusOf(cause)` (`web/src/api.ts:106`).
2. `JobsContext` `payload` type gains `track_rowids?: number[]; failed?: number[]; aligned?: number[]`. `JobProgress`: `align` -> "Aligning lyrics".
3. `TrackAction` adds `"align"`; `ITEMS` gets `{ action: "align", label: "Auto-align lyrics" }` after "Edit lyrics", shown only when `track.has_lyrics`; `SelectionBar` gets `{ action: "align", label: "Auto-align" }` before "Remove".
4. `useTrackActions.run("align", tracks)`: opens `AlignDialog` with the tracks. `AlignDialog` posts on mount; single track skipped as `synced` -> confirm view (copy per Design: auto-align), Replace re-posts with `replace: true`; 503, 409, or every track `no_lyrics` -> error view with Close; otherwise closes itself (the job strip reports).
5. `Settings.tsx` licence line adds the aligners: "...plus CLAP, MiniLM, an English emotion model, Laya, and lyric aligners (MMS-FA, free for non-commercial use only, and schufo)."
6. `npm --prefix web ci` if `web/node_modules` is missing.
**New symbols:** `export type AlignSkip`; `export function alignTracks(trackRowids: number[], replace?: boolean): Promise<{ job_id: number | null; skipped: AlignSkip[] }>`; `export function saveTappedTimings(trackRowid: number, startsMs: number[]): Promise<{ ok: boolean }>`; `export function AlignDialog({ tracks, onClose }: { tracks: Track[]; onClose: () => void })`
**Verify:** `npm --prefix web run build` — expected: exit 0. Then `node ~/.claude/skills/design-brain/scripts/preflight.mjs web/src/components/AlignDialog.tsx web/src/components/TrackMenu.tsx web/src/components/SelectionBar.tsx` — expected: no errors.
**Forbidden:** no new deps or tokens; no em dash in copy; don't touch `SyncedLyrics.tsx` (Task 14).
**Depends:** Task 12
**Design:** auto-align
**Why:** Gives Auto-align its menu and bulk paths with the owner's per-track replace rule.

### Task 14: Lyrics panel Auto-align states and Tap along mode [design]
- [ ] status
**Objective:** The lyrics panel offers Auto-align, Re-align and Tap along, shows aligning and failure notes, and runs the tap-along mode that saves an `.lrc`.
**Write-scope:** `web/src/views/SyncedLyrics.tsx`
`web/src/views/TapAlong.tsx` (new)
`web/src/styles/np.css`
**Read-context:** `web/src/views/SyncedLyrics.tsx:78-185,480-589`, `web/src/styles/np.css:60-125`, `web/src/player/NowPlayingSheet.tsx:60-70,410-422`, `web/src/components/SmartBanner.tsx:26-57`, `DESIGN.md:270-300`
**Anchors:** `export function LyricsPanel() {` @ `web/src/views/SyncedLyrics.tsx`; `export function useSmart(): SmartView {` @ `web/src/components/SmartBanner.tsx`; `export const useJobs = () => useContext(JobsContext);` @ `web/src/jobs/JobsContext.tsx`; `export function startTap(total: number): TapState` @ `web/src/player/tapAlong.ts` (from Task 6); `export function saveTappedTimings(trackRowid: number, startsMs: number[]): Promise<{ ok: boolean }>` @ `web/src/api.ts` (from Task 13); `export function AlignDialog({ tracks, onClose }: { tracks: Track[]; onClose: () => void })` @ `web/src/components/AlignDialog.tsx` (from Task 13)
**Steps:**
1. `LyricsBody`: `smartOn = useSmart().status?.state === "installed"`. Links: unsynced -> "Auto-align" (smartOn) and "Tap along"; stale -> existing two plus "Re-align" (smartOn) and "Tap along"; synced -> "Tap along". Auto-align and Re-align open `AlignDialog` for this track.
2. Aligning: `useJobs().job` of kind `align`, state `queued`/`running`, whose `payload.track_rowids` includes this track -> the note "Aligning to the song…" replaces the links. When that job reaches `done`: refetch; if `payload.failed` includes the track, show "Couldn't align, tap along instead" with the Tap along link until the next action.
3. `TapAlong` (props: `lines`, `trackRowid`, `positionMs`, `playing`, `seek`, `toggle`, `onCancel`, `onSaved`; `LyricsBody` passes `usePlayer()`'s `toggle`): on mount `seek(0)`, then `toggle()` when not `playing`; renders per Design: tap-along (eyebrow, counter, stamped/cue/upcoming lines with stamps `m:ss.s`, Tap pill, hint, Undo, Start over, Cancel; "Save timings" when done). Keys per Constraints. Cue line `scrollIntoView({ block: "center" })`, instant under reduced motion. Save -> `saveTappedTimings(trackRowid, state.stamps)` then `onSaved` (refetch); failure -> `.error` line.
4. Tap along uses `data.timing_lines`; it is hidden when that list is empty.
**New symbols:** `export function TapAlong(props: { lines: string[]; trackRowid: number; positionMs: number; playing: boolean; seek: (ms: number) => void; toggle: () => void; onCancel: () => void; onSaved: () => void })`
**Verify:** `npm --prefix web run build` — expected: exit 0. `npm --prefix web test` — expected: all pass. `node ~/.claude/skills/design-brain/scripts/preflight.mjs web/src/views/TapAlong.tsx web/src/views/SyncedLyrics.tsx web/src/styles/np.css` — expected: no errors.
**Forbidden:** no new deps or tokens; don't change the offset nudge or tap-to-seek; don't edit `PlayerContext.tsx` (`usePlayer()` exposes `toggle`, `playing`, `seek`: `web/src/player/PlayerContext.tsx:72-75`).
**Depends:** Task 6, Task 13
**Design:** tap-along
**Why:** The panel is where people notice bad timings, so the fix lives there.

### Task 15: Slow tests for the real aligners [test]
- [ ] status
**Objective:** `-m slow` runs schufo, MMS-FA and the worker `align` op on real weights and checks shapes and order.
**Write-scope:** `tests/test_align_slow.py` (new)
**Read-context:** `tests/test_worker_models_slow.py:1-40`, `muse/smart.py:84-115`, `docs/plans/2026-10-29-desktop-m4b-preflight.md` (from Task 1)
**Anchors:** `class SchufoAligner:` @ `muse/schufo.py` (from Task 7); `class MmsAligner:` @ `muse/mms_align.py` (from Task 8); `def real_handlers(weights: Path) -> dict[str, Callable[[dict], object]]:` @ `muse/worker_main.py`
**Steps:**
1. `pytestmark = pytest.mark.slow`. Module fixture downloads the manifest's `weights/schufo/` entries with `smart.download(f, Path.home() / ".cache" / "muse-test-weights")` (skipped when present).
2. schufo on `tests/fixtures/smoke/tone.wav` with `["hello there", "[Chorus]", "how are you"]`: three entries, index 1 None, others floats `>= 0` and non-decreasing.
3. `G2p` with the real npz: `phonemes("zzyzxian")` non-empty and all in `PHONEME2IDX`.
4. `MmsAligner()` (HF cache) with `["사랑해", "愛してる", "baby"]`, lang3 `"kor"`: three floats, non-decreasing.
5. `real_handlers(Path.home() / ".cache" / "muse-test-weights")["align"]({"path": str(tone), "lines": ["I love you so much", "every single day"]})`: `aligner == "schufo"`, `lang == "en"`, two starts.
**New symbols:** none
**Verify:** `uv run --extra embed pytest -m slow tests/test_align_slow.py -p no:cacheprovider` — expected: 4 passed.
**Forbidden:** no source edits; no accuracy thresholds (a tone has no singing).
**Depends:** Task 7, Task 8, Task 9, Task 10
**Why:** Characterizes the real models end to end; accuracy is owner check 3.

### Task 16: Freeze the worker locally and smoke align
- [ ] status
**Objective:** The frozen Linux worker answers `hello`, `minilm` and `align` from a weights dir.
**Write-scope:** `scripts/freeze_worker.py`
**Read-context:** `scripts/freeze_worker.py`, `scripts/worker_smoke.py`, `docs/plans/2026-10-29-desktop-m4b-preflight.md` (from Task 1)
**Anchors:** `def main() -> int:` @ `scripts/freeze_worker.py`
**Steps:**
1. `df -BG --output=avail /home/i4nle3`; under 10G stop and report.
2. `uv run --extra embed --group package python scripts/freeze_worker.py`.
3. `uv run --extra embed python scripts/fetch_weights.py --keys minilm,schufo,mms --out build/ci-weights`.
4. Smoke (Verify). If `align` fails on missing package data, add the matching `--collect-data`/`--hidden-import` flag in `freeze_worker.py`, refreeze, rerun; at most three rounds, then park with the error.
5. Delete `build/worker-dist/`, `build/pyinstaller-worker/`, `build/ci-weights/`, `build/muse-worker-*.zip`.
**New symbols:** none
**Verify:** `uv run python scripts/worker_smoke.py --worker build/worker-dist/muse-worker/muse-worker --weights build/ci-weights --ops hello,minilm,align --fixture tests/fixtures/smoke/tone.wav` — expected: `ok hello`, `ok minilm`, `ok align`, exit 0.
**Forbidden:** no source edits outside `scripts/freeze_worker.py`; no onefile; don't run a web build at the same time.
**Depends:** Task 9, Task 10
**Why:** PyInstaller misses package data silently; the frozen exe is what users run.

### Task 17: Document aligners, tap-along and licences [docs]
- [ ] status
**Objective:** README, CONTEXT.md, CLAUDE.md and DESIGN.md describe Auto-align, tap-along, the align op and job, and every new licence.
**Write-scope:** `README.md`
`CONTEXT.md`
`CLAUDE.md`
`DESIGN.md`
**Read-context:** `README.md:76-180`, `CONTEXT.md:28-60`, `CLAUDE.md` (Jobs and Architecture), `DESIGN.md:336-400`, this plan's Assumptions
**Anchors:** `def run_align_job` @ `muse/jobs.py` (from Task 12); `export function TapAlong` @ `web/src/views/TapAlong.tsx` (from Task 14)
**Steps:**
1. README Smart features: Auto-align (English to schufo, others to MMS-FA, runs only when asked, writes an `.lrc` in the data dir), Tap along. Licences: replace the two "arrives with smart features (M4)" lines with MMS-FA `MahmoudAshraf/mms-300m-1130-forced-aligner` (CC-BY-NC-4.0, from Meta's MMS), schufo lyrics-aligner (MIT, cite Schulze-Forster et al. 2021, TASLP 29:2382-2395), trained on MUSDB18 and its lyrics extension (both licensed non-commercial, Zenodo 1117372 and 3989267), CMU Pronouncing Dictionary (BSD-style), g2p_en GRU weights (Apache-2.0), uroman with its required acknowledgement sentence ("This project uses the universal romanizer software 'uroman' written by Ulf Hermjakob, USC Information Sciences Institute (2015-2020)"), py3langid (BSD-3).
2. CONTEXT.md: worker ops list gains `align`; new entries **Auto-align**, **Aligner** (schufo, MMS-FA), **Tap along**, each with an _Avoid_ line (Avoid: transcribe, sync for Auto-align; Avoid: karaoke for Tap along).
3. CLAUDE.md: Jobs list gains `align`; one sentence naming `muse/align_text.py`, `muse/ctc_align.py`, `muse/g2p.py` (pure) and `muse/schufo.py`, `muse/mms_align.py` (worker only).
4. DESIGN.md §9 components: Auto-align states and Tap along (pill, cue dot, stamps, keys).
**New symbols:** none
**Verify:** `grep -c "uroman\|MUSDB18\|Tap along\|Auto-align" README.md` — expected: 4 or more.
**Forbidden:** no claims not in this plan or the code; no em dashes in new UI copy quoted.
**Depends:** Task 12, Task 14, Task 16
**Why:** CLAUDE.md asks for CONTEXT.md updates with new terms, and the licences carry attribution duties.

### Task 18: CI tag run for the worker and app [mechanical]
- [ ] status
**Objective:** The packaging workflow runs green on all legs with the align smoke, and the worker zips are available as artifacts.
**Write-scope:** `docs/plans/2026-10-29-desktop-m4b-aligners.md`
**Read-context:** `.github/workflows/package.yml:1-70`
**Anchors:** none
**Steps:**
1. Stop and ask the owner before pushing a tag: this run spends billed macOS minutes. Proceed only on an explicit yes in this session.
2. `git push origin worktree-m4b-plan`.
3. `git tag ci-package-m4b-1`, then `git push origin ci-package-m4b-1`.
4. `gh run list --workflow package.yml --limit 1`, then `gh run watch <id>` (one command each).
5. Record under this task: run URL, each leg's result, artifact names and zip sizes.
**New symbols:** none
**Verify:** `gh run view <id> --json conclusion --jq .conclusion` — expected: `success`.
**Forbidden:** no workflow edits here (a failure is a report, not a fix); no force-push; no release creation.
**Depends:** Task 10, Task 16
**Why:** Replaces M4a Task 17: the release worker must contain the aligners.
