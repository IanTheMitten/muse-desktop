# Technical spec - Muse desktop app: a public, standalone fork for other people's laptops

> Status: ready for planning. Feed to /autopilot-plan once ready - it verifies this line and refuses a spec that isn't "ready for planning".

## Source

No PRD. Goal stated by the owner on 2026-10-29:

- Release Muse as a standalone desktop app that anyone can open on a laptop and
  point at their own music. No server, no tailnet, no Cadence. The owner keeps
  running the existing tailnet server deployment for themself; that repo and its
  Cadence contract are untouched by this work.
- The downloader ("Add") is not a default feature. Anyone who wants it can
  still get it.
- Local-player functions: add from folders, edit and add lyrics, delete
  tracks, hide insignificant artists, change album art and artist images, edit
  tags, manual playlists, folder watch/rescan, and mp3/flac/etc playback.
- Laya, MERT, and lyric auto-alignment run locally. Auto-align runs only when
  the user asks for it.
- Lyrics and artwork are stored locally and never streamed from an API. That
  includes LRCLIB results, which are written to disk once.

Owner constraints carried in from memory and earlier decisions:
- No personal API keys in core paths.
- The audience listens to K-pop/J-pop, so lyrics features must handle ko/ja.
- The UI voice is PRODUCT.md's plain style.

## Architecture

There are two repos, and they share no code at runtime.

- **This repo (private, the owner's server)** stays as it is. Nothing in this
  spec edits `muse/`, the Cadence surface, or `deploy/`.
- **`muse-desktop` (new, public)** is a clean copy with no git history. It is
  cut from this repo's `main` now. It does not wait for
  `docs/specs/2026-10-28-musicbrainz-single-source.md`; the fork strips the
  mirror-bound routes itself (Decision 1).

Inside the fork there are three processes:

1. **Tauri v2 shell** (Rust, a thin layer). It owns the native window and
   loads the existing React/Vite UI from `web/`. It launches the sidecar at
   startup and kills it at quit. It handles the unlock shortcut's key events
   and passes them to the UI.
2. **Core sidecar.** This is the FastAPI app (`create_app`), frozen with
   PyInstaller and registered through `bundle.externalBin`, using
   target-triple file names and `tauri-plugin-shell`. It binds to `127.0.0.1`
   on a random free port, and the shell passes that port to the UI. It carries
   no torch. It contains:
   - the player, streaming, and the library DB layer
   - the folder scanner and watcher
   - all editing features
   - LRCLIB and art fetches
   - the MusicBrainz browse used by Add
   - the jobs and the JobBus
   - the recommender's pure half (`reco.py`, `arc.py`) and its impure half
     (`recommend.py`)
3. **ML worker (optional, downloaded on opt-in).** A second frozen binary
   containing:
   - torch CPU
   - MERT-v1-95M
   - CLAP
   - MiniLM
   - the VA heads
   - Laya (the `laya` package, imported in-process; no `laya-serve`)
   - schufo/lyrics-aligner
   - MMS-FA

   The core sidecar drives it over the stdin/stdout JSON-line protocol
   `muse/content.py` already uses for MERT. That protocol grows from one model
   to all of them, and the separate `universal_reco` interpreter goes away.

What the copy leaves out:
- `muse/contract.py`, `docs/contracts/`, and the Cadence routes, heartbeat
  sweeper, and contract tests
- `muse/net.py`'s tailnet binding (replaced by loopback)
- `muse/catalog.py` and `muse/features.py`'s real mirrors (the fork keeps only
  `EmptyCatalog`/`EmptyFeatures`), plus the mirror-bound `/api/catalog/*`
  routes and the `track`/`album`/`artist` branches of `POST /api/jobs`. Add
  uses the `mb` and `mb_release` paths only.
- `download_manifest.json` and `muse/library/importer.py`'s manifest path
  (replaced by the folder scanner)
- `music_downloader.py`, `legacy/`, `laya_tune/`, `deploy/`, `docs/notes/`
- the PRDs and plans tied to the owner's library
- `muse/systemone.py`'s hosted Jev backend
- `muse/prompt*.py` (NL prompt input was dropped on 2026-10-19)
- Genius (`lyricsgenius` needs a personal token)
- the owner's `MUSIC/` and DB files

