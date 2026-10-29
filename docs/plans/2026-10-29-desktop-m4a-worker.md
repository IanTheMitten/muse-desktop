# Plan - Muse desktop M4a: ML worker, smart-features installer, Laya tagging, retire universal_reco, M3 fixes, new brand mark

Source spec: `docs/specs/2026-10-29-desktop-app-fork.md` (Status: ready for planning; `spec-lint --handoff` exit 0, 1 warn no-source-prd). Earlier plans: `docs/plans/2026-10-29-desktop-m1-core.md` (milestone list at lines 9-23), `docs/plans/2026-10-29-desktop-m2-editing.md`, `docs/plans/2026-10-29-desktop-m3-shell.md` (13/13 done; PR #1 open and mergeable, CI green on macos-15, macos-15-intel, windows-latest).

Repo: `~/Projects/muse-desktop`. Base: branch `worktree-m3-plan` at d218eea (M3 is not merged to main yet; this plan's branch `worktree-m4-plan` starts there). Python commands run from the repo root, npm through `npm --prefix web`. Release rule from M1 holds: `docs/plans/` and `docs/specs/` never ship.

## Scope

M1 milestone list, M4 line (`docs/plans/2026-10-29-desktop-m1-core.md:22`): ML worker, smart-features installer, Laya in-worker replacing `muse/systemone.py` and the hosted Jev path in `muse/lyric_tag_batch.py`, retiring `universal_reco/`, aligners. Owner split it (2026-10-29): **M4a (this plan)** = worker, installer, banner and Settings section, every model leg plus Laya through the worker, retire `universal_reco/` and `systemone.py`, worker packaging and CI. **M4b (next plan)** = schufo + MMS-FA aligners, language routing, Auto-align UI. Spec decisions covered: 4, 5, the worker half of 2 and 3, and the Testing strategy lines for the worker protocol and the installer.

Also in this plan, from the owner this round: the app does not scroll (Task 1), and a new brand mark: an asterisk and the colours of https://chillmuse.github.io/muse-player/ (Task 12).

**Owner answers (2026-10-29, this planning round):**
- Hosting: make `muse-desktop` public before release; worker archives on its GitHub Releases; the Laya finetune on the owner's Hugging Face repo; third-party weights from their original Hugging Face repos, pinned by commit and sha256. No rehosting of other people's weights.
- Worker build: develop and test on local Linux; the macOS and Windows worker freeze runs in CI only at the final phase (Task 17).
- Laya checkpoint: ship v4 (`~/models/laya-muse-v4`); a later checkpoint is a manifest change.
- Split M4 into M4a and M4b; plan M4a now.

**Not in this plan:** aligners and Auto-align (M4b); the downloader (M5); signing and notarization (spec Deferred); a native Now Playing bridge (only if owner check 6 of M3 fails on a Mac); smart features on Intel Macs (torch 2.15.0 ships no macOS x86_64 wheel, see Premises; the app reports "unsupported" there); `muse/scanner.py:126` treating a missing folder named like `Album.mp3` as one song (M3 review note 8, negligible); the Windows `fcntl` no-op locks (recorded `shortcut:`s, only one writer exists); CI caching for the Rust build.

## M3 review (2026-10-29, read-only pass over `main..worktree-m3-plan`)

Backend 1143 passed, web 27 passed, build green. No blocking bug. Folded into this plan:

| M3 finding | Where fixed |
|---|---|
| App does not scroll: `web/src/main.tsx:10` imports `MiniWindow` statically, so `web/src/styles/mini.css:2` (`html, body, #root { height: 100%; overflow: hidden; }`) is in the global bundle (`web/dist/assets/index-*.css` contains `html,body,#root{height:100%;overflow:hidden}`) | Task 1 |
| Dropping a parent of a listed folder 400s the whole drop (`muse/api.py:820-821` then `_check_folders`); `music_files` under a new folder are never pruned | Task 1 |
| Navigation guard allows `127.0.0.1` on any port (`src-tauri/src/main.rs:52-54`) | Task 2 |
| Sidecar exit after ready is ignored (`main.rs:151-159`); a late `MUSE_LISTENING` after the 90 s failure still navigates (`main.rs:172-179`) | Task 2 |
| No NSIS uninstall hook; worker exe not killed (`src-tauri/windows/hooks.nsh:2-5`) | Task 2 |
| Windows data dir is Roaming AppData (`main.rs:67`); GBs of models must not roam | Task 2 |
| Sidecar freeze would bundle torch if it is in the venv (`scripts/freeze_sidecar.py:20-32`) | Task 2 |
| `shell_smoke.py` leftover-process check covers only `muse-core` | Task 2 |
| Real encoders shell out to `ffprobe`/`ffmpeg` on PATH (`muse/embed.py:239-271`), absent in a frozen app | Task 4 (PyAV decode) |
| The shell never kills children; the worker must exit on its own stdin EOF; Windows needs `CREATE_NO_WINDOW`; worker freeze must be onedir | Tasks 5, 7, 14 |

## Assumptions (decided at planning)

- **Two halves, one repo.** Worker side: `muse/worker_main.py` (JSON-line server), `muse/mert.py` (MERT, moved from `universal_reco/content_embed/model.py`), `muse/audio_decode.py` (PyAV) and the existing real encoders in `muse/embed.py`. Sidecar side: `muse/worker.py` (client and adapters, no torch), `muse/smart.py` (installer, no torch). The sidecar never imports torch, transformers, sentence_transformers, laya or av; `muse/api.py` stops importing the real encoder classes.
- **Worker protocol (one version, `PROTOCOL = 1`).** One request per stdin line: `{"id": <int>, "op": <str>, ...args}`. One reply per line on stdout, in order: `{"id": <int>, "ok": <result>}` or `{"id": <int|null>, "error": "<message>"}`. Ops: `hello` (answered by the server itself: `{"protocol": 1, "ops": [sorted op names]}`), `clap_audio {path}` -> 512 floats, `clap_text {text}` -> 512 floats, `minilm {text}` -> 384 floats, `audio_va {path}` -> `[valence, arousal]`, `lyrics_va {text}` -> `[valence, arousal]`, `mert {path}` -> 768 floats, `laya {state, questions}` -> laya's `system_one` dict. Models load lazily on first use and stay loaded. stdout carries only protocol lines (fd 1 is pointed at stderr before any model import, as `universal_reco/content_embed/batch.py:128` does). stdin EOF ends the process.
- **Weights layout.** `<data dir>/models/weights/<key>/...` mirrors each Hugging Face repo's file paths. Keys: `mert`, `clap`, `minilm`, `audio_va`, `lyrics_va`, `laya`. A real encoder gets `source=<that dir>` and calls `from_pretrained(str(source))`; with `source=None` it keeps today's model-id load (dev and slow tests use the local HF cache). `model_name` values do not change, so stored embedding rows stay comparable.
- **Pinned files** (repo @ revision: files):
  - `mert` = `m-a-p/MERT-v1-95M` @ `12af15fef9d0ac838c3f475bfbbf26d2060dd4f5`: `config.json`, `configuration_MERT.py`, `modeling_MERT.py`, `preprocessor_config.json`, `pytorch_model.bin`
  - `clap` = `laion/larger_clap_music_and_speech` @ `195c3a3e68faebb3e2088b9a79e79b43ddbda76b`: `config.json`, `merges.txt`, `preprocessor_config.json`, `pytorch_model.bin`, `special_tokens_map.json`, `tokenizer.json`, `tokenizer_config.json`, `vocab.json`
  - `minilm` = `sentence-transformers/all-MiniLM-L6-v2` @ `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`: `1_Pooling/config.json`, `config.json`, `config_sentence_transformers.json`, `model.safetensors`, `modules.json`, `sentence_bert_config.json`, `special_tokens_map.json`, `tokenizer.json`, `tokenizer_config.json`, `vocab.txt`
  - `audio_va` = `audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim` @ `6eba34a2485ea31cb03600241787c3a5edab8626`: `LICENSE`, `config.json`, `model.safetensors`, `preprocessor_config.json`, `vocab.json`
  - `lyrics_va` = `j-hartmann/emotion-english-distilroberta-base` @ `0e1cd914e3d46199ed785853e12b57304e04178b`: `config.json`, `merges.txt`, `pytorch_model.bin`, `special_tokens_map.json`, `tokenizer.json`, `tokenizer_config.json`, `vocab.json`
  - `laya` = local `~/models/laya-muse-v4`, served from `https://huggingface.co/IanTheMitten/laya-muse-v4/resolve/main/<path>`: `encoder/config.json`, `model.safetensors`, `rl_agent_config.json`, `tokenizer/tokenizer.json`, `tokenizer/tokenizer_config.json`. Recorded model name `laya-muse-v4`.
  - URLs for the other five: `https://huggingface.co/<repo>/resolve/<revision>/<path>`.
- **Manifest** `muse/smart_manifest.json`: `{"version": 1, "weights": [{"path": "weights/<key>/<file>", "url": ..., "sha256": ..., "size": ...}], "workers": {"<triple>": {"path": "muse-worker-<triple>.zip", "url": "https://github.com/IanTheMitten/muse-desktop/releases/download/worker-v1/muse-worker-<triple>.zip", "sha256": ..., "size": ...}}}`. Committed with `"workers": {}`; the owner adds each worker after publishing release `worker-v1` (`scripts/manifest_add_worker.py`). Triples: `aarch64-apple-darwin`, `x86_64-pc-windows-msvc`. Linux has no worker entry (dev only), so Linux reports "unsupported" unless `MUSE_EMBED_BATCH=1` with `MUSE_WORKER_CMD`.
- **Installer.** Plain HTTPS with `requests` (already a core dependency). Each file downloads to `<models>/<path>.part`, resumes with `Range: bytes=<n>-` (a 200 reply restarts from zero), streams through sha256, renames on a match, deletes `.part` and raises on a mismatch. A file already at its final path is skipped (its hash was checked when it landed). Before downloading: needed = sum of sizes not yet complete + 3x the worker archive size (unzip room); `shutil.disk_usage(models).free` below that raises "Not enough disk space: needs N.N GB". The worker zip extracts to `<models>/worker/` keeping each entry's Unix mode bits, then the zip is deleted. `<models>/installed.json` (`{"manifest_version": 1, "triple": "..."}`) is written last; `installed()` is true only when it matches the shipped manifest version. Job kind `smart_install`.
- **Worker command.** `MUSE_WORKER_CMD` (shlex-split) when set; else `[<models>/worker/muse-worker/muse-worker(.exe), "--weights", <models>/weights]`. The client starts it lazily on the first request with env `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `PYTHONIOENCODING=utf-8`, and `OMP_NUM_THREADS`/`MKL_NUM_THREADS`/`OPENBLAS_NUM_THREADS` = `max(1, os.cpu_count() // 2)` (overrides the shell's `OPENBLAS_NUM_THREADS=1`); on Windows `creationflags=subprocess.CREATE_NO_WINDOW`. stderr is inherited (lands in `muse.log`). It stops after 120 s idle, on `stop()`, and at app shutdown (stdin closed, then kill after 10 s).
- **Crash rule (spec Error handling).** A dead or silent worker raises `WorkerError` for that request; the next request starts it again, but at most 2 starts per `begin()` session. Each chain run and each vibe search calls `begin()`. So a crashing worker fails the job once and is relaunched once, never looped over the library.
- **When ML runs.** `ml_on = config.embed_batch or smart.installed(config.models_dir)`, read at each chain run. The chain becomes: scan, lyrics, art, then when `ml_on`: embed backfill (CLAP, MiniLM, VA through the worker), MERT content batch, Laya lyric tags, then the stale taste retrain. The standalone startup embed task and `config.content_embed` go away. A finished install spawns the chain.
- **API.** `GET /api/smart` -> `{"state": "installed"|"not_installed"|"unsupported", "download_bytes": <int>, "job": <job row or null>}` (job = the active or most recent `smart_install` job). `POST /api/smart/install` -> 202 `{"job_id": <int>}`; 409 when one is running or state is `installed`/`unsupported`. `/api/health` `content.expected_on_this_host` is replaced by `content.smart_installed`. Vibe search without the worker and without an injected `app.state.text_encoder` answers 503 "Vibe search needs smart features".
- **Decode.** PyAV 19.0.2 (BSD; wheels bundle FFmpeg's LGPL libraries) replaces ffprobe/ffmpeg/torchaudio/torchcodec. Damaged packets are skipped (`av.error.InvalidDataError` per packet), the way the old ffmpeg fallback concealed them.
- **Dependencies.** `embed` extra becomes `torch==2.15.0`, `transformers==5.22.0`, `sentence-transformers==6.2.0`, `numpy`, `av==19.0.2`, `laya==0.3.20` (the version v4 was trained with: `~/tmp-kaggle-laya/run.py:29`). Linux pulls torch from the explicit `pytorch-cpu` index (`https://download.pytorch.org/whl/cpu`) so no CUDA wheels land on this disk; macOS and Windows use PyPI (CPU builds).
- **Worker freeze.** PyInstaller 6.22.3 `--onedir`, name `muse-worker`, entry `scripts/worker_entry.py`, flags from the Task 3 results file. Zip layout: top folder `muse-worker/` holding the exe and `_internal/`. Never onefile (unpacks GBs per start and orphans its child).
- **Windows data dir** moves to `app_local_data_dir()` (`%LOCALAPPDATA%`). No migration: the alpha has not shipped. macOS and Linux paths are unchanged (both getters return the same dir there).
- **Navigation guard.** `tauri` scheme, host `tauri.localhost`, or `http://127.0.0.1:<the sidecar's port>` only, and loopback only while the sidecar is up. The capability's `http://127.0.0.1:*` stays (static config); the guard keeps windows off other ports.
- **Brand.** The mark is Phosphor's asterisk path (MIT, the same glyph the website's `.logo` uses). Website palette sampled from `https://chillmuse.github.io/muse-player/assets/index-6IOdIu7d.css`. The brand colours apply to brand surfaces only (icon, tray glyph, favicon, nav wordmark, boot page); the in-app theme stays record-drenched (DESIGN.md §1) because the owner asked for the logo, and the accent rule is the app's core.
- **Deferred spec items decided here:** worker hosting (GitHub Releases for the worker, Hugging Face for weights); Laya weights licence: `convaiinnovations/laya` is Apache-2.0 (HF API `cardData.license`), so a derived checkpoint may be redistributed. Licence notes for the README: MERT CC-BY-NC-4.0, audeering CC-BY-NC-SA-4.0, CLAP Apache-2.0, MiniLM Apache-2.0, j-hartmann emotion model: no licence on its model card, Laya Apache-2.0.

## Owner prerequisite before `/autopilot` starts

Free at least 15 GB on `/`. Today `df -h /home/i4nle3` shows 4.0 GB free (99% used). Reclaimable, the owner's call: `uv cache prune` (unused entries of 9.9 GB `~/.cache/uv`), `pip cache purge` (4.2 GB `~/.cache/pip`), old Laya checkpoints `~/models/laya-muse-v1..v3` (1.29 GB each; keep v4). Task 3 stops at step 1 when less than 15 GB is free.

## Premises

- Spec passes handoff lint. Verified: `node ~/.claude/skills/spec-brainstorming/scripts/spec-lint.mjs --handoff docs/specs/2026-10-29-desktop-app-fork.md` (exit 0).
- Baseline on d218eea: 1143 passed, 1 skipped, 3 deselected; `tests/test_api_catalog_browse.py::test_retry_creates_a_new_job_and_leaves_the_failure_readable` failed once in a full run and passed alone (timing flake). Verified: `uv run pytest -p no:cacheprovider`. Web: 27 vitest tests, build green (M3 review run).
- Free disk is 4.0 GB of 227 GB. Verified: `df -h /home/i4nle3`.
- torch 2.15.0 cp312 wheels: PyPI `macosx_14_0_arm64`, `win_amd64`, `manylinux_2_28_x86_64` (CUDA), no macOS x86_64; CPU index has `torch-2.15.0+cpu-cp312-cp312-manylinux_2_28_x86_64.whl` and `win_amd64`. Verified: PyPI JSON API and `https://download.pytorch.org/whl/cpu/torch/`.
- av 19.0.2 has cp312-abi3 wheels for macOS arm64 and x86_64, win_amd64, manylinux x86_64; laya 0.4.17 is latest on PyPI, 0.3.20 is what v4 trained on; transformers 5.22.0, sentence-transformers 6.2.0, pyinstaller 6.22.3. Verified: PyPI JSON API, `~/tmp-kaggle-laya/run.py:29`.
- laya 0.3.20 exposes `def load(model_id_or_path: str = "convaiinnovations/laya", device: Optional[str] = None, ...)` and `Agent.system_one(self, state, questions, lang=None, ...)`; it ran on transformers 5.17.0 and torch 2.14.0 in `~/laya`. Verified: `~/laya/lib/python3.14/site-packages/laya/agent.py:845,912`, `ls ~/laya/lib/python3.14/site-packages`.
- `~/models/laya-muse-v4` holds `model.safetensors` (1287653720 bytes), `rl_agent_config.json`, `tokenizer/tokenizer.json`, `tokenizer/tokenizer_config.json`, `encoder/config.json` (plus eval reports). v4 scores 0.751 on detail labels vs Jev 0.82 (`docs/notes/2026-10-22-laya-lyric-finetune-results.md` in `~/Projects/muse_intelligence` at 6bbd1c4). Verified: `find ~/models/laya-muse-v4 -type f`.
- The five pinned repos' file lists, sizes and LFS sha256s at the pinned revisions are as listed in Assumptions. Verified: `https://huggingface.co/api/models/<repo>/tree/<rev>?recursive=1`. Weights total about 3.56 GB.
- `~/.cache/huggingface/hub` already caches MERT, CLAP, MiniLM, both VA models and `convaiinnovations/laya` (5.6 GB). Verified: `ls ~/.cache/huggingface/hub`.
- `muse/embed.py` real encoders call `from_pretrained(self.model_name)` (`muse/embed.py:290-292`, `:349`, `:496`, `:550-552`); the audio-VA head reads `hf_hub_download(model_name, "model.safetensors")` (`muse/embed.py:395`); decode is ffprobe/ffmpeg subprocesses (`muse/embed.py:239-271`).
- MERT today: `universal_reco/content_embed/model.py` (torchaudio first, ffmpeg fallback, 5 s chunks at 24 kHz, mean over 13 hidden layers); the sidecar drives it through `SubprocessContentEmbedder` (`muse/content.py:193`) with `uv run --project universal_reco` (`muse/content.py:188-190`).
- `create_app` builds encoders through factories (`muse/api.py:631-643`), runs the embed task only when `config.embed_batch` (`muse/api.py:646`), MERT after downloads only when `config.content_embed` (`muse/api.py:704`), and vibe search constructs `ClapAudioEncoder()` in-process (`muse/api.py:1029-1040`).
- The launch chain is `run_chain` at `muse/api.py:554-593`; `/api/health` is `muse/api.py:844-882`; the import route is `muse/api.py:800-837`.
- Tests pinning what this plan changes: `tests/test_config.py:44-61` (laya/curator fields), `tests/test_health_content.py:74,96,104,113` (`expected_on_this_host`), `tests/test_embed_startup_wiring.py:22-62` (`embed_batch`), `tests/test_api_vibe.py:60-79` (503 via `ClapAudioEncoder`), `tests/conftest.py:133-140` (`make_config`), `tests/test_systemone.py`, `tests/test_content_subprocess.py`, `tests/test_content_slow.py`, `tests/test_lyric_tag_batch.py:14` (`SystemOneError`). Verified: `grep -rn "universal_reco\|SubprocessContentEmbedder\|systemone\|laya_url\|curator_\|embed_batch\|expected_on_this_host" tests/`.
- Shell facts: data dir `app.path().app_data_dir()` (`src-tauri/src/main.rs:67`); `allowed_origin(url: &tauri::Url) -> bool` used at `main.rs:89` and `src-tauri/src/tray.rs:77`; `Terminated` handled only before a port (`main.rs:151-159`); `shell_smoke.py` checks only `muse-core` (`scripts/shell_smoke.py:21-25`); `package.yml` is one `package` job (`.github/workflows/package.yml:9-43`).
- Website brand: favicon is a `#28172f` rounded tile with a pink `#f5b9dc` glyph; CSS `:root` has `--surface:#24142c`, `--muted:#c5b3c8`, `--accent:#f5b9dc`, text `#f8eaf4`; warm `#ffdb8c`; `.logo span{color:var(--accent)}` holds the Phosphor asterisk path `M214.86,180.12a8,8,0,0,1-11,2.74L136,142.13V216a8,8,0,0,1-16,0V142.13L52.12,182.86a8,8,0,1,1-8.23-13.72L112.45,128,43.89,86.86a8,8,0,1,1,8.23-13.72L120,113.87V40a8,8,0,0,1,16,0v73.87l67.88-40.73a8,8,0,1,1,8.23,13.72L143.55,128l68.56,41.14A8,8,0,0,1,214.86,180.12Z` (viewBox `0 0 256 256`). Verified: `curl` of the page, `favicon.svg` and `assets/index-6IOdIu7d.css`.
- Icons regenerate with `bash scripts/tauri.sh icon src-tauri/icons/icon.svg -o src-tauri/icons` (then delete `android/` and `ios/`) and `bash scripts/tauri.sh icon src-tauri/icons/tray-template.svg -o src-tauri/icons/tray -p 44`; the CLI needs `npm --prefix web ci` first. Verified: `docs/plans/2026-10-29-desktop-m3-shell.md:312`, memory `m3-shell-build-gotchas`.

## Memory carried into briefs

- **Worktree sessions refuse compound shell commands** (`a && b`, `a; b`, pipes with computed values, heredocs, `cd x && ...`): every Verify step is one plain command, run in order. Use `npm --prefix web ...` and the Write tool for files.
- **Fresh worktree needs `npm --prefix web ci`** before any web build, test, or `scripts/tauri.sh icon`.
- **`python` is not on PATH:** always `uv run python`. Torch-needing commands use `uv run --extra embed ...`.
- **Smoke after startup/API changes:** `bash scripts/smoke.sh` must print `SMOKE-OK`.
- **TestClient hangs on `/api/jobs/stream`:** never open the SSE route in a test.
- **Repo-local git identity is set:** do not change git config. **`git commit -- <path>` fails on staged deletions:** `git add` then plain `git commit`.
- **Exit 139 at pytest teardown** is a known flake: rerun once. `test_retry_creates_a_new_job_and_leaves_the_failure_readable` can flake once: rerun once.
- **Never `pkill -f`/`pgrep -f` with a pattern in the same command line:** use `pgrep -x muse-core` / `pgrep -x muse-worker`.
- **No `cargo` without `scripts/tauri.sh`** (shared target dir `~/.cache/muse-tauri-target`). **Freeze after the web build finishes**, never in parallel (hashed asset race).
- **Disk is tight:** delete `build/preflight/` and `build/worker-dist/` after recording results; never download weights outside the HF cache or a test's tmp dir.
- **UI built in M2 and M3 was never exercised in a browser:** owner checks cover it.

## Owner checks after the run (cannot be automated here)

1. The app scrolls again on every view; the mini window still has no scrollbars.
2. Upload the five Laya files from `~/models/laya-muse-v4` (paths as in Assumptions) to Hugging Face model repo `IanTheMitten/laya-muse-v4` (a different repo name means re-running `uv run python scripts/build_manifest.py --laya-repo <name>`).
3. Make the GitHub repo public; create release `worker-v1` from the Task 17 CI artifacts `muse-worker-aarch64-apple-darwin.zip` and `muse-worker-x86_64-pc-windows-msvc.zip`; run `uv run python scripts/manifest_add_worker.py <triple> <zip> <url>` for each; commit `muse/smart_manifest.json`; rebuild the app.
4. On a Mac and a Windows PC: the banner shows at launch; Not now hides it until the next launch; Install downloads, shows progress, survives a network drop (resumes), and Radio and Muse then use the new legs; vibe search answers.
5. The new icon in the Dock/taskbar, the tray glyph, the wordmark in the wide nav rail, the favicon in a browser tab, and the boot page.
6. M3's six owner checks (`docs/plans/2026-10-29-desktop-m3-shell.md` "Owner checks") are still open.

## Design: brand-mark

READ: Brand-mark refresh for an existing desktop music player, matching the owner's website (plum tile, pink-to-warm asterisk), leaning toward flat SVG on the existing muse-player token system.
DIALS: VARIANCE 3 / MOTION 1 / DENSITY 2
PALETTE: Brand surfaces only (in-app theme unchanged): tile #28172f, boot background #24142c (dark) / #f4f3f6 (light, existing), mark gradient #f5b9dc to #ffdb8c, glow #7a4b8f, text #f8eaf4, muted #c5b3c8; light-mode wordmark mark #a8437a (6.1:1 on #f4f3f6).
TYPE: display Figtree Variable (the app's own UI face; wordmark "muse" lowercase, weight 750, letter-spacing -0.04em) / body Figtree Variable / boot page system-ui (no web fonts allowed there)
SIGNATURE: Phosphor's six-arm asterisk filled with a linear gradient from #f5b9dc (top left) to #ffdb8c (bottom right) on a #28172f rounded tile with one soft #7a4b8f radial glow behind it, the website's starburst in small.
ANCHORS: chillmuse.github.io/muse-player (the "muse" wordmark followed by the asterisk in pink, the #28172f favicon tile), Arc browser (one flat glyph on a saturated rounded tile), Plexamp (dark tile with one warm accent).
VARIETY: system: muse-player (same product as the 2026-10-28 ledger entry; coherence, no rotation). Changes only the brand fingerprint: icon motif bars to asterisk, icon field charcoal wash to plum.
CONTENT: Wordmark text "muse". Boot copy unchanged ("Starting Muse", "Muse couldn't start"). Glyph path: the Phosphor asterisk path in Premises, viewBox 0 0 256 256 (spans x 40..216, y 32..224 roughly).
FORMAT: production
TARGET: src-tauri/icons/icon.svg, src-tauri/icons/tray-template.svg, src-tauri/boot/index.html, web/public/favicon.svg, web/index.html, web/src/components/Icons.tsx, web/src/components/NavBar.tsx, web/src/styles/shell.css
CONSTRAINTS: App icon keeps the macOS grid (824 tile at 100,100, rx 185 on a 1024 canvas); asterisk about 520 px across, centred: transform="translate(512 512) scale(2.95) translate(-128 -128)". Tray glyph: the asterisk only, fill #000, 22x22 viewBox, transform="translate(11 11) scale(0.07) translate(-128 -128)" (template image). Favicon: 64x64 viewBox, tile rx 14 #28172f, asterisk with the same gradient, transform="translate(32 32) scale(0.2) translate(-128 -128)". No text in any icon, no filters, no external refs. Nav wordmark: <span class="nav-brand">muse<BrandMark/></span> with the mark 0.8em, aria-hidden, colour #f5b9dc in dark and #a8437a in light (two new tokens --brand-mark in tokens.css are not allowed: define them in shell.css under .nav-brand with the existing prefers-color-scheme / data-theme selectors). Boot page: dark background becomes #24142c and raised #2f1c38; a 48 px asterisk (same gradient) above the heading; no animation. index.html theme-color dark becomes #24142c and gets <link rel="icon" href="/favicon.svg" type="image/svg+xml">. WCAG AA on all text. No em dash.

## Design: smart-features

READ: In-app opt-in for non-technical listeners to download optional ML models, PRODUCT.md plain voice, built from the existing Settings rows and the glass job strip.
DIALS: VARIANCE 2 / MOTION 2 / DENSITY 4
PALETTE: Existing tokens only: --glass, --hairline, --text, --text-2, --accent / --on-accent (Install is .btn.primary), --ink-raised for the banner card in light mode via --glass.
TYPE: display Newsreader Variable (section h2 as the other Settings sections) / body Figtree Variable
SIGNATURE: A single-line glass banner at the top of the main column that becomes its own progress bar while installing: the accent fill grows behind the text, so the offer and its progress are one object.
ANCHORS: macOS System Settings software update row (size, then one button, then progress in the same row), Plexamp's "Sonic analysis" setting (plain explanation of what the analysis buys you), Apple Music's library-update banner (dismissible, never modal).
VARIETY: system: muse-player (coherence). New component; reuses .section, .section-head, .folder-row, .btn, .job-strip-track styles.
CONTENT: Banner (state not_installed): "Smart features aren't installed. Muse can listen to your songs and read their lyrics to choose better next songs. <size> download." Buttons "Install" (primary) and "Not now". Installing: "Installing smart features · <done> of <total>" plus percent. Failed: "Smart features didn't install. <job message>" with "Try again". Settings section h2 "Smart features"; help: "Muse listens to each song and reads its lyrics on this computer, so Radio and Muse pick better next songs. Nothing leaves your computer." Status rows: "Not installed · <size> download", "Installed", "Installing · <done> of <total>", "Not available on this computer. Smart features need a Mac with Apple silicon or Windows." Licence note: "Uses MERT and an audio emotion model that are free for non-commercial use only, plus CLAP, MiniLM, an English emotion model and Laya." Sizes from the API's download_bytes, formatted "3.6 GB" (one decimal, GB = 1e9). Job progress text from job.progress and download_bytes.
FORMAT: production
TARGET: web/src/components/SmartBanner.tsx (new), web/src/styles/smart.css (new), web/src/App.tsx, web/src/views/Settings.tsx, web/src/api.ts, web/src/components/JobProgress.tsx
CONSTRAINTS: Banner never modal, never blocks playback; rendered inside <main> above <Routes>; hidden when state is installed or unsupported, on /settings (the section is there), and for the rest of this page session after "Not now" (module-level flag, not storage: "until the next launch", spec Decision 4). role="status" for progress, buttons 44 px min height, focus visible, aria-live polite. Progress bar animates width only through transform: scaleX on an inner element; reduced-motion: no transition. No em dash. Phone width 320 px: banner text wraps, buttons wrap below.

## Phase 1 - M3 fixes and the worker preflight

### Task 1: App scrolls again; dropping a parent folder works
- [x] status
**Objective:** The main app scrolls (the mini window's overflow rule no longer leaks into the global CSS), and dropping a folder that contains listed folders or files replaces them instead of failing.
**Write-scope:**
`web/src/styles/mini.css`
`web/src/views/MiniWindow.tsx`
`muse/api.py`
`tests/test_api_import.py`
**Read-context:** `web/src/styles/mini.css:1-3`, `web/src/views/MiniWindow.tsx:1-40`, `web/src/main.tsx:1-16`, `muse/api.py:772-837`, `tests/test_api_import.py:1-60`
**Anchors:**
`export function MiniWindow() {` @ `web/src/views/MiniWindow.tsx`
`async def import_paths(body: ImportPaths) -> dict:` @ `muse/api.py`
`def _check_folders(folders: list[Path]) -> None:` @ `muse/api.py`
`async def put_music_folders(body: MusicFoldersWrite) -> dict:` @ `muse/api.py`
**Steps:**
1. `mini.css` line 2 becomes `html.mini-window, html.mini-window body, html.mini-window #root { height: 100%; overflow: hidden; }` with the comment `/* Scoped: Vite bundles this file into the main app's CSS too (MiniWindow is imported statically). */`.
2. `MiniWindow`: first effect `document.documentElement.classList.add("mini-window")`, cleanup removes it.
3. `import_paths`: when a dropped directory `p` is not inside a listed folder, remove every listed folder that is inside `p` (`f.is_relative_to(p)`), then append `p`; count it in `folders_added`. After the loop, drop every `music_files` entry that is inside any folder in the final list. Save when folders or files changed (`folders_added or songs_added` or a pruned file).
4. `put_music_folders`: after resolving folders, drop `music_files` entries inside any of them before `settings.save`.
5. Tests in `test_api_import.py`: `test_parent_folder_replaces_listed_child` (child listed, drop parent: folders == [parent], `folders_added` 1); `test_parent_drop_with_song_in_same_gesture` (drop parent + an outside song: 200, both added); `test_new_folder_prunes_dropped_songs_inside_it` (song in `music_files`, drop its folder: song gone from `music_files`); `test_put_folders_prunes_music_files`.
**New symbols:** none
**Verify:** `uv run pytest -p no:cacheprovider tests/test_api_import.py` then `npm --prefix web ci` then `npm --prefix web run build` then `grep -rc "html,body,#root{height:100%;overflow:hidden}" web/dist/assets` then `grep -rl "html.mini-window" web/dist/assets`. Expected: all import tests pass; build green; every count line ends `:0`; one css file listed.
**Forbidden:** no change to `web/src/main.tsx` (static import stays); no other CSS edits; data-dir containment still 400s.
**Depends:** none
**Why:** M3 review: the mini window's rule killed page scroll everywhere (owner report), and a parent-folder drop lost the whole gesture.

### Task 2: Shell hardening: port-pinned guard, sidecar death, local data dir, uninstall hook, torch-free sidecar
- [x] status
**Objective:** The shell only navigates to its own sidecar port, returns to the failed boot page when the sidecar dies after start, ignores a late port after failure, stores data in local app data, kills `muse-core` and `muse-worker` on install and uninstall, and the sidecar freeze excludes ML packages.
**Write-scope:**
`src-tauri/src/main.rs`
`src-tauri/src/tray.rs`
`src-tauri/windows/hooks.nsh`
`scripts/freeze_sidecar.py`
`scripts/shell_smoke.py`
**Read-context:** `src-tauri/src/main.rs:52-195`, `src-tauri/src/tray.rs:56-81`, `src-tauri/windows/hooks.nsh:1-5`, `scripts/freeze_sidecar.py:20-33`, `scripts/shell_smoke.py:21-26`, this plan's Assumptions (Navigation guard, Windows data dir)
**Anchors:**
`pub(crate) fn allowed_origin(url: &tauri::Url) -> bool {` @ `src-tauri/src/main.rs`
`pub fn open_mini(app: AppHandle) -> Result<(), String> {` @ `src-tauri/src/tray.rs`
`def sidecar_running() -> bool:` @ `scripts/shell_smoke.py`
`def main() -> int:` @ `scripts/freeze_sidecar.py`
**Steps:**
1. `main.rs`: `allowed_origin(url: &tauri::Url, port: Option<u16>) -> bool` = scheme `tauri`, or host `tauri.localhost`, or (scheme `http`, host `127.0.0.1`, `port.is_some()` and `url.port() == port`). Add `pub(crate) fn current_port(app: &tauri::AppHandle) -> Option<u16>`: `try_state::<Mutex<Shell>>()`, None when absent or `failed`, else `port`. `main`'s `on_navigation`: clone the app handle into the closure and pass `current_port(&h)`. Same in `tray.rs` `open_mini` (`crate::allowed_origin(url, crate::current_port(&h))`).
2. Data dir fallback: `app.path().app_local_data_dir()?`.
3. `CommandEvent::Terminated` when `port.is_some()`: set `failed = true`, `port = None`, push "Muse stopped (exit code <code>)", close the `mini` window if it exists, and navigate `main` to `"http://tauri.localhost/index.html"` on Windows (`cfg!(windows)`) else `"tauri://localhost/index.html"`. The boot page then polls `boot_status` and shows the failure.
4. `MUSE_LISTENING` while `failed` is true: log the line, do not set `port`, do not navigate.
5. `hooks.nsh`: preinstall also runs `nsExec::Exec 'taskkill /F /T /IM muse-worker.exe'` + `Pop $0`; add `!macro NSIS_HOOK_PREUNINSTALL` with both taskkills (each followed by `Pop $0`) and comment "Same lock on uninstall: a running exe would be left behind."
6. `freeze_sidecar.py`: add `"--exclude-module"` for each of `torch`, `transformers`, `sentence_transformers`, `laya`, `av` to the PyInstaller args.
7. `shell_smoke.py` `sidecar_running()`: true when either `muse-core` or `muse-worker` runs (`tasklist` image names `muse-core.exe`/`muse-worker.exe`; `pgrep -x` each).
**New symbols:** `pub(crate) fn current_port(app: &tauri::AppHandle) -> Option<u16>`; `allowed_origin` gains `port: Option<u16>`
**Verify:** `npm --prefix web ci` then `uv run python scripts/shell_smoke.py --build --expect mp3,flac,wav`. Expected: last line `SHELL-SMOKE-OK`.
**Forbidden:** never `kill()` the sidecar; don't change the token flow, `boot_status` shape, or the capability file; no new crates; no `cargo` without `scripts/tauri.sh`.
**Depends:** none
**Why:** M3 review findings 1-6. The worker exe name is fixed now (`muse-worker`) so the installer hooks are right before the worker exists.

### Task 3: Worker dependencies and the model/freeze preflight [experiment]
- [!] status
**Stuck-report:**
cause-class: env-blocker
attempts: 0 agent runs | owner: needs-env
blocking: 4, 5, 14, 15, 16, 17
detail: Step 1 disk check: 4G free on /home/i4nle3, plan requires 15G (Owner prerequisite). Not dispatched. Free disk (uv cache prune, pip cache purge, old ~/models/laya-muse-v1..v3), then re-run /autopilot.
**Objective:** The `embed` extra resolves with CPU torch and every worker model loads and answers once on this machine, from local dirs, and a PyInstaller onedir build of those imports runs; results recorded.
**Write-scope:**
`pyproject.toml`
`uv.lock`
`scripts/worker_preflight.py` (new)
`scripts/preflight_freeze_entry.py` (new)
`docs/plans/2026-10-29-desktop-m4a-preflight.md` (new)
**Read-context:** `pyproject.toml:1-60`, this plan's Assumptions (Pinned files, Dependencies, Decode, Worker freeze), `universal_reco/content_embed/model.py:28-38`, `muse/embed.py:274-300`, `muse/embed.py:360-460`
**Anchors:**
`def embed(path: str) -> np.ndarray:` @ `universal_reco/content_embed/model.py`
`def _build_audio_va_model(model_name: str):` @ `muse/embed.py`
**Steps:**
1. `df -BG --output=avail /home/i4nle3`; under 15G: write the results file with that number and "Stopped: free disk first (plan Owner prerequisite)", mark the task parked, stop.
2. `pyproject.toml`: `embed` extra = `torch==2.15.0`, `transformers==5.22.0`, `sentence-transformers==6.2.0`, `numpy`, `av==19.0.2`, `laya==0.3.20`; add `[[tool.uv.index]]` `name = "pytorch-cpu"`, `url = "https://download.pytorch.org/whl/cpu"`, `explicit = true`, and `[tool.uv.sources]` `torch = [{ index = "pytorch-cpu", marker = "sys_platform == 'linux'" }]`. Update the extra's comment (worker only; the sidecar never imports it). `uv lock`.
3. `worker_preflight.py` (run with `uv run --extra embed python scripts/worker_preflight.py`): for each pinned repo, `huggingface_hub.snapshot_download(repo, local_files_only=True)` (fallback: model id, noted); load from that local dir exactly as production will (CLAP `ClapModel`/`ClapProcessor`, MiniLM `SentenceTransformer`, audio VA via `_build_audio_va_model`'s approach on the local `model.safetensors`, `pipeline("text-classification", model=<dir>, top_k=None)`, MERT `AutoModel.from_pretrained(<dir>, trust_remote_code=True)` + `Wav2Vec2FeatureExtractor`), `laya.load(str(Path.home() / "models/laya-muse-v4"), device="cpu")` and one `system_one` choice question; decode every `tests/fixtures/smoke/tone.*` with `av` to mono float32 at 16000/24000/48000 Hz. Print a table: model, load seconds, one-inference seconds, output shape; decode per format: samples, seconds.
4. `preflight_freeze_entry.py`: imports torch, transformers, sentence_transformers, av, laya; loads MERT from the local snapshot with `trust_remote_code=True`, one forward on 1 s of zeros at 24 kHz; `laya.load(...)` one `system_one`; prints `FREEZE-OK`. Freeze: `uv run --extra embed --group package pyinstaller scripts/preflight_freeze_entry.py --onedir --noconfirm --distpath build/preflight/dist --workpath build/preflight/work --specpath build/preflight`, run the exe. Iterate on `--collect-submodules`/`--collect-data`/`--copy-metadata`/`--hidden-import` flags until it prints `FREEZE-OK` or 5 attempts are spent.
5. Results file: versions resolved (`uv tree --extra embed --depth 1`), the table, the final flag list verbatim, `du -sh build/preflight/dist`, run time, every error met and its fix, and a verdict line. Then delete `build/preflight/`.
**New symbols:** none
**Verify:** `uv run python -c "import pathlib; t = pathlib.Path('docs/plans/2026-10-29-desktop-m4a-preflight.md').read_text(); print('verdict' in t.lower(), 'flags' in t.lower())"`. Expected: the measurement table and flag list are recorded; prints `True True`.
Negative result is a valid outcome.
**Forbidden:** no muse/ source edits; never download weights except into the HF cache; don't commit `build/`.
**Depends:** none
**Why:** Step 5 of planning: six model artifacts, a pinned laya on a newer transformers, PyAV and a torch freeze all sit under Tasks 4-14. One measured run de-risks them before any code depends on them.

## Phase 2 - Worker core

### Task 4: PyAV decode, local-dir encoders and MERT in muse
- [ ] status
**Objective:** Real encoders load from a `source` dir and decode with PyAV; MERT lives in `muse/mert.py`; none of it imports torch or av at module import.
**Write-scope:**
`muse/audio_decode.py` (new)
`muse/embed.py`
`muse/mert.py` (new)
`tests/test_audio_decode.py` (new)
`tests/test_worker_models_slow.py` (new)
**Read-context:** `muse/embed.py:230-590`, `universal_reco/content_embed/model.py:1-140`, `docs/plans/2026-10-29-desktop-m4a-preflight.md` (from Task 3), `tests/fixtures/smoke/`
**Anchors:**
`def _probe_duration_seconds(audio_path: Path) -> float:` @ `muse/embed.py`
`def _build_audio_va_model(model_name: str):` @ `muse/embed.py`
`class ClapAudioEncoder:` @ `muse/embed.py`
`def embed(path: str) -> np.ndarray:` @ `universal_reco/content_embed/model.py`
**Steps:**
1. `audio_decode.py`: `class AudioDecodeError(RuntimeError)`; `def decode_mono(path: Path | str, sample_rate: int, start: float = 0.0, duration: float | None = None) -> "np.ndarray"` (lazy `import av, numpy`; open, seek to `start` when > 0, demux the first audio stream, `packet.decode()` inside `try/except av.error.InvalidDataError: continue`, resample with `av.AudioResampler(format="flt", layout="mono", rate=sample_rate)`, flush the resampler, concatenate, trim to `duration`; zero samples raises `AudioDecodeError(f"No audio decoded from {path}")`); `def duration_seconds(path: Path | str) -> float` (container duration, else stream duration x time_base, else decoded length).
2. `embed.py`: `_probe_duration_seconds` returns `audio_decode.duration_seconds(audio_path)`; `_decode_window` returns `audio_decode.decode_mono(audio_path, sample_rate, max(0.0, start_seconds), duration_seconds)`. `ClapAudioEncoder`, `MiniLmLyricsEncoder`, `MerAudioVAEncoder`, `TextVAEncoder` take `source: Path | None = None` and load from `str(source) if source else self.model_name`; `_build_audio_va_model(model_name: str, source: Path | None = None)` reads `source / "model.safetensors"` when given, else `hf_hub_download(...)`. Update the module docstring's ffmpeg mentions.
3. `mert.py`: `MERT_MODEL = "m-a-p/MERT-v1-95M"`, `MERT_SAMPLE_RATE = 24000`, `class MertEncoder` with `model_name = MERT_MODEL`, `__init__(self, source: Path | None = None)` (lazy torch/transformers; `AutoModel.from_pretrained(src, trust_remote_code=True)`, `Wav2Vec2FeatureExtractor.from_pretrained(src, trust_remote_code=True)`), `encode(self, path: Path | str) -> list[float]`: `decode_mono(path, 24000)`, then the 5 s chunk / 13-layer mean exactly as `universal_reco/content_embed/model.py:93-140`; empty raises `ValueError`.
4. `test_audio_decode.py` (`pytest.importorskip("av")`): each of the 6 fixtures decodes to > 0 float32 samples at 16000; a 1.0 s window from 0.5 s is 16000 samples +-1%; `duration_seconds` within 0.1 s of `mutagen.File(path).info.length`; 1 KB of random bytes raises `AudioDecodeError`; importing `muse.audio_decode` and `muse.mert` leaves `torch` and `av` out of `sys.modules` (run in a subprocess).
5. `test_worker_models_slow.py` (`pytestmark = pytest.mark.slow`): each real encoder with `source=None` on `tests/fixtures/smoke/tone.wav`: CLAP 512 floats L2 ~1, `encode_text` 512, MiniLM 384, audio VA and lyrics VA two floats in [-1, 1], MERT 768.
**New symbols:** `AudioDecodeError`, `decode_mono(...)`, `duration_seconds(...)`, `MertEncoder`, `MERT_SAMPLE_RATE`; `source` params as above
**Verify:** `uv run --extra embed pytest -p no:cacheprovider tests/test_audio_decode.py` then `uv run --extra embed pytest -p no:cacheprovider -m slow tests/test_worker_models_slow.py` then `uv run pytest -p no:cacheprovider`. Expected: all pass; default suite still 1143+ passed with the decode tests skipped or passing.
**Forbidden:** no torch or av import at module level anywhere in `muse/`; don't change `model_name` constants or vector math; don't touch `universal_reco/` (Task 11 deletes it).
**Depends:** Task 3
**Why:** The frozen worker has no ffmpeg on PATH (M3 carry-in) and must load weights from the installer's dirs, not the HF hub.

### Task 5: Worker JSON-line server
- [ ] status
**Objective:** `python -m muse.worker_main --weights DIR` serves the protocol in Assumptions, loading each model lazily from `DIR/<key>`.
**Write-scope:**
`muse/worker_main.py` (new)
`scripts/worker_entry.py` (new)
`tests/test_worker_main.py` (new)
`tests/test_worker_main_slow.py` (new)
**Read-context:** `universal_reco/content_embed/batch.py:110-200`, `muse/lyric_tag_batch.py:222-230`, this plan's Assumptions (Worker protocol, Weights layout), `muse/mert.py` (from Task 4)
**Anchors:**
`def _seize_stdout_for_the_protocol() -> TextIO:` @ `universal_reco/content_embed/batch.py`
`class MertEncoder` @ `muse/mert.py` (from Task 4)
`class ClapAudioEncoder:` @ `muse/embed.py`
**Steps:**
1. `PROTOCOL = 1`. `def serve(stdin: TextIO, stdout: TextIO, handlers: dict[str, Callable[[dict], object]]) -> None`: skip blank lines; bad JSON or non-object -> `{"id": null, "error": "bad request: ..."}`; non-int `id` -> id null error; `op == "hello"` -> `{"protocol": 1, "ops": sorted(handlers)}`; unknown op -> `{"id": id, "error": "unknown op <op>"}`; handler exception -> `{"id": id, "error": "<Type>: <msg>"}`; else `{"id": id, "ok": result}`. `json.dumps(..., ensure_ascii=False)`, write, flush, per line. Returns at EOF.
2. `def real_handlers(weights: Path) -> dict[str, Callable[[dict], object]]`: lazy per key (construct on first call, cache): `src(key) = weights / key if (weights / key).is_dir() else None`; `clap_audio`/`clap_text` share one `ClapAudioEncoder(source=src("clap"))`; `minilm` -> `MiniLmLyricsEncoder`; `audio_va` -> `MerAudioVAEncoder` (return a list); `lyrics_va` -> `TextVAEncoder`; `mert` -> `MertEncoder`; `laya` -> `laya.load(str(weights / "laya"), device="cpu").system_one(args["state"], args["questions"])`, raising `RuntimeError("Laya weights missing")` when `weights/laya` is absent. First construction calls `torch.set_num_threads(max(1, (os.cpu_count() or 2) // 2))`.
3. `def main(argv: list[str] | None = None) -> int`: `--weights` (required, Path); seize stdout first (port the universal_reco function and its docstring essentials), log to stderr, `serve(sys.stdin, proto, real_handlers(args.weights))`, return 0. `if __name__ == "__main__": sys.exit(main())`.
4. `scripts/worker_entry.py`: `from muse.worker_main import main` / `raise SystemExit(main())`.
5. `test_worker_main.py` (default run, fake handlers, `io.StringIO`): ok reply; hello lists ops; unknown op; handler raising; bad JSON; JSON array; missing id; blank lines produce nothing; replies in input order; `real_handlers(tmp_path)` returns exactly `{"clap_audio","clap_text","minilm","audio_va","lyrics_va","mert","laya"}` and (in a subprocess) leaves `torch` out of `sys.modules`; `laya` handler with no `weights/laya` returns an error reply mentioning "Laya weights missing" (call through `serve`, construction raises before torch import).
6. `test_worker_main_slow.py` (`slow`): subprocess `uv`-free: `[sys.executable, "-m", "muse.worker_main", "--weights", tmp]` with `tmp/laya` a symlink to `~/models/laya-muse-v4` (skip if missing); send hello, `mert` and `clap_audio` on `tests/fixtures/smoke/tone.wav`, `laya` with one choice question; assert shapes; close stdin; process exits 0 within 20 s.
**New symbols:** `PROTOCOL`, `serve(...)`, `real_handlers(...)`, `main(...)` in `muse/worker_main.py`
**Verify:** `uv run pytest -p no:cacheprovider tests/test_worker_main.py` then `uv run --extra embed pytest -p no:cacheprovider -m slow tests/test_worker_main_slow.py`. Expected: all pass.
**Forbidden:** nothing but protocol lines on the real stdout; no module-level torch/laya imports; no network access in the default tests.
**Depends:** Task 4
**Why:** Decision 4: one worker hosts every model behind the JSON-line protocol `muse/content.py` already used, grown from one model to all.

### Task 6: Worker client tests [test]
- [x] status
**Objective:** `tests/test_worker_client.py` pins the client and adapter behaviour against a fake worker script, red until Task 7.
**Write-scope:**
`tests/test_worker_client.py` (new)
**Read-context:** `tests/test_content_subprocess.py:1-60`, this plan's Assumptions (Worker protocol, Worker command, Crash rule)
**Anchors:**
`class SubprocessContentEmbedder:` @ `muse/content.py`
**Steps:**
1. A fake worker written to `tmp_path/fake_worker.py` (stdlib only): appends one line to `tmp_path/starts` on start; serves the protocol; ops: `echo` -> args minus id/op; `pid` -> `os.getpid()`; `fail` -> error reply "boom"; `die` -> `os._exit(3)`; `hang` -> `time.sleep(60)`; `clap_audio`/`clap_text` -> `[0.1]*512`; `minilm` -> `[0.1]*384`; `audio_va`/`lyrics_va` -> `[0.5, -0.5]`; `mert` -> error reply "bad file" when `path` ends in `bad.wav`, else `[0.2]*768`; `laya` -> `{g: {"probabilities": {"a": 0.7, "b": 0.3}} for g in questions}`; also writes the env value of `OMP_NUM_THREADS` and `HF_HUB_OFFLINE` for op `env`.
2. Cases (client = `WorkerClient([sys.executable, str(script)], idle_seconds=..., timeout_seconds=...)`): `request("echo", x=1) == {"x": 1}`; `request("fail")` raises `WorkerReplyError` matching "boom" and a following `request("pid")` gives the same pid; `request("die")` raises `WorkerError`, next `request("pid")` works with a new pid; after `die` twice in one session the third request raises `WorkerError` and `starts` has 2 lines, then `client.begin()` and `request("pid")` works (3 lines); `hang` with `timeout_seconds=1` raises `WorkerError` within 5 s and the process is gone; `idle_seconds=0.5`: after a request and 1.5 s `client.running is False`; `stop()` -> process exits within 5 s; `env` shows `HF_HUB_OFFLINE == "1"` and `OMP_NUM_THREADS == str(max(1, (os.cpu_count() or 2) // 2))`; lazy start: constructing the client writes no `starts` line.
3. Adapters: `WorkerAudioEncoder(client).encode(Path("x.wav"))` length 512 and `.encode_text("hi")` 512; `WorkerLyricsEncoder` 384; `WorkerAudioVAEncoder(...).encode(...) == (0.5, -0.5)` (a tuple); `WorkerLyricsVAEncoder` same; `list(WorkerContentEmbedder(client).embed_many([(1, "a.wav"), (2, "bad.wav"), (3, "c.wav")]))` gives `(1, [0.2]*768, None)`, `(2, None, "bad file")`, `(3, [0.2]*768, None)`; `worker_ask(client)({"text": "x"}, {"g": ("instr", {"a": "A", "b": "B"})}) == {"g": {"a": 0.7, "b": 0.3}}`; model names: `WorkerAudioEncoder.model_name == embed.CLAP_MODEL`, `WorkerContentEmbedder.model_name == content.MERT_MODEL`, `LAYA_MODEL == "laya-muse-v4"`.
4. `worker_command(tmp_path / "models")` ends with `["--weights", str(tmp_path / "models" / "weights")]` and its first item ends with `muse-worker/muse-worker` (plus `.exe` on Windows); `worker_command(tmp_path, "uv run x --y")` == `["uv", "run", "x", "--y"]`.
5. Signatures under test (from Task 7, copy verbatim): `class WorkerError(RuntimeError)`, `class WorkerReplyError(Exception)`, `WorkerClient(command: list[str], *, idle_seconds: float = 120.0, timeout_seconds: float = 600.0)`, `.request(op: str, **args) -> object`, `.begin() -> None`, `.stop() -> None`, `.running -> bool` (property), `WorkerAudioEncoder(client)`, `WorkerLyricsEncoder(client)`, `WorkerAudioVAEncoder(client)`, `WorkerLyricsVAEncoder(client)`, `WorkerContentEmbedder(client)`, `worker_ask(client)`, `LAYA_MODEL`, `worker_command(models_dir: Path, override: str | None = None) -> list[str]`.
**New symbols:** none
**Verify:** `uv run pytest -p no:cacheprovider tests/test_worker_client.py`. Expected: collection fails with `ModuleNotFoundError: No module named 'muse.worker'` (red).
**Forbidden:** no edits outside the test file; no sleeps over 2 s except the documented waits; never touch the real network.
**Depends:** none
**Why:** Spec Testing strategy: "the worker protocol against a fake worker process". Test-first so Task 7 is judged against fixed behaviour.

### Task 7: Worker client and adapters
- [ ] status
**Objective:** `muse/worker.py` passes `tests/test_worker_client.py`.
**Write-scope:**
`muse/worker.py` (new)
**Read-context:** `tests/test_worker_client.py` (from Task 6), `muse/content.py:112-125`, `muse/content.py:193-300`, `muse/embed.py:84-160`, `muse/lyric_tag_batch.py:222-230`, this plan's Assumptions (Worker command, Crash rule)
**Anchors:**
`class ContentEmbedder(Protocol):` @ `muse/content.py`
`class AudioEncoder(Protocol):` @ `muse/embed.py`
`def backend_ask(backend):` @ `muse/lyric_tag_batch.py`
**Steps:**
1. Implement every signature listed in Task 6 step 5. `WorkerClient`: one `threading.Lock` around a request; lazy `subprocess.Popen(command, stdin=PIPE, stdout=PIPE, stderr=None, text=True, encoding="utf-8", bufsize=1, env=..., creationflags=CREATE_NO_WINDOW on Windows else 0)`; a daemon reader thread puts stdout lines on a `queue.Queue` and `None` at EOF; request writes `{"id": n, "op": op, **args}`, waits `queue.get(timeout=timeout_seconds)`, skips replies with another id; `None`, `BrokenPipeError`, `OSError` or `queue.Empty` -> kill the process, raise `WorkerError`; `"error"` -> `WorkerReplyError(message)`; `"ok"` -> value. Start count per session: refuse a third start with `WorkerError("Smart features stopped working; they will retry next time")`. Idle: `threading.Timer(idle_seconds, self.stop)` re-armed after every request. `stop()`: close stdin, wait 10 s, then kill.
2. Adapters call ops per Assumptions; VA adapters return `tuple(result)`; `WorkerContentEmbedder.embed_many` yields `(rowid, None, str(e))` on `WorkerReplyError` and lets `WorkerError` propagate. `worker_ask(client)` builds questions exactly as `backend_ask` (`{"type": "choice", "instructions": ..., "criteria": labels}`) and returns `{g: answers[g]["probabilities"]}`.
3. `worker_command`: override via `shlex.split`; else exe `models_dir / "worker" / "muse-worker" / ("muse-worker.exe" if os.name == "nt" else "muse-worker")`.
**New symbols:** as Task 6 step 5
**Verify:** `uv run pytest -p no:cacheprovider tests/test_worker_client.py` then `uv run pytest -p no:cacheprovider`. Expected: all pass; full suite green.
**Forbidden:** no torch/laya/av import; don't edit the tests (committed test wins); no new dependency.
**Depends:** Task 6
**Why:** The sidecar's only door to the models; the crash rule and idle stop are spec Data flow ("started lazily, stopped when idle and at quit").

## Phase 3 - Installer and wiring

### Task 8: Smart-features installer tests [test]
- [x] status
**Objective:** `tests/test_smart_install.py` pins download, resume, hash, disk and install-marker behaviour, red until Task 9.
**Write-scope:**
`tests/test_smart_install.py` (new)
**Read-context:** this plan's Assumptions (Manifest, Installer)
**Anchors:**
`def make_config(tmp_path, **overrides) -> Config:` @ `tests/conftest.py`
**Steps:**
1. Fakes: `FakeResponse(status_code, body: bytes)` with `iter_content(chunk_size)`, `raise_for_status()` (raises on >= 400), context-manager methods; `FakeSession(files: dict[url, bytes], honor_range=True)` whose `get(url, headers=None, stream=True, timeout=60)` records headers and returns 206 with the tail when `Range: bytes=n-` is honoured, else 200 with all bytes. A helper builds a manifest dict with two weight files and one worker zip (made with `zipfile`, holding `muse-worker/muse-worker` with mode 0o755 and `muse-worker/_internal/lib.txt`), real sha256s and sizes.
2. Cases: fresh `download(...)` writes the file, no `.part` left; resume: a `.part` with the first half -> request header `Range: bytes=<half>-`, final bytes equal; server ignores Range (200) -> file still correct; wrong sha256 -> `HashMismatch`, neither file nor `.part` remains; a final file already present -> no `get` call; `install(...)` with `disk_free=lambda p: SimpleNamespace(free=10)` raises `NotEnoughSpace` whose message matches `r"Not enough disk space: needs \d+\.\d GB"` and downloads nothing; full `install(...)`: weights at `models/weights/...`, `models/worker/muse-worker/muse-worker` executable (`os.access(..., os.X_OK)`, POSIX only), zip deleted, `installed.json` == `{"manifest_version": 1, "triple": "aarch64-apple-darwin"}`, `installed(models, manifest) is True`, and False after bumping the manifest version; `on_progress(done, total)` last call has `done == total`; `files_for(manifest, "x86_64-apple-darwin")` raises `Unsupported`; `status(models, manifest, None)["state"] == "unsupported"`, `status(...)["download_bytes"]` == sum of sizes; `host_triple()` returns one of the known triples or None; `load_manifest()` of the shipped file has `version`, `weights` (each with `path` starting `weights/`, 64-hex `sha256`, int `size`) and a `workers` dict.
3. Signatures under test (from Task 9, copy verbatim): `MANIFEST_PATH`, `SMART_INSTALL = "smart_install"`, `class Unsupported(Exception)`, `class HashMismatch(Exception)`, `class NotEnoughSpace(Exception)`, `@dataclass(frozen=True) class ManifestFile: path: str; url: str; sha256: str; size: int`, `host_triple() -> str | None`, `load_manifest(path: Path = MANIFEST_PATH) -> dict`, `files_for(manifest: dict, triple: str | None) -> list[ManifestFile]`, `installed(models_dir: Path, manifest: dict | None = None) -> bool`, `status(models_dir: Path, manifest: dict, triple: str | None) -> dict`, `download(file: ManifestFile, models_dir: Path, *, session=None, on_bytes=None) -> Path`, `install(models_dir: Path, manifest: dict, triple: str, *, session=None, on_progress=None, disk_free=shutil.disk_usage) -> None`.
**New symbols:** none
**Verify:** `uv run pytest -p no:cacheprovider tests/test_smart_install.py`. Expected: collection fails with `ModuleNotFoundError: No module named 'muse.smart'` (red).
**Forbidden:** no network; no edits outside the test file.
**Depends:** none
**Why:** Spec Testing strategy: "the smart-features installer: hash mismatch, resume, disk full".

### Task 9: Installer module, manifest and manifest scripts
- [ ] status
**Objective:** `muse/smart.py` passes `tests/test_smart_install.py`, and `muse/smart_manifest.json` holds the real pinned weights.
**Write-scope:**
`muse/smart.py` (new)
`muse/smart_manifest.json` (new)
`scripts/build_manifest.py` (new)
`scripts/manifest_add_worker.py` (new)
**Read-context:** `tests/test_smart_install.py` (from Task 8), this plan's Assumptions (Pinned files, Manifest, Installer)
**Anchors:**
`def save(path: Path, settings: dict) -> None:` @ `muse/settings.py`
**Steps:**
1. `smart.py`: every signature in Task 8 step 3. `host_triple`: `("darwin","arm64")` -> `aarch64-apple-darwin`, `("darwin","x86_64")` -> `x86_64-apple-darwin`, `("win32","amd64"|"x86_64")` -> `x86_64-pc-windows-msvc`, `("linux","x86_64")` -> `x86_64-unknown-linux-gnu`, else None (`sys.platform`, `platform.machine().lower()`). `files_for`: weights + `workers[triple]` (raise `Unsupported` when missing or triple None). `download` uses `session or requests.Session()`, 1 MiB chunks, sha256 over the existing `.part` first when resuming, `os.replace` into place. `install`: disk check, download each missing file, extract the worker zip into `models/worker` preserving `(info.external_attr >> 16) & 0o777` when non-zero, delete the zip, write `installed.json` last (`settings.save` style: write tmp then `os.replace`). `status`: `installed` -> `{"state": "installed", "download_bytes": 0}`; `Unsupported` -> `{"state": "unsupported", "download_bytes": 0}`; else `not_installed` and the sum of sizes of files not yet at their final path.
2. `scripts/build_manifest.py` (`--laya-dir`, default `~/models/laya-muse-v4`; `--laya-repo`, default `IanTheMitten/laya-muse-v4`; `--out`, default `muse/smart_manifest.json`): for each pinned repo and file list in Assumptions, `GET https://huggingface.co/api/models/<repo>/tree/<rev>?recursive=1`; LFS files take `lfs.oid` and `size`; non-LFS files are downloaded from the resolve URL and hashed; Laya files are hashed locally. Keep an existing `workers` object. Run it to write the manifest (`"version": 1`).
3. `scripts/manifest_add_worker.py <triple> <zip> <url>`: sets `workers[triple]` = `{path: "muse-worker-<triple>.zip", url, sha256, size}`.
**New symbols:** as Task 8 step 3; scripts as above
**Verify:** `uv run python scripts/build_manifest.py` then `uv run pytest -p no:cacheprovider tests/test_smart_install.py` then `uv run python -c "import json; m = json.load(open('muse/smart_manifest.json')); print(len(m['weights']), sum(f['size'] for f in m['weights']))"`. Expected: tests pass; prints `40` and a total between 3500000000 and 3700000000.
**Forbidden:** no torch import; no edits to the tests; no worker entries invented (`workers` stays `{}`).
**Depends:** Task 8
**Why:** Decision 4: hash-pinned manifest shipped with the app, resumable downloads into `models/`.

### Task 10: Wire the worker and installer into the app
- [ ] status
**Objective:** The chain runs embed, MERT and Laya through the worker when `ml_on`; `/api/smart` and `/api/smart/install` work; vibe search uses the worker; config and health follow Assumptions.
**Write-scope:**
`muse/config.py`
`muse/api.py`
`muse/jobs.py`
`tests/conftest.py`
`tests/test_config.py`
`tests/test_health_content.py`
`tests/test_embed_startup_wiring.py`
`tests/test_api_vibe.py`
`tests/test_api_smart.py` (new)
**Read-context:** `muse/config.py:19-110`, `muse/api.py:452-720`, `muse/api.py:844-882`, `muse/api.py:1003-1050`, `muse/jobs.py:87-100`, `muse/jobs.py:195-340`, `muse/worker.py` (from Task 7), `muse/smart.py` (from Task 9), this plan's Assumptions (When ML runs, API)
**Anchors:**
`async def run_chain(launch: bool) -> None:` @ `muse/api.py`
`def health() -> dict:` @ `muse/api.py`
`async def run_embed_backfill(` @ `muse/jobs.py`
`def _event(job: dict | None) -> dict:` @ `muse/jobs.py`
`def run(conn, embedder: ContentEmbedder | None = None) -> dict:` @ `muse/content_embed_batch.py`
`def run(conn, ask, model: str) -> dict:` @ `muse/lyric_tag_batch.py`
`class WorkerClient` @ `muse/worker.py` (from Task 7)
`def install(models_dir: Path, manifest: dict, triple: str, *, session=None, on_progress=None, disk_free=shutil.disk_usage) -> None:` @ `muse/smart.py` (from Task 9)
**Steps:**
1. `Config`: add `models_dir: Path` (from_env: `store("MUSE_MODELS_DIR", "models")`) and `worker_cmd: str | None = None` (`MUSE_WORKER_CMD`); remove `content_embed`, `laya_url`, `laya_model`, `laya_timeout_ms`, `curator_jev_url`, `curator_model`, `curator_timeout_ms` and their env reads and comments; `embed_batch` comment: "Run the ML steps without an installed worker (dev, with MUSE_WORKER_CMD)". `make_config`: add `models_dir=tmp_path / "models"`, drop `content_embed=False`. Fix `tests/test_config.py` accordingly.
2. `create_app(..., worker_client=None)`: lifespan sets `app.state.worker = worker_client or WorkerClient(worker_command(config.models_dir, config.worker_cmd))`; shutdown calls `app.state.worker.stop()`. Factories return the injected encoder or `Worker*Encoder(app.state.worker)`. Remove the `ClapAudioEncoder, MerAudioVAEncoder, MiniLmLyricsEncoder, TextVAEncoder` import and the standalone startup `embed_task`.
3. `run_chain`: after art, when `config.embed_batch or smart.installed(config.models_dir)`: `app.state.worker.begin()`, then each in its own `try/except Exception: log.exception(...)`: `await run_embed_backfill(conn, <factories>, bus, ...)`; `await asyncio.to_thread(content_embed_batch.run, conn, WorkerContentEmbedder(app.state.worker))`; `await asyncio.to_thread(lyric_tag_batch.run, conn, worker_ask(app.state.worker), LAYA_MODEL)`. Then the taste retrain as before. `run_content_backfill(conn, library_db, embedder)` gains a required `embedder` passed to `content_embed_batch.run(conn, embedder)` (Task 11 removes the default); the download worker's `content_backfill` calls it with `WorkerContentEmbedder(app.state.worker)` under the same `ml_on` check; update its callers in tests (`grep -rn run_content_backfill tests/`).
4. `jobs.py`: `async def run_smart_install(conn, bus: JobBus, models_dir: Path, manifest: dict, triple: str | None, *, session=None, disk_free=None) -> dict`: create `smart_install` job, running, `asyncio.to_thread(smart.install, ...)` with `on_progress` updating progress and message `f"{done / 1e9:.1f} of {total / 1e9:.1f} GB"` and publishing `bus.publish_threadsafe`; errors -> `error` state with `str(error)` ("LRCLIB unreachable"-style plain text: `requests.ConnectionError` -> "Download server unreachable"); done -> `done`. Lifespan reaps running `smart_install` jobs like the others.
5. Routes: `GET /api/smart` and `POST /api/smart/install` per Assumptions (manifest = `smart.load_manifest()`, triple = `smart.host_triple()`); on success the task spawns the chain. `/api/health` `content`: replace `expected_on_this_host` with `"smart_installed": smart.installed(config.models_dir)`. Vibe search: use `app.state.text_encoder` when set; else, when not `ml_on`, 503 "Vibe search needs smart features"; else `app.state.worker.begin()` and `WorkerAudioEncoder(app.state.worker).encode_text(q)`.
6. Tests: `test_health_content.py` (new key), `test_embed_startup_wiring.py` (chain runs embed via injected encoders when `embed_batch=True`, not when False and not installed), `test_api_vibe.py` (503 when not installed and no injected encoder; message contains "smart features"), new `test_api_smart.py` with a fake `worker_client` and a fake installer path: GET states (`not_installed` with a test manifest via monkeypatched `smart.load_manifest`/`host_triple`; `unsupported`; `installed` after writing `installed.json`), POST 202 then job reaches `done` with a fake session, 409 while running, chain runs the content and lyric-tag steps through the fake client when installed.
**New symbols:** `Config.models_dir`, `Config.worker_cmd`; `create_app(worker_client=...)`; `run_smart_install(...)`; routes `GET /api/smart`, `POST /api/smart/install`
**Verify:** `uv run pytest -p no:cacheprovider` then `bash scripts/smoke.sh`. Expected: full suite green; `SMOKE-OK`.
**Forbidden:** no torch import path from `muse/api.py`; never run ML inside a request except vibe search's one call; don't delete `universal_reco/`, `systemone.py` or `content.SubprocessContentEmbedder` (Task 11); don't open `/api/jobs/stream` in tests.
**Depends:** Task 7, Task 9
**Why:** Decision 4 and the spec Data flow: install, then a library-wide embedding batch; every leg degrades to missing until then.

### Task 11: Retire universal_reco, systemone and the Jev path
- [ ] status
**Objective:** `universal_reco/`, `muse/systemone.py` and the subprocess MERT embedder are gone; the standalone batches run through the worker; tests follow.
**Write-scope:**
`universal_reco/`
`muse/systemone.py`
`muse/content.py`
`muse/content_embed_batch.py`
`muse/lyric_tag_batch.py`
`pyproject.toml`
`tests/test_systemone.py`
`tests/test_content_subprocess.py`
`tests/test_content_slow.py`
`tests/test_lyric_tag_batch.py`
`tests/test_jobs_content_embed.py`
`tests/fixtures/sample.wav` (new)
**Read-context:** `muse/content.py:1-30`, `muse/content.py:155-300`, `muse/content_embed_batch.py:1-60`, `muse/content_embed_batch.py:150-247`, `muse/lyric_tag_batch.py:1-60`, `muse/lyric_tag_batch.py:222-274`, `tests/test_lyric_tag_batch.py:1-40`, `tests/test_content_slow.py:1-60`, `muse/worker.py` (from Task 7)
**Anchors:**
`class SubprocessContentEmbedder:` @ `muse/content.py`
`def main(argv: list[str] | None = None) -> int:` @ `muse/lyric_tag_batch.py`
`def run(conn, embedder: ContentEmbedder | None = None) -> dict:` @ `muse/content_embed_batch.py`
**Steps:**
1. `git mv universal_reco/tests/fixtures/sample.wav tests/fixtures/sample.wav`, then `git rm -r universal_reco muse/systemone.py tests/test_systemone.py tests/test_content_subprocess.py`.
2. `content.py`: delete `UNIVERSAL_RECO_ROOT`, `_uv_executable`, `DEFAULT_COMMAND`, `SubprocessContentEmbedder` and now-unused imports; module docstring says the MERT leg runs in the ML worker (`muse/worker.py`, `muse/mert.py`).
3. `content_embed_batch.py`: `run(conn, embedder)` requires an embedder (no subprocess default); `main()` builds `WorkerContentEmbedder(WorkerClient(worker_command(config.models_dir, config.worker_cmd)))` and stops the client at the end; docstrings drop systemd/universal_reco wording.
4. `lyric_tag_batch.py`: drop the `muse.systemone` import, the key check and `jev_backend`; `main()` runs `run(conn, worker_ask(client), LAYA_MODEL)` with a `WorkerClient` as above; `import_dir(..., model: str = "jev-latest")` keeps its default (it labels imported prototype files). Module docstring: tags come from Laya in the ML worker.
5. `tests/test_lyric_tag_batch.py`: replace `SystemOneError` with `WorkerReplyError` from `muse.worker` where a failing ask is simulated. `tests/test_content_slow.py`: rewrite as one slow test driving `content_embed_batch.run(conn, WorkerContentEmbedder(WorkerClient([sys.executable, "-m", "muse.worker_main", "--weights", str(tmp_path)])))` on `tests/fixtures/sample.wav`, asserting one 768-wide vector. `tests/test_jobs_content_embed.py`: pass an embedder wherever `run` relied on the default.
6. `pyproject.toml`: the `slow` marker text drops universal_reco and names the `embed` extra.
**New symbols:** none
**Verify:** `uv run python -c "import pathlib; print(pathlib.Path('universal_reco').exists())"` then `uv run pytest -p no:cacheprovider` then `uv run --extra embed pytest -p no:cacheprovider -m slow tests/test_content_slow.py`. Expected: `False`; full suite green; slow test passes.
**Forbidden:** no change to `content.py`'s scoring, stats or coverage functions; don't touch `muse/api.py`; no schema migration.
**Depends:** Task 10
**Why:** M1 milestone list: "retiring `universal_reco/`"; spec Decision 5 (no hosted Jev, no personal keys).

## Phase 4 - Desktop UI

### Task 12: Asterisk brand mark across icon, tray, favicon, nav and boot page [design]
- [x] status
**Objective:** Every brand surface shows the asterisk mark in the website palette per **Design: brand-mark**, and the generated icon set is rebuilt from the new SVGs.
**Write-scope:**
`src-tauri/icons/`
`src-tauri/boot/index.html`
`web/public/favicon.svg` (new)
`web/index.html`
`web/src/components/Icons.tsx`
`web/src/components/NavBar.tsx`
`web/src/styles/shell.css`
**Read-context:** `src-tauri/icons/icon.svg`, `src-tauri/icons/tray-template.svg`, `src-tauri/boot/index.html:1-60`, `web/src/components/NavBar.tsx:29-43`, `web/src/styles/shell.css:14-20`, `web/src/styles/shell.css:70-80`, `web/index.html`, `DESIGN.md:350-362`
**Anchors:**
`export function NavBar() {` @ `web/src/components/NavBar.tsx`
**Steps:**
1. `icon.svg`, `tray-template.svg`, `web/public/favicon.svg` per the brief's CONSTRAINTS (gradient `<linearGradient>` x1 0 y1 0 x2 1 y2 1, stops `#f5b9dc` and `#ffdb8c`; glow a `<radialGradient>` of `#7a4b8f` at 0.55 opacity fading to 0, centred at 35%/30% of the tile).
2. `Icons.tsx`: `export function BrandMark()` renders `<svg viewBox="0 0 256 256" aria-hidden="true" focusable="false" className="brand-mark"><path d="<asterisk path>"/></svg>` with `fill="currentColor"`.
3. `NavBar.tsx`: `<span className="nav-brand">muse<BrandMark /></span>`. `shell.css` `.nav-brand` (wide rail rule at line 78): lowercase wordmark weight 750, letter-spacing -0.04em, inline-flex, gap 4px; `.nav-brand .brand-mark` 0.8em, colour `#f5b9dc`, light scheme `#a8437a` under the existing light selectors.
4. `index.html`: `<link rel="icon" href="/favicon.svg" type="image/svg+xml" />`; dark theme-color `#24142c`.
5. `boot/index.html`: dark `--bg #24142c`, `--raised #2f1c38`; inline 48 px asterisk SVG with the gradient above the h1, `aria-hidden="true"`.
6. Regenerate: `npm --prefix web ci`, `bash scripts/tauri.sh icon src-tauri/icons/icon.svg -o src-tauri/icons`, delete `src-tauri/icons/android/` and `src-tauri/icons/ios/`, `bash scripts/tauri.sh icon src-tauri/icons/tray-template.svg -o src-tauri/icons/tray -p 44`.
**New symbols:** `BrandMark` in `web/src/components/Icons.tsx`
**Verify:** `node ~/.claude/skills/design-brain/scripts/preflight.mjs src-tauri/boot/index.html web/src/components/NavBar.tsx web/src/styles/shell.css` then `uv run python -c "import xml.dom.minidom as m; [m.parse(p) for p in ('src-tauri/icons/icon.svg', 'src-tauri/icons/tray-template.svg', 'web/public/favicon.svg')]; print('svg-ok')"` then `npm --prefix web run build`. Expected: preflight passes; `svg-ok`; build green.
**Forbidden:** don't change `tokens.css`, the record-wash accent rule, or any other view; no web font added; no text in icons; no animation.
**Depends:** none
**Design:** brand-mark
**Why:** Owner request this round: the app logo becomes the website's asterisk in its colours.

### Task 13: Smart-features banner and Settings section [design]
- [ ] status
**Objective:** The UI offers, installs and reports smart features per **Design: smart-features**.
**Write-scope:**
`web/src/api.ts`
`web/src/components/SmartBanner.tsx` (new)
`web/src/styles/smart.css` (new)
`web/src/App.tsx`
`web/src/views/Settings.tsx`
`web/src/components/JobProgress.tsx`
**Read-context:** `web/src/api.ts:100-175`, `web/src/App.tsx:43-88`, `web/src/views/Settings.tsx:21-60`, `web/src/views/Settings.tsx:215-250`, `web/src/jobs/JobsContext.tsx:1-87`, `web/src/components/JobProgress.tsx:1-70`, `web/src/styles/settings.css:1-20`, `PRODUCT.md`, this plan's Assumptions (API)
**Anchors:**
`export async function get<T>(path: string, signal?: AbortSignal): Promise<T> {` @ `web/src/api.ts`
`export function App() {` @ `web/src/App.tsx`
`export function Settings() {` @ `web/src/views/Settings.tsx`
`export const useJobs = () => useContext(JobsContext);` @ `web/src/jobs/JobsContext.tsx`
**Steps:**
1. `api.ts`: `export type SmartStatus = { state: "installed" | "not_installed" | "unsupported"; download_bytes: number; job: Job | null }`; `getSmartStatus(signal?)` (GET `/api/smart`), `installSmartFeatures()` (POST `/api/smart/install`, returns `{job_id}`), using the file's existing fetch helpers.
2. `SmartBanner.tsx`: loads status, re-reads it when `useJobs().job` of kind `smart_install` changes state; renders per the brief (offer, installing with the scaleX fill, failed with Try again); module-level `let dismissed = false` for Not now; hidden on `/settings` (`useLocation`).
3. `App.tsx`: `<SmartBanner />` as the first child of `<main className="main">`. `smart.css` imported by the banner.
4. `Settings.tsx`: `SmartFeatures()` section after Playback, per the brief's CONTENT; Install/Try again calls `installSmartFeatures`.
5. `JobProgress.tsx` labels: `smart_install` "Installing smart features", `embed` "Listening to your songs", `content_embed` "Listening to your songs", `lyric_tag` "Reading lyrics".
**New symbols:** `SmartStatus`, `getSmartStatus`, `installSmartFeatures`, `SmartBanner`, `SmartFeatures`
**Verify:** `node ~/.claude/skills/design-brain/scripts/preflight.mjs web/src/components/SmartBanner.tsx web/src/styles/smart.css web/src/views/Settings.tsx` then `npm --prefix web test` then `npm --prefix web run build`. Expected: preflight passes; 27+ tests pass; build green.
**Forbidden:** no modal; no localStorage/sessionStorage for the dismissal; no new npm dependency; don't touch `shell.css` or `tokens.css` (Task 12 owns `shell.css`).
**Depends:** Task 10
**Design:** smart-features
**Why:** Decision 4: a dismissible banner at every launch until installed, no permanent opt-out.

## Phase 5 - Worker packaging

### Task 14: Freeze the worker and smoke it locally
- [ ] status
**Objective:** `scripts/freeze_worker.py` builds `build/worker-dist/muse-worker/` and a zip, and `scripts/worker_smoke.py` proves the frozen worker answers hello, mert, clap_audio, minilm and laya on a fixture.
**Write-scope:**
`scripts/freeze_worker.py` (new)
`scripts/worker_smoke.py` (new)
`scripts/fetch_weights.py` (new)
`scripts/freeze_sidecar.py`
**Read-context:** `scripts/freeze_sidecar.py:1-40`, `docs/plans/2026-10-29-desktop-m4a-preflight.md` (from Task 3), `muse/worker_main.py` (from Task 5), `muse/smart.py` (from Task 9), `scripts/worker_entry.py` (from Task 5)
**Anchors:**
`def main(argv: list[str] | None = None) -> int:` @ `muse/worker_main.py` (from Task 5)
`def download(file: ManifestFile, models_dir: Path, *, session=None, on_bytes=None) -> Path:` @ `muse/smart.py` (from Task 9)
**Steps:**
1. `freeze_worker.py`: triple from `rustc -vV` as `freeze_sidecar.py` does; PyInstaller `scripts/worker_entry.py --onedir --noconfirm --log-level WARN --name muse-worker --distpath build/worker-dist --workpath build/pyinstaller-worker --specpath build/pyinstaller-worker` plus the flag list from the Task 3 results file verbatim and `--collect-submodules muse`; then `shutil.make_archive("build/muse-worker-<triple>", "zip", root_dir="build/worker-dist", base_dir="muse-worker")` and print the zip path, size and sha256.
2. `fetch_weights.py --keys mert,minilm --out DIR`: downloads the manifest weights whose path starts `weights/<key>/` with `smart.download` into `DIR` (so files land at `DIR/weights/<key>/...`).
3. `worker_smoke.py --worker EXE --weights DIR --ops hello,mert,... --fixture tests/fixtures/smoke/tone.wav`: starts the exe, sends each op (laya with one choice question), checks dims (mert 768, clap_audio 512, minilm 384) and hello's ops, closes stdin, waits 20 s for exit 0, prints `WORKER-SMOKE-OK` or the failure and exits 1.
4. `freeze_sidecar.py`: `--add-data muse/smart_manifest.json{os.pathsep}muse` so `smart.MANIFEST_PATH` resolves in the frozen sidecar.
5. Local run: `uv run --extra embed --group package python scripts/freeze_worker.py`, then the smoke with `--weights` a tmp dir holding only a `laya` symlink to `~/models/laya-muse-v4` (other models fall back to the HF cache) and `--ops hello,mert,clap_audio,minilm,laya`. Record dist size and smoke time in the commit message. Delete `build/worker-dist/` and `build/pyinstaller-worker/` afterwards.
**New symbols:** scripts as above
**Verify:** `uv run --extra embed --group package python scripts/freeze_worker.py` then `uv run python scripts/worker_smoke.py --worker build/worker-dist/muse-worker/muse-worker --weights build/smoke-weights --ops hello,mert,clap_audio,minilm,laya --fixture tests/fixtures/smoke/tone.wav` (after creating `build/smoke-weights/laya` as the symlink) then `npm --prefix web run build` then `uv run --group package python scripts/freeze_sidecar.py`. Expected: zip printed; `WORKER-SMOKE-OK`; sidecar freeze succeeds.
**Forbidden:** no `--onefile` for the worker; no new dependency; don't commit `build/`.
**Depends:** Task 5, Task 9
**Why:** Decision 4: a second frozen binary per OS. Proven on Linux before CI spends macOS minutes.

### Task 15: CI worker job
- [ ] status
**Objective:** `.github/workflows/package.yml` gains a `worker` job on `macos-15` and `windows-latest` that freezes, smoke-tests and uploads the worker zip.
**Write-scope:**
`.github/workflows/package.yml`
**Read-context:** `.github/workflows/package.yml:1-43`, `scripts/freeze_worker.py` (from Task 14), `scripts/fetch_weights.py` (from Task 14), `scripts/worker_smoke.py` (from Task 14)
**Anchors:**
`def main(argv: list[str] | None = None) -> int:` @ `muse/worker_main.py` (from Task 5)
**Steps:**
1. New job `worker`: matrix `os: [macos-15, windows-latest]`, `fail-fast: false`, `timeout-minutes: 90`, `defaults.run.shell: bash`. Steps: `actions/checkout@v7`; `astral-sh/setup-uv@v10.3.3` with `enable-cache: true`; `dtolnay/rust-toolchain@stable` (for the triple); `uv run --extra embed --group package python scripts/freeze_worker.py`; `uv run --extra embed python scripts/fetch_weights.py --keys mert,minilm --out build/ci-weights`; `uv run python scripts/worker_smoke.py --worker build/worker-dist/muse-worker/muse-worker${{ runner.os == 'Windows' && '.exe' || '' }} --weights build/ci-weights --ops hello,mert,minilm --fixture tests/fixtures/smoke/tone.wav`; `actions/upload-artifact@v7` named `worker-${{ matrix.os }}` with `build/muse-worker-*.zip`, `if-no-files-found: error`.
2. Header comment: add "The worker job freezes torch; Intel Macs get no worker (no torch wheel)."
**New symbols:** none
**Verify:** `uv run --with pyyaml python -c "import yaml; d = yaml.safe_load(open('.github/workflows/package.yml')); print(sorted(d['jobs']))"`. Expected: `['package', 'worker']`.
**Forbidden:** no Linux leg; no release publishing or signing; no new triggers; don't change the `package` job.
**Depends:** Task 14
**Why:** Owner answer: worker freezes in CI on macOS and Windows, run once at the end (Task 17).

## Phase 6 - Docs and proof

### Task 16: Docs for the worker, smart features, licences and brand [docs]
- [ ] status
**Objective:** README, CLAUDE.md, CONTEXT.md and DESIGN.md describe M4a as built.
**Write-scope:**
`README.md`
`CLAUDE.md`
`CONTEXT.md`
`DESIGN.md`
**Read-context:** `README.md:28-110`, `CLAUDE.md`, `CONTEXT.md`, `DESIGN.md:340-362`, this plan's Assumptions and Owner checks, `muse/worker.py` (from Task 7), `muse/smart.py` (from Task 9)
**Anchors:**
`def worker_command(models_dir: Path, override: str | None = None) -> list[str]:` @ `muse/worker.py` (from Task 7)
**Steps:**
1. README: Windows data dir `%LOCALAPPDATA%\io.github.ianthemitten.muse`; `models/` in "Where data lives"; a "Smart features" section (what it does, download size from the manifest total, where it installs, unsupported on Intel Macs and Linux); dev run (`uv sync --extra embed`, `MUSE_EMBED_BATCH=1 MUSE_WORKER_CMD="uv run --extra embed python -m muse.worker_main --weights <dir>" uv run python -m muse`); freezing the worker; Licenses: the model licences from Assumptions, "free and non-commercial" consequence, PyAV/FFmpeg LGPL, Phosphor asterisk MIT.
2. CLAUDE.md: replace the `universal_reco/` paragraph and its commands with the worker (`muse/worker_main.py`, `muse/worker.py`, `muse/smart.py`, `muse/mert.py`, `muse/audio_decode.py`), the torch rule (worker only), and `uv run --extra embed pytest -m slow`; drop `muse.systemone`/Jev mentions.
3. CONTEXT.md: add "ML worker", "smart features", "smart-features installer", "worker protocol"; update entries that name `universal_reco` or Jev.
4. DESIGN.md: the app icon and tray glyph entry (asterisk, plum tile, gradient), boot page colours, nav wordmark, and a "Smart features" component entry (banner and Settings section).
5. A "Releasing the worker" list in README: the owner checks 2-3 commands verbatim.
**New symbols:** none
**Verify:** `grep -rn "universal_reco\|systemone\|JEV_API_KEY\|%APPDATA%" README.md CLAUDE.md CONTEXT.md`. Expected: no output (exit 1).
**Forbidden:** no claims about Mac/Windows behaviour not yet run (say "after the owner publishes"); don't edit code or `docs/specs/`.
**Depends:** Task 11, Task 12, Task 13, Task 14
**Why:** CLAUDE.md and CONTEXT.md are the agents' map; stale `universal_reco` text would send the next executor into a deleted directory.

### Task 17: Run the CI matrix with the worker [experiment]
- [ ] status
**Objective:** One CI run on tag `ci-package-m4a-1` builds the app (three legs) and the worker (two legs); results recorded.
**Write-scope:**
`docs/plans/2026-10-29-desktop-m4a-ci-results.md` (new)
**Read-context:** `.github/workflows/package.yml` (from Task 15), `docs/plans/2026-10-29-desktop-m3-ci-results.md`
**Anchors:**
`def main() -> int:` @ `scripts/shell_smoke.py`
**Steps:**
1. `git push origin worktree-m4-plan`, then `git tag ci-package-m4a-1`, then `git push origin ci-package-m4a-1`.
2. `gh run list --workflow package.yml --limit 1 --json databaseId,status`; `gh run watch <id>`; `gh run download <id> -D build/ci`.
3. Results file: per leg status and duration; worker zip size and sha256 per triple; smoke reports; any failure with its log excerpt and a proposed fix. A failure is recorded, not fixed here.
Negative result is a valid outcome.
**New symbols:** none
**Verify:** `uv run python -c "import pathlib; print(pathlib.Path('docs/plans/2026-10-29-desktop-m4a-ci-results.md').stat().st_size > 0)"`. Expected: the per-leg results are recorded; prints `True`.
**Forbidden:** no release publishing; no repo visibility change; no workflow edits in this task.
**Depends:** Task 15, Task 16
**Why:** The only place the worker meets macOS and Windows before the owner publishes it.
