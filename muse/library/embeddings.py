"""Where content embeddings are recorded, and the embed batch's work queue.

One row per track, written once and then only ever replaced -- never a second
table for "has this been embedded", because `embeddings.audio_vec IS NULL`
and `embeddings.audio_va IS NULL` already answer that: a track with no row
here, or a row missing either, has not been fully embedded, and that is the
whole work queue. A batch killed halfway through restarts into exactly this
set.

A NULL `feature_vec` or `lyrics_vec` on an otherwise-complete row is not
missing work -- a null_response feature reading and an instrumental with no
lyrics file are real answers, not tracks left behind. `audio_vec` and
`audio_va` gate the queue because both are computed from the audio file
directly and every `done` track has one; `feature_vec` and `lyrics_vec` do
not gate it because they can legitimately end up NULL.
"""

import sqlite3
import time



def _now() -> int:
    return int(time.time())


def pending(conn: sqlite3.Connection) -> list[dict]:
    """Playable tracks not yet fully embedded: the resumable batch's work
    queue.

    Covers a track with no `embeddings` row at all, one left behind by a
    killed run with `audio_vec` still NULL, and one that has content vectors
    (ticket 01) but no `audio_va` yet (ticket 02, or any caller of `upsert`
    that didn't pass the emotion columns) -- `audio_va` is only ever computed
    here, in a full embed, never by the lyrics-changed pass, which only
    fuses `emotion_vec` from an `audio_va` that already exists. Without this,
    such a row would have no path back into the emotion leg at all.

    The `audio_va` clause is qualified by `audio_va_model IS NULL`, which is
    the difference between "never asked" and "asked, and the model was
    gone" -- the same distinction `track_features` draws for a track the
    mirror had no numbers for. Unqualified, a permanently unavailable
    audio-VA model leaves every track with a NULL `audio_va`, so the whole
    library qualifies as pending and re-embeds on every restart: ~50 minutes
    of CLAP that was already correct, burned each time.

    The cost of the qualification is that restoring the model does not
    re-queue those tracks by itself. That is deliberate and matches how
    frozen feature bounds behave (PRD O3): clearing `audio_va_model` is an
    explicit reindex, not something a restart should decide to do.
    """
    rows = conn.execute(
        "SELECT t.track_rowid, t.file_path, t.lyrics_path"
        "  FROM tracks t LEFT JOIN embeddings e USING (track_rowid)"
        " WHERE t.status = 'done'"
        "   AND (e.track_rowid IS NULL OR e.audio_vec IS NULL"
        "        OR (e.audio_va IS NULL AND e.audio_va_model IS NULL))"
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
    lyrics_vec: bytes | None,
    audio_model: str | None,
    lyrics_model: str | None,
    lyrics_hash: str | None,
    audio_va: bytes | None = None,
    lyrics_va: bytes | None = None,
    emotion_vec: bytes | None = None,
    audio_va_model: str | None = None,
    lyrics_va_model: str | None = None,
    now: int | None = None,
) -> None:
    """Write a full embed result for one track, replacing whatever was there.

    Every field is given explicitly, never merged with what is already
    stored -- a full embed always recomputes every leg, so there is nothing
    to preserve. A lyrics-only re-embed goes through `update_lyrics` instead,
    which is what preserves the rest. The emotion columns default to None so
    a caller that only has content vectors (most existing tests) does not
    have to spell out five more Nones just to write a row.
    """
    at = now if now is not None else _now()
    conn.execute(
        "INSERT INTO embeddings (track_rowid, audio_vec, feature_vec,"
        " lyrics_vec, audio_model, lyrics_model, lyrics_hash, audio_va,"
        " lyrics_va, emotion_vec, audio_va_model, lyrics_va_model,"
        " indexed_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(track_rowid) DO UPDATE SET"
        "   audio_vec = excluded.audio_vec,"
        "   feature_vec = excluded.feature_vec,"
        "   lyrics_vec = excluded.lyrics_vec,"
        "   audio_model = excluded.audio_model,"
        "   lyrics_model = excluded.lyrics_model,"
        "   lyrics_hash = excluded.lyrics_hash,"
        "   audio_va = excluded.audio_va,"
        "   lyrics_va = excluded.lyrics_va,"
        "   emotion_vec = excluded.emotion_vec,"
        "   audio_va_model = excluded.audio_va_model,"
        "   lyrics_va_model = excluded.lyrics_va_model,"
        "   indexed_at = excluded.indexed_at",
        (
            track_rowid, audio_vec, feature_vec, lyrics_vec, audio_model,
            lyrics_model, lyrics_hash, audio_va, lyrics_va, emotion_vec,
            audio_va_model, lyrics_va_model, at,
        ),
    )
    conn.commit()


