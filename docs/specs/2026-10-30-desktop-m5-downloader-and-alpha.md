# Technical spec - Muse desktop M5: the downloader unlock, Add, and the v0.1 alpha

> Status: ready for planning. Feed to /autopilot-plan once ready - it verifies this line and refuses a spec that isn't "ready for planning".

## Source

No PRD. This spec extends `docs/specs/2026-10-29-desktop-app-fork.md` (the fork
spec), whose Decision 11 already settled the unlock flow, the password and an
app-managed yt-dlp. Its milestone line (`docs/plans/2026-10-29-desktop-m1-core.md:23`)
is "M5: the downloader unlock and Add". The owner asked on 2026-10-30 for M5
"including all the open decisions from past milestones".

Items carried in from earlier plans and memory:

- `muse/ingest.py:482-484` calls `lyrics_lrclib._apply` without the required
  `lyrics_root` inside `try/except`, so a downloaded track silently gets no
  lyrics (M2 plan line 11).
- M1 moved to M5: Genius, the discover post-freeze filter, `Config.catalog_freeze`,
  `muse/ingest.py` writing lyrics beside the audio, and scanning `downloads/`.
- M1 decided ffmpeg is needed because `muse/ingest.py:213` uses
  `FFmpegExtractAudio`, and assumed M5 would fetch a static ffmpeg. Decision 2
  below replaces that.
- The public alpha is not cut: the repo is private, release `worker-v1` does
  not exist, Laya v4 is not on Hugging Face, and app bundles exist only as CI
  artifacts (M4a owner checks 2-3, M4b owner check 1).
- About 17 owner hand checks from M3, M4a and M4b are open, and no UI built
  since M2 has run in a browser. The scroll lock bug slipped through that way.
- macOS media keys and Now Playing are unverified (M3 owner check 6).
- Auto-update was never decided. The fork spec put only its tests out of v1.

Facts found while gathering, which shaped the decisions:

- `muse/ingest.py:192` imports the `yt_dlp` Python module. The frozen sidecar
  has no Python interpreter that could update a module in place.
- yt-dlp release 2026.10.26 publishes `yt-dlp_macos` (universal),
  `yt-dlp.exe`, `yt-dlp_linux` and `SHA2-256SUMS`.
- yt-dlp's FFmpeg-Builds have no macOS build. The macOS static ffmpeg sources
  (evermeet.cx, osxexperts.net) have no stable URL that a hash can pin.
- Without ffmpeg, yt-dlp leaves YouTube's itag 140 (AAC) in a DASH m4a
  container and skips its fixup step. `-x` audio extraction fails outright.
- `run_download_worker` (`muse/jobs.py:651`) runs outside `chain_lock`
  (`muse/api.py:597`). If `downloads/` is watched, the scanner can import a
  half-written file as a second row.
- `muse/discover.py` drops every release dated on or before 2025-11-13. In the
  fork, which has no mirror, Add would show only new releases.
- While locked, the `/add` route is always mounted (`web/src/App.tsx:66`).
  Home's "Top songs right now" shelf links songs the user doesn't own to `/add`
  (`web/src/views/Home.tsx:165`). `POST /api/jobs` download kinds and
  `/api/discover/*` answer as well.
- PyAV `av==19.0.2` is pinned in the `embed` extra, and the sidecar freeze
  excludes it (`scripts/freeze_sidecar.py:33`). `tests/test_audio_decode.py:55`
  asserts that `av` is not imported.

## Architecture

The fork spec's three processes stay: Tauri shell, core sidecar and optional
ML worker. M5 changes only the core sidecar and the UI. It adds two
app-managed things:

- **`<data dir>/bin/yt-dlp` (`yt-dlp.exe` on Windows).** It is fetched after
  unlock and run as a subprocess. Nothing imports `yt_dlp`.
- **`<data dir>/downloads/`.** This is Add's output and a scan root. Downloads
  are staged in `downloads/.incoming/` and moved into
  `downloads/<Artist>/<Album>/` under `chain_lock`.

