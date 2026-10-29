# Plan - Muse desktop M1: fork baseline, loopback server, folder library

Source spec: `docs/specs/2026-10-29-desktop-app-fork.md` (Status: ready for planning; `spec-lint --handoff` exit 0, 1 warn no-source-prd). It is a copy of the spec in the private repo.

Repo: `~/Projects/muse-desktop` (private GitHub `IanTheMitten/muse-desktop`, branch `main`). The cut was made at planning time: `git archive` of muse `main` at c339b4d, then the private directories and root scripts nothing imports were deleted (commit 6ff8cd3). Every Python command runs from the repo root. Every npm command runs from `web/`.

**Release rule:** the public release is a squash of this repo that drops `docs/plans/` and `docs/specs/`. They hold owner paths and must never ship.

## Milestones (owner answer 2026-10-29: milestone plans, M1 now)

- **M1 (this plan):**
  - Python 3.12 pin and the strip of mirror, manifest, contract and backup code.
  - Config reshape around one data dir.
  - Loopback bind with a per-launch token, and MIME per format.
  - Lyrics stored in the data dir.
  - The folder scanner, folder watcher and launch chain (scan, LRCLIB, art, taste retrain).
  - Settings > Music folders.
  - "File missing" and "Format not supported" labels.
  - Fork docs.
- **M2:** tag overrides, delete/tombstone UI, hide artist, art and artist-image upload, lyric editing and invalidation, the playlists tombstone rule, queue editing, bulk select, gapless/crossfade, sort/filter, the duplicate finder, and a web test runner for the `PlayerContext` tests.
- **M3:** Tauri shell, sidecar packaging, tray, mini-player, drag-drop, native folder picker, single instance, the CI packaging matrix. Owner question carried: verify by CI only or install Rust locally. Owner has no budget, so the alpha is unsigned. Private-repo Actions minutes on macOS count 10x.
- **M4:** ML worker, smart-features installer, Laya in-worker replacing `muse/systemone.py` and the hosted Jev path in `muse/lyric_tag_batch.py`, retiring `universal_reco/`, aligners. Owner said "Decide at M4" for where the worker is built.
- **M5:** the downloader unlock and Add.

## Assumptions (decided at planning)

- **The play sweeper stays.** Spec line 74 lists the "heartbeat sweeper" among the Cadence parts to drop. But the web player sends its own heartbeats (`web/src/player/sync.ts:9` `HEARTBEAT_MS = 10_000`, `web/src/player/PlayerContext.tsx:732`). Spec line 567 keeps "ADR 0001 unchanged". Without the sweeper, a crashed tab leaves a span open forever. Only `GET /api/contract` is Cadence-only, and it goes.
- **Token transport follows Decision 3 exactly.**
  - Every `/api` request sends the header `X-Muse-Token`.
  - Every URL a media element, `Image` or `EventSource` loads carries `?t=<token>`.
  - The server accepts either, compared with `hmac.compare_digest`, and only checks paths under `/api`. The SPA shell and `/assets` stay open.
  - `Config.token = None` disables the check (tests only).
  - `python -m muse` always runs with a token: `MUSE_TOKEN`, or a fresh `secrets.token_urlsafe(32)`.
  - The UI gets its first token from `?t=` on the page URL, stores it in `sessionStorage["muse-token"]`, and strips it with `history.replaceState`, so a reload keeps working (LAYOUT invariant 5). M3 replaces the bootstrap with Tauri's init script.
- **Port.** The sidecar binds `127.0.0.1` on `Config.port` (default 0). After binding it prints exactly `MUSE_LISTENING <port>` on stdout, so the M3 shell reads the port with no race. There is no `MUSE_HOST`; loopback only.
- **Data dir.** `MUSE_DATA_DIR` defaults to `<repo>/data` (gitignored). Every store sits under it: `library.sqlite3`, `art/`, `lyrics/`, `downloads/` (`Config.music_root`) and `settings.json`. Each one also takes its own `MUSE_*` override. The M3 shell passes the per-user app-data path through `MUSE_DATA_DIR`, with no platformdirs dependency.
- **`settings.json` holds the music folders.** Its shape is `{"music_folders": [abs paths], "downloader_unlocked": false}`. A missing or corrupt file reads as those defaults. Writes are atomic: a temp file, then `os.replace`. M1 never writes `downloader_unlocked`. Nav shows Add only when it is true.
- **One background chain, serialised by one `asyncio.Lock`.** The shared connection is per-statement locked (`muse/library/db.py:48-76`) and `record_cover` uses `BEGIN`/`COMMIT` on it (`muse/library/art.py:84-117`), so two background jobs in threads at once can collide in a transaction. Launch, the watcher and `POST /api/scan` therefore all run one coroutine, under the lock, in this order:
  1. scan
  2. LRCLIB sync (`config.lyrics_sync`)
  3. art backfill (`config.art_backfill`), which replaces the existing launch-time art backfill task
  4. taste retrain, at launch only, when `config.taste_retrain_at_launch` is set and the last done `taste_retrain` job is more than 86400 s old or absent

  The scan creates no job row when no configured folder exists. M1 does not scan `downloads/` (`Config.music_root`); M5 adds it once unlocked (spec line 96). That keeps every existing `/api/jobs` assertion intact.
- **Watcher.** It uses `watchfiles.awatch(*existing_roots, stop_event=..., rust_timeout=60000, yield_on_timeout=True)`.
  - It watches only roots that exist.
  - It restarts when the folder list changes (on `PUT`).
  - On each 60 s timeout it re-checks which roots exist. A root that appeared or vanished triggers the chain and a restart. That is how a re-plugged drive gets restored.
  - With no existing roots, it sleeps 60 s and re-checks.
- **Scanner identity (Decision 9).**
  - Path first. A row whose `file_size` and `file_mtime` are unchanged and whose status is `done` is not re-read.
  - A vanished row and a new file with equal `(file_size, duration_ms, artist_name, album_name, track_name)` in one scan count as a move.
  - A file gone from a present folder becomes `missing_file`. All rows under an absent folder become `missing_file` and are never move-matched.
  - A path whose row has `removed_at` set is skipped. M1 adds the column; M2 adds the delete UI.
  - New rows use `origin='local'`, `metadata_tier='tags'`, `track_id=NULL`, `artist_rowid=NULL`. There is no CHECK constraint on either column (`muse/library/schema.py:20-26`).
  - Albums are keyed `album_key = f"{(album_artist or artist).casefold()}\x1f{album.casefold()}"`, with `album_rowid` allocated above the 1e9 floor.
- **Sidecars and embedded art, on insert only.**
  - `<stem>.lrc` is copied to `lyrics/<rowid>.lrc`.
  - The first of `<stem>.lyrics.txt` or `<stem>.txt` is copied to `lyrics/<rowid>.txt` and becomes `tracks.lyrics_path`.
  - When only an `.lrc` exists, `lyrics/<rowid>.txt` is written from `strip_timestamps` of it.
  - Then `lyrics_state` is set to `synced` (when there is an `.lrc`) or `plain`, so `pending()` (`muse/library/lyrics_state.py:19-40`) never sends that track to LRCLIB.
  - Embedded art is written once per album without an `album_art` row: `art/<album_rowid><ext>` through `record_cover(..., source="embedded")`.
- **Tags.** `mutagen.File(path, easy=True)` gives easy keys for mp3/flac/ogg/opus/m4a. For WAVE it returns raw ID3 frames (verified, see Premises), so WAVE reads frames `TIT2`, `TPE1`, `TALB`, `TPE2`, `TRCK`, `TPOS` and `TSRC` by id. Embedded pictures:
  - ID3 `APIC` (mp3, wav)
  - FLAC `.pictures`
  - MP4 `covr`
  - none for Ogg in v1
- **Formats.** The scanner imports `.m4a .mp3 .flac .wav .ogg .opus`. MIME types are `audio/mp4`, `audio/mpeg`, `audio/flac`, `audio/wav`, `audio/ogg`, and `audio/ogg` for `.opus`. A new `tracks.format` column (lowercase extension without the dot, NULL for rows not from the scanner) rides in `TRACK_FIELDS`. The UI calls `canPlayType` once per format. NULL format counts as playable.
- **Moved to later milestones, not stripped in M1:**
  - Genius (`build_genius_client`, `lyricsgenius`), the discover post-freeze filter (`muse/discover.py:150-170`), `Config.catalog_freeze`, and `muse/ingest.py` writing lyrics beside the audio all go to M5.
  - `muse/systemone.py`, `muse/lyric_tag_batch.py`, `Config.laya_*`/`curator_*` and `universal_reco/` go to M4.
  - The download pipeline stays dormant: no yt-dlp is installed. Scanning `downloads/` goes to M5.
- **Deferred spec items decided here:** ffmpeg is needed after unlock, because `muse/ingest.py:213` uses `FFmpegExtractAudio`. M5 fetches a static ffmpeg the same way it fetches yt-dlp. The remaining Deferred items belong to M3/M4/M5.

## Premises

