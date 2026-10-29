"""The mood plane, measured by this app rather than by Spotify.

`features.py` plots the same four bands from `track_features.valence/energy`,
which is metadata the 29 GB audio-features mirror happens to carry. This module
plots them from `embeddings.emotion_vec`, which is the fused audio+lyrics
valence/arousal point the embedding batch already computes for the recommender's
emotion leg (ADR 0003). Three things follow from the swap:

* **Coverage goes up, not down.** The emotion leg covers every track the batch
  has seen, including anything the mirror never had a row for.
* **The mirror stops being load-bearing for a surface.** A missing mirror was
  always allowed to cost quality, never availability; with the plane on our own
  vectors it costs neither.
* **The axis is honest.** It is arousal as a model measured it, not Spotify's
  "energy", which is a blend of several things.

`features.py` is not deleted and not deprecated: it still owns the Spotify
feature leg of the recommender. This module owns the *picture*.

## Why the split is at the median

The models do not emit a centred plane. On a real library the raw output sits
at 64% intense with four warm tracks, because the wall these covers sit against
is not the middle of the model's range. Splitting at each axis's own median
puts a quarter of the library either side of both lines and turns the map back
into something a listener can aim at.

The cost is stated rather than hidden: a band is now relative to the library, so
a track can change band as the library grows. That is the right trade for a
*chooser* — the question it answers is "which quarter of what I own is this",
not "what is this track's absolute arousal" — but it means the medians are
library state, and `medians()` recomputes them from whatever is measured now.
"""

import sqlite3

from .features import BAND_LABELS, BAND_NOTES, BANDS
from .queries import TRACK_FIELDS, _track


def _points(conn: sqlite3.Connection) -> list[tuple[int, float, float]]:
    """Every playable track that has an emotion vector, unpacked.

    Playable only, for the same reason the recommender's vectors are: a track
    whose file is missing still belongs in the library and in search (design
    principle 3), but putting it on a map whose quadrants are play buttons
    would be offering something that cannot be played.
    """
    from ..embed import unpack_vector

    rows = conn.execute(
        "SELECT e.track_rowid, e.emotion_vec"
        "  FROM embeddings e JOIN tracks t USING (track_rowid)"
        " WHERE t.status = 'done' AND e.emotion_vec IS NOT NULL"
        " ORDER BY e.track_rowid"
    ).fetchall()
    out = []
    for row in rows:
        vec = unpack_vector(row["emotion_vec"])
        if vec and len(vec) == 2:
            out.append((row["track_rowid"], float(vec[0]), float(vec[1])))
    return out


def _median(values: list[float]) -> float:
    """The middle value, averaging the two middles on an even count.

    Written out rather than imported from `statistics` so the empty case is a
    documented 0.0 instead of an exception on a library with nothing measured.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def medians(conn: sqlite3.Connection) -> dict:
    """Where this library's own centre sits, and how many tracks say so.

    Recomputed per call rather than stored. It is one pass over a few hundred
    pairs already being read for the map itself, and a stored median is a value
    that can silently fall out of step with the library it describes.
    """
    points = _points(conn)
    return {
        "valence": round(_median([valence for _, valence, _ in points]), 4),
        "arousal": round(_median([arousal for _, _, arousal in points]), 4),
        "measured": len(points),
    }


def band_of(valence: float, arousal: float, centre: dict) -> str:
    """Which quadrant a point falls in, relative to this library's centre.

    The four names are the same ones `features.py` uses and mean the same
    thing, so nothing downstream has to know which module drew the map.
    """
    if arousal >= centre["arousal"]:
        return "bright" if valence >= centre["valence"] else "intense"
    return "warm" if valence >= centre["valence"] else "low"


def map_points(conn: sqlite3.Connection) -> list[dict]:
    """The whole measured library as points, with the centre they are read
    against.

    Coordinates are returned raw, in the models' own [-1, 1], and the centre is
    returned beside them: the client places the crosshair, which keeps this
    endpoint honest about what was measured rather than pre-normalising the
    numbers into something that cannot be checked.
    """
    centre = medians(conn)
    return [
        {
            "track_rowid": rowid,
            # Three decimals is finer than a pixel at any size this renders,
            # and it takes a fifth off the payload.
            "valence": round(valence, 3),
            "arousal": round(arousal, 3),
            "band": band_of(valence, arousal, centre),
        }
        for rowid, valence, arousal in _points(conn)
    ]


def band_summary(conn: sqlite3.Connection) -> list[dict]:
    """Every band, in order, whether or not the library has anything in it.

    A band at zero is information — it says what this library is — so it is
    returned rather than dropped. Runtime comes from the tracks themselves in
    one grouped query rather than four.
    """
    centre = medians(conn)
    counts = {band: 0 for band in BANDS}
    runtimes = {band: 0 for band in BANDS}

    durations = dict(
        conn.execute(
            "SELECT track_rowid, COALESCE(duration_ms, 0) FROM tracks"
            " WHERE status = 'done'"
        ).fetchall()
    )
    for rowid, valence, arousal in _points(conn):
        band = band_of(valence, arousal, centre)
        counts[band] += 1
        runtimes[band] += durations.get(rowid, 0)

    return [
        {
            "key": band,
            "label": BAND_LABELS[band],
            "note": BAND_NOTES[band],
            "tracks": counts[band],
            "runtime_ms": runtimes[band],
        }
        for band in BANDS
    ]


def band_tracks(conn: sqlite3.Connection, band: str, limit: int) -> list[dict]:
    """A mix: this band, shuffled, capped.

    The shuffle happens here rather than in SQL because the band membership is
    not a SQL predicate any more — it depends on a median over the whole set —
    so the rowids are selected first and the tracks fetched second.
    """
    if band not in BANDS:
        return []
    import random

    centre = medians(conn)
    rowids = [
        rowid
        for rowid, valence, arousal in _points(conn)
        if band_of(valence, arousal, centre) == band
    ]
    random.shuffle(rowids)
    chosen = rowids[:limit]
    if not chosen:
        return []

    placeholders = ",".join("?" * len(chosen))
    rows = conn.execute(
        f"SELECT {TRACK_FIELDS} FROM tracks"
        f" WHERE track_rowid IN ({placeholders}) AND status = 'done'",
        chosen,
    ).fetchall()
    by_rowid = {row["track_rowid"]: _track(row) for row in rows}
    return [by_rowid[rowid] for rowid in chosen if rowid in by_rowid]


def coverage(conn: sqlite3.Connection) -> dict:
    """How much of the playable library the plane can speak for.

    Said out loud on the surface rather than quietly plotting fewer points than
    the library has.
    """
    playable = conn.execute(
        "SELECT COUNT(*) FROM tracks WHERE status = 'done'"
    ).fetchone()[0]
    return {"playable": playable, "measured": len(_points(conn))}