The user data dir uses each platform's per-user app data directory. It holds:
- `library.sqlite3`
- `lyrics/` (`.txt` and `.lrc` files keyed by track id)
- `art/` (album and artist images)
- `models/` (worker binary plus weights)
- `bin/yt-dlp` (only after unlock)
- `downloads/` (Add's output, only after unlock; scanned like a music folder)
- `settings.json`

The user's music folders are read-only to the app. The one exception is
"Move to Trash", which needs explicit confirmation. Add never writes into them;
downloads land in the app-owned `downloads/`.

## Decisions

### Decision: 1. Public fork as a clean copy
- **Status:** Accepted
- **Context:** The owner keeps a private server deployment with Cadence, the
  47 GB mirrors, and private notes and training data. The public app needs none
  of that and must not leak it.
- **Options considered:** these, each with its trade-off:
  - (a) Same repo with a `Config.mode` flag for server or desktop. This needs
    the least new code, but the owner-specific code ships in a public tree.
  - (b) A git fork that pulls upstream. Fixes flow across, but the history
    leaks private docs and personal paths.
  - (c) A clean copy with no history, with fixes ported by hand.
  - (d) A shared core package. This means the least drift but the most
    refactoring up front.
- **Decision:** (c), picked by the owner. No private history or paths reach
  the public repo, and the fork is free to drop the 3.14, tailnet, Cadence, and
  mirror constraints.
- **Consequences:** Recommender and lyrics fixes made after the cut get
  ported by hand, and the two repos will drift. The cut happens now, picked by
  the owner, rather than waiting for the 2026-10-28 MusicBrainz single-source
  spec. So the fork deletes the `/api/catalog/*` routes and the mirror-bound
  `POST /api/jobs` branches itself, and the existing `muse/discover.py` is its
  only browse path.

### Decision: 2. Tauri v2 shell with a PyInstaller-frozen Python sidecar
- **Status:** Accepted
- **Context:** The targets are macOS (Apple Silicon) and Windows. The app has
  to feel like a desktop app, and the React UI and Python backend already
  exist.
- **Options considered:** these, each with its trade-off:
  - (a) pywebview plus PyInstaller. This is all Python and the least new code,
    but installers, signing, and updates are manual.
  - (b) Tauri v2 plus a Python sidecar. It has a `.dmg`/`.msi`/NSIS bundler,
    an updater, and signing hooks, but adds a Rust toolchain.
  - (c) Electron plus a sidecar. It has the most mature tooling but a shell of
    about 100 MB.
- **Decision:** (b), picked by the owner. Tauri's docs name PyInstaller-bundled
  Python API servers as a standard sidecar case. Sidecars are declared under
  `bundle.externalBin` (paths are relative to `src-tauri`), named
  `<name>-<target-triple>`, and need `tauri-plugin-shell`.
- **Consequences:** what this changes:
  - On Windows, an NSIS reinstall can silently fail to replace a sidecar that
    is still running (tauri-apps/tauri#15134). The installer must kill the
    sidecar and the worker first, and the shell must kill both at quit.
  - macOS signing and notarization of a PyInstaller binary inside the `.app`
    is known to be fiddly (see Open questions).

### Decision: 3. Loopback binding and the sidecar lifecycle
- **Status:** Accepted
- **Context:** `muse/net.py` refuses to start without Tailscale, which was
  deliberate for the server (PRD stories 2 and 5). The desktop app has no
  network surface, and a second user account on the same laptop must not reach
  it.
- **Options considered:** these, each with its trade-off:
  - (a) Bind `127.0.0.1` on a fixed port. This is simple, but it collides
    with other apps and with a second running instance.
  - (b) Bind `127.0.0.1` on a random free port, and have the shell pass the
    port plus a per-launch random token to the UI. Every `/api` request
    carries the token.
  - (c) A Unix socket or named pipe. The webview cannot fetch from it without
    a custom protocol bridge.
- **Decision:** (b). A random port avoids collisions. The token stops another
  local process from driving the API or reading the library over loopback.
  The token is generated by the shell, handed to the sidecar through an env var
  and to the UI through Tauri's init script, and never written to disk.
- **Consequences:** `web/src/api.ts` gains one header, and the audio
  `<audio src>` stream URL carries the token as a query parameter, since media
  elements cannot set headers. The app allows one running instance per user;
  a second launch focuses the existing window.

### Decision: 4. Optional ML worker, downloaded on opt-in, with a reminder at every launch
- **Status:** Accepted
- **Context:** the forces at play:
  - Every model depends on torch: CLAP, MiniLM, the VA heads, MERT, Laya, and
    both aligners.
  - Unpacked, the CPU torch wheel alone measured close to 1 GB in a 2021
    PyTorch forum report.
  - The app's core job is to open, add music, and play, which needs none of
    them.
- **Options considered:** these, each with its trade-off:
  - (a) Bundle torch in the sidecar. One build, but every user downloads
    1-2 GB or more.
  - (b) A separate worker that is downloaded on opt-in and driven over
    the existing JSON-line protocol.
  - (c) Export every model to ONNX Runtime. Smallest result, but six or more
    export and parity jobs, and MERT uses `trust_remote_code`.
- **Decision:** (b), picked by the owner.
  - The worker binary and the weights are downloaded together into
    `models/`. Each file is hash-pinned in a manifest shipped with the app,
    and downloads resume after an interruption.
  - The app shows a dismissible banner at every launch until the smart
    features are installed. Dismissing it hides the banner until the next
    launch only; there is no permanent opt-out.
- **Consequences:** what this changes:
  - Until the opt-in, every model leg is missing, and the recommender
    degrades through ADR 0008's per-leg CDF calibration, as it does today with
    a leg absent. Radio still works on taste plus whatever legs remain.
  - Each OS needs two frozen builds.
  - Worker crashes, missing files, or hash mismatches never block playback.
  - "Smart features" means:
    - embedding (CLAP, MERT, MiniLM, VA), run as a background job after
      install and on new tracks
    - lyric tagging (Laya)
    - auto-align, run only on request

### Decision: 5. Laya runs lyric tagging locally, replacing hosted Jev
- **Status:** Accepted
- **Context:** `muse/lyric_tag_batch.py` calls hosted Jev
  (`api.typesafe.ai`), which needs a key, against the no-personal-keys rule.
  Laya is the pip package `laya` (Apache-2.0, built on torch), base
  `convaiinnovations/laya` subfolder `multilingual`.
- **Options considered:** these, each with its trade-off:
  - (a) Laya replaces Jev for tags.
  - (b) Tags plus bringing back the NL prompt.
  - (c) No LLM in the desktop app, which drops theme and darkness.
- **Decision:** (a), picked by the owner. Laya is imported inside the ML
  worker and answers the same `system_one(state, questions)` shape
  `backend_ask` consumes (verified: `laya/agent.py:845` defines
  `system_one(state, questions, ...)` in laya 0.3.20). The tagger runs the
  owner's curated-song finetune (`prompt-finetune` branch, commits `bef7f77`
  and `6bbd1c4`), picked by the owner over the stock `multilingual` base.
- **Consequences:** what this changes:
  - Only the finetuned weights ship, in the smart-features download. The
    training data, the labels, the bundles, `laya_tune/`, and
    `docs/notes/prompt_eval_set.json` never enter the public repo or the
    download.
  - The finetune becomes a release dependency, so its checkpoint has to be
    located and exported before the worker can be built (see Open
    questions).
  - Arc-engine theme and darkness depend on the worker being installed.
  - `laya/onnx_agent.py` also exposes `system_one`, which leaves an ONNX
    route open later if torch-free tagging is ever wanted.

### Decision: 6. Lyric auto-align: schufo/lyrics-aligner for English, MMS-FA for everything else, no vocal split
- **Status:** Accepted
- **Context:** the forces at play:
  - The owner wants a light aligner that fits known lyrics to the audio, not
    a transcriber. It runs only when the user presses "Auto-align" on a track
    or a selection.
  - ko and ja must be covered.
- **Options considered:** these, each with its trade-off:
  - (a) stable-ts. MIT, multilingual, but runs Whisper, so heavier.
  - (b) WhisperX. Its default align models cover only en/fr/de/es/it.
  - (c) schufo/lyrics-aligner. MIT code, with weights in the repo. It aligns
    and separates vocals in one network, so it works on full mixes. It is
    English only (39 CMU/ARPAbet phonemes) and unmaintained since Nov 2021.
  - (d) Meta MMS-FA (`mms-300m-1130-forced-aligner` through
    `ctc-forced-aligner`). Multilingual through romanization, but
    CC-BY-NC-4.0.
  - (e) A Demucs vocal split before aligning.
- **Decision:** (c) for English lyrics plus (d) for everything else, with no
  vocal split in v1. All three picked by the owner.
  - English lyrics go to schufo. Its word-to-phoneme step uses offline g2p in
    place of the README's manual CMU web tool.
  - Any other language goes to MMS-FA.
  - The output is written as an `.lrc` in the data dir.
  - The existing tap and offset UI corrects any drift.
- **Consequences:** what this changes:
  - The app must stay free and non-commercial, because MMS-FA's weights are
    CC-BY-NC-4.0. Selling the app or adding a paid tier means replacing the
    non-English aligner first. The README and the about screen carry the
    attribution.
  - Lyric language detection decides the route.
  - MMS-FA on a full mix without separation will be weaker than schufo on
    English.
  - schufo's training data (MUSDB18) license is unchecked (see Open
    questions).