- The spec passes handoff lint. Verified: `node ~/.claude/skills/spec-brainstorming/scripts/spec-lint.mjs --handoff docs/specs/2026-10-29-desktop-app-fork.md` (exit 0).
- Backend baseline in the fork on 3.14: 1150 passed, 2 skipped, 2 deselected. Verified: `uv run pytest -p no:cacheprovider` in `~/Projects/muse-desktop`.
- The same tree on Python 3.12 also passes 1150. Verified: a `git archive main` export in `~/tmp-desktop-312`, `requires-python` set to `>=3.12`, `uv sync --python 3.12`, pytest.
- 3.12 wheels resolve for `aarch64-apple-darwin` and `x86_64-pc-windows-msvc`: torch, torchaudio, implicit 0.7.3, scipy, pyinstaller 6.22.3, mutagen 1.48.1, send2trash 2.1.0, watchfiles 1.3.0. `ctc-forced-aligner` and `g2p-en` are sdist-only (an M4 concern). Verified: `uv pip compile --python-version 3.12 --python-platform <triple> --only-binary torch,torchaudio,implicit,scipy,numpy,pyinstaller,watchfiles,tokenizers,safetensors`.
- `watchfiles` is already installed through `uvicorn[standard]`, and `awatch` has `rust_timeout` and `yield_on_timeout`. Verified: `inspect.signature(watchfiles.awatch)` on 1.2.0.
- `mutagen.File(wav, easy=True)` returns a `WAVE` whose tags are raw ID3 frames (`TIT2`, `TPE1`, ...), and `info.length` is in seconds. Verified: a stdlib `wave` file tagged with mutagen 1.48.1 in the scratchpad.
- Frontend builds green. Verified: `cd web && npm ci && npm run build` (index JS 318.65 kB). No web test runner or linter: `web/package.json` scripts are dev/build/preview.
- `/api/catalog/*` and the `track`/`album`/`artist` branches of `POST /api/jobs` are already gone on main. The kinds are `mb`, `mb_release` and `url` (`muse/api.py:1350-1527`). The catalog is still read by `_open_catalog` (`muse/api.py:269`), `repair_joined_credits` (`muse/api.py:471`), `TieredMetadataProvider` (`muse/api.py:583`), `nearest.recommended_albums` (`muse/api.py:519`) and `track_id_for_isrc` (`muse/api.py:1518`).
- `muse/library/tracks.py:19` imports `_index_track` (the FTS writer) from `importer.py`, so deleting the importer must move it.
- 18 test files construct `Config(...)` by hand. `tests/test_systemone.py`'s 5 hits are `FakeConfig(`, not `Config`. Verified: `grep -c "Config(" tests/*.py`, `grep -n "Config(" tests/test_systemone.py`.
- Tests that seed rows through `import_manifest`: `tests/test_art.py`, `tests/test_search.py`, `tests/test_plays.py`, `tests/test_ratings.py`, `tests/test_ingest_run_download.py`, plus the whole-subject files `tests/test_import.py`, `tests/test_backup.py`, `tests/test_contract_shapes.py`. Verified: `grep -c "import_manifest\|make_manifest\|make_record" tests/*.py`.
- Schema is at version 20 (`MIGRATIONS` ends `_m020_reask_missing_art`, `muse/library/schema.py:675-697`). Tests pin the version only through `LATEST_VERSION` (`tests/test_migrations.py:29`).
- `lyrics_state` states `synced` and `plain` exist (`muse/lyrics_lrclib.py:189`, `muse/lyrics_lrclib.py:201`). `upsert` commits (`muse/library/lyrics_state.py:106-140`).
- No test pins a whole track dict, so adding `format` to `TRACK_FIELDS` breaks no assertion. The only `"gain_db"`/`"has_lyrics"` hits are in `tests/test_import.py`, which Task 3 deletes. Verified: `grep -rln '"has_lyrics"\|"gain_db"' tests`.
- Jobs timestamps are unix seconds (`muse/library/jobs.py:25-26`).
- Chart rows carry no image URL, so the UI loads no third-party art (`muse/charts.py:39`).
- `TrackRow` already labels `playable=false` rows "File missing" (`web/src/components/TrackRow.tsx:22`). `playable` is set server-side as `status == "done"` (`muse/library/queries.py:45`).
- `useJobs().job` is the latest event of any kind, and `JobProgress` labels every job "Fetching cover art" (`web/src/components/JobProgress.tsx:31-37`).

## Memory carried into briefs

- **FKs are immediate:** insert the `tracks` row before its `lyrics_state` or `album_art` row.
- **No relative paths in tests:** `insert_track`'s default `file_path` is relative `MUSIC/<rowid>.m4a`. Scanner and lyrics tests use absolute `tmp_path` paths only, and every `make_config` path sits under `tmp_path`.
- **`pytest` addopts already has `-q`:** run `uv run pytest -p no:cacheprovider` and read the last line.
- **Exit 139 at pytest teardown** in the catalog/browse API tests is a known flake. Rerun once before treating it as a regression.
- **`scope-check` collapses a new directory** into `?? dir/`. Re-check with `git status -uall`.
- **Commit with `git commit -- <paths>`** when the task deleted files.

## Design: settings

READ: Reading this as: a product settings screen for one listener at a desk or on a phone, quiet utilitarian language, leaning toward the existing Muse record-wash system (DESIGN.md, LAYOUT.md).
DIALS: VARIANCE 3 / MOTION 2 / DENSITY 4
PALETTE: existing tokens only, drenched-by-the-record rung inherited: --ink #121117 / #f4f3f6, --text, --text-2, --hairline, --accent (per record; neutral #e9e6e1 / #2b2830 with no record), --on-accent. No new colour.
TYPE: display Newsreader Variable (page title only, via .page-title) / body Figtree Variable (everything else)
SIGNATURE: each music folder is one row with its full path in tabular Figtree, a live count "N songs" or the plain state "Not connected" when the folder is absent, and a quiet Remove text button. The list reads like a mount table, not a form.
ANCHORS: Plexamp settings (one column of plain labelled sections, no cards), Apple Music for Mac "Files" pane (folder path shown in full, one action per row), macOS System Settings (section headings above grouped rows, hairline separators)
VARIETY: cohere, no rotation. The ledger's 2026-09-03 entry says "Same product as every later Muse build: cohere with this system rather than rotating away from it". Previous fingerprint: labelled rail, art-led shelves, Newsreader + Figtree, record wash.
CONTENT: real copy.
  - Page title "Settings".
  - Section "Music folders" with the help line "Muse reads these folders and never changes the files in them."
  - Input label "Folder path", button "Add folder", per-row "Remove", section button "Rescan now".
  - Scan status line from the scan job: "Scanning · 120 of 800", then the job message when it ends.
  - Empty state: "No folders yet. Add the folder your music lives in."
  - Errors: the 400 `detail` text from the server, shown under the input.
  - No placeholder data.
FORMAT: production
TARGET: existing stack: web/src/views/Settings.tsx (new) + web/src/styles/settings.css (new), route /settings, nav entry. React 19 + TS + Vite.
CONSTRAINTS: WCAG AA. Label above input, never placeholder-as-label. Visible focus. 44 px tap targets (LAYOUT invariant 4). No em dash in UI copy. Reuse .section, .section-head, .field, .btn, .btn.primary from web/src/styles/base.css:21-41. The mobile floor holds at 320 px: the path wraps with overflow-wrap:anywhere, no horizontal scroll. No modal and no toast: the status sits inline. Animate only opacity/transform, with a prefers-reduced-motion fallback.

## Design: track-row

READ: Reading this as: a state change on an existing list row for one listener, plain honest language, leaning toward the existing Muse record-wash system.
DIALS: VARIANCE 2 / MOTION 1 / DENSITY 5
PALETTE: existing tokens only: the label uses --text-2 like .row-sub today. The row is dimmed exactly as aria-disabled rows are now. No new colour; state never rides on colour alone (DESIGN.md §3).
TYPE: display Newsreader Variable (unchanged, not used in rows) / body Figtree Variable
SIGNATURE: the reason replaces the subtitle in place. "File missing" or "Format not supported on this Mac" / "on this PC" sits where the artist line was, so the row says why it will not play instead of failing silently.
ANCHORS: Apple Music greyed "not available" rows (reason replaces the subtitle), Plexamp unavailable-track dimming (row stays in place, keeps its number), Spotify local-files rows (same row shape, playback simply unavailable)
VARIETY: cohere, no rotation (same ledger note as settings).
CONTENT: the two exact strings "File missing" and "Format not supported on this Mac" (or "on this PC"). No placeholders.
FORMAT: production
TARGET: existing stack: web/src/components/TrackRow.tsx plus web/src/player/formats.ts (new), and the call sites that pass `playable`.
CONSTRAINTS: WCAG AA. aria-disabled stays. The label must be in the accessible name (it is text in the button). No em dash. No layout shift between playable and unplayable rows.

## Phase 1 - Fork baseline

### Task 1: Pin Python 3.12 and move mutagen, watchfiles and the taste deps into core [mechanical]
- [ ] status
**Objective:** The project resolves and tests on Python 3.12, with `mutagen`, `watchfiles`, `implicit` and `numpy` as core dependencies.
**Write-scope:**
`pyproject.toml`
`uv.lock`
`.python-version` (new)
`.gitignore`
**Read-context:** `pyproject.toml:1-40`, `.gitignore:1-30`
**Anchors:**
`requires-python = ">=3.14"` @ `pyproject.toml`
**Steps:**
1. In `pyproject.toml`, set `requires-python = ">=3.12"`.
2. In `[project] dependencies`, after `"requests",`, add `"mutagen",`, `"watchfiles",`, `"implicit",` and `"numpy",`.
3. Delete the whole `taste = [...]` optional group and the comment block directly above it. Leave `embed` and `ingest` untouched.
4. Create `.python-version` containing the single line `3.12`.
5. Append to `.gitignore`: a comment line `# Desktop data dir (Config.data_dir default)` and then `/data/`.
6. Run `uv lock`, then `uv sync`. The existing `.venv` is 3.14; `uv sync` recreates it on 3.12.
**New symbols:** none
**Verify:** `uv run python -c "import sys, mutagen, watchfiles, implicit; assert sys.version_info[:2] == (3, 12), sys.version"` and then `uv run pytest -p no:cacheprovider`. Expected: no assertion error; the last line shows `1150 passed` and 0 failed (skip/deselect counts may shift now that `implicit` is installed).
**Forbidden:** don't touch `universal_reco/pyproject.toml`; no other dependency changes; don't run `uv sync --extra <one>`.
**Depends:** none
**Why:** Decision 14 picks 3.12. Decisions 9 and 13 put mutagen, the file watcher and the ALS retrain in the core sidecar.

### Task 2: Seed test rows with insert_track instead of the manifest importer
- [ ] status
**Objective:** No test outside `tests/test_import.py`, `tests/test_backup.py` and `tests/test_contract_shapes.py` calls `import_manifest`, `make_manifest` or `make_record`, and the suite stays at 1150 passed.
**Write-scope:**
`tests/conftest.py`
`tests/test_art.py`
`tests/test_search.py`
`tests/test_plays.py`
`tests/test_ratings.py`
`tests/test_ingest_run_download.py`
**Read-context:** `tests/conftest.py:41-145`, `tests/test_art.py:50-70`, `tests/test_search.py:20-40`, `tests/test_plays.py:18-30`, `tests/test_ratings.py:15-25`, `tests/test_ingest_run_download.py:640-700`
**Anchors:**
`def insert_track(conn, rowid, *, track_id="auto", status="done", album_rowid=20,` @ `tests/conftest.py`
`def make_record(track_rowid, status="done", file_path=None, **overrides):` @ `tests/conftest.py`
**Steps:**
1. In `tests/conftest.py`, give `insert_track` the extra keyword arguments the five files need, each defaulting to today's behavior: `isrc=None`, `track_no=1`, `disc_no=1`, `file_path="auto"`, where `"auto"` keeps the current `f"MUSIC/{rowid}.m4a" if status == "done" else None`. After the INSERT, call `_index_track(conn, rowid, track_name, artist_name, album_name)` imported from `muse.library.tracks`, so FTS search finds seeded rows the way the importer did. Keep `make_manifest`/`make_record` for now; Task 3 deletes them.
2. In each of `tests/test_art.py`, `tests/test_search.py`, `tests/test_plays.py` and `tests/test_ratings.py`, replace every `import_manifest(make_manifest(...make_record(...)...), FakeCatalog(), conn)` seeding call with one `insert_track(conn, rowid, ...)` per record, carrying the same field values. Remove the now-unused imports.
3. In `tests/test_ingest_run_download.py`, delete `test_a_manifest_error_holder_keeps_its_id_and_the_new_row_goes_unstamped` (line 661, the only test there that calls `import_manifest`; its subject is the importer). Then remove the unused imports.
**New symbols:** `insert_track(..., isrc=None, track_no=1, disc_no=1, file_path="auto")` keyword parameters in `tests/conftest.py`
**Verify:** `uv run pytest -p no:cacheprovider` and then `grep -rlE "import_manifest|make_manifest|make_record" tests | sort`. Expected: pytest's last line reads `passed` with 0 failures (1150 minus the deleted manifest tests); grep prints exactly `tests/conftest.py`, `tests/test_backup.py`, `tests/test_contract_shapes.py` and `tests/test_import.py`.
**Forbidden:** don't edit anything under `muse/`; don't weaken an assertion to make a reseeded test pass. If a test fails only after reseeding, the seed is wrong: compare field by field with `make_record`'s defaults.
**Depends:** Task 1
**Why:** Task 3 deletes the importer. Tests that only used it to seed rows must survive that deletion unchanged in meaning.

