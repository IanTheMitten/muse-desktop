# Developing Muse

Commands, environment and the parts of the system a contributor touches.
Architecture in depth is in [CLAUDE.md](../CLAUDE.md), the glossary in
[CONTEXT.md](../CONTEXT.md) and the design decisions in [adrs/](adrs/).

## Requirements

- Python 3.12, managed with [`uv`](https://docs.astral.sh/uv/)
- Node 22
- Rust, only for the desktop shell and for freezing the worker

## Run the server

```sh
uv sync
cd web && npm ci && npm run build && cd ..
uv run python -m muse
```

The server binds `127.0.0.1` on a free port and logs a line like
`Open http://127.0.0.1:<port>/?t=<token>`. Open that URL in a browser. The
token is new on every launch; set `MUSE_PORT` to pin the port.

Add your music folders in the app's settings. Muse scans them at launch and
watches them for changes.

## Run the desktop shell

A Tauri shell wraps the server in a native window. To build and run it from
source, for development:

```sh
npm --prefix web ci
npm --prefix web run build
uv run --group package python scripts/freeze_sidecar.py
bash scripts/tauri.sh dev
```

`scripts/tauri.sh` shares one `CARGO_TARGET_DIR` (`~/.cache/muse-tauri-target`)
across worktrees. A headless build and smoke test:

```sh
uv run python scripts/shell_smoke.py --build --expect mp3,flac,wav
```

## Run the ML worker from source

Every model runs in a separate process, the ML worker
(`muse/worker_main.py`, JSON lines on stdin and stdout). To use it from a
source checkout:

```sh
uv sync --extra embed
MUSE_EMBED_BATCH=1 MUSE_WORKER_CMD="uv run --extra embed python -m muse.worker_main --weights <dir>" uv run python -m muse
```

`<dir>` holds one folder per model under the layout the installer writes
(`<dir>/mert`, `<dir>/clap`, `<dir>/minilm`, `<dir>/audio_va`,
`<dir>/lyrics_va`, `<dir>/laya`).

Freezing and publishing the worker is in [RELEASING.md](RELEASING.md).

## Data dir

`MUSE_DATA_DIR` (default `./data` from source) holds everything Muse writes.
Each path can be overridden on its own: `MUSE_LIBRARY_DB`, `MUSE_LYRICS_ROOT`,
`MUSE_ART_ROOT`, `MUSE_MUSIC_ROOT`, `MUSE_SETTINGS`.

## Tests

```sh
uv run pytest                        # skips slow/model tests
uv run --extra embed pytest -m slow  # the real CLAP/MiniLM/MERT/ALS tests
cd web && npm test                   # vitest
npm --prefix web run e2e             # Playwright
```

Recommender changes are measured with the offline harness, before and after:

```sh
uv run python -m muse.reco_eval
```
