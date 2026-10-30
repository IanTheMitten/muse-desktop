# Plan - Muse desktop M5: the downloader unlock, Add, and the v0.1 alpha

Spec: `docs/specs/2026-10-30-desktop-m5-downloader-and-alpha.md` (handoff lint exit 0). Every
Decision there is a premise; cited as `spec:<line>`. Refusals: no ffmpeg, no `yt_dlp` module,
no signing, no auto-update, README never mentions the unlock.

Run with `/autopilot docs/plans/2026-10-30-desktop-m5-downloader-and-alpha.md` in a Sonnet
session, from worktree `.claude/worktrees/m5-plan` (branch `worktree-m5-plan`, forked from
`worktree-m5-spec` at 3b0a476, which sits on origin/main 76fdc3b). Local `main` (cb3349a) is stale:
never base work on it.

## Assumptions (decided here; spec Deferred items and plan-time gaps)

1. **yt-dlp flags (spec:553, Deferred).** Pinned from `--help` of the verified 2026.10.26
   `yt-dlp_linux` (sha256 `767ba9dc...6795` matches `SHA2-256SUMS`). Every call starts
   `--ignore-config --no-warnings --no-playlist`. Probe: `-J <query>` (no download), answer is
   `entries[0]` when `entries` exists, else the dict. Download: `--no-progress --force-overwrites
   -f bestaudio[ext=m4a] -o <dir>/<base>.%(ext)s -J --no-simulate <query>`. Update: `-U`. Not
   `--flat-playlist` (flat entries carry no `thumbnail`). A live YouTube probe was refused by the
   planning session's permission classifier, so Task 2 measures it (see risk below).
2. **Risk carried into Task 2:** yt-dlp releases since late 2025 want an external JS runtime
   (Deno) for full YouTube support. Whether itag 140 (`m4a`) still resolves without one is
   unmeasured. Task 2 is the gate; a negative result parks Phase 2 for the owner.
3. **Remux lives in `muse/remux.py`** (new), not inline in ingest, so the preflight script and
   ingest share one function. `NO_M4A` is defined there. The remux runs inside
   `YtDlpAudioSource.fetch` (spec:72-75 puts it in the source), so it precedes the length check;
   a stream copy of a wrong 52-minute file costs seconds and the staged file is deleted either way.
4. **Staging name** is `downloads/.incoming/<track_rowid>.<ext>`; the final name is
   `downloads/<safe Artist>/<safe Album or Singles>/<NN> - <safe Title>.<ext>` under
   `music_root.resolve()` (the scanner compares resolved paths, `muse/scanner.py:125`).
5. **`mark_done` also records `file_size` and `file_mtime`.** Without them the next scan re-reads
   tags of a downloaded row (`muse/scanner.py:166-169`) and re-keys its album away from the
   MusicBrainz-allocated `album_rowid`.
6. **Lock bridge.** `run_download` runs in a worker thread; `chain_lock` is an `asyncio.Lock`.
   `run_download` takes `under_lock(fn)`; the app's version is
   `asyncio.run_coroutine_threadsafe(<async with chain_lock: await asyncio.to_thread(fn)>, loop).result()`.
   Default (tests) calls `fn()` directly.
7. **Freeze filter is already dead in the fork:** `/api/discover/artists/{mbid}/releases` never
   passes `since` (`muse/api.py:1167-1169`). Decision 8 (spec:311) is deletion only.
8. **`Config` defaults keep tests offline:** `ytdlp_release_url: str = ""` and
   `update_check: bool = False` on the dataclass; `from_env` sets the real values
   (`MUSE_YTDLP_URL`, `MUSE_UPDATE_CHECK`), the same pattern as `download_gap_seconds`
   (`muse/config.py:44-48`). An empty URL makes `ytdlp.install` fail at once with
   "Couldn't get the downloader".
9. **Downloader status** rides on `GET /api/settings` as
   `"downloads": {"path", "songs"} | null` and
   `"downloader": {"installed", "install_state", "update_failed"}`. Retry of a failed install is
   `POST /api/downloader/install` (unlocked only), because re-entering the password is not a retry.
   `ytdlp_install` is queued on unlock only when the binary is missing (Decision 7: relock keeps it).
10. **Update check ignores non-app tags** (`tag_name` must match `^v\d`), and the worker release
    is created with `--latest=false`, so `releases/latest` stays the app.
11. **Opening the release link** uses `tauri-plugin-shell` 2.6.0 already in `src-tauri/Cargo.toml`
    (`window.__TAURI__.shell.open`, permission `shell:allow-open`, default scope
    `^((mailto:\w+)|(tel:\w+)|(https?://\w+)).+`). The shell passes `MUSE_APP_VERSION` from
    `app.package_info().version`; one Rust line, despite spec:261 (needed by Decision 11).
12. **e2e fixtures** copy and re-tag `tests/fixtures/smoke/tone.{mp3,flac,wav}` with mutagen
    rather than encoding new audio (spec:388 says "made with PyAV"; same result, no encoder risk).
    The fake chart and a 404 yt-dlp URL are served by the e2e server script itself.
13. **Playwright pin:** `@playwright/test` `1.64.1` (published 2026-10-14, `npm view`).
14. **Watcher ignores `.incoming`** so a download's partial writes do not spawn chain runs.

## Premises

- Spec ready: `node ~/.claude/skills/spec-brainstorming/scripts/spec-lint.mjs --handoff docs/specs/2026-10-30-desktop-m5-downloader-and-alpha.md` exit 0 (1 warn, no PRD).
- Baseline default suite green: `uv run pytest -p no:cacheprovider -q` exit 0 on 3b0a476; 1212 tests collected (`--co`), `tests/test_audio_decode.py` skipped by `pytest.importorskip("av")` (`tests/test_audio_decode.py:7`) because `av` is not in the core venv (`uv run python -c "import av"` ModuleNotFoundError).
- `yt_dlp` module not installed in the dev venv (`uv run python -c "import yt_dlp"` fails); `muse/ingest.py:191-194` and `muse/api.py:380-381,404-405` guard on `YoutubeDL is None`.
- Download pipeline: `run_download` `muse/ingest.py:343-518`; lyrics bug `muse/ingest.py:482-486`; Genius `muse/ingest.py:265-330`; `YtDlpAudioSource` `muse/ingest.py:197-235`.
- `run_download_worker` `muse/jobs.py:651-708` calls `run_download_job` which runs `run_download_fn` in `asyncio.to_thread` (`muse/jobs.py:626-628`); `run_smart_install` `muse/jobs.py:311-353` is the install-job shape to copy.
- `chain_lock` `muse/api.py:597`; `run_chain` folders `muse/api.py:601-602`; `start_watcher` uses `settings.music_folders` only `muse/api.py:673-683`; download wiring `muse/api.py:688-748`; reap `muse/api.py:542-554`; `_settings_view` `muse/api.py:788-799`; health `catalog_freeze` `muse/api.py:932`; discover routes `muse/api.py:1129,1154,1176`; `create_job` `muse/api.py:1756`; `retry_job` `muse/api.py:1935-1958`; `install_smart` sleep(0) trick `muse/api.py:907-910`.
- Scanner skips dot-dirs and dot-files already: `muse/scanner.py:144-146`.
- `settings.DEFAULTS` already has `downloader_unlocked` (`muse/settings.py:10`); `tests/test_settings.py:5` pins `load(...) == DEFAULTS` (no new keys planned).
- NavBar already gates the Add tab (`web/src/components/NavBar.tsx:31-32`); `/add` always mounted (`web/src/App.tsx:66`); Home chart links non-owned songs to `/add` (`web/src/views/Home.tsx:35-36,164-174`).
- `api.ts` publishes `catalog_freeze` in `Health` (`web/src/api.ts:4-13`); nothing else in `web/src` reads it (`grep -rn catalog_freeze web/src`).
- `lyrics_quality.clean_genius` has no caller but `muse/ingest.py:291` (`grep -rn clean_genius`).
- Tests passing `genius_client`: `tests/test_ingest_lyrics.py`, `tests/test_api_jobs.py`, `tests/test_ingest_run_download.py`, `tests/test_api_discover.py` (`grep -rln genius_client tests`). Tests monkeypatching `YoutubeDL`: `tests/test_ingest_audio.py:32,49`, `tests/test_api_jobs.py:250`.
- Tests hitting the routes Decision 4 guards: `tests/test_api_jobs.py`, `tests/test_api_discover.py`, `tests/test_api_catalog_browse.py` (`grep -rln "/api/jobs\|/api/discover" tests`; `test_api_scan.py` and `test_token.py` only GET `/api/jobs`).
- `catalog_freeze` tests: `tests/test_config.py:4-11`; `since=` tests: `tests/test_discover.py:54-303`.
- `av` excluded from the sidecar freeze `scripts/freeze_sidecar.py:33`; pinned `av==19.0.2` in `embed` extra `pyproject.toml:21-30`; `ingest` extra `pyproject.toml:36-40`.
- `tauri-plugin-shell = "=2.6.0"` `src-tauri/Cargo.toml`; capability `src-tauri/capabilities/default.json`; sidecar env built at `src-tauri/src/main.rs:101-109`; nav guard `allowed_origin` `src-tauri/src/main.rs:52-56`.
- `package.yml` triggers on `v*` and `ci-package-*` tags only; `package` job matrix `macos-15, macos-15-intel, windows-latest`; `worker` job `macos-15, windows-latest`, uploads `build/muse-worker-*.zip` (`.github/workflows/package.yml:4-68`). `scripts/manifest_add_worker.py <triple> <zip> <url>`; `muse/smart_manifest.json` `"workers": {}`.
- Password: fork spec `docs/specs/2026-10-29-desktop-app-fork.md:390` (`download-enjoy`); `printf %s download-enjoy | sha256sum` = `70d35a1c658bc9a2a6b5adfc61b99adcb33aabd7d2f5ff283fc378bb8eb173f3`.
- Charts URL `muse/charts.py:14-15` (`{base}/api/v2/{storefront}/music/most-played/{n}/songs.json`), env `MUSE_CHART_URL` `muse/config.py:81-82`.
- Frontend token: `?t=` is read once into sessionStorage (`web/src/token.ts:3-23`); the page scrolls on the document (`web/src/styles/shell.css:2-3`, `body.locked` `web/src/styles/base.css:8`).
- Disk: 22 GB free (`df -h /home`).

## Memory carried into briefs

- **Worktree sessions refuse compound shell commands** (`a && b`, `a; b`, `cd x && ...`, inline `python -c` with quotes inside pipes): each Verify step is one plain command, run in order. Use `npm --prefix web ...` and the Write tool for files.
- **`python` is not on PATH:** always `uv run python`. A fresh worktree needs `npm --prefix web ci` before web build/test.
- **Red-test batching:** a committed red test file breaks collection for siblings running the full suite. Task Verifies below are targeted; only phase gates run the full suite.
- **`plays` inserts need** `reached_ms`, `duration_ms`, `heartbeat_at`, `ended_reason` (NOT NULL).
- **TestClient hangs on `/api/jobs/stream`:** never open the SSE route in a test.
- **Smoke after startup changes:** `bash scripts/smoke.sh` must print `SMOKE-OK`.
- **Flakes:** exit 139 at pytest teardown, and `test_retry_creates_a_new_job_and_leaves_the_failure_readable` failing once: rerun once.
- **Never `pkill -f`/`pgrep -f` with the pattern on the same line:** use `pgrep -x`.
- **No bare `cargo`:** use `scripts/tauri.sh` (shared target `~/.cache/muse-tauri-target`). **Freeze only after the web build finishes**, never in parallel.
- **Git:** repo-local identity is set, do not change git config; `git commit -- <path>` fails on staged deletions, so `git add` then plain `git commit`.
- **Billed CI:** pushing any `v*`/`worker-v*`/`ci-package-*` tag spends macOS minutes; only Task 21, and only after an explicit owner yes.