### Task 3: Strip the mirror, manifest, contract and backup code
- [ ] status
**Objective:** The fork has no mirror reader, manifest importer, Cadence contract or backup module, and the remaining suite is green.
**Write-scope:**
`muse/api.py`
`muse/catalog.py`
`muse/features.py`
`muse/contract.py`
`muse/backup.py`
`muse/library/importer.py`
`muse/library/__init__.py`
`muse/library/tracks.py`
`docs/contracts/`
`docs/adrs/0007-muse-owns-the-cadence-contract.md`
`tests/conftest.py`
`tests/test_contract.py`
`tests/test_contract_shapes.py`
`tests/test_import.py`
`tests/test_backup.py`
`tests/test_artist_credits.py`
`tests/test_catalog_browse.py`
**Read-context:** `muse/api.py:30-70`, `muse/api.py:269-345`, `muse/api.py:429-520`, `muse/api.py:700-715`, `muse/library/__init__.py:1-40`, `muse/library/importer.py:1-75`, `muse/library/importer.py:260-293`, `muse/library/tracks.py:1-25`, `muse/library/tracks.py:225-240`, `muse/library/tracks.py:271-307`, `muse/catalog.py:86-266`, `muse/features.py:56-120`
**Anchors:**
`def _open_catalog(config: Config):` @ `muse/api.py`
`def _open_features(config: Config):` @ `muse/api.py`
`def contract() -> ContractVersion:` @ `muse/api.py`
`class SqliteCatalog:` @ `muse/catalog.py`
`class EmptyCatalog:` @ `muse/catalog.py`
`class SqliteFeatures:` @ `muse/features.py`
`def repair_joined_credits(conn: sqlite3.Connection, catalog) -> int:` @ `muse/library/tracks.py`
`from .importer import _index_track` @ `muse/library/tracks.py`
`def _is_instrumental(file_path: str | None, track_name: str) -> bool:` @ `muse/library/importer.py`
**Steps:**
1. Move `_index_track` (body unchanged) and `_is_instrumental` with `_INSTRUMENTAL_MARKERS` from `muse/library/importer.py` into `muse/library/tracks.py`, renamed `index_track` and `is_instrumental_path`. Import `normalize_title` from `..titles`. Replace `from .importer import _index_track` and the call at the old line 235 with `index_track`. Update `tests/conftest.py`'s `insert_track` (Task 2) to import `index_track`.
2. Delete `muse/library/importer.py`. In `muse/library/__init__.py`, remove the `from .importer import ...` line and the `"ImportResult"`, `"import_manifest"` and `"load_manifest"` entries in `__all__`.
3. In `muse/api.py`:
   - Make `_open_catalog(config)` return `EmptyCatalog()` with a one-line docstring: "No mirror in the desktop fork; kept as the seam tests monkeypatch."
   - Delete `_open_features` and the `SqliteCatalog`, `SqliteFeatures` and `EmptyFeatures` imports when unused.
   - In the lifespan, delete the manifest import block (`if config.manifest.exists(): ... else: log.warning(...)`), the `repair_joined_credits` block and the `sync_features` block.
   - Delete the `/api/contract` route and the `from .contract import ...` line.
4. In `muse/library/tracks.py`, delete `repair_joined_credits`.
5. In `muse/catalog.py`, delete `class SqliteCatalog`, `DEFAULT_CATALOG_PATH` and `_with_artist_list`. In `muse/features.py`, delete `class SqliteFeatures`, `_SELECT` and `DEFAULT_FEATURES_PATH`. Keep every Protocol, `EmptyCatalog`, `EmptyFeatures`, `TrackFacts`, `TrackFeatures` and `clean_isrc`. Remove imports that become unused, such as `sqlite3`.
6. Delete `muse/contract.py`, `muse/backup.py`, `docs/contracts/`, `docs/adrs/0007-muse-owns-the-cadence-contract.md`, `tests/test_contract.py`, `tests/test_contract_shapes.py`, `tests/test_import.py`, `tests/test_backup.py` and `tests/test_artist_credits.py`.
7. In `tests/test_catalog_browse.py`, delete every test that builds `SqliteCatalog`; keep the `EmptyCatalog` tests. Delete the file if none remain. In `tests/conftest.py`, delete `make_manifest` and `make_record`.
**New symbols:** `def index_track(conn: sqlite3.Connection, rowid: int, track_name: str, artist_name: str, album_name: str) -> None` and `def is_instrumental_path(file_path: str | None, track_name: str) -> bool` in `muse/library/tracks.py`
**Verify:** `uv run pytest -p no:cacheprovider` and then `grep -rnE "import_manifest|load_manifest|SqliteCatalog|SqliteFeatures|CONTRACT_VERSION|repair_joined_credits|muse\.backup|from \.backup|from \.importer|make_manifest" muse tests`. Expected: pytest's last line has 0 failures; grep prints nothing.
**Forbidden:** don't touch `muse/config.py` (Task 4 owns it; `config.manifest` and the other fields stay defined for now); don't touch `muse/ingest.py`, `muse/systemone.py`, `muse/lyric_tag_batch.py` or `muse/discover.py` (later milestones); don't remove the play sweeper `_sweep_forever` (see Assumptions).
**Depends:** Task 2
**Why:** Spec lines 73-88: the copy leaves out the contract, the mirrors and the manifest path. `_open_catalog` stays as the seam that `tests/test_api_discover.py:217` monkeypatches.

### Task 4: Web token plumbing: header on every fetch, ?t= on every media URL
- [x] status
**Objective:** Every `/api` request from the UI carries `X-Muse-Token`, and every media, `Image` and `EventSource` URL carries `?t=`. Both come from one module that bootstraps the token from the page URL and survives a reload.
**Write-scope:**
`web/src/token.ts` (new)
`web/src/main.tsx`
`web/src/api.ts`
`web/src/muse/mood.ts`
`web/src/player/sync.ts`
`web/src/player/plays.ts`
`web/src/player/PlayerContext.tsx`
`web/src/player/wash.ts`
`web/src/player/NowPlayingSheet.tsx`
`web/src/components/CoverArt.tsx`
`web/src/views/AlbumTracks.tsx`
`web/src/views/Playlists.tsx`
`web/src/jobs/JobsContext.tsx`
**Read-context:** `web/src/api.ts:100-260`, `web/src/api.ts:640-690`, `web/src/main.tsx:1-20`, `web/src/components/CoverArt.tsx:30-50`, `web/src/player/wash.ts:45-60`, `web/src/player/PlayerContext.tsx:645-655`, `web/src/player/PlayerContext.tsx:776-786`, `web/src/jobs/JobsContext.tsx:40-45`
**Anchors:**
`export async function get<T>(path: string, signal?: AbortSignal): Promise<T> {` @ `web/src/api.ts`
`export function paletteFor(key: number, src: string): Promise<Palette> {` @ `web/src/player/wash.ts`
`const source = new EventSource("/api/jobs/stream");` @ `web/src/jobs/JobsContext.tsx`
`element.src = ` @ `web/src/player/PlayerContext.tsx`
**Steps:**
1. Create `web/src/token.ts`:
   - It reads `t` from `new URL(location.href).searchParams` at module load.
   - If `t` is present, it stores it under `sessionStorage` key `muse-token` (try/catch), deletes it from the URL and calls `history.replaceState(history.state, "", url.toString())`.
   - Otherwise it reads `sessionStorage.getItem("muse-token")` (try/catch, null on throw).
   - It exports `token: string | null`, `withToken(url: string): string` (appends `?t=` or `&t=` with `encodeURIComponent`, and returns `url` unchanged when `token` is null), and `apiFetch(input: string, init?: RequestInit): Promise<Response>`. `apiFetch` calls `fetch(input, {...init, headers})`, where the headers are `new Headers(init?.headers)` with `X-Muse-Token` set when `token` is non-null.
