# Plan - Muse desktop M2: editing, removal, queue editing, gapless, sort, duplicates

Source spec: `docs/specs/2026-10-29-desktop-app-fork.md` (Status: ready for planning; `spec-lint --handoff` exit 0, 1 warn no-source-prd). M1 plan: `docs/plans/2026-10-29-desktop-m1-core.md` (15/15 done, main at 9236bbd).

Repo: `~/Projects/muse-desktop`. Python commands run from the repo root, npm commands from `web/`. Release rule from M1 holds: `docs/plans/` and `docs/specs/` never ship.

## Scope

M1 milestone list, M2 line: tag overrides, delete/tombstone UI, hide artist, art and artist-image upload, lyric editing and invalidation, the playlists tombstone rule, queue editing, bulk select, gapless/crossfade, sort/filter, the duplicate finder, a web test runner. Folded M1 leftovers: Play all queues unsupported formats (Task 3), something sets `removed_at` (Task 8), music folder must not contain the data dir (Task 11), stale `muse.backup` comments (Task 1).

**Carried to M5, not in this plan:** `muse/ingest.py:484` calls `lyrics_lrclib._apply` without the now-required `lyrics_root`; a try/except swallows the TypeError. Dormant until the downloader returns. Drag-drop (Tauri event) stays M3.

## Assumptions (decided at planning)

- **Tag overrides are write-through.** Table `track_overrides(track_rowid, field, value)` is the record of a user edit (Decision 7). The edited value is also written into the `tracks` columns, so every existing query (artists, albums, FTS, recommender) reads it with no COALESCE. The scanner re-applies overrides after reading file tags on every re-read. Editable fields: `track_name`, `artist_name`, `album_name`, `track_no`, `disc_no`, `year`. Album artist is not editable in v1.
- **Album re-homing.** An `album_name` override rebuilds `album_key` as `<old album-artist part>\x1f<new album casefold>` and takes `album_rowid_for_key`, so editing the album name moves a track into an existing album with that key. An `artist_name` override leaves `album_key` alone (the track re-homes under Artists only). Whenever `album_rowid` changes, `art_path` is re-read from `album_art` for the new album.
- **Reset to file tags** deletes the override rows and sets `file_mtime = NULL`, so the next scan re-reads the file; the route kicks the chain. A track whose file is missing keeps the edited values until the file returns.
- **Tombstone = `status='removed'` plus `removed_at`.** Every read path already filters `status IN ('done','missing_file')` or `status = 'done'` (`grep -rn "status = 'done'\|status IN" muse`), so a removed row drops out of browse, search, recommender and health playable counts with no per-query edits. Restore sets `status='missing_file'`, `removed_at=NULL`, `file_mtime=NULL`; the next scan flips it to `done` if the file exists.
- **Playlists tombstone rule** is read-time: `playlists.get`/`summaries`/`_covers` skip `status='removed'` rows. Restoring a track brings it back into its playlists.
- **Move to Trash** uses `send2trash` (new core dep). A trash failure leaves the row untouched and is reported per track. Removal never deletes a file without the explicit Trash action.
- **No explicit `BEGIN` in the new write modules.** The shared connection cannot hold two transactions (M1 assumption), and a request handler can run while the chain is inside `record_cover`'s transaction. Writes are autocommit statements, ordered so a crash leaves a re-applicable state.
- **Hidden artists** are keyed by exact `artist_name` (`hidden_artists` table). Hidden drops the artist from `GET /api/library/artists` only; albums, songs and search are unchanged.
- **Artist images** live in `artist_images(artist_name, art_path, source)` and are upload-only (`source='manual'`), stored at `art/artists/<sha1(name)[:16]><ext>`.
- **Album art upload** writes `art/<album_rowid>-manual<ext>` and calls the existing `record_cover(..., source="manual")`. The backfill only asks albums with no `album_art` row (`muse/library/art.py:20-60`), so manual art is never overwritten; Task 12 pins that with a test.
- **Art caching.** `_art_response` switches from `immutable, max-age=31536000` to `no-cache` with an etag carrying the file's `st_mtime_ns`, so an upload shows on reload. Loopback makes revalidation free.
- **Uploads are raw request bodies** (`Content-Type: image/jpeg|image/png|image/webp`, max 10 MB). No `python-multipart` dependency.
- **Lyric edit** writes `lyrics/<rowid>.txt`, sets `tracks.lyrics_path`, nulls `embeddings.lyrics_vec/lyrics_hash/lyrics_model`, deletes the track's `lyric_tag_sets` row (tags cascade), and upserts `lyrics_state(state='manual', synced=0)`. A blank text deletes both lyric files and sets `lyrics_path=NULL`. When an `.lrc` exists after an edit, it is **stale**: `state='manual' AND synced=0 AND lrc exists`. The synced route then serves plain text plus `lrc_stale: true`; the UI offers "Keep old timings" (`synced=1`) or "Remove timings" (delete the `.lrc`). Re-align arrives with the M4 aligner.
- **Year** is a new `tracks.year` column read from the `date` easy tag (`TDRC` for WAVE). Migration 22 sets `file_mtime = NULL` on local rows so the next scan backfills it once.
- **Sort** is server-side on `GET /api/library/songs?sort=title|added|plays|year|duration&order=asc|desc`, NULLs last. The UI's choices: Title (asc), Recently added (added desc), Most played (plays desc), Year (desc), Length (duration desc). "Filter" stays the existing search field.
- **Duplicates:** groups of `status IN ('done','missing_file')` tracks with equal `(normalize_title(track_name), artist_name.casefold())`, clustered by `duration_ms` within 2000 ms of the cluster's first (shortest) member; tracks with NULL duration are never grouped.
- **Queue editing is client-only** (Decision 15) and touches only rows after the current index, so `index` never moves on an edit. Play next / Add to queue on an empty queue start playback of the given tracks.
- **Gapless/crossfade:** two `<audio>` elements. The next track is preloaded only when it is deterministic: not under shuffle, not under repeat-one (`upcomingIndex`). Shuffle keeps today's `ended`-then-load behaviour (a small gap). Crossfade seconds (0 to 12, default 0) is a per-device preference in `localStorage["muse.crossfade"]`, like volume (`web/src/player/gain.ts:25`), not `settings.json` (whose shape `tests/test_api_scan.py:27` pins). Crossfade is equal-power and capped at half the outgoing track. The outgoing span closes as `track_end` at the position where the handoff starts (spec line 516-519).
- **Web tests:** vitest 3.2.4, node environment, no jsdom. Pure modules (`queue.ts`, `crossfade.ts`, `formats.ts`) carry the logic the spec lists for `PlayerContext` tests; `PlayerContext` itself is checked by build, the phase gate and the oracle review.
- **Bulk select** ships on Library > Songs and the album page. Playlist detail keeps single-row actions only.
- **Dialogs** are native `<dialog>` with `showModal()` (focus trap and Esc for free). DESIGN.md §9's "modals are rare" rule is widened in Task 20 to name these four.

## Premises

- Spec passes handoff lint. Verified: `node ~/.claude/skills/spec-brainstorming/scripts/spec-lint.mjs --handoff docs/specs/2026-10-29-desktop-app-fork.md` (exit 0).
- Backend baseline: 1095 passed, 1 skipped, 3 deselected. Verified: `uv run pytest -p no:cacheprovider` on main 9236bbd.
- Schema is at 21; `tracks.removed_at` exists and nothing sets it. Verified: `muse/library/schema.py:674-683`, `grep -rn "removed_at" muse`.
- Scanner skips a path whose row has `removed_at` and its UPDATE path does not touch `art_path`. Verified: `muse/scanner.py:155-175`.
- No `artists` table exists; artists are `GROUP BY artist_name`. Verified: `muse/library/queries.py:51-72`.
- `record_cover` takes any `source` and the backfill only reads albums with no `album_art` row. Verified: `muse/library/art.py:48-58`, `muse/library/art.py:79-117`.
- The synced route serves `.lrc` lines whenever the file parses; `lyrics_state.synced` is not consulted. Verified: `muse/api.py:1342-1383`.
- The lyric tag queue re-asks a track with no `lyric_tag_sets` row. Verified: `muse/library/lyric_tags.py:88-103`.
- `playlist_tracks` rows cascade only on hard delete; reads join `tracks` with no status filter. Verified: `muse/library/playlists.py:58-84`.
- Seven queue builders filter on raw `t.playable` (8 expressions). Verified: `grep -rn "\.filter((t\(rack\)\?) => t\(rack\)\?\.playable)\|track?.playable" web/src` hits `Library.tsx:148,153`, `AlbumTracks.tsx:30`, `PlaylistDetail.tsx:31`, `Home.tsx:58,81`, `ArtistAlbums.tsx:40`, `Playlists.tsx:64`.
- `PlayerContext` renders one `<audio>` and exposes `enqueue` only; nothing outside it calls `enqueue`. Verified: `web/src/player/PlayerContext.tsx:46-92`, `web/src/player/PlayerContext.tsx:832-877`, `grep -rn enqueue web/src`.
- `gain.ts` holds one module-level element and gain node; only `PlayerContext` calls `attachGain`/`setGain`. Verified: `web/src/player/gain.ts:19-23`, `grep -rn "setGain\|attachGain" web/src`.
- vitest 3.2.4 installs against vite 6.4.3 on Node 22.22.1, runs a `.test.ts` in the node environment, and `tsc -b` stays green with test files under `src/`. Verified: scratch copy of `web/`, `npm install -D vitest@3.2.4`, `npx vitest run` (1 passed), `npx tsc -b` (exit 0).
- Node 22 has a global `navigator` (`userAgent` `Node.js/22`), so `formats.ts` imports under vitest with only `document` stubbed. Verified: same scratch run.
- `tests/test_api_scan.py:27` pins the exact `GET /api/settings` dict. Verified: read.
- `FileTags` is built with keyword arguments everywhere, so a defaulted trailing `year` field breaks no caller. Verified: `grep -rn "FileTags(" tests muse`.
- `scripts/smoke.sh` launches `python -m muse` on a fresh `MUSE_DATA_DIR`, gets 401 without the token, 200 with the header and with `?t=`, and finds 0 token lines in the log. Verified: `bash scripts/smoke.sh` prints `SMOKE-OK` (committed with this plan).
- No web test or exhaustive `Record<>` keys on `Track["status"]` or `Artist`. Verified: `grep -rn "Record<" web/src`.

## Memory carried into briefs

- **Smoke after startup/API changes:** `bash scripts/smoke.sh` must print `SMOKE-OK` (fresh data dir, token never logged).
- **`python` is not on PATH:** always `uv run python`.
- **Node 22 cannot run `.ts`:** web tests run through `npx vitest run`, never `node file.ts`.
- **TestClient hangs on `/api/jobs/stream`:** never open the SSE route in a test.
- **Repo-local git identity is set:** do not change git config.
- **`git commit -- <path>` fails on staged deletions:** `git add` then plain `git commit`.
- **FKs are immediate:** a `tracks` row exists before any child row.
- **Absolute `tmp_path` paths only** in scanner and lyrics tests.
- **Exit 139 at pytest teardown** is a known flake: rerun once.

## Design: track-actions

READ: Reading this as: list-row actions and multi-select for one listener managing their own library at a desk or on a phone, quiet utilitarian language, leaning toward the existing Muse record-wash system (DESIGN.md, LAYOUT.md).
DIALS: VARIANCE 2 / MOTION 2 / DENSITY 5
PALETTE: existing tokens only: --ink, --ink-raised (menu surface), --text, --text-2, --hairline, --glass (selection bar), --accent / --on-accent (primary action only). Selected rows: background --hairline plus inset 2px --text-2 ring. No new colour.
TYPE: display Newsreader Variable (unused here) / body Figtree Variable
SIGNATURE: a selected row keeps its place and gains a quiet ring, and a single glass bar rises above the player naming the count ("3 selected") with every bulk action in plain words, so selection never changes the list itself.
ANCHORS: Apple Music for Mac (row "..." menu with Play Next / Add to Queue / Get Info), Finder (cmd-click toggles, shift-click extends), Plexamp (one floating action bar above the player while selecting)
VARIETY: cohere, no rotation. Ledger 2026-09-03: "cohere with this system rather than rotating away from it". Fingerprint: labelled rail, art-led shelves, Newsreader + Figtree, record wash.
CONTENT: menu items "Play next", "Add to queue", "Add to playlist…", "Edit details", "Edit lyrics", "Remove from library…". Bar: "{n} selected", "Play next", "Add to queue", "Add to playlist", "Edit details", "Remove", icon button "Clear selection". Sort label "Sort by", options "Title", "Recently added", "Most played", "Year", "Length". No placeholder data.
FORMAT: production
TARGET: existing stack: web/src/components/TrackRow.tsx, TrackMenu.tsx (new), SelectionBar.tsx (new), TrackActions.tsx (new), selection.ts (new), web/src/styles/actions.css (new), views Library.tsx and AlbumTracks.tsx.
CONSTRAINTS: WCAG AA. Menu is role="menu" with role="menuitem" buttons, arrow keys move, Esc closes and returns focus to the More button. More button is 44x44 with aria-label "More for {title}". Selection state is in the accessible name (visually hidden ", selected"), never colour alone. Bar wraps to two rows at 320 px with no horizontal scroll and sits above the player (z-index 20, like .job-strip). Animate only opacity/transform; prefers-reduced-motion instant. No em dash.