## Owner checks (one current list; replaces M3, M4a and M4b lists)

Before Task 21: (a) make the GitHub repo public; (b) upload the five Laya v4 files from
`~/models/laya-muse-v4` to Hugging Face `IanTheMitten/laya-muse-v4`; (c) merge the M5 PR into
`main`; (d) say yes to each tag push.

After the release (on the `v0.1.0` installers):
1. Mac (Apple silicon and Intel): right-click > Open installs; app scrolls on every view; tray Play/Pause and Next; close-to-tray notice, Show Muse, Quit Muse leaves no `muse-core`.
2. Mini player from control bar and tray: title follows, buttons work, art opens main window, Escape closes.
3. Drag a folder and a single song onto the window; Settings "Choose folder…" opens the native picker.
4. Media keys and the Now Playing widget follow playback (M3 check 6; a native bridge is a follow-up only if this fails, spec:555).
5. Smart features: banner at launch, Not now, Install with progress and resume after a network drop, Radio and Muse use the new legs, vibe search answers (Apple silicon and Windows; Intel shows "Not available").
6. Auto-align on 5 English, 5 Korean, 2 Japanese songs; Tap along (Space, Backspace, Escape keeps Now Playing open, Save).
7. Icon in Dock/taskbar, tray glyph, wordmark, favicon, boot page.
8. Unlock on a Mac, then Add one song: no Gatekeeper prompt for `bin/yt-dlp`; the song plays and has lyrics if LRCLIB has them.
9. Windows: unlock, Add one song: no console window flashes; SmartScreen "More info > Run anyway" works.
10. Ten Add downloads of YouTube-Music-only uploads: count "No m4a audio" failures (spec:554 follow-up trigger).
11. When a later release exists: Settings shows "Muse <latest> is out" and the link opens the browser.

## Design: settings-downloader

READ: Reading this as: two quiet rows and one password prompt added to the existing Settings mount table for one listener, plain utility language, leaning toward the Muse settings sections (DESIGN.md §9 "Smart features").
DIALS: VARIANCE 1 / MOTION 1 / DENSITY 4
PALETTE: restrained, existing tokens only: `--text` (row titles), `--text-2` (row state, link), `--hairline` (row rules), `--ink-raised` (dialog surface via `.dlg`), `--accent` only on the dialog's primary Unlock button (`.btn.primary`). No new colour, no new token.
TYPE: display Newsreader Variable (section `h2`, `.serif.dlg-title`) / body Figtree Variable / utility Figtree tabular-nums (version numbers, song counts)
SIGNATURE: the External downloader row reads like any unfinished setting, "Plugins are under development.", so the easter egg is invisible; after unlock the same row says "On. Add is in the menu." beside a quiet "Lock again".
ANCHORS: macOS System Settings > General > About (a version line with an inline "update available" link), Plexamp settings (flat labelled rows, one action per row), 1Password unlock prompt (one password field, one error line, no lockout copy)
VARIETY: cohere, no rotation: same product as the 2026-10-28 ledger entry (system: muse-player). Fingerprint unchanged: labelled rail, art-led shelves, Newsreader + Figtree, record wash.
CONTENT: New section after Removed songs: `h2` "Advanced". Row 1: `.folder-path` "Muse 0.1.0" (from `getUpdate().current`); `.folder-state` holds the link "Muse 0.2.0 is out" only when `latest` is not null (numbers are real data, these are examples). Row 2: `.folder-path` "External downloader"; `.folder-state` locked "Plugins are under development.", unlocked "On. Add is in the menu."; unlocked only, a `.folder-remove` button "Lock again". Dialog (`Dialog`, title "Administrator password"): label "Password" above `input type="password" autocomplete="off"`, buttons "Unlock" (`.btn.primary`, submit) and "Cancel" (`.btn`); 403 shows `p.error role="alert"` "That's not it."; any other failure "Couldn't unlock. Try again.". Music folders: when `settings.downloads` is not null, a first `.folder-row` with `.folder-path` "Downloads (kept by Muse)" and `.folder-state` "<n> songs" (`count(n, "song")` from `web/src/format.ts`), no button. "No folders yet…" shows only when `folder_status` is empty and `downloads` is null.
FORMAT: production
TARGET: existing React 19 + Vite app: `web/src/views/Settings.tsx`, `web/src/styles/settings.css`, reusing `web/src/components/Dialog.tsx` unchanged.
CONSTRAINTS: WCAG AA both schemes; 44px targets; label above input; visible focus; Escape closes the dialog; the Advanced section never hints at a shortcut (no tooltip, no `aria-keyshortcuts`, no hint text); the update link opens outside the app (`shell.shell.open(url)` when `shell` exists, else plain `target="_blank" rel="noreferrer"`); no em dash; no new tokens; no spinner.

## Design: add-downloader-status

READ: Reading this as: one status sentence at the top of the Add screen for an unlocked listener, plain language, leaning on the existing Add strip and `.error` block (DESIGN.md §9, LAYOUT invariant 5).
DIALS: VARIANCE 1 / MOTION 1 / DENSITY 4
PALETTE: restrained, existing tokens: `--text-2` via `.add-note` for the info and warning lines, `.error` (`--hairline` fill, `--text`) for the failure line; no accent (Add spends accent only on `.button-primary` and the strip fill).
TYPE: body Figtree Variable
SIGNATURE: one sentence slot above the Activity strip that swaps between three states and renders nothing when the downloader is ready.
ANCHORS: Homebrew's single "Updating Homebrew..." line before work starts, macOS Software Update's inline "Couldn't check for updates" with a retry, Plexamp's offline strip (one line, never modal)
VARIETY: cohere, no rotation (same ledger note as settings-downloader).
CONTENT: `downloader.installed == false` and `install_state` is `queued` or `running`: `p.add-note role="status"` "Getting the downloader…". Not installed and not active: `p.error role="alert"` "Couldn't get the downloader." plus `button.button.button-quiet` with `RetryIcon` "Try again" (calls `installDownloader()`, then reloads settings). Installed and `update_failed`: `p.add-note role="status"` "Couldn't update the downloader. Downloads may fail until it updates.". `humanError` gains, checked before the existing rules: `/No m4a audio/` "No m4a audio for this video. Try another link."; `/Downloader missing/` "The downloader was missing. Muse is getting it again, so retry in a minute."; `/Already in downloads/` "A file with this name is already in Downloads."; `/Downloader locked/` "Stopped when the downloader was locked.". `JobProgress` label for kind `ytdlp_install`: "Getting the downloader".
FORMAT: production
TARGET: `web/src/views/Add.tsx`, `web/src/styles/add.css` (only if a margin is needed), `web/src/components/JobProgress.tsx`
CONSTRAINTS: WCAG AA; 44px button; no spinner, no motion; no em dash; status line never reddens the rest of the screen; reuse existing classes.

## Design: home-chart-nav

READ: Reading this as: a subtraction on Home and the nav for a locked install, so nothing a listener sees points at a downloader, leaning on the existing shelf rules ("an empty shelf is not drawn", `web/src/views/Home.tsx:28-32`).
DIALS: VARIANCE 1 / MOTION 1 / DENSITY 5
PALETTE: restrained, unchanged tokens; no new colour.
TYPE: display Newsreader Variable (shelf titles, unchanged) / body Figtree Variable
SIGNATURE: while locked, "Top songs right now" lists only songs the listener owns, keeping chart rank numbers, and disappears when none are owned.
ANCHORS: Apple Music "Top Songs" filtered to library (rank kept), Plexamp library-only mode (no store links), Spotify hidden-store regions (absence, not a disabled control)
VARIETY: cohere, no rotation (same ledger note as settings-downloader).
CONTENT: locked: rows with `s.track` only, `TrackRow` unchanged; the "Not in your library" link rows never render. Unlocked: today's behaviour. Nav: Add tab only when unlocked (today), now also refreshed after unlock/lock without a reload.
FORMAT: production
TARGET: `web/src/views/Home.tsx`, `web/src/components/NavBar.tsx`, `web/src/App.tsx`
CONSTRAINTS: WCAG AA; no layout shift beyond removed rows; no em dash; no disabled placeholders for hidden things.

## Phase 1 - Foundation, preflight and the binary helper

### Task 1: Move PyAV into core and add the stream-copy remux
- [x] status
**Objective:** `av==19.0.2` is a core dependency, the sidecar freeze keeps it, and `muse.remux.remux_m4a` stream-copies an m4a in place, proven by tests.
**Write-scope:**
`pyproject.toml`
`uv.lock`
`scripts/freeze_sidecar.py`
`muse/remux.py` (new)
`tests/test_remux.py` (new)
**Read-context:** `pyproject.toml:6-40`; `scripts/freeze_sidecar.py:20-35`; `tests/test_audio_decode.py:53-58`; `muse/ingest.py:238-262` (`tag_m4a`).
**Anchors:** `def tag_m4a(` @ `muse/ingest.py`
**Steps:**
1. `pyproject.toml`: add `"av==19.0.2",` to `[project] dependencies`; delete `"av==19.0.2",` from the `embed` extra. Run `uv lock`, then `uv sync`.
2. `scripts/freeze_sidecar.py:33`: the exclude tuple becomes `("torch", "transformers", "sentence_transformers", "laya")`.
3. `muse/remux.py`: module docstring "Stream-copy YouTube's DASH m4a into a standard m4a with PyAV, no ffmpeg binary (spec Decision 2). `av` is imported inside the function so startup never loads it." Define `NO_M4A = "No m4a audio for this video. Try another link."` and `def remux_m4a(path: Path) -> None`: `import av` inside; `tmp = path.with_name(f"{path.stem}.remux.m4a")`; open `src = av.open(str(path))`; no `src.streams.audio` raises `RuntimeError(NO_M4A)`; `stream = src.streams.audio[0]`; `dst = av.open(str(tmp), "w", format="ipod")`; `out = dst.add_stream_from_template(stream)`; for each `packet in src.demux(stream)`: skip `packet.dts is None`, set `packet.stream = out`, `dst.mux(packet)`; close both (with-blocks); `os.replace(tmp, path)`. Any exception: `tmp.unlink(missing_ok=True)` then `raise RuntimeError(NO_M4A) from error` (re-raise the same message when it already is one).
4. `tests/test_remux.py` (`pytest.importorskip("av")` at top), cases: (a) helper encodes 2 s of a 440 Hz sine to `tmp/a.m4a` with av (`codec "aac"`, 44100 Hz, mono, `format="ipod"`); `remux_m4a` leaves `a.m4a` present, no `*.remux.m4a` left, `mutagen.mp4.MP4(path).info.length` within 0.1 of 2.0; then `tag_m4a(path, artist_name="A", album_name="B", track_title="C", track_id=None, track_no=1, total_tracks=1, disc_no=None)` and `MP4(path)["\xa9nam"] == ["C"]`. (b) 1024 random bytes as `bad.m4a` raise `RuntimeError` whose `str` equals `NO_M4A`, and no `bad.remux.m4a` remains. (c) subprocess `[sys.executable, "-c", "import sys, muse.remux; assert 'av' not in sys.modules"]` with `check=True`.
**New symbols:** `NO_M4A: str`, `def remux_m4a(path: Path) -> None` in `muse/remux.py`.
**Verify:** `uv run pytest -p no:cacheprovider tests/test_remux.py tests/test_audio_decode.py -q` - expected: all pass, test_audio_decode now runs (not skipped). Then `uv run pytest -p no:cacheprovider -q` - expected: exit 0.
**Forbidden:** no other dependency changes; don't touch `muse/ingest.py`; don't edit `tests/test_audio_decode.py`.
**Depends:** none
**Why:** Decision 2 (spec:127-158) replaces ffmpeg with a PyAV stream copy; av must ship in the sidecar. Lazy import keeps the startup property `tests/test_audio_decode.py:55` pins.