def update_lyrics(
    conn: sqlite3.Connection,
    track_rowid: int,
    *,
    lyrics_vec: bytes | None,
    lyrics_model: str | None,
    lyrics_hash: str | None,
    lyrics_va: bytes | None,
    emotion_vec: bytes | None,
    lyrics_va_model: str | None,
    now: int | None = None,
) -> None:
    """Re-embed only the lyrics-derived legs of an already-embedded track.

    `audio_vec`, `feature_vec`, and `audio_va` are untouched -- the reason
    `lyrics_hash` exists at all is so a manual lyrics edit triggers exactly
    this and nothing more, not a full rescan of a track whose audio has not
    changed. `emotion_vec` is lyrics-derived too (it is the fusion of
    audio_va and lyrics_va), so it is recomputed and written here, by the
    caller, using the audio_va already on the row.
    """
    at = now if now is not None else _now()
    conn.execute(
        "UPDATE embeddings SET lyrics_vec = ?, lyrics_model = ?,"
        " lyrics_hash = ?, lyrics_va = ?, emotion_vec = ?,"
        " lyrics_va_model = ?, indexed_at = ? WHERE track_rowid = ?",
        (
            lyrics_vec, lyrics_model, lyrics_hash, lyrics_va, emotion_vec,
            lyrics_va_model, at, track_rowid,
        ),
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


def emotion_coverage(conn: sqlite3.Connection) -> dict:
    """Tracks with a computed emotion leg vs. tracks playable, for the
    health endpoint -- same shape as `coverage`, gated on `emotion_vec`
    instead of `audio_vec`, since a track can be content-embedded before its
    emotion leg lands (e.g. mid-migration, before a fresh batch run)."""
    row = conn.execute(
        "SELECT COUNT(*) AS playable FROM tracks WHERE status = 'done'"
    ).fetchone()
    embedded = conn.execute(
        "SELECT COUNT(*) FROM embeddings WHERE emotion_vec IS NOT NULL"
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

    `emotion_vec` comes back as a `(valence, arousal)` tuple rather than a
    list, because `reco.emotion_sim` measures distance on a bounded plane and
    the pair is a point, not a vector of arbitrary length.
    """
    # Imported here rather than at module level: `muse.embed` imports this
    # module, so the reverse edge can only be taken once both are loaded.
    # `library/content.py` takes the same edge at module level and gets away
    # with it because nothing in `muse.embed` imports *it*.
    from ..embed import unpack_vector

    rows = conn.execute(
        "SELECT e.track_rowid, e.audio_vec, e.feature_vec, e.lyrics_vec,"
        "       e.emotion_vec"
        "  FROM embeddings e JOIN tracks t USING (track_rowid)"
        " WHERE t.status = 'done'"
        " ORDER BY e.track_rowid"
    ).fetchall()
    out: dict[int, dict] = {}
    for row in rows:
        emotion = unpack_vector(row["emotion_vec"])
        out[row["track_rowid"]] = {
            "audio_vec": unpack_vector(row["audio_vec"]),
            "feature_vec": unpack_vector(row["feature_vec"]),
            "lyrics_vec": unpack_vector(row["lyrics_vec"]),
            "emotion_vec": tuple(emotion) if emotion else None,
        }
    return out


def audio_arousal(conn: sqlite3.Connection) -> dict[int, float]:
    """Stored audio arousal (the second component of `audio_va`) per playable
    track. A track with no `audio_va` is absent, not zero."""
    from ..embed import unpack_vector  # same edge as `vectors`

    rows = conn.execute(
        "SELECT e.track_rowid, e.audio_va"
        "  FROM embeddings e JOIN tracks t USING (track_rowid)"
        " WHERE t.status = 'done' AND e.audio_va IS NOT NULL"
        " ORDER BY e.track_rowid"
    ).fetchall()
    return {row["track_rowid"]: unpack_vector(row["audio_va"])[1] for row in rows}