## Design: edit-dialogs

READ: Reading this as: small modal forms for one listener correcting their own library, plain honest language, leaning toward the existing Muse system (the lyric offset popover on --ink-raised is the precedent).
DIALS: VARIANCE 2 / MOTION 2 / DENSITY 4
PALETTE: existing tokens only: dialog surface --ink-raised, inset 1px --hairline ring, ::backdrop rgb(0 0 0 / .5), text --text / --text-2, primary --accent / --on-accent, error text --text on --hairline (`.error`, `web/src/styles/add.css:9`). No new colour.
TYPE: display Newsreader Variable (dialog title only, 600) / body Figtree Variable (fields, help, buttons)
SIGNATURE: the Remove dialog states the consequence in one sentence ("Muse stops showing them and keeps their play history. You can restore them in Settings.") and gives the irreversible choice its own plainly named button, "Also move to Trash", never a checkbox.
ANCHORS: macOS Music "Song Info" sheet (stacked labelled fields, one Save), Finder "Move to Trash" confirmation (the destructive verb is the button label), Plexamp edit sheet (empty field means unchanged in multi-edit)
VARIETY: cohere, no rotation (same ledger note as track-actions).
CONTENT: EditDetails: title "Edit details" or "Edit {n} songs"; labels "Title", "Artist", "Album", "Track number", "Disc number", "Year"; multi help "Leave a field empty to keep each song's own value."; buttons "Save", "Reset to file tags", "Cancel"; reset help "Goes back to the tags in the file after the next scan." Remove: title `Remove "{title}"?` or "Remove {n} songs?"; buttons "Remove from library", "Also move to Trash", "Cancel"; trash help "Move to Trash also deletes the files from your music folder."; failure "Couldn't move {n} to Trash: {error}". AddToPlaylist: title "Add to playlist", rows "{name} · {n} songs", field "New playlist", button "Create". LyricsEdit: title "Edit lyrics", label "Lyrics", help "Saving replaces the lyrics for this song only.", buttons "Save", "Cancel". No placeholders.
FORMAT: production
TARGET: existing stack: web/src/components/Dialog.tsx (new) plus EditDetailsDialog.tsx, RemoveDialog.tsx, AddToPlaylistDialog.tsx, LyricsEditDialog.tsx (new), web/src/styles/dialogs.css (new).
CONSTRAINTS: WCAG AA. Native <dialog> + showModal(); Esc and Cancel close; focus starts on the first field (or the primary button) and returns to the opener. Label above every input, never placeholder-as-label. Inputs and textarea >= 16 px. 44 px targets. Width min(520px, 100vw - 32px), works at 320 px. Enter: opacity + translateY(6px) 0.2s --ease, reduced-motion instant. No em dash.

## Design: queue-edit

READ: Reading this as: direct manipulation of the Up next list inside Now Playing for one listener, quiet language, leaning toward the existing Muse system.
DIALS: VARIANCE 2 / MOTION 3 / DENSITY 5
PALETTE: existing tokens only: handle and remove glyphs --text-2 at rest, --text on hover/focus; the lifted row --ink-raised with the existing cover shadow (0 6px 20px rgb(0 0 0 / .35)). No new colour.
TYPE: display Newsreader Variable (unchanged) / body Figtree Variable
SIGNATURE: a grip at the row's leading edge lifts the row under the finger and the others make room by transform alone; the same grip moves the row one step with the arrow keys.
ANCHORS: Apple Music "Playing Next" (grip handles, remove per row), Spotify queue (drag within "Next in queue" only), iOS Reminders (lifted row shadow while dragging)
VARIETY: cohere, no rotation (same ledger note).
CONTENT: handle aria-label "Move {title}", remove aria-label "Remove {title} from queue", group headings unchanged ("Next from {album}", "Then Muse continues with"). No placeholders.
FORMAT: production
TARGET: existing stack: web/src/player/NowPlayingSheet.tsx (UpNext), web/src/components/TrackRow.tsx (`trailing`/`leading` slots), web/src/components/Icons.tsx (GripIcon, CloseIcon), web/src/styles/np.css.
CONSTRAINTS: WCAG AA. Both controls 44x44. Drag uses pointer events (mouse and touch), constrained to the row's own group. ArrowUp/ArrowDown on a focused grip moves one step and keeps focus on that grip. Animate transform only; prefers-reduced-motion: no transition. No em dash.

## Design: media-edit

READ: Reading this as: in-place editing of covers, artist pictures and lyrics on the screens that show them, plain honest language, leaning toward the existing Muse system.
DIALS: VARIANCE 2 / MOTION 2 / DENSITY 4
PALETTE: existing tokens only: secondary actions are `.btn` (--hairline fill), quiet ones `.link-btn` (--text-2). No new colour.
TYPE: display Newsreader Variable (artist name heading, existing .page-title.serif) / body Figtree Variable
SIGNATURE: the artist page gains a round portrait beside the name (the uploaded image, or the first album cover when none), with "Change picture" and "Hide artist" as plain quiet actions under it.
ANCHORS: Apple Music artist page (round portrait beside the name), macOS Music "Artwork" tab (choose a file, applied at once), Plexamp lyrics (edit entry point inside the lyrics view)
VARIETY: cohere, no rotation (same ledger note).
CONTENT: album page "Change cover"; artist page "Change picture", "Hide artist", after hide the status line "{artist} is hidden. Unhide it in Settings."; lyrics panel "Edit lyrics" (.link-btn under the text, and in the no-lyrics note "Add lyrics"); stale note "These timings were made for the old words." with buttons "Keep old timings" and "Remove timings"; upload error "Couldn't use that image: {detail}". Library sort: see track-actions CONTENT. No placeholders.
FORMAT: production
TARGET: existing stack: web/src/views/AlbumTracks.tsx, ArtistAlbums.tsx, Library.tsx, SyncedLyrics.tsx, web/src/jobs/JobsContext.tsx (`bumpArt`), web/src/styles/library.css, web/src/styles/np.css.
CONSTRAINTS: WCAG AA. File input is visually hidden behind a real <button> (keyboard reachable), accept="image/jpeg,image/png,image/webp". Status and errors inline with role="status"/role="alert", no toast. 44 px targets. Portrait 96 px at <600 px, 128 px above, border-radius 50%. Animate only opacity/transform, reduced-motion instant. No em dash.

## Design: settings-m2

READ: Reading this as: more sections on the existing quiet settings screen for one listener, plain utilitarian language, leaning toward the M1 settings mount-table rows.
DIALS: VARIANCE 3 / MOTION 2 / DENSITY 4
PALETTE: existing tokens only, as Design: settings in the M1 plan: --ink, --text, --text-2, --hairline, --accent (range fill, primary), --on-accent. No new colour.
TYPE: display Newsreader Variable (page title only) / body Figtree Variable
SIGNATURE: every list (duplicates, hidden artists, removed songs) uses the M1 folder-row shape: one hairline-separated row, the thing on the left in --text with its fact under it in --text-2, one quiet text action on the right.
ANCHORS: Plexamp settings (plain labelled sections, no cards), Apple Music for Mac "Playback" pane (one Crossfade slider with seconds), macOS Photos "Duplicates" album (group, pick the copy to keep, merge the rest)
VARIETY: cohere, no rotation (same ledger note).
CONTENT: sections in order "Music folders" (existing), "Playback", "Duplicates", "Hidden artists", "Removed songs". Playback: label "Crossfade", value "Off" or "{n} s", help "Songs blend into each other. Off plays them back to back with no gap." Duplicates: button "Find duplicates", empty "No duplicates found.", group head "{title} · {artist}", per copy "{album} · {FORMAT} · {m:ss}" with a "Keep" radio, button "Remove the other copies". Hidden artists: row action "Unhide", empty "No hidden artists." Removed songs: row "{title}" with "{artist} · {album}", action "Restore", empty "Nothing removed." No placeholders.
FORMAT: production
TARGET: existing stack: web/src/views/Settings.tsx, web/src/styles/settings.css.
CONSTRAINTS: WCAG AA. Range uses the existing `.range` class with `--p` fill, label above, value in an <output>. Keep radios are real <input type="radio"> per group with a visible label. 44 px targets. 320 px floor, no horizontal scroll, long titles wrap. No modal except the shared RemoveDialog. No em dash.

## Phase 1 - Foundations

### Task 1: Remove stale muse.backup and systemd comments [mechanical]
- [x] status
**Objective:** `taste_retrain.py` and `content_embed_batch.py` no longer mention `muse.backup` or a systemd timer.
**Write-scope:**
`muse/taste_retrain.py`
`muse/content_embed_batch.py`
**Read-context:** `muse/taste_retrain.py:1-12`, `muse/taste_retrain.py:74-84`, `muse/content_embed_batch.py:1-12`, `muse/content_embed_batch.py:234-242`
**Anchors:**
`def run(conn, trainer: TasteTrainer | None = None) -> dict:` @ `muse/taste_retrain.py`
**Steps:**
1. `muse/taste_retrain.py:3`: replace the sentence fragment "Standalone, like `muse.backup` -- run from a systemd timer" (and the rest of that sentence if it continues onto the next line) with "Runs as a `taste_retrain` job at launch when the last retrain is over a day old, or standalone with `uv run python -m muse.taste_retrain`." Keep the rest of the docstring.
2. `muse/taste_retrain.py:79`: in the comment ending "the same convention `muse.backup` uses for a failed push.", replace that clause with "the same convention every job uses for a failure."
3. `muse/content_embed_batch.py:3`: replace "Standalone, like `muse.taste_retrain` and `muse.backup` -- run from a systemd" (and the rest of that sentence) with "Standalone, like `muse.taste_retrain`: run it with `uv run python -m muse.content_embed_batch`."
4. `muse/content_embed_batch.py:238`: replace "the same convention `muse.backup` and `muse.taste_retrain`" with "the same convention `muse.taste_retrain`", keeping the rest of the comment.
**New symbols:** none
**Verify:** `grep -rn "muse.backup\|systemd" muse/taste_retrain.py muse/content_embed_batch.py; uv run pytest -p no:cacheprovider tests/test_jobs_taste_retrain.py tests/test_content_batch.py`. Expected: grep prints nothing; pytest all passed.
**Forbidden:** comment and docstring text only; no code change; don't touch other files.
**Depends:** none
**Why:** Parked M1 task 3r2. `muse/backup.py` was stripped in M1 and the desktop app has no systemd.

### Task 2: Migration 22, year tag, and scanner year
- [x] status
**Objective:** Schema 22 adds `track_overrides`, `hidden_artists`, `artist_images` and `tracks.year`; tags and the scanner read and store the year.
**Write-scope:**
`muse/library/schema.py`
`muse/tags.py`
`muse/scanner.py`
`tests/test_tags.py`
`tests/test_m022_schema.py` (new)
`tests/test_scanner.py`
**Read-context:** `muse/library/schema.py:674-710`, `muse/tags.py:1-56`, `muse/scanner.py:40-75`, `muse/scanner.py:160-178`, `tests/test_tags.py:1-35`, `tests/test_scanner.py:1-66`, `tests/test_migrations.py:16-36`
**Anchors:**
`def _m021_local_files(conn: sqlite3.Connection) -> None:` @ `muse/library/schema.py`
`class FileTags:` @ `muse/tags.py`
`def read_tags(path: Path) -> FileTags | None:` @ `muse/tags.py`
`def _fields(path: Path, t: "_tags.FileTags") -> dict:` @ `muse/scanner.py`
**Steps:**
1. `schema.py`: add `_m022_edits` after `_m021_local_files` and append it to `MIGRATIONS`. Docstring: "Desktop Decisions 7 and 10: tag overrides, hidden artists, artist images, and the year the sort needs. Local rows lose file_mtime so the next scan re-reads every file once for its year." Body, one `conn.execute` each:
   - `CREATE TABLE track_overrides (track_rowid INTEGER NOT NULL REFERENCES tracks(track_rowid), field TEXT NOT NULL, value TEXT NOT NULL, updated_at INTEGER NOT NULL, PRIMARY KEY (track_rowid, field))`
   - `CREATE TABLE hidden_artists (artist_name TEXT PRIMARY KEY, hidden_at INTEGER NOT NULL)`
   - `CREATE TABLE artist_images (artist_name TEXT PRIMARY KEY, art_path TEXT NOT NULL, source TEXT NOT NULL, updated_at INTEGER NOT NULL)`
   - `ALTER TABLE tracks ADD COLUMN year INTEGER`
   - `UPDATE tracks SET file_mtime = NULL WHERE origin = 'local'`