### Task 2: Preflight the yt-dlp binary and the m4a path against YouTube [experiment]
- [x] status
**Objective:** A recorded measurement of whether the 2026.10.26+ standalone binary, with the plan's exact flags and no JS runtime, yields an m4a that `remux_m4a` and `tag_m4a` accept.
**Write-scope:**
`scripts/ytdlp_preflight.py` (new)
`docs/plans/2026-10-30-desktop-m5-preflight.md` (new)
**Read-context:** this plan's Assumptions 1-2; `muse/remux.py` (from Task 1); `muse/ingest.py:238-262`.
**Anchors:** `def remux_m4a(path: Path) -> None` @ `muse/remux.py` (from Task 1)
**Steps:**
1. `scripts/ytdlp_preflight.py` (argparse; `--out` default `build/ytdlp-preflight`; `--query` default `ytsearch1:Rick Astley - Never Gonna Give You Up`). Steps, each recorded in a dict `report`:
   a. GET `https://github.com/yt-dlp/yt-dlp/releases/latest/download/SHA2-256SUMS` and the host asset (`yt-dlp_linux` on linux, `yt-dlp_macos` on darwin, `yt-dlp.exe` on win32) with `requests` (timeout 300); compare sha256; write `<out>/bin/<asset>` mode `0o755`. Record `sha256_ok`.
   b. `[bin, "--version"]` - record `version`.
   c. Probe `[bin, "--ignore-config", "--no-playlist", "-J", query]` (warnings kept on purpose); record `probe_rc`, the stderr lines containing `WARNING` (the JS-runtime warning, if any), `entries[0]` keys `id`, `title`, `duration`, `thumbnail` presence, and the audio-only formats as `[format_id, ext, acodec]` where `vcodec == "none"`.
   d. Download `[bin, "--ignore-config", "--no-warnings", "--no-playlist", "--no-progress", "--force-overwrites", "-f", "bestaudio[ext=m4a]", "-o", "<out>/dl/track.%(ext)s", "-J", "--no-simulate", query]` (timeout 900); record `download_rc`, last stderr line, file size of `<out>/dl/track.m4a`.
   e. `remux_m4a` on it; `mutagen.mp4.MP4` length vs probe `duration` (record both); `tag_m4a` then read `\xa9nam` back.
   f. Print `json.dumps(report, indent=2)` then `PREFLIGHT-OK` if a-e all succeeded, else `PREFLIGHT-FAIL <first failing step letter>`; exit 0 either way.
2. Run `uv run python scripts/ytdlp_preflight.py`. If the session's permission layer refuses the network run, stop and report `- [!]` asking the owner to run `! uv run python scripts/ytdlp_preflight.py` and paste the output.
3. Write `docs/plans/2026-10-30-desktop-m5-preflight.md`: date, host, the full report JSON, the verdict line, and one sentence per finding: m4a available or not, JS-runtime warning text, remux length delta.
4. Delete `build/ytdlp-preflight/`.
**New symbols:** none
**Verify:** `uv run python scripts/ytdlp_preflight.py` - expected: a report JSON and a final verdict line; record it verbatim in the results doc. Then `git status --short docs/plans` - expected: the results doc listed.
Negative result is a valid outcome.
**Forbidden:** no new dependencies; do not install Deno or any JS runtime; don't change `muse/`.
**Depends:** Task 1
**Why:** Five Phase 2-3 tasks sit on this binary and this format (Assumption 2). Phase 1 gate: `PREFLIGHT-OK` lets Phase 2 start; `PREFLIGHT-FAIL c` or `d` (no m4a without a JS runtime) parks the plan for the owner to choose between bundling Deno and the Opus path (spec:554).

### Task 3: Write the muse/ytdlp.py tests [test]
- [x] status
**Objective:** `tests/test_ytdlp.py` pins the binary helper's contract and fails red because `muse.ytdlp` does not exist.
**Write-scope:**
`tests/test_ytdlp.py` (new)
**Read-context:** Task 4's New symbols (copied below); `tests/test_smart_install.py` (fake-session shape); `muse/remux.py` (from Task 1).
**Anchors:** `NO_M4A` @ `muse/remux.py` (from Task 1)
**Steps:**
1. Signatures under test (verbatim from Task 4): `YTDLP_INSTALL = "ytdlp_install"`, `MISSING = "Downloader missing"`, `INSTALL_FAILED = "Couldn't get the downloader"`, `def asset_name(platform: str = sys.platform) -> str`, `def binary_path(data_dir: Path, platform: str = sys.platform) -> Path`, `def install(data_dir: Path, base_url: str, *, session=None, platform: str = sys.platform) -> Path`, `def update(binary: Path, *, run=subprocess.run) -> bool`, `def probe(binary: Path, query: str, *, run=subprocess.run) -> dict | None`, `def download(binary: Path, query: str, out_dir: Path, base_name: str, *, run=subprocess.run) -> dict`.
2. Fake session: `.get(url, timeout)` returns an object with `.content` (bytes) and `.raise_for_status()`; it serves `<base>/SHA2-256SUMS` as `"<sha>  yt-dlp_linux\n<other>  yt-dlp.exe\n"` and `<base>/yt-dlp_linux` as `b"#!fake"`; it records URLs.
3. Cases: (a) `asset_name("darwin") == "yt-dlp_macos"`, `"win32" -> "yt-dlp.exe"`, `"linux" -> "yt-dlp_linux"`; (b) `binary_path(d, "win32") == d / "bin" / "yt-dlp.exe"`, `binary_path(d, "linux") == d / "bin" / "yt-dlp"`; (c) install with a matching hash writes `bin/yt-dlp` with the bytes, mode `& 0o777 == 0o755` (skip mode check on win32), no `*.part` left, URLs fetched are `<base>/SHA2-256SUMS` and `<base>/yt-dlp_linux`; (d) mismatched hash raises `RuntimeError` with `str == INSTALL_FAILED` and leaves no file in `bin/`; (e) `base_url=""` raises `RuntimeError(INSTALL_FAILED)` without calling the session; (f) a session whose `.get` raises `requests.ConnectionError` gives `RuntimeError(INSTALL_FAILED)`.
4. Fake executable (POSIX only, `skipif(sys.platform == "win32")`): a script `#!<sys.executable>` written to `tmp/bin/yt-dlp`, mode 0o755, whose behaviour is driven by its argv: `-U` exits 1; `--no-simulate` in argv writes `<-o template with %(ext)s replaced by m4a>` and prints `{"_type": "playlist", "entries": [{"id": "abc", "duration": 200.0}]}`; otherwise prints `{"_type": "playlist", "entries": [{"id": "abc", "title": "T", "thumbnail": "http://x/t.jpg"}]}`. Cases: (g) `update(fake) is False` and `update(tmp / "missing") is False`; (h) `probe(fake, "ytsearch1:a - b") == {"id": "abc", "title": "T", "thumbnail": "http://x/t.jpg"}`; (i) `probe(tmp / "missing", "q") is None`; (j) `download(fake, "q", out, "7")` returns `{"id": "abc", "duration": 200.0}` and `out / "7.m4a"` exists; (k) `download(tmp / "missing", ...)` raises `RuntimeError(MISSING)`.
5. With an injected `run` returning `subprocess.CompletedProcess(args, 1, "", "ERROR: [youtube] x: Requested format is not available. Use --list-formats")`: (l) `download` raises `RuntimeError(NO_M4A)` (`binary` must exist: touch it); (m) returncode 1 with stderr `"ERROR: boom\n"` raises `RuntimeError("ERROR: boom")`; (n) the `run` call's args start `[str(binary), "--ignore-config", "--no-warnings", "--no-playlist"]` and contain `"-f", "bestaudio[ext=m4a]"`, `"--force-overwrites"`, `"-J"`, `"--no-simulate"`, and end with the query.
**New symbols:** none (tests only)
**Verify:** `uv run pytest -p no:cacheprovider tests/test_ytdlp.py -q` - expected: collection error `ModuleNotFoundError: No module named 'muse.ytdlp'`.
**Forbidden:** no network; no edits outside `tests/test_ytdlp.py`.
**Depends:** Task 1
**Why:** Test-first pairing for Task 4. The fake executable is the spec's own test shape (spec:499-501).

### Task 4: Implement muse/ytdlp.py
- [x] status
**Objective:** One module owns the yt-dlp binary: fetch with hash check, `-U`, the metadata probe and the download, all through `subprocess`; `tests/test_ytdlp.py` goes green.
**Write-scope:**
`muse/ytdlp.py` (new)
**Read-context:** `tests/test_ytdlp.py` (from Task 3); this plan's Assumption 1; `muse/smart.py` (download-and-verify style); `muse/remux.py` (from Task 1).
**Anchors:** `NO_M4A` @ `muse/remux.py` (from Task 1)
**Steps:**
1. Docstring: "The app-managed yt-dlp standalone binary (spec Decision 1): fetched after unlock into `<data dir>/bin`, checked against the release's SHA2-256SUMS, updated with -U, run only as a subprocess. Nothing imports `yt_dlp`."
2. Constants: `YTDLP_INSTALL`, `MISSING`, `INSTALL_FAILED` (values in Task 3 step 1), `SUMS = "SHA2-256SUMS"`, `ASSETS = {"darwin": "yt-dlp_macos", "win32": "yt-dlp.exe", "linux": "yt-dlp_linux"}`, `BASE_FLAGS = ["--ignore-config", "--no-warnings", "--no-playlist"]`; `from .remux import NO_M4A`.
3. `asset_name`: `ASSETS[platform]`, a missing key raises `RuntimeError(INSTALL_FAILED)`. `binary_path`: `data_dir / "bin" / ("yt-dlp.exe" if platform == "win32" else "yt-dlp")`.
4. `install`: empty `base_url` raises `RuntimeError(INSTALL_FAILED)`. `get = (session or requests).get`. Fetch `f"{base_url}/{SUMS}"` (timeout 30) and `f"{base_url}/{asset}"` (timeout 300), `raise_for_status()` each; parse sums lines `"<hex>  <name>"`. Write `target.with_name(target.name + ".part")`; hash mismatch or asset absent from sums: unlink part, raise. `os.chmod(part, 0o755)`, `os.replace(part, target)`, return target. Wrap `requests.RequestException` and `OSError` into `RuntimeError(INSTALL_FAILED)`.
5. `_run(run, args, timeout)`: `run(args, capture_output=True, text=True, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))`.
6. `update`: missing binary returns False; `_run(run, [str(binary), "-U"], 120).returncode == 0`; `OSError`/`subprocess.SubprocessError` returns False.
7. `probe`: missing binary returns None; args `[str(binary), *BASE_FLAGS, "-J", query]`, timeout 60; nonzero rc or any exception returns None; parse JSON; return `entries[0]` when the dict has a non-empty `entries`, else the dict.
8. `download`: missing binary raises `RuntimeError(MISSING)`; args `[str(binary), *BASE_FLAGS, "--no-progress", "--force-overwrites", "-f", "bestaudio[ext=m4a]", "-o", str(out_dir / f"{base_name}.%(ext)s"), "-J", "--no-simulate", query]`, timeout 900; nonzero rc: stderr containing `"Requested format is not available"` raises `RuntimeError(NO_M4A)`, else `RuntimeError(<last non-empty stderr line>)` or `RuntimeError(f"yt-dlp exited {rc}")`; else return the picked entry as in `probe`.
**New symbols:** those listed in Task 3 step 1, plus `SUMS`, `ASSETS`, `BASE_FLAGS`.
**Verify:** `uv run pytest -p no:cacheprovider tests/test_ytdlp.py -q` - expected: all pass.
**Forbidden:** no new deps; no `shell=True`; don't edit the tests (committed test wins: report a mismatch instead).
**Depends:** Task 3
**Why:** Decision 1 (spec:90-125). Callers answer `None`/raise `MISSING` when the binary is absent, matching today's `YoutubeDL is None` behaviour.