### Decision: 7. Tag edits live in a DB override layer; audio files are never written
- **Status:** Accepted
- **Context:** The app runs on strangers' libraries, which may be read-only,
  cloud-synced, or shared with another player. Corrupting a file cannot be
  undone.
- **Options considered:** these, each with its trade-off:
  - (a) An override layer in the DB.
  - (b) Write the tags with mutagen.
  - (c) Both, chosen by a setting.
- **Decision:** (a). An `overrides` row (track id, field, value) wins over the
  scanned file tag on every read and every rescan. Edits can be undone by
  deleting the row.
- **Consequences:** Edits do not appear in other players. Grouping by artist
  or album reads through the overrides. "Edit artist name" on one track can
  re-home it to another artist.

### Decision: 8. Lyrics and art live in the app data dir; files beside the audio are read once on import
- **Status:** Accepted
- **Context:** The owner wants lyrics and art stored locally and never
  streamed. Today the server writes `.lyrics.txt` and `.lrc` beside the audio
  and caches art on disk. Writing into strangers' music folders breaks
  Decision 7's no-write rule.
- **Options considered:** these, each with its trade-off:
  - (a) The data dir, keyed by track or album id.
  - (b) Sidecar files beside the audio.
  - (c) Blobs in SQLite.
- **Decision:** (a).
  - On import, the scanner copies any `.lrc`, `.txt`, or embedded cover art
    it finds beside or inside the audio into the data dir.
  - From then on, every write goes to the data dir: lyric edits, LRCLIB
    fetches (fetched once, never re-fetched), aligner output, and custom
    art.
  - The UI never contacts a third-party host; every image and lyric is served
    from the sidecar.
