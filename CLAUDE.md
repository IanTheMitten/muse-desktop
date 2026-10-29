# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Muse Player — a self-hosted, single-user music player and recommender served
from one FastAPI process, reached over a Tailscale tailnet (phone, laptop,
the server itself), never the public internet. It plays a local library,
recommends what to play next from audio/lyrics/emotion/taste, shows and
repairs lyrics, and grows the library without an interactive terminal script.

Read `PRODUCT.md` for the product register/voice, `docs/prds/2026-08-06-muse-player.md`
for the full spec, and `docs/adrs/*.md` before touching `plays`, the
manifest/library split, or the recommender's legs — each documents a
deliberate rejection of the obvious alternative design.

## Commands

Backend (from repo root, Python 3.14, managed with `uv`):
```sh
uv sync                       # install deps (core only; see extras below)
uv run pytest                 # run the test suite (skips slow/model tests by default)
uv run pytest tests/test_reco.py            # single file
uv run pytest tests/test_reco.py::test_name # single test
uv run pytest -m slow                       # the real CLAP/MiniLM/ALS tests (needs embed/taste extras)
uv run python -m muse         # start the server (binds tailnet addresses only, see muse/net.py)
uv run python -m muse.taste_retrain  # standalone nightly taste-embedding retrain
uv run python -m muse.reco_eval      # offline recommender harness, read-only (~4 min)
uv run python -m muse.arc_eval       # offline arc harness, read-only
uv sync --project universal_reco     # the content leg's own venv (separate deps)
uv run python -m muse.content_embed_batch  # library-wide MERT content-embed batch
```

`universal_reco/` is a decoupled sibling project with its own `pyproject.toml`,
lockfile, and virtualenv — it needs `torchaudio` and `torchcodec`, which the
`embed` extra does not have, and targets `>=3.10` against muse's `>=3.14`.
`muse` never imports it; `muse/content.py` shells out to
`python -m content_embed.batch` over a stdin/stdout JSON line protocol. The
default test run never touches it.
Optional dependency groups (`embed`, `taste`, `ingest`) pull in
torch/transformers/sentence-transformers, implicit/scipy, and
yt-dlp/mutagen/lyricsgenius respectively. The default test run and the app
itself never import the first two unless `config.embed_batch` is on or
`ImplicitAlsTrainer` is explicitly constructed — everything else runs against
deterministic stub encoders/trainers.

**On the deployed machine, sync with `uv sync --all-extras`, never
`uv sync --extra <one>`.** `uv sync` is exact: syncing for one group
uninstalls the others, and nothing announces it. This machine spent a day
holding `ingest` and not `embed` — every embed job died on `No module named
'torch'` while `/api/health` still reported the library fully embedded from
before. The systemd units in `deploy/` all run `uv run --all-extras` for the
same reason; a bare `uv sync` typed by hand is what undoes that.

Frontend (from `web/`, Node/npm):
```sh
npm install
npm run dev       # vite dev server
npm run build     # tsc -b && vite build -> web/dist, served by the FastAPI app
npm run preview
```
No lint/format tooling is configured for either side (no ruff/mypy/eslint
config present) — don't invent one unasked.

## Architecture

**One process, one writer.** `muse/api.py:create_app` builds the whole
FastAPI app; `muse/__main__.py` is the only entrypoint that binds sockets and
runs it (tailnet addresses only — no `0.0.0.0` fallback, see `muse/net.py`).
At startup the lifespan: migrates `library.sqlite3`, imports
`download_manifest.json`, syncs the Spotify audio-features mirror, reaps any
job left `running` by a killed process, and kicks off background art
backfill (default on) and embed backfill (default off, `MUSE_EMBED_BATCH=1`)
as `asyncio` tasks — never inside a request handler.

**Three SQLite stores with different lifecycles**, wired through
`muse/config.py` (`Config.from_env`, every path env-overridable):
- `library.sqlite3` — the one writable, backed-up store. Tracks, plays,
  ratings, player state, jobs, embeddings, taste embeddings. Migrated by a
  plain append-only ladder in `muse/library/schema.py` (never edit a shipped
  migration).
- `spotify_clean.sqlite3` / `spotify_clean_audio_features.sqlite3` — read-only
  17.8 GB / 29 GB catalog mirrors (`muse/catalog.py`, `muse/features.py`).
  Optional at startup: missing mirrors degrade quality (no duration/ISRC, no
  mood surfaces), never availability (`EmptyCatalog`/`EmptyFeatures`). The
  mirror is a static scrape frozen at 2025-11-13 (`Config.catalog_freeze`), so
  anything released after has no row in it at all. `muse/discover.py` is the
  only browse path (artist search, release groups by `kind`, tracklist), and
  the one MusicBrainz path that runs inline with a request rather than as a
  background job, because a person is typing and waiting. The mirror is read
  by key, never searched: at job build only, by ISRC through
  `track_id_for_isrc`, to stamp `track_id`. `POST /api/jobs` takes `"mb"` for
  one track, whose facts are already on the request because the browse screen
  rendered them, `"mb_release"` for a whole record, resolved server-side in
  two more MusicBrainz calls, or `"url"` for an explicit source. The two
  MusicBrainz kinds fan out to per-track `download` jobs carrying every
  resolved fact, because the MusicBrainz client has no disk cache and
  re-resolving at download time would re-ask for
  one release once per track. A post-freeze track gets both a local
  `track_rowid` and a local `album_rowid`, allocated above the same 1e9 floor
  that keeps them out of the mirror's own id space, so every track of one
  release still lands in one album.