## Phase 2 - The pipeline and the worker

### Task 5: Rewrite the ingest tests for the staged, locked, LRCLIB-only pipeline [test]
- [ ] status
**Objective:** The ingest tests describe the new `run_download` (staging, lock, real `_apply`, no Genius) and fail red against today's code.
**Write-scope:**
`tests/test_ingest_run_download.py`
`tests/test_ingest_lyrics.py`
`tests/test_ingest_audio.py`
**Read-context:** Task 6's New symbols (below); `tests/test_ingest_run_download.py:1-100,255-300`; `tests/test_lyrics_sync.py:27-58` (`SYNCED_LRC`, `_candidate`); `tests/test_scanner.py:10-13` (`ft`); `muse/scanner.py:109-119`.
**Anchors:**
`def scan(` @ `muse/scanner.py`
`def lyrics_files(lyrics_root: Path, track_rowid: int) -> tuple[Path, Path]:` @ `muse/lyrics.py`
**Steps:**
1. Signatures under test (verbatim from Task 6): `def run_download(conn, *, payload: dict, metadata_provider: MetadataProvider, audio_source: AudioSource, music_root: Path, lyrics_root: Path, art_root: Path, art_provider, embed_backfill, under_lock=None) -> DownloadResult`; `def fetch_lyrics(track: dict) -> SyncOutcome`; `class YtDlpAudioSource: def __init__(self, binary: Path)`; `ALREADY_IN_DOWNLOADS = "Already in downloads"`.
2. `test_ingest_run_download.py`, every existing call and `_deps`: drop `genius_client`, add `lyrics_root=tmp_path / "lyrics"`; every `fetch_lyrics` monkeypatch lambda takes `(track)` only. Existing assertions stay.
3. New cases there (metadata `_metadata()`: Radiohead / OK Computer / Airbag / track_no 1, payload `{"track_rowid": 100}`):
   (a) the fake source's `out_dir` is `tmp_path / "MUSIC" / ".incoming"` and `base_name == "100"`; the row's `file_path == str((tmp_path / "MUSIC").resolve() / "Radiohead" / "OK Computer" / "01 - Airbag.m4a")`, that file exists, `.incoming` holds no files.
   (b) a pre-existing final file with bytes `b"keep"`: result `status == "error"`, `message == "Already in downloads"`, the file still reads `b"keep"`, the staged file is gone, the row `status == "error"`.
   (c) real lyrics: monkeypatch `muse.ingest.search_lrclib` to return `[_candidate(duration=284, syncedLyrics=SYNCED_LRC, trackName="Airbag", artistName="Radiohead", albumName="OK Computer")]`, leave `fetch_lyrics` real, patch `tag_m4a`; after a done result: both paths of `lyrics_files(tmp_path / "lyrics", 100)` exist, the row's `lyrics_path` equals the `.txt` path, `lyrics_state.state == "synced"` for rowid 100, and `list((tmp_path / "MUSIC").rglob("*.lyrics.txt")) == []`.
   (d) lock: `under_lock` fake sets `held = True` around `fn()` and counts calls; a fake source records `held` at `fetch` time; expect the recorded value False and calls `>= 2`.
   (e) scan between steps: a fake source that, after writing its staged file, runs `scan(conn, [tmp_path / "MUSIC"], lyrics_root=..., art_root=..., read_tags=lambda p: ft())`; after `run_download` is done run `scan` again with a `read_tags` that raises `AssertionError`; expect `SELECT COUNT(*) FROM tracks == 1` and the second scan's `unchanged == 1`.
   (f) a fake source that writes its staged file then raises `RuntimeError("boom")`: result error, `.incoming` holds no files.
4. `test_ingest_lyrics.py`: keep the LRCLIB-hit and all-miss cases calling `fetch_lyrics(track)`; delete the Genius fall-through case; add a case asserting `muse.ingest` has none of `build_genius_client`, `_genius_lyrics`, `GENIUS_TOKEN`.
5. `test_ingest_audio.py`: replace `FakeYdl`. Monkeypatch `muse.ytdlp.download` with a fake writing `out_dir / f"{base_name}.m4a"` and returning `{"id": "yt_abc123", "duration": 200.0}`, and `muse.ingest.remux_m4a` with a recorder. Cases: result path suffix `.m4a`, `source_site_id == "yt_abc123"`, `duration_s == 200.0`, the recorder saw that path once, the fake saw the query unchanged (URL and `ytsearch1:` forms); a fake that writes nothing makes `fetch` raise `RuntimeError` with `str == NO_M4A`. Keep the two `safe_component` tests.
**New symbols:** none (tests only)
**Verify:** `uv run pytest -p no:cacheprovider tests/test_ingest_run_download.py tests/test_ingest_lyrics.py tests/test_ingest_audio.py -q` - expected: failures with `TypeError` (unexpected `lyrics_root`/`under_lock` keyword, `fetch_lyrics()` arity, `YtDlpAudioSource()` argument); no collection error other than these.
**Forbidden:** no network; no edits outside the three files.
**Depends:** Task 4
**Why:** Decisions 3 and 6 (spec:160-204, 266-292) and the testing strategy (spec:504-512). Case (e) is the race Decision 3 exists to close; (c) closes memory note `ingest-apply-needs-lyrics-root-m5`.

### Task 6: Rewrite run_download: stage, remux, move under the lock, LRCLIB only
- [ ] status
**Objective:** `run_download` stages in `.incoming`, takes `under_lock` for every DB write and the final move, writes lyrics through the real `_apply`, and Genius is gone; Task 5's tests pass.
**Write-scope:**
`muse/ingest.py`
`muse/library/tracks.py`
`lyrics_quality.py`
`pyproject.toml`
`uv.lock`
**Read-context:** `muse/ingest.py:183-518`; `muse/library/tracks.py:261-282`; `muse/lyrics_lrclib.py:247-269`; `muse/library/art.py:84-117`; tests from Task 5.
**Anchors:**
`def _apply(` @ `muse/lyrics_lrclib.py`
`def record_cover(` @ `muse/library/art.py`
`def download(binary: Path, query: str, out_dir: Path, base_name: str, *, run=subprocess.run) -> dict` @ `muse/ytdlp.py` (from Task 4)
**Steps:**
1. Delete the `yt_dlp` import block, `lyricsgenius` block, `GENIUS_TOKEN`, `_genius_lyrics`, `build_genius_client`; `fetch_lyrics(track)` returns `decide_outcome(track, search_lrclib(...))` with today's arguments. Module docstring: drop yt-dlp/lyricsgenius extra wording; say real sources shell out to the yt-dlp binary (`muse/ytdlp.py`). Import `from . import ytdlp`, `from .remux import NO_M4A, remux_m4a`, `from .lyrics_lrclib import _apply`.
2. `YtDlpAudioSource.__init__(self, binary: Path)`; `fetch` makes `out_dir`, calls `ytdlp.download(self._binary, query, out_dir, base_name)`, expects `out_dir / f"{base_name}.m4a"` (missing raises `RuntimeError(NO_M4A)`), calls `remux_m4a(path)`, returns `AudioResult(path, str(info["id"]) if info.get("id") else None, float(d) if (d := info.get("duration")) else None)`.
3. `run_download` new signature (Task 5 step 1). `lock = under_lock or (lambda fn: fn())`. Step 1 (find_done, resolve, claim_track_id, album_rowid_for_release, write_pending) runs inside one `lock(...)` call; `metadata_provider.resolve` stays outside it (it may probe the network).
4. Staging: `incoming = music_root / ".incoming"`, `base_name = str(resolved_rowid)`. On fetch exception: delete `incoming.glob(f"{base_name}.*")`, `lock(mark_error)`, return error. Length mismatch: unchanged message, delete staged, `lock(mark_error)`. Tag: unchanged try/except.
5. Outside the lock: `outcome = fetch_lyrics(track_dict)` in try/except (None on failure); art `cover = art_provider.cover(album_ref)` and `cache_cover` in try/except as today.
6. `final = music_root.resolve() / safe_component(artist) / safe_component(album or "Singles") / f"{NN:02d} - {safe_component(title)}{staged.suffix}"`. One `lock(_land)` where `_land`: if `final.exists()`: unlink staged, `mark_error(ALREADY_IN_DOWNLOADS)`, return error result; else mkdir parents, `os.replace(staged, final)`, `st = final.stat()`, `mark_done(conn, rowid, file_path=str(final), lyrics_path=None, art_path=<art path when album_rowid is None else None>, source_site_id=..., file_size=st.st_size, file_mtime=int(st.st_mtime))`; then in its own try/except `_apply(conn, {**track_dict, "file_path": str(final)}, outcome, checked_at=int(time.time()), lyrics_root=lyrics_root)` when `outcome` is not None; then in its own try/except `record_cover(conn, album_rowid, art_path, source=cover.source, mbid=cover.mbid)` when a cover was cached and `album_rowid` is not None. Then `embed_backfill()` outside the lock, return done.
7. `muse/library/tracks.py` `mark_done`: add keyword args `file_size: int | None = None, file_mtime: int | None = None`, written with `COALESCE(?, file_size)` / `COALESCE(?, file_mtime)`.
8. `lyrics_quality.py`: delete `clean_genius`. `pyproject.toml`: delete the `ingest` extra and its comment block; `uv lock`.
**New symbols:** `ALREADY_IN_DOWNLOADS = "Already in downloads"`; signatures in Task 5 step 1; `mark_done(..., file_size: int | None = None, file_mtime: int | None = None)`.
**Verify:** `uv run pytest -p no:cacheprovider tests/test_ingest_run_download.py tests/test_ingest_lyrics.py tests/test_ingest_audio.py tests/test_library_tracks.py tests/test_lyrics_sync.py -q` - expected: all pass. Then `grep -rni genius muse/ingest.py` - expected: no output. (API tests fail to import `build_genius_client` until Task 8: expected.)
**Forbidden:** don't touch `muse/api.py`, `muse/jobs.py` or any test; no new deps; don't change `_length_mismatch` or `TieredMetadataProvider`.
**Depends:** Task 5
**Why:** Decision 3's ordering (spec:178-192) keeps the scanner from ever seeing a file without its row; Assumption 5 keeps scans from re-keying downloaded albums.