- **Consequences:** what this changes:
  - A lyric edit invalidates three things: the lyrics embedding, the lyric
    tags, and `lyrics_state`.
  - Editing the plain text while an `.lrc` exists marks the `.lrc` stale. The
    UI offers to re-align it or to keep the old timings.
  - Backing up means copying the data dir.

### Decision: 9. Folder import, folder watch, and track identity
- **Status:** Accepted
- **Context:** the forces at play:
  - The fork has no `download_manifest.json`, so a folder scan is the only
    importer.
  - Library rows are keyed on mirror rowids today, with post-freeze ids
    allocated above 1e9 by `next_local_rowid`.
  - Users move and rename files.
- **Options considered:** these, each with its trade-off:
  - (a) Identity by file path. This is simple, but a moved file becomes a
    new track and loses its plays.
  - (b) Identity by a content hash of the audio stream. This survives moves,
    but every file has to be read on the first scan.
  - (c) Path first, with a fallback to (size, duration, tag tuple) when the
    path disappears.
- **Decision:** (c).
  - Tracks get local rowids from `next_local_rowid`.
  - When a path vanishes and a new path appears with the same size,
    duration, and tags within one scan, the track moves instead of being
    recreated, so plays and edits survive.
  - The user picks folders under Settings, Music folders.
  - Folders are watched with OS file events and also rescanned at launch.
  - Tags are read with mutagen, which moves from the `ingest` extra into
    core.
- **Consequences:** what this changes:
  - A move done while the app is closed is still caught at the next launch.
  - If a file changes while the app is closed, its edits stay attached as
    long as the path is unchanged.
  - The FK-immediate rule applies: rows are inserted parent-first.

### Decision: 10. Delete asks each time; hiding an artist is a flag; manual art wins
- **Status:** Accepted
- **Context:** Plays are immutable fact (ADR 0001), so deleting a track cannot
  delete its history. A rescan must not bring back a removed track.
- **Options considered:** these, each with its trade-off:
  - Delete: (a) remove from the library only; (b) ask each time; (c) always
    move the file to Trash.
  - Artist: (a) hide from the Artists list; (b) delete the artist and its
    tracks; (c) merge into another artist.
- **Decision:** the parts, each settled:
  - **Delete track**, picked by the owner. A dialog offers two actions:
    - "Remove from library" tombstones the row (`removed_at`, and the path is
      remembered). The scanner skips that path, and plays are kept.
    - "Also move to Trash" also sends the file to the OS trash with
      `send2trash`. That library uses native trash calls on macOS and Windows
      through ctypes, with no compiler needed.

    A "Removed tracks" view in settings can restore a track.
  - **Hide artist**, picked by the owner. Sets `hidden=1`, which drops the
    artist from the Artists list and from artist search. Its tracks stay
    playable and stay under the albums they belong to. Settings lists hidden
    artists so they can be unhidden.
  - **Album art and artist images.** An uploaded image is stored with
    `source=manual`. The art backfill never overwrites `manual`. Artist images
    are new (no source exists today) and are upload-only in v1.
- **Consequences:** Trash moves are only as recoverable as the OS trash. The
  recommender skips tombstoned tracks.

### Decision: 11. The unofficial Add: a hidden unlock, then an app-managed yt-dlp kept up to date
- **Status:** Accepted
- **Context:** The owner wants Add off by default, obtainable by anyone who
  wants it, and with yt-dlp always up to date.
