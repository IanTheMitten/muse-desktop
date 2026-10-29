"""Importing `download_manifest.json` into the library.

See ADR 0002. The manifest is owned by `music_downloader.py` and is a record of
*acquisition attempts*, failures included. This module reads it and never writes
it. Data flows one direction only.

Import runs at every startup, not once. It is idempotent, keyed on
`track_rowid`, so a downloader run that adds fifty tracks needs no coordination
at all — restart, and they appear. Running it continuously also means a bug in
it surfaces today rather than during a rebuild in two years.
"""

import json
import os
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..catalog import CatalogLookup, TrackFacts
from ..config import PROJECT_ROOT
from ..titles import normalize_title

# `06 - Some Song - Inst.m4a`. LRCLIB carries an explicit instrumental boolean
# and replaces this heuristic in slice 4.
_INSTRUMENTAL_MARKERS = (" - inst.", " - instrumental.")

# Fields the app owns. An import must never overwrite them: `art_path` is filled
# by the art backfill, `gain_db` by slice 2, `added_at` records when the track
# first appeared here, and `lyrics_path` is filled by the lyrics-sync batch
# (slice 4) for a track the manifest never recorded one for -- the exact same
# reasoning as `art_path`: a re-import must not silently erase a pointer the
# app itself resolved after the download that the manifest knows nothing about.
APP_OWNED = ("art_path", "gain_db", "added_at", "lyrics_path")

# Columns whose value can come from the catalog mirror. A re-import that has
# no value for one (the mirror missing, or the track absent from it) keeps
# what is stored rather than writing NULL over it.
CATALOG_SOURCED = ("album_rowid", "track_no", "disc_no", "duration_ms", "isrc")


@dataclass
class ImportResult:
    inserted: int = 0
    updated: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    missing_files: int = 0
    missing_from_catalog: int = 0

    @property
    def total(self) -> int:
        return self.inserted + self.updated


