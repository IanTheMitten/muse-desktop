"""Folder scanner: indexes local audio files into `tracks`. Music folders are
only ever read; lyrics sidecars and embedded art are copied into app storage."""

import json
import logging
import os
import shutil
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import tags as _tags
from .tags import AUDIO_EXTENSIONS
from .library import lyrics_state, overrides
from .library.art import record_cover
from .library.tracks import (
    album_rowid_for_key,
    album_rowid_for_release,
    index_track,
    is_instrumental_path,
    next_local_rowid,
)
from .lrc import strip_timestamps
from .lyrics import lyrics_files

log = logging.getLogger("muse.scanner")


@dataclass
class ScanResult:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    moved: int = 0
    missing: int = 0
    unreadable: int = 0
    skipped_removed: int = 0


def _fields(path: Path, t: "_tags.FileTags") -> dict:
    artist = t.artist or "Unknown artist"
    album = t.album or "Unknown album"
    return {
        "track_name": t.title or path.stem,
        "artist_name": artist,
        "album_name": album,
        "album_key": f"{(t.album_artist or artist).casefold()}\x1f{album.casefold()}",
        "track_no": t.track_no,
        "disc_no": t.disc_no,
        "duration_ms": t.duration_ms,
        "isrc": t.isrc,
        "year": t.year,
    }


def _insert(conn, path, st, f, now, lyrics_root, art_root, pic) -> None:
    rowid = next_local_rowid(conn)
    album_rowid = album_rowid_for_key(conn, f["album_key"])
    conn.execute(
        "INSERT INTO tracks (track_rowid, track_id, origin, metadata_tier, artist_rowid,"
        " album_rowid, artist_name, artist_names, album_name, track_name, track_no, disc_no,"
        " duration_ms, isrc, file_path, is_instrumental, status, added_at, file_size,"
        " file_mtime, format, album_key, year)"
        " VALUES (?, NULL, 'local', 'tags', NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'done',"
        " ?, ?, ?, ?, ?, ?)",
        (rowid, album_rowid, f["artist_name"], json.dumps([f["artist_name"]]),
         f["album_name"], f["track_name"], f["track_no"], f["disc_no"], f["duration_ms"],
         f["isrc"], str(path), int(is_instrumental_path(str(path), f["track_name"])), now,
         st.st_size, int(st.st_mtime), path.suffix.lower().lstrip("."), f["album_key"], f["year"]),
    )
    index_track(conn, rowid, f["track_name"], f["artist_name"], f["album_name"])

    lrc = path.with_suffix(".lrc")
    txt = next((p for p in (path.with_name(f"{path.stem}.lyrics.txt"),
                            path.with_suffix(".txt")) if p.is_file()), None)
    has_lrc = lrc.is_file()
    try:
        if txt or has_lrc:
            lyrics_root.mkdir(parents=True, exist_ok=True)
            txt_dst, lrc_dst = lyrics_files(lyrics_root, rowid)
            if has_lrc:
                shutil.copyfile(lrc, lrc_dst)
            if txt:
                shutil.copyfile(txt, txt_dst)
            else:
                txt_dst.write_text(
                    strip_timestamps(lrc.read_text(encoding="utf-8", errors="replace")),
                    encoding="utf-8")
            conn.execute("UPDATE tracks SET lyrics_path = ? WHERE track_rowid = ?",
                         (str(txt_dst), rowid))
            lyrics_state.upsert(conn, rowid, state="synced" if has_lrc else "plain",
                                synced=has_lrc, checked_at=now)
    except OSError as e:
        log.warning("lyrics sidecar copy failed for %s: %s", path, e)

    try:
        if pic and conn.execute("SELECT 1 FROM album_art WHERE album_rowid = ?",
                                (album_rowid,)).fetchone() is None:
            data, ext = pic
            art_root.mkdir(parents=True, exist_ok=True)
            out = art_root / f"{album_rowid}{ext}"
            out.write_bytes(data)
            record_cover(conn, album_rowid, str(out), source="embedded", now=now)
    except OSError as e:
        log.warning("embedded art write failed for %s: %s", path, e)