- **Options considered:** these, each with its trade-off:
  - (a) The user installs yt-dlp themself and the app detects it. Nothing is
    shipped or fetched, but the user handles updates.
  - (b) A separate build variant with yt-dlp bundled.
  - (c) A build-from-source flag only.
  - (d) A hidden unlock, after which the app fetches yt-dlp and keeps it
    updated.
- **Decision:** (d), designed by the owner.
  - **Unlock flow:**
    - Settings › Advanced › "External downloader" shows "Plugins are under
      development."
    - On that screen, Ctrl+Shift+Option+D (macOS) or Ctrl+Shift+Alt+D
      (Windows) opens an "Administrator password" prompt. The owner's
      original Shift+Fn+Ctrl+D was replaced because Fn is not delivered to
      apps on most laptops.
    - The password is `download-enjoy`, compared against a hash in the
      source, not the plain text.
    - A correct password sets `downloader_unlocked` in `settings.json`, which
      persists across launches. Settings has a "Lock again" option.
  - **After unlock:**
    - The app downloads the official yt-dlp standalone binary into
      `bin/` and runs `yt-dlp -U` at launch and before each download job.
      `-U` updates within the binary's current channel.
    - The Add screen appears. It uses MusicBrainz metadata through
      `muse/discover.py`, the `muse/ingest.py` provider seam, and the
      existing download jobs.
- **Consequences:** what this changes:
  - The password is not access control. The repo is public, so anyone can
    read the hash and the flow. The README must not describe it as a rights
    check. It is an easter egg that keeps casual users from finding a
    downloader.
  - The shipped binary can fetch a downloader, so the legal distance of
    option (a) is given up. The owner accepted this.
  - Download jobs need ffmpeg if yt-dlp merges formats (see Open questions).

### Decision: 12. Playback formats: native webview codecs only, with a canPlayType label
- **Status:** Accepted
- **Context:** `muse/streaming.py` and `api.py` hard-code `.m4a` as
  `audio/mp4`. The owner wants mp3, flac, and other formats. Windows
  WebView2 is Chromium (mp3, AAC, FLAC, Opus). macOS WKWebView plays mp3,
  AAC, and FLAC; Ogg/Opus in Safari may need 18.4 or later, which is
  unconfirmed.
- **Options considered:** these, each with its trade-off:
  - (a) Native playback only, with a `canPlayType` check.
  - (b) Bundle ffmpeg and transcode on the fly when a format is unsupported.
- **Decision:** (a).
  - Streaming sends the correct MIME type for each file extension.
  - The scanner imports `.m4a`, `.mp3`, `.flac`, `.wav`, `.ogg`, and `.opus`.
  - The UI calls `canPlayType` once per format at launch. A track whose
    format the webview cannot play shows "Format not supported on this Mac"
    (or "PC") in place of a silent failure.
- **Consequences:** Some Ogg files may not play on older macOS versions.
  ffmpeg transcoding stays a possible later addition.

### Decision: 13. Manual playlists, and the taste retrain moves in-app
- **Status:** Accepted
- **Context:** The owner picked manual playlists. `main` already ships them
  (`muse/library/playlists.py`, `/api/playlists`, and the `Playlists` and
  `PlaylistDetail` views). The server's nightly
  `muse-taste-retrain.timer` is a systemd unit, and the fork has no systemd.
- **Options considered:** these, each with its trade-off:
  - Playlists: (a) copy the existing playlists module and views into the
    fork; (b) reuse the arc `POST /api/arc` spec storage, which regenerates a
    list each time and so does not match "manual".
  - Retrain: (a) a job at launch when the last run is more than 24 h old;
    (b) an OS scheduler entry.
- **Decision:** the parts, each settled:
  - Playlists: (a). The existing module is copied as-is. The only new
    rule: a tombstoned track drops out of playlists. An earlier draft of
    this spec said "new tables"; that was wrong, because they already
    exist.
  - Retrain: (a). The ALS taste retrain needs implicit and scipy, not torch,
    so it stays in the core sidecar and runs as a `taste_retrain` job at
    launch when it is stale.
- **Consequences:** The app installs nothing in the OS scheduler. A laptop
  that is rarely opened retrains rarely, and that is fine.

### Decision: 14. Fork Python version: 3.12
- **Status:** Accepted
- **Context:** This repo pins `>=3.14`. `universal_reco` targets `>=3.10`.
  The fork freezes torch, torchaudio, transformers, and laya with PyInstaller,
  and hook coverage plus wheel availability are widest on established
  versions.
- **Options considered:** these, each with its trade-off:
  - (a) Keep 3.14. Same as upstream, but torch, PyInstaller hook, and wheel
    coverage for 3.14 on macOS arm64 and Windows is unverified.
  - (b) 3.12 for both the sidecar and the worker.
