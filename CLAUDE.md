# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Muse desktop — a local music player and recommender for your own music
folders, with no account and no server. One FastAPI process serves the
React UI and the API on loopback; a Tauri shell (later milestones) wraps it
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
uv run pytest -m slow                       # the real CLAP/MiniLM/ALS tests (needs the embed extra)
uv run python -m muse         # start the server on 127.0.0.1 and print the launch URL
uv run python -m muse.taste_retrain  # standalone taste-embedding retrain
uv run python -m muse.reco_eval      # offline recommender harness, read-only (~4 min)
uv run python -m muse.arc_eval       # offline arc harness, read-only
uv sync --project universal_reco     # the content leg's own venv (retired in M4)
uv run python -m muse.content_embed_batch  # library-wide MERT content-embed batch
```

`universal_reco/` is a decoupled sibling project with its own `pyproject.toml`,
lockfile, and virtualenv; `muse` never imports it. `muse/content.py` shells out
to `python -m content_embed.batch` over a stdin/stdout JSON line protocol. It is
retired in M4, when the ML worker replaces it. The default test run never
touches it.
Optional dependency groups: `embed` (torch/transformers/sentence-transformers)
and `ingest` (yt-dlp/mutagen/lyricsgenius). The default test run and the app
itself never import torch unless `config.embed_batch` is on or
`ImplicitAlsTrainer` is explicitly constructed — everything else runs against
deterministic stub encoders/trainers.

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
the lyrics sync backfill, the art backfill, and (launch only, if the last
retrain is over a day old) a taste retrain, one after another under a single
`asyncio.Lock`, because the shared connection cannot hold two transactions.
The watcher (`muse/watch.py`) and `POST /api/scan` take the same lock by
calling the same chain. `muse/scanner.py` reads tags (`muse/tags.py`, mutagen,
read-only) and upserts tracks; a file that disappears gets `status = 'missing_file'`
rather than a deleted row, and a row with `removed_at` set is never revived.

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
stubs. This is the only module that imports torch, and only inside a real
encoder's `__init__`.

**Jobs** (`muse/jobs.py`, `muse/library/jobs.py`) are the one mechanism for
long-running background work — `download`, `scan`, `art_backfill`, `embed`,
`taste_retrain` — all recorded as rows in the `jobs` table so `/api/health`
and `/api/jobs` see every kind uniformly, whether it ran inside the API
process or as a standalone `python -m muse.taste_retrain` run.
`JobBus` is an in-process SSE fan-out (`/api/jobs/stream`) and only exists
inside the long-lived API process — the standalone retrain has no live progress
bar for this reason.

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