2. In `web/src/main.tsx`, add `import "./token";` as the first import, so the URL is cleaned before the router reads it.
3. Replace every `fetch(` with `apiFetch(` in `web/src/api.ts` (the 6 call sites at today's lines 106, 140, 174, 214, 249, 650), `web/src/muse/mood.ts:63`, `web/src/player/sync.ts:48`, `web/src/player/sync.ts:67`, `web/src/player/plays.ts:27`, `web/src/player/plays.ts:49` and `web/src/player/plays.ts:68`. Keep every init option, including `keepalive: true`.
4. Wrap URLs in `withToken(...)` at exactly these sites:
   - `CoverArt.tsx`: the `src` expression at line 44. Wrap the whole result, including the `?v=` suffix.
   - `paletteFor` in `wash.ts`, where it assigns `img.src`.
   - `PlayerContext.tsx`: `element.src` (line 650) and the Media Session artwork `src` (line 783).
   - `NowPlayingSheet.tsx:179` img `src`.
   - `AlbumTracks.tsx:56` img `src`.
   - `Playlists.tsx:19` img `src`.
   - `JobsContext.tsx:43`, the `EventSource` URL.

   All other art reaches the DOM through `CoverArt` or `paletteFor`.
**New symbols:** `export const token: string | null`, `export function withToken(url: string): string` and `export function apiFetch(input: string, init?: RequestInit): Promise<Response>` in `web/src/token.ts`
**Verify:** from the repo root: `(cd web && npm run build)`, then `grep -rn "fetch(" web/src --include=*.ts --include=*.tsx | grep -v apiFetch`, then `node --experimental-strip-types --input-type=module -e "globalThis.location={href:'http://127.0.0.1:5/library?t=abc'};const s=new Map();globalThis.sessionStorage={getItem:k=>s.get(k)??null,setItem:(k,v)=>s.set(k,v)};let u;globalThis.history={state:null,replaceState:(a,b,c)=>{u=c}};const m=await import('./web/src/token.ts');if(m.token!=='abc'||s.get('muse-token')!=='abc'||u!=='http://127.0.0.1:5/library'||m.withToken('/api/x?v=1')!=='/api/x?v=1&t=abc')throw new Error('token bootstrap wrong');console.log('ok')"`. Expected: the build succeeds; grep prints only the one `fetch(` line inside `web/src/token.ts`; node prints `ok`.
**Forbidden:** no new npm deps; don't change any URL path; don't add the token to `localStorage` (the token is per launch); `token.ts` imports nothing else from `web/src`.
**Depends:** none
**Why:** Decision 3 consequences: one header in `api.ts`, and `?t=` where media elements cannot set headers. sessionStorage keeps LAYOUT invariant 5 (reload keeps the player) after the URL is cleaned.

## Phase 2 - Config, loopback server, storage foundation

### Task 5: Reshape Config around the data dir and add make_config for tests
- [ ] status
**Objective:** `Config` describes one data dir, loopback port and token, and every test builds its config with `make_config(tmp_path)`, which keeps every path under `tmp_path`.
**Write-scope:**
`muse/config.py`
`tests/conftest.py`
`tests/test_config.py`
`tests/test_api_catalog_browse.py`
`tests/test_api_vibe.py`
`tests/test_api_discover.py`
`tests/test_api_lyrics_synced.py`
`tests/test_api_recommend.py`
`tests/test_api_spa.py`
`tests/test_api_jobs.py`
`tests/test_charts.py`
`tests/test_art_chain_wiring.py`
`tests/test_dashboard_shelves.py`
`tests/test_embed_startup_wiring.py`
`tests/test_health_content.py`
`tests/test_playlists.py`
`tests/test_health_taste.py`
`tests/test_plays.py`
**Read-context:** `muse/config.py:1-110`, `tests/test_plays.py:145-162`, `tests/test_config.py:1-90`
**Anchors:**
`class Config:` @ `muse/config.py`
`def from_env(cls) -> "Config":` @ `muse/config.py`
`PROJECT_ROOT = Path(__file__).resolve().parent.parent` @ `muse/config.py`
**Steps:**
1. Rewrite the `Config` fields in this order:
   - No defaults: `data_dir: Path`, `library_db: Path`, `music_root: Path`, `art_root: Path`, `lyrics_root: Path`, `settings_path: Path`, `web_dist: Path`.
   - Defaults: `port: int = 0`, `host: str = "127.0.0.1"`, `token: str | None = None`, `art_backfill: bool = True`, `lyrics_sync: bool = True`, `embed_batch: bool = False`, `content_embed: bool = False`, `taste_retrain_at_launch: bool = True`, `catalog_freeze: str = "2025-11-13"`.
   - Then the existing `laya_*`, `curator_*`, `download_gap_seconds` and `chart_*` fields, unchanged.
   - Delete `root`, `manifest`, `catalog`, `audio_features`, `backup_dir` and `backup_target`.
2. In `from_env`:
   - `data_dir = Path(os.environ.get("MUSE_DATA_DIR") or PROJECT_ROOT / "data")`.
   - Each store takes `os.environ.get(<env>)` or a `data_dir` default: `MUSE_LIBRARY_DB` gives `data_dir/"library.sqlite3"`, `MUSE_MUSIC_ROOT` gives `data_dir/"downloads"`, `MUSE_ART_ROOT` gives `data_dir/"art"`, `MUSE_LYRICS_ROOT` gives `data_dir/"lyrics"`, `MUSE_SETTINGS` gives `data_dir/"settings.json"`.
   - `web_dist` comes from `_path("MUSE_WEB_DIST", "web/dist")`.
   - `port=int(os.environ.get("MUSE_PORT", "0"))` and `token=os.environ.get("MUSE_TOKEN") or None`.
   - `lyrics_sync` from `MUSE_LYRICS_SYNC` (default `"1"`) and `taste_retrain_at_launch` from `MUSE_TASTE_RETRAIN` (default `"1"`), both with the same `!= "0"` rule `art_backfill` uses.
   - No `MUSE_HOST`.
   - Rewrite the module docstring in 3 lines: every path lives under one data dir and is env-overridable.
3. Add to `tests/conftest.py`:
   ```python
   def make_config(tmp_path, **overrides) -> Config:
       base = dict(data_dir=tmp_path, library_db=tmp_path / "library.sqlite3",
                   music_root=tmp_path / "MUSIC", art_root=tmp_path / "art",
                   lyrics_root=tmp_path / "lyrics", settings_path=tmp_path / "settings.json",
                   web_dist=tmp_path / "web", port=8080, art_backfill=False,
                   lyrics_sync=False, embed_batch=False, content_embed=False,
                   taste_retrain_at_launch=False)
       return Config(**{**base, **overrides})
   ```
4. In every listed test file, replace each `Config(...)` construction with `make_config(tmp_path, <only the kwargs whose value differs from make_config's base>)`, then drop now-unused `Config` imports. In `tests/test_config.py`, keep the `catalog_freeze` and laya/curator tests. Add `test_paths_default_under_the_data_dir` (with `MUSE_DATA_DIR=<tmp>` set and the other path vars unset, `library_db == tmp/"library.sqlite3"` and `lyrics_root == tmp/"lyrics"`) and `test_port_defaults_to_zero_and_token_to_none`. Delete tests of removed fields.
**New symbols:** `Config` fields `data_dir`, `lyrics_root`, `settings_path`, `token`, `lyrics_sync`, `taste_retrain_at_launch`; `def make_config(tmp_path, **overrides) -> Config` in `tests/conftest.py`
**Verify:** `uv run pytest -p no:cacheprovider` and then `grep -rnE "(^|[^A-Za-z_])Config\(" tests | grep -v "Config(\*\*"`. Expected: pytest's last line has 0 failures; grep prints nothing (`Config.from_env(` and `FakeConfig(` do not match; `make_config`'s own `Config(**...)` is excluded).
**Forbidden:** no default path outside `tmp_path` in `make_config`; don't touch `muse/api.py` (if a removed field is still read there, stop and report, since Task 3 should have removed it); don't touch `muse/__main__.py` (Task 6).
**Depends:** Task 3
**Why:** Spec lines 90-97 define one per-user data dir. One test helper replaces 19 hand-built configs, so later field changes touch one place.

### Task 6: Loopback entrypoint, per-launch token middleware, MIME by extension
- [ ] status
**Objective:** `python -m muse` binds `127.0.0.1` on a free port and prints `MUSE_LISTENING <port>`. `/api` refuses requests without the token. Streams send the right MIME type per extension.
**Write-scope:**
`muse/__main__.py`
`muse/net.py`
`muse/api.py`
`muse/streaming.py`
`tests/test_net.py`
`tests/test_token.py` (new)
`tests/test_streaming.py`
**Read-context:** `muse/__main__.py:1-80`, `muse/api.py:65-75`, `muse/api.py:654-660`, `muse/api.py:1598-1645`, `muse/streaming.py:1-30`, `tests/test_streaming.py:1-30`
**Anchors:**
`def main() -> int:` @ `muse/__main__.py`
`def _listener(address: str, port: int) -> socket.socket:` @ `muse/__main__.py`
`AUDIO_MEDIA_TYPE = "audio/mp4"` @ `muse/api.py`
`def stream(track_rowid: int, request: Request) -> Response:` @ `muse/api.py`
`app = FastAPI(title="Muse", lifespan=lifespan)` @ `muse/api.py`
`def make_config(tmp_path, **overrides) -> Config:` @ `tests/conftest.py` (from Task 5)
**Steps:**
1. Delete `muse/net.py` and `tests/test_net.py`.
2. Rewrite `muse/__main__.py`:
   - Keep logging setup and `_listener`, minus the IPv6 branch (IPv4 loopback only) and minus `SO_REUSEADDR`. Port 0 never needs it, and on Windows it would let another process bind the same address.
   - In `main()`: `config = Config.from_env()`, then `generated = config.token is None`, then `config = dataclasses.replace(config, token=config.token or secrets.token_urlsafe(32))`.
   - Bind with `sock = _listener("127.0.0.1", config.port)`. On `OSError`, print `Could not bind 127.0.0.1:<port> (<error>)` to stderr and return 1.
   - `port = sock.getsockname()[1]`, then `print(f"MUSE_LISTENING {port}", flush=True)`.
   - If `generated`, log `Open http://127.0.0.1:%d/?t=%s` with port and token. Never log the token otherwise.
   - Run `uvicorn.Server(uvicorn.Config(create_app(config), log_level="info")).run(sockets=[sock])`.
   - Rewrite the module docstring: loopback only, token per launch, why (Decision 3).
3. In `muse/api.py`, right after `app = FastAPI(...)`, add an `@app.middleware("http")` function `require_token`:
   - When `config.token` is set and `request.url.path` starts with `/api`, accept `request.headers.get("x-muse-token")` or `request.query_params.get("t")` matching `config.token` via `hmac.compare_digest`.
   - Otherwise return `JSONResponse({"detail": "Missing or wrong token"}, status_code=401)`.
   - Non-`/api` paths pass through.
4. In `muse/streaming.py`, add `MEDIA_TYPES = {".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".flac": "audio/flac", ".wav": "audio/wav", ".ogg": "audio/ogg", ".opus": "audio/ogg"}` and `def media_type_for(path: Path) -> str` (lowercased suffix lookup, default `"application/octet-stream"`). Replace the docstring line "Audio is never transcoded: `.m4a` in, `.m4a` out." with "Audio is never transcoded: bytes go out exactly as stored."
5. In `muse/api.py`, delete `AUDIO_MEDIA_TYPE` and its comment, and use `media_type_for(path)` at both `media_type=` sites in `stream`.
6. Create `tests/test_token.py` with `TestClient(create_app(make_config(tmp_path, token="s3cret")))`. Cover:
   - `/api/health` with no token gives 401.
   - The header gives 200.
   - `?t=s3cret` gives 200.
   - A wrong header gives 401.
   - `/api/jobs/stream?t=wrong` gives 401.
   - `/api/jobs/stream?t=s3cret` gives 200 with `text/event-stream`. Open it with `client.stream("GET", ...)` and read the first line, then close.
   - `/` gives 200 without a token (SPA placeholder).
   - `make_config(tmp_path)` (token None) gives `/api/health` 200 with no token.
   - The stream route with `?t=` on a seeded `insert_track` row pointing at an absolute `tmp_path/"a.mp3"` gives 200 with content-type `audio/mpeg`, and the same URL with header `Range: bytes=0-1` gives 206.
