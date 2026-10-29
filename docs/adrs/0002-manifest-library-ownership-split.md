# ADR 0002: Manifest and library are split by ownership, not by cutover

- **Status:** Accepted
- **Date:** 2026-08-10
- **Slice:** 1 (Player)
- **Related:** `docs/prds/2026-08-06-muse-player.md`, `docs/superpowers/specs/2026-08-06-spec-slice-1-player.md`, `music_downloader.py`

## Context

`download_manifest.json` currently holds everything the system knows about
what has been acquired: 775 entries keyed by catalog `track_rowid`, each with
file paths, source query, status, and error text. `music_downloader.py` owns
it, rewriting the whole file on every upsert.

Slice 1 introduces `library.sqlite3`, which needs the same facts plus things
the manifest has no place for — play history, ratings, player state, art
paths, loudness gain. The tempting move is a cutover: import the manifest once,
then have the app own the data and let the downloader write to SQLite too.
One store, one writer, no duplication.

That is a trap for three reasons.

The manifest is a **record of attempts**, not a record of the library. Its 64
`status:"error"` entries are the memory of what was tried and failed, which is
exactly what stops the next download run from retrying them blindly. Folding
those into a table of playable tracks conflates two different questions.

The downloader is **working software that predates the app** and will keep
running independently. Making it depend on the app's schema couples a script
that works to a system that does not exist yet. The PRD already forbids
modifying `music_downloader.py` during slices 1–2 for this reason.

And a one-time import is a **cutover with a failure mode that only appears
later**: once the app starts writing back, the manifest silently rots, and the
next downloader run operates on a stale view of what exists.

## Decision

Ownership is split by writer, and data flows one direction only.

**`download_manifest.json` is owned by `music_downloader.py`.** It is the
record of acquisition attempts, including failures. The app **never writes it**
— not to correct a path, not to mark a file missing, not to record a
successful repair. The app opens it read-only.

**`library.sqlite3` is owned by the app.** It holds the playable library plus
everything the manifest has no concept of: plays, ratings, player state, art,
gain.

**Import runs at every startup, not once.** It is idempotent, keyed on
`track_rowid`:

```
import_manifest(manifest, catalog_lookup, conn) -> ImportResult
```

New entries insert. Changed entries update the fields the manifest owns.
Fields the app owns — `art_path`, `gain_db`, `added_at` — are never
overwritten by an import. Entries whose `file_path` points at a file that is
no longer on disk are marked, not deleted, because deleting a track row would
orphan its play history.

Because import is idempotent and runs continuously, a downloader run that adds
50 tracks needs no coordination at all: restart the app, or wait for the next
one, and they appear.

## Consequences

**Good.** The downloader keeps working, unmodified and unaware of the app. The
import path is exercised on every single startup rather than once during a
migration nobody will ever run again, so a bug in it surfaces immediately
instead of during a rebuild years later. `import_manifest()` takes a manifest
dict, a catalog lookup, and a connection — all injected — which makes it the
primary test seam for slice 1, runnable against a real SQLite temp file with no
fixture catalog and no fixture audio.

**Bad.** Two stores hold overlapping facts, and the manifest is authoritative
for the overlap. Anything the app learns about a track that contradicts the
manifest — a corrected artist name, a moved file — cannot be written back and
must either be tolerated or fixed in the downloader. Slice 3 will have to
revisit this when the app gains its own ingest path, and at that point the app
becomes a second writer of acquisition records; that is a deliberate future
decision, not an accident of this one.

**Bad.** Startup cost scales with manifest size. At 775 entries this is
invisible. If it ever is not, the fix is a content hash to skip unchanged
imports — not moving to a one-time cutover.

**Binding.** No code path in the app may open `download_manifest.json` for
writing. The catalog mirrors are likewise opened `mode=ro` and are never
written by anything.