Sidecar modules:

- **`muse/ytdlp.py` (new).** It handles the binary: install (fetch, then check
  against `SHA2-256SUMS`), update (`-U`), the download call and the
  metadata/thumbnail probe calls, all through `subprocess`. Windows uses
  `CREATE_NO_WINDOW`.
- **`muse/ingest.py`.** The `AudioSource` protocol is unchanged.
  `YtDlpAudioSource` shells out through `muse/ytdlp.py`, then remuxes the
  DASH m4a with PyAV (lazy import), then tags with mutagen. Genius is
  removed. The lyrics step is LRCLIB only and writes into `lyrics_root`.
- **`muse/discover.py`.** The freeze filter is removed.
- **`muse/api.py`.** It gets a `require_unlocked` check on the download
  surface, unlock and lock routes, `downloads/` as a conditional scan root,
  and download DB writes under `chain_lock`.
- **Update check.** One keyless GitHub Releases call per launch, shown in
  Settings.

New outside the app:

- A Playwright smoke suite (`web/e2e/`), run on a Linux CI runner.
- A tag-triggered release workflow that publishes v0.1.0.

## Decisions

### Decision: 1. yt-dlp runs as the official standalone binary
- **Status:** Accepted
- **Context:** The fork spec's Decision 11 requires yt-dlp to be fetched only
  after unlock and kept up to date. Today `muse/ingest.py:192` imports the
  `yt_dlp` module. A frozen module can only change with an app release, and a
  frozen module would put a downloader inside every copy, locked or not.
- **Options considered:** these, each with its trade-off:
  - (a) Standalone binary. It is fetched after unlock, checked against the
    release's `SHA2-256SUMS`, run as a subprocess, and updated with `-U`.
  - (b) Freeze the `yt_dlp` module into the sidecar. This is the smallest
    diff, but it can't self-update and every install carries it.
- **Decision:** (a), picked by the owner.
  - **Assets:** `yt-dlp_macos` (universal, so it covers Intel Macs) and
    `yt-dlp.exe`, plus `yt-dlp_linux` for dev only.
  - **Download:** from `https://github.com/yt-dlp/yt-dlp/releases/latest/download/<asset>`,
    together with `SHA2-256SUMS` from the same release. A hash mismatch
    deletes the file.
  - **Install:** the binary is saved as `bin/yt-dlp(.exe)` with mode `0o755`.
    This runs as a `ytdlp_install` job.
  - **Updates:** `yt-dlp -U` runs in the background once at launch when
    unlocked, and once at the start of each download-queue drain. The fork
    spec said "before each download job", but a 12-track `mb_release` would
    then make 12 update calls. One per drain keeps the binary current
    without hammering GitHub.
  - **Callers:** the download call, `_ytdlp_probe_info` and
    `_ytdlp_thumbnail_search` (`muse/api.py:371-410`) all go through
    `muse/ytdlp.py`. When the binary is missing they answer `None`, as they
    do today when `YoutubeDL is None`.
- **Consequences:** what this changes:
  - `yt-dlp` and `lyricsgenius` leave `pyproject.toml`, and the `ingest`
    extra loses them.
  - The binary is unsigned. Files fetched with `requests` get no macOS
    quarantine attribute, so Gatekeeper does not prompt. The release smoke
    must still prove this on a Mac.
  - The exact CLI flags and JSON fields are pinned at plan time against
    2026.10.26 (see Open questions).

### Decision: 2. No ffmpeg; PyAV remuxes YouTube's m4a stream
- **Status:** Accepted
- **Context:** the forces at play:
  - `FFmpegExtractAudio` needs ffmpeg.
  - No static ffmpeg for macOS arm64 has a stable, hash-pinnable URL. The
    fork spec's Decision 4 rule is that downloaded executables are
    hash-verified.
  - Without ffmpeg, yt-dlp leaves a DASH m4a that WebKit may not play and
    mutagen may not tag.
  - PyAV is already pinned and bundles FFmpeg's LGPL libraries.