7. In `tests/test_streaming.py`, add `test_media_type_for_each_extension` covering all six extensions, the uppercase `.MP3`, and an unknown `.xyz`.
**New symbols:** `MEDIA_TYPES: dict[str, str]` and `def media_type_for(path: Path) -> str` in `muse/streaming.py`; middleware `require_token` in `create_app`
**Verify:** `uv run pytest -p no:cacheprovider tests/test_token.py tests/test_streaming.py` and then `uv run pytest -p no:cacheprovider`. Expected: both test files pass; the full run's last line has 0 failures.
**Forbidden:** don't bind `0.0.0.0` or any non-loopback address; don't write the token to disk or include it in any log line except the self-generated dev URL; no new deps.
**Depends:** Task 5
**Why:** Decisions 3 and 12. The `MUSE_LISTENING` line lets the M3 shell learn the port without a race, and the token stops other local processes from reading the library.

### Task 7: Migration v21, settings.json module, lyric file paths, format in track fields
- [ ] status
**Objective:** Schema v21 adds the scanner's columns. `muse/settings.py` reads and writes `settings.json` atomically. `muse/lyrics.py` names the data-dir lyric files. Track payloads carry `format`.
**Write-scope:**
`muse/library/schema.py`
`muse/settings.py` (new)
`muse/lyrics.py`
`muse/library/queries.py`
`tests/test_migrations.py`
`tests/test_settings.py` (new)
`tests/test_lyrics.py`
**Read-context:** `muse/library/schema.py:655-700`, `muse/library/queries.py:20-47`, `muse/lyrics.py:1-50`, `tests/test_migrations.py:1-30`
**Anchors:**
`def _m020_reask_missing_art(conn: sqlite3.Connection) -> None:` @ `muse/library/schema.py`
`MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [` @ `muse/library/schema.py`
`TRACK_FIELDS = """` @ `muse/library/queries.py`
`def read_lyrics(path: str | Path | None) -> str | None:` @ `muse/lyrics.py`
**Steps:**
1. Add `_m021_local_files(conn)` after `_m020_reask_missing_art` and append it to `MIGRATIONS`. Statements, one `conn.execute` each:
   - `ALTER TABLE tracks ADD COLUMN file_size INTEGER`
   - `ALTER TABLE tracks ADD COLUMN file_mtime INTEGER`
   - `ALTER TABLE tracks ADD COLUMN format TEXT`
   - `ALTER TABLE tracks ADD COLUMN album_key TEXT`
   - `ALTER TABLE tracks ADD COLUMN removed_at INTEGER`
   - `CREATE INDEX tracks_file_path ON tracks(file_path)`
   - `CREATE INDEX tracks_album_key ON tracks(album_key)`

   Docstring: "The folder scanner's identity columns (desktop Decision 9) and the tombstone M2's delete sets (Decision 10)."
2. In `TRACK_FIELDS`, add `format,` after `gain_db,`.
3. Create `muse/settings.py`:
   - `DEFAULTS = {"music_folders": [], "downloader_unlocked": False}`.
   - `def load(path: Path) -> dict`: a missing file, unreadable JSON or a non-dict all return `dict(DEFAULTS)` (deep copy of the list) and log a warning for the corrupt case. Missing keys are filled from `DEFAULTS`. Unknown keys are kept.
   - `def save(path: Path, settings: dict) -> None`: `path.parent.mkdir(parents=True, exist_ok=True)`, write JSON (indent 2) to `path.with_suffix(".json.tmp")`, then `os.replace` it onto `path`.
   - `def music_folders(path: Path) -> list[Path]`: `[Path(p) for p in load(path)["music_folders"]]`.
4. In `muse/lyrics.py`, add `def lyrics_files(lyrics_root: Path, track_rowid: int) -> tuple[Path, Path]` returning `(lyrics_root / f"{track_rowid}.txt", lyrics_root / f"{track_rowid}.lrc")`. Update the module docstring's first line to say files live in the data dir's `lyrics/`.
5. Tests:
   - `tests/test_migrations.py`: `test_migrate_adds_the_local_file_columns` (all five columns present, both index names present).
   - `tests/test_settings.py`: missing file gives defaults; corrupt JSON gives defaults; a round-trip save then load; a save leaves no `.json.tmp`; an unknown key survives a round trip.
   - `tests/test_lyrics.py`: `lyrics_files(tmp_path, 7)` gives `(tmp_path/"7.txt", tmp_path/"7.lrc")`.
**New symbols:** `_m021_local_files`; `muse/settings.py` with `DEFAULTS`, `load`, `save`, `music_folders`; `def lyrics_files(lyrics_root: Path, track_rowid: int) -> tuple[Path, Path]`
**Verify:** `uv run pytest -p no:cacheprovider tests/test_migrations.py tests/test_settings.py tests/test_lyrics.py` and then `uv run pytest -p no:cacheprovider`. Expected: all pass; the full run has 0 failures.
**Forbidden:** never edit a shipped migration (`_m001` to `_m020`); no new deps; `settings.py` imports only stdlib.
**Depends:** Task 5
**Why:** Decisions 8 and 9 need path, size, mtime and album-key identity plus a tombstone, and the folder list lives in `settings.json` (spec line 97). `format` feeds Decision 12's `canPlayType` label.

## Phase 3 - Scanner, lyrics in the data dir, watcher

### Task 8: Red tests for tag reading and the folder scanner [test]
- [ ] status
**Objective:** `tests/test_tags.py` and `tests/test_scanner.py` encode every scanner rule below and fail only because `muse/tags.py` and `muse/scanner.py` do not exist.
**Write-scope:**
`tests/test_tags.py` (new)
`tests/test_scanner.py` (new)
**Read-context:** `tests/conftest.py:1-40`, `muse/library/lyrics_state.py:19-40`, `muse/library/art.py:84-117`, `muse/library/tracks.py:21-36`
**Anchors:**
`def conn(tmp_path):` @ `tests/conftest.py`
`def track_row(conn, rowid):` @ `tests/conftest.py`
**Steps:**
1. Code against these exact signatures (Task 9 implements them):
   - `muse.tags`: `AUDIO_EXTENSIONS = (".m4a", ".mp3", ".flac", ".wav", ".ogg", ".opus")`.
   - `@dataclass(frozen=True, slots=True) class FileTags: title: str | None; artist: str | None; album: str | None; album_artist: str | None; track_no: int | None; disc_no: int | None; isrc: str | None; duration_ms: int | None`.
   - `def read_tags(path: Path) -> FileTags | None` and `def embedded_picture(path: Path) -> tuple[bytes, str] | None` (ext `".jpg"` or `".png"`).
   - `muse.scanner`: `@dataclass(slots=True) class ScanResult: added: int = 0; updated: int = 0; moved: int = 0; missing: int = 0; skipped_removed: int = 0; unchanged: int = 0; unreadable: int = 0`.
   - `def scan(conn, folders: list[Path], *, lyrics_root: Path, art_root: Path, read_tags=tags.read_tags, embedded_picture=tags.embedded_picture, now: int | None = None, on_progress=None) -> ScanResult`.
2. `tests/test_tags.py` builds a real WAV in `tmp_path` with stdlib `wave` (mono, 2 bytes per sample, 8000 Hz, 16000 frames, so 2.0 s). It tags it through `mutagen.wave.WAVE(p).add_tags()` with frames TIT2 "Song", TPE1 "Artist", TALB "Album", TPE2 "AA", TRCK "3/10", TPOS "1/2", and APIC (mime `image/jpeg`, data `b"\xff\xd8jpg"`). Cases:
   - (a) `read_tags` gives title "Song", artist "Artist", album "Album", album_artist "AA", track_no 3, disc_no 1, duration_ms 2000.
   - (b) `embedded_picture` gives `(b"\xff\xd8jpg", ".jpg")`.
   - (c) A file of random bytes named `x.mp3` gives `read_tags(...) is None` and `embedded_picture(...) is None`.
3. In `tests/test_scanner.py`, every path is under `tmp_path`, and audio files are any bytes. A fake `read_tags` is a dict keyed by filename that returns `FileTags`; `embedded_picture` is a fake returning None unless the case sets it; `now=1000`. Cases, one test each:
   1. A new file inserts a row with `origin='local'`, `metadata_tier='tags'`, `status='done'`, `format='mp3'`, an absolute `file_path`, `file_size`, `file_mtime == int(st_mtime)`, `track_rowid >= 1_000_000_000`, `added_at == 1000`, `artist_names == '["Artist"]'`, and `added == 1`.
   2. Missing tags fall back: title to the file stem, artist to `"Unknown artist"`, album to `"Unknown album"`.
   3. Two files with the same album_artist and album (case differs) share one `album_rowid`; a different album gets another.
   4. Rescanning with no change gives `unchanged == 1` and no write (`file_mtime` unchanged, row count unchanged).
   5. A changed size re-reads tags and updates `track_name`, with `updated == 1` and the same `track_rowid`.
   6. A file renamed within the folder (same bytes, same tags) keeps its `track_rowid`, updates `file_path`, gives `moved == 1`, and a `plays` row inserted for it before the move still references it.
   7. A deleted file in a present folder gives `status='missing_file'` and `missing == 1`.
   8. A folder path that does not exist marks its earlier rows `missing_file`. When it reappears with the same files, they are `done` again with the same `track_rowid`s.
   9. A row with `removed_at` set and the same `file_path` is skipped (`skipped_removed == 1`, no new row).
   10. A file `read_tags` returns None for gives `unreadable == 1` and no row.
   11. `song.lrc` beside `song.mp3` is copied to `lyrics_root/<rowid>.lrc`; `lyrics_root/<rowid>.txt` holds the timestamp-stripped text; `lyrics_path` points at it; `lyrics_state.state == 'synced'`.
   12. `song.txt` alone is copied to `<rowid>.txt` with `lyrics_state.state == 'plain'`.
   13. `embedded_picture` returning `(b"img", ".png")` writes `art_root/<album_rowid>.png`; the `album_art` row has `source == 'embedded'`; `tracks.art_path` is set.
   14. Hidden files (`.song.mp3`) and `.jpg` files are ignored.
   15. The scanned track is found by `muse.library.queries.search(conn, "Song")`. The signature is `def search(conn: sqlite3.Connection, query: str, limit: int = 60) -> list[dict]` (`muse/library/queries.py:239`), returning track dicts: assert one has `track_name == "Song"`.
   16. The music folder's directory listing and every audio file's bytes are unchanged after a scan (compare sorted `os.listdir` and `read_bytes` before and after).