2. `tags.py`: add `year: int | None = None` as the last `FileTags` field. Add an eighth key to both tuples: `"TDRC"` (WAVE) and `"date"` (easy). Add `def _year(value: str | None) -> int | None:` returning `int(value[:4])` when `value` is not None, `value[:4].isdigit()` and `1000 <= int(value[:4]) <= 9999`, else None. Pass `year=_year(date)`.
3. `scanner.py`: `_fields` adds `"year": t.year`. `_insert` adds `year` to the INSERT column list and values. The UPDATE branch adds `year=?`.
4. `tests/test_tags.py`: in `_tagged_wav` add `f.tags.add(TDRC(encoding=3, text="2019-05-03"))` (import `TDRC` from `mutagen.id3`); `test_read_tags` also asserts `t.year == 2019`.
5. `tests/test_scanner.py`: `ft()` gains `year=None` passed through; add `test_year_stored` (tags with `year=2001`, scan, row `year == 2001`).
6. `tests/test_m022_schema.py`: open a raw `sqlite3.connect(tmp_path / "db.sqlite3", isolation_level=None)` with `row_factory = sqlite3.Row` (pattern `tests/test_migrations.py:16-22`). Build version 21 by hand: `CREATE TABLE schema_version (version INTEGER NOT NULL, applied_at INTEGER NOT NULL)`, then for `i, fn in enumerate(MIGRATIONS[:21])` call `fn(raw)` and insert `(i + 1, 0)`. Insert one `origin='local'` track with `file_mtime = 5` (every NOT NULL column of `tracks` filled). Then `assert migrate(raw) == 22`; the three tables and `tracks.year` appear in `PRAGMA table_info`; that row's `file_mtime` is NULL.
**New symbols:** `_m022_edits(conn) -> None`; `FileTags.year: int | None = None`; `_year(value: str | None) -> int | None`
**Verify:** `uv run pytest -p no:cacheprovider` then `bash scripts/smoke.sh`. Expected: 0 failed (1095 + new tests); smoke prints `SMOKE-OK` (a fresh data dir migrates to 22 and serves).
**Forbidden:** never edit a shipped migration; no `executescript`; no new deps; don't add `year` to `TRACK_FIELDS` here (Task 9 does).
**Depends:** none
**Why:** One migration for M2's tables keeps the ladder short. The year backfill rides the scanner's existing re-read path instead of a separate job.

### Task 3: Web test runner and playable-only queues
- [x] status
**Objective:** `npm test` runs vitest, and no queue builder can queue a track whose format the webview cannot play.
**Write-scope:**
`web/package.json`
`web/package-lock.json`
`web/src/player/formats.ts`
`web/src/player/formats.test.ts` (new)
`web/src/views/Library.tsx`
`web/src/views/AlbumTracks.tsx`
`web/src/views/PlaylistDetail.tsx`
`web/src/views/Home.tsx`
`web/src/views/ArtistAlbums.tsx`
`web/src/views/Playlists.tsx`
**Read-context:** `web/src/player/formats.ts:1-28`, `web/src/views/Home.tsx:50-85`, `web/src/views/Playlists.tsx:55-70`, `web/src/views/Library.tsx:145-160`
**Anchors:**
`export function unplayableReason(track: { playable: boolean; format: string | null }): string | null {` @ `web/src/player/formats.ts`
**Steps:**
1. `cd web && npm install -D vitest@3.2.4`. Add script `"test": "vitest run"` to `package.json`.
2. `formats.ts`: add `export function playableOnly<T extends { playable: boolean; format: string | null }>(tracks: T[]): T[] { return tracks.filter((t) => unplayableReason(t) === null); }`.
3. Replace each raw filter with `playableOnly(...)`: `Library.tsx:148` and `:153`, `AlbumTracks.tsx:30`, `PlaylistDetail.tsx:31`, `Home.tsx:58`, `ArtistAlbums.tsx:40`, `Playlists.tsx:64`. `Home.tsx:81` becomes `chart.flatMap((s) => (s.track && unplayableReason(s.track) === null ? [s.track] : []))`. Import from `../player/formats`.
4. `formats.test.ts` (vitest, node env): before a dynamic `await import("./formats")`, `vi.stubGlobal("document", { createElement: () => ({ canPlayType: (m: string) => (m === "audio/flac" ? "" : "maybe") }) })`. Cases:
   - `unplayableReason({playable:false, format:"mp3"})` is `"File missing"`.
   - `unplayableReason({playable:true, format:"flac"})` starts with `"Format not supported on this "`.
   - `unplayableReason({playable:true, format:"mp3"})` and `format:null` are null.
   - `playableOnly` over `[mp3 ok, mp3 missing, flac ok, null ok]` keeps exactly the first and the last, in order.
**New symbols:** `playableOnly<T>(tracks: T[]): T[]` in `web/src/player/formats.ts`; npm script `test`
**Verify:** `cd web && npm test && npm run build && { grep -rn "t\(rack\)\?) => t\(rack\)\?\.playable\|track?\.playable" src; true; }`. Expected: vitest 4 passed (1 file); build succeeds; grep prints nothing.
**Forbidden:** no other dev deps (no jsdom, no testing-library); don't change `TrackRow`; don't change `PlayerContext`.
**Depends:** none
**Why:** M1 leftover: Play all and Shuffle queued tracks the row already labels unplayable. The runner is the M2 web test seam.

### Task 4: Red tests for queue editing and crossfade math [test]
- [x] status
**Objective:** `queue.test.ts` and `crossfade.test.ts` pin the pure queue and crossfade functions and fail because the modules do not exist yet.
**Write-scope:**
`web/src/player/queue.test.ts` (new)
`web/src/player/crossfade.test.ts` (new)
**Read-context:** `web/src/player/PlayerContext.tsx:321-339`, `web/src/player/sync.ts:1-20`, `web/src/player/formats.test.ts` (from Task 3)
**Anchors:**
`export type RepeatMode` @ `web/src/player/sync.ts`
**Steps:**
1. Signatures under test (copy exactly; Task 5 implements them):
   - `queue.ts`: `insertNext<T>(queue: T[], index: number, tracks: T[]): T[]`, `append<T>(queue: T[], tracks: T[]): T[]`, `removeAt<T>(queue: T[], index: number, at: number): T[]`, `move<T>(queue: T[], index: number, from: number, to: number): T[]`, `nextIndex(length: number, at: number, mode: { shuffle: boolean; repeat: RepeatMode }, random?: () => number): number`, `upcomingIndex(length: number, at: number, mode: { shuffle: boolean; repeat: RepeatMode }): number | null`.
   - `crossfade.ts`: `CROSSFADE_KEY = "muse.crossfade"`, `MAX_CROSSFADE_S = 12`, `GAPLESS_LEAD_MS = 50`, `getCrossfadeSeconds(): number`, `setCrossfadeSeconds(seconds: number): void`, `fadeMs(durationMs: number, crossfadeS: number): number`, `handoffAtMs(durationMs: number, crossfadeS: number): number | null`, `fadeGains(elapsedMs: number, fadeMs: number): { out: number; in: number }`, `handoffClose(positionMs: number): { reason: "track_end"; positionMs: number }`.
2. `queue.test.ts`, with `a..d`/`x,y` as strings:
   - `insertNext(["a","b","c"],0,["x","y"])` equals `["a","x","y","b","c"]`; `insertNext(["a","b"],1,["x"])` equals `["a","b","x"]`.
   - `append(["a"],["x","y"])` equals `["a","x","y"]`.
   - `removeAt(["a","b","c"],0,2)` equals `["a","b"]`; with `at` 0 (== index) and with `at` 5 the result `toBe` the input array.
   - `move(["a","b","c","d"],0,1,3)` equals `["a","c","d","b"]`; `move(...,0,3,1)` equals `["a","d","b","c"]`; `from` 0, `to` 0, and `from === to` each return the input array (`toBe`).
   - `nextIndex(0,0,off)` is 0; `(3,0,off)` 1; `(3,2,off)` 2; `(3,2,{shuffle:false,repeat:"all"})` 0; shuffle `(3,1,…, random)` with random returning 0.4 then 0.9 gives 2; shuffle with length 1 gives 0. (`off` = `{shuffle:false, repeat:"off"}`.)
   - `upcomingIndex(3,0,off)` 1; `(3,2,off)` null; `(3,2,repeat all)` 0; `(1,0,repeat all)` null; any shuffle null; repeat `"one"` null.
3. `crossfade.test.ts` (stub `localStorage` with `vi.stubGlobal` holding a `Map`; `vi.unstubAllGlobals()` after each):
   - `getCrossfadeSeconds()`: no value 0; `"6"` 6; `"20"` 12; `"-3"` 0; `"abc"` 0; a `getItem` that throws gives 0.
   - `setCrossfadeSeconds(7.6)` stores `"8"`; `15` stores `"12"`; `-1` stores `"0"`; key is `"muse.crossfade"`.
   - `fadeMs(200000,6)` 6000; `fadeMs(8000,6)` 4000; `fadeMs(200000,0)` 0.
   - `handoffAtMs(200000,0)` 199950; `(200000,6)` 194000; `(8000,6)` 4000; `(30,0)` 0; `(0,6)` null; `(NaN,6)` null.
   - `fadeGains(0,6000)` is `{out:1,in:0}`; `(6000,6000)` out `toBeCloseTo(0)`, in 1; `(3000,6000)` both `toBeCloseTo(Math.SQRT1_2)`; `(9000,6000)` same as 6000; `(100,0)` is `{out:0,in:1}`.
   - `handoffClose(194000.4)` equals `{ reason: "track_end", positionMs: 194000 }`.
**New symbols:** none (tests only)
**Verify:** `cd web && npx vitest run src/player/queue.test.ts src/player/crossfade.test.ts`. Expected: 2 failed test files, each with `Failed to resolve import` naming `./queue` or `./crossfade`; `formats.test.ts` untouched.
**Forbidden:** don't create `queue.ts` or `crossfade.ts`; no source edits; no new deps.
**Depends:** Task 3
**Why:** Spec testing strategy lists queue edits and the crossfade span close for `PlayerContext`; pinning them as pure functions keeps them testable without a DOM.