### Task 7: Add the ytdlp_install job and the per-drain hook to the jobs module
- [ ] status
**Objective:** `run_ytdlp_install` records a `ytdlp_install` job around `ytdlp.install`, and `run_download_worker` awaits an optional `before_drain()` once when it picks up work after being idle.
**Write-scope:**
`muse/jobs.py`
`tests/test_jobs_download.py`
**Read-context:** `muse/jobs.py:311-353` (`run_smart_install`); `muse/jobs.py:651-708`; `tests/test_jobs_download.py:1-183`.
**Anchors:**
`async def run_smart_install(` @ `muse/jobs.py`
`async def run_download_worker(` @ `muse/jobs.py`
`def install(data_dir: Path, base_url: str, *, session=None, platform: str = sys.platform) -> Path` @ `muse/ytdlp.py` (from Task 4)
**Steps:**
1. `async def run_ytdlp_install(conn, bus: JobBus, data_dir: Path, base_url: str, *, session=None) -> dict`: same shape as `run_smart_install` (create job of kind `ytdlp.YTDLP_INSTALL` with `{}` payload as its first statement, running "Getting the downloader", `asyncio.to_thread(ytdlp.install, data_dir, base_url, session=session)`, CancelledError -> cancelled "Server stopped" and re-raise, any other exception -> error with message `ytdlp.INSTALL_FAILED`, success -> done "Ready"); publish events; return the finished row.
2. `run_download_worker`: new keyword `before_drain=None` (an async callable). Keep a local `idle = True`; right after `job_id = await queue.get()`, if `idle and before_drain is not None`: `await before_drain()` inside try/except Exception that logs `"Download worker: before_drain failed"` and continues; set `idle = False`. After each job, `idle = queue.empty()`. Docstring: one sentence that this is where `yt-dlp -U` runs, once per drain (spec:109-113).
3. Tests in `tests/test_jobs_download.py`: (a) three jobs queued at once with a counting `before_drain` -> called once; (b) a job, drain, then another job -> called twice; (c) a raising `before_drain` still lets the job finish `done`; (d) `run_ytdlp_install` with `base_url=""` ends `error` with message `"Couldn't get the downloader"`; (e) with a fake session (same shape as Task 3 step 2, `platform` default is the host: build sums for `ytdlp.asset_name()`) ends `done` and `ytdlp.binary_path(tmp_path)` exists.
**New symbols:** `async def run_ytdlp_install(conn, bus: JobBus, data_dir: Path, base_url: str, *, session=None) -> dict`; `run_download_worker(..., before_drain=None)`.
**Verify:** `uv run pytest -p no:cacheprovider tests/test_jobs_download.py tests/test_ytdlp.py -q` - expected: all pass.
**Forbidden:** don't touch `muse/api.py`; don't change existing worker semantics (gap, jitter, content backfill).
**Depends:** Task 4
**Why:** Install is a job like every other long download (CLAUDE.md "Jobs"); `-U` once per drain is the spec's cadence (spec:109-113).

## Phase 3 - The API: wiring, lock, update check

### Task 8: Wire the API to the binary, the lock bridge and the new pipeline
- [ ] status
**Objective:** `create_app` drives downloads through `muse/ytdlp.py` and the new `run_download` (lyrics root, `under_lock`, `before_drain`), Genius is gone from the API, and the existing API tests pass again.
**Write-scope:**
`muse/api.py`
`muse/config.py`
`tests/test_api_jobs.py`
`tests/test_api_discover.py`
**Read-context:** `muse/api.py:40-60,366-415,475-760`; `muse/config.py:40-84`; `tests/test_api_jobs.py:87-130,216-300`; this plan's Assumptions 6 and 8.
**Anchors:**
`async def run_ytdlp_install(conn, bus: JobBus, data_dir: Path, base_url: str, *, session=None) -> dict` @ `muse/jobs.py` (from Task 7)
`def probe(binary: Path, query: str, *, run=subprocess.run) -> dict | None` @ `muse/ytdlp.py` (from Task 4)
`def update(binary: Path, *, run=subprocess.run) -> bool` @ `muse/ytdlp.py` (from Task 4)
**Steps:**
1. `config.py`: field `ytdlp_release_url: str = ""` with a comment (empty means never fetch; every hand-built Config, so tests stay offline); `from_env`: `ytdlp_release_url=os.environ.get("MUSE_YTDLP_URL", "https://github.com/yt-dlp/yt-dlp/releases/latest/download")`.
2. `api.py`: import `from . import ytdlp`, `run_ytdlp_install`, `shutil`; line 43 drops `build_genius_client`. Delete `_ytdlp_thumbnail_search` and `_ytdlp_probe_info` (api.py:371-414). Remove `create_app`'s `genius_client` parameter and its docstring mention.
3. Lifespan: `binary = ytdlp.binary_path(config.data_dir)`; `app.state.ytdlp_update_failed = False`; add `job_rows.abandon_running_of_kind(conn, ytdlp.YTDLP_INSTALL)` to `abandoned`; after the reap, `shutil.rmtree(config.music_root / ".incoming", ignore_errors=True)`.
4. `start_ytdlp_install()`: return when `job_rows.active(conn, ytdlp.YTDLP_INSTALL)`; else create a task of `run_ytdlp_install(conn, bus, config.data_dir, config.ytdlp_release_url, session=getattr(app.state, "ytdlp_session", None))` tracked in `install_tasks`; `app.state.start_ytdlp_install = start_ytdlp_install`.
5. `async def update_ytdlp()`: `app.state.ytdlp_update_failed = not await asyncio.to_thread(ytdlp.update, binary)`. `async def before_drain()`: binary missing -> `start_ytdlp_install()`, else `await update_ytdlp()`. At launch, when `settings.load(config.settings_path)["downloader_unlocked"]` and `binary.exists()`, create a tracked task of `update_ytdlp()` in `install_tasks`.
6. `under_lock(fn)` per Assumption 6, defined after `chain_lock`. Metadata tier `ytdlp_info=lambda q: ytdlp.probe(binary, q)`; thumbnail tier `thumbnail_url=lambda artist, album: (ytdlp.probe(binary, f"ytsearch1:{artist} - {album}") or {}).get("thumbnail")`; default audio source `YtDlpAudioSource(binary)`.
7. `_payload_to_kwargs`: drop `genius_client`; add `"lyrics_root": config.lyrics_root`, `"under_lock": under_lock`. Pass `before_drain=before_drain` to `run_download_worker`.
8. Tests: in both files drop every `genius_client=None` argument and make `fetch_lyrics` lambdas take `(track)`. Rewrite `test_post_jobs_url_kind_resolves_via_real_ytdlp_probe` to `monkeypatch.setattr("muse.ytdlp.probe", lambda binary, query, **kw: {"title": "Some Song", "uploader": "Some Artist", "duration": 210, "id": "abc123"})` and delete `FakeYdlProbe`; update its docstring to name `ytdlp.probe`.
**New symbols:** `Config.ytdlp_release_url: str = ""`; `app.state.start_ytdlp_install`, `app.state.ytdlp_update_failed`.
**Verify:** `uv run pytest -p no:cacheprovider tests/test_api_jobs.py tests/test_api_discover.py tests/test_api_catalog_browse.py tests/test_ingest_run_download.py tests/test_jobs_download.py tests/test_config.py -q` - expected: all pass. Then `grep -rni genius muse/` - expected: no output. Then `bash scripts/smoke.sh` - expected: `SMOKE-OK`.
**Forbidden:** no route changes (Task 10 owns routes); don't touch `muse/discover.py` or health; no new deps.
**Depends:** Task 6, Task 7
**Why:** Joins Phase 2's pieces. `under_lock` is the one bridge between the worker thread and `chain_lock` (Decision 3).

### Task 9: Write the lock, unlock and downloads-root API tests [test]
- [ ] status
**Objective:** Tests pin Decision 4's 404s, the unlock/lock routes, the downloads scan root and the settings view, and fail red; existing fixtures unlock so they keep passing after Task 10.
**Write-scope:**
`tests/test_api_downloader.py` (new)
`tests/conftest.py`
`tests/test_api_jobs.py`
`tests/test_api_discover.py`
`tests/test_api_catalog_browse.py`
`tests/test_scanner.py`
`tests/test_api_scan.py`
**Read-context:** Task 10's New symbols; `tests/conftest.py:133-141`; `tests/test_api_smart.py:110-145` (sleep(0) and `smart_session` injection); `tests/test_api_scan.py:1-30` (`read_tags` fake); `tests/test_api_discover.py:196-235`.
**Anchors:**
`def make_config(tmp_path, **overrides) -> Config:` @ `tests/conftest.py`
`def binary_path(data_dir: Path, platform: str = sys.platform) -> Path` @ `muse/ytdlp.py` (from Task 4)
**Steps:**
1. `conftest.py`: `def unlock(config: Config) -> None` (load settings, set `downloader_unlocked` True, save). Call it on the config before `create_app` in the `client` fixtures of `test_api_jobs.py`, `test_api_discover.py`, `test_api_catalog_browse.py`, in the url-kind test's own app, and in the standalone `create_app` of `test_mb_release_stamps_the_track_id_the_isrc_joins_to` (`tests/test_api_discover.py:860`). `tests/test_api_scan.py:26-27` pins the whole settings body: add `"downloads": None, "downloader": {"installed": False, "install_state": None, "update_failed": False}` to that expected dict (red until Task 10).
2. `test_api_downloader.py` (TestClient, `make_config(tmp_path)`, `art_provider` fake that returns None, stub encoders as in `test_api_jobs.py`). Cases, locked unless stated:
   (a) `POST /api/jobs` with `{"kind": "mb", ...MB-like payload}`, `{"kind": "mb_release", "release_group_mbid": "<uuid4>"}`, `{"kind": "url", "url": "https://youtube.com/watch?v=x", "artist_name": "A", "track_name": "T"}` each 404;
   (b) retry of an `error` download job (`job_rows.create` + update) 404;
   (c) `GET /api/discover/artists?q=x`, `/api/discover/artists/<uuid4>/releases`, `/api/discover/release-groups/<uuid4>/tracks` each 404; unlocked with `mb=` a fake whose `get` returns None, each is not 404;
   (d) `POST /api/settings/downloader {"password": "nope"}` 403, settings stay locked, no `ytdlp_install` job;
   (e) `{"password": "download-enjoy"}` 200, body `downloader_unlocked` True, settings file True, a `ytdlp_install` job row exists, `config.music_root` is a dir;
   (f) same with a file already at `binary_path(config.data_dir)`: no `ytdlp_install` job;
   (g) unlocked, two queued download jobs from `job_rows.create`; `DELETE /api/settings/downloader` 200, body `downloader_unlocked` False, both jobs `error` with message `"Downloader locked"`;
   (h) `GET /api/settings`: locked and empty `downloads` is None; unlocked equals `{"path": str(config.music_root), "songs": 0}`; locked with `music_root/A/x.mp3` present is not None; `downloader` has exactly keys `installed`, `install_state`, `update_failed`;
   (i) locked with `music_root/A/x.mp3` and `read_tags` fake: after the launch chain settles, a `tracks` row with that `file_path` exists; locked and empty `music_root/.incoming/y.mp3` only: no row;
   (j) `POST /api/downloader/install` locked 404; unlocked 202 with `job_id`;
   (k) `music_root/.incoming/stale.m4a` before startup is gone after entering the client;
   (l) unlocked install with `ytdlp_release_url=""` ends `error` "Couldn't get the downloader" (poll `/api/jobs/{id}` up to 5 s).
