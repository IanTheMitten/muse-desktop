"""Audio features, and the mood bands the dashboard is built out of.

Two jobs. First, materialising `muse.features` — the read-only Spotify mirror —
into `track_features`, once per track, ever. Second, the only interpretation
this system performs on those numbers: four bands over valence and energy.

**Why bands and not a mood model.** Valence is "musical positiveness" and
energy is intensity. Two axes, split at the middle, give four quadrants that a
listener can actually predict from the name. The names are deliberately about
the *sound* — bright, warm, low, intense — and not about the listener. A player
that told you an album was "sad" would be claiming to know something it does
not.

The split is at 0.5 on both axes because the axes are defined on 0..1 and any
other number would be a taste dressed up as a threshold.
"""

import sqlite3
import time
from dataclasses import dataclass

from ..features import FeatureLookup
from .queries import TRACK_FIELDS, _track

# Order matters: it is the order the bands appear on the dashboard, and it runs
# bright to dark rather than alphabetically.
BANDS = ("bright", "warm", "low", "intense")

MID = 0.5

BAND_LABELS = {
    "bright": "Bright",
    "warm": "Warm",
    "low": "Low",
    "intense": "Intense",
}

# Shown under the label. Describes the sound, never the listener.
BAND_NOTES = {
    "bright": "up and open",
    "warm": "easy, unhurried",
    "low": "quiet and heavy",
    "intense": "dark, driving",
}

BAND_SQL = {
    "bright": f"valence >= {MID} AND energy >= {MID}",
    "warm": f"valence >= {MID} AND energy < {MID}",
    "low": f"valence < {MID} AND energy < {MID}",
    "intense": f"valence < {MID} AND energy >= {MID}",
}

# A mix is a queue, not a playlist, and a queue nobody will reach the end of is
# just a long list. Sixty tracks is roughly four hours.
MIX_LIMIT = 60


def band_of(valence: float | None, energy: float | None) -> str | None:
    """The band a pair of measurements falls in, or None when unmeasured.

    Kept in step with `BAND_SQL` by a test that walks a grid of both.
    """
    if valence is None or energy is None:
        return None
    if valence >= MID:
        return "bright" if energy >= MID else "warm"
    return "intense" if energy >= MID else "low"


@dataclass
class FeatureSync:
    asked: int = 0
    stored: int = 0
    without: int = 0


def sync_features(conn: sqlite3.Connection, lookup: FeatureLookup) -> FeatureSync:
    """Copy the mirror's numbers for any track that has not been asked about.

    Idempotent and cheap to run at every startup: the second run asks about
    nothing, because a track the mirror had no answer for still gets a row.
    """
    # Two populations: never asked, and asked before migration 005 existed.
    #
    # The second is not hypothetical. 005 added speechiness, liveness, key,
    # and time_signature to a table this function had already filled, and
    # because the skip test was "has a row at all", those four stayed NULL
    # on every existing track forever -- which failed the embed batch on the
    # NOT NULL bound and keeps the feature leg null. A migration added
    # columns and nothing went back for them.
    #
    # The re-ask predicate has to be narrow, or every startup re-asks about
    # the tracks the mirror genuinely has nothing for. An *old* column
    # present with *all four* new ones absent is the precise signature of a
    # pre-005 write: the mirror stores every field together or none, so a
    # track it answered for cannot have valence without time_signature.
    rows = conn.execute(
        "SELECT track_rowid, track_id FROM tracks"
        " WHERE track_id IS NOT NULL"
        "   AND (track_rowid NOT IN (SELECT track_rowid FROM track_features)"
        "        OR track_rowid IN ("
        "             SELECT track_rowid FROM track_features"
        "              WHERE valence IS NOT NULL"
        "                AND speechiness IS NULL AND liveness IS NULL"
        "                AND key IS NULL AND time_signature IS NULL))"
    ).fetchall()
    if not rows:
        return FeatureSync()

    by_id = {row["track_id"]: row["track_rowid"] for row in rows}
    found = lookup.features(list(by_id))
    now = int(time.time())

    result = FeatureSync(asked=len(by_id))
    for track_id, track_rowid in by_id.items():
        measured = found.get(track_id)
        if measured is None:
            result.without += 1
        else:
            result.stored += 1
        conn.execute(
            "INSERT OR REPLACE INTO track_features (track_rowid, valence,"
            " energy, danceability, acousticness, instrumentalness, tempo,"
            " loudness, mode, speechiness, liveness, key, time_signature,"
            " checked_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                track_rowid,
                # `measured` is None for a track the mirror has nothing for,
                # and the row still gets written — with nothing in it.
                getattr(measured, "valence", None),
                getattr(measured, "energy", None),
                getattr(measured, "danceability", None),
                getattr(measured, "acousticness", None),
                getattr(measured, "instrumentalness", None),
                getattr(measured, "tempo", None),
                getattr(measured, "loudness", None),
                getattr(measured, "mode", None),
                getattr(measured, "speechiness", None),
                getattr(measured, "liveness", None),
                getattr(measured, "key", None),
                getattr(measured, "time_signature", None),
                now,
            ),
        )
    return result