- `download_manifest.json` — owned by `music_downloader.py`, not the app;
  imported into `library.sqlite3` at every startup. See ADR 0002 for why this
  is a permanent split, not a one-time cutover.

**`muse/library/`** is the DB access layer — one module per concern
(`plays.py`, `ratings.py`, `jobs.py`, `taste.py`, `embeddings.py`,
`insights.py`, `queries.py`, `importer.py`, `state.py`, `art.py`,
`features.py`) — imported as `from . import library` in `api.py`, which then
calls `library.<name>` or the submodule directly (`library_taste`,
`job_rows`, etc.). `muse/library/db.py` and `schema.py` own the connection
and migrations; nothing else opens a raw `sqlite3.connect`.

**The recommender (slice 2)** fuses per-track similarity "legs" —
audio (CLAP **and** MERT content, sharing one weight per ADR 0004), Spotify
features, lyrics (MiniLM), emotion (valence-arousal), and a learned taste
embedding — into one `rank()` score. Each leg's similarity is first mapped
through its own empirical CDF over random track pairs (ADR 0008), so a
missing leg does not move the score. Measure any scoring change with
`python -m muse.reco_eval` against the baseline in
`docs/notes/2026-10-09-reco-calibration-eval.md` before shipping it.

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
long-running background work — `download`, `art_backfill`, `embed`,
`taste_retrain` — all recorded as rows in the `jobs` table so `/api/health`
and `/api/jobs` see every kind uniformly, whether it ran inside the API
process or as a separate systemd-timer invocation (`muse.taste_retrain`).
`JobBus` is an in-process SSE fan-out (`/api/jobs/stream`) and only exists
inside the long-lived API process — the nightly retrain has no live progress
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
calls centralized in `web/src/api.ts`. Built output (`web/dist`) is served
directly by the FastAPI app as a mounted SPA (`muse/api.py`, catch-all
route); if `web/dist` doesn't exist, `/` serves a placeholder page instead of
failing.

**Deployment** (`deploy/`, see `deploy/README.md`): systemd **user** units
(not system — they need this user's `uv` and home directory), enabled via
`loginctl enable-linger`. `muse.service` runs the app; `muse-backup.timer`
(04:00 nightly) does a `VACUUM INTO` snapshot plus `rsync` push to a second
machine; `muse-taste-retrain.timer` (03:00 nightly, before backup) runs
`python -m muse.taste_retrain`. Both timers are `Persistent=true` so a
missed night (machine asleep) catches up rather than silently skipping.

## The Cadence contract

Cadence (the home voice system) calls a pinned subset of `/api`, listed in
`docs/contracts/cadence.md`. A change to it lands Muse-first, in one commit:
the code, `CONTRACT_VERSION` in `muse/contract.py`,
`uv run python -m muse.contract --write`, and the doc. Then Cadence syncs.
`tests/test_contract.py` and `tests/test_contract_shapes.py` fail until they
agree. Cadence does not use `/api/player/state`. A Cadence span must send a
heartbeat at least every 30 s while open, paused included, or the sweeper
closes it as `lost`. See ADR 0007.

**Leave what is in sync with Cadence alone.** Future work should not touch the
Cadence surface: the 8 operations in `CADENCE_SURFACE`, their params, request
bodies, status codes, the response fields pinned in
`tests/test_contract_shapes.py`, the six `ended_reason` values, or the
sweeper's heartbeat timing. Build around them. If a task seems to need such a
change, stop and ask the owner first — it is a contract bump that needs a
Cadence-side change, not a routine edit. Never "fix" a failing contract test
by editing the snapshot, the test, or the version to match.

## Testing conventions

`tests/conftest.py` provides a real (temp-file, migrated) `library.sqlite3`
connection rather than a mock — SQLite in a temp dir is fast and most
behavior worth testing is database behavior a mock wouldn't exercise.
`FakeCatalog`/`FakeFeatures` stand in for the multi-GB mirrors. Tests marked
`slow` (needing real CLAP/MiniLM/ALS via the `embed`/`taste` extras) are
excluded by default (`addopts = "-q -m 'not slow'"` in `pyproject.toml`); run
them explicitly with `-m slow` when changing encoder or trainer internals.

## Terminology

`CONTEXT.md` is the canonical glossary (leg, embedding batch vs. retrain job,
encoder protocol, emotion vec, feedback score, taste embedding) — check it
before introducing a new term for something it already names, and update it
when a term's meaning changes.