3. `test_scanner.py`: `holds_audio` cases: missing folder False; only `.incoming/a.mp3` False; `A/a.mp3` True; `A/notes.txt` False.
**New symbols:** `def unlock(config: Config) -> None` in `tests/conftest.py`.
**Verify:** `uv run pytest -p no:cacheprovider tests/test_api_downloader.py tests/test_scanner.py -q` - expected: failures (404/405 where 200/403 expected, `AttributeError: holds_audio`); `uv run pytest -p no:cacheprovider tests/test_api_scan.py -q` - expected: exactly one failure, the settings-body equality; then `uv run pytest -p no:cacheprovider tests/test_api_jobs.py tests/test_api_discover.py tests/test_api_catalog_browse.py -q` - expected: all pass.
**Forbidden:** no network; no edits to `muse/`.
**Depends:** Task 8
**Why:** Decisions 3-5 and 7 (spec:194-196, 219-235, 254-260, 302-307), testing strategy (spec:509-516).

### Task 10: Implement the unlock, lock, download guard and downloads root
- [ ] status
**Objective:** The download surface answers 404 while locked, the password unlocks it, Lock again cancels queued downloads, and `downloads/` is a scan and watch root when unlocked or holding audio; Task 9's tests pass.
**Write-scope:**
`muse/api.py`
`muse/settings.py`
`muse/scanner.py`
`muse/watch.py`
**Read-context:** tests from Task 9; `muse/api.py:597-686,788-833,892-911,1129-1190,1756-1960`; `muse/watch.py:13-60`; `muse/scanner.py:109-150`.
**Anchors:**
`def watch_folders(` @ `muse/watch.py`
`def music_folders(path: Path) -> list[Path]:` @ `muse/settings.py`
`def _settings_view() -> dict:` @ `muse/api.py`
**Steps:**
1. `settings.py`: `DOWNLOADER_PASSWORD_SHA256 = "70d35a1c658bc9a2a6b5adfc61b99adcb33aabd7d2f5ff283fc378bb8eb173f3"` with comment "Not access control (fork spec Decision 11): an easter egg's hash, public by design."; `def password_ok(password: str) -> bool` via `hmac.compare_digest(hashlib.sha256(password.encode()).hexdigest(), DOWNLOADER_PASSWORD_SHA256)`.
2. `scanner.py`: `def holds_audio(folder: Path) -> bool` walking like `scan` (skip dot dirs and dot files, `AUDIO_EXTENSIONS`), False when not a dir.
3. `watch.py`: pass `watch_filter=_filter` to `awatch`, where `_default = watchfiles.DefaultFilter()` and `_filter(change, path)` is `_default(change, path) and ".incoming" not in Path(path).parts`.
4. `api.py`: `def unlocked() -> bool` reads settings; `def require_unlocked() -> None` raises `HTTPException(404)`. Use `dependencies=[Depends(require_unlocked)]` on the three `/api/discover/*` routes; in `create_job` call it first when `payload.get("kind") in ("mb", "mb_release", "url")`; in `retry_job` call it after the 404 lookup when `job["kind"] == DOWNLOAD`.
5. `def download_roots() -> list[Path]`: `[config.music_root]` when `unlocked()` or `scanner.holds_audio(config.music_root)`, else `[]`. `run_chain` folders `+ download_roots()`; `start_watcher` passes `settings.music_folders(...) + download_roots()`.
6. `_settings_view` adds `"downloads"` (None when `download_roots()` is empty, else `{"path": str(config.music_root), "songs": <done, not removed, under that prefix, same SQL as folder_status>}`) and `"downloader": {"installed": binary.exists(), "install_state": <state of active or last ytdlp_install job, or None>, "update_failed": app.state.ytdlp_update_failed}`.
7. `class DownloaderUnlock(BaseModel): password: str = Field(max_length=200)`. `POST /api/settings/downloader`: wrong -> 403 "That's not it."; right -> set and save, `config.music_root.mkdir(parents=True, exist_ok=True)`, when the binary is missing `app.state.start_ytdlp_install()` then `await asyncio.sleep(0)`, `app.state.start_watcher()`, `app.state.spawn_chain()`, return `_settings_view()`. `DELETE /api/settings/downloader`: save locked, every `job_rows.queued_ids_of_kind(conn, DOWNLOAD)` -> `update(state=ERROR, message="Downloader locked")` and publish, `start_watcher()`, return the view. `POST /api/downloader/install` (status 202, `require_unlocked`): `start_ytdlp_install()`, `await asyncio.sleep(0)`, return `{"job_id": <last ytdlp_install id>}`.
**New symbols:** `DOWNLOADER_PASSWORD_SHA256`, `def password_ok(password: str) -> bool` (settings.py); `def holds_audio(folder: Path) -> bool` (scanner.py); routes above.
**Verify:** `uv run pytest -p no:cacheprovider tests/test_api_downloader.py tests/test_scanner.py tests/test_watch.py tests/test_settings.py tests/test_api_jobs.py tests/test_api_discover.py tests/test_api_catalog_browse.py tests/test_api_scan.py -q` - expected: all pass. Then `grep -c download-enjoy muse/settings.py muse/api.py` - expected: `0` for both.
**Forbidden:** no plaintext password in `muse/`; don't change `settings.DEFAULTS`; no Rust changes.
**Depends:** Task 9
**Why:** Decisions 3, 4, 5, 7. One `require_unlocked` keeps the guard in one place (spec:220-223).

### Task 11: Add the update check and remove catalog_freeze and the dead since path
- [ ] status
**Objective:** `GET /api/update` reports a newer GitHub release from a once-per-launch background check, and `catalog_freeze` plus `discover.release_groups`' `since` path are gone.
**Write-scope:**
`muse/update.py` (new)
`muse/config.py`
`muse/api.py`
`muse/discover.py`
`tests/test_update.py` (new)
`tests/test_config.py`
`tests/test_discover.py`
**Read-context:** `muse/discover.py:1-56,104-235`; `muse/api.py:913-950`; `muse/config.py:40-84`; `tests/test_discover.py:40-310`; `tests/test_config.py`.
**Anchors:**
`def release_groups(` @ `muse/discover.py`
`def health() -> dict:` @ `muse/api.py`
**Steps:**
1. `muse/update.py`: `RELEASES_URL = "https://api.github.com/repos/IanTheMitten/muse-desktop/releases/latest"`; `def app_version() -> str` = `os.environ.get("MUSE_APP_VERSION")` or `importlib.metadata.version("muse")`, falling back to `"0.0.0"` on `PackageNotFoundError`; `def check(current: str, *, fetch=None) -> dict`: `fetch(url) -> dict` defaults to `requests.get(url, timeout=10, headers={"Accept": "application/vnd.github+json"})` with `raise_for_status().json()`; any exception logs at info and returns `{"current": current, "latest": None}`; tag must match `^v(\d+(?:\.\d+)*)$`; when its integer tuple is greater than `current`'s, return `{"current": current, "latest": "<x.y.z>", "url": data["html_url"]}`, else `latest` None.
2. `config.py`: delete `catalog_freeze` field and env line; add `update_check: bool = False` (comment: hand-built Configs stay offline) and `from_env` `update_check=os.environ.get("MUSE_UPDATE_CHECK", "1") != "0"`.
3. `api.py`: `create_app(..., update_fetch=None)`; lifespan sets `app.state.update = {"current": update.app_version(), "latest": None}` and, when `config.update_check`, a tracked task `asyncio.to_thread(update.check, current, fetch=update_fetch)` storing its result; `GET /api/update` returns `app.state.update`. Health: delete `"catalog_freeze"` and the docstring paragraph about it.
4. `discover.py`: delete the `since` parameter, `RELEASE_GROUP_SEARCH`, the search branch, the date filter line and the docstring paragraphs about the freeze, the filtered path and the lexicographic rule; reword the module docstring's first paragraph and the "Nothing newer than the mirror." sentences to "This artist has no releases of that kind.". Keep browse paging, `MAX_BROWSE_PAGES`, the sort, None-vs-[].
5. Tests: `test_config.py` delete the two `catalog_freeze` tests, add one asserting `Config.from_env().update_check is True` and `MUSE_UPDATE_CHECK=0` gives False. `test_discover.py` delete every test calling `release_groups(..., since=...)`; add one: a group dated `"2019-05-01"` and one `"2025-11-01"` are both returned, newest first. `test_update.py`: newer tag (`v0.2.0` vs `0.1.0`) gives `latest "0.2.0"` and `url`; same tag gives None; `worker-v2` gives None; a raising fetch gives None; the route with `create_app(make_config(tmp_path, update_check=True), update_fetch=fake)` answers the fake's result after the task settles (poll 2 s).
**New symbols:** `muse/update.py`: `RELEASES_URL`, `def app_version() -> str`, `def check(current: str, *, fetch=None) -> dict`; `Config.update_check: bool = False`; `create_app(..., update_fetch=None)`; `GET /api/update`.
**Verify:** `uv run pytest -p no:cacheprovider tests/test_update.py tests/test_config.py tests/test_discover.py tests/test_api_discover.py tests/test_health_content.py tests/test_health_taste.py -q` - expected: all pass. Then `grep -rn "catalog_freeze\|CATALOG_FREEZE" muse tests` - expected: no output. Then `bash scripts/smoke.sh` - expected: `SMOKE-OK`.
**Forbidden:** no new deps; don't touch `web/` (Task 13 removes the TS field).
**Depends:** Task 10
**Why:** Decisions 8 and 11 (spec:311-329, 410-437). Assumption 10 keeps worker tags out of the check.

## Phase 4 - The UI and the shell

### Task 12: Write the unlock-chord and owned-chart tests [test]
- [ ] status
**Objective:** `web/src/unlock.test.ts` pins the shortcut matcher and the locked chart filter and fails red because `web/src/unlock.ts` does not exist.
**Write-scope:**
`web/src/unlock.test.ts` (new)
**Read-context:** `web/src/dropMessage.test.ts` (vitest style); Task 13's New symbols.
**Anchors:** `export type ChartSong = {` @ `web/src/api.ts`
**Steps:**
1. Signatures (verbatim from Task 13): `export function isUnlockChord(e: Pick<KeyboardEvent, "ctrlKey" | "shiftKey" | "altKey" | "code">): boolean` and `export function ownedChart<T extends { track: unknown }>(songs: T[]): T[]`, imported from `./unlock`.
2. Cases: chord true for `{ctrlKey, shiftKey, altKey: true, code: "KeyD"}`; false when each one of the three modifiers is false (three cases); false for `code: "KeyE"`; true when an extra `key: "∂"` property is present (macOS Option); `ownedChart` keeps only `track !== null` rows in order, returns `[]` for `[]`, and returns all rows when every row has a track.
**New symbols:** none (tests only)
**Verify:** `npm --prefix web ci` then `npm --prefix web test` - expected: one failed file, `unlock.test.ts`, failing to resolve `./unlock`; the other test files pass.
**Forbidden:** no other files.
**Depends:** none
**Why:** Spec vitest items (spec:522-524). `e.code` because Option turns `e.key` into `∂` (spec:252-253).

