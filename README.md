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
- Windows: `%LOCALAPPDATA%\io.github.ianthemitten.muse`

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
- `models/`: smart features (`weights/`, `worker/`, `installed.json`)

Your music folders are never written to.

## Smart features

Smart features let Muse listen to each song and read its lyrics, on your
computer, so Radio and Muse choose better next songs. They also power vibe
search and the lyric tags. Nothing leaves your computer except the download.

- **Install:** Muse offers it in a banner and in Settings > Smart features.
  It downloads the models (about 3.6 GB, the banner shows the exact size) and a
  program, the ML worker, that runs them. Files are checked against a
  hash list and a dropped download resumes where it stopped.
- **Where it installs:** `models/` in the data dir. Models go in
  `models/weights/`, the worker in `models/worker/`.
- **Not supported:** Intel Macs (PyTorch ships no wheel for them) and Linux.
  Settings says so there. The worker is offered once the owner publishes it.

To run the worker from source for development:

```sh
uv sync --extra embed
MUSE_EMBED_BATCH=1 MUSE_WORKER_CMD="uv run --extra embed python -m muse.worker_main --weights <dir>" uv run python -m muse
```

`<dir>` holds one folder per model under the layout the installer writes
(`<dir>/mert`, `<dir>/clap`, `<dir>/minilm`, `<dir>/audio_va`,
`<dir>/lyrics_va`, `<dir>/laya`).

To freeze the worker on the machine you will run it on (needs Rust, for the
target triple):

```sh
uv run --extra embed --group package python scripts/freeze_worker.py
```

It writes `build/muse-worker-<target triple>.zip` and prints its size and
sha256. The `package` workflow's `worker` job does the same on macOS and
Windows.

### Releasing the worker

Owner steps, after the CI run produces the zips:

1. Upload the five Laya files from `~/models/laya-muse-v4` (`encoder/config.json`,
   `model.safetensors`, `rl_agent_config.json`, `tokenizer/tokenizer.json`,
   `tokenizer/tokenizer_config.json`) to Hugging Face model repo
   `IanTheMitten/laya-muse-v4`. A different repo name means re-running
   `uv run python scripts/build_manifest.py --laya-repo <name>`.
2. Make the GitHub repo public. Create release `worker-v1` from the CI
   artifacts `muse-worker-aarch64-apple-darwin.zip` and
   `muse-worker-x86_64-pc-windows-msvc.zip`.
3. For each zip run
   `uv run python scripts/manifest_add_worker.py <triple> <zip> <url>`, commit
   `muse/smart_manifest.json`, and rebuild the app.

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

Smart features download these models from their original Hugging Face repos
(Laya's finetune comes from the owner's repo):

- MERT-v1-95M (CC-BY-NC-4.0)
- audeering wav2vec2 emotion model (CC-BY-NC-SA-4.0)
- CLAP larger_clap_music_and_speech (Apache-2.0)
- all-MiniLM-L6-v2 (Apache-2.0)
- j-hartmann emotion-english-distilroberta-base: its model card states no licence
- Laya, `convaiinnovations/laya` and the finetune built on it (Apache-2.0)

Two of those are non-commercial, so Muse with smart features is free and
non-commercial.

- PyAV decodes audio. PyAV is BSD; its wheels bundle FFmpeg's LGPL libraries.
- The asterisk in the logo is Phosphor's (MIT).
- MMS-FA (CC-BY-NC-4.0): arrives with smart features (M4)
- schufo lyrics-aligner (MIT): arrives with smart features (M4)