### Task 5: Queue and crossfade modules, PlayerContext queue editing
- [x] status
**Objective:** `queue.ts` and `crossfade.ts` pass Task 4's tests, and `PlayerContext` exposes `playNext`, `moveInQueue` and `removeFromQueue`.
**Write-scope:**
`web/src/player/queue.ts` (new)
`web/src/player/crossfade.ts` (new)
`web/src/player/PlayerContext.tsx`
**Read-context:** `web/src/player/queue.test.ts` (from Task 4), `web/src/player/crossfade.test.ts` (from Task 4), `web/src/player/PlayerContext.tsx:46-92`, `web/src/player/PlayerContext.tsx:279-340`, `web/src/player/PlayerContext.tsx:812-827`, `web/src/player/gain.ts:25-45`
**Anchors:**
`const enqueue = useCallback(` @ `web/src/player/PlayerContext.tsx`
`const next = useCallback(() => {` @ `web/src/player/PlayerContext.tsx`
**Steps:**
1. `queue.ts`: implement Task 4's signatures. Edits act only on positions `> index` and `< queue.length`; anything else returns the input array unchanged. `insertNext` splices after `index`. `nextIndex` is today's `next()` body (`PlayerContext.tsx:328-338`) as a pure function with injected `random` (default `Math.random`). `upcomingIndex`: null under shuffle or repeat `"one"`; `at + 1` when `< length`; `0` under repeat `"all"` unless that equals `at`; else null.
2. `crossfade.ts`: implement Task 4's signatures. `getCrossfadeSeconds` reads `localStorage.getItem(CROSSFADE_KEY)` in try/catch, `Number()`, non-finite gives 0, then clamp to 0..12 and round. `setCrossfadeSeconds` stores the clamped rounded value as a string, swallowing storage errors. `fadeMs = max(0, min(crossfadeS*1000, durationMs/2))`. `handoffAtMs` returns null unless `Number.isFinite(durationMs) && durationMs > 0`, else `max(0, durationMs - max(fadeMs(...), GAPLESS_LEAD_MS))`. `fadeGains`: `fadeMs <= 0` gives `{out:0,in:1}`; else `t = clamp(elapsed/fadeMs, 0, 1)`, `out = cos(t*PI/2)`, `in = sin(t*PI/2)`. `handoffClose` rounds.
3. `PlayerContext.tsx`:
   - `next` uses `nextIndex(queue.length, at, { shuffle, repeat })` inside the existing `setIndex` updater.
   - Add to `PlayerApi` and the `useMemo` value/deps: `playNext: (tracks: Track[]) => void; moveInQueue: (from: number, to: number) => void; removeFromQueue: (at: number) => void;`.
   - `playNext` and `enqueue`: when `queueRef.current.length === 0`, call `playQueue(tracks, 0, { kind: "track", label: "Queue" })`; else `setQueue((q) => insertNext(q, indexRef.current, tracks))` (or `append(q, tracks)`) and `remember(...)`.
   - `removeFromQueue(at)`: `setQueue((q) => removeAt(q, indexRef.current, at))`. `moveInQueue(from, to)`: `setQueue((q) => move(q, indexRef.current, from, to))`. Neither touches `seen` or `radioAdded`.
**New symbols:** Task 4's signatures in `web/src/player/queue.ts` and `web/src/player/crossfade.ts`; `PlayerApi.playNext`, `PlayerApi.moveInQueue`, `PlayerApi.removeFromQueue`
**Verify:** `cd web && npm test && npm run build`. Expected: vitest 3 files passed, 0 failed; build succeeds.
**Forbidden:** don't touch the `<audio>` element or `gain.ts` (Task 14); don't change radio fill/trim logic; don't edit the Task 4 tests; no new deps.
**Depends:** Task 4
**Why:** Decision 15: Play next inserts after the current track, Add to queue appends, drag and ✕ edit the rest. Keeping edits after the playhead means `index` never needs fixing up.

## Phase 2 - Library layer

### Task 6: Red tests for tag overrides [test]
- [x] status
**Objective:** `tests/test_overrides.py` pins the override layer and fails only because `muse.library.overrides` does not exist.
**Write-scope:**
`tests/test_overrides.py` (new)
**Read-context:** `tests/test_scanner.py:1-66` (Env/ft helpers, copy them), `tests/conftest.py:88-131`, `muse/scanner.py:150-205`
**Anchors:**
`def scan(` @ `muse/scanner.py`
`def insert_track(conn, rowid, *, track_id="auto", status="done", album_rowid=20,` @ `tests/conftest.py`
**Steps:**
1. API under test (Task 7): `from muse.library import overrides`; `overrides.FIELDS == ("track_name", "artist_name", "album_name", "track_no", "disc_no", "year")`; `overrides.get(conn, track_rowid) -> dict`; `overrides.write(conn, track_rowids: list[int], values: dict, *, now: int | None = None) -> int`; `overrides.clear(conn, track_rowids: list[int]) -> int`; `overrides.apply(conn, track_rowid: int, fields: dict) -> dict`.
2. Cases (scanner cases copy the `Env`/`ft` helpers from `tests/test_scanner.py`, with `ft` taking `year=None`):
   - `write` on one `insert_track` row with `{"track_name": "New"}` returns 1; `tracks.track_name == "New"`; `get` gives `{"track_name": "New"}`; `queries.search(conn, "New")` finds it (FTS reindexed).
   - `write` with `{"artist_name": "B"}` sets `artist_names` to `'["B"]'` and leaves `album_rowid` unchanged.
   - `write` with `{"album_name": "Other"}` on two scanned tracks of album X, where a third scanned track already lives in album "Other" by the same album artist: both edited rows take the third row's `album_rowid` and its `art_path`.
   - `write` with `{"track_no": 7, "year": 1999}` stores ints; `get` returns ints.
   - `write` with an unknown key raises `ValueError`; with an empty dict raises `ValueError`.
   - Override survives a rescan of a changed file: scan, `write {"track_name": "Mine"}`, rewrite the file bytes (size change), rescan: `track_name == "Mine"`, `track_no` follows the file.
   - Move keeps overrides: scan `a.mp3`, `write {"track_name": "Mine"}`, rename the file, rescan: `ScanResult.moved == 1`, same `track_rowid`, `track_name == "Mine"`.
   - `clear` deletes the rows, returns the number of tracks touched, sets `file_mtime` NULL; the next scan restores the file's title.
   - `apply` with no overrides returns an equal dict; with an `album_name` override rewrites `album_key` keeping the part before `\x1f`.
**New symbols:** none (tests only)
**Verify:** `uv run pytest -p no:cacheprovider tests/test_overrides.py`. Expected: collection error `ModuleNotFoundError: No module named 'muse.library.overrides'` (or ImportError naming `overrides`), nothing else.
**Forbidden:** no source edits; don't edit `tests/test_scanner.py`.
**Depends:** Task 2
**Why:** Decision 7: an override wins on every read and every rescan. The scanner interaction (re-read, move match) is where this breaks silently.

