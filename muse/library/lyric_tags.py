"""Storage for lyric tags: `lyric_tag_sets` and `lyric_tags` (migration 17).

`docs/specs/2026-10-12-lyric-tag-store.md` D1-D4. A *tag set* is one song's
answers at one taxonomy version; its rows in `lyric_tags` are the raw
probabilities, `{segment: {group: {label: prob}}}` once read. The taxonomy,
segmentation and every derived score live in the pure `muse/lyric_tags.py`;
this module only stores, links and finds what needs (re-)tagging.

A set with `track_rowid` NULL was tagged from prefetched lyrics before the
song's audio landed. It waits, keyed by `recording_mbid`, until the batch
links it (`link_candidates` + `attach`) or drops it for different lyrics.

Staleness: a set stamps
`embeddings.lyrics_hash` when written, and a repaired lyric changes that
hash and puts the track back in `pending`.
"""

import hashlib
import sqlite3
import time


def _now() -> int:
    return int(time.time())


def write_set(
    conn: sqlite3.Connection,
    *,
    track_rowid: int | None,
    recording_mbid: str | None,
    state: str,
    text_hash: str,
    lyrics_hash: str | None,
    model: str,
    tags: dict,
    taxonomy_version: int,
    now: int | None = None,
) -> int:
    """Write one song's tag set and return its id. A linked set replaces
    whatever set the track already had (its tags go with it, by cascade)."""
    if track_rowid is not None:
        conn.execute("DELETE FROM lyric_tag_sets WHERE track_rowid = ?", (track_rowid,))
    cursor = conn.execute(
        "INSERT INTO lyric_tag_sets (track_rowid, recording_mbid, state, text_hash,"
        " lyrics_hash, taxonomy_version, model, written_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (track_rowid, recording_mbid, state, text_hash, lyrics_hash,
         taxonomy_version, model, now if now is not None else _now()),
    )
    set_id = int(cursor.lastrowid)
    conn.executemany(
        "INSERT INTO lyric_tags (set_id, segment, grp, label, prob) VALUES (?, ?, ?, ?, ?)",
        [(set_id, segment, grp, label, float(prob))
         for segment, groups in tags.items()
         for grp, probs in groups.items()
         for label, prob in probs.items()],
    )
    conn.commit()
    return set_id


def read(conn: sqlite3.Connection, track_rowids: list[int] | None = None) -> dict[int, dict]:
    """`{track_rowid: {segment: {group: {label: prob}}}}` for linked sets.
    A `no_lines` set reads as `{}`; a track with no set is absent."""
    where, args = "", []
    if track_rowids is not None:
        if not track_rowids:
            return {}
        where = f" WHERE s.track_rowid IN ({','.join('?' * len(track_rowids))})"
        args = list(track_rowids)
    else:
        where = " WHERE s.track_rowid IS NOT NULL"
    out: dict[int, dict] = {}
    for row in conn.execute(
        "SELECT s.track_rowid, s.state FROM lyric_tag_sets s" + where, args
    ):
        out[row["track_rowid"]] = {}
    for row in conn.execute(
        "SELECT s.track_rowid, t.segment, t.grp, t.label, t.prob"
        "  FROM lyric_tag_sets s JOIN lyric_tags t ON t.set_id = s.id" + where, args
    ):
        out[row["track_rowid"]].setdefault(row["segment"], {}).setdefault(row["grp"], {})[
            row["label"]] = row["prob"]
    return out


def pending(conn: sqlite3.Connection, taxonomy_version: int) -> list[int]:
    """Playable, non-instrumental tracks with lyrics whose tags are missing,
    stale (lyrics repaired since: `IS NOT` so a NULL hash compares), or from
    an older taxonomy -- the batch's work queue (spec D3)."""
    rows = conn.execute(
        "SELECT t.track_rowid FROM tracks t"
        "  LEFT JOIN lyric_tag_sets s ON s.track_rowid = t.track_rowid"
        "  LEFT JOIN embeddings e ON e.track_rowid = t.track_rowid"
        " WHERE t.status = 'done' AND t.lyrics_path IS NOT NULL"
        "   AND COALESCE(t.is_instrumental, 0) = 0"
        "   AND (s.id IS NULL OR s.lyrics_hash IS NOT e.lyrics_hash"
        "        OR s.taxonomy_version < ?)"
        " ORDER BY t.track_rowid",
        (taxonomy_version,),
    ).fetchall()
    return [row["track_rowid"] for row in rows]