### Task 13: Gate Add in the app shell and filter the Home chart while locked [design]
- [ ] status
**Objective:** `/add` is mounted only when unlocked, the nav updates on unlock and lock without a reload, Home's chart shows only owned songs while locked, and `api.ts` knows the new endpoints.
**Write-scope:**
`web/src/unlock.ts` (new)
`web/src/useDownloader.ts` (new)
`web/src/api.ts`
`web/src/components/NavBar.tsx`
`web/src/App.tsx`
`web/src/views/Home.tsx`
**Read-context:** `web/src/unlock.test.ts` (from Task 12); `web/src/api.ts:1-20,128-175,797-806`; `web/src/components/NavBar.tsx`; `web/src/App.tsx:56-78`; `web/src/views/Home.tsx:34-82,140-180`; `web/src/useLoad.ts`.
**Anchors:**
`export const getSettings = (signal?: AbortSignal) =>` @ `web/src/api.ts`
`export function NavBar() {` @ `web/src/components/NavBar.tsx`
**Steps:**
1. `unlock.ts` (no imports): `isUnlockChord` returns `e.ctrlKey && e.shiftKey && e.altKey && e.code === "KeyD"`; `ownedChart` filters `s.track != null`.
2. `api.ts`: remove `catalog_freeze` and its comment from `Health`. `Settings` gains `downloads: { path: string; songs: number } | null` and `downloader: { installed: boolean; install_state: string | null; update_failed: boolean }`. Add `unlockDownloader(password: string): Promise<Settings>` (`send("POST", "/settings/downloader", { password })`), `lockDownloader(): Promise<Settings>` (`send("DELETE", ...)`), `installDownloader(): Promise<void>` (`send("POST", "/downloader/install")`), `type UpdateInfo = { current: string; latest: string | null; url?: string }`, `getUpdate = (signal?: AbortSignal) => get<UpdateInfo>("/update", signal)`.
3. `useDownloader.ts`: `export const SETTINGS_CHANGED = "muse:settings-changed"`; `export function settingsChanged(): void` dispatches that window event; `export function useDownloaderUnlocked(): boolean | null` loads `getSettings` on mount and on each event (null until the first answer, false on error).
4. `NavBar`: use the hook instead of its own `useLoad`. `App`: `const unlocked = useDownloaderUnlocked()` inside a small `AddRoute` component rendering `<Add />` when true, `null` when null, `<Navigate to="/" replace />` when false; the `/add` route uses it.
5. `Home`: `const unlocked = useDownloaderUnlocked()`; `const chart = unlocked ? all : ownedChart(all)` (null counts as locked); `chartTracks` derives from that list.
**New symbols:** `isUnlockChord`, `ownedChart` (unlock.ts); `SETTINGS_CHANGED`, `settingsChanged`, `useDownloaderUnlocked` (useDownloader.ts); `unlockDownloader`, `lockDownloader`, `installDownloader`, `UpdateInfo`, `getUpdate` (api.ts).
**Verify:** `npm --prefix web test` - expected: all pass. `npm --prefix web run build` - expected: exit 0. `node ~/.claude/skills/design-brain/scripts/preflight.mjs web/src/views/Home.tsx web/src/components/NavBar.tsx web/src/App.tsx` - expected: no errors.
**Forbidden:** no new deps; don't touch Settings or Add views; keep `PlayerProvider` above `BrowserRouter` (`web/src/App.tsx:36-40`).
**Depends:** Task 12
**Design:** home-chart-nav
**Why:** Decision 4 routing and chart (spec:224-228); one event keeps three readers of `downloader_unlocked` in step.

### Task 14: Build Settings Advanced, the password dialog, the Downloads row and the update notice [design]
- [ ] status
**Objective:** Settings shows the Advanced section (version, update link, External downloader with Lock again), the hidden chord opens the Administrator password dialog, and Music folders shows the fixed Downloads row.
**Write-scope:**
`web/src/views/Settings.tsx`
`web/src/styles/settings.css`
`web/src/shell.ts`
**Read-context:** `web/src/views/Settings.tsx:1-216,250-294`; `web/src/components/Dialog.tsx`; `web/src/components/EditDetailsDialog.tsx` (form-in-dialog pattern); `web/src/shell.ts`; `web/src/styles/settings.css`; `web/src/styles/dialogs.css`.
**Anchors:**
`export function Settings() {` @ `web/src/views/Settings.tsx`
`export function isUnlockChord(e: Pick<KeyboardEvent, "ctrlKey" | "shiftKey" | "altKey" | "code">): boolean` @ `web/src/unlock.ts` (from Task 13)
`export function settingsChanged(): void` @ `web/src/useDownloader.ts` (from Task 13)
**Steps:**
1. `shell.ts`: add `shell: { open(url: string): Promise<void> }` to the `Tauri` type.
2. `Settings`: a `window` `keydown` listener mounted with the screen opens the dialog when `isUnlockChord(e)` and not unlocked (`preventDefault`). Render `<Advanced data={data} setData={setData} />` after `<RemovedSongs />`.
3. `Advanced`: copy, classes and states exactly as `## Design: settings-downloader` CONTENT. Version row from `getUpdate()` (row hidden until it answers). Link click: `preventDefault` then `shell.shell.open(url)` when `shell` exists. Lock again: `setData(await lockDownloader())`, then `settingsChanged()`.
4. `UnlockDialog` (inside Settings.tsx, built on `Dialog`): submit calls `unlockDownloader(password)`; success `setData(result)`, `settingsChanged()`, close; `statusOf(error) === 403` shows "That's not it."; other errors "Couldn't unlock. Try again.". Clear the password on close.
5. `MusicFolders`: render the Downloads row first when `data.downloads` is not null; empty-state rule per CONTENT.
**New symbols:** `function Advanced(...)`, `function UnlockDialog(...)` (module-private).
**Verify:** `npm --prefix web test` - expected: all pass. `npm --prefix web run build` - expected: exit 0. `node ~/.claude/skills/design-brain/scripts/preflight.mjs web/src/views/Settings.tsx web/src/styles/settings.css` - expected: no errors.
**Forbidden:** no new deps; no shortcut hint anywhere in the UI; don't edit `Dialog.tsx`.
**Depends:** Task 13
**Design:** settings-downloader
**Why:** Decisions 3, 5, 7, 11 UI (spec:195-196, 249-260, 429-431).

### Task 15: Show the downloader status on Add and name the new failures [design]
- [ ] status
**Objective:** Add shows "Getting the downloader…", "Couldn't get the downloader." with Try again, or the update warning, and job cards explain the new failure messages.
**Write-scope:**
`web/src/views/Add.tsx`
`web/src/styles/add.css`
`web/src/components/JobProgress.tsx`
**Read-context:** `web/src/views/Add.tsx:108-140,617-748`; `web/src/components/JobProgress.tsx:30-50`; `web/src/styles/add.css:1-75`.
**Anchors:**
`function humanError(message: string | null): string {` @ `web/src/views/Add.tsx`
`export async function installDownloader(): Promise<void>` @ `web/src/api.ts` (from Task 13)
**Steps:**
1. `Add`: load `getSettings` on mount and on each `useJobs().job` event whose kind is `ytdlp_install`; render the status slot above the Activity strip per `## Design: add-downloader-status` CONTENT.
2. `humanError`: the four new rules first, copy verbatim from CONTENT.
3. `JobProgress`: `else if (job.kind === "ytdlp_install") label = "Getting the downloader";` beside the other kinds.
**New symbols:** none exported.
**Verify:** `npm --prefix web test` - expected: all pass. `npm --prefix web run build` - expected: exit 0. `node ~/.claude/skills/design-brain/scripts/preflight.mjs web/src/views/Add.tsx web/src/components/JobProgress.tsx` - expected: no errors.
**Forbidden:** no new deps; don't touch Settings.tsx or api.ts.
**Depends:** Task 13
**Design:** add-downloader-status
**Why:** Error handling section (spec:470-480).

