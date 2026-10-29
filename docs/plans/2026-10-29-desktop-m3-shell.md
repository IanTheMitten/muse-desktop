# Plan - Muse desktop M3: Tauri shell, sidecar packaging, tray, mini window, drag-drop, CI matrix

Source spec: `docs/specs/2026-10-29-desktop-app-fork.md` (Status: ready for planning; `spec-lint --handoff` exit 0, 1 warn no-source-prd). Earlier plans: `docs/plans/2026-10-29-desktop-m1-core.md` (milestone list at lines 9-23), `docs/plans/2026-10-29-desktop-m2-editing.md` (20/20 done, main at cb3349a).

Repo: `~/Projects/muse-desktop`. Python commands run from the repo root, npm through `npm --prefix web`. Release rule from M1 holds: `docs/plans/` and `docs/specs/` never ship.

## Scope

M1 milestone list, M3 line (`docs/plans/2026-10-29-desktop-m1-core.md:21`): Tauri shell, sidecar packaging, tray, mini-player, drag-drop, native folder picker, single instance, the CI packaging matrix. Carried from M2: drag-drop (Tauri event). Spec decisions covered: 2, 3 (shell half), 15 (drag-drop), 16, and the packaging-smoke part of the Testing strategy (spec lines 645-654).

**Owner answers (2026-10-29, this planning round):**
- Verify with a local Rust toolchain plus Linux Tauri deps; the macOS + Windows CI matrix runs only at the final phase (Task 13). Rust 1.99.1 and the apt packages are installed.
- Linux is dev-only: `tauri build --debug --no-bundle` and headless runs here, no Linux bundle or CI leg.

**Not in this plan:** signing/notarization (spec Deferred, alpha ships unsigned), the ML worker and its installer (M4), the downloader (M5), a native Now Playing bridge (only if Task 13 shows Media Session missing on macOS; then it is a follow-up), external links opening in the system browser (no external links exist: `grep -rn 'target="_blank"\|window.open' web/src` is empty).

## Assumptions (decided at planning)

- **The webview loads the UI from the sidecar.** The main window starts on a local boot page (`frontendDist: "boot"`), then navigates to `http://127.0.0.1:<port>/` once the sidecar prints `MUSE_LISTENING <port>`. The sidecar already serves `web/dist` (`muse/api.py:1880-1905`), so relative `/api` URLs, the stream URLs and the SPA routes work unchanged, with no CORS. The frozen sidecar bundles `web/dist`; debug builds of the shell set `MUSE_WEB_DIST` to the repo's `web/dist`, so a UI change needs only `npm --prefix web run build` and a reload.
- **Token (Decision 3).** The shell makes 32 random bytes (`getrandom::fill`), hex-encodes them, passes `MUSE_TOKEN` to the sidecar, and injects `window.__MUSE_TOKEN__ = "<hex>";` through `initialization_script` into every window it builds. `web/src/token.ts` prefers it over `?t=`. The token is never written to `muse.log`: the sidecar logs the `Open http...?t=` line only when it generated the token itself (`muse/__main__.py:52-53`).
- **Navigation guard.** Each window's `on_navigation` allows only scheme `tauri`, host `tauri.localhost` (the Windows boot page origin) and host `127.0.0.1`. Everything else is refused, so the injected token never reaches another site.
- **IPC without an npm dependency.** `app.withGlobalTauri: true` exposes `window.__TAURI__` (core, event, webview, window, dialog) on the loopback pages too. Web code reaches it only through `web/src/shell.ts`, which is `undefined` in a plain browser. The only new npm package is the dev dependency `@tauri-apps/cli@2.12.4`.
- **One capability.** `src-tauri/capabilities/default.json` with `"remote": {"urls": ["http://127.0.0.1:*"]}` covers the boot page and the loopback pages. App commands are callable from the remote origin only when declared in `build.rs` through `AppManifest::commands` and granted as `allow-<command>` permissions.
- **Sidecar lifecycle.** PyInstaller `--onefile` runs as a bootloader plus a child; killing the bootloader orphans the child and leaves its `_MEI*` temp dir behind. So the shell never kills the sidecar. It holds the `CommandChild` and drops it on `RunEvent::Exit`, which closes the sidecar's stdin. With `MUSE_PARENT_WATCH=1`, `muse/__main__.py` reads stdin on a daemon thread; at EOF it sets `server.should_exit` and, 10 s later, `os._exit(0)`. A shell crash closes the pipe the same way.
- **Data dir.** `MUSE_DATA_DIR` env when set (dev and smoke), otherwise Tauri's `app_data_dir()` for identifier `io.github.ianthemitten.muse`. Sidecar stdout and stderr append to `<data dir>/muse.log`, renamed to `muse.log.1` at launch when over 5 MB. The shell also sets `OPENBLAS_NUM_THREADS=1` (implicit warns about OpenBLAS threads in the frozen run).
- **Boot status contract.** Command `boot_status` returns `{"state": "starting" | "failed", "lines": [<last 20 log lines>], "log_path": "<abs path>"}`. `failed` when the sidecar terminates before `MUSE_LISTENING`, or 90 s pass without it. The boot page polls it every 500 ms.
- **Close behaviour (Decision 16).** Before the sidecar is ready, closing the main window quits. After, it hides the window; the first time per data dir (marker file `<data dir>/.tray-notice-shown`) a native message says "Muse is still playing in the tray. To quit, choose Quit Muse from its menu." (macOS: "menu bar" for "tray"). Quit Muse in the tray menu is the only way to stop the app. macOS `RunEvent::Reopen` (dock click) shows the main window.
- **Tray menu:** Play/Pause, Next, Mini player, Show Muse, separator, Quit Muse. Play/Pause and Next emit `muse:command` to the main window. macOS uses a 44 px template glyph (`icon_as_template`), other platforms the app icon.
- **Mini window.** DESIGN.md already calls `ControlBar` the "mini player" (`DESIGN.md:331`), so the new always-on-top window is the **mini window** in code and docs; its user-facing label stays "Mini player". Label `mini`, URL `http://127.0.0.1:<port>/mini`, 400x96, no decorations, not resizable, always on top, skipped in the taskbar. `web/src/main.tsx` renders `MiniWindow` instead of `App` on `/mini`, so the mini window never creates a second `<audio>`, plays heartbeat or EventSource. It only sends commands.
- **Events.** `muse:command` (payload `"toggle" | "next" | "previous"`, to `main`, from tray or mini window); `muse:now` (payload `{track: {track_rowid, track_name, artist_name, has_art} | null, playing}`, main to `mini`, on every change); `muse:hello` (mini to main on mount; main answers with `muse:now`). Commands `open_mini` and `show_main`.
- **Drag-drop.** Tauri's webview drag-drop events (`getCurrentWebview().onDragDropEvent`, payload `enter`/`over`/`drop`/`leave` with `paths`). The queue reorder uses pointer events (`web/src/player/NowPlayingSheet.tsx:501`), not HTML5 drag, so Tauri's default `dragDropEnabled` breaks nothing. Dropped paths go to new `POST /api/library/import`: a folder becomes a watched music folder (same nesting and data-dir rules as `PUT`), an audio file becomes an entry in new `settings.json` key `music_files`, read in place and rescanned on every chain run, never watched (spec line 501-503). A file inside a music folder, or already listed, counts as `already`. v1 has no list of dropped songs in Settings; Remove from library tombstones one.
- **Folder picker.** In the shell, Settings > Music folders shows a "Choose folder…" button (`__TAURI__.dialog.open({directory: true, multiple: true})`) in place of the path field. The browser keeps the path field.
- **Single instance:** `tauri-plugin-single-instance`, registered first; a second launch shows and focuses the main window.
- **Packaging.** `scripts/freeze_sidecar.py` freezes `scripts/sidecar_entry.py` with PyInstaller 6.22.3 (new `package` dependency group) to `src-tauri/binaries/muse-core-<rustc host triple>[.exe]`. Bundle targets `app`, `dmg`, `nsis`. Windows NSIS `installerHooks` runs `taskkill /F /T /IM muse-core.exe` before install (Decision 2).
- **One Cargo target dir.** `scripts/tauri.sh` wraps the CLI and defaults `CARGO_TARGET_DIR` to `~/.cache/muse-tauri-target`, so worktrees share one ~3 GB build. CI does not use the wrapper.
- **Smoke mode.** When `MUSE_SMOKE_REPORT` is set, the shell injects `src-tauri/src/smoke.js`: on the loopback page it waits for the 6 fixture songs, decodes each through the real stream URL (`loadeddata`, muted, no `play()`, so no autoplay policy applies), records `canPlayType`, `/api/health` status and `'mediaSession' in navigator`, then calls command `smoke_report`, which writes the JSON and exits. `scripts/shell_smoke.py` drives it locally (own `Xvfb` on headless Linux) and in CI, then checks no `muse-core` process is left within 15 s.
- **Formats expected to decode:** CI (macOS, Windows): `mp3,flac,m4a,wav`; Ogg and Opus are recorded, not required (Decision 12). Local Linux: `mp3,flac,wav` (this WebKitGTK reports `canPlayType("audio/mp4") == ""`).
- **CI trigger.** `workflow_dispatch` plus tag pushes `v*` and `ci-package-*`. `workflow_dispatch` needs the file on the default branch, so Task 13 triggers by pushing tag `ci-package-m3-1`.
- **Deferred spec items decided here:** Media Session on WKWebView: Task 13 records `'mediaSession' in navigator` from the real macOS webview; whether the system widget and media keys respond is an owner check on a Mac (listed below), and a native bridge is a follow-up only if it fails. Signing stays deferred (unsigned alpha). Worker hosting stays M4.