def link_candidates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Unlinked sets whose song has now landed: `set_id`, `track_rowid`,
    `text_hash`, `lyrics_path`, `e_lyrics_hash`. A track that already has
    its own set is not a candidate, and when two tracks share a
    recording_mbid the lowest rowid is the one offered (spec D4)."""
    return conn.execute(
        "SELECT s.id AS set_id, t.track_rowid, s.text_hash, t.lyrics_path,"
        "       e.lyrics_hash AS e_lyrics_hash"
        "  FROM lyric_tag_sets s"
        "  JOIN tracks t ON t.recording_mbid = s.recording_mbid"
        "  LEFT JOIN embeddings e ON e.track_rowid = t.track_rowid"
        " WHERE s.track_rowid IS NULL AND t.status = 'done' AND t.lyrics_path IS NOT NULL"
        "   AND NOT EXISTS (SELECT 1 FROM lyric_tag_sets x WHERE x.track_rowid = t.track_rowid)"
        "   AND t.track_rowid = (SELECT MIN(t2.track_rowid) FROM tracks t2"
        "                         WHERE t2.recording_mbid = s.recording_mbid"
        "                           AND t2.status = 'done' AND t2.lyrics_path IS NOT NULL)"
        " ORDER BY s.id"
    ).fetchall()


def attach(conn: sqlite3.Connection, set_id: int, track_rowid: int,
           lyrics_hash: str | None) -> None:
    """Link an unlinked set to its track, stamping the track's current
    `embeddings.lyrics_hash` (the comparison target, as themes do)."""
    conn.execute(
        "UPDATE lyric_tag_sets SET track_rowid = ?, lyrics_hash = ? WHERE id = ?",
        (track_rowid, lyrics_hash, set_id),
    )
    conn.commit()


def delete_set(conn: sqlite3.Connection, set_id: int) -> None:
    conn.execute("DELETE FROM lyric_tag_sets WHERE id = ?", (set_id,))
    conn.commit()


def find_set(conn: sqlite3.Connection, *, track_rowid: int | None = None,
             recording_mbid: str | None = None) -> sqlite3.Row | None:
    """A track's set, or else the first set (linked or not) for an mbid."""
    if track_rowid is not None:
        return conn.execute(
            "SELECT * FROM lyric_tag_sets WHERE track_rowid = ?", (track_rowid,)
        ).fetchone()
    return conn.execute(
        "SELECT * FROM lyric_tag_sets WHERE recording_mbid = ? ORDER BY id", (recording_mbid,)
    ).fetchone()


def text_hashes(conn: sqlite3.Connection) -> dict[int, str]:
    """`{track_rowid: text_hash}` for linked `tagged` sets -- the lyrics half
    of the arc engine's version key. `no_lines` sets are left out: their
    hash is of empty input, so every one of them would look like one song."""
    return {
        row["track_rowid"]: row["text_hash"]
        for row in conn.execute(
            "SELECT track_rowid, text_hash FROM lyric_tag_sets"
            " WHERE track_rowid IS NOT NULL AND state = 'tagged'"
        )
    }


def stamp(conn: sqlite3.Connection) -> tuple:
    """A value that changes whenever a *linked* set in `lyric_tag_sets` is
    written (what the arc tag index reads; unlinked corpus sets are left out
    so corpus writes don't invalidate its cache): the arc engine's tag cache
    key. `id` is reused when a rewrite deletes the
    newest row and inserts again, and `written_at` has one-second grain, so
    an aggregate can miss a rewrite; this fingerprints every linked set's row."""
    rows = conn.execute(
        "SELECT id, track_rowid, state, text_hash, model, taxonomy_version, written_at"
        "  FROM lyric_tag_sets WHERE track_rowid IS NOT NULL ORDER BY id"
    ).fetchall()
    digest = hashlib.sha1(repr([tuple(row) for row in rows]).encode()).hexdigest()
    return (len(rows), digest)
