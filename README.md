# Muse desktop

A local music player and recommender for your own music folders. No account,
no server: it runs on your machine and only your machine can reach it.

Status: alpha. Run it from source for now; unsigned builds come in M3.

## Requirements

- Python 3.12, managed with [`uv`](https://docs.astral.sh/uv/)
- Node 22

## Run it

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

## Where data lives

Everything Muse writes goes under one data dir: `MUSE_DATA_DIR`, default
`./data`. It holds:

- `library.sqlite3`: tracks, plays, ratings, jobs
- `lyrics/`: `.txt` and `.lrc` files keyed by track id
- `art/`: album and artist images
- `settings.json`: your music folders
- `downloads/`: output of Add, scanned like a music folder
- `models/`: arrives with smart features (M4)

Your music folders are never written to.

## Tests

```sh
uv run pytest
```

## More

- Commands and architecture: [CLAUDE.md](CLAUDE.md)
- Glossary: [CONTEXT.md](CONTEXT.md)
- Product voice: [PRODUCT.md](PRODUCT.md)
- Decisions: [docs/adrs/](docs/adrs/)

## Licenses

- MMS-FA (CC-BY-NC-4.0): arrives with smart features (M4)
- schufo lyrics-aligner (MIT): arrives with smart features (M4)