def coverage(conn: sqlite3.Connection) -> dict:
    """How much of the playable library the mood surfaces actually speak for.

    The dashboard says this out loud rather than plotting 705 tracks under a
    heading that claims 723.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS playable,"
        "       SUM(f.valence IS NOT NULL) AS measured"
        "  FROM tracks t LEFT JOIN track_features f USING (track_rowid)"
        " WHERE t.status = 'done'"
    ).fetchone()
    return {"playable": row["playable"] or 0, "measured": row["measured"] or 0}


def band_summary(conn: sqlite3.Connection) -> list[dict]:
    """Every band, in order, whether or not the library has anything in it.

    A band with nothing in it is information — it says what this library is —
    so it is returned at zero rather than dropped.
    """
    summary = []
    for band in BANDS:
        row = conn.execute(
            "SELECT COUNT(*) AS tracks, COALESCE(SUM(t.duration_ms), 0) AS runtime_ms"
            "  FROM track_features f JOIN tracks t USING (track_rowid)"
            f" WHERE t.status = 'done' AND {BAND_SQL[band]}"
        ).fetchone()
        summary.append(
            {
                "key": band,
                "label": BAND_LABELS[band],
                "note": BAND_NOTES[band],
                "tracks": row["tracks"],
                "runtime_ms": row["runtime_ms"],
            }
        )
    return summary


def band_tracks(
    conn: sqlite3.Connection, band: str, limit: int = MIX_LIMIT
) -> list[dict]:
    """A mix: this band, shuffled, capped.

    Shuffled in SQL rather than held anywhere, which is what makes the mix
    ephemeral — asking again gives a different cut of the same band. Nothing is
    stored, so there is nothing to keep in step with the library.
    """
    if band not in BAND_SQL:
        return []
    rows = conn.execute(
        f"SELECT {TRACK_FIELDS} FROM tracks"
        "  JOIN track_features USING (track_rowid)"
        f" WHERE status = 'done' AND {BAND_SQL[band]}"
        " ORDER BY RANDOM() LIMIT ?",
        (limit,),
    ).fetchall()
    return [_track(row) for row in rows]


# How much each measurement counts toward "sounds like". Valence and energy
# carry the mood and are weighted for it; tempo is divided by 200 first so bpm
# sits on the same 0..1 scale as everything else rather than swamping it.
_DISTANCE = """
    ( 1.6 * (f.valence      - :valence)      * (f.valence      - :valence)
    + 1.6 * (f.energy       - :energy)       * (f.energy       - :energy)
    + 0.7 * (COALESCE(f.acousticness, 0) - :acousticness)
          * (COALESCE(f.acousticness, 0) - :acousticness)
    + 0.5 * (COALESCE(f.danceability, 0) - :danceability)
          * (COALESCE(f.danceability, 0) - :danceability)
    + 0.4 * (COALESCE(f.tempo, 0) / 200.0 - :tempo)
          * (COALESCE(f.tempo, 0) / 200.0 - :tempo) )
