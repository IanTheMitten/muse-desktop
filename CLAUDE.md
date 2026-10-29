# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Muse desktop — a local music player and recommender for your own music
folders, with no account and no server. One FastAPI process serves the
React UI and the API on loopback; a Tauri shell (`src-tauri/`) wraps it
in a native window. It plays the folders you point it at, recommends what to
play next from audio/lyrics/emotion/taste, shows and repairs lyrics, and
fetches cover art. This repo is the public fork of the owner's private
server build; the spec is `docs/specs/2026-10-29-desktop-app-fork.md`.

Read `PRODUCT.md` for the product register/voice and `docs/adrs/*.md`
before touching `plays` or the recommender's legs — each documents a
deliberate rejection of the obvious alternative design.

## Commands

Backend (from repo root, Python 3.12, managed with `uv`):
```sh
uv sync                       # install deps (core only; see extras below)
uv run pytest                 # run the test suite (skips slow/model tests by default)
uv run pytest tests/test_reco.py            # single file
uv run pytest tests/test_reco.py::test_name # single test
uv run --extra embed pytest -m slow         # the real CLAP/MiniLM/MERT/ALS tests (needs the embed extra)
uv run python -m muse         # start the server on 127.0.0.1 and print the launch URL
uv run python -m muse.taste_retrain  # standalone taste-embedding retrain
uv run python -m muse.reco_eval      # offline recommender harness, read-only (~4 min)
uv run python -m muse.arc_eval       # offline arc harness, read-only
uv run python -m muse.content_embed_batch  # library-wide MERT content-embed batch, through the worker
uv run python -m muse.lyric_tag_batch      # library-wide lyric tagging, through the worker (Laya)
uv run --extra embed python -m muse.worker_main --weights <dir>   # the ML worker by hand (JSON lines on stdin)
uv run --extra embed --group package python scripts/freeze_worker.py   # PyInstaller onedir worker -> build/muse-worker-<triple>.zip
```

Desktop shell (from repo root; needs Rust and `web/node_modules`):
```sh
uv run --group package python scripts/freeze_sidecar.py   # PyInstaller sidecar -> src-tauri/binaries/muse-core-<triple>
bash scripts/tauri.sh dev                                 # shell in dev mode
bash scripts/tauri.sh build --debug --no-bundle           # debug binary, no installer
uv run python scripts/shell_smoke.py --build --expect mp3,flac,wav   # headless build + smoke, prints SHELL-SMOKE-OK
```
`scripts/tauri.sh` runs the Tauri CLI with one shared `CARGO_TARGET_DIR`
(`~/.cache/muse-tauri-target`) so worktrees do not each build their own copy.
CI (`.github/workflows/package.yml`: `workflow_dispatch` and tags `v*`,
`ci-package-*`) packages macOS and Windows and does not use the wrapper.

**The ML worker.** Every model runs in a separate process, not in the API
process. `muse/worker_main.py` is the worker: a JSON-line server (one request
per stdin line, one reply per stdout line, `PROTOCOL = 1`) with ops `hello`,
`clap_audio`, `clap_text`, `minilm`, `audio_va`, `lyrics_va`, `mert` and `laya`.
Its models are `muse/embed.py` (CLAP, MiniLM, the two VA encoders),
`muse/mert.py` (MERT) and `laya`; `muse/audio_decode.py` decodes audio with PyAV.
The sidecar side is `muse/worker.py` (`WorkerClient` and the adapters that fit
the `AudioEncoder`/`LyricsEncoder` protocols) and `muse/smart.py` (the
smart-features installer and `muse/smart_manifest.json`). The worker is
`MUSE_WORKER_CMD` when set, else `<models dir>/worker/muse-worker/muse-worker`.
`config.embed_batch` (`MUSE_EMBED_BATCH`) turns the ML steps on without an
installed worker; otherwise they run when `smart.installed()`.