### Task 16: Pass the app version and allow the release link in the shell, then smoke the frozen sidecar
- [ ] status
**Objective:** The shell sets `MUSE_APP_VERSION` for the sidecar and permits `shell:allow-open`; the sidecar freezes with `av` included and passes the shell smoke.
**Write-scope:**
`src-tauri/src/main.rs`
`src-tauri/capabilities/default.json`
**Read-context:** `src-tauri/src/main.rs:84-115`; `src-tauri/capabilities/default.json`; memory `m3-shell-build-gotchas` (in this plan's Memory section).
**Anchors:** `fn main() {` @ `src-tauri/src/main.rs`
**Steps:**
1. `main.rs`: after `env.insert("MUSE_TOKEN"...)`, add `env.insert("MUSE_APP_VERSION".to_string(), app.package_info().version.to_string());`.
2. `default.json`: add `"shell:allow-open"` to `permissions`.
3. Build and smoke in this order, serially: `npm --prefix web ci`, `npm --prefix web run build`, `uv run --group package python scripts/freeze_sidecar.py`, `uv run python scripts/shell_smoke.py --build --expect mp3,flac,wav`.
**New symbols:** none
**Verify:** `uv run python scripts/shell_smoke.py --build --expect mp3,flac,wav` - expected: final line `SHELL-SMOKE-OK`. Then `bash scripts/smoke.sh src-tauri/binaries/muse-core-x86_64-unknown-linux-gnu` - expected: `SMOKE-OK`.
**Forbidden:** no new crates or plugins; no bare `cargo`; don't commit `src-tauri/binaries/` or `build/`.
**Depends:** Task 1
**Why:** Decision 11 needs the version from the shell and the link out of the webview (spec:424-436); the smoke proves `av` freezes (Decision 2 consequence).

## Phase 5 - Browser e2e, docs and the release workflow

### Task 17: Add the Playwright harness, the e2e server and the scroll regression check
- [ ] status
**Objective:** `npm --prefix web run e2e` starts a fresh Muse on fixtures with a fake chart and runs a Chromium spec proving every main view scrolls; CI runs it on PRs and pushes to main.
**Write-scope:**
`web/package.json`
`web/package-lock.json`
`web/playwright.config.ts` (new)
`web/e2e/scroll.spec.ts` (new)
`web/e2e/app.ts` (new)
`scripts/e2e_server.py` (new)
`.github/workflows/e2e.yml` (new)
`.gitignore`
**Read-context:** `muse/__main__.py`; `muse/charts.py:14-40`; `web/src/token.ts:3-23`; `tests/fixtures/smoke/`; `.github/workflows/package.yml:20-30`; this plan's Assumptions 8, 12, 13.
**Anchors:** `def chart_url(base_url: str, storefront: str, n: int = 10) -> str:` @ `muse/charts.py`
**Steps:**
1. `npm --prefix web install --save-dev --save-exact @playwright/test@1.64.1`; script `"e2e": "playwright test"`. `.gitignore`: `web/test-results/`, `web/playwright-report/`.
2. `scripts/e2e_server.py`: temp dir; music `<tmp>/music/<Artist>/<Album>/<NN> - <Title>.<ext>` for 12 tracks (3 artists x 2 albums x 2 tracks) copied from `tests/fixtures/smoke/tone.{mp3,flac,wav}` in rotation and tagged with mutagen (title, artist, album, tracknumber; wav via `mutagen.wave.WAVE` `add_tags` and ID3 `TIT2/TPE1/TALB/TRCK`); first track is "First Light" by "Ada Arc". `data/settings.json` `{"music_folders": [<music>], "music_files": [], "downloader_unlocked": false}`. A `ThreadingHTTPServer` on 127.0.0.1:8766 answers `/api/v2/us/music/most-played/10/songs.json` with `{"feed": {"results": [{"name": "First Light", "artistName": "Ada Arc", "url": "https://music.apple.com/x"}, {"name": "Not Owned Song", "artistName": "Nobody", "url": "https://music.apple.com/y"}]}}` and 404 elsewhere. Env: `MUSE_DATA_DIR`, `MUSE_PORT=8765`, `MUSE_TOKEN=e2e-token`, `MUSE_CHART_URL=http://127.0.0.1:8766`, `MUSE_YTDLP_URL=http://127.0.0.1:8766/ytdlp`, `MUSE_ART_BACKFILL=0`, `MUSE_LYRICS_SYNC=0`, `MUSE_TASTE_RETRAIN=0`, `MUSE_UPDATE_CHECK=0`, `MUSE_WEB_DIST=<repo>/web/dist`; then `from muse.__main__ import main; main()`.
3. `playwright.config.ts`: `testDir: "e2e"`, `workers: 1`, `fullyParallel: false`, `retries: 0`, chromium project only, `use.baseURL` `http://127.0.0.1:8765`, `webServer: { command: "uv run python ../scripts/e2e_server.py", url: "http://127.0.0.1:8765/api/health?t=e2e-token", timeout: 120_000, reuseExistingServer: false }`.
4. `e2e/app.ts`: `export async function open(page, path = "/")` = `page.goto(path + "?t=e2e-token")` then wait for the `nav` named "Sections".
5. `scroll.spec.ts`, viewport 1200x420: for `/library` and `/settings` assert `document.documentElement.scrollHeight > innerHeight`; for `/`, `/library`, `/settings`, `/muse`, when it overflows, `mouse.wheel(0, 600)` then `window.scrollY > 0`. Then on `/library` play the first song, open Now playing (control bar button "Open now playing: …"), close it ("Close now playing"), and assert the page still scrolls.
6. `.github/workflows/e2e.yml`: `on: pull_request` and `push: branches: [main]`; `ubuntu-latest`; checkout v7, `astral-sh/setup-uv@v10.3.3`, setup-node v7 (22); `uv sync`; `npm --prefix web ci`; `npm --prefix web run build`; `npm --prefix web exec -- playwright install --with-deps chromium`; `npm --prefix web run e2e`; on failure upload `web/playwright-report`.
**New symbols:** none
**Verify:** `npm --prefix web run build` then `npm --prefix web exec -- playwright install chromium` then `npm --prefix web run e2e` - expected: scroll spec passes. Then `npm --prefix web test` - expected: vitest unchanged and green (e2e not collected by vitest).
**Forbidden:** no other new deps; don't add e2e to `npm test`; no `--with-deps` locally (needs sudo).
**Depends:** Task 13
**Why:** Decision 10 (spec:369-408); the scroll lock shipped because no UI ran in a browser.

### Task 18: Write the e2e flow specs
- [ ] status
**Objective:** Chromium specs cover play/next/queue, edit details then reload, remove and restore, lyrics with tap-along keys, the Settings sections, and the unlock flow including the locked chart.
**Write-scope:**
`web/e2e/flows.spec.ts` (new)
`web/e2e/unlock.spec.ts` (new)
**Read-context:** `web/e2e/app.ts` (from Task 17); `scripts/e2e_server.py` (from Task 17); `web/src/components/TrackMenu.tsx:8-16,74-94`; `web/src/components/ControlBar.tsx:41-120`; `web/src/player/NowPlayingSheet.tsx:150-320`; `web/src/views/TapAlong.tsx:110-140`; `web/src/components/EditDetailsDialog.tsx`; `web/src/components/RemoveDialog.tsx`; `muse/api.py:249-251` (`LyricsWrite`).
**Anchors:** `export async function open(` @ `web/e2e/app.ts` (from Task 17)
**Steps:**
1. `flows.spec.ts` (`test.describe.serial`): (a) Library: play the first song; the player shows its title; "Next" changes it; Now playing > "Up next" lists the remaining songs. (b) "More for <title>" > "Edit details", title "E2E Renamed", save, `page.reload()`, Library shows "E2E Renamed". (c) "Remove from library…" on another song, confirm; it leaves Library; Settings > Removed songs > Restore; it is back. (d) `request.put("/api/tracks/<rowid>/lyrics?t=e2e-token", { data: { text: <5 lines> } })` for the playing song (rowid from `/api/library/songs?t=e2e-token`); open Now playing > "Lyrics" > "Tap along"; press Space; "Line 2 of 5" visible; Backspace gives "Line 1 of 5"; Escape leaves tap-along and the "Now playing" dialog is still open. (e) Settings shows headings "Music folders", "Playback", "Duplicates", "Hidden artists", "Removed songs", "Advanced".
2. `unlock.spec.ts` (`test.describe.serial`, runs after flows by file order): locked Home "Top songs right now" shows "First Light" and not "Not Owned Song"; nav has no "Add"; `/add` lands on `/`. Settings: "Plugins are under development."; `keyboard.press("Control+Shift+Alt+KeyD")` opens "Administrator password"; "nope" shows "That's not it."; "download-enjoy" closes it; nav shows "Add" without reload; Add opens and shows "Couldn't get the downloader." (the fake URL 404s); Home now lists "Not Owned Song". Settings "Lock again": "Add" leaves the nav.
**New symbols:** none
**Verify:** `npm --prefix web run e2e` - expected: scroll, flows and unlock specs all pass.
**Forbidden:** no edits outside the two spec files; a real UI bug found here is reported as `- [!]`, never patched in a spec.
**Depends:** Task 17, Task 14, Task 15
**Why:** Decision 10's flow list (spec:392-400).

### Task 19: Document the alpha: README, release notes, CLAUDE.md [docs]
- [ ] status
**Objective:** README tells a stranger how to install the unsigned alpha and what the licences mean, release notes for v0.1.0 exist, and CLAUDE.md matches M5.
**Write-scope:**
`README.md`
`docs/release-notes/v0.1.0.md` (new)
`CLAUDE.md`
**Read-context:** `README.md`; `CLAUDE.md`; `web/src/views/Settings.tsx:280-288` (licence copy); spec:355-361.
**Anchors:** none
**Steps:**
1. README: "Install" section: download from GitHub Releases; macOS right-click the app > Open on first launch (unsigned); Windows SmartScreen "More info" > "Run anyway". "Licences" section: Muse code licence as in the repo; smart features download models that are free for non-commercial use only: MERT (CC-BY-NC-4.0), audeering emotion model (CC-BY-NC-SA-4.0), MMS-FA aligner (CC-BY-NC-4.0), schufo trained on MUSDB18 (`other-nc`); a sentence that Muse is free and for non-commercial use. Never mention the unlock, Add, yt-dlp or a password.
2. `docs/release-notes/v0.1.0.md`: "Muse 0.1.0 (alpha)": what it does (plays your folders, recommends, lyrics with auto-align, cover art), unsigned install steps (same as README), Intel Macs get no smart features, known gaps (owner checks 4 and 10 from this plan). No mention of the unlock.
3. CLAUDE.md: replace the `ingest` extra sentence with: downloads use the yt-dlp standalone binary fetched into `<data dir>/bin` after unlock (`muse/ytdlp.py`), PyAV is core for the m4a remux (`muse/remux.py`), Genius is gone; add `npm --prefix web run e2e` (Playwright, CI) to Frontend commands; mention `downloads/` as a scan root when unlocked or holding audio and `.incoming` staging under `chain_lock`.
**New symbols:** none
**Verify:** `grep -ci "unlock\|yt-dlp\|password" README.md docs/release-notes/v0.1.0.md` - expected: `0` for both files. `grep -c "genius" CLAUDE.md` - expected: `0`.
**Forbidden:** don't touch code; no claims not in the repo or spec.
**Depends:** none
**Why:** Decision 9 step 4 (spec:355-361).

### Task 20: Extend the packaging workflow to publish worker-v* and v* releases
- [ ] status
**Objective:** A `worker-v*` tag builds only the worker legs and publishes a non-latest release with both zips; a `v*` tag builds only the app legs and publishes the installers with the checked-in notes.
**Write-scope:**
`.github/workflows/package.yml`
**Read-context:** `.github/workflows/package.yml`; this plan's Assumption 10.
**Anchors:** none
**Steps:**
1. `on.push.tags`: `["v*", "worker-v*", "ci-package-*"]`.
2. `package` job: `if: ${{ !startsWith(github.ref, 'refs/tags/worker-v') }}`. `worker` job: `if: ${{ !startsWith(github.ref, 'refs/tags/v') }}`.
3. Job `release-worker`: `needs: worker`, `if: ${{ startsWith(github.ref, 'refs/tags/worker-v') }}`, `runs-on: ubuntu-latest`, `permissions: contents: write`; `actions/download-artifact@v7` with `pattern: worker-*`, `merge-multiple: true`, `path: dist`; `gh release create "$GITHUB_REF_NAME" dist/*.zip --latest=false --title "Smart features worker $GITHUB_REF_NAME" --notes "Worker builds for Muse smart features."` with `GH_TOKEN: ${{ github.token }}` and `GH_REPO: ${{ github.repository }}`.
4. Job `release-app`: `needs: package`, `if: ${{ startsWith(github.ref, 'refs/tags/v') }}`, same runner and permissions; download `pattern: bundle-*` merged into `dist`; `gh release create "$GITHUB_REF_NAME" dist/*.dmg dist/*.exe --title "Muse ${GITHUB_REF_NAME#v}" --notes-file "docs/release-notes/$GITHUB_REF_NAME.md"` after `actions/checkout@v7`.
5. Update the header comment to name the two release flows.
**New symbols:** none
**Verify:** `uv run --with pyyaml==6.0.3 python -c "import yaml; d = yaml.safe_load(open('.github/workflows/package.yml')); print(sorted(d['jobs']))"` - expected: `['package', 'release-app', 'release-worker', 'worker']`.
**Forbidden:** don't change build or smoke steps; no new actions beyond `actions/download-artifact@v7`; no tag pushes.
**Depends:** none
**Why:** Decision 9 steps 2-3 (spec:344-354).

## Phase 6 - The v0.1 alpha (owner-gated, billed)

### Task 21: Publish worker-v1, fill the manifest, publish v0.1.0 [mechanical]
- [ ] status
**Objective:** GitHub releases `worker-v1` and `v0.1.0` exist with their assets, and `muse/smart_manifest.json` on `main` names both worker zips.
**Write-scope:**
`muse/smart_manifest.json`
**Read-context:** this plan's Owner checks (before Task 21); `scripts/manifest_add_worker.py`; `.github/workflows/package.yml` (from Task 20).
**Anchors:** none
**Steps:**
1. Stop unless the owner has said yes in this session to pushing `worker-v1` and confirmed prerequisites (a)-(c). If not, report `- [!]` "needs owner yes for billed tag worker-v1".
2. `git fetch origin main`, `git tag worker-v1 origin/main`, `git push origin worker-v1`; `gh run watch` the `package` run; expect `worker` legs and `release-worker` green.
3. `gh release download worker-v1 -D build/worker-v1`; for each of `aarch64-apple-darwin` and `x86_64-pc-windows-msvc`: `uv run python scripts/manifest_add_worker.py <triple> build/worker-v1/muse-worker-<triple>.zip https://github.com/IanTheMitten/muse-desktop/releases/download/worker-v1/muse-worker-<triple>.zip`.
4. `uv run pytest -p no:cacheprovider tests/test_smart_install.py -q` green; branch `m5-worker-manifest` from `origin/main`, commit the manifest, push, `gh pr create --draft`; stop for the owner to merge.
5. After the owner merges and says yes to `v0.1.0`: `git fetch origin main`, `git tag v0.1.0 origin/main`, `git push origin v0.1.0`; `gh run watch`; expect `package` legs and `release-app` green; `gh release view v0.1.0` lists two `.dmg` and one `.exe`.
6. Delete `build/worker-v1/`.
**New symbols:** none
**Verify:** `gh release view worker-v1 --json assets --jq '.assets[].name'` - expected: two `muse-worker-*.zip`. `gh release view v0.1.0 --json assets --jq '.assets[].name'` - expected: two `.dmg`, one `.exe`.
**Forbidden:** never push a tag without the owner's yes in this session; never force-push or retag; don't edit the workflow here.
**Depends:** Task 16, Task 18, Task 19, Task 20
**Why:** Decision 9 order (spec:340-367); each tag spends billed macOS minutes.