**New symbols:** none
**Verify:** `uv run pytest -p no:cacheprovider tests/test_tags.py tests/test_scanner.py`. Expected: collection errors, `ModuleNotFoundError: No module named 'muse.tags'` and `No module named 'muse.scanner'`, and nothing else.
**Forbidden:** don't create `muse/tags.py` or `muse/scanner.py`; no relative paths (memory: `insert_track`'s relative `MUSIC/` default pollutes the repo); don't use `insert_track` with its default `file_path` in scanner tests.
**Depends:** Task 7
**Why:** Decision 9's identity and Decision 8's copy-in rules are the riskiest logic in M1. Pinning them first keeps the implementer honest.

### Task 9: Implement tag reading and the folder scanner
- [ ] status
**Objective:** `muse/tags.py` and `muse/scanner.py` make Task 8's tests pass.
**Write-scope:**
`muse/tags.py` (new)
`muse/scanner.py` (new)
`muse/library/tracks.py`
**Read-context:** `tests/test_scanner.py` (from Task 8), `tests/test_tags.py` (from Task 8), `muse/library/tracks.py:21-75`, `muse/library/art.py:84-117`, `muse/library/lyrics_state.py:106-140`, `muse/lrc.py:72-90`
**Anchors:**
`def next_local_rowid(conn: sqlite3.Connection) -> int:` @ `muse/library/tracks.py`
`def index_track(conn: sqlite3.Connection, rowid: int, track_name: str, artist_name: str, album_name: str) -> None` @ `muse/library/tracks.py` (from Task 3)
`def record_cover(` @ `muse/library/art.py`
`def upsert(` @ `muse/library/lyrics_state.py`
`def strip_timestamps(text: str) -> str:` @ `muse/lrc.py`
`def lyrics_files(lyrics_root: Path, track_rowid: int) -> tuple[Path, Path]` @ `muse/lyrics.py` (from Task 7)
**Steps:**
1. `muse/tags.py`:
   - `read_tags`: `mutagen.File(path, easy=True)`, wrapped in `try/except Exception` (returns None on exception or a None result).
   - For a `mutagen.wave.WAVE`, read ID3 frames by id (`TIT2` title, `TPE1` artist, `TALB` album, `TPE2` album_artist, `TRCK` track, `TPOS` disc, `TSRC` isrc). Otherwise read easy keys `title`, `artist`, `album`, `albumartist`, `tracknumber`, `discnumber`, `isrc`, taking the first value.
   - Numbers parse the part before `/`, giving None when that is not a digit.
   - `duration_ms = round(info.length * 1000)` when `info` has `length`.
   - `embedded_picture`: raw `mutagen.File(path)` in the same try. ID3 tags (mp3/wav) use `tags.getall("APIC")[0]`; FLAC uses `.pictures[0]`; MP4 uses `tags["covr"][0]`. The ext is `".png"` when the mime or `imageformat` says PNG, else `".jpg"`. Anything else returns None.
2. In `muse/library/tracks.py`, add `def album_rowid_for_key(conn: sqlite3.Connection, album_key: str) -> int`. It looks up `album_rowid` from `tracks WHERE album_key = ? AND album_rowid IS NOT NULL LIMIT 1`, and otherwise allocates the next local album rowid with the same `MAX(album_rowid) >= LOCAL_ROWID_FLOOR` rule `album_rowid_for_release` uses.
3. `muse/scanner.py`, `scan()`:
   - Walk each folder that `is_dir()` with `os.walk(followlinks=False)`, resolving paths. Skip names starting with `.` and suffixes not in `AUDIO_EXTENSIONS` (case-insensitive).
   - Apply the rules in Task 8 case order. Rows considered for vanishing or unplugging are `origin='local' AND removed_at IS NULL` with `Path(file_path).is_relative_to(folder)`.
   - Move matching takes the lowest `track_rowid` among the vanished rows matching `(file_size, duration_ms, artist_name, album_name, track_name)`.
   - The insert order per new file:
     1. Allocate the rowid.
     2. `INSERT INTO tracks`, with `is_instrumental = is_instrumental_path(file_path, title)`.
     3. `index_track`.
     4. Copy the sidecars into `lyrics_files(...)` paths.
     5. `UPDATE tracks SET lyrics_path`.
     6. `lyrics_state.upsert(conn, rowid, state=..., synced=..., checked_at=now)`.
     7. Embedded art: when there is no `album_art` row for the album, write the bytes, then `record_cover(conn, album_rowid, str(path), source="embedded")`.
   - Plain autocommit statements, no `BEGIN` (`upsert` commits).
   - Call `on_progress(done, total, str(path.name))` per file.
   - Open music files read-only; never write under a music folder.
**New symbols:** everything in Task 8 Step 1; `def album_rowid_for_key(conn: sqlite3.Connection, album_key: str) -> int` in `muse/library/tracks.py`
**Verify:** `uv run pytest -p no:cacheprovider tests/test_tags.py tests/test_scanner.py` and then `uv run pytest -p no:cacheprovider`. Expected: both files fully green; the full run has 0 failures.
**Forbidden:** don't edit Task 8's tests (if one looks wrong, report it as a finding); no new deps beyond mutagen (Task 1); no `BEGIN` around helper calls that commit; tracks row before `lyrics_state`/`album_art` (FKs are immediate).
**Depends:** Task 8
**Why:** Decision 9 identity and Decision 8 copy-in. The music folders stay read-only (spec lines 99-101).

### Task 10: LRCLIB writes and the synced route use the data-dir lyrics files
- [ ] status
**Objective:** LRCLIB results are written to `lyrics_root/<rowid>.txt|.lrc`, and `/api/tracks/{id}/lyrics/synced` reads the `.lrc` from there. Nothing writes beside the audio.
**Write-scope:**
`muse/lyrics_lrclib.py`
`muse/jobs.py`
`muse/lyrics_sync.py`
`muse/api.py`
`tests/test_lyrics_sync.py`
`tests/test_api_lyrics_synced.py`
`tests/test_api_jobs.py`
**Read-context:** `muse/lyrics_lrclib.py:228-280`, `muse/jobs.py:340-428`, `muse/lyrics_sync.py:31-60`, `muse/api.py:1285-1327`, `tests/test_lyrics_sync.py:230-340`, `tests/test_api_lyrics_synced.py:45-70`
**Anchors:**
`def _write_files(track: dict, outcome: SyncOutcome) -> Path | None:` @ `muse/lyrics_lrclib.py`
`def sync_batch(` @ `muse/lyrics_lrclib.py`
`async def run_lyrics_sync_backfill(` @ `muse/jobs.py`
`def track_lyrics_synced(track_rowid: int) -> dict:` @ `muse/api.py`
`def lyrics_files(lyrics_root: Path, track_rowid: int) -> tuple[Path, Path]` @ `muse/lyrics.py` (from Task 7)
**Steps:**
1. `_write_files(track, outcome, lyrics_root: Path)`: compute `txt, lrc = lyrics_files(lyrics_root, track["track_rowid"])` and `lyrics_root.mkdir(parents=True, exist_ok=True)`. Write `lrc` when `outcome.lrc_text` is set and `txt` when `outcome.lyrics_text` is set. Return `txt` if written, else None. It no longer needs `file_path`. Update the docstring.
2. Thread a required keyword `lyrics_root: Path` through `_apply`, `sync_batch` (after `lrclib_search`, as keyword-only) and `run_lyrics_sync_backfill`, which passes it on. In `muse/lyrics_sync.py`, pass `config.lyrics_root`.
3. In `track_lyrics_synced`, replace the `found["file_path"]`-based `.lrc` lookup with `_, lrc_path = lyrics_files(app.state.config.lyrics_root, track_rowid)`, then keep the same read and parse logic.
4. Update the tests:
   - Tests that assert files beside the audio (`audio_path.with_suffix(".lrc")`, `.lyrics.txt`) now assert `tmp_path/"lyrics"/f"{rowid}.lrc"` and `.txt`, and pass `lyrics_root=tmp_path / "lyrics"` to every `sync_batch(...)` and `run_lyrics_sync_backfill(...)` call.
   - The `lyrics_path` "manifest/path" test keeps its intent: a preset `lyrics_path` is not overwritten.
   - In `tests/test_api_lyrics_synced.py`, `_seed` writes the `.lrc` to `tmp_path/"lyrics"/f"{rowid}.lrc"`.
**New symbols:** keyword parameter `lyrics_root: Path` on `_write_files`, `_apply`, `sync_batch`, `run_lyrics_sync_backfill`
**Verify:** `uv run pytest -p no:cacheprovider tests/test_lyrics_sync.py tests/test_api_lyrics_synced.py tests/test_api_jobs.py` and then `uv run pytest -p no:cacheprovider`, then `grep -n "with_suffix" muse/lyrics_lrclib.py muse/api.py`. Expected: all green; grep prints nothing.
**Forbidden:** don't change `decide_outcome` or the `manual` guard; don't touch `muse/ingest.py` (the M5 download path still writes beside its own downloads); don't touch the lifespan (Task 12).
**Depends:** Task 7
**Why:** Decision 8: LRCLIB is fetched once into the data dir, and strangers' music folders are never written.

### Task 11: Scan job and folder watcher
- [ ] status
**Objective:** `run_scan_job` records a `scan` job with progress on the bus. `watch_folders` calls back on file changes and when a missing root appears or vanishes.
**Write-scope:**
`muse/jobs.py`
`muse/watch.py` (new)
`tests/test_jobs_scan.py` (new)
`tests/test_watch.py` (new)
**Read-context:** `muse/jobs.py:85-170`, `muse/jobs.py:340-428`
**Anchors:**
`async def run_art_backfill(` @ `muse/jobs.py`
`def _event(job: dict | None) -> dict:` @ `muse/jobs.py`
`def scan(conn, folders: list[Path], *, lyrics_root: Path, art_root: Path, read_tags=tags.read_tags, embedded_picture=tags.embedded_picture, now: int | None = None, on_progress=None) -> ScanResult` @ `muse/scanner.py` (from Task 9)
**Steps:**
1. In `muse/jobs.py`, add `SCAN = "scan"` and `async def run_scan_job(conn, folders: list[Path], bus: JobBus, *, lyrics_root: Path, art_root: Path, read_tags=None) -> dict | None`, the same shape as `run_lyrics_sync_backfill`:
   - Return None without a row when `job_rows.active(conn, SCAN)`, or when no folder in `folders` `is_dir()`.
   - Create the job with payload `{"folders": len(folders)}`, then RUNNING with message `Scanning`.
   - Run `scanner.scan` in `asyncio.to_thread`, with `read_tags` passed only when not None. `on_progress` updates progress and publishes threadsafe with message `f"Scanning · {done} of {total}"` and payload `{"done": done, "total": total}`.
   - On success: DONE, with message `f"{r.added} added, {r.moved} moved, {r.missing} missing"` plus `f", {r.unreadable} unreadable"` when non-zero.
   - Cancel and error handling are copied from `run_lyrics_sync_backfill`.
