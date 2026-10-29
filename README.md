# Muse desktop

A local music player and recommender for your own music folders. No account,
no server: it runs on your machine and only your machine can reach it.

Status: alpha. Run it from source, or build the desktop app below.

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

## Desktop app (alpha)

A Tauri shell wraps the server in a native window. To build and run it from
source on Linux, for development:

```sh
npm --prefix web ci
npm --prefix web run build
uv run --group package python scripts/freeze_sidecar.py
bash scripts/tauri.sh dev
```

macOS and Windows builds come from the `package` GitHub Actions workflow. It
runs on manual dispatch and on tags named `v*` or `ci-package-*`. The builds
are unsigned. On macOS, right-click the app and choose Open. On Windows, in the
SmartScreen warning choose More info, then Run anyway.

The desktop app keeps its data dir in the platform's app data folder, under
`io.github.ianthemitten.muse`:

- Linux: `~/.local/share/io.github.ianthemitten.muse`
- macOS: `~/Library/Application Support/io.github.ianthemitten.muse`
- Windows: `%APPDATA%\io.github.ianthemitten.muse`

`MUSE_DATA_DIR` overrides it. The server's log is `muse.log` in that dir; when
Muse can't start, the start screen shows its last lines and the path.

- **Tray:** closing the window hides Muse to the tray (menu bar on macOS), where
  the menu has Play/Pause, Next, Mini player, Show Muse and Quit Muse.
- **Mini player:** a small always-on-top window with the cover, title and
  previous, play/pause and next buttons.
- **Drop:** drag a folder onto the window to watch it, or a song to add it in place.

## Where data lives

Everything Muse writes goes under one data dir: `MUSE_DATA_DIR`, default
`./data` (the desktop app's default is above). It holds:

- `library.sqlite3`: tracks, plays, ratings, jobs
- `lyrics/`: `.txt` and `.lrc` files keyed by track id
- `art/`: album and artist images
- `settings.json`: your music folders and any songs you dropped in
- `muse.log`: the desktop app's server log
- `downloads/`: output of Add, scanned like a music folder
- `models/`: arrives with smart features (M4)

Your music folders are never written to.

## Editing your library

Edits never touch your files. Changes to a song's title, artist, album, track
number, disc number or year are stored in Muse's own database; Reset to file
tags drops them and the next scan reads the file again.

- **Remove** hides a song and keeps its play history. **Also move to Trash**
  sends the file to your system Trash as well; plain Remove never deletes a file.
  Restore removed songs in Settings > Removed songs.
- **Hidden artists** (Hide artist on an artist page) leave the Artists list.
  Their albums and songs still show. Unhide in Settings > Hidden artists.
- **Covers and artist pictures:** upload an image (JPEG, PNG or WebP, up to
  10 MB) on an album or artist page. An uploaded cover is never replaced by
  the automatic cover fetch.
- **Lyrics:** edit a song's lyrics from its More menu. If the song had synced
  timings, you choose to keep the old timings or remove them.

## Tests

```sh
uv run pytest
cd web && npm test
```

## More

- Commands and architecture: [CLAUDE.md](CLAUDE.md)
- Glossary: [CONTEXT.md](CONTEXT.md)
- Product voice: [PRODUCT.md](PRODUCT.md)
- Decisions: [docs/adrs/](docs/adrs/)

## Licenses

- MMS-FA (CC-BY-NC-4.0): arrives with smart features (M4)
- schufo lyrics-aligner (MIT): arrives with smart features (M4)