- **Options considered:** these, each with its trade-off:
  - (a) `-f bestaudio[ext=m4a]` with no postprocessor, then a PyAV
    stream-copy remux into a standard m4a.
  - (b) Fetch a static ffmpeg after unlock (the M1 assumption). Any format
    works, but the macOS bytes can't be verified.
  - (c) Keep the raw DASH m4a. Playback and tagging are unverified.
- **Decision:** (a), picked by the owner.
  - yt-dlp writes `<base>.m4a` into `.incoming/`.
  - `muse/ingest.py` opens it with `av` and copies the audio stream, with no
    re-encode, into `<base>.remux.m4a`. It replaces the original with that
    file, then tags it with mutagen (`tag_m4a`, unchanged).
  - A video with no m4a audio stream fails its job with "No m4a audio for
    this video. Try another link."
- **Consequences:** what this changes:
  - `av==19.0.2` moves from the `embed` extra into core dependencies, and
    `scripts/freeze_sidecar.py` stops excluding it. That adds about
    30-40 MB to the sidecar.
  - `av` is imported only inside the remux function, so
    `tests/test_audio_decode.py:55` and the fast-startup property hold.
  - `muse/ingest.py`'s `-x` path and its `preferredcodec` options go away.
  - Some uploads that exist only as Opus/webm can't be added in v1 (see
    Open questions).

### Decision: 3. Downloads are staged, then moved into the library under chain_lock
- **Status:** Accepted
- **Context:** the forces at play:
  - The fork spec scans `downloads/` like a music folder.
  - The watcher fires on file events, and the scanner inserts any audio
    path that has no row.
  - The download worker writes to the shared connection outside
    `chain_lock`. The M1 plan's rule is that the connection can't hold two
    transactions, because `record_cover` uses `BEGIN`/`COMMIT`.
  - The chain can hold the lock for hours during a first-install embed
    backfill.
- **Options considered:** these, each with its trade-off:
  - (a) Stage in a dot-dir, and take the lock only for DB writes and the
    final move.
  - (b) Run the whole download under the lock. This is simple, but downloads
    stall behind long chains and scans stall behind yt-dlp.
  - (c) Never scan `downloads/`. There is no race, but downloaded tracks lose
    missing-file detection and rescans.
- **Decision:** (a), picked by the owner. Each download runs in this order:
  1. `write_pending`, under the lock.
  2. yt-dlp into `downloads/.incoming/`. No lock.
  3. The length check, then the remux, then the tag. No lock.
  4. The LRCLIB fetch and the art provider fetch. No lock, network only.
  5. Under the lock:
     - `os.replace` into `downloads/<safe Artist>/<safe Album or Singles>/<NN - Title>.m4a`.
       A file already there fails the job with "Already in downloads", and
       the staged file is deleted.
     - `mark_done` with the final path.
     - The lyrics `_apply` and the art `record_cover`.

  The scanner skips any directory whose name starts with `.` (the plan
  verifies or adds this). At startup, `.incoming/` is emptied after stale
  jobs are reaped.

  `downloads/` is a scan root whenever the downloader is unlocked or the
  folder holds audio files. Settings > Music folders shows it as a fixed
  line, "Downloads (kept by Muse)", which can't be removed.
- **Consequences:** what this changes:
  - Because a scan holds the same lock, it never sees a file without its row.
  - The embed backfill callback in `_payload_to_kwargs` still runs after
    `mark_done`, outside the lock, as today.
  - Dropping the lock between steps means a download's first write and its
    final move can sit on either side of a chain run. That is harmless:
    the pending row has no `file_path`, so the scanner never matches it.

### Decision: 4. A locked app hides Add everywhere and the server refuses the download surface
- **Status:** Accepted
- **Context:** the forces at play:
  - The fork spec's Decision 11 makes the unlock an easter egg that keeps
    casual users from finding a downloader.
  - Today a locked app still routes `/add`.
  - Home's chart shelf links songs the user doesn't own to `/add`.
  - The API accepts `mb`, `mb_release` and `url` jobs and serves the
    MusicBrainz browse.
