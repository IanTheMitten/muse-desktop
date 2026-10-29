"""Hidden artists and manual artist images."""

import sqlite3
import time


def hide(conn: sqlite3.Connection, artist_name: str, hidden: bool, *, now: int | None = None) -> None:
    if hidden:
        conn.execute(
            "INSERT OR REPLACE INTO hidden_artists (artist_name, hidden_at) VALUES (?, ?)",
            (artist_name, int(time.time()) if now is None else now))
    else:
        conn.execute("DELETE FROM hidden_artists WHERE artist_name = ?", (artist_name,))
    conn.commit()


def hidden(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT artist_name, hidden_at FROM hidden_artists ORDER BY artist_name COLLATE NOCASE")
    return [dict(r) for r in rows]


def set_image(conn: sqlite3.Connection, artist_name: str, art_path: str, *, now: int | None = None) -> None:
    conn.execute(
        "INSERT INTO artist_images (artist_name, art_path, source, updated_at)"
        " VALUES (?, ?, 'manual', ?)"
        " ON CONFLICT(artist_name) DO UPDATE SET art_path = excluded.art_path,"
        " source = 'manual', updated_at = excluded.updated_at",
        (artist_name, art_path, int(time.time()) if now is None else now))
    conn.commit()


def image_path(conn: sqlite3.Connection, artist_name: str) -> str | None:
    row = conn.execute(
        "SELECT art_path FROM artist_images WHERE artist_name = ?", (artist_name,)).fetchone()
    return row["art_path"] if row else None