"""


def neighbours(
    conn: sqlite3.Connection, track_rowid: int, limit: int = 8
) -> list[dict]:
    """The tracks that sound most like this one.

    Nearest neighbours in five dimensions over 723 rows — a full scan that
    costs less than a millisecond, and honest in a way a collaborative model
    cannot be with eighteen plays on the clock.

    One track per album: ten cuts off the record already playing is not a
    recommendation, it is the same record.
    """
    seed = conn.execute(
        "SELECT t.album_rowid, f.* FROM track_features f"
        "  JOIN tracks t USING (track_rowid)"
        " WHERE f.track_rowid = ? AND f.valence IS NOT NULL",
        (track_rowid,),
    ).fetchone()
    if seed is None:
        return []

    rows = conn.execute(
        f"SELECT {TRACK_FIELDS}, {_DISTANCE} AS distance"
        "  FROM tracks JOIN track_features f USING (track_rowid)"
        " WHERE status = 'done' AND f.valence IS NOT NULL"
        "   AND tracks.track_rowid != :seed"
        " ORDER BY distance LIMIT :fetch",
        {
            "seed": track_rowid,
            "valence": seed["valence"],
            "energy": seed["energy"],
            "acousticness": seed["acousticness"] or 0,
            "danceability": seed["danceability"] or 0,
            "tempo": (seed["tempo"] or 0) / 200.0,
            # Room to drop same-album duplicates without a second query.
            "fetch": limit * 6,
        },
    ).fetchall()

    picked: list[dict] = []
    seen_albums = {seed["album_rowid"]}
    for row in rows:
        if row["album_rowid"] in seen_albums:
            continue
        seen_albums.add(row["album_rowid"])
        picked.append(_track(row))
        if len(picked) == limit:
            break
    return picked


# The embed batch's 13-dim feature_vec (ADR 0003 / ticket 01): the 12
# measured columns this module owns, plus `tracks.duration_ms`, in the exact
# order the PRD specifies. Order matters -- it is baked into every stored
# `feature_vec` blob.
FEATURE_VEC_COLUMNS = (
    "danceability", "energy", "loudness", "speechiness", "acousticness",
    "instrumentalness", "liveness", "valence", "tempo", "key", "mode",
    "time_signature", "duration_ms",
)


def raw_feature_values(
    conn: sqlite3.Connection, track_rowid: int
) -> tuple[float, ...] | None:
    """The 13 raw numbers `feature_vec` normalizes, or None when any of them
    is missing.

    None covers a track never measured (no `track_features` row) and one
    Spotify answered `null_response` for (a row whose measurements are all
    NULL) identically -- both mean "nothing to build a feature_vec from",
    same as `feature_vec` being null downstream.
    """
    row = conn.execute(
        "SELECT f.danceability, f.energy, f.loudness, f.speechiness,"
        "       f.acousticness, f.instrumentalness, f.liveness, f.valence,"
        "       f.tempo, f.key, f.mode, f.time_signature, t.duration_ms"
        "  FROM track_features f JOIN tracks t USING (track_rowid)"
        " WHERE f.track_rowid = ?",
        (track_rowid,),
    ).fetchone()
    if row is None:
        return None
    values = tuple(row)
    return None if any(value is None for value in values) else values


def measured_bounds(conn: sqlite3.Connection) -> dict[str, tuple[float, float]]:
    """Min/max per `feature_vec` column across every fully-measured track,
    computed live. `frozen_bounds` is what the embed batch actually
    normalizes against; this is the one-time capture underneath it."""
    aggregates = ", ".join(
        f"MIN(f.{column}), MAX(f.{column})"
        if column != "duration_ms" else "MIN(t.duration_ms), MAX(t.duration_ms)"
        for column in FEATURE_VEC_COLUMNS
    )
    row = conn.execute(
        f"SELECT {aggregates}"
        "  FROM track_features f JOIN tracks t USING (track_rowid)"
        " WHERE f.valence IS NOT NULL"
    ).fetchone()
    # Every aggregate, not just the first. The original guard checked
    # `row[0]` -- valence's MIN -- and took a populated valence to mean the
    # whole 13-column reading was there. On the real library it is not:
    # speechiness, liveness, key, and time_signature are NULL for every row
    # the features sync has written, so the guard passed and the insert put
    # NULL into a NOT NULL column, failing the entire embed batch before it
    # embedded a single track.
    #
    # Returning {} keeps the leg unfrozen, which is what this function's
    # docstring already promised. Freezing the partial set instead would be
    # worse than the crash: `normalize_feature_vector` requires every column,
    # so the feature leg would be permanently null *and* the missing columns
    # could never be captured once their data arrived, because bounds freeze
    # once (PRD O3).
    values = tuple(row) if row is not None else ()
    if not values or any(value is None for value in values):
        return {}
    return {
        column: (values[2 * i], values[2 * i + 1])
        for i, column in enumerate(FEATURE_VEC_COLUMNS)
    }


def frozen_bounds(conn: sqlite3.Connection) -> dict[str, tuple[float, float]]:
    """The bounds `feature_vec` is normalized against, captured once.

    Empty until the first track anywhere has a full 13-value reading -- at
    that point the current spread across the library is frozen and stored,
    and every `feature_vec` computed from then on, including for tracks
    added later, is scaled against that same frozen range rather than one
    that quietly shifts under already-stored vectors.
    """
    rows = conn.execute("SELECT column, min_value, max_value FROM feature_norm_bounds")
    existing = {row["column"]: (row["min_value"], row["max_value"]) for row in rows}
    if existing:
        return existing

    bounds = measured_bounds(conn)
    if not bounds:
        return {}

    # One statement, all 13 rows: a single execute() is atomic on its own,
    # under SharedConnection's per-call lock, with no window between rows
    # for a process kill to leave a partial set behind, or for another
    # writer's own BEGIN/COMMIT bracket on this same shared connection (the
    # art backfill's `record_cover`, running concurrently on its own worker
    # thread) to land in the middle of this one and collide.
    items = list(bounds.items())
    placeholders = ", ".join(["(?, ?, ?)"] * len(items))
    values = [value for column, bound in items for value in (column, *bound)]
    conn.execute(
        f"INSERT INTO feature_norm_bounds (column, min_value, max_value)"
        f" VALUES {placeholders}",
        values,
    )
    return bounds


def map_points(conn: sqlite3.Connection) -> list[dict]:
    """Every measured track as one point on the valence/energy plane.

    The whole library at once, which is the point: it is a picture of what is
    owned, not a summary of it. 705 points is ~20 KB of JSON and one SVG.
    """
    rows = conn.execute(
        "SELECT f.track_rowid, f.valence, f.energy FROM track_features f"
        "  JOIN tracks t USING (track_rowid)"
        " WHERE t.status = 'done' AND f.valence IS NOT NULL"
        " ORDER BY f.track_rowid"
    ).fetchall()
    return [
        {
            "track_rowid": row["track_rowid"],
            # Three decimals is finer than one pixel on any screen this renders
            # at, and it takes a fifth off the payload.
            "valence": round(row["valence"], 3),
            "energy": round(row["energy"], 3),
            "band": band_of(row["valence"], row["energy"]),
        }
        for row in rows
    ]