def holds_audio(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    for _, dirnames, filenames in os.walk(folder, followlinks=False):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if any(not n.startswith(".") and Path(n).suffix.lower() in AUDIO_EXTENSIONS
               for n in filenames):
            return True
    return False


def scan(
    conn: sqlite3.Connection,
    folders: list[Path],
    *,
    lyrics_root: Path,
    art_root: Path,
    read_tags: Callable = _tags.read_tags,
    embedded_picture: Callable = _tags.embedded_picture,
    now: int | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> ScanResult:
    now = now if now is not None else int(time.time())
    res = ScanResult()
    present: list[Path] = []
    files: list[Path] = []
    for folder in folders:
        folder = Path(folder).resolve()
        if folder.suffix.lower() in AUDIO_EXTENSIONS and not folder.is_dir():
            # One dropped song (Decision 15): read in place, rescanned with the chain, never watched.
            if folder.is_file():
                files.append(folder)
            else:
                res.missing += conn.execute(
                    "UPDATE tracks SET status = 'missing_file' WHERE file_path = ?"
                    " AND status = 'done' AND removed_at IS NULL", (str(folder),)).rowcount
            continue
        if not folder.is_dir():
            for r in _rows_under(conn, folder):
                if r["status"] == "done":
                    conn.execute("UPDATE tracks SET status = 'missing_file'"
                                 " WHERE track_rowid = ?", (r["track_rowid"],))
                    res.missing += 1
            continue
        present.append(folder)
        for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            files += [Path(dirpath) / n for n in sorted(filenames)
                      if not n.startswith(".") and Path(n).suffix.lower() in AUDIO_EXTENSIONS]

    total, done = len(files), 0
    seen: set[str] = set()
    new: list[tuple[Path, os.stat_result, "_tags.FileTags"]] = []
    for path in files:
        done += 1
        if on_progress:
            on_progress(done, total, path.name)
        if str(path) in seen:
            continue
        try:
            st = path.stat()
        except OSError:
            res.unreadable += 1
            continue
        seen.add(str(path))
        row = conn.execute("SELECT * FROM tracks WHERE file_path = ?", (str(path),)).fetchone()
        if row is not None and row["removed_at"] is not None:
            res.skipped_removed += 1
            continue
        if (row is not None and row["status"] == "done" and row["file_size"] == st.st_size
                and row["file_mtime"] == int(st.st_mtime)):
            res.unchanged += 1
            continue
        t = read_tags(path)
        if t is None:
            res.unreadable += 1
            continue
        if row is None:
            new.append((path, st, t))
            continue
        f = overrides.apply(conn, row["track_rowid"], _fields(path, t))
        if row["metadata_tier"] != "tags" and "album_name" not in overrides.get(
                conn, row["track_rowid"]):
            # a download groups by release, not by tag album_key: keep it there,
            # and rejoin the release once an album edit is reset
            f["album_key"] = None
            album_rowid = (album_rowid_for_release(conn, row["release_mbid"])
                           if row["album_key"] is not None and row["release_mbid"]
                           else row["album_rowid"])
        else:
            album_rowid = album_rowid_for_key(conn, f["album_key"])
        conn.execute(
            "UPDATE tracks SET track_name=?, artist_name=?, artist_names=?, album_name=?,"
            " album_key=?, album_rowid=?, track_no=?, disc_no=?, duration_ms=?, isrc=?,"
            " art_path=COALESCE((SELECT art_path FROM album_art WHERE album_rowid = ?), art_path),"
            " year=?, file_size=?, file_mtime=?, status='done', error=NULL WHERE track_rowid=?",
            (f["track_name"], f["artist_name"], json.dumps([f["artist_name"]]),
             f["album_name"], f["album_key"], album_rowid,
             f["track_no"], f["disc_no"], f["duration_ms"], f["isrc"], album_rowid,
             f["year"], st.st_size, int(st.st_mtime), row["track_rowid"]))
        index_track(conn, row["track_rowid"], f["track_name"], f["artist_name"], f["album_name"])
        res.updated += 1

    vanished = sorted(
        (r for folder in present for r in _rows_under(conn, folder)
         if r["file_path"] not in seen),
        key=lambda r: r["track_rowid"])
    for path, st, t in new:
        f = _fields(path, t)
        def same(r):
            g = overrides.apply(conn, r["track_rowid"], f)
            return (r["file_size"], r["duration_ms"], r["artist_name"], r["album_name"],
                    r["track_name"]) == (st.st_size, g["duration_ms"], g["artist_name"],
                                         g["album_name"], g["track_name"])
        match = next((r for r in vanished if same(r)), None)
        if match is not None:
            vanished.remove(match)
            conn.execute(
                "UPDATE tracks SET file_path=?, file_size=?, file_mtime=?, status='done',"
                " error=NULL WHERE track_rowid=?",
                (str(path), st.st_size, int(st.st_mtime), match["track_rowid"]))
            res.moved += 1
        else:
            _insert(conn, path, st, f, now, lyrics_root, art_root, embedded_picture(path))
            res.added += 1
    for r in vanished:
        if r["status"] == "done":
            conn.execute("UPDATE tracks SET status = 'missing_file' WHERE track_rowid = ?",
                         (r["track_rowid"],))
            res.missing += 1
    return res


def _rows_under(conn: sqlite3.Connection, folder: Path) -> list[sqlite3.Row]:
    return [r for r in conn.execute(
        "SELECT * FROM tracks WHERE origin = 'local' AND removed_at IS NULL"
        " AND file_path IS NOT NULL")
        if Path(r["file_path"]).is_relative_to(folder)]