- **Decision:** (b). The owner confirmed it in the stack round. The plan
  must check the exact torch, PyInstaller, and laya wheel availability for
  3.12 on macOS arm64 and Windows before pinning any version. No version is
  pinned here.
- **Consequences:** Any 3.13- or 3.14-only syntax in the copied code has to
  be rewritten, so plan time must grep for it.

### Decision: 15. Conventional player operations
- **Status:** Accepted
- **Context:** The owner asked for "other functions that local music players
  have". The existing app already has play, pause, next, previous, seek,
  volume, the queue, shuffle, repeat one and all, search, ratings, synced
  lyrics, playlists, and Media Session. The fork carries those over unchanged.
  The owner picked these additions: queue editing, bulk select and edit,
  gapless and crossfade, drag-drop, sort and filter, and a duplicate finder.
  `PlayerContext` exposes only `enqueue` today.
- **Options considered:** these, each with its trade-off:
  - Gapless: (a) two `<audio>` elements, with the next one preloaded and
    started just before the current one ends, and the crossfade done with
    the existing `player/gain.ts` gain nodes. Near-gapless: a few ms can
    remain. (b) Web Audio buffers decoding whole tracks. Sample-exact, but
    about 50 MB of RAM per track, a slow start, and seek has to be rebuilt.
    (c) Crossfade only, no gapless.
  - Duplicates: (a) a metadata match on normalized title plus artist
    (`muse/titles.py` `normalize_title`) with duration within 2 s. (b) A
    Chromaprint fingerprint, which needs the fpcalc binary. (c) Metadata plus
    MERT vector similarity.
- **Decision:** the parts, each settled:
  - **Gapless and crossfade:** (a), picked by the owner. Crossfade is off by
    default, set from 0 to 12 s in settings. Range streaming and seek stay as
    they are.
  - **Duplicate finder:** (a), picked by the owner. A settings view lists the
    duplicate groups. The user picks the copy to keep, and the rest go
    through Decision 10's ask-each-time delete. Plays stay on the tombstoned
    row and are never reassigned (ADR 0001).
  - **Queue editing:**
    - "Play next" inserts after the current track and "Add to queue"
      appends.
    - Drag reorders and ✕ removes.
    - This is client-only in `PlayerContext`, since the client owns the
      queue and there is no server session.
  - **Bulk select and edit:** shift- or cmd-click to multi-select. One edit
    writes one override row per selected track (Decision 7). A bulk delete
    asks once for the whole selection. A bulk add-to-playlist is also
    available.
  - **Drag-drop:**
    - Dropping a folder adds it as a watched music folder.
    - Dropped files are referenced in place, imported, and rescanned at
      launch.
    - It uses Tauri's drag-drop event, because an HTML drop exposes no real
      file paths.
  - **Sort and filter:** the library list routes sort server-side by date
    added, play count, year, and duration.
- **Consequences:** what this changes:
  - Crossfade ends the outgoing track early. Its span closes with
    `track_end` at the moment the fade starts, and the recorded position
    shows where it stopped, so ADR 0001's rule that a play records physical
    fact holds.
  - Near-gapless is not sample-exact. A live album may show a gap of a few
    ms.
  - A song tagged with two spellings is not caught as a duplicate.
  - All recommendations except gapless and duplicates were presented to the
    owner, who raised no objection.

### Decision: 16. Mini-player and tray
- **Status:** Accepted
- **Context:** The owner picked a mini-player and a tray. Playback runs
  inside the main window's webview, and moving audio between windows cuts it.
- **Options considered:** these, each with its trade-off:
  - (a) Audio stays in the main window. Closing the window hides it to the
    tray. The mini-player is a second, small, always-on-top Tauri window that
    only sends commands. It needs a command channel, but playback is never
    interrupted.
  - (b) Shrink the main window into mini mode. One window, but no
    always-on-top player alongside a full library view.
- **Decision:** (a), recommended, and the owner raised no objection.
  - The mini-player and the tray menu (play/pause, next, show, quit) send
    commands to the main window through Tauri events.
  - Quit is the only way to stop the sidecar.
