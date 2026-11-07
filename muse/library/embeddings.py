"""Where content embeddings are recorded, and the embed batch's work queue.

One row per track, written once and then only ever replaced -- never a second
table for "has this been embedded", because `embeddings.audio_vec IS NULL`
already answers that: a track with no row here, or a row missing it, has not
been embedded, and that is the whole work queue. A batch killed halfway
through restarts into exactly this set.

A NULL `feature_vec` on an otherwise-complete row is not missing work -- a
null_response feature reading is a real answer, not a track left behind.
`audio_vec` is the single gate because it is computed from the audio file
directly and every `done` track has one; `feature_vec` does not gate it
because it can legitimately end up NULL. `lyrics_hash` is not a vector: it
is the lyrics-edit fingerprint that lyric-tag staleness keys on.
"""

import sqlite3
import time



def _now() -> int:
    return int(time.time())


def pending(conn: sqlite3.Connection) -> list[dict]:
    """Playable tracks with no `audio_vec` yet: the resumable batch's work
    queue. Covers a track with no `embeddings` row at all and one left behind
    by a killed run."""
    rows = conn.execute(
        "SELECT t.track_rowid, t.file_path, t.lyrics_path"
        "  FROM tracks t LEFT JOIN embeddings e USING (track_rowid)"
        " WHERE t.status = 'done'"
        "   AND (e.track_rowid IS NULL OR e.audio_vec IS NULL)"
        " ORDER BY t.track_rowid"
    ).fetchall()
    return [dict(row) for row in rows]


def lyrics_hash_by_track(conn: sqlite3.Connection) -> dict[int, str | None]:
    """The stored `lyrics_hash` for every already-embedded track.

    The batch driver compares this against a freshly computed hash of each
    track's current cleaned lyrics to find a manual edit (story 56) without
    reading the whole library through a second SQL predicate.
    """
    rows = conn.execute(
        "SELECT track_rowid, lyrics_hash FROM embeddings"
        " WHERE audio_vec IS NOT NULL"
    ).fetchall()
    return {row["track_rowid"]: row["lyrics_hash"] for row in rows}


def upsert(
    conn: sqlite3.Connection,
    track_rowid: int,
    *,
    audio_vec: bytes | None,
    feature_vec: bytes | None,
    audio_model: str | None,
    lyrics_hash: str | None,
    now: int | None = None,
) -> None:
    """Write a full embed result for one track, replacing whatever was there.

    Every field is given explicitly, never merged with what is already
    stored. A lyrics-only change goes through `update_lyrics_hash` instead.
    """
    at = now if now is not None else _now()
    conn.execute(
        "INSERT INTO embeddings (track_rowid, audio_vec, feature_vec,"
        " audio_model, lyrics_hash, indexed_at)"
        " VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(track_rowid) DO UPDATE SET"
        "   audio_vec = excluded.audio_vec,"
        "   feature_vec = excluded.feature_vec,"
        "   audio_model = excluded.audio_model,"
        "   lyrics_hash = excluded.lyrics_hash,"
        "   indexed_at = excluded.indexed_at",
        (track_rowid, audio_vec, feature_vec, audio_model, lyrics_hash, at),
    )
    conn.commit()


def update_lyrics_hash(
    conn: sqlite3.Connection,
    track_rowid: int,
    *,
    lyrics_hash: str | None,
    now: int | None = None,
) -> None:
    """Refresh only the lyrics fingerprint of an already-embedded track, so a
    manual lyrics edit is noticed once and lyric-tag staleness can key on it."""
    at = now if now is not None else _now()
    conn.execute(
        "UPDATE embeddings SET lyrics_hash = ?, indexed_at = ?"
        " WHERE track_rowid = ?",
        (lyrics_hash, at, track_rowid),
    )
    conn.commit()


def coverage(conn: sqlite3.Connection) -> dict:
    """Tracks embedded vs. tracks playable, for the health endpoint."""
    row = conn.execute(
        "SELECT COUNT(*) AS playable FROM tracks WHERE status = 'done'"
    ).fetchone()
    embedded = conn.execute(
        "SELECT COUNT(*) FROM embeddings WHERE audio_vec IS NOT NULL"
    ).fetchone()[0]
    return {"playable": row["playable"], "embedded": embedded}


def vectors(conn: sqlite3.Connection) -> dict[int, dict]:
    """Every stored embedding, unpacked, keyed by track. The recommender
    holds the whole set in memory -- at library scale this is a couple of
    megabytes and a handful of small dot products, which is why there is no
    ANN index anywhere in this system.

    Only playable tracks. A track whose file is missing still appears in the
    library and in search (design principle 3), but recommending one would be
    offering something that cannot be played.
    """
    # Imported here rather than at module level: `muse.embed` imports this
    # module, so the reverse edge can only be taken once both are loaded.
    # `library/content.py` takes the same edge at module level and gets away
    # with it because nothing in `muse.embed` imports *it*.
    from ..embed import unpack_vector

    rows = conn.execute(
        "SELECT e.track_rowid, e.audio_vec, e.feature_vec"
        "  FROM embeddings e JOIN tracks t USING (track_rowid)"
        " WHERE t.status = 'done'"
        " ORDER BY e.track_rowid"
    ).fetchall()
    out: dict[int, dict] = {}
    for row in rows:
        out[row["track_rowid"]] = {
            "audio_vec": unpack_vector(row["audio_vec"]),
            "feature_vec": unpack_vector(row["feature_vec"]),
        }
    return out


_energy_cache: dict = {}


def clear_energy_cache() -> None:
    _energy_cache.clear()


def audio_energy(conn: sqlite3.Connection) -> dict[int, float]:
    """CLAP zero-shot energy on [-1, 1] per playable track of the current
    audio model. Empty until both energy prompt vectors are stored."""
    from .. import energy
    from ..embed import unpack_vector  # same edge as `vectors`
    from . import prompts, queries

    model = prompts.current_audio_model(conn)
    if model is None:
        return {}
    calm = prompts.get(conn, model, energy.CALM)
    energetic = prompts.get(conn, model, energy.ENERGETIC)
    if calm is None or energetic is None:
        return {}
    database = conn.execute("PRAGMA database_list").fetchone()[2]
    key = (database, queries.vector_fingerprint(conn), prompts.stamp(conn), model)
    cached = _energy_cache.get("entry")
    if cached is not None and cached[0] == key:
        return cached[1]
    rows = conn.execute(
        "SELECT e.track_rowid, e.audio_vec"
        "  FROM embeddings e JOIN tracks t USING (track_rowid)"
        " WHERE t.status = 'done' AND e.audio_vec IS NOT NULL AND e.audio_model = ?"
        " ORDER BY e.track_rowid", (model,)
    ).fetchall()
    raw = {row["track_rowid"]: energy.raw_energy(unpack_vector(row["audio_vec"]), calm, energetic)
           for row in rows}
    result = energy.to_unit(raw)
    _energy_cache["entry"] = (key, result)
    return result