- **Options considered:** these, each with its trade-off:
  - (a) Hide the UI, and have the server answer 404 on the download surface.
    The chart stays, showing only songs the user owns.
  - (b) As (a), and also skip the Apple chart fetch while locked.
  - (c) Hide the UI only.
- **Decision:** (a), picked by the owner. When the downloader is locked:
  - **Server:** `POST /api/jobs` with kind `mb`, `mb_release` or `url`,
    `POST /api/jobs/{id}/retry` on a `download` job, and every
    `/api/discover/*` route answer 404, through one `require_unlocked`
    check that reads `settings.json`.
  - **Routing:** `/add` is mounted only when unlocked, and otherwise
    redirects to `/`.
  - **Home chart shelf:** it renders only rows with a `track` (songs the
    user owns), with no Add links. The keyless Apple chart fetch
    (`muse/charts.py`, cached 6 h) stays, because it feeds Home.
- **Consequences:** what this changes:
  - A locked install makes no MusicBrainz browse calls and holds no
    downloader.
  - Queued `download` jobs left from an unlocked session are cancelled on
    lock (Decision 7).
  - Existing `/api/jobs` tests that queue downloads must unlock first through
    `make_config`/settings.

### Decision: 5. Unlock mechanics
- **Status:** Assumed - not confirmed
- **Context:** The fork spec's Decision 11 fixed the screen, the shortcut, the
  password and the persistence. It also said the Tauri shell handles the
  shortcut's key events. That is unnecessary: the shortcut only matters while
  the Settings screen has focus, inside the webview. This decision is cheap to
  reverse, and the owner did not review it.
- **Options considered:** these, each with its trade-off:
  - (a) A webview `keydown` listener on the Advanced screen.
  - (b) A Tauri global shortcut, as the fork spec wrote. That fires outside
    the app and needs a plugin and a capability.
- **Decision:** (a).
  - **Advanced section:** a new Settings > Advanced section has an "External
    downloader" row that reads "Plugins are under development."
  - **Shortcut:** while Settings is mounted, `ctrlKey && shiftKey &&
    altKey && e.code === "KeyD"` opens the "Administrator password" dialog.
    It uses `e.code`, because Option changes `e.key` to `∂` on macOS.
  - **Unlock route:** `POST /api/settings/downloader` `{"password": str}`.
    The server compares the hex sha256 of the password with a constant in
    `muse/settings.py` using `hmac.compare_digest`.
    - A wrong password gets 403 and the dialog shows "That's not it."
    - The right one writes `downloader_unlocked: true` and queues
      `ytdlp_install`.
  - **Lock route:** `DELETE /api/settings/downloader`, which is "Lock again".
- **Consequences:** The fork spec's architecture line about the shell
  handling the shortcut no longer applies, and no Rust changes are needed.
  The hash sits in a public repo. That is fine, because the fork spec's
  Decision 11 already says this is not access control.

### Decision: 6. Downloaded tracks get LRCLIB lyrics inline, into the data dir; Genius is removed
- **Status:** Accepted
- **Context:** the forces at play:
  - The fork spec drops Genius (it needs a personal token, which breaks the
    no-personal-keys rule).
  - The fork spec's Decision 8 puts every lyric in the data dir.
  - `muse/ingest.py:482-487` calls `_apply` without `lyrics_root` and still
    computes a `.lyrics.txt` path beside the audio.
- **Options considered:** these, each with its trade-off:
  - (a) Fix the inline LRCLIB step.
  - (b) Delete the step and let the watcher-triggered chain's LRCLIB sync
    handle it. That is less code, but lyrics arrive late, possibly behind a
    long backfill.
