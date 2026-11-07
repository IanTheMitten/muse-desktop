"""Cached CLAP text-prompt vectors, one per (audio model, name).

The energy axis scores every track against two of these (`muse/energy.py`);
a vector belongs to the audio model that embedded it, so a model change
leaves the old rows unread rather than wrong.
"""

import sqlite3


def current_audio_model(conn: sqlite3.Connection) -> str | None:
    """The audio model most tracks were embedded with; ties go to the lower name."""
    row = conn.execute(
        "SELECT audio_model FROM embeddings WHERE audio_vec IS NOT NULL"
        " AND audio_model IS NOT NULL"
        " GROUP BY audio_model ORDER BY COUNT(*) DESC, audio_model LIMIT 1"
    ).fetchone()
    return row["audio_model"] if row else None


def get(conn: sqlite3.Connection, audio_model: str, name: str) -> list[float] | None:
    from ..embed import unpack_vector

    row = conn.execute(
        "SELECT vec FROM prompt_vectors WHERE audio_model = ? AND name = ?",
        (audio_model, name),
    ).fetchone()
    return unpack_vector(row["vec"]) if row else None


def put(conn: sqlite3.Connection, audio_model: str, name: str, vec: list[float]) -> None:
    from ..embed import pack_vector

    conn.execute(
        "INSERT OR REPLACE INTO prompt_vectors (audio_model, name, vec) VALUES (?, ?, ?)",
        (audio_model, name, pack_vector(vec)),
    )
    conn.commit()


def stamp(conn: sqlite3.Connection) -> tuple:
    return tuple(conn.execute("SELECT COUNT(*), MAX(rowid) FROM prompt_vectors").fetchone())