### Task 7: Tag override layer and scanner integration
- [x] status
**Objective:** `muse/library/overrides.py` exists, the scanner re-applies overrides on re-read and move, and Task 6 passes.
**Write-scope:**
`muse/library/overrides.py` (new)
`muse/scanner.py`
**Read-context:** `tests/test_overrides.py` (from Task 6), `muse/scanner.py:40-56`, `muse/scanner.py:150-205`, `muse/library/tracks.py:74-87`, `muse/library/tracks.py:295-325`
**Anchors:**
`def album_rowid_for_key(conn: sqlite3.Connection, album_key: str) -> int:` @ `muse/library/tracks.py`
`def index_track(` @ `muse/library/tracks.py`
`def _fields(path: Path, t: "_tags.FileTags") -> dict:` @ `muse/scanner.py`
**Steps:**
1. `overrides.py` (module docstring: the Decision 7 override layer, write-through so every reader sees the edit; no BEGIN because the shared connection may be inside the chain's transaction):
   - `FIELDS` and `INT_FIELDS = {"track_no", "disc_no", "year"}`.
   - `get`: `SELECT field, value FROM track_overrides WHERE track_rowid = ?`, ints converted.
   - `apply(conn, rowid, fields)`: copy, overlay `get`; if `album_name` was overridden, `album_key = fields["album_key"].split("\x1f", 1)[0] + "\x1f" + album.casefold()` (when `fields["album_key"]` is None use `fields["artist_name"].casefold()` as the prefix).
   - `_store(conn, rowid, f)`: `album_rowid = album_rowid_for_key(conn, f["album_key"])`; one UPDATE of `track_name, artist_name, artist_names (json.dumps([artist])), album_name, album_key, album_rowid, track_no, disc_no, year`, plus `art_path = (SELECT art_path FROM album_art WHERE album_rowid = ?)`; then `index_track`.
   - `write`: reject empty or unknown keys with `ValueError`; for each rowid that exists, upsert each `(rowid, field, str(value), now)` with `INSERT ... ON CONFLICT(track_rowid, field) DO UPDATE`, then read the row's current columns, `apply`, `_store`. Return the count of rows updated.
   - `clear`: `DELETE FROM track_overrides WHERE track_rowid IN (...)`, `UPDATE tracks SET file_mtime = NULL WHERE track_rowid IN (...)`; return the UPDATE rowcount.
2. `scanner.py`: import `from .library import overrides`. In the UPDATE branch, `f = overrides.apply(conn, row["track_rowid"], _fields(path, t))` before the UPDATE, and the UPDATE also sets `art_path = (SELECT art_path FROM album_art WHERE album_rowid = ?)` for the new `album_rowid`. In move matching, compare each vanished `r` against `overrides.apply(conn, r["track_rowid"], f)`'s `(duration_ms, artist_name, album_name, track_name)` plus `st.st_size`.
**New symbols:** `muse/library/overrides.py`: `FIELDS`, `INT_FIELDS`, `get`, `apply`, `write`, `clear`, `_store`
**Verify:** `uv run pytest -p no:cacheprovider tests/test_overrides.py tests/test_scanner.py` then `uv run pytest -p no:cacheprovider`. Expected: all green, 0 failed.
**Forbidden:** never write the audio file (Decision 7); no `BEGIN`; don't edit Task 6's tests; don't change `_insert`.
**Depends:** Task 6
**Why:** Write-through keeps every existing query unchanged; re-applying in the scanner is what makes the override win on rescan.

### Task 8: Remove, restore and trash, plus the playlists tombstone rule
- [x] status
**Objective:** `muse/library/removal.py` tombstones, trashes and restores tracks, playlists hide removed tracks, and the recommender's playable set excludes them.
**Write-scope:**
`muse/library/removal.py` (new)
`muse/library/playlists.py`
`pyproject.toml`
`uv.lock`
`tests/test_removal.py` (new)
**Read-context:** `muse/library/playlists.py:1-110`, `muse/library/queries.py:20-48`, `muse/library/queries.py:440-450`, `muse/scanner.py:150-160`, `tests/test_playlists.py:1-100`
**Anchors:**
`def get(conn: sqlite3.Connection, playlist_id: int) -> dict | None:` @ `muse/library/playlists.py`
`def summaries(conn: sqlite3.Connection) -> list[dict]:` @ `muse/library/playlists.py`
`def _covers(conn: sqlite3.Connection, playlist_id: int) -> list[int]:` @ `muse/library/playlists.py`
`def playable_rowids(conn: sqlite3.Connection) -> set[int]:` @ `muse/library/queries.py`
**Steps:**
1. `uv add send2trash` (updates `pyproject.toml` and `uv.lock`).
2. `removal.py` (docstring: Decision 10; a tombstone is `status='removed'` plus `removed_at`, so every status-filtered reader drops it; plays and ratings stay):
   - `remove(conn, track_rowids, *, trash: bool, now: int | None = None, send=send2trash) -> dict`. Per rowid: row `SELECT file_path, status FROM tracks WHERE track_rowid = ?`; missing or already `removed` gives a failure `"No track {rowid}"`. If `trash` and `file_path` and `Path(file_path).exists()`: `send(file_path)`; on `OSError as e` record failure `str(e)` and do not tombstone. Then `UPDATE tracks SET status = 'removed', removed_at = ? WHERE track_rowid = ?`. Return `{"removed": [ids], "failed": [{"track_rowid": id, "error": text}]}`.
   - `restore(conn, track_rowids) -> int`: `UPDATE tracks SET status = 'missing_file', removed_at = NULL, file_mtime = NULL WHERE track_rowid IN (...) AND status = 'removed'`; return rowcount.
   - `removed(conn) -> list[dict]`: `SELECT {TRACK_FIELDS}, removed_at FROM tracks WHERE status = 'removed' ORDER BY removed_at DESC, track_rowid` through `queries._track`.
3. `playlists.py`: `_covers` and `get` add `AND t.status != 'removed'` / `WHERE tracks.status != 'removed'`; `summaries` joins tracks with `LEFT JOIN tracks t ON t.track_rowid = pt.track_rowid AND t.status != 'removed'` and counts `COUNT(t.track_rowid) AS track_count`. Add `append(conn, playlist_id, track_rowids, *, now=None) -> bool`: False if the playlist is missing; else insert at positions after `MAX(position)` (or 0) with one `executemany`, bump `updated_at`, return True. Unknown track raises `sqlite3.IntegrityError`. Update the module docstring's deletion sentence to cover tombstones.
4. `tests/test_removal.py`: tombstone hides from `queries.songs`, `queries.search`, `queries.playable_rowids`, `playlists.get` and the summary count, and keeps `plays` rows; restore brings it back into the playlist with status `missing_file`; trash calls the fake `send` with the path and tombstones; a `send` raising `OSError("busy")` leaves status `done` and reports `"busy"`; trash on a missing file just tombstones; a scan after restore with the file present flips it to `done` (Env pattern from `tests/test_scanner.py:15-45`); `append` keeps order after existing positions and returns False for an unknown playlist.
**New symbols:** `muse/library/removal.py`: `remove`, `restore`, `removed`; `playlists.append(conn, playlist_id, track_rowids, *, now=None) -> bool`
**Verify:** `uv run pytest -p no:cacheprovider tests/test_removal.py tests/test_playlists.py` then `uv run pytest -p no:cacheprovider`. Expected: all green, 0 failed.
**Forbidden:** never delete a file except through `send`; never delete a `plays` or `tracks` row; no `BEGIN`; don't edit `queries.py` (Task 9).
**Depends:** none
**Why:** Decision 10 and the M1 leftover: something must set `removed_at`. Read-time filtering makes restore lossless.

### Task 9: Library reads: sort, year, duplicates, hidden artists, artist images
- [x] status
**Objective:** `queries.songs` sorts server-side, tracks carry `year`, duplicates group, hidden artists leave the artist list, and artist images and hidden flags have a store.
**Write-scope:**
`muse/library/queries.py`
`muse/library/artists.py` (new)
`tests/test_library_m2.py` (new)
**Read-context:** `muse/library/queries.py:20-90`, `muse/library/queries.py:320-335`, `muse/titles.py:52-60`, `tests/test_library_songs.py:1-15`
**Anchors:**
`def songs(conn: sqlite3.Connection) -> list[dict]:` @ `muse/library/queries.py`
`def artists(conn: sqlite3.Connection) -> list[dict]:` @ `muse/library/queries.py`
`def tracks_by_rowid(` @ `muse/library/queries.py`
`def normalize_title(title: str) -> str:` @ `muse/titles.py`
**Steps:**
1. `TRACK_FIELDS`: add `year,` after `format,`.
2. `songs(conn, sort: str = "title", order: str = "asc")`. Map `SONG_SORTS = {"title": "track_name COLLATE NOCASE", "added": "added_at", "plays": "(SELECT COUNT(*) FROM plays p WHERE p.track_rowid = tracks.track_rowid)", "year": "year", "duration": "duration_ms"}`; unknown sort or order raises `ValueError`. ORDER BY `{col} IS NULL, {col} {ASC|DESC}, track_name COLLATE NOCASE, track_rowid` (title skips the NULL term).
3. `artists()`: add `AND artist_name NOT IN (SELECT artist_name FROM hidden_artists)` and the column `EXISTS (SELECT 1 FROM artist_images i WHERE i.artist_name = tracks.artist_name) AS has_image`, returned as a bool.
4. `tracks_by_rowid`: add `AND status != 'removed'`.
5. `duplicates(conn) -> list[list[dict]]`: `TRACK_FIELDS` rows `WHERE status IN ('done','missing_file') AND duration_ms IS NOT NULL`; group by `(normalize_title(track_name), artist_name.casefold())`; inside a group sort by `duration_ms` then `track_rowid`, start a new cluster when `duration_ms - cluster[0]["duration_ms"] > 2000`; keep clusters of 2+; return them sorted by `(key, first track_rowid)`.
6. `artists.py`: `hide(conn, artist_name, hidden: bool, *, now=None) -> None` (INSERT OR REPLACE or DELETE on `hidden_artists`); `hidden(conn) -> list[dict]` (`artist_name, hidden_at` by `artist_name COLLATE NOCASE`); `set_image(conn, artist_name, art_path, *, now=None) -> None` (upsert `source='manual'`); `image_path(conn, artist_name) -> str | None`.
7. `tests/test_library_m2.py`: each sort key asc and desc with NULLs last (plays sort: raw `INSERT INTO plays (track_rowid, started_at, reached_ms, duration_ms, heartbeat_at) VALUES (?, 0, 0, 1000, 0)` rows); bad sort raises; `year` present in a track dict; duplicates: "Song" and "song" by "A" / "a" at 180000 and 181500 group (`normalize_title` lowercases; it does not strip "(Remastered)"), a third "Song" at 184000 is not in that cluster, another artist never groups, NULL duration excluded; hidden artist drops from `artists()` but stays in `songs()` and `albums()`; `has_image` true after `set_image`; `tracks_by_rowid` drops a removed row.
**New symbols:** `queries.SONG_SORTS`; `queries.songs(conn, sort="title", order="asc")`; `queries.duplicates(conn)`; `muse/library/artists.py`: `hide`, `hidden`, `set_image`, `image_path`
**Verify:** `uv run pytest -p no:cacheprovider tests/test_library_m2.py tests/test_library_songs.py tests/test_ratings.py` then `uv run pytest -p no:cacheprovider`. Expected: all green, 0 failed.
**Forbidden:** keep `songs(conn)` default output identical; don't filter hidden artists anywhere but `artists()`; no new deps.
**Depends:** Task 2
**Why:** Decision 15 sort and duplicate finder, Decision 10 hide artist and artist images. One file owns the reads so the API tasks only route.

### Task 10: Lyric edit and invalidation
- [x] status
**Objective:** `muse/library/lyrics_edit.py` saves edited lyrics into the data dir, invalidates the lyrics embedding, lyric tags and `lyrics_state`, and manages a stale `.lrc`.
**Write-scope:**
`muse/library/lyrics_edit.py` (new)
`tests/test_lyrics_edit.py` (new)
**Read-context:** `muse/library/lyrics_state.py:100-140`, `muse/lyrics.py:18-30`, `muse/library/schema.py:160-175`, `muse/library/schema.py:378-400`, `muse/library/lyric_tags.py:88-103`
**Anchors:**
`def upsert(` @ `muse/library/lyrics_state.py`
`def lyrics_files(lyrics_root: Path, track_rowid: int) -> tuple[Path, Path]:` @ `muse/lyrics.py`
**Steps:**
1. `lyrics_edit.py` (docstring: Decision 8's three invalidations and the stale `.lrc` rule):
   - `save_text(conn, track_rowid, text, *, lyrics_root, now=None) -> dict | None`: None when no `tracks` row. `txt, lrc = lyrics_files(lyrics_root, rowid)`. If `text.strip()` is empty: unlink both (`missing_ok=True`), `lyrics_path = NULL`. Else `lyrics_root.mkdir(parents=True, exist_ok=True)`, write `text` to `txt.with_suffix(".tmp")` (utf-8) then `os.replace` onto `txt`, set `lyrics_path = str(txt)`. Then `UPDATE embeddings SET lyrics_vec = NULL, lyrics_hash = NULL, lyrics_model = NULL WHERE track_rowid = ?`, `DELETE FROM lyric_tag_sets WHERE track_rowid = ?`, `lyrics_state.upsert(conn, rowid, state="manual", synced=False, checked_at=now)`. Return `{"lrc_stale": lrc.is_file()}`.
   - `is_stale(conn, track_rowid, *, lyrics_root) -> bool`: `.lrc` exists and the `lyrics_state` row has `state = 'manual' AND synced = 0`.
   - `keep_timings(conn, track_rowid, *, lyrics_root, now=None) -> bool`: False unless `is_stale`; else upsert `state="manual", synced=True`.
   - `drop_timings(conn, track_rowid, *, lyrics_root, now=None) -> bool`: False unless the `.lrc` exists; unlink it, upsert `state="manual", synced=False`.
2. `tests/test_lyrics_edit.py` (absolute `tmp_path` paths; insert the track first, FKs are immediate): save writes the file and sets `lyrics_path`; an `embeddings` row with a `lyrics_vec` blob, `lyrics_hash` and `audio_vec` keeps `audio_vec` and loses the three lyrics columns; a `lyric_tag_sets` row with a `lyric_tags` child is gone; `lyrics_state` is `manual`/0 and `lyrics_state.pending` does not list it; with an `.lrc` present `lrc_stale` is True and `is_stale` True; `keep_timings` makes `is_stale` False; `drop_timings` deletes the `.lrc`; blank text deletes both files and nulls `lyrics_path`; unknown rowid returns None.
**New symbols:** `muse/library/lyrics_edit.py`: `save_text`, `is_stale`, `keep_timings`, `drop_timings`
**Verify:** `uv run pytest -p no:cacheprovider tests/test_lyrics_edit.py` then `uv run pytest -p no:cacheprovider`. Expected: all green, 0 failed.
**Forbidden:** never write beside the audio file (Decision 8); don't touch `lyrics_state.py` or `api.py`; no `BEGIN`.
**Depends:** none
**Why:** Decision 8: a lyric edit invalidates the lyrics embedding, the tags and `lyrics_state`, and marks an existing `.lrc` stale.

## Phase 3 - API and client

### Task 11: Track edit routes, sort, duplicates, data-dir folder check
- [x] status
**Objective:** The API edits details, removes and restores tracks, lists removed tracks and duplicates, sorts songs, and refuses a music folder that contains the data dir.
**Write-scope:**
`muse/api.py`
`tests/test_api_tracks_edit.py` (new)
**Read-context:** `muse/api.py:199-236`, `muse/api.py:686-730`, `muse/api.py:815-850`, `tests/test_playlists.py:88-100`, `muse/library/overrides.py` (from Task 7), `muse/library/removal.py` (from Task 8), `muse/library/queries.py` (from Task 9)
**Anchors:**
`async def put_music_folders(body: MusicFoldersWrite) -> dict:` @ `muse/api.py`
`def library_songs() -> list[dict]:` @ `muse/api.py`
`class PlaylistWrite(BaseModel):` @ `muse/api.py`
`def write(conn, track_rowids: list[int], values: dict, *, now: int | None = None) -> int` @ `muse/library/overrides.py` (from Task 7)
`def remove(conn, track_rowids, *, trash: bool, now: int | None = None, send=send2trash) -> dict` @ `muse/library/removal.py` (from Task 8)
**Steps:**
1. Models: `TrackIds(BaseModel)`: `track_rowids: list[int] = Field(min_length=1, max_length=500)`. `DetailsValues(BaseModel)`: `track_name`, `artist_name`, `album_name`: `str | None = Field(None, min_length=1, max_length=300)` stripped (blank after strip raises); `track_no: int | None = Field(None, ge=1, le=999)`, `disc_no` `ge=1, le=99`, `year` `ge=1000, le=9999`. `DetailsWrite(TrackIds)`: `values: DetailsValues`. `RemoveWrite(TrackIds)`: `trash: bool = False`.
2. Routes (import `from .library import overrides, removal`):
   - `PATCH /api/tracks/details`: 404 `f"No track {id}"` for the first unknown rowid (`queries.track`); `vals = body.values.model_dump(exclude_none=True)`, empty gives 422 `"Nothing to change"`; `overrides.write`; return `{"tracks": queries.tracks_by_rowid(conn, ids)}`.
   - `POST /api/tracks/details/reset` (async): `overrides.clear`, `app.state.spawn_chain()`, return `{"reset": n}`.
   - `POST /api/tracks/remove`: return `removal.remove(conn, ids, trash=body.trash)`.
   - `POST /api/tracks/restore` (async): `removal.restore`, `spawn_chain()`, return `{"restored": n}`.
   - `GET /api/library/removed`: `removal.removed(conn)`.
   - `GET /api/library/songs`: params `sort: Literal["title","added","plays","year","duration"] = "title"`, `order: Literal["asc","desc"] = "asc"`, passed to `queries.songs`.
   - `GET /api/library/duplicates`: `{"groups": queries.duplicates(conn)}`.
3. `put_music_folders`: after the nesting check, `data = cfg.data_dir.resolve()`; for each folder, if `data.is_relative_to(f)`: 400 `f"{f} contains the Muse data folder {data}"`. (Only containment: a folder inside the data dir stays legal, which `tests/test_api_scan.py` relies on.)
4. `tests/test_api_tracks_edit.py` (TestClient fixture as `tests/test_playlists.py:88-98`): details 200 changes `track_name` in `/api/library/songs`; unknown rowid 404; empty values 422; `track_no: 0` 422; reset 200; remove then `/api/library/songs` lacks it and `/api/library/removed` has it; restore 200 and the row is back in `removed`-free songs as unplayable; sort `?sort=added&order=desc` orders by `added_at`; `?sort=bogus` 422; duplicates returns `{"groups": [...]}` with one pair; a music folder that is a parent of `config.data_dir` gives 400 with "contains the Muse data folder".
**New symbols:** models `TrackIds`, `DetailsValues`, `DetailsWrite`, `RemoveWrite`; routes `PATCH /api/tracks/details`, `POST /api/tracks/details/reset`, `POST /api/tracks/remove`, `POST /api/tracks/restore`, `GET /api/library/removed`, `GET /api/library/duplicates`; `GET /api/library/songs?sort=&order=`
**Verify:** `uv run pytest -p no:cacheprovider tests/test_api_tracks_edit.py tests/test_api_scan.py` then `uv run pytest -p no:cacheprovider` then `bash scripts/smoke.sh`. Expected: all green, 0 failed; `SMOKE-OK`.
**Forbidden:** never open `/api/jobs/stream` in a test; no new deps; don't change existing route paths or response shapes.
**Depends:** Task 7, Task 8, Task 9
**Why:** Decisions 7, 10 and 15 over HTTP. The data-dir check is the cheap M1 leftover that M5's `downloads/` scan needs.

### Task 12: Media routes: art and artist-image upload, hide artist, lyrics edit, playlist append
- [x] status
**Objective:** Covers and artist pictures upload, artists hide and unhide, lyrics save with the stale `.lrc` choice, playlists append, and art responses revalidate.
**Write-scope:**
`muse/api.py`
`tests/test_api_media.py` (new)
**Read-context:** `muse/api.py:1234-1300`, `muse/api.py:1326-1400`, `muse/library/art.py:79-117`, `muse/library/artists.py` (from Task 9), `muse/library/lyrics_edit.py` (from Task 10), `muse/library/playlists.py` (from Task 8), `tests/test_api_lyrics_synced.py:1-40`
**Anchors:**
`def _art_response(path: str | None, tag: str) -> FileResponse:` @ `muse/api.py`
`def track_lyrics_synced(track_rowid: int) -> dict:` @ `muse/api.py`
`def record_cover(` @ `muse/library/art.py`
`def save_text(conn, track_rowid, text, *, lyrics_root, now=None) -> dict | None` @ `muse/library/lyrics_edit.py` (from Task 10)
`def append(conn, playlist_id, track_rowids, *, now=None) -> bool` @ `muse/library/playlists.py` (from Task 8)
**Steps:**
1. `_art_response`: headers become `"cache-control": "no-cache"` and `"etag": f'"{tag}-{Path(path).stat().st_mtime_ns}"'`.
2. Upload helper: `IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}`, `MAX_IMAGE_BYTES = 10 * 1024 * 1024`; `async def _image_body(request) -> tuple[bytes, str]`: content type (before `;`) not in `IMAGE_TYPES` gives 415 `"Use a JPEG, PNG or WebP image"`; `await request.body()` empty gives 400 `"No image"`, over the limit 413 `"Image is over 10 MB"`.
3. Routes (async where they read the body; `import hashlib`; `from .library import artists as library_artists, lyrics_edit`):
   - `PUT /api/albums/{album_rowid}/art`: 404 if `queries.album` is None; write `art_root / f"{album_rowid}-manual{ext}"` (mkdir parents); `library_art.record_cover(conn, album_rowid, str(path), source="manual")`; return `{"ok": True}`.
   - `GET /api/artists/{artist_name}/image`: `_art_response(library_artists.image_path(conn, name), f"artist-{sha}")` where `sha = hashlib.sha1(name.encode()).hexdigest()[:16]`.
   - `PUT /api/artists/{artist_name}/image`: 404 if `queries.albums_by_artist` is empty; write `art_root / "artists" / f"{sha}{ext}"`; `set_image`; `{"ok": True}`.
   - `GET /api/library/hidden-artists`: `library_artists.hidden(conn)`. `PUT /api/artists/{artist_name}/hidden` body `ArtistHidden(hidden: bool)`: `hide`; return `{"artist_name": name, "hidden": hidden}`.
   - `PUT /api/tracks/{track_rowid}/lyrics` body `LyricsWrite(text: str = Field(max_length=100_000))`: `save_text(..., lyrics_root=config.lyrics_root)`; None gives 404.
   - `POST /api/tracks/{track_rowid}/lyrics/timings` body `LyricsTimings(action: Literal["keep", "drop"])`: call `keep_timings`/`drop_timings`; False gives 404 `"No stale timings for that track"` (keep) or `"No timings for that track"` (drop); else `{"ok": True}`.
   - `track_lyrics_synced`: `stale = lyrics_edit.is_stale(conn, track_rowid, lyrics_root=...)`; parse the `.lrc` only when not stale; response gains `"lrc_stale": stale`.
   - `POST /api/playlists/{playlist_id}/tracks` body `TrackIds` (from Task 11): `append`; False gives 404; `IntegrityError` gives 422 `"Unknown track in playlist"`; return `library_playlists.get(...)`.
4. `tests/test_api_media.py`: album upload stores `source='manual'` and `GET /api/albums/{id}/art` returns the bytes with `cache-control: no-cache`; then `library_art.albums_needing_art(conn)` does not list that album and its `album_art.source` is still `manual` (manual survives backfill); 415 on `text/plain`; 413 over 10 MB; artist image round-trip and `has_image` in `/api/library/artists`; hide removes the artist from `/api/library/artists` and lists it in `/api/library/hidden-artists`, unhide restores; lyrics PUT then `/lyrics/synced` serves plain with `lrc_stale` True when an `.lrc` existed, `timings keep` makes it synced again, `drop` removes the file; playlist append 200, unknown playlist 404, unknown track 422. Update any existing assertion in `tests/test_api_lyrics_synced.py` that pins the whole synced dict (none at plan time: `grep -n "== {" tests/test_api_lyrics_synced.py` shows only the offset dict).
**New symbols:** `IMAGE_TYPES`, `MAX_IMAGE_BYTES`, `_image_body`; models `ArtistHidden`, `LyricsWrite`, `LyricsTimings`; routes listed in Step 3; synced response key `lrc_stale`
**Verify:** `uv run pytest -p no:cacheprovider tests/test_api_media.py tests/test_api_lyrics_synced.py tests/test_art.py` then `uv run pytest -p no:cacheprovider` then `bash scripts/smoke.sh`. Expected: all green, 0 failed; `SMOKE-OK`.
**Forbidden:** no `python-multipart` or other new deps; never contact the network in a route (art and lyrics are local files); never open `/api/jobs/stream` in a test.
**Depends:** Task 11
**Why:** Decisions 8 and 10 over HTTP. Revalidating art is what makes an uploaded cover appear without a hard refresh.

### Task 13: Client functions and types for the M2 routes
- [x] status
**Objective:** `web/src/api.ts` has typed functions for every M2 route.
**Write-scope:**
`web/src/api.ts`
**Read-context:** `web/src/api.ts:28-70`, `web/src/api.ts:91-165`, `web/src/api.ts:205-300`
**Anchors:**
`async function refusal(path: string, response: Response): Promise<ApiError> {` @ `web/src/api.ts`
`export const getSongs = (signal?: AbortSignal) =>` @ `web/src/api.ts`
`export type SyncedLyrics = {` @ `web/src/api.ts`
**Steps:**
1. Types: `Track` gains `year: number | null;` after `format`, and `status` becomes `"done" | "missing_file" | "error" | "removed"`. `Artist` gains `has_image: boolean;`. `SyncedLyrics` gains `lrc_stale: boolean;`.
2. Add a local helper `async function send<T>(method: string, path: string, body?: unknown): Promise<T>`: `apiFetch(`/api${path}`, { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) })`; non-ok throws `await refusal(path, response)`; returns `response.json()`.
3. Add exactly:
   - `export type DetailsValues = Partial<{ track_name: string; artist_name: string; album_name: string; track_no: number; disc_no: number; year: number }>;`
   - `saveDetails(trackRowids: number[], values: DetailsValues): Promise<Track[]>` (PATCH `/tracks/details` body `{track_rowids, values}`, returns `.tracks`).
   - `resetDetails(trackRowids: number[]): Promise<void>` (POST `/tracks/details/reset`).
   - `export type RemoveResult = { removed: number[]; failed: { track_rowid: number; error: string }[] };` and `removeTracks(trackRowids: number[], trash: boolean): Promise<RemoveResult>` (POST `/tracks/remove`).
   - `restoreTracks(trackRowids: number[]): Promise<void>` (POST `/tracks/restore`).
   - `getRemovedTracks = (signal?: AbortSignal) => get<Track[]>("/library/removed", signal)`.
   - `export type SongSort = "title" | "added" | "plays" | "year" | "duration";` and change `getSongs` to `(signal?: AbortSignal, sort: SongSort = "title", order: "asc" | "desc" = "asc")` requesting `/library/songs?sort=${sort}&order=${order}`.
   - `getDuplicates = (signal?: AbortSignal) => get<{ groups: Track[][] }>("/library/duplicates", signal)`.
   - `getHiddenArtists = (signal?: AbortSignal) => get<{ artist_name: string; hidden_at: number }[]>("/library/hidden-artists", signal)`; `setArtistHidden(name: string, hidden: boolean): Promise<void>` (PUT `/artists/${encodeURIComponent(name)}/hidden`).
   - `uploadAlbumArt(albumRowid: number, file: File): Promise<void>` and `uploadArtistImage(name: string, file: File): Promise<void>`: `apiFetch` PUT with `headers: { "Content-Type": file.type }, body: file`; non-ok throws `refusal`.
   - `saveLyrics(trackRowid: number, text: string): Promise<{ lrc_stale: boolean }>` (PUT `/tracks/${id}/lyrics` body `{text}`); `setLyricsTimings(trackRowid: number, action: "keep" | "drop"): Promise<void>` (POST `/tracks/${id}/lyrics/timings`).
   - `addToPlaylist(playlistId: number, trackRowids: number[]): Promise<PlaylistDetail>` (POST `/playlists/${id}/tracks` body `{track_rowids}`).
**New symbols:** listed in Step 3, plus `send<T>` (module-private)
**Verify:** `cd web && npm run build && npm test`. Expected: build succeeds; vitest all passed.
**Forbidden:** don't change existing function behaviour beyond `getSongs`'s added optional params; no other files.
**Depends:** none
**Why:** One client seam for Phase 4-5 UI tasks, so no designer invents an endpoint shape.

## Phase 4 - Player engine, dialogs, queue UI

### Task 14: Gapless and crossfade with two audio elements
- [x] status
**Objective:** The next deterministic track is preloaded and started just before the current one ends, with an optional equal-power crossfade, and the outgoing play span closes as `track_end` at the handoff position.
**Write-scope:**
`web/src/player/gain.ts`
`web/src/player/PlayerContext.tsx`
**Read-context:** `web/src/player/gain.ts:1-100`, `web/src/player/PlayerContext.tsx:214-232`, `web/src/player/PlayerContext.tsx:341-373`, `web/src/player/PlayerContext.tsx:646-693`, `web/src/player/PlayerContext.tsx:776-881`, `web/src/player/crossfade.ts` (from Task 5), `web/src/player/queue.ts` (from Task 5)
**Anchors:**
`export function attachGain(audio: HTMLAudioElement | null): void {` @ `web/src/player/gain.ts`
`export function setGain(gainDb: number | null): void {` @ `web/src/player/gain.ts`
`export function handoffAtMs(durationMs: number, crossfadeS: number): number | null` @ `web/src/player/crossfade.ts` (from Task 5)
`export function upcomingIndex(length: number, at: number, mode: { shuffle: boolean; repeat: RepeatMode }): number | null` @ `web/src/player/queue.ts` (from Task 5)
**Steps:**
1. `gain.ts`: one shared `AudioContext` (created on the first `attachGain`), and per element maps `nodes: Map<HTMLAudioElement, GainNode>`, `dbs: Map<…, number | null>`, `fades: Map<…, number>` (default 1). `attachGain(audio)` attaches each element once (keep the guarded fallback). `setGain(audio: HTMLAudioElement, gainDb: number | null)`; new `setFade(audio: HTMLAudioElement, factor: number)`. `applyGain(audio)` = `(db == null ? 1 : 10 ** (db / 20)) * getVolume() * fade` on the node, or `audio.volume = clamp(getVolume() * fade, 0, 1)` without one. `setVolume` re-applies every known element. Keep the file's header comment and update it for two elements.
2. `PlayerContext.tsx` elements: replace `audio` with `pair = useRef<(HTMLAudioElement | null)[]>([null, null])`, `active = useRef(0)`, `el = () => pair.current[active.current]`, `standby = () => pair.current[1 - active.current]`. Every former `audio.current` uses `el()`. Render `{[0, 1].map((slot) => <audio key={slot} ref={(node) => { pair.current[slot] = node; }} preload="metadata" … />)}`; every handler starts `if (event.currentTarget !== el()) return;`, and `onPlay` calls `attachGain(event.currentTarget)`.
3. Refs: `preloaded: {rowid: number; at: number} | null`, `handoffTimer: number | null`, `handedOff: number | null` (rowid started by a handoff), `fadeOut: {el: HTMLAudioElement; ms: number} | null`, `fading = false`, `fadeTimer`; state `fadeDone` (number, bumped when a fade ends).
4. Preload effect, deps `[current, queue, index, shuffle, repeat, fadeDone]`: skip while `fading`. `at = upcomingIndex(queue.length, index, { shuffle, repeat })`; `t = at == null ? null : queue[at]`. If `t` and `unplayableReason(t) === null` and `preloaded` differs: `standby().src = withToken(stream url)`, `preload = "auto"`, `load()`, store `preloaded`. Else `preloaded = null`.
5. Schedule in the active `onTimeUpdate` (after the position updates): return when `!preloaded`, `handoffTimer != null`, `repeat === "one"`, or `queue[preloaded.at]?.track_rowid !== preloaded.rowid`. `at = handoffAtMs(duration_s * 1000, getCrossfadeSeconds())`; when `at != null && at - ms <= 1000`, `handoffTimer = setTimeout(doHandoff, max(0, at - ms))`.
6. `doHandoff`: clear `handoffTimer`; if `playId` is set, `const c = handoffClose(positionRef.current)`, `closeSpan(id, c.reason, c.positionMs)`, `playId = null`; `seekCount = 0`; `fadeOut = { el: el(), ms: fadeMs(duration, getCrossfadeSeconds()) }`; `active = 1 - active`; `handedOff = preloaded.rowid`; `preloaded = null`; `autoplay = true`; `closingReason = "track_end"`; `setIndex(at)`.
7. Load effect: when `handedOff === current.track_rowid`: clear `handedOff`, `setGain(el(), current.gain_db)`, then start: `setFade(el(), fadeOut.ms > 0 ? 0 : 1)`, `el().play()`, `fading = true`; for `ms > 0` a 50 ms `setInterval` applies `fadeGains(elapsed, ms)` to both elements and at the end pauses the outgoing, resets its fade to 1, clears `fading`, bumps `fadeDone`; for `ms <= 0` a `setTimeout(GAPLESS_LEAD_MS * 2)` clears `fading` and bumps `fadeDone` (the outgoing ends on its own). Otherwise (normal selection): keep today's src/load/play path, and also pause the standby, clear any fade interval, reset both fades to 1, clear `fading`. Cleanup also clears `handoffTimer`.
8. `seek`, `clearQueue` and `toggle`'s pause also clear `handoffTimer`; `clearQueue` pauses both elements. Media Session handlers use `el()`.
**New symbols:** `setFade(audio: HTMLAudioElement, factor: number): void` in `gain.ts`; `setGain` signature becomes `setGain(audio: HTMLAudioElement, gainDb: number | null)`
**Verify:** `cd web && npm test && npm run build && grep -c "<audio" src/player/PlayerContext.tsx && { grep -n "audio\.current" src/player/PlayerContext.tsx; true; }`. Expected: vitest all passed; build succeeds; `<audio` count prints `1`; the `audio.current` grep prints nothing.
**Forbidden:** don't change `plays.ts` or the ended-reason vocabulary (ADR 0001); don't preload under shuffle or repeat-one; no Web Audio buffer decoding; don't change radio fill/trim; no new deps.
**Depends:** Task 5
**Why:** Decision 15 option (a): near-gapless with two elements and the existing gain stage. The span closes at the fade start so `plays` records where the track physically stopped.

### Task 15: Edit, remove, add-to-playlist and lyrics dialogs [design]
- [x] status
**Objective:** Four dialog components exist and call the Task 13 client functions.
**Write-scope:**
`web/src/components/Dialog.tsx` (new)
`web/src/components/EditDetailsDialog.tsx` (new)
`web/src/components/RemoveDialog.tsx` (new)
`web/src/components/AddToPlaylistDialog.tsx` (new)
`web/src/components/LyricsEditDialog.tsx` (new)
`web/src/styles/dialogs.css` (new)
**Read-context:** `web/src/styles/base.css:21-41`, `web/src/styles/np.css:79-84`, `web/src/styles/add.css:9`, `web/src/api.ts` (from Task 13), `DESIGN.md:317-360`
**Anchors:**
`export function saveDetails(trackRowids: number[], values: DetailsValues): Promise<Track[]>` @ `web/src/api.ts` (from Task 13)
`export function removeTracks(trackRowids: number[], trash: boolean): Promise<RemoveResult>` @ `web/src/api.ts` (from Task 13)
`export function addToPlaylist(playlistId: number, trackRowids: number[]): Promise<PlaylistDetail>` @ `web/src/api.ts` (from Task 13)
`export function saveLyrics(trackRowid: number, text: string): Promise<{ lrc_stale: boolean }>` @ `web/src/api.ts` (from Task 13)
**Steps:**
1. `Dialog.tsx`: `export function Dialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode })` renders `<dialog className="dlg" aria-labelledby>` with an `h2.serif` title; `showModal()` on mount, `onClose` on the native `close`/`cancel` event; imports `../styles/dialogs.css`. Restores focus to the previously focused element on unmount.
2. `EditDetailsDialog({ tracks, onClose, onSaved }: { tracks: Track[]; onClose: () => void; onSaved: (tracks: Track[]) => void })`: fields per Design CONTENT. One track: prefilled, Title shown. Several: no Title field; each other field prefilled only when every track shares the value, otherwise empty, with the multi help line. Submit sends only fields whose trimmed value differs from the prefill and is non-empty (numbers via `Number`, `type="number"` inputs); nothing changed closes. "Reset to file tags" calls `resetDetails` then `onSaved(tracks)`. Server `detail` shows inline (`role="alert"`).
3. `RemoveDialog({ tracks, onClose, onRemoved }: { tracks: Track[]; onClose: () => void; onRemoved: (removed: number[]) => void })`: copy per Design CONTENT; "Remove from library" calls `removeTracks(ids, false)`, "Also move to Trash" `removeTracks(ids, true)`. All removed: `onRemoved` then close. Any failed: call `onRemoved` with the removed ids, keep the dialog open showing the failure line, and offer only "Close".
4. `AddToPlaylistDialog({ tracks, onClose, onAdded }: { tracks: Track[]; onClose: () => void; onAdded: () => void })`: lists `getPlaylists()`; choosing one calls `addToPlaylist`. "New playlist" + "Create" calls `createPlaylist(name, ids.slice(0, 100))`, then `addToPlaylist` with the rest when more than 100.
5. `LyricsEditDialog({ track, onClose, onSaved }: { track: Track; onClose: () => void; onSaved: (lrcStale: boolean) => void })`: loads `getLyrics(track.track_rowid)` (null gives ""), one `textarea` labelled "Lyrics" (min 12 rows, `lang` unset, 16 px), Save calls `saveLyrics`.
6. `dialogs.css` per Design: edit-dialogs (surface, backdrop, width, enter motion, reduced motion, `.field textarea` styling matching `.field input`, actions row wrapping).
**New symbols:** `Dialog`, `EditDetailsDialog`, `RemoveDialog`, `AddToPlaylistDialog`, `LyricsEditDialog` with the props above
**Verify:** `cd web && npm run build && npm test && node ~/.claude/skills/design-brain/scripts/preflight.mjs src/components/Dialog.tsx src/components/EditDetailsDialog.tsx src/components/RemoveDialog.tsx src/components/AddToPlaylistDialog.tsx src/components/LyricsEditDialog.tsx src/styles/dialogs.css`. Expected: build succeeds; vitest passed; preflight `0 errors`.
**Forbidden:** no new npm deps; no toast; no em dash in UI strings; don't wire the dialogs into views (Tasks 17-19); no new colour tokens.
**Depends:** Task 13
**Design:** edit-dialogs
**Why:** Decision 10 asks each time before removing; Decision 7 edits details; Decision 8 edits lyrics. One dialog shell keeps focus and Esc behaviour identical across them.

### Task 16: Queue editing in Up next [design]
- [x] status
**Objective:** Up next rows can be dragged to reorder within their group, moved with arrow keys, and removed with ✕.
**Write-scope:**
`web/src/player/NowPlayingSheet.tsx`
`web/src/components/TrackRow.tsx`
`web/src/components/Icons.tsx`
`web/src/styles/np.css`
**Read-context:** `web/src/player/NowPlayingSheet.tsx:46-60`, `web/src/player/NowPlayingSheet.tsx:262-280`, `web/src/player/NowPlayingSheet.tsx:340-380`, `web/src/components/TrackRow.tsx:1-58`, `web/src/components/Icons.tsx:1-60`, `web/src/player/PlayerContext.tsx` (from Task 5)
**Anchors:**
`export function TrackRow(props: {` @ `web/src/components/TrackRow.tsx`
`function UpNext({` @ `web/src/player/NowPlayingSheet.tsx`
`moveInQueue: (from: number, to: number) => void;` @ `web/src/player/PlayerContext.tsx` (from Task 5)
**Steps:**
1. `TrackRow`: add optional `leading?: ReactNode` and `trailing?: ReactNode`, rendered inside the `li` before and after the row button. No other change.
2. `Icons.tsx`: add `GripIcon` (two columns of three dots) and `CloseIcon` (an x) on the 24 grid, same stroke as the set.
3. `UpNext` gains `onMove: (from: number, to: number) => void` and `onRemove: (at: number) => void`, wired from `usePlayer().moveInQueue`/`removeFromQueue`. Each row: `leading` grip button (aria-label "Move {title}"), `trailing` remove button (aria-label "Remove {title} from queue").
4. Drag: pointerdown on the grip captures the pointer, records the row heights of its group (`getBoundingClientRect`), moves the row with `transform: translateY` and shifts the rows it passes by one row height (transform only), and on pointerup calls `onMove(fromAt, toAt)` with queue indexes from the same group's `at` values. Escape or pointercancel snaps back without a move.
5. Keyboard: ArrowUp/ArrowDown on a grip calls `onMove(at, neighbour.at)` within the group and keeps focus on the moved row's grip after re-render (focus by `data-at` lookup in an effect).
6. `np.css`: grip and remove at 44x44, `--text-2` at rest, `--text` on hover/focus; lifted row `--ink-raised` with the cover shadow; `@media (prefers-reduced-motion: reduce)` drops the shift transitions.
**New symbols:** `TrackRow` props `leading?: ReactNode`, `trailing?: ReactNode`; `GripIcon`, `CloseIcon`; `UpNext` props `onMove`, `onRemove`
**Verify:** `cd web && npm run build && npm test && node ~/.claude/skills/design-brain/scripts/preflight.mjs src/player/NowPlayingSheet.tsx src/components/TrackRow.tsx src/styles/np.css`. Expected: build succeeds; vitest passed; preflight `0 errors`.
**Forbidden:** no drag-and-drop library; no HTML5 `draggable` (fails on touch); don't change the group headings or radio logic; no em dash.
**Depends:** Task 5
**Design:** queue-edit
**Why:** Decision 15: drag reorders and ✕ removes, client-only.

## Phase 5 - Library UI wiring

### Task 17: Track menu, multi-select and the selection bar [design]
- [x] status
**Objective:** Library > Songs and the album page offer a per-row More menu and shift/cmd-click multi-select with a bulk action bar, both driving the Task 15 dialogs.
**Write-scope:**
`web/src/components/TrackRow.tsx`
`web/src/components/Icons.tsx`
`web/src/components/TrackMenu.tsx` (new)
`web/src/components/SelectionBar.tsx` (new)
`web/src/components/TrackActions.tsx` (new)
`web/src/components/selection.ts` (new)
`web/src/components/selection.test.ts` (new)
`web/src/styles/actions.css` (new)
`web/src/views/Library.tsx`
`web/src/views/AlbumTracks.tsx`
**Read-context:** `web/src/components/TrackRow.tsx` (from Task 16), `web/src/views/Library.tsx:46-110`, `web/src/views/Library.tsx:262-292`, `web/src/views/AlbumTracks.tsx:14-35`, `web/src/views/AlbumTracks.tsx:98-123`, `web/src/styles/shell.css:1-98`, `web/src/components/Dialog.tsx` (from Task 15)
**Anchors:**
`export function TrackRow(props: {` @ `web/src/components/TrackRow.tsx`
`export function RemoveDialog({ tracks, onClose, onRemoved }: { tracks: Track[]; onClose: () => void; onRemoved: (removed: number[]) => void })` @ `web/src/components/RemoveDialog.tsx` (from Task 15)
`playNext: (tracks: Track[]) => void;` @ `web/src/player/PlayerContext.tsx` (from Task 5)
**Steps:**
1. `selection.ts`: `export function useSelection(rowids: number[])` returning `{ selected: Set<number>; onSelect: (event: React.MouseEvent, rowid: number) => boolean; clear: () => void }`, built on a pure `export function nextSelection(selected: Set<number>, anchor: number | null, rowids: number[], rowid: number, keys: { shift: boolean; meta: boolean }): { selected: Set<number>; anchor: number | null; handled: boolean }`: meta/ctrl toggles and sets the anchor; shift with an anchor adds the inclusive range in `rowids` order; a plain click with a non-empty selection toggles; a plain click with an empty selection is `handled: false`. The hook clears when `rowids` changes and on Escape.
2. `selection.test.ts`: the four click rules above plus shift without an anchor acting like meta.
3. `TrackRow`: add `selected?: boolean` and `onSelect?: (event: React.MouseEvent<HTMLButtonElement>) => boolean`. The button's click runs `onSelect?.(event)` first and plays only when it returns false and the row is playable, so an unplayable row can still be selected. Selected rows get `data-selected` and a visually hidden ", selected" inside `.row-name`.
4. `Icons.tsx`: add `MoreIcon` (three horizontal dots).
5. `TrackMenu({ track, onAction }: { track: Track; onAction: (action: TrackAction) => void })` with `export type TrackAction = "next" | "queue" | "playlist" | "details" | "lyrics" | "remove"`: the More button and a `role="menu"` popover per Design: track-actions (opens upward when the button's bottom is within 320 px of the viewport bottom).
6. `TrackActions.tsx`: `export function useTrackActions(onChanged: () => void): { run: (action: TrackAction, tracks: Track[]) => void; dialogs: ReactNode }`. `next`/`queue` call `playNext`/`enqueue` with `playableOnly(tracks)`; the rest open the matching Task 15 dialog; `onChanged` after a save, removal or reset.
7. `SelectionBar({ count, onAction, onClear })`: the glass bar per Design (actions next, queue, playlist, details, remove; clear button with `CloseIcon`, aria-label "Clear selection").
8. `Library.tsx` Songs list and `AlbumTracks.tsx` rows: `selected`, `onSelect`, `trailing={<TrackMenu …/>}`; render the bar when the selection is non-empty and `dialogs` once. `onChanged`: Library drops `allSongs` (refetch) and re-runs the search; AlbumTracks adds a `version` state to its `useLoad` deps and bumps it.
**New symbols:** `useSelection`, `nextSelection`, `TrackMenu`, `TrackAction`, `useTrackActions`, `SelectionBar`, `MoreIcon`; `TrackRow` props `selected?`, `onSelect?`
**Verify:** `cd web && npm test && npm run build && node ~/.claude/skills/design-brain/scripts/preflight.mjs src/components/TrackMenu.tsx src/components/SelectionBar.tsx src/components/TrackRow.tsx src/styles/actions.css`. Expected: vitest all passed (selection tests included); build succeeds; preflight `0 errors`.
**Forbidden:** no new deps; no em dash; don't remove the Task 16 `leading`/`trailing` props; don't add selection to playlists or Home; don't change `PlayerContext`.
**Depends:** Task 15, Task 16
**Design:** track-actions
**Why:** Decision 15 bulk select and edit, Decision 10's ask-each-time delete, and per-row Play next / Add to queue.

### Task 18: Cover and artist picture upload, hide artist, lyrics editing entry, library sort [design]
- [ ] status
**Objective:** The album page changes its cover, the artist page shows and changes a picture and hides the artist, the lyrics panel edits lyrics and resolves stale timings, and Songs sorts server-side.
**Write-scope:**
`web/src/views/AlbumTracks.tsx`
`web/src/views/ArtistAlbums.tsx`
`web/src/views/Library.tsx`
`web/src/views/SyncedLyrics.tsx`
`web/src/jobs/JobsContext.tsx`
`web/src/styles/library.css`
`web/src/styles/np.css`
**Read-context:** `web/src/views/AlbumTracks.tsx` (from Task 17), `web/src/views/ArtistAlbums.tsx:1-51`, `web/src/views/Library.tsx` (from Task 17), `web/src/views/SyncedLyrics.tsx:76-172`, `web/src/jobs/JobsContext.tsx:16-80`, `web/src/components/CoverArt.tsx:1-57`, `web/src/components/LyricsEditDialog.tsx` (from Task 15)
**Anchors:**
`export function JobsProvider({ children }: { children: ReactNode }) {` @ `web/src/jobs/JobsContext.tsx`
`export function ArtistAlbums() {` @ `web/src/views/ArtistAlbums.tsx`
`function LyricsBody({` @ `web/src/views/SyncedLyrics.tsx`
`export function uploadAlbumArt(albumRowid: number, file: File): Promise<void>` @ `web/src/api.ts` (from Task 13)
**Steps:**
1. `JobsContext`: add `bumpArt: () => void` to `JobsApi` (default no-op) that increments `artVersion`, so every `CoverArt` re-requests.
2. `AlbumTracks.tsx`: pass the plain `/api/albums/${id}/art` to `CoverArt` (today it pre-applies `withToken`, which `CoverArt` applies again). Add "Change cover" (`.btn`) in `.album-actions`: a real button that clicks a visually hidden `<input type="file" accept="image/jpeg,image/png,image/webp">`; on change `uploadAlbumArt`, then `bumpArt()`; errors inline `role="alert"` with the Design copy.
3. `ArtistAlbums.tsx`: header with a round portrait (`/api/artists/${encodeURIComponent(artist)}/image` when the artist has an image, else the first album's cover; learn `has_image` from `getArtists()` by name), "Change picture" (same hidden-input pattern, `uploadArtistImage`, `bumpArt`) and "Hide artist" (`setArtistHidden(artist, true)`, then the inline status line from Design CONTENT; keep the page).
4. `Library.tsx`: the Artists gallery uses the artist image when `artist.has_image`, else today's album art. Songs tab: a "Sort by" `<select>` in `.field` styling beside the segment control, options per Design CONTENT mapped to `(sort, order)`: Title (title, asc), Recently added (added, desc), Most played (plays, desc), Year (year, desc), Length (duration, desc). The choice lives in the URL as `sort` (replace, like `tab`), feeds `getSongs(signal, sort, order)`, and is ignored while a search query is active (search keeps relevance order).
5. `SyncedLyrics.tsx`: plain and synced views get an "Edit lyrics" `.link-btn` (the "No lyrics for this track." note gets "Add lyrics"), opening `LyricsEditDialog`; after save, remount the body (key bump) so it refetches. When `data.lrc_stale`, show the stale note with "Keep old timings" (`setLyricsTimings(id, "keep")`) and "Remove timings" (`"drop"`), each refetching after.
6. CSS per Design: media-edit (portrait sizes, hidden file input with the existing `.vh` class, note spacing).
**New symbols:** `JobsApi.bumpArt`
**Verify:** `cd web && npm run build && npm test && node ~/.claude/skills/design-brain/scripts/preflight.mjs src/views/AlbumTracks.tsx src/views/ArtistAlbums.tsx src/views/Library.tsx src/views/SyncedLyrics.tsx src/styles/library.css src/styles/np.css`. Expected: build succeeds; vitest passed; preflight `0 errors`.
**Forbidden:** no new deps; no toast; no em dash; don't add an aligner or re-align button (M4); don't change the offset nudge.
**Depends:** Task 17
**Design:** media-edit
**Why:** Decision 10 (manual art, artist images, hide artist), Decision 8 (lyric edits, stale `.lrc`), Decision 15 (server-side sort).

### Task 19: Settings: crossfade, duplicates, hidden artists, removed songs [design]
- [x] status
**Objective:** Settings has Playback, Duplicates, Hidden artists and Removed songs sections that work end to end.
**Write-scope:**
`web/src/views/Settings.tsx`
`web/src/styles/settings.css`
**Read-context:** `web/src/views/Settings.tsx:1-148`, `web/src/styles/settings.css`, `web/src/styles/base.css:52-58`, `web/src/player/crossfade.ts` (from Task 5), `web/src/components/RemoveDialog.tsx` (from Task 15)
**Anchors:**
`export function Settings() {` @ `web/src/views/Settings.tsx`
`export function getCrossfadeSeconds(): number` @ `web/src/player/crossfade.ts` (from Task 5)
`export const getDuplicates = (signal?: AbortSignal) =>` @ `web/src/api.ts` (from Task 13)
**Steps:**
1. Split the file: keep the music-folder section as `function MusicFolders()` and add `Playback`, `Duplicates`, `HiddenArtists`, `RemovedSongs` functions in the same file, rendered in that order inside `Screen` once settings load.
2. `Playback`: `.range` input 0 to 12 step 1, initial `getCrossfadeSeconds()`, `--p` set to the percentage, `onChange` calls `setCrossfadeSeconds`; `<output>` shows "Off" or "{n} s"; help line from Design CONTENT.
3. `Duplicates`: "Find duplicates" loads `getDuplicates()`; per group a fieldset with legend "{title} · {artist}" and one radio per copy labelled "{album} · {FORMAT} · {m:ss}" (FORMAT uppercased, "?" when null), default the first playable copy; "Remove the other copies" opens `RemoveDialog` with the unchecked copies and reloads the list on removal.
4. `HiddenArtists`: `getHiddenArtists()` rows with "Unhide" (`setArtistHidden(name, false)`, then reload).
5. `RemovedSongs`: `getRemovedTracks()` rows (title; "{artist} · {album}") with "Restore" (`restoreTracks([id])`, then reload). Reload all three lists when `libraryVersion` changes.
6. `settings.css`: reuse the folder-row look for every new list; fieldset reset (no border, hairline separators); 44 px targets.
**New symbols:** `MusicFolders`, `Playback`, `Duplicates`, `HiddenArtists`, `RemovedSongs` (module-private components)
**Verify:** `cd web && npm run build && npm test && node ~/.claude/skills/design-brain/scripts/preflight.mjs src/views/Settings.tsx src/styles/settings.css`. Expected: build succeeds; vitest passed; preflight `0 errors`.
**Forbidden:** don't change the music-folder behaviour or copy; don't write crossfade to `settings.json`; no new deps; no em dash.
**Depends:** Task 15, Task 5
**Design:** settings-m2
**Why:** Decision 10's "Removed tracks" view and hidden-artist list, Decision 15's crossfade setting and duplicate finder.

## Phase 6 - Docs

### Task 20: Document M2 in the fork docs [docs]
- [ ] status
**Objective:** README, CLAUDE.md, CONTEXT.md, DESIGN.md and LAYOUT.md describe M2's behaviour as built.
**Write-scope:**
`README.md`
`CLAUDE.md`
`CONTEXT.md`
`DESIGN.md`
`LAYOUT.md`
**Read-context:** `CLAUDE.md:1-200`, `CONTEXT.md`, `DESIGN.md:317-360`, `LAYOUT.md:1-85`, this plan's Assumptions section, `muse/library/overrides.py` (from Task 7), `muse/library/removal.py` (from Task 8)
**Anchors:**
`def write(conn, track_rowids: list[int], values: dict, *, now: int | None = None) -> int` @ `muse/library/overrides.py` (from Task 7)
**Steps:**
1. README (audience: a stranger running from source): a short "Editing your library" section: edits never touch your files (overrides), Remove vs Also move to Trash, Settings > Removed songs to restore, hidden artists, cover and artist picture upload, lyric edits; `cd web && npm test` under development commands.
2. CLAUDE.md: add the web test command; list `overrides.py`, `removal.py`, `artists.py`, `lyrics_edit.py` in the `muse/library/` paragraph; one paragraph on the tombstone (`status='removed'`) and the write-through override rule; one sentence on the two-element player in the Frontend paragraph.
3. CONTEXT.md: define **override**, **tombstone**, **stale timings**, **handoff** (gapless/crossfade switch) in the glossary's existing style.
4. DESIGN.md §9: Track rows gain the More menu, selection ring and queue grip/remove; widen "Modals are rare" to name the four edit dialogs; add the selection bar to the z-index list (20).
5. LAYOUT.md: Settings sections order; the selection bar's place above the player.
**New symbols:** none
**Verify:** `grep -n "track_overrides\|status='removed'\|npm test" CLAUDE.md README.md && grep -n "tombstone\|override" CONTEXT.md`. Expected: each grep finds at least one line.
**Forbidden:** every claim checked against the code; no plan or spec paths in README; don't document M3-M5 features as present.
**Depends:** Task 18, Task 19
**Why:** CLAUDE.md requires CONTEXT.md to change when terms do; executors of M3+ read these first.