- **Decision:** (a), picked by the owner.
  - `fetch_lyrics` becomes LRCLIB only.
  - `_apply(conn, track, outcome, checked_at=..., lyrics_root=config.lyrics_root)`
    runs under the lock in Decision 3 step 5.
  - `lyrics_path` comes from what `_apply` wrote under `lyrics/`, never from
    a path beside the audio.
  - `build_genius_client`, `_genius_lyrics`, `GENIUS_TOKEN`, the
    `genius_client` parameters and `lyricsgenius` are removed.
    `lyrics_quality.clean_genius` is removed if nothing else calls it.
  - A test runs `run_download` against a fake LRCLIB with the real `_apply`,
    not monkeypatched away.
- **Consequences:** The memory note `ingest-apply-needs-lyrics-root-m5` closes.
  An LRCLIB outage leaves the track without lyrics until the next chain
  LRCLIB sync picks it up, as for any scanned track.

### Decision: 7. "Lock again" hides Add and keeps everything the unlock created
- **Status:** Accepted
- **Context:** The fork spec's Decision 11 offers "Lock again" but never said
  what happens to the binary, the queue or the downloaded songs.
- **Options considered:** these, each with its trade-off:
  - (a) Hide Add and keep the binary and the songs.
  - (b) Hide Add, delete `bin/yt-dlp`, and keep the songs.
- **Decision:** (a), picked by the owner.
  - Locking writes `downloader_unlocked: false` and marks queued `download`
    jobs `error` with "Downloader locked". A running job finishes.
  - Add disappears.
  - `bin/yt-dlp` stays, so unlocking again needs no fetch.
  - Downloaded songs stay, because `downloads/` remains a scan root while it
    holds audio (Decision 3).
- **Consequences:** A locked install that was once unlocked still has the
  binary on disk. `-U` does not run while locked.

### Decision: 8. Add browses every release; the freeze filter and catalog_freeze go
- **Status:** Assumed - not confirmed
- **Context:** `muse/discover.py`'s filter exists to show "what the mirror is
  missing" after 2025-11-13. The fork has no mirror (the fork spec's
  Decision 1), so the filter hides almost every record. This was moved to M5
  by the M1 plan. Cheap to reverse; the owner did not review it.
- **Options considered:** these, each with its trade-off:
  - (a) Remove the filter and `Config.catalog_freeze`, so every release group
    is listed, newest first.
  - (b) Keep the filter with an empty freeze date.
- **Decision:** (a).
  - The lexicographic date comparison and the `firstreleasedate` search
    clause go.
  - The sort stays.
  - `MUSE_CATALOG_FREEZE`, `/api/health`'s `catalog_freeze` and any UI copy
    that says "newer than the mirror" are removed.
  - "Already in library" stays `library_tracks.find_done`.
- **Consequences:** Large discographies hit `MAX_BROWSE_PAGES` truncation
  more often. That truncation already exists and stays silent.

### Decision: 9. M5 ends with the v0.1 alpha release
- **Status:** Accepted
- **Context:** Every past plan deferred publishing to owner checks that never
  ran. The worker installer has no URLs (`muse/smart_manifest.json`
  `"workers": {}`), so smart features can't install for anyone.
- **Options considered:** these, each with its trade-off:
  - (a) A final M5 phase that cuts v0.1.0.
  - (b) A separate M6 release milestone.
  - (c) Upload CI artifacts by hand, with no workflow.
- **Decision:** (a), picked by the owner. The order:
  1. **Owner:**
     - Make the repo public.
     - Upload the five Laya v4 files to `IanTheMitten/laya-muse-v4`.
  2. **Worker release:** a tag `worker-v1` triggers the existing worker CI
     legs. A workflow step publishes release `worker-v1` with both worker
     zips.
     - `scripts/manifest_add_worker.py` fills `muse/smart_manifest.json`,
       which is committed.
     - Worker releases stay versioned on their own, so an app update never
       forces a 2-5 GB re-download.
  3. **App release:** a tag `v0.1.0` triggers the release workflow. It builds
     the unsigned `.dmg` for `aarch64` and `x86_64` and the NSIS `.exe`, then
     publishes them to GitHub Release `v0.1.0`, with release notes from a
     checked-in file.
  4. **README:**
     - Install steps for unsigned builds: right-click > Open on macOS, and
       SmartScreen "More info > Run anyway" on Windows.
     - The licence attributions already gathered: MERT CC-BY-NC-4.0,
       audeering CC-BY-NC-SA-4.0, MMS-FA CC-BY-NC-4.0, and MUSDB18 `other-nc`.
     - The free and non-commercial note.
     - The README never mentions the unlock (as the M1 plan ruled).