2. Create `muse/watch.py` with `async def watch_folders(folders: list[Path], on_change: Callable[[], Awaitable[None]], *, stop: asyncio.Event, poll_seconds: float = 60.0) -> None`. Loop until `stop.is_set()`:
   - Before the loop, `previous = sorted(f for f in folders if f.is_dir())`.
   - At the top of each pass, `roots = sorted(f for f in folders if f.is_dir())`. If `roots != previous`, `await on_change()`, then set `previous = roots`. This covers going from zero roots to one.
   - If `roots` is empty, `await asyncio.wait_for(stop.wait(), poll_seconds)` (catching `TimeoutError`) and continue.
   - Else `async for changes in watchfiles.awatch(*roots, stop_event=stop, rust_timeout=int(poll_seconds * 1000), yield_on_timeout=True)`:
     - On a non-empty `changes`, `await on_change()`.
     - Then if `sorted(f for f in folders if f.is_dir()) != roots`, break, so the next pass sees the difference and calls `on_change` once.
   - `on_change` exceptions are logged with `log.exception` and never end the loop.
3. `tests/test_jobs_scan.py` (absolute `tmp_path` folders, fake `read_tags`):
   - A job row with state `done` and the summary message.
   - `None` with no row when the folder is missing.
   - A second call while one is active returns None.
4. `tests/test_watch.py`, using `asyncio.run` with `poll_seconds=0.5` and a 10 s overall `asyncio.wait_for`:
   - (a) Writing a file in a watched dir calls `on_change`.
   - (b) A folder created after start (missing at start) triggers `on_change` within the timeout.
   - (c) Setting `stop` ends `watch_folders`.
**New symbols:** `SCAN = "scan"` and `async def run_scan_job(conn, folders: list[Path], bus: JobBus, *, lyrics_root: Path, art_root: Path, read_tags=None) -> dict | None` in `muse/jobs.py`; `async def watch_folders(folders: list[Path], on_change: Callable[[], Awaitable[None]], *, stop: asyncio.Event, poll_seconds: float = 60.0) -> None` in `muse/watch.py`
**Verify:** `uv run pytest -p no:cacheprovider tests/test_jobs_scan.py tests/test_watch.py` and then `uv run pytest -p no:cacheprovider`. Expected: all green; the full run has 0 failures.
**Forbidden:** don't touch `muse/api.py` (Task 12); don't use `force_polling`; no new deps.
**Depends:** Task 9, Task 10
**Why:** Decision 9: folders are watched with OS events, rescanned at launch, and a re-plugged drive must restore its tracks (spec lines 610-611). The watcher alone cannot see a root that did not exist when it started.

## Phase 4 - Wiring and UI

### Task 12: Lifespan chain, settings and scan routes
- [ ] status
**Objective:** On launch, the app runs scan, LRCLIB, art and a stale retrain under one lock. A watcher reruns the chain. `GET /api/settings`, `PUT /api/settings/music_folders` and `POST /api/scan` exist.
**Write-scope:**
`muse/api.py`
`tests/test_api_scan.py` (new)
`tests/test_art_chain_wiring.py`
**Read-context:** `muse/api.py:429-655`, `muse/api.py:666-706`, `muse/taste_retrain.py:27-56`, `muse/library/jobs.py:133-150`, `tests/test_art_chain_wiring.py:1-60`
**Anchors:**
`def create_app(` @ `muse/api.py`
`def run(conn, trainer: TasteTrainer | None = None) -> dict:` @ `muse/taste_retrain.py`
`def last(` @ `muse/library/jobs.py`
`async def run_scan_job(conn, folders: list[Path], bus: JobBus, *, lyrics_root: Path, art_root: Path, read_tags=None) -> dict | None` @ `muse/jobs.py` (from Task 11)
`async def watch_folders(folders: list[Path], on_change: Callable[[], Awaitable[None]], *, stop: asyncio.Event, poll_seconds: float = 60.0) -> None` @ `muse/watch.py` (from Task 11)
**Steps:**
1. Add `taste_trainer=None` and `read_tags=None` keyword parameters to `create_app` (injectable like `art_provider`).
2. In the lifespan, create `app.state.chain_lock = asyncio.Lock()` and define `async def run_chain(launch: bool) -> None`, stored as `app.state.run_chain` so routes reach it. Under the lock, in order:
   1. `folders = settings.music_folders(config.settings_path)` (not `config.music_root`: M5 adds `downloads/` once the downloader is unlocked), then `await run_scan_job(conn, folders, bus, lyrics_root=config.lyrics_root, art_root=config.art_root, read_tags=read_tags)`.
   2. If `config.lyrics_sync`: `await run_lyrics_sync_backfill(conn, bus, lyrics_root=config.lyrics_root)`.
   3. If `config.art_backfill`: the existing `run_art_backfill(conn, <same provider expression as today>, config.art_root, bus)` call, moved here. Delete the separate launch `backfill` task.
   4. If `launch and config.taste_retrain_at_launch`, and `job_rows.last(conn, TASTE_RETRAIN, state=job_rows.DONE)` is None or its `updated_at < time.time() - 86400`: `await asyncio.to_thread(taste_retrain.run, conn, taste_trainer)`.

   Each step is wrapped in `try/except Exception: log.exception(...)`, so one failure never skips the rest.