**Torch rule.** Only the worker imports torch, transformers,
sentence_transformers, laya or av, and only inside a real encoder's
`__init__`/handler. The sidecar (`muse/api.py`, `muse/worker.py`,
`muse/smart.py`) never does. Optional dependency groups: `embed`
(torch/transformers/sentence-transformers/av/laya) and `ingest`
(yt-dlp/mutagen/lyricsgenius). The default test run and the app itself never
import torch; `ImplicitAlsTrainer` imports `implicit` only when explicitly
constructed. Everything else runs against deterministic stub
encoders/trainers.

Frontend (from `web/`, Node 22/npm):
```sh
npm ci
npm run dev       # vite dev server
npm run build     # tsc -b && vite build -> web/dist, served by the FastAPI app
npm run preview
npm test           # vitest, node environment (pure modules in web/src/player)
```
No lint/format tooling is configured for either side (no ruff/mypy/eslint
config present) — don't invent one unasked.

## Architecture

**One process, one writer.** `muse/api.py:create_app` builds the whole
FastAPI app; `muse/__main__.py` (`main()`) is the only entrypoint that binds
sockets and runs it: `127.0.0.1` only, on a free port unless `MUSE_PORT` is
set. It prints `MUSE_LISTENING <port>` to stdout and logs
`Open http://127.0.0.1:<port>/?t=<token>`. The token is generated per launch
(or taken from `MUSE_TOKEN`) and every `/api` request must carry it.
At startup the lifespan migrates `library.sqlite3`, reaps any job left
`running` by a killed process, and starts the **launch chain** and the folder
watcher as `asyncio` tasks — never inside a request handler.

**The launch chain** (`run_chain` in `muse/api.py`) runs the folder scan,
the lyrics sync backfill, the art backfill, then (when `config.embed_batch` or
`smart.installed()`) the embed backfill, the MERT content batch and Laya lyric
tagging, all through the worker, and (launch only, if the last
retrain is over a day old) a taste retrain, one after another under a single
`asyncio.Lock`, because the shared connection cannot hold two transactions.
The watcher (`muse/watch.py`) and `POST /api/scan` take the same lock by
calling the same chain. `muse/scanner.py` reads tags (`muse/tags.py`, mutagen,
read-only) and upserts tracks; a file that disappears gets `status = 'missing_file'`
rather than a deleted row, and a row with `removed_at` set is never revived.

**The desktop shell** (`src-tauri/`) starts on a local boot page
(`src-tauri/boot/index.html`) that polls the `boot_status` command. It spawns
the frozen sidecar (`scripts/freeze_sidecar.py`) with a random per-launch
`MUSE_TOKEN`, and when the sidecar prints `MUSE_LISTENING <port>` navigates the
main window to `http://127.0.0.1:<port>/`; each window's `on_navigation` allows
only the boot origin and `127.0.0.1`. The token reaches the UI as
`window.__MUSE_TOKEN__` from an initialization script, which `web/src/token.ts`
prefers over `?t=`. The shell never kills the sidecar: it drops the child on
exit, which closes its stdin, and with `MUSE_PARENT_WATCH=1` `muse/__main__.py`
exits on that EOF. One capability (`src-tauri/capabilities/default.json`,
remote `http://127.0.0.1:*`) covers the boot page and the loopback pages; app
commands (`boot_status`, `smoke_report`, `open_mini`, `show_main`) are declared
in `build.rs` through `AppManifest` and granted as `allow-<command>`. Web code
reaches `window.__TAURI__` only through `web/src/shell.ts` (`undefined` in a
browser). Events: `muse:command` (`toggle`/`next`/`previous`, tray or mini
window to main), `muse:now` (main to mini) and `muse:hello` (mini to main). The
data dir is `MUSE_DATA_DIR` or Tauri's `app_data_dir()`; sidecar output goes to
`<data dir>/muse.log`. Dropped files and folders go to `POST /api/library/import`.

**One data dir.** `muse/config.py` (`Config.from_env`) puts every path under
`MUSE_DATA_DIR` (default `./data`), each overridable on its own:
- `library.sqlite3` (`MUSE_LIBRARY_DB`) — the one writable store. Tracks,
  plays, ratings, player state, jobs, embeddings, taste embeddings. Migrated by
  a plain append-only ladder in `muse/library/schema.py` (never edit a shipped
  migration).