- **Consequences:** what this changes:
  - The app keeps running after its window is closed. The first time the
    window is closed, the app says so once ("Muse is still playing in the
    tray").
  - On Windows the NSIS kill-before-install rule (Decision 2) also covers
    a hidden instance that is still running.

## Data flow

- **Launch:**
  1. The shell starts the sidecar with a port and a token.
  2. The sidecar migrates `library.sqlite3` and reaps stale jobs.
  3. It starts the folder watcher and runs a rescan job.
  4. It runs a taste retrain if the last one is more than 24 h old.
  5. If the downloader is unlocked, it runs `yt-dlp -U` in the background.
  6. The UI loads, and shows the smart-features banner if the worker is not
     installed.
- **Import:**
  - The scanner job walks the folders and reads tags with mutagen.
  - Each file is upserted against tombstones and the move heuristic.
  - Sidecar `.lrc` and `.txt` files and embedded art are copied into the
    data dir.
  - Each new track queues an LRCLIB fetch and an art backfill (online, once).
  - If the worker is installed, it also queues embedding and tagging jobs.
- **Playback:** `<audio src="/api/tracks/{id}/stream?t=<token>">` uses range
  requests (`muse/streaming.py`, unchanged apart from the MIME type).
  Plays are recorded as on the server, with ADR 0001 unchanged.
- **Edits:**
  - Tag edits write an `overrides` row.
  - Lyric edits write the data-dir file and invalidate the lyrics embedding,
    tags, and `lyrics_state`, then queue a re-embed and re-tag if the worker
    is present.
  - Art upload writes `art/` with `source=manual`.
- **ML:**
  - Jobs send one JSON line per item to the worker over stdin and read the
    results from stdout.
  - The worker is started lazily, on the first ML job, and is stopped when
    idle and at quit.
- **Smart-features install:**
  1. A job downloads the manifest of files into `models/`, resuming
     interrupted downloads.
  2. It verifies each file's hash.
  3. It marks the worker installed.
  4. It queues a library-wide embedding batch.
- **Add, only when unlocked:**
  1. MusicBrainz browse.
  2. A `POST /api/jobs` call with `mb` or `mb_release`.
  3. A download job runs yt-dlp into `<data dir>/downloads/`, picked by the
     owner over a user-chosen folder, then tags the file there.
  4. The track is imported like any scanned file.

## Error handling

- **Offline, or an online service down** (LRCLIB, Cover Art
  Archive/MusicBrainz, the smart-features host, yt-dlp updates):
  - Playback, the library, editing, and the installed smart features keep
    working.
  - The failed fetch becomes a failed job, shown in the jobs list with a
    plain message ("LRCLIB unreachable") and a retry button.
  - Art and lyrics fetches are never awaited inside a request.
- **Smart-features download:**
  - A partial download resumes.
  - A hash mismatch deletes the file and the job fails with a retry.
  - Disk full fails the job with "Not enough disk space: needs N GB".
- **Worker crash or missing binary:** the ML job fails, the legs behave as
  missing (ADR 0008), and the next ML job relaunches the worker once.
  Playback is never blocked.
- **Aligner returns nothing usable** (empty or too few onsets): no `.lrc` is
  written, and the track shows "Couldn't align, tap along instead".
- **Music folder unplugged or unreadable:** its tracks show "File missing".
  They are not tombstoned, so plugging the drive back in restores them.
- **Unplayable format:** the `canPlayType` label from Decision 12.
- **Move to Trash fails** (`OSError`): the track stays in the library and the
  error is shown. The row is never tombstoned while the file still exists in
  place.
- **Sidecar fails to start:** the shell shows "Muse couldn't start" with the
  sidecar's last log lines, and the log path is in the data dir.
- **yt-dlp `-U` fails:** the current binary keeps working and a warning shows
  on the Add screen.

## Testing strategy

- **Carried over:** pytest against a real temp SQLite database plus the stub
  encoders, clean-copied with the modules. The Cadence and contract tests are
  dropped along with their code.
- **New unit and DB tests:**
  - the scanner: new, changed, tombstoned, moved, and unplugged-folder cases
  - override precedence across rescans
  - the hidden-artist filter
  - `source=manual` art surviving a backfill
  - lyric-edit invalidation (embedding, tags, `lyrics_state`) and marking
    the `.lrc` stale
  - playlists CRUD, and a deleted track leaving its playlists
  - the unlock hash check
  - the loopback token being required on `/api` and on the stream URL
  - the MIME type for each extension
  - the worker protocol against a fake worker process
  - the smart-features installer: hash mismatch, resume, disk full
  - the aligner route: English to schufo, everything else to MMS-FA
  - queue edits (play next, reorder, remove) in `PlayerContext`
  - crossfade closing the outgoing span as `track_end` with its position
  - duplicate grouping: the title-normalization and 2 s duration window
  - the bulk override write
  - the library sort parameters
- **Slow tier (`-m slow`):** the real worker runs MERT, Laya, schufo, and
  MMS-FA on one short fixture clip each.
- **Packaging smoke:** a GitHub Actions matrix on macOS arm64 and Windows.
  1. Build the sidecar, the worker, and the Tauri app.
  2. Launch the app.
  3. Hit `/api/health` with the token.
  4. Play fixture `.mp3`, `.flac`, `.m4a`, and `.wav` files in the webview.
  5. Run the `canPlayType` check for Ogg/Opus.

  This matrix is also the check on WKWebView codec behaviour, which is
  unconfirmed.
- **Out of v1:** signed-installer, notarization, and auto-update tests.

## Tech stack

- Shell: Tauri v2 with `tauri-plugin-shell`; the sidecar is declared through
  `bundle.externalBin` (Decision 2).
- UI: the existing React 19, TypeScript, Vite, and react-router app from
  `web/` (Decision 2).
- Core sidecar: Python 3.12 (Decision 14) and FastAPI, frozen with PyInstaller
  (Decision 2). It uses:
  - mutagen for tags (Decision 9)
  - an OS file-event watcher (Decision 9)
  - send2trash (Decision 10)
  - implicit and scipy for the taste retrain (Decision 13)
  - requests for LRCLIB, CAA, and MusicBrainz
- ML worker: Python 3.12 and torch CPU, frozen with PyInstaller (Decision 4).
  It contains:
  - transformers: MERT-v1-95M, CLAP, MiniLM
  - the VA heads
  - `laya` (Decision 5)
  - schufo/lyrics-aligner with offline g2p, and `ctc-forced-aligner` with
    MMS-FA (Decision 6)
- Storage: SQLite (`library.sqlite3`) plus files in the per-user app data
  directory (Decision 8).
- Downloader: the yt-dlp standalone binary with its built-in `-U`, only after
  unlock (Decision 11).
- No version is pinned in this spec. The plan verifies each one.

## Open questions

- Deferred: macOS signing and notarization, and Windows code signing. v1 can ship unsigned builds with "right-click, Open" instructions. Signing needs a paid Apple developer account and a Windows certificate, which is a cost decision for release time, not build time.
- Deferred: where the ML worker binaries and weights are hosted (GitHub Releases on the public repo vs. Hugging Face for the weights). It affects only the URLs in the hash manifest, and the plan picks one when it builds the installer job.
- Deferred: whether yt-dlp downloads on the user's laptop need ffmpeg (for format merging or m4a remux). The plan checks which yt-dlp format selector the existing `muse/ingest.py` uses. If that selector needs ffmpeg, a static ffmpeg is fetched alongside yt-dlp after unlock, in the same way.
- Deferred: licenses for MUSDB18 and its lyrics extension, which schufo's weights were trained on. This matters only for commercial use, which Decision 6 already rules out. The plan records the finding in the README's attribution section.
- Deferred: where the owner's Laya finetune checkpoint lives (Kaggle output or a local dir from the `prompt-finetune` work), and whether the base `convaiinnovations/laya` weights' license allows redistributing a derived checkpoint. The `laya` package is Apache-2.0, but the weights' own license is unchecked. If it forbids redistribution, the fallback is the stock base model, which the user downloads from its own host. The plan verifies this before building the worker.
- Deferred: whether macOS WKWebView forwards the Media Session API to the system Now Playing widget and media keys. Windows WebView2 is Chromium and does. The packaging smoke matrix checks it on macOS, and if it fails, the plan adds a Tauri-side native Now Playing bridge. Media keys working is not optional; only how they are reached is open.
- Deferred: the offline g2p library that replaces schufo's manual CMU web step, and how lyric language is detected for aligner routing. Both are library choices inside the worker, verified at plan time. Neither changes the architecture.

## Provenance

- user-picked: Decision 1 (clean-copy fork); Decision 2 (Tauri plus sidecar);
  Decision 4 (opt-in worker plus a reminder at every launch); Decision 5
  (Laya replaces Jev); Decision 6 (schufo plus MMS-FA, no split; the owner
  supplied schufo); Decision 10 (delete asks each time, hide the artist);
  Decision 11 (the unlock flow and the password, designed by the owner);
  dropping Genius; the feature set (tag editing, playlists, formats, folder
  watch); Decision 15's picks (queue editing, bulk edit, gapless and
  crossfade with two audio elements, drag-drop, sort and filter, duplicate
  finder by metadata); Decision 16's mini-player and tray.
- recommended-and-accepted: Decision 3 (loopback with a random port and a
  token, one instance); Decision 7 (override layer); Decision 8 (data-dir
  storage); Decision 9 (path identity plus the move heuristic); Decision 12
  (native codecs); Decision 13 (playlist tables, retrain at launch);
  Decision 14 (Python 3.12, which still needs a wheel check at plan time);
  Decision 11's download location (data-dir `downloads/`); Decision 1's cut
  timing (cut now); the testing strategy. Decisions 3, 9 and 13 were first
  written before the owner saw them, then explained in plain terms and
  confirmed by the owner.
- assumed: none.
- overrides: Decision 11 replaced the recommended external-yt-dlp option with
  an app-managed download, at the owner's call. The unlock shortcut's Fn key
  was replaced with Option/Alt for detectability, and the owner confirmed it.