- **Consequences:** what this changes:
  - The two tag pushes spend billed macOS minutes, so each needs an explicit
    owner yes at run time.
  - Intel Macs get the app, but smart features report "unsupported" (M4a:
    no torch x86_64 macOS wheel).
  - Signing stays out (the fork spec's deferral; the owner has no budget).

### Decision: 10. A Playwright smoke suite runs the real UI in CI
- **Status:** Accepted
- **Context:** the forces at play:
  - No UI from M2 onward has run in a browser.
  - The scroll lock shipped that way.
  - About 17 owner hand checks are open.
  - Local disk is tight (memory: the M4a disk limit).
- **Options considered:** these, each with its trade-off:
  - (a) A Playwright Chromium e2e suite in CI.
  - (b) A one-time agent-run checklist pass with screenshots.
  - (c) Owner hand checks only.
- **Decision:** (a), picked by the owner.
  - **Setup:**
    - `@playwright/test` is a web devDependency.
    - Specs live in `web/e2e/`.
    - It runs as `npm --prefix web run e2e`, separate from `npm test`, so
      the vitest run and local disk are untouched.
  - **What it starts:**
    - A CI job on `ubuntu-latest` builds `web/dist` and generates a fixture
      music folder: a few short tagged mp3/flac/wav files made with PyAV in a
      script.
    - It starts `uv run python -m muse` with a fresh `MUSE_DATA_DIR` and
      `MUSE_TOKEN`, then opens `/?t=<token>`.
  - **Flows covered:**
    - Every main view scrolls (a regression check for the scroll lock).
    - Play, next, and the queue.
    - Edit details, then reload.
    - Remove and restore.
    - The lyrics panel and the tap-along keys.
    - Settings sections.
    - The unlock flow, including that Add appears and Home's chart shelf
      has no Add links while locked. The chart fetch is faked.
  - **Triggers:** pull requests and pushes to `main`.
  - **What stays manual:** WebKit-specific behaviour (WebKitGTK has no m4a
    locally), the tray, the mini window, drag-drop, the native picker, media
    keys and the smart-features install stay owner hand checks. They are
    consolidated into one current list in the plan.
- **Consequences:** Linux CI minutes are free once the repo is public. A
  Chromium pass does not prove WKWebView or WebView2. The packaging smoke
  matrix still covers codecs.

### Decision: 11. Update check: notify only
- **Status:** Accepted
- **Context:** the forces at play:
  - The alpha ships unsigned.
  - Tauri's updater needs a minisign key that the owner would have to guard
    forever.
  - The fork spec never decided auto-update.
- **Options considered:** these, each with its trade-off:
  - (a) Notify only, with a keyless GitHub Releases check.
  - (b) `tauri-plugin-updater` with a minisign key and `latest.json`.
  - (c) No check.
- **Decision:** (a), picked by the owner.
  - **The check:** once per launch, in the background, the sidecar calls
    `GET https://api.github.com/repos/IanTheMitten/muse-desktop/releases/latest`
    and compares `tag_name` with the app version. The app version comes from
    the shell through `MUSE_APP_VERSION`, falling back to the
    `pyproject.toml` version in dev.
  - **API:** `GET /api/update` answers `{"current", "latest", "url"}` or
    `{"current", "latest": null}`.
  - **UI:** Settings shows "Muse <latest> is out" with a link that opens the
    release page in the system browser, through `tauri-plugin-opener` or the
    shell plugin's `open`. The plan picks whichever is already a dependency.
  - **Failure:** any error is silent and logged.
- **Consequences:** what this changes:
  - This is the app's first external link, so the navigation guard must keep
    it out of the webview.
  - Moving to the Tauri updater later stays possible, with no data
    migration.

## Data flow

- **Unlock:**
  1. Settings > Advanced, then the shortcut, then the password.
  2. `POST /api/settings/downloader` writes `settings.json` and queues
     `ytdlp_install`, which fetches the binary, checks its hash and runs
     `chmod`.
  3. The nav shows Add, and `downloads/` joins the scan roots. The watcher
     restarts, as it does on a folder change.
- **Launch when unlocked:** the chain is unchanged. A background `yt-dlp -U`
  runs outside the chain because it touches no DB.
- **Add:**
  1. MusicBrainz browse (`/api/discover/*`, inline, unlocked only).
  2. `POST /api/jobs` with `mb` or `mb_release` fans out into per-track
     `download` jobs.
  3. `run_download_worker` handles them one at a time, with the existing gap
     and jitter. `-U` runs once at the start of each drain.
  4. Each job follows the steps in Decision 3.
  5. After the drain, the existing content backfill runs when smart features
     are installed.
- **Lock again:** `DELETE /api/settings/downloader`. Queued downloads are
  marked `error`, and the nav hides Add. `downloads/` stays a scan root while
  it holds audio.
- **Update check:** at launch, one HTTPS call. The result is held in memory
  and served by `GET /api/update`.
- **Release:**
  1. Owner steps.
  2. The `worker-v1` tag publishes the worker release.
  3. The manifest commit.
  4. The `v0.1.0` tag publishes the app release.

## Error handling

- **yt-dlp fetch fails, or its hash mismatches:** the `ytdlp_install` job
  fails with "Couldn't get the downloader" and a retry button. Add shows the
  same line, and the unlock stays set.
- **`yt-dlp -U` fails:** the current binary keeps working, and the Add screen
  shows a quiet warning (as the fork spec says).
- **Binary missing at download time** (deleted by hand): the job fails with
  "Downloader missing" and queues `ytdlp_install` again.
- **No m4a stream, or the remux fails:** the job fails with "No m4a audio for
  this video. Try another link." The staged file is deleted.
- **Length mismatch:** unchanged (`_length_mismatch`). The staged file is
  deleted.
- **The final path already exists:** the job fails with "Already in
  downloads", and the staged file is deleted. Nothing in `downloads/` is ever
  overwritten.
- **LRCLIB or art fetch fails:** the track still lands. The chain's sync and
  backfill retry later.
- **Kill mid-download:** the startup reap marks the job `error`, and
  `.incoming/` is emptied. The pending row stays `status='error'`,
  diagnosable, as today.
- **A locked request to the download surface:** 404, with no body detail.
- **Wrong password:** 403. The dialog shows "That's not it.", with no
  lockout.
- **Update check fails or the repo is private:** silent. `latest: null`.

## Testing strategy

- **Unit and DB tests (default pytest run, no network, no real binary):**
  - `muse/ytdlp.py` against a fake executable script: install with hash
    match and mismatch, `-U` failure tolerated, missing binary answers `None`
    in the probes.
  - Remux: a fixture m4a goes through `av` stream copy. Marked to skip when
    `av` is missing, though `av` is core after M5.
  - `run_download` end to end with a fake audio source:
    - It stages in `.incoming/`, moves under the lock, and refuses an
      existing final path.
    - The real `_apply` writes `lyrics/<rowid>.*`.
    - No file is written beside the audio.
  - Scanner: skips dot-dirs. `downloads/` is a root when unlocked or
    non-empty, and not a root when locked and empty.
  - A scan running during a download never creates a second row. Simulated
    by running the scan between the download's steps.
  - `require_unlocked`: each guarded route answers 404 when locked and works
    when unlocked.
  - Unlock: the right password, a wrong one, and lock-again cancelling
    queued jobs.
  - Discover: releases before 2025-11-13 are listed. `catalog_freeze` is gone
    from config and health.
  - Update check: newer tag, same tag, network error, using a fake fetch.
  - Genius removal: `grep -rn genius muse/` is empty, apart from history in
    docs.
- **vitest:**
  - The unlock key matcher (`e.code`, the modifier set).
  - The Home chart filter while locked.
- **Playwright (CI, Decision 10):** the flows listed in Decision 10.
- **Packaging and release:**
  - The existing CI matrix (`package.yml`) still runs on tags.
  - The release workflow is checked on the real `worker-v1` and `v0.1.0`
    tags, after the owner says yes.
  - On a Mac, an owner hand check: unlock, then Add one song. This proves
    the unsigned `yt-dlp_macos` runs without a Gatekeeper prompt.
- **Out of M5:** signed installers, notarization, auto-update tests, and
  WebKit e2e.

## Tech stack

- **yt-dlp:** the official standalone binary, 2026.10.26 or newer at fetch
  time, kept current with `-U` (Decision 1).
- **PyAV `av==19.0.2`:** moves into core dependencies for the remux
  (Decision 2). It is already pinned and BSD, with LGPL FFmpeg libraries.
- **mutagen:** core, unchanged. It tags the remuxed m4a.
- **requests:** core, unchanged. It fetches the binary and makes the update
  check.
- **`@playwright/test` with Chromium:** a web devDependency, CI only
  (Decision 10). The plan pins the version.
- **GitHub Actions:** the release workflow on tags `worker-v*` and `v*`
  (Decision 9).
- **Removed:** `yt-dlp` (Python package) and `lyricsgenius` (Decisions 1
  and 6).

## Open questions

- Deferred: exact yt-dlp CLI flags and JSON fields (download `--dump-single-json --no-simulate` vs `--print`, the probe's flat search) - pinned at plan time by running the 2026.10.26 binary once; architecture does not change.
- Deferred: how often `bestaudio[ext=m4a]` is missing on YouTube Music-only uploads - measured by an owner hand check of 10 Add downloads after M5; if failures are common, a follow-up accepts Opus/webm and remuxes to `.opus` (Ogg) with the same PyAV path, which CI already showed both webviews decode.
- Deferred: macOS media keys and the Now Playing widget - owner runs M3 check 6 on the v0.1.0 `.dmg`; a native `MPNowPlayingInfoCenter` bridge becomes a follow-up only if it fails (owner pick: keep conditional).
- Deferred: macOS code signing, notarization and Windows signing - unchanged from the fork spec; the alpha ships unsigned, revisit when there is budget.

## Provenance

- user-picked: Decision 1 (standalone yt-dlp binary); Decision 2 (PyAV remux, no ffmpeg); Decision 3 (stage, then move under chain_lock); Decision 4 (locked app hides Add, server 404s, chart stays owned-only); Decision 6 (inline LRCLIB, Genius removed); Decision 7 (Lock again keeps files); Decision 9 (M5 ends with v0.1 alpha); Decision 10 (Playwright smoke in CI); Decision 11 (notify-only update check); media keys stay conditional (Open questions). Every one of these was the recommended option, chosen by the owner from 2-3 alternatives on 2026-10-30.
- recommended-and-accepted: the `-U` cadence of once per drain (inside Decision 1); `downloads/` as a fixed, non-removable scan root (inside Decision 3); worker releases versioned apart from app releases (inside Decision 9, consistent with M4a's `worker-v1`).
- assumed: Decision 5 (webview keydown, server-side sha256 check; replaces the fork spec's shell-handled shortcut) and Decision 8 (remove the freeze filter and `catalog_freeze`) - both cheap to reverse and flagged "Assumed - not confirmed".
- overrides: Decision 2 overrides the M1 plan's assumption that M5 fetches a static ffmpeg; Decision 1 changes the fork spec's "-U before each download job" to once per drain; Decision 5 drops the fork spec's shell-side shortcut handling.