## Premises

- Spec passes handoff lint. Verified: `node ~/.claude/skills/spec-brainstorming/scripts/spec-lint.mjs --handoff docs/specs/2026-10-29-desktop-app-fork.md` (exit 0).
- Baselines on main cb3349a: backend 1134 passed, 1 skipped, 3 deselected; web 21 vitest tests pass and `npm run build` is green after `npm --prefix web ci` (the main checkout's `node_modules` predated vitest). Verified: `uv run pytest -p no:cacheprovider`, `npm --prefix web test`, `npm --prefix web run build`.
- Toolchain here: rustc/cargo 1.99.1 via rustup (`~/.cargo/env` sourced by `~/.profile:28` and `~/.bashrc:119`); webkit2gtk-4.1 2.52.6, gtk+-3.0 3.24.52, ayatana-appindicator3 0.5.94, `xvfb-run`, `Xvfb`, `ffmpeg`. Verified: `pkg-config --modversion webkit2gtk-4.1 gtk+-3.0 ayatana-appindicator3-0.1`, `which xvfb-run ffmpeg`.
- Crate and npm versions resolve: tauri 2.12.5, tauri-build 2.7.4, tauri-plugin-shell 2.6.0, tauri-plugin-single-instance 2.5.5, tauri-plugin-dialog 2.8.4, `@tauri-apps/cli` 2.12.4. Verified: crates.io API, npm registry, and a scratch build (`tauri build --debug --no-bundle`, 2 m 05 s cold, 4-25 s incremental, target dir 2.9 GB).
- PyInstaller 6.22.3 freezes `python -m muse` (`--onefile --collect-submodules uvicorn --collect-submodules muse --add-data web/dist`) into an 81 MB Linux binary that prints `MUSE_LISTENING` 1.5 s after launch, migrates a fresh data dir to schema 22, answers 401 without the token, scans 6 fixture formats and serves the SPA from the bundle. Only warnings: implicit's CUDA libs and `scipy.special._cdflib`. Verified: scratch freeze plus `curl` probes.
- `PROJECT_ROOT = Path(__file__).resolve().parent.parent` (`muse/config.py:11`) resolves to the PyInstaller bundle root, so the bundled `web/dist` is found with no env. Verified: the frozen binary served `/` from the bundle.
- SIGKILL on the onefile bootloader leaves the server child running (reparented to PID 1), and a graceful `app.exit(0)` in the shell also leaves it (tauri-plugin-shell does not kill children at exit). A child thread reading stdin to EOF exits once the spawning process is gone. Verified: scratch `ps` before and after `kill -9`; scratch frozen probe wrote `eof-exit` after its parent exited.
- In the scratch shell, `navigate()` to `http://127.0.0.1:<port>/` works; the init-script token, `window.__TAURI__.event/dialog` and `'mediaSession' in navigator` are present on the loopback page; a custom command from that page fails with `probe_report not allowed. Plugin not found` until declared through `AppManifest::commands` and granted `allow-probe-report`; the same single capability also lets the local boot page invoke it. Permissions `core:window:allow-close`, `core:window:allow-start-dragging`, `dialog:allow-open` resolve. Tray (`TrayIconBuilder`, `Menu::with_items`, `PredefinedMenuItem::separator`, `icon_as_template`, `Image::from_bytes` with feature `image-png`), `WebviewWindowBuilder` with `WebviewUrl::External`, `DialogExt::message`, `CloseRequested`/`prevent_close` and `RunEvent::Exit`/`Reopen` compile clean. Verified: scratch builds and headless runs.
- `xvfb-run -a` intermittently hangs with no output after earlier runs were killed; a direct `Xvfb` is the reliable route. Verified: repeated scratch runs.
- WebKitGTK here: `canPlayType` mp3/flac/wav `maybe`, ogg/opus `probably`, m4a `""`. Verified: scratch probe report.
- Tauri config keys exist in tauri-utils 2.10.2: `bundle.windows.nsis.installerHooks` (`config.rs:942`), `mainBinaryName` (`config.rs:4105`), `dragDropEnabled` default true (`config.rs:2408`). `tauri icon <svg> -o <dir> -p 44` writes `44x44.png`. Verified: registry source read, scratch run.
- `run_scan_job` returns before scanning unless some entry is a directory, so a files-only library needs its guard widened (Task 2). Verified: `muse/jobs.py:450`.
- Settings shape is pinned exactly at `tests/test_api_scan.py:26-27`; `tests/test_settings.py:5-11` compares against `settings.DEFAULTS` (still equal after widening). Verified: `grep -rn "downloader_unlocked" tests web/src`.
- `.gitignore` ignores `*.wav`, `*.mp3`, `*.flac`, `*.m4a`, so fixtures need a negation line. Verified: `cat .gitignore`.
- `.python-version` is `3.12`, so `uv` in CI freezes with 3.12 (Decision 14). Verified: `cat .python-version`.
- GitHub action majors: `actions/checkout@v7`, `actions/setup-node@v7`, `astral-sh/setup-uv@v10`, `actions/upload-artifact@v7`, `dtolnay/rust-toolchain@stable`; runner `macos-15` is arm64. Verified: `gh api repos/<owner>/<repo>/releases/latest`, `gh api repos/actions/runner-images/contents/images/macos`.
- `CloseIcon`, `PlayIcon`, `PauseIcon`, `PrevIcon`, `NextIcon` exist in `web/src/components/Icons.tsx`; `useCardMesh(trackRowid, hasArt)` returns the card wash with no player context (`web/src/components/Wash.tsx:63-80`); `CoverArt` applies `withToken` itself (`web/src/components/CoverArt.tsx:46`).

## Memory carried into briefs

- **Worktree sessions refuse compound shell commands** (`a && b`, `a; b`, heredocs, `cd x && ...`): every Verify step is one plain command, run in order. Use `npm --prefix web ...` and the Write tool for files.
- **Fresh worktree needs `npm --prefix web ci`** before any web build or test.
- **`python` is not on PATH:** always `uv run python`.
- **Smoke after startup/API changes:** `bash scripts/smoke.sh` must print `SMOKE-OK`.
- **TestClient hangs on `/api/jobs/stream`:** never open the SSE route in a test.
- **Repo-local git identity is set:** do not change git config. **`git commit -- <path>` fails on staged deletions:** `git add` then plain `git commit`.
- **Exit 139 at pytest teardown** is a known flake: rerun once.
- **Never `pkill -f`/`pgrep -f` with a pattern in the same command line:** it matches the running shell itself. Use `pgrep -x muse-core`.
- **UI built in M2 was never exercised in a browser** (only build/vitest/preflight): the owner checks below cover M3's UI by hand.

## Owner checks after the run (cannot be automated here)

1. `bash scripts/tauri.sh dev` on the Ubuntu desktop: the window opens on the library; the tray icon shows; Play/Pause and Next in the tray drive playback.
2. Close the window: the one-time notice appears, music keeps playing; Show Muse brings it back; Quit Muse stops it and `pgrep -x muse-core` prints nothing within 15 s.
3. Mini player from the control bar and the tray: title follows the track, buttons work, the art opens the main window, Escape closes it.
4. Drag a folder and a single song from the file manager onto the window: overlay, then the result line; the song plays.
5. Settings > Music folders: "Choose folder…" opens the native picker.
6. On a Mac with the Task 13 `.dmg` (right-click, Open): media keys and the Now Playing widget follow playback.

## Design: boot-page

READ: Reading this as: a one-second desktop launch screen and its rare failure state for one listener who just opened the app, quiet plain language, leaning toward the existing Muse ink housing (DESIGN.md §2).
DIALS: VARIANCE 1 / MOTION 1 / DENSITY 3
PALETTE: restrained, ink tokens copied as literals (the boot page cannot load tokens.css): dark bg #121117, raised #1c1b22, text #f5f3f0, text-2 #dcd9d4, hairline rgb(255 255 255 / 0.08); light bg #f4f3f6, raised #ffffff, text #141218, text-2 #35313b, hairline rgb(20 18 24 / 0.09). No accent.
TYPE: display system-ui 800 / body system-ui / utility ui-monospace for log lines (Figtree ships inside the web build, which the shell page cannot reach)
SIGNATURE: the failure screen shows the real last log lines and where the full log lives, so a stuck listener has something concrete to send.
ANCHORS: VS Code "window failed to load" page (plain heading plus actionable detail), macOS crash reporter (raw text in a scrollable well), Plexamp launch (no splash art, only the housing colour)
VARIETY: cohere, no rotation. Ledger 2026-09-03: "cohere with this system rather than rotating away from it". Fingerprint: labelled rail, art-led shelves, Newsreader + Figtree, record wash.
CONTENT: starting: "Starting Muse". Failure: heading "Muse couldn't start", "These are the last lines from its log. The full log is at:", the log path, the lines, "Close this window to quit." Real data from `boot_status`.
FORMAT: production
TARGET: `src-tauri/boot/index.html`, one static file, inline CSS and JS, no build step
CONSTRAINTS: WCAG AA in both schemes (`prefers-color-scheme`); no spinner, no animation; no em dash; the log well scrolls (max-height 50vh) and never pushes the path off screen at 360x560; path is selectable text.

## Design: mini-window

READ: Reading this as: an always-on-top 400x96 remote control for one listener working in another app, quiet utilitarian language, leaning toward the Muse record wash (DESIGN.md §1, §9).
DIALS: VARIANCE 2 / MOTION 2 / DENSITY 5
PALETTE: drenched by the record at the card band: background `useCardMesh(track_rowid, has_art)` over --ink; --text, --text-2, --hairline; the play button uses --accent / --on-accent token defaults (the mini window does not run applyAccent). No new colour.
TYPE: display Newsreader Variable (title, 600) / body Figtree Variable (artist, buttons)
SIGNATURE: the window is a sliver of the room: the same four-point card wash as the Home cards, so a corner of the screen takes the record's colour.
ANCHORS: Apple Music MiniPlayer (art left, title and artist stacked, transport right), Plexamp mini player (art click returns to the full app), macOS Picture in Picture (borderless, drag anywhere, close in the corner)
VARIETY: cohere, no rotation (same ledger note as above).
CONTENT: title and artist from `muse:now`; empty state title "Nothing playing", no artist line, transport disabled. Labels: "Previous", "Play"/"Pause", "Next", "Close mini player", art button "Show Muse".
FORMAT: production
TARGET: `web/src/views/MiniWindow.tsx`, `web/src/styles/mini.css`, `web/src/main.tsx` (route branch)
CONSTRAINTS: WCAG AA; fixed 400x96 window, padding 16px, art 64px at --r-s; 44px transport targets, 28px close at top-right 6px; visible focus; Escape closes; root and text block carry `data-tauri-drag-region`; one-line ellipsis on title and artist; no motion; no em dash.

## Design: drop-overlay

READ: Reading this as: a full-window drop target and a one-line result for a listener dragging folders from Finder or Explorer, quiet plain language, leaning toward the Muse glass panels.
DIALS: VARIANCE 1 / MOTION 2 / DENSITY 2
PALETTE: restrained: --glass with 24px backdrop blur over the app; inner frame 2px solid --accent, radius --r-l, inset 16px; text --text and --text-2.
TYPE: display Figtree Variable 700 at --t-l / body Figtree Variable at --t-s
SIGNATURE: the whole window becomes the target behind glass, framed in the record's accent, so the drop zone is unmistakable without a dashed box.
ANCHORS: Finder drop highlight (whole-window frame), Slack file drop (full-window glass with one sentence), Apple Music import (result in plain words)
VARIETY: cohere, no rotation.
CONTENT: hover: "Drop to add to your library" and "Folders stay watched. Songs stay where they are." Result: `dropMessage()` output (Task 8 cases) or the server's error text.
FORMAT: production
TARGET: `web/src/components/DropZone.tsx`, `web/src/styles/drop.css`, mounted in `web/src/App.tsx`
CONSTRAINTS: z-index 50 (above `.np` 40); `pointer-events: none`; result in an `aria-live="polite"` region, errors `role="alert"`; opacity fade 160ms var(--ease), none under `prefers-reduced-motion`; result hides after 2500 ms; no em dash.

## Design: folder-picker

READ: Reading this as: two small desktop-only affordances inside existing chrome (Settings > Music folders and the control bar), quiet utilitarian language, leaning toward the existing `.btn` and `.icon-btn.stroke`.
DIALS: VARIANCE 1 / MOTION 1 / DENSITY 5
PALETTE: existing tokens only: `.btn` (--hairline fill), `.icon-btn.stroke`, --text-2 for the hint. No new colour.
TYPE: display Newsreader Variable (unused here) / body Figtree Variable (inherited)
SIGNATURE: in the desktop app the path field gives way to one native "Choose folder…" button, because typing a path is a browser workaround.
ANCHORS: Apple Music "Add to Library…" (native picker), VS Code "Open Folder…", Spotify desktop local files "Add a source"
VARIETY: cohere, no rotation.
CONTENT: button "Choose folder…"; hint "You can also drag a folder onto this window."; control bar icon button `aria-label="Open mini player"`.
FORMAT: production
TARGET: `web/src/views/Settings.tsx`, `web/src/styles/settings.css`, `web/src/components/ControlBar.tsx`, `web/src/components/Icons.tsx`
CONSTRAINTS: without the shell, today's path form renders unchanged; 44px targets; visible focus; errors in the existing `#folder-error`; the new icon matches `LyricsIcon`'s stroke style; no em dash.

## Design: app-icon

READ: Reading this as: the dock/taskbar icon and the menu-bar glyph for a listener scanning a crowded dock, quiet confident language, leaning toward the record wash plus the existing 3-bar equaliser glyph.
DIALS: VARIANCE 3 / MOTION 1 / DENSITY 1
PALETTE: committed: base #121117; wash blobs #7a4b8f (plum), #b5573a (ember), #2f6f7e (teal), #a8842a (ochre) as radial gradients at 12% 18%, 88% 12%, 82% 88%, 8% 92%, transparent at 62%, 58%, 64%, 58%; bars #f5f3f0. Tray template: #000000 on transparent only.
TYPE: none, no lettering
SIGNATURE: the record wash in a rounded square with the now-playing equaliser bars in front, the same three bars that mark the current track inside the app.
ANCHORS: Apple Music icon (one glyph on a saturated field), Plexamp icon (abstract, no letterform), macOS menu-bar extras (template glyph at 22pt)
VARIETY: cohere, no rotation.
CONTENT: no text.
FORMAT: production
TARGET: `src-tauri/icons/icon.svg` (viewBox 0 0 1024 1024), `src-tauri/icons/tray-template.svg` (viewBox 0 0 22 22)
CONSTRAINTS: icon: rounded rect x=100 y=100 width=824 height=824 rx=185, transparent outside it (macOS grid margin); three bars width 96, gap 36, heights 300/420/220, bottoms at y=702, rx=48, centred horizontally; legible at 32px. Tray: three bars width 3, gap 2.5, heights 11/15/8, bottoms at y=18, rx=1.5, x at 4/9.5/15.

## Phase 1 - Foundations

### Task 1: Sidecar exits when the shell goes away
- [x] status
**Objective:** With `MUSE_PARENT_WATCH=1`, `python -m muse` shuts down cleanly once its stdin closes; without it, nothing changes.
**Write-scope:**
`muse/__main__.py`
`tests/test_parent_watch.py` (new)
**Read-context:** `muse/__main__.py:1-62`, `scripts/smoke.sh:1-20`
**Anchors:**
`def main() -> int:` @ `muse/__main__.py`
`def _listener(address: str, port: int) -> socket.socket:` @ `muse/__main__.py`
**Steps:**
1. `muse/__main__.py`: import `os`, `threading`, `time`. Add, after `_listener`:
   `def _exit_with_parent(server: uvicorn.Server) -> None:` with docstring "Stop when stdin closes: the shell that spawned us is gone (Decision 3). A PyInstaller onefile binary is a bootloader plus a child, and killing the bootloader orphans the child; a closed stdin pipe is the one signal every platform delivers." Body: `if sys.stdin is None: return`; `sys.stdin.buffer.read()`; `server.should_exit = True`; `time.sleep(10)`; `os._exit(0)`.
2. In `main()`, after `server = uvicorn.Server(...)` and before `server.run(...)`: `if os.environ.get("MUSE_PARENT_WATCH") == "1": threading.Thread(target=_exit_with_parent, args=(server,), daemon=True).start()`.
3. `tests/test_parent_watch.py`: helper `_launch(tmp_path, watch: bool) -> subprocess.Popen` runs `[sys.executable, "-m", "muse"]` with `stdin=PIPE, stdout=PIPE, stderr=DEVNULL, text=True`, env = `os.environ` copy minus `MUSE_PORT`, plus `MUSE_DATA_DIR=<tmp_path>/data`, `MUSE_TOKEN=parent-watch-token`, and `MUSE_PARENT_WATCH=1` only when `watch`. It reads `p.stdout.readline()` up to 20 times until a line starts with `MUSE_LISTENING`, else kills the process and fails.
   - `test_exits_when_stdin_closes`: launch with watch, `p.stdin.close()`, `assert p.wait(timeout=20) == 0`.
   - `test_keeps_running_without_the_flag`: launch without watch, `p.stdin.close()`, `with pytest.raises(subprocess.TimeoutExpired): p.wait(timeout=3)`, then `p.terminate()` and `p.wait(timeout=20)`.
**New symbols:** `_exit_with_parent(server: uvicorn.Server) -> None`
**Verify:** `uv run pytest -p no:cacheprovider tests/test_parent_watch.py` then `uv run pytest -p no:cacheprovider` then `bash scripts/smoke.sh`. Expected: 2 passed; full suite 0 failed; `SMOKE-OK`.
**Forbidden:** no new deps; don't change the bind, the `MUSE_LISTENING` line or token handling; don't read stdin unless the flag is set (a terminal or `/dev/null` stdin must not stop the server).
**Depends:** none
**Why:** Verified in planning: the onefile child survives both a killed bootloader and a graceful shell exit. The shell (Task 6) drops the child's stdin at exit instead of killing it, so the temp dir is cleaned and the DB closes.

### Task 2: Drop import route and dropped songs in the scan
- [ ] status
**Objective:** `POST /api/library/import` adds dropped folders as music folders and dropped audio files to `settings.music_files`, and the chain scans those files in place.
**Write-scope:**
`muse/settings.py`
`muse/scanner.py`
`muse/jobs.py`
`muse/api.py`
`tests/test_api_import.py` (new)
`tests/test_api_scan.py`
**Read-context:** `muse/settings.py:1-41`, `muse/scanner.py:109-137`, `muse/jobs.py:433-453`, `muse/api.py:127-129`, `muse/api.py:545-560`, `muse/api.py:750-795`, `tests/test_api_scan.py:1-30`, `tests/test_api_scan.py:88-106`
**Anchors:**
`def music_folders(path: Path) -> list[Path]:` @ `muse/settings.py`
`def _rows_under(conn: sqlite3.Connection, folder: Path) -> list[sqlite3.Row]:` @ `muse/scanner.py`
`class MusicFoldersWrite(BaseModel):` @ `muse/api.py`
`async def put_music_folders(body: MusicFoldersWrite) -> dict:` @ `muse/api.py`
`async def run_chain(launch: bool) -> None:` @ `muse/api.py`
`async def run_scan_job(` @ `muse/jobs.py`
`def _client(tmp_path):` @ `tests/test_api_scan.py`
**Steps:**
1. `settings.py`: `DEFAULTS = {"music_folders": [], "music_files": [], "downloader_unlocked": False}`; add `def music_files(path: Path) -> list[Path]:` returning `[Path(p) for p in load(path)["music_files"]]`.
2. `scanner.py`, inside `scan`'s `for folder in folders:` loop, right after `folder = Path(folder).resolve()`: if `folder.suffix.lower() in AUDIO_EXTENSIONS and not folder.is_dir()`: comment "One dropped song (Decision 15): read in place, rescanned with the chain, never watched."; if `folder.is_file()` append it to `files`, else `res.missing += conn.execute("UPDATE tracks SET status = 'missing_file' WHERE file_path = ? AND status = 'done' AND removed_at IS NULL", (str(folder),)).rowcount`; then `continue`.
2b. `jobs.py:450` (`run_scan_job`): the guard `if not any(f.is_dir() for f in folders):` becomes `if not any(f.exists() for f in folders):`, so a library of dropped songs alone still scans; update the docstring's "no folder exists" to "no folder or dropped song exists".
3. `api.py`: add `class ImportPaths(BaseModel): paths: list[str]` after `MusicFoldersWrite`; import `AUDIO_EXTENSIONS` from `.tags`. In `run_chain` use `folders = settings.music_folders(config.settings_path) + settings.music_files(config.settings_path)`.
4. `api.py`: move the nesting loop and the data-dir loop out of `put_music_folders` into `def _check_folders(folders: list[Path]) -> None:` defined just above it (same messages, raises the same `HTTPException(400, ...)`); `put_music_folders` calls it after deduping.
5. `api.py`: add `@app.post("/api/library/import")` `async def import_paths(body: ImportPaths) -> dict:` after `put_music_folders`. Load settings; `folders = [Path(p) for p in saved["music_folders"]]`, `files = list(saved["music_files"])`. Per raw path: not absolute: `ignored += 1`; else `p = Path(raw).resolve()`; directory: `already += 1` if `p` equals or `is_relative_to` a listed folder, else append, `folders_added += 1`; file with suffix in `AUDIO_EXTENSIONS`: `already += 1` if `str(p) in files` or under a listed folder, else append `str(p)`, `songs_added += 1`; otherwise `ignored += 1`. If `folders_added`: `_check_folders(folders)`. If anything was added: save both lists (`[str(f) for f in folders]`), call `app.state.start_watcher()` when `folders_added`, then `app.state.spawn_chain()`. Return `{"folders_added": .., "songs_added": .., "already": .., "ignored": ..}`.
6. `tests/test_api_scan.py:26-27`: the expected dict gains `"music_files": []`.
7. `tests/test_api_import.py` (reuse `_client`/`_tags` shape from `tests/test_api_scan.py:14-21`): (a) folder drop: response `folders_added == 1`, `GET /api/settings` lists it; (b) song outside any folder: `songs_added == 1`, `music_files` lists it, and polling `/api/library/songs` for up to 10 s finds "Fake Song"; (c) song inside a listed folder: `already == 1`, `music_files` stays `[]`; (d) same folder dropped twice: second response `already == 1`; (e) a `.txt` file and `"rel/x.mp3"`: `ignored == 2`; (f) dropping a folder that contains the data dir: 400; (g) `muse.scanner.scan` on a temp DB with a listed file path that was deleted after a first scan: that row's `status == "missing_file"`.
**New symbols:** `music_files(path: Path) -> list[Path]`; `class ImportPaths(BaseModel)`; `_check_folders(folders: list[Path]) -> None`; route `POST /api/library/import`
**Verify:** `uv run pytest -p no:cacheprovider tests/test_api_import.py tests/test_api_scan.py tests/test_settings.py` then `uv run pytest -p no:cacheprovider` then `bash scripts/smoke.sh`. Expected: all passed; full suite 0 failed; `SMOKE-OK`.
**Forbidden:** don't watch dropped files; don't change `PUT` messages or status codes; no new deps; never open `/api/jobs/stream`.
**Depends:** none
**Why:** Decision 15 drag-drop: folders become watched folders, files are referenced in place and rescanned at launch. The UI half is Task 8.

### Task 3: Packaging tooling, smoke fixtures and smoke driver
- [ ] status
**Objective:** One command freezes the sidecar to `src-tauri/binaries/`, one wraps the Tauri CLI with a shared target dir, and `scripts/shell_smoke.py` can drive a built shell against committed fixtures.
**Write-scope:**
`pyproject.toml`
`uv.lock`
`.gitignore`
`scripts/freeze_sidecar.py` (new)
`scripts/sidecar_entry.py` (new)
`scripts/tauri.sh` (new)
`scripts/shell_smoke.py` (new)
`scripts/smoke.sh`
`tests/fixtures/smoke/` (new)
**Read-context:** `scripts/smoke.sh:1-20`, `pyproject.toml:37-43`, `.gitignore:1-70`, `muse/config.py:11`
**Anchors:**
`def main() -> int:` @ `muse/__main__.py`
**Steps:**
1. `pyproject.toml` `[dependency-groups]`: add `package = ["pyinstaller==6.22.3"]`; run `uv lock`.
2. `.gitignore`: append `/build/`, `/src-tauri/target/`, `/src-tauri/binaries/`, `/src-tauri/gen/`, and `!tests/fixtures/smoke/*` (after the audio patterns).
3. `scripts/sidecar_entry.py`: `from muse.__main__ import main` then `raise SystemExit(main())`, with a one-line docstring saying PyInstaller needs a script, not `-m`.
4. `scripts/freeze_sidecar.py`: docstring "Freeze `python -m muse` into the Tauri sidecar (Decision 2): src-tauri/binaries/muse-core-<target triple>, the name bundle.externalBin expects. web/dist must be built first; the SPA ships inside." Read the triple from `rustc -vV` (line starting `host:`). Exit 1 with "web/dist is missing: run `npm --prefix web run build` first" when `web/dist/index.html` is absent. Call `PyInstaller.__main__.run([...])` with: the entry script, `--onefile`, `--noconfirm`, `--log-level`, `WARN`, `--name`, `muse-core-<triple>`, `--distpath` `src-tauri/binaries`, `--workpath` and `--specpath` `build/pyinstaller`, `--add-data` `f"{dist}{os.pathsep}web/dist"`, `--collect-submodules` `uvicorn`, `--collect-submodules` `muse` (absolute paths from the repo root).
5. `scripts/tauri.sh`: `#!/usr/bin/env bash`, comment "The Tauri CLI with one shared Cargo target dir, so each git worktree does not build its own ~3 GB copy.", `set -euo pipefail`, `export PATH="$HOME/.cargo/bin:$PATH"`, `export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/muse-tauri-target}"`, `cd "$(dirname "$0")/.."`, `exec web/node_modules/.bin/tauri "$@"`.
6. `scripts/smoke.sh`: the launch line runs `${1:-uv run python -m muse}` in place of `uv run python -m muse`, so `bash scripts/smoke.sh <binary>` smoke-tests a frozen sidecar; add that to the header comment.
7. Fixtures, each 1 s, 440 Hz, mono: run `ffmpeg -y -f lavfi -i sine=frequency=440:duration=1 -ac 1 -ar 44100 [-c:a aac|libvorbis] -metadata title="Smoke <ext>" -metadata artist="Muse Smoke" -metadata album="Smoke Fixtures" tests/fixtures/smoke/tone.<ext>` for mp3, flac, m4a (`-c:a aac`), wav, ogg (`-c:a libvorbis`); opus uses `-ar 48000 -c:a libopus`. Six files, about 140 KB total.
8. `scripts/shell_smoke.py` (argparse; docstring names the spec's packaging smoke, lines 645-654). Options: `--build`, `--binary PATH`, `--expect` (comma list, required), `--report-out PATH`. `--build` runs, in order, `npm --prefix web run build`, `uv run --group package python scripts/freeze_sidecar.py`, `bash scripts/tauri.sh build --debug --no-bundle`. Binary default: `<CARGO_TARGET_DIR or ~/.cache/muse-tauri-target>/debug/muse-shell`; append `.exe` on win32 when missing. Make a temp dir; write `data/settings.json` = `{"music_folders": [<abs tests/fixtures/smoke>], "downloader_unlocked": false}`; env adds `MUSE_DATA_DIR` and `MUSE_SMOKE_REPORT=<tmp>/report.json`. On Linux with no `DISPLAY`, start `Xvfb -displayfd <w> -screen 0 1280x800x24 -nolisten tcp` (an `os.pipe()` write end in `pass_fds`), read the display number from the pipe and set `DISPLAY=:<n>`; terminate Xvfb at the end. Run the binary; fail after 180 s. Fail when the report is missing. Print one row per format (`can_play_type`, `result`) plus `health` and `media_session`. Pass when `health == 200` and every `--expect` format has `result == "decoded"`. Then poll up to 15 s for no sidecar (`pgrep -x muse-core` exit 1; win32: `muse-core.exe` absent from `tasklist /FI "IMAGENAME eq muse-core.exe"`); a leftover fails. Copy the report to `--report-out` when given. On failure print the last 30 lines of `data/muse.log`. Final line `SHELL-SMOKE-OK`, or `SHELL-SMOKE-FAIL` with exit 1.
**New symbols:** `scripts/freeze_sidecar.py:main() -> int`; `scripts/shell_smoke.py:main() -> int`
**Verify:** `npm --prefix web ci` then `npm --prefix web run build` then `uv run --group package python scripts/freeze_sidecar.py` then `bash scripts/smoke.sh src-tauri/binaries/muse-core-x86_64-unknown-linux-gnu` then `uv run python scripts/shell_smoke.py --help` then `git status --short tests/fixtures/smoke`. Expected: freeze writes the binary (about 80 MB); smoke prints `SMOKE-OK`; `--help` exits 0; git lists 6 untracked fixtures (not ignored).
**Forbidden:** don't add pyinstaller to `[project] dependencies`; no other new deps; don't commit `build/` or `src-tauri/binaries/`; don't change smoke.sh's checks.
**Depends:** none
**Why:** Every Rust task verifies through these scripts as single plain commands (worktree sessions refuse compound commands). The shared target dir keeps a ~3 GB build from multiplying across worktrees on a 96%-full disk.

### Task 4: App icon, tray glyph and boot page [design]
- [x] status
**Objective:** `src-tauri/icons/icon.svg`, `src-tauri/icons/tray-template.svg` and `src-tauri/boot/index.html` exist per their Design sections.
**Write-scope:**
`src-tauri/icons/icon.svg` (new)
`src-tauri/icons/tray-template.svg` (new)
`src-tauri/boot/index.html` (new)
**Read-context:** `DESIGN.md:11-50`, `DESIGN.md:52-108`, `web/src/styles/tokens.css:1-40`
**Anchors:**
`export function meshCss(cols: number[][], where: Where): string {` @ `web/src/player/wash.ts`
**Steps:**
1. `icon.svg` per **Design: app-icon** (rounded rect, four radial gradients over #121117 in `<defs>`, three bars). Pure SVG, no filters, no text, no external refs.
2. `tray-template.svg` per **Design: app-icon** tray constraints: three `<rect>`s, `fill="#000"`, nothing else.
3. `boot/index.html` per **Design: boot-page**. Script: when `window.__TAURI__` is absent, show the starting state and stop. Else poll `window.__TAURI__.core.invoke("boot_status")` every 500 ms; on `state === "failed"` stop polling and render the failure view from `lines` and `log_path` with `textContent` only (never `innerHTML`). The shell navigates away on success, so no success state.
**New symbols:** none
**Verify:** `node ~/.claude/skills/design-brain/scripts/preflight.mjs src-tauri/boot/index.html` then `uv run python -c "import xml.dom.minidom as m; m.parse('src-tauri/icons/icon.svg'); m.parse('src-tauri/icons/tray-template.svg'); print('svg-ok')"`. Expected: preflight passes; `svg-ok`.
**Forbidden:** no external fonts, scripts or images in the boot page; no animation; no em dash; don't create other files under `src-tauri/` (Task 6 owns them).
**Depends:** none
**Design:** boot-page
**Why:** Task 6 needs the icon sources and the boot page to build. The `boot_status` shape is fixed in Assumptions, so this runs before the Rust exists.

### Task 5: Web shell bridge and injected token
- [x] status
**Objective:** The UI reads the shell's injected token, and the main window answers tray and mini-window events through one `ShellBridge` component.
**Write-scope:**
`web/src/shell.ts` (new)
`web/src/token.ts`
`web/src/components/ShellBridge.tsx` (new)
`web/src/App.tsx`
**Read-context:** `web/src/token.ts:1-34`, `web/src/App.tsx:30-50`, `web/src/player/PlayerContext.tsx:56-80`
**Anchors:**
`export const token: string | null = boot();` @ `web/src/token.ts`
`export function App() {` @ `web/src/App.tsx`
`export function usePlayer(): PlayerApi {` @ `web/src/player/PlayerContext.tsx`
**Steps:**
1. `shell.ts`: header comment "The desktop shell's API, or undefined in a plain browser. Tauri injects window.__TAURI__ (app.withGlobalTauri) into every window, the sidecar's pages included; nothing imports @tauri-apps/api." Export `type ShellCommand = "toggle" | "next" | "previous"`; `type NowState = { track: { track_rowid: number; track_name: string; artist_name: string; has_art: boolean } | null; playing: boolean }`; `type DragDrop = { type: "enter"; paths: string[] } | { type: "over" } | { type: "drop"; paths: string[] } | { type: "leave" }`; a `Tauri` type with `core.invoke<T>(cmd, args?)`, `event.listen<T>(event, cb: (e: { payload: T }) => void): Promise<() => void>`, `event.emitTo(target, event, payload?)`, `webview.getCurrentWebview().onDragDropEvent(cb: (e: { payload: DragDrop }) => void): Promise<() => void>`, `window.getCurrentWindow().close(): Promise<void>`, `dialog.open(o: { directory: boolean; multiple: boolean }): Promise<string | string[] | null>`; and `export const shell: Tauri | undefined = (window as unknown as { __TAURI__?: Tauri }).__TAURI__;`.
2. `token.ts`: first lines of `boot()`: read `(window as unknown as { __MUSE_TOKEN__?: string }).__MUSE_TOKEN__` and return it when set, comment "the desktop shell's init script (Decision 3)".
3. `ShellBridge.tsx`: `export function ShellBridge(): null`. With `shell` undefined it does nothing. Keep the latest `{toggle, next, previous}` and `NowState` in refs. Effect on `[current, playing]`: build `NowState` from `current`, store it, `void shell.event.emitTo("mini", "muse:now", state)`. Mount-only effect: `listen<ShellCommand>("muse:command", e => commands.current[e.payload]())` and `listen("muse:hello", () => void shell.event.emitTo("mini", "muse:now", now.current))`; cleanup calls both unlisteners.
4. `App.tsx`: render `<ShellBridge />` as the first child of `<PlayerProvider>`.
**New symbols:** `shell`, `ShellCommand`, `NowState`, `DragDrop` in `web/src/shell.ts`; `ShellBridge(): null`
**Verify:** `npm --prefix web ci` then `npm --prefix web run build` then `npm --prefix web test`. Expected: build green; 21 passed.
**Forbidden:** no npm deps (no `@tauri-apps/api`); don't touch `PlayerContext.tsx`; no UI markup.
**Depends:** none
**Why:** Contract-first: the Rust tasks and the three UI tasks share these event names and the token path, so they can run in parallel in Phase 2.

## Phase 2 - Shell and desktop UI

### Task 6: Tauri shell core: sidecar lifecycle, boot status, token, smoke mode
- [ ] status
**Objective:** `uv run python scripts/shell_smoke.py --build --expect mp3,flac,wav` builds and runs the shell headless and prints `SHELL-SMOKE-OK`.
**Write-scope:**
`web/package.json`
`web/package-lock.json`
`src-tauri/Cargo.toml` (new)
`src-tauri/Cargo.lock` (new)
`src-tauri/build.rs` (new)
`src-tauri/tauri.conf.json` (new)
`src-tauri/capabilities/default.json` (new)
`src-tauri/src/main.rs` (new)
`src-tauri/src/smoke.js` (new)
`src-tauri/icons/` (new)
**Read-context:** `scripts/shell_smoke.py` (from Task 3), `scripts/tauri.sh` (from Task 3), `src-tauri/boot/index.html` (from Task 4), `muse/__main__.py:34-62`, this plan's Assumptions
**Anchors:**
`def _exit_with_parent(server: uvicorn.Server) -> None:` @ `muse/__main__.py` (from Task 1)
**Steps:**
1. `npm --prefix web install -D @tauri-apps/cli@2.12.4`.
2. `Cargo.toml`: package `muse-shell` 0.1.0, edition 2021; build-dep `tauri-build = { version = "=2.7.4", features = [] }`; deps `tauri = { version = "=2.12.5", features = ["tray-icon", "image-png"] }`, `tauri-plugin-shell = "=2.6.0"`, `tauri-plugin-single-instance = "=2.5.5"`, `tauri-plugin-dialog = "=2.8.4"`, `getrandom = "0.3"`, `serde_json = "1"`.
3. `build.rs`: `tauri_build::try_build(tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(&["boot_status", "smoke_report", "open_mini", "show_main"]))).expect("failed to run tauri-build")`.
4. `tauri.conf.json`: `productName` "Muse", `version` "0.1.0", `identifier` "io.github.ianthemitten.muse", `build.frontendDist` "boot", `app` `{ "withGlobalTauri": true, "windows": [], "security": { "csp": null } }`, `bundle` `{ "active": true, "targets": ["app", "dmg", "nsis"], "icon": ["icons/32x32.png", "icons/128x128.png", "icons/128x128@2x.png", "icons/icon.icns", "icons/icon.ico"], "externalBin": ["binaries/muse-core"] }`.
5. `capabilities/default.json`: identifier `default`, windows `["main", "mini"]`, `remote.urls` `["http://127.0.0.1:*"]`, permissions `core:default`, `core:window:allow-close`, `core:window:allow-start-dragging`, `dialog:allow-open`, `allow-boot-status`, `allow-smoke-report`, `allow-open-mini`, `allow-show-main`.
6. Icons: `bash scripts/tauri.sh icon src-tauri/icons/icon.svg -o src-tauri/icons`, delete the generated `src-tauri/icons/android/` and `src-tauri/icons/ios/`, then `bash scripts/tauri.sh icon src-tauri/icons/tray-template.svg -o src-tauri/icons/tray -p 44`.
7. `main.rs`: `pub struct Shell { pub port: Option<u16>, pub failed: bool, pub lines: VecDeque<String>, pub log_path: PathBuf, pub data_dir: PathBuf, pub token: String, pub child: Option<CommandChild> }`, managed as `Mutex<Shell>`. Builder: `tauri_plugin_single_instance::init` first (show, unminimize, focus `main`), then shell and dialog plugins, `invoke_handler(generate_handler![boot_status, smoke_report])`. `setup`: data dir from `MUSE_DATA_DIR` else `app.path().app_data_dir()`, create it; rename `muse.log` to `muse.log.1` when over 5 MB; token = 32 `getrandom::fill` bytes as lowercase hex; init script `window.__MUSE_TOKEN__ = "<token>";` plus `include_str!("smoke.js")` when `MUSE_SMOKE_REPORT` is set; build window `main` (`WebviewUrl::App("index.html".into())`, title "Muse", 1200x800, min 360x560, the init script, `on_navigation` per Assumptions). Spawn `app.shell().sidecar("muse-core")` with env `MUSE_DATA_DIR`, `MUSE_TOKEN`, `MUSE_PARENT_WATCH=1`, `OPENBLAS_NUM_THREADS=1`, and in debug builds `MUSE_WEB_DIST = concat!(env!("CARGO_MANIFEST_DIR"), "/../web/dist")`. An async task appends every Stdout/Stderr line to `muse.log`, keeps the last 20 in `lines`, and on `MUSE_LISTENING <port>` sets `port` and navigates `main` to `http://127.0.0.1:<port>/`; `Terminated` before a port sets `failed` and pushes "Muse stopped (exit code <code>)". A second task sets `failed` with "Muse did not start within 90 seconds" when no port after 90 s. Run with `.build(...)` then `.run(...)`: on `RunEvent::Exit` take `child` out of the state and drop it (never `kill`).
8. Commands: `boot_status(state) -> serde_json::Value` per the Assumptions contract; `smoke_report(app, report: serde_json::Value) -> Result<(), String>`: Err when `MUSE_SMOKE_REPORT` is unset, else write `report.to_string()` there and `app.exit(0)`.
9. `smoke.js` per Assumptions "Smoke mode": runs on `DOMContentLoaded` only when `location.protocol === "http:"` and `location.pathname === "/"`; uses header `X-Muse-Token: window.__MUSE_TOKEN__`; polls `/api/library/songs` every 500 ms up to 60 times for 6 songs; per song an `Audio` with `preload="auto"`, `muted = true`, `src=/api/tracks/<id>/stream?t=<token>`, resolving `"decoded"` on `loadeddata`, `"error <code>"` on `error`, `"timeout"` after 10 s; report `{health, media_session, formats: {<format>: {can_play_type, result}}, error?}`; types: mp3 `audio/mpeg`, flac `audio/flac`, m4a `audio/mp4`, wav `audio/wav`, ogg `audio/ogg; codecs="vorbis"`, opus `audio/ogg; codecs="opus"`; finally `window.__TAURI__.core.invoke("smoke_report", { report })`.
**New symbols:** `struct Shell`; commands `boot_status`, `smoke_report`
**Verify:** `npm --prefix web ci` then `uv run python scripts/shell_smoke.py --build --expect mp3,flac,wav`. Expected: last line `SHELL-SMOKE-OK` (health 200, mp3/flac/wav `decoded`, no `muse-core` left).
**Forbidden:** never `kill()` the sidecar; never log or write the token; no tray, mini window or close handling (Task 10); no `@tauri-apps/api`; don't edit `src-tauri/boot/index.html` or the icon SVGs; no `cargo` without `scripts/tauri.sh` (shared target dir).
**Depends:** Task 1, Task 3, Task 4
**Why:** Decisions 2 and 3. Every API and permission named here was compiled and run in a scratch shell at planning (see Premises).

### Task 7: Mini window view [design]
- [x] status
**Objective:** `/mini` renders the mini window, which mirrors `muse:now` and sends `muse:command`, without mounting `App`.
**Write-scope:**
`web/src/views/MiniWindow.tsx` (new)
`web/src/styles/mini.css` (new)
`web/src/main.tsx`
**Read-context:** `web/src/shell.ts` (from Task 5), `web/src/main.tsx:1-15`, `web/src/components/ControlBar.tsx:1-60`, `web/src/components/Wash.tsx:63-80`, `web/src/components/CoverArt.tsx:15-50`, `DESIGN.md:204-240`
**Anchors:**
`export function useCardMesh(trackRowid: number | null, hasArt: boolean): string | undefined {` @ `web/src/components/Wash.tsx`
`export function CoverArt({` @ `web/src/components/CoverArt.tsx`
`export const shell: Tauri | undefined` @ `web/src/shell.ts` (from Task 5)
**Steps:**
1. `main.tsx`: render `location.pathname === "/mini" ? <MiniWindow /> : <App />` inside `StrictMode`; keep the font and style imports above.
2. `MiniWindow.tsx`: state `NowState` (initial `{track: null, playing: false}`). With `shell`: on mount `listen<NowState>("muse:now", ...)` then `emitTo("main", "muse:hello")`; buttons call `emitTo("main", "muse:command", "previous" | "toggle" | "next")`; art button `invoke("show_main")`; close button and Escape call `shell.window.getCurrentWindow().close()`. Background `useCardMesh(track?.track_rowid ?? null, track?.has_art ?? false)`. Art `CoverArt` with `src=/api/tracks/<id>/art`. Icons `PrevIcon`, `PlayIcon`/`PauseIcon`, `NextIcon`, `CloseIcon` from `../components/Icons`. Import `../styles/mini.css`.
3. Layout, states and copy per **Design: mini-window**.
**New symbols:** `MiniWindow(): JSX.Element`
**Verify:** `npm --prefix web ci` then `npm --prefix web run build` then `node ~/.claude/skills/design-brain/scripts/preflight.mjs web/src/views/MiniWindow.tsx web/src/styles/mini.css`. Expected: build green; preflight passes.
**Forbidden:** don't import `usePlayer`, `PlayerProvider`, `JobsProvider` or `App` into the mini window; no new tokens or colours; no npm deps.
**Depends:** Task 5
**Design:** mini-window
**Why:** Decision 16 (a): audio stays in the main window, the mini window only sends commands, so playback never moves between webviews.

### Task 8: Drop overlay and import call [design]
- [ ] status
**Objective:** Dragging files over the desktop window shows the overlay; a drop posts the paths to `/api/library/import` and shows a one-line result.
**Write-scope:**
`web/src/components/DropZone.tsx` (new)
`web/src/styles/drop.css` (new)
`web/src/dropMessage.ts` (new)
`web/src/dropMessage.test.ts` (new)
`web/src/api.ts`
`web/src/App.tsx`
**Read-context:** `web/src/shell.ts` (from Task 5), `web/src/api.ts:138-160`, `web/src/App.tsx:30-85`, `DESIGN.md:204-240`
**Anchors:**
`export async function putMusicFolders(folders: string[]): Promise<Settings> {` @ `web/src/api.ts`
`export function App() {` @ `web/src/App.tsx`
**Steps:**
1. `api.ts`: `export type ImportResult = { folders_added: number; songs_added: number; already: number; ignored: number };` and `export async function importPaths(paths: string[]): Promise<ImportResult>`, a `POST /api/library/import` with `{ paths }`, same shape and `refusal` handling as `putMusicFolders`.
2. `dropMessage.ts`: `export function dropMessage(r: ImportResult): string`. Added parts "N folder(s)" and "N song(s)" (singular at 1) joined by " and " after "Added "; nothing added and `already > 0`: "Already in your library"; else "Nothing to add. Muse plays MP3, M4A, FLAC, WAV, OGG and Opus files."
3. `dropMessage.test.ts` cases: `{1,0,0,0}` "Added 1 folder"; `{2,3,0,0}` "Added 2 folders and 3 songs"; `{0,1,0,0}` "Added 1 song"; `{0,0,2,0}` "Already in your library"; `{0,0,0,1}` the "Nothing to add" sentence; `{1,0,1,1}` "Added 1 folder".
4. `DropZone.tsx`: renders nothing without `shell`. Subscribes once to `shell.webview.getCurrentWebview().onDragDropEvent`: `enter`/`over` show the hover state, `leave` hides, `drop` calls `importPaths(paths)` and shows `dropMessage(result)` or the error's message, hidden after 2500 ms. Markup, states and motion per **Design: drop-overlay**; import `../styles/drop.css`.
5. `App.tsx`: render `<DropZone />` after `<NowPlayingSheet />` inside `SheetProvider`.
**New symbols:** `ImportResult`, `importPaths(paths: string[]): Promise<ImportResult>`, `dropMessage(r: ImportResult): string`, `DropZone(): JSX.Element | null`
**Verify:** `npm --prefix web ci` then `npm --prefix web test` then `npm --prefix web run build` then `node ~/.claude/skills/design-brain/scripts/preflight.mjs web/src/components/DropZone.tsx web/src/styles/drop.css`. Expected: 27 passed (21 + 6); build green; preflight passes.
**Forbidden:** no HTML5 `ondrop` handlers (they get no real paths); don't touch `ShellBridge` or `PlayerContext`; no npm deps.
**Depends:** Task 5
**Design:** drop-overlay
**Why:** Decision 15 drag-drop, UI half; the route is Task 2. The message logic is a pure function so vitest covers it in the node environment.

### Task 9: Native folder picker and mini player button [design]
- [ ] status
**Objective:** In the shell, Settings > Music folders offers "Choose folder…" (native picker) instead of the path field, and the control bar opens the mini window.
**Write-scope:**
`web/src/views/Settings.tsx`
`web/src/styles/settings.css`
`web/src/components/ControlBar.tsx`
`web/src/components/Icons.tsx`
**Read-context:** `web/src/shell.ts` (from Task 5), `web/src/views/Settings.tsx:53-180`, `web/src/components/ControlBar.tsx:76-117`, `web/src/components/Icons.tsx:1-30`, `web/src/components/Icons.tsx:170-176`
**Anchors:**
`function MusicFolders({` @ `web/src/views/Settings.tsx`
`export function ControlBar() {` @ `web/src/components/ControlBar.tsx`
`export const LyricsIcon = (props: Props) => (` @ `web/src/components/Icons.tsx`
**Steps:**
1. `Settings.tsx` `MusicFolders`: when `shell` is set, replace the `<form className="folder-add">` with a `.btn` "Choose folder…" that calls `shell.dialog.open({ directory: true, multiple: true })`, normalises the result to an array, returns on null, then `save([...folders, ...picked])`; under it the hint "You can also drag a folder onto this window." Keep the rescan button, `#folder-error` and the scan status. Without `shell`, render today's form unchanged.
2. `Icons.tsx`: add `export const MiniPlayerIcon = (props: Props) => (...)` in `LyricsIcon`'s stroke style: an outer rounded rect with a smaller filled-outline rect in its lower right.
3. `ControlBar.tsx`: in `.player-side`, before the Lyrics button, when `shell` is set: `<button className="icon-btn stroke" onClick={() => void shell.core.invoke("open_mini")} aria-label="Open mini player"><MiniPlayerIcon /></button>`.
4. Styling per **Design: folder-picker** in `settings.css` (hint text only if needed).
**New symbols:** `MiniPlayerIcon`
**Verify:** `npm --prefix web ci` then `npm --prefix web run build` then `node ~/.claude/skills/design-brain/scripts/preflight.mjs web/src/views/Settings.tsx web/src/components/ControlBar.tsx`. Expected: build green; preflight passes.
**Forbidden:** don't change `putMusicFolders` or the server rules; don't remove the browser form; no npm deps.
**Depends:** Task 5
**Design:** folder-picker
**Why:** M1 deferred the native picker to M3 (`docs/plans/2026-10-29-desktop-m1-core.md:709`). The mini button is how a pointer user reaches Decision 16's mini window without the tray.

## Phase 3 - Tray, windows and packaging

### Task 10: Tray, mini window command, close to tray, single instance
- [ ] status
**Objective:** The shell has the tray menu, `open_mini`/`show_main`, hide-on-close with a one-time notice, dock reopen and second-launch focus, and the smoke still passes.
**Write-scope:**
`src-tauri/src/main.rs` (from Task 6: edit it; marked (new) only because it does not exist at plan time)
`src-tauri/src/tray.rs` (new)
**Read-context:** `src-tauri/src/main.rs` (from Task 6), this plan's Assumptions (Close behaviour, Tray menu, Mini window, Events)
**Anchors:**
`pub struct Shell` @ `src-tauri/src/main.rs` (from Task 6)
**Steps:**
1. `tray.rs`: `pub fn build(app: &AppHandle) -> tauri::Result<()>`: `MenuItem::with_id` items `toggle` "Play/Pause", `next` "Next", `mini` "Mini player", `show` "Show Muse", `quit` "Quit Muse", plus `PredefinedMenuItem::separator` before quit; `Menu::with_items`; `TrayIconBuilder::with_id("muse").tooltip("Muse").menu(&menu).show_menu_on_left_click(true)`; macOS: `Image::from_bytes(include_bytes!("../icons/tray/44x44.png"))` and `icon_as_template(true)`, else `app.default_window_icon()`; `on_menu_event`: toggle/next `app.emit_to("main", "muse:command", id)`, mini `open_mini`, show `show_main`, quit `app.exit(0)`.
2. `tray.rs` commands: `#[tauri::command] pub fn show_main(app: AppHandle)` (show, unminimize, set_focus on `main`); `#[tauri::command] pub fn open_mini(app: AppHandle) -> Result<(), String>`: show and focus an existing `mini`; Err("Muse is still starting") when `port` is None; else `WebviewWindowBuilder::new(&app, "mini", WebviewUrl::External("http://127.0.0.1:<port>/mini"))` with title "Muse", 400x96, `resizable(false)`, `decorations(false)`, `always_on_top(true)`, `skip_taskbar(true)`, the same token init script and `on_navigation` guard as `main`.
3. `main.rs`: `mod tray;`; add `tray::open_mini, tray::show_main` to `generate_handler!`; call `tray::build` in `setup`; the single-instance callback calls `tray::show_main`.
4. `main.rs` close: `main`'s `on_window_event` `CloseRequested`: when `port` is None do nothing (window closes, app quits); else `api.prevent_close()`, hide `main`, and when `<data_dir>/.tray-notice-shown` is absent create it and show `app.dialog().message(<text>).title("Muse").show(|_| {})` with the Assumptions copy (`cfg!(target_os = "macos")` picks "menu bar").
5. `main.rs` run loop: `#[cfg(target_os = "macos")] RunEvent::Reopen { .. }` calls `tray::show_main`.
**New symbols:** `tray::build`, `tray::show_main`, `tray::open_mini`
**Verify:** `uv run python scripts/shell_smoke.py --build --expect mp3,flac,wav`. Expected: `SHELL-SMOKE-OK`.
**Forbidden:** never `kill()` the sidecar; no new crates or features; don't change `boot_status`, `smoke_report` or the token flow; no `cargo` without `scripts/tauri.sh`.
**Depends:** Task 6
**Why:** Decision 16 and Decision 3's single instance. These calls compiled and ran headless in the planning scratch shell; the hand checks are in "Owner checks".

### Task 11: Bundle config, NSIS hook, CI packaging workflow
- [ ] status
**Objective:** The bundle carries the Windows pre-install kill hook, and `.github/workflows/package.yml` builds and smoke-tests on `macos-15` and `windows-latest`.
**Write-scope:**
`src-tauri/tauri.conf.json` (from Task 6: edit it; marked (new) only because it does not exist at plan time)
`src-tauri/windows/hooks.nsh` (new)
`.github/workflows/package.yml` (new)
**Read-context:** `src-tauri/tauri.conf.json` (from Task 6), `scripts/shell_smoke.py` (from Task 3), this plan's Assumptions (CI trigger, Formats expected)
**Anchors:**
`def main() -> int:` @ `scripts/shell_smoke.py` (from Task 3)
**Steps:**
1. `hooks.nsh`: comment "Decision 2: a running sidecar keeps its exe locked, so a reinstall can silently keep the old one (tauri-apps/tauri#15134). Stop it before files are copied." then `!macro NSIS_HOOK_PREINSTALL` / `nsExec::Exec 'taskkill /F /T /IM muse-core.exe'` / `Pop $0` / `!macroend`.
2. `tauri.conf.json` `bundle`: add `"category": "Music"` and `"windows": { "nsis": { "installerHooks": "windows/hooks.nsh" } }`.
3. `package.yml`: header comment "Builds the desktop app on the two release targets and smoke-tests it in the real webview. Manual or tag-triggered only: macOS minutes count 10x on a private repo." `on`: `workflow_dispatch` and `push.tags: ["v*", "ci-package-*"]`. One job `package`, matrix `os: [macos-15, windows-latest]`, `fail-fast: false`, `timeout-minutes: 60`, `defaults.run.shell: bash`. Steps: `actions/checkout@v7`; `astral-sh/setup-uv@v10`; `actions/setup-node@v7` with `node-version: 22`; `dtolnay/rust-toolchain@stable`; `npm --prefix web ci`; `npm --prefix web run build`; `uv run --group package python scripts/freeze_sidecar.py`; `web/node_modules/.bin/tauri build`; `actions/upload-artifact@v7` named `bundle-${{ matrix.os }}` with `src-tauri/target/release/bundle/dmg/*.dmg` and `src-tauri/target/release/bundle/nsis/*.exe`, `if-no-files-found: error`; `uv run python scripts/shell_smoke.py --binary src-tauri/target/release/muse-shell --expect mp3,flac,m4a,wav --report-out smoke-report.json`; `if: always()` upload `smoke-report.json` as `smoke-${{ matrix.os }}`.
**New symbols:** none
**Verify:** `uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('.github/workflows/package.yml')); print('yaml-ok')"` then `uv run python scripts/shell_smoke.py --build --expect mp3,flac,wav`. Expected: `yaml-ok`; `SHELL-SMOKE-OK` (config still valid).
**Forbidden:** no signing or notarization steps; no Linux matrix leg (owner: dev only); no `pull_request` or branch `push` triggers; don't edit `main.rs`.
**Depends:** Task 6
**Why:** Decision 2's NSIS rule and the spec's packaging smoke (lines 645-654). The macOS leg is the only check on WKWebView codecs and Media Session.

## Phase 4 - Proof and docs

### Task 12: Desktop shell docs [docs]
- [ ] status
**Objective:** README, CLAUDE.md, CONTEXT.md and DESIGN.md describe the shell, packaging and the new surfaces as built.
**Write-scope:**
`README.md`
`CLAUDE.md`
`CONTEXT.md`
`DESIGN.md`
**Read-context:** `README.md:1-80`, `CLAUDE.md:1-60`, `CONTEXT.md:1-60`, `DESIGN.md:317-360`, this plan's Assumptions, `scripts/tauri.sh` (from Task 3), `scripts/shell_smoke.py` (from Task 3)
**Anchors:**
`def main() -> int:` @ `scripts/freeze_sidecar.py` (from Task 3)
**Steps:**
1. README: a "Desktop app (alpha)" section: build from source on Linux for development (`npm --prefix web ci`, `npm --prefix web run build`, `uv run --group package python scripts/freeze_sidecar.py`, `bash scripts/tauri.sh dev`); macOS and Windows builds come from the `package` workflow; builds are unsigned (macOS: right-click, Open; Windows: More info, Run anyway); where the data dir and `muse.log` live per platform; the tray and mini player in one sentence each.
2. CLAUDE.md: Commands gains the freeze, `bash scripts/tauri.sh dev|build --debug --no-bundle` and `uv run python scripts/shell_smoke.py --build --expect mp3,flac,wav` lines; Architecture gains a short "The desktop shell" paragraph (boot page, loopback navigation, injected token, parent watch, one capability plus `AppManifest`, events and commands).
3. CONTEXT.md: add terms **Shell**, **Sidecar**, **Parent watch**, **Mini window** (and why it is not "mini player", which is `ControlBar`), **Dropped song** (`music_files`).
4. DESIGN.md §9: add Mini window, Drop overlay, Boot page and the app icon and tray glyph, each one or two sentences matching the Design sections of this plan; z-index list gains the drop overlay at 50.
**New symbols:** none
**Verify:** `grep -n "Desktop app (alpha)" README.md` then `grep -n "shell_smoke" CLAUDE.md` then `grep -n "Mini window" CONTEXT.md DESIGN.md`. Expected: each prints at least one line.
**Forbidden:** every claim checked against the code; no plan or spec paths in README; don't document M4/M5 features as present; no em dash in UI copy quoted from the app.
**Depends:** Task 7, Task 8, Task 9, Task 10, Task 11
**Why:** CLAUDE.md requires CONTEXT.md to change when terms do; M4 executors read these first.

### Task 13: Run the CI packaging matrix [experiment]
- [ ] status
**Objective:** The `package` workflow runs on macOS and Windows, and its results are recorded in `docs/plans/2026-10-29-desktop-m3-ci-results.md`.
**Write-scope:**
`docs/plans/2026-10-29-desktop-m3-ci-results.md` (new)
**Read-context:** `.github/workflows/package.yml` (from Task 11), this plan's Assumptions (CI trigger, Formats expected, Deferred spec items)
**Anchors:**
`def main() -> int:` @ `scripts/shell_smoke.py` (from Task 3)
**Steps:**
Negative result is a valid outcome.
1. Commit any pending work, then `git push origin HEAD:refs/heads/m3-ci`, then `git tag ci-package-m3-1`, then `git push origin ci-package-m3-1`.
2. `gh run list --workflow package.yml --limit 1 --json databaseId,status` to get the run id; `gh run watch <id>` until done; `gh run download <id> -D build/ci` for the smoke reports.
3. Write the results file: per OS, pass/fail of each step that ran, the smoke table (format, `can_play_type`, `result`), `health`, `media_session`, whether `muse-core` was left, bundle file names and sizes, run URL and duration. When a leg fails, quote the failing step's last 30 log lines (`gh run view <id> --log-failed`).
4. If a fix is obvious, do not apply it; list it under "Follow-ups" in the results file.
**New symbols:** none
**Verify:** `test -s docs/plans/2026-10-29-desktop-m3-ci-results.md` then `gh run view <id> --json conclusion`. Expected: the file exists and is non-empty; the conclusion is recorded in it, whatever it is.
**Forbidden:** don't edit code, the workflow or other files; no re-runs beyond one retry of an infrastructure failure (runner lost, download timeout); don't push to `main`; don't delete the tag.
**Depends:** Task 10, Task 11
**Why:** The owner chose CI-only verification for macOS and Windows, run once at the end. WKWebView codecs and Media Session are unconfirmed (spec Decision 12 and Deferred items), so a red leg is information, not a broken task.