3. Launch: `app.state.chain_task = asyncio.create_task(run_chain(True))`.
4. The watcher:
   - Define `def start_watcher()`, stored as `app.state.start_watcher`. If a watcher is running, it first sets that watcher's own stop `Event`, then cancels its task. Each watcher gets its own fresh `asyncio.Event` kept on `app.state.watch_stop`. It then starts `watch_folders(settings.music_folders(config.settings_path), lambda: run_chain(False), stop=<fresh Event>)`.
   - Start it at launch.
   - Shutdown sets `app.state.watch_stop` first (so awatch's thread exits without waiting for `rust_timeout`), then cancels the watcher and chain tasks and awaits them, swallowing `CancelledError`, before `conn.close()`.
5. Routes:
   - `GET /api/settings` returns `settings.load(config.settings_path)` plus `"folder_status": [{"path": p, "present": Path(p).is_dir(), "songs": n}]` per music folder. `n` is `SELECT COUNT(*) FROM tracks WHERE status = 'done' AND removed_at IS NULL AND file_path LIKE ?`, with `p + os.sep + '%'`.
   - `PUT /api/settings/music_folders` (an `async def` route, because it calls `asyncio.create_task`) takes body model `MusicFoldersWrite(folders: list[str])`. Validate each entry: `Path(p).is_absolute()`, else 400 `f"{p} is not an absolute path"`; `is_dir()`, else 400 `f"{p} is not a folder"`. Then resolve each, dedupe, and reject nesting: if any folder `is_relative_to` another, 400 `f"{a} is inside {b}"`. Save `music_folders` as resolved strings, call `app.state.start_watcher()`, `asyncio.create_task(app.state.run_chain(False))`, and return the saved settings with `folder_status`.
   - `POST /api/scan` (an `async def` route) returns status 202 with `{"started": True}` after `asyncio.create_task(app.state.run_chain(False))`.
6. Add `import settings` from `.` and `taste_retrain` alongside the existing imports.
7. `tests/test_api_scan.py`, using `TestClient(create_app(make_config(tmp_path), read_tags=<fake>))`:
   - Default `GET /api/settings` gives `{"music_folders": [], "downloader_unlocked": False, "folder_status": []}`.
   - After the valid-folder case below, `folder_status` gives `[{"path": <resolved>, "present": True, "songs": 1}]`.
   - A relative path gives 400; a missing dir gives 400; a nested pair gives 400.
   - A valid folder holding one `song.mp3` gives 200, `settings.json` written under `tmp_path`, and then polling `/api/library/songs` (up to 10 s, 0.1 s sleeps) finds `track_name` from the fake tags.
   - With no folders, no `scan` job appears in `/api/jobs` after launch.
   - `POST /api/scan` gives 202.
8. Update `tests/test_art_chain_wiring.py` only where it asserted the old standalone launch task. Its provider-chain assertions stay.
**New symbols:** `create_app(..., taste_trainer=None, read_tags=None)`; `MusicFoldersWrite`; routes `GET /api/settings`, `PUT /api/settings/music_folders`, `POST /api/scan`
**Verify:** `uv run pytest -p no:cacheprovider tests/test_api_scan.py tests/test_art_chain_wiring.py` and then `uv run pytest -p no:cacheprovider`. Expected: all green; the full run has 0 failures (one rerun allowed for the known exit-139 teardown flake).
**Forbidden:** don't run two chain steps concurrently; don't start a retrain on non-launch chains; don't remove `_sweep_forever`; don't write `downloader_unlocked`; no new deps.
**Depends:** Task 6, Task 11
**Why:** Spec launch flow (lines 550-557) and Decision 13's retrain-at-launch. The single lock exists because the shared connection cannot hold two transactions at once.

### Task 13: Settings screen with Music folders, nav entry, scan-aware job strip [design]
- [ ] status
**Objective:** `/settings` lists, adds and removes music folders and shows scan progress. The nav has Settings and shows Add only when unlocked. The job strip names scan and lyrics jobs by kind, and Library reloads when a scan ends.
**Write-scope:**
`web/src/views/Settings.tsx` (new)
`web/src/styles/settings.css` (new)
`web/src/App.tsx`
`web/src/components/NavBar.tsx`
`web/src/components/Icons.tsx`
`web/src/components/JobProgress.tsx`
`web/src/jobs/JobsContext.tsx`
`web/src/api.ts`
`web/src/views/Library.tsx`
**Read-context:** `web/src/components/NavBar.tsx:1-31`, `web/src/App.tsx:20-75`, `web/src/components/Screen.tsx:1-50`, `web/src/components/JobProgress.tsx:1-50`, `web/src/jobs/JobsContext.tsx:16-80`, `web/src/styles/base.css:21-41`, `web/src/api.ts:46-66`, `web/src/useLoad.ts:1-40`, `LAYOUT.md:1-40`
**Anchors:**
`export function NavBar() {` @ `web/src/components/NavBar.tsx`
`export function JobProgress() {` @ `web/src/components/JobProgress.tsx`
`export function JobsProvider({ children }: { children: ReactNode }) {` @ `web/src/jobs/JobsContext.tsx`
`export function Screen({` @ `web/src/components/Screen.tsx`
`export type Track = {` @ `web/src/api.ts`
`export function apiFetch(input: string, init?: RequestInit): Promise<Response>` @ `web/src/token.ts` (from Task 4)
**Steps:**
1. In `api.ts`:
   - Add `format: string | null;` to `Track` after `gain_db`.
   - Add `type Settings = { music_folders: string[]; downloader_unlocked: boolean; folder_status: { path: string; present: boolean; songs: number }[] }`, `getSettings(signal?)`, `putMusicFolders(folders: string[]): Promise<Settings>` and `startScan(): Promise<void>`, built on `apiFetch`.
   - A non-ok response throws `ApiError` carrying the JSON `detail` text when present.
2. `JobsContext`:
   - Add `libraryVersion: number` to `JobsApi` (default 0), bumped when an event with `kind === "scan"` moves from running/queued to `done`.
   - Widen `Job.payload` to `{ albums?: number; done?: number; total?: number }`.
   - The existing `artVersion` bump only fires for `kind === "art_backfill"`.
3. `JobProgress`: the label is chosen by `job.kind`:
   - `"scan"` shows `Scanning · {done} of {total}` while running.
   - `"lyrics_sync"` shows `Fetching lyrics`.
   - `"art_backfill"` keeps today's text.
   - Any other kind shows `job.message ?? "Working"`.
   - When finished, it shows `job.message`.
4. `Library.tsx`: add `libraryVersion` from `useJobs()` to the deps of each `useLoad` call.
5. `Settings.tsx`, using `Screen` with title "Settings":
   - One `.section` "Music folders" with the help line from the Design brief.
   - One row per `folder_status` entry: the full path, then a status line (`{songs} songs`, or `Not connected` when `present` is false), then a `Remove` text button that PUTs `music_folders` without it.
   - A `.field` with label "Folder path" above an input, an `Add folder` `.btn.primary` that PUTs the list plus the trimmed input and shows the 400 `detail` under the input, and a `Rescan now` `.btn` calling `startScan()`.
   - An inline `role="status"` line showing the current scan job's progress or message.
   - The empty state copy from the brief.
6. `App.tsx`: add `<Route path="/settings" element={<Settings />} />` and import `./styles/settings.css`.
7. `NavBar.tsx`:
   - Add a `SettingsGlyph` (new in `Icons.tsx`, a 24-viewBox gear path in the same style as `LibraryGlyph`) tab `{ to: "/settings", label: "Settings" }` as the last tab.
   - Load `getSettings` once with `useLoad`, and render the Add tab only when `data?.downloader_unlocked` is true.
8. `settings.css`: folder rows separated by `--hairline`; the path in `--text` with `overflow-wrap: anywhere`; Remove as a 44 px-high text button in `--text-2`; no new colour tokens.
**New symbols:** `Settings` view; `getSettings`, `putMusicFolders`, `startScan`, `Settings` type in `api.ts`; `libraryVersion` in `JobsApi`; `SettingsGlyph` in `Icons.tsx`
**Verify:** `cd web && npm run build && node ~/.claude/skills/design-brain/scripts/preflight.mjs src/views/Settings.tsx src/styles/settings.css src/components/NavBar.tsx src/components/JobProgress.tsx`. Expected: the build succeeds; preflight `0 errors`.
**Forbidden:** no new npm deps; no modal or toast; no em dash in UI strings; don't change the five existing tab routes; don't add a folder picker (M3 adds the native one); don't write `downloader_unlocked`.
**Depends:** Task 4, Task 12
**Design:** settings
**Why:** Decision 9: the user picks folders under Settings, Music folders. Decision 11: Add is hidden until unlocked. The job strip would otherwise call a scan "Fetching cover art".

### Task 14: "Format not supported" and "File missing" row labels [design]
- [ ] status
**Objective:** A track whose format the webview cannot play shows "Format not supported on this Mac" (or "PC") and cannot be started. A missing file still shows "File missing".
**Write-scope:**
`web/src/player/formats.ts` (new)
`web/src/components/TrackRow.tsx`
`web/src/views/Library.tsx`
`web/src/views/AlbumTracks.tsx`
`web/src/views/PlaylistDetail.tsx`
`web/src/views/Muse.tsx`
`web/src/views/Home.tsx`
`web/src/player/NowPlayingSheet.tsx`
**Read-context:** `web/src/components/TrackRow.tsx:1-60`, `web/src/views/Library.tsx:262-276`, `web/src/views/AlbumTracks.tsx:98-110`, `web/src/views/PlaylistDetail.tsx:165-175`, `web/src/views/Muse.tsx:55-62`, `web/src/views/Home.tsx:130-262`, `web/src/player/NowPlayingSheet.tsx:358-368`
**Anchors:**
`export function TrackRow(props: {` @ `web/src/components/TrackRow.tsx`
`const label = playable ? sub : "File missing";` @ `web/src/components/TrackRow.tsx`
**Steps:**
1. Create `web/src/player/formats.ts`:
   - `MIME: Record<string, string> = { m4a: "audio/mp4", mp3: "audio/mpeg", flac: "audio/flac", wav: "audio/wav", ogg: "audio/ogg", opus: 'audio/ogg; codecs="opus"' }`.
   - `canPlayFormat(format: string | null): boolean`: null or an unknown key returns true. Otherwise it returns `document.createElement("audio").canPlayType(MIME[format]) !== ""`, memoised per format in a `Map`.
   - `const platformWord = navigator.userAgent.includes("Mac") ? "Mac" : "PC"`.
   - `export function unplayableReason(track: { playable: boolean; format: string | null }): string | null` returns `"File missing"` when `!track.playable`, `` `Format not supported on this ${platformWord}` `` when `!canPlayFormat(track.format)`, and null otherwise.
2. `TrackRow`: replace the `playable: boolean` prop with `unplayable: string | null`. Then `const playable = unplayable === null;` and `const label = playable ? sub : unplayable;`. The rest is unchanged.
3. At each call site that passes `playable={x.playable}` (`Library.tsx:273`, `AlbumTracks.tsx:106`, `PlaylistDetail.tsx:172`, `Muse.tsx:60`, `Home.tsx:136`, `Home.tsx:157`, `Home.tsx:237`, `Home.tsx:259`, `NowPlayingSheet.tsx:365`), pass `unplayable={unplayableReason(x)}` instead, with the same object `x`.
**New symbols:** `canPlayFormat`, `unplayableReason` in `web/src/player/formats.ts`; `TrackRow` prop `unplayable: string | null` (replaces `playable`)
**Verify:** `cd web && npm run build && { grep -rn "playable={" src | grep -v unplayable; true; } && node ~/.claude/skills/design-brain/scripts/preflight.mjs src/components/TrackRow.tsx src/player/formats.ts`. Expected: the build succeeds; grep prints nothing; preflight `0 errors`.
**Forbidden:** no new deps; don't change how `PlayerContext` picks the next track; no em dash; don't change row layout or CSS.
**Depends:** Task 13
**Design:** track-row
**Why:** Decision 12: no silent failure on an unsupported format. The label is decided once at launch per format.

### Task 15: Rewrite README, CLAUDE.md and CONTEXT.md for the desktop fork [docs]
- [ ] status
**Objective:** The fork's README, CLAUDE.md and CONTEXT.md describe the desktop app as it stands after M1, with no tailnet, Cadence, mirror or manifest instructions left.
**Write-scope:**
`README.md`
`CLAUDE.md`
`CONTEXT.md`
**Read-context:** `CLAUDE.md:1-200`, `README.md`, `CONTEXT.md`, `muse/config.py` (from Task 5), `muse/__main__.py` (from Task 6), `docs/specs/2026-10-29-desktop-app-fork.md:28-101`
**Anchors:**
`def main() -> int:` @ `muse/__main__.py`
**Steps:**
1. `README.md` is written for a stranger installing from source. Cover:
   - What Muse desktop is (a local player and recommender for your own music folders, with no account and no server).
   - Requirements: Python 3.12 via `uv`, Node 22.
   - Run it: `uv sync`, `cd web && npm ci && npm run build`, `uv run python -m muse`, then open the printed `http://127.0.0.1:<port>/?t=...` URL.
   - Where data lives (`MUSE_DATA_DIR`, default `./data`; list its contents per spec lines 90-97).
   - That music folders are never written.
   - Status: alpha, unsigned builds come in M3.
   - Licenses section: placeholder lines for MMS-FA CC-BY-NC-4.0 and schufo MIT, marked "arrives with smart features (M4)".
   - Do not describe the M5 unlock or its password.
2. `CLAUDE.md`, keeping the structure:
   - Rewrite "What this is".
   - Commands: drop `muse.reco_eval`'s ~4 min note only if the module is gone (it stays), drop the `--all-extras` deployed-machine paragraph and the `deploy/` section, and keep `uv sync --project universal_reco` with the note "retired in M4".
   - Architecture: one process bound to loopback with a per-launch token; one data dir; the folder scanner, watcher and launch chain under one lock; delete the three-stores section's mirror and manifest bullets (state the fork has `EmptyCatalog`/`EmptyFeatures` only).
   - Delete "The Cadence contract" section entirely.
   - Keep the recommender, seam, jobs, `plays` and testing sections, updated for `make_config`.
3. `CONTEXT.md`: add the terms `data dir`, `scan job`, `music folder`, `tombstone (removed_at)` and `launch chain`, one or two lines each. Remove terms that only name the mirror, the manifest or Cadence.
4. Every command and path you write must exist in the repo. Verify each with `ls` or `grep` before writing it.
**New symbols:** none
**Verify:** `grep -niE "tailnet|tailscale|cadence|download_manifest|spotify_clean|muse-backup|systemd" README.md CLAUDE.md CONTEXT.md`. Expected: only lines that explicitly say a thing was removed from the fork (no instructions).
**Forbidden:** no owner home paths (`/home/`), no personal library facts (track counts, artist names), no mention of the unlock password; don't edit PRODUCT.md, DESIGN.md or LAYOUT.md.
**Depends:** Task 12
**Why:** Spec line 33: this repo becomes public. Its docs must describe the desktop app, not the owner's server.
