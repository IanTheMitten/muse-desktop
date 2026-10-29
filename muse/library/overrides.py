"""The Decision 7 override layer: user edits to tags live in `track_overrides`
and are written through to `tracks`, so every reader sees the edit. The audio
file is never written. No BEGIN: the shared connection may already be inside
the launch chain's transaction."""

import json
import sqlite3
import time

from .tracks import album_rowid_for_key, index_track

FIELDS = ("track_name", "artist_name", "album_name", "track_no", "disc_no", "year")
INT_FIELDS = {"track_no", "disc_no", "year"}


def get(conn: sqlite3.Connection, rowid: int) -> dict:
    return {r["field"]: int(r["value"]) if r["field"] in INT_FIELDS else r["value"]
            for r in conn.execute(
                "SELECT field, value FROM track_overrides WHERE track_rowid = ?", (rowid,))}


def apply(conn: sqlite3.Connection, rowid: int, fields: dict) -> dict:
    f = {**fields, **get(conn, rowid)}
    if "album_name" in get(conn, rowid):
        prefix = (fields.get("album_key") or fields["artist_name"].casefold() + "\x1f"
                  ).split("\x1f", 1)[0]
        f["album_key"] = prefix + "\x1f" + f["album_name"].casefold()
    return f


def _store(conn: sqlite3.Connection, rowid: int, f: dict) -> None:
    # catalog rows have no album_key: keep their album_rowid
    album_rowid = (album_rowid_for_key(conn, f["album_key"]) if f.get("album_key")
                   else f["album_rowid"])
    conn.execute(
        "UPDATE tracks SET track_name=?, artist_name=?, artist_names=?, album_name=?,"
        " album_key=?, album_rowid=?, track_no=?, disc_no=?, year=?,"
        " art_path = COALESCE((SELECT art_path FROM album_art WHERE album_rowid = ?),"
        " (SELECT art_path FROM tracks WHERE album_rowid = ? AND art_path IS NOT NULL"
        " AND track_rowid != ? LIMIT 1), art_path)"
        " WHERE track_rowid=?",
        (f["track_name"], f["artist_name"], json.dumps([f["artist_name"]]), f["album_name"],
         f.get("album_key"), album_rowid, f["track_no"], f["disc_no"], f["year"],
         album_rowid, album_rowid, rowid, rowid))
    index_track(conn, rowid, f["track_name"], f["artist_name"], f["album_name"])


def write(conn: sqlite3.Connection, rowids: list[int], values: dict) -> int:
    if not values or any(k not in FIELDS for k in values):
        raise ValueError(f"override fields must be a non-empty subset of {FIELDS}")
    now = int(time.time())
    n = 0
    for rowid in rowids:
        row = conn.execute("SELECT * FROM tracks WHERE track_rowid = ?", (rowid,)).fetchone()
        if row is None:
            continue
        for k, v in values.items():
            conn.execute(
                "INSERT INTO track_overrides (track_rowid, field, value, updated_at)"
                " VALUES (?, ?, ?, ?) ON CONFLICT(track_rowid, field)"
                " DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (rowid, k, str(v), now))
        _store(conn, rowid, apply(conn, rowid, dict(row)))
        n += 1
    return n


def clear(conn: sqlite3.Connection, rowids: list[int]) -> int:
    marks = ",".join("?" * len(rowids))
    conn.execute(f"DELETE FROM track_overrides WHERE track_rowid IN ({marks})", rowids)
    # null mtime makes the next scan re-read the file and restore its tags
    return conn.execute(
        f"UPDATE tracks SET file_mtime = NULL WHERE track_rowid IN ({marks})", rowids).rowcount