def load_manifest(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _is_instrumental(file_path: str | None, track_name: str) -> bool:
    haystack = (file_path or track_name or "").lower()
    return any(marker in haystack for marker in _INSTRUMENTAL_MARKERS)


def _artist_names(record: dict) -> str:
    """JSON array of artist names.

    774 of 775 manifest entries carry `artist_names`. The one that does not
    falls back to its single `artist_name` rather than importing an empty list,
    because an empty list downstream reads as "no artists" instead of "we only
    know one".
    """
    names = record.get("artist_names")
    if isinstance(names, list) and names:
        return json.dumps(names)
    if isinstance(names, str) and names.strip():
        return json.dumps([names.strip()])
    return json.dumps([record.get("artist_name") or ""])


def _resolve_path(raw: str | None, root: Path) -> str | None:
    """Turn a manifest path into one that does not depend on the cwd.

    The manifest stores paths relative to the project root, because
    `music_downloader.py` is always run from there. Anything else that resolves
    them against its own working directory gets a different answer: launched
    from a subdirectory, every file looks missing and the import quietly marks
    the entire library `missing_file` while reporting complete success.

    Resolved to absolute at import time, so nothing downstream — streaming,
    lyrics, the art job — has to know this rule or be launched from the right
    place.
    """
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    return str(path)


def _resolve_status(
    record: dict, file_path: str | None, file_exists: Callable[[str], bool]
) -> str:
    """`done` | `error` | `missing_file`, decided by what is on disk.

    The manifest's own `status` is a record of whether the *download run*
    finished cleanly, which is not the same question as whether there is a
    playable file. Measured on 2026-08-10: all 64 `error` entries, 12 of them
    have audio between 3 MB and 11 MB, and every one of those 12 failed at the
    Genius lyrics step after the audio had already been written. Trusting the
    label there would hide twelve playable tracks forever.

    So: audio on disk means `done`, whatever the manifest says. The error text
    is preserved either way, so slice 4 still knows the lyrics were never
    fetched.

    The mirror case — a `done` entry whose file is gone — imports as
    `missing_file`: visible, excluded from browse, and not a crash the first
    time it is clicked. There are none today, but a deleted file is one `rm`
    away and it must not take playback down with it.
    """
    if file_path and file_exists(file_path):
        return "done"
    if (record.get("status") or "").strip().lower() == "done":
        return "missing_file"
    return "error"


def import_manifest(
    manifest: dict,
    catalog_lookup: CatalogLookup,
    conn: sqlite3.Connection,
    *,
    root: Path | None = None,
    file_exists: Callable[[str], bool] = os.path.exists,
    now: int | None = None,
) -> ImportResult:
    """Import every manifest entry into `tracks`. Idempotent on `track_rowid`.

    `root` is what the manifest's relative paths are relative to — the project
    directory, never the process's working directory.
    """
    root = root or PROJECT_ROOT
    downloads = manifest.get("downloads") or {}
    stamp = now if now is not None else int(time.time())
    result = ImportResult()

    records = []
    for key, record in downloads.items():
        rowid = record.get("track_rowid")
        if rowid is None:
            rowid = int(key)
        records.append((int(rowid), record))

    facts = catalog_lookup.track_facts([rowid for rowid, _ in records])
    existing = {
        row[0] for row in conn.execute("SELECT track_rowid FROM tracks").fetchall()
    }

    for rowid, record in records:
        fact: TrackFacts | None = facts.get(rowid)
        if fact is None:
            result.missing_from_catalog += 1

        file_path = _resolve_path(record.get("file_path"), root)
        lyrics_path = _resolve_path(record.get("lyrics_path"), root)
        status = _resolve_status(record, file_path, file_exists)
        result.by_status[status] = result.by_status.get(status, 0) + 1
        if status == "missing_file":
            result.missing_files += 1

        track_name = record.get("track_name") or ""
        artist_name = record.get("artist_name") or ""
        album_name = record.get("album_name") or ""

        row = {
            "track_rowid": rowid,
            "track_id": record.get("track_id"),
            "origin": "catalog",
            "metadata_tier": "catalog",
            "artist_rowid": record.get("artist_rowid"),
            "album_rowid": record.get("album_rowid")
            or (fact.album_rowid if fact else None),
            "artist_name": artist_name,
            "artist_names": _artist_names(record),
            "album_name": album_name,
            "track_name": track_name,
            "track_no": record.get("track_no") or (fact.track_no if fact else None),
            # The catalog is authoritative for disc number; the manifest's is not
            # reliable. Its value is the fallback, not the other way round.
            "disc_no": (fact.disc_no if fact else None) or record.get("disc_no"),
            "duration_ms": fact.duration_ms if fact else None,
            "isrc": fact.isrc if fact else None,
            "file_path": file_path,
            "lyrics_path": lyrics_path,
            "is_instrumental": int(_is_instrumental(file_path, track_name)),
            "source_site_id": record.get("source_site_id"),
            "status": status,
            "error": record.get("error"),
            "added_at": stamp,
        }

        _settle_track_id(conn, row)

        columns = list(row)
        placeholders = ",".join("?" * len(columns))
        # Every column except the app-owned ones is refreshed from the manifest.
        # The catalog-sourced ones keep their stored value when this import
        # has none: the mirror is optional and can be deleted, and a re-import
        # without it must not erase facts it gave earlier that nothing else
        # on disk holds.
        updates = ",".join(
            f"{name}=COALESCE(excluded.{name}, tracks.{name})"
            if name in CATALOG_SOURCED
            else f"{name}=excluded.{name}"
            for name in columns
            if name not in APP_OWNED and name != "track_rowid"
        )
        conn.execute(
            f"INSERT INTO tracks ({','.join(columns)}) VALUES ({placeholders})"
            f" ON CONFLICT(track_rowid) DO UPDATE SET {updates}",
            [row[name] for name in columns],
        )

        if rowid in existing:
            result.updated += 1
        else:
            result.inserted += 1

        _index_track(conn, rowid, track_name, artist_name, album_name)

    return result


def _settle_track_id(conn: sqlite3.Connection, row: dict) -> None:
    """Keep `tracks.track_id` unique before this row is upserted.

    The manifest is authoritative for the overlap (ADR 0002), so a manifest
    entry takes its Spotify id from an app-ingested (`origin='local'`) row,
    whatever that row's status: the same end state `run_download` reaches
    when the manifest entry came first. Only `track_id` moves; the local row
    keeps its rowid, plays and ratings. Another catalog row keeps the id and
    this one imports without it, so two entries never trade it across runs.
    """
    if row["track_id"] is None:
        return
    holder = conn.execute(
        "SELECT track_rowid, origin FROM tracks WHERE track_id = ? AND track_rowid != ?",
        (row["track_id"], row["track_rowid"]),
    ).fetchone()
    if holder is None:
        return
    if holder[1] == "local":
        conn.execute(
            "UPDATE tracks SET track_id = NULL WHERE track_rowid = ?", (holder[0],)
        )
    else:
        row["track_id"] = None


def _index_track(
    conn: sqlite3.Connection,
    rowid: int,
    track_name: str,
    artist_name: str,
    album_name: str,
) -> None:
    """Refresh the FTS row for a track.

    Delete-then-insert rather than update, because `tracks_fts` is a standalone
    FTS5 table keyed by `rowid` and re-running the import must not accumulate
    duplicate index entries.

    The `norm_*` columns are search entries only. Nothing displays them.
    """
    conn.execute("DELETE FROM tracks_fts WHERE rowid = ?", (rowid,))
    conn.execute(
        "INSERT INTO tracks_fts"
        "  (rowid, track_name, artist_name, album_name,"
        "   norm_track_name, norm_artist_name, norm_album_name)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            rowid,
            track_name,
            artist_name,
            album_name,
            normalize_title(track_name),
            normalize_title(artist_name),
            normalize_title(album_name),
        ),
    )