- `lyrics/`, `art/`, `downloads/` (`MUSE_LYRICS_ROOT`, `MUSE_ART_ROOT`,
  `MUSE_MUSIC_ROOT`) and `settings.json` (`MUSE_SETTINGS`, the music folder
  list).

The user's music folders are configured at runtime, not by env: `GET
/api/settings`, `PUT /api/settings/music_folders`, and `POST /api/scan`. They
are never written to. The fork has no catalog or audio-features mirror;
`muse/catalog.py` and `muse/features.py` keep only `EmptyCatalog` and
`EmptyFeatures` (lookups return nothing, so there are no duration/ISRC or
mood-surface facts from a mirror). `muse/discover.py` is the MusicBrainz
browse path, run inline with a request because a person is typing and waiting.
`POST /api/jobs` takes `"mb"` for one track or `"mb_release"` for a whole
record; both fan out to per-track `download` jobs carrying every resolved fact.

**Edits are write-through and removal is a tombstone.** A user edit to a tag
is recorded in `track_overrides(track_rowid, field, value)` and also written
into the `tracks` columns, so every reader sees it with no COALESCE; the
scanner re-applies overrides after each tag read, and the audio file is never
written (`overrides.write`). Removal sets `status='removed'` plus `removed_at`
and never deletes the row: every reader already filters on `status`, so the
row drops out of browse, search and the recommender while plays and ratings
stay. `removal.restore` sets `status='missing_file'`, clears `removed_at` and
nulls `file_mtime` so the next scan flips it back to `done`. Move to Trash
(`send2trash`) is explicit and a failure leaves the row alone.

**`muse/library/`** is the DB access layer — one module per concern
(`plays.py`, `ratings.py`, `jobs.py`, `taste.py`, `embeddings.py`,
`insights.py`, `queries.py`, `state.py`, `art.py`, `features.py`,
`tracks.py`, `lyrics_state.py`, `playlists.py`, `overrides.py`, `removal.py`,
`artists.py`, `lyrics_edit.py`) — imported as `from . import library` in `api.py`, which then
calls `library.<name>` or the submodule directly (`library_taste`,
`job_rows`, etc.). `muse/library/db.py` and `schema.py` own the connection
and migrations; nothing else opens a raw `sqlite3.connect`.

**The recommender (slice 2)** fuses per-track similarity "legs" —
audio (CLAP **and** MERT content, sharing one weight per ADR 0004), Spotify-style
features (null in the fork, which has no feature mirror), lyrics (MiniLM), emotion (valence-arousal), and a learned taste
embedding — into one `rank()` score. Each leg's similarity is first mapped
through its own empirical CDF over random track pairs (ADR 0008), so a
missing leg does not move the score. Measure any scoring change with
`uv run python -m muse.reco_eval` before and after, and compare the two runs
(the owner's baseline note is not in this fork).

**The seam is split in two on purpose.** `muse/reco.py` is pure — the
per-leg math plus `rank()` itself, importing only `math`, with a test
asserting it never reaches for a database connection. `muse/arc.py` holds the same purity contract for the arc engine's curve,
filters and version key — importing only `math`, `re`, `typing` (ADR 0009). `muse/recommend.py` is
the impure half: it loads vectors, reads corpus statistics, evaluates the
content leg's coverage gate, retrieves candidates, and applies the
constraints that are not evidence (explicit ratings, artist spacing,
freshness, the relaxation ladder). The split is what lets story 44's learned
scorer replace `rank()` without touching anything else, so keep it — a
`Connection` in `reco.py` breaks the seam even when the tests still pass.

Radio and playlists are `GET /api/arc/{seed_rowid}` and `POST /api/arc` (a
request spec in the body), both planned by `arc_engine.plan` in
`muse/arc_engine.py`: a beam search over `arc.pick_cost` in which darkness and
theme are cost, not filters (ADR 0010). Theme comes from lyric tags and the
lyrics leg is off on that path. ADR 0009 retired archetypes, drift and the
curator.

Radio has **no server-side session**. The client owns the queue, so both
radio-shaped endpoints are stateless: `GET /api/recommend/{track_rowid}` (what
the UI's radio uses today) takes the moving query point as its path parameter
and the rolling exclusion set as `exclude=`, and `GET /api/arc/{seed_rowid}`
(`mode=radio`, with `pos`/`recent`/`exclude`; not wired into the UI yet) does
the same for the arc engine. See ADR 0003 for
why emotion uses Euclidean distance on a bounded VA plane instead of cosine,
and why the taste embedding (implicit-ALS over play-adjacency co-occurrence,
`muse/taste.py`) replaces the earlier hand-tuned `feedback_score` leg once
enough history exists. `muse/embed.py` defines the `AudioEncoder`/
`LyricsEncoder`/`*VAEncoder` protocol seam: real implementations
(CLAP/MiniLM/MER/TextVA) need torch and are constructed lazily via factories,
never at import time; tests and the default run use deterministic hash-based
stubs. Torch is imported only in the worker's modules (this one,
`muse/mert.py`, `muse/worker_main.py`), inside a real encoder's `__init__` or
a handler (see the torch rule above).

**Jobs** (`muse/jobs.py`, `muse/library/jobs.py`) are the one mechanism for
long-running background work — `download`, `scan`, `art_backfill`, `embed`,
`taste_retrain`, `align` — all recorded as rows in the `jobs` table so `/api/health`
and `/api/jobs` see every kind uniformly, whether it ran inside the API
process or as a standalone `python -m muse.taste_retrain` run.
`JobBus` is an in-process SSE fan-out (`/api/jobs/stream`) and only exists
inside the long-lived API process — the standalone retrain has no live progress
bar for this reason.
The `align` job (`run_align_job`) calls the worker's `align` op; `muse/align_text.py`,
`muse/ctc_align.py` and `muse/g2p.py` are pure, `muse/schufo.py` and
`muse/mms_align.py` run in the worker only.

**`plays` stores physical fact, never interpretation** (ADR 0001):
`ended_reason` is recorded exactly as the client reports it
(`track_end`/`user_next`/`user_prev`/`new_selection`/`pause_timeout`/`lost`)
and never turned into a derived `was_skip` at write time, because this table
is the one thing in the system that can't be regenerated if written wrong.
`PlayClose.known_reason` in `muse/api.py` is the enforcement point.

**Frontend** (`web/`, React 19 + TypeScript + Vite + react-router 7): views
in `web/src/views/`, shared player/job state via React context
(`web/src/player/PlayerContext.tsx`, `web/src/jobs/JobsContext.tsx`), API
calls centralized in `web/src/api.ts`. The player owns two `<audio>` elements so the next track can be preloaded for the gapless/crossfade handoff. Built output (`web/dist`) is served
directly by the FastAPI app as a mounted SPA (`muse/api.py`, catch-all
route); if `web/dist` doesn't exist, `/` serves a placeholder page instead of
failing.

## Testing conventions

`tests/conftest.py` provides a real (temp-file, migrated) `library.sqlite3`
connection rather than a mock — SQLite in a temp dir is fast and most
behavior worth testing is database behavior a mock wouldn't exercise.
`make_config(tmp_path, **overrides)` in `tests/conftest.py` builds a `Config`
with every path under `tmp_path` and every background task off; pass overrides
to turn one on.
`FakeCatalog`/`FakeFeatures` stand in for catalog and feature lookups. Tests marked
`slow` (needing real CLAP/MiniLM/ALS via the `embed` extra) are
excluded by default (`addopts = "-q -m 'not slow'"` in `pyproject.toml`); run
them explicitly with `-m slow` when changing encoder or trainer internals.

## Terminology

`CONTEXT.md` is the canonical glossary (leg, embedding batch vs. retrain job,
encoder protocol, emotion vec, feedback score, taste embedding) — check it
before introducing a new term for something it already names, and update it
when a term's meaning changes.
