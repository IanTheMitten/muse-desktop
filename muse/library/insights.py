"""Everything the dashboard shows, in one payload.

One request, because the dashboard is the first screen and four round trips
over a tailnet is four chances to look slow. The whole thing is a handful of
scans over 723 rows plus one 705-point list; it costs a few milliseconds.

The honesty rule this module exists to keep: **nothing here pretends to know
the listener.** With eighteen plays on record, "because you listened to X" is a
sentence the data cannot support. What it can support is what the library
sounds like, what has never been played, and what has not been played in a
long time — so those are what it returns. `top_artists` stays `None` until
there is enough history to rank, rather than showing a chart of three rows.
"""

import json
import sqlite3
import time
from datetime import datetime, timedelta

from . import emotion, features
from .queries import TRACK_FIELDS, _track, track as one_track

# Below this, a ranking is a list of whatever happened to be played first.
TOP_ARTISTS_MIN_PLAYS = 50

# How far back "not in a while" starts.
FORGOTTEN_AFTER_DAYS = 30

WEEK_DAYS = 7
NEVER_PLAYED_ALBUMS = 6

# Eight fills a strip on a laptop and scrolls on a phone. More is a longer
# scroll for a band whose whole job is the first two or three entries.
RECENT_ALBUMS = 8
FORGOTTEN_TRACKS = 8
SOUNDS_LIKE = 8
ON_REPEAT_DAYS = 7
ON_REPEAT_TRACKS = 10
RECENTLY_ADDED_ALBUMS = 8

# How far back to look for a seed. A listener who played ten unmeasured tracks
# in a row is not going to be helped by an eleventh guess.
SEED_LOOKBACK = 10

# Time actually heard. `reached_ms` is the furthest position reached, and
# scrubbing back and forth can push it past the track; a play can never have
# been longer than the track it was.
_HEARD = "MIN(p.reached_ms, p.duration_ms)"


def _resume(conn: sqlite3.Connection) -> dict | None:
    """Where the queue was left, whichever device left it there."""
    row = conn.execute(
        "SELECT queue, queue_index, position_ms FROM player_state WHERE id = 1"
    ).fetchone()
    if row is None:
        return None
    queue = json.loads(row["queue"])
    if not queue or not 0 <= row["queue_index"] < len(queue):
        return None
    track = one_track(conn, queue[row["queue_index"]])
    if track is None:
        return None
    return {
        "track": track,
        "position_ms": row["position_ms"],
        "queue_length": len(queue),
        "queue_index": row["queue_index"],
    }


def _seed(conn: sqlite3.Connection) -> dict | None:
    """The most recently played track that the mirror has measurements for."""
    rows = conn.execute(
        "SELECT DISTINCT track_rowid, MAX(started_at) AS at FROM plays"
        " GROUP BY track_rowid ORDER BY at DESC LIMIT ?",
        (SEED_LOOKBACK,),
    ).fetchall()
    for row in rows:
        measured = conn.execute(
            "SELECT 1 FROM track_features"
            " WHERE track_rowid = ? AND valence IS NOT NULL",
            (row["track_rowid"],),
        ).fetchone()
        if measured:
            return one_track(conn, row["track_rowid"])
    return None


def _recent_albums(conn: sqlite3.Connection) -> list[dict]:
    """Albums played most recently, newest first.

    "Carry on with what I was doing" is the most likely intent on open, so this
    is the first thing Home shows. By album rather than by track: what you
    return to is a record, and a row per track would fill the strip with six
    entries from the same sleeve.
    """
    rows = conn.execute(
        "SELECT t.album_rowid, t.album_name, t.artist_name,"
        "       COUNT(*)                    AS track_count,"
        "       SUM(t.duration_ms)          AS runtime_ms,"
        "       MAX(t.art_path IS NOT NULL) AS has_art,"
        "       MAX(p.started_at)           AS last_played"
        "  FROM plays p"
        "  JOIN tracks t ON t.track_rowid = p.track_rowid"
        " WHERE t.status = 'done' AND t.album_rowid IS NOT NULL"
        " GROUP BY t.album_rowid"
        " ORDER BY last_played DESC LIMIT ?",
        (RECENT_ALBUMS,),
    ).fetchall()
    return [{**dict(row), "has_art": bool(row["has_art"])} for row in rows]


def _never_played(conn: sqlite3.Connection) -> list[dict]:
    """Albums with no play against any of their tracks.

    Shuffled rather than ordered: with 87 albums and 18 plays this is very
    nearly the whole library, and any fixed order would show the same six
    albums forever.
    """
    rows = conn.execute(
        "SELECT album_rowid, album_name, artist_name,"
        "       COUNT(*)                  AS track_count,"
        "       SUM(duration_ms)          AS runtime_ms,"
        "       MAX(art_path IS NOT NULL) AS has_art"
        "  FROM tracks t"
        " WHERE status = 'done' AND album_rowid IS NOT NULL"
        "   AND NOT EXISTS (SELECT 1 FROM plays p"
        "                     JOIN tracks pt ON pt.track_rowid = p.track_rowid"
        "                    WHERE pt.album_rowid = t.album_rowid)"
        " GROUP BY album_rowid, album_name"
        " ORDER BY RANDOM() LIMIT ?",
        (NEVER_PLAYED_ALBUMS,),
    ).fetchall()
    return [{**dict(row), "has_art": bool(row["has_art"])} for row in rows]


def _forgotten(conn: sqlite3.Connection, now_ms: int) -> list[dict]:
    """Played once, and not since. Oldest first — the point is the gap."""
    cutoff = now_ms - FORGOTTEN_AFTER_DAYS * 86_400_000
    # The aggregate runs in a subquery so `plays.duration_ms` never shadows the
    # track's own — the two mean different things and TRACK_FIELDS wants the
    # track's.
    rows = conn.execute(
        f"SELECT {TRACK_FIELDS}, heard.last_played"
        "  FROM tracks JOIN (SELECT track_rowid, MAX(started_at) AS last_played"
        "                      FROM plays GROUP BY track_rowid) heard"
        "    USING (track_rowid)"
        " WHERE status = 'done' AND heard.last_played < ?"
        " ORDER BY heard.last_played LIMIT ?",
        (cutoff, FORGOTTEN_TRACKS),
    ).fetchall()
    return [_track(row) for row in rows]


def _on_repeat(conn: sqlite3.Connection, now_ms: int) -> list[dict]:
    """Tracks played at least twice in the last week, most-played first."""
    cutoff = now_ms - ON_REPEAT_DAYS * 86_400_000
    # Aggregate in a subquery for the same reason as `_forgotten`.
    rows = conn.execute(
        f"SELECT {TRACK_FIELDS}, heard.plays"
        "  FROM tracks JOIN (SELECT track_rowid, COUNT(*) AS plays,"
        "                           MAX(started_at) AS last_played"
        "                      FROM plays WHERE started_at >= ?"
        "                     GROUP BY track_rowid) heard"
        "    USING (track_rowid)"
        " WHERE status = 'done' AND heard.plays >= 2"
        " ORDER BY heard.plays DESC, heard.last_played DESC LIMIT ?",
        (cutoff, ON_REPEAT_TRACKS),
    ).fetchall()
    return [{**_track(row), "plays": row["plays"]} for row in rows]


def _recently_added(conn: sqlite3.Connection) -> list[dict]:
    """Albums whose newest track landed most recently."""
    rows = conn.execute(
        "SELECT album_rowid, album_name, artist_name,"
        "       COUNT(*)                  AS track_count,"
        "       SUM(duration_ms)          AS runtime_ms,"
        "       MAX(art_path IS NOT NULL) AS has_art"
        "  FROM tracks"
        " WHERE status = 'done' AND album_rowid IS NOT NULL"
        " GROUP BY album_rowid, album_name"
        " ORDER BY MAX(added_at) DESC, album_rowid LIMIT ?",
        (RECENTLY_ADDED_ALBUMS,),
    ).fetchall()
    return [{**dict(row), "has_art": bool(row["has_art"])} for row in rows]


def _week(conn: sqlite3.Connection, now_ms: int) -> list[dict]:
    """Minutes listened per local day, ending today.

    Bucketed by the server's local day rather than by UTC: the listener's
    "yesterday" is the one they lived through, and this app has exactly one
    listener in exactly one timezone.
    """
    today = datetime.fromtimestamp(now_ms / 1000).date()
    days = [today - timedelta(days=offset) for offset in range(WEEK_DAYS - 1, -1, -1)]
    since = int(datetime.combine(days[0], datetime.min.time()).timestamp() * 1000)

    heard = {
        row["day"]: row["ms"] or 0
        for row in conn.execute(
            "SELECT date(p.started_at / 1000, 'unixepoch', 'localtime') AS day,"
            f"      SUM({_HEARD}) AS ms"
            "  FROM plays p WHERE p.started_at >= ? GROUP BY day",
            (since,),
        )
    }
    return [
        {
            "day": day.isoformat(),
            "weekday": day.strftime("%a"),
            "minutes": round(heard.get(day.isoformat(), 0) / 60_000),
        }
        for day in days
    ]


def _top_artists(conn: sqlite3.Connection) -> list[dict] | None:
    total = conn.execute("SELECT COUNT(*) FROM plays").fetchone()[0]
    if total < TOP_ARTISTS_MIN_PLAYS:
        return None
    rows = conn.execute(
        "SELECT t.artist_name, COUNT(*) AS plays,"
        f"      SUM({_HEARD}) AS heard_ms"
        "  FROM plays p JOIN tracks t USING (track_rowid)"
        " GROUP BY t.artist_name"
        " ORDER BY heard_ms DESC, plays DESC LIMIT 5"
    ).fetchall()
    return [dict(row) for row in rows]


def _library(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT SUM(status = 'done')            AS playable,"
        "       SUM(status = 'missing_file')    AS missing,"
        "       COUNT(DISTINCT album_rowid)     AS albums,"
        "       COUNT(DISTINCT artist_name)     AS artists,"
        "       COALESCE(SUM(CASE WHEN status = 'done' THEN duration_ms END), 0)"
        "                                       AS runtime_ms"
        "  FROM tracks WHERE status IN ('done', 'missing_file')"
    ).fetchone()
    return {key: row[key] or 0 for key in row.keys()}


def _listening(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        f"SELECT COUNT(*) AS plays, COALESCE(SUM({_HEARD}), 0) AS heard_ms"
        "  FROM plays p"
    ).fetchone()
    return {"plays": row["plays"], "heard_ms": row["heard_ms"]}


def dashboard(conn: sqlite3.Connection, now_ms: int | None = None) -> dict:
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)

    # The plane comes from our own emotion vectors when there are any, and
    # from the Spotify mirror's features when there are not. Which one drew it
    # is said in the payload rather than left for the client to guess, because
    # the axes are not the same thing: `arousal` is a model's measurement and
    # `energy` is Spotify's blend.
    plane = emotion if emotion.coverage(conn)["measured"] else features
    measured = plane.coverage(conn)
    bands = plane.band_summary(conn)
    total_measured = measured["measured"] or 0
    for band in bands:
        # Share of what is measured, not of the library — a share computed
        # against 723 when only 705 are measured would never reach 1.
        band["share"] = (
            round(band["tracks"] / total_measured, 4) if total_measured else 0
        )

    seed = _seed(conn)

    return {
        "generated_at": now_ms,
        "resume": _resume(conn),
        "mixes": bands,
        "sounds_like": {
            "seed": seed,
            "tracks": (
                features.neighbours(conn, seed["track_rowid"], SOUNDS_LIKE)
                if seed
                else []
            ),
        },
        "recent_albums": _recent_albums(conn),
        "never_played": _never_played(conn),
        "on_repeat": _on_repeat(conn, now_ms),
        "recently_added": _recently_added(conn),
        "forgotten": _forgotten(conn, now_ms),
        "stats": {
            "library": _library(conn),
            "measured": measured,
            "map": plane.map_points(conn),
            "map_source": "emotion" if plane is emotion else "features",
            "map_centre": (
                emotion.medians(conn) if plane is emotion else None
            ),
            "week": _week(conn, now_ms),
            "listening": _listening(conn),
            "top_artists": _top_artists(conn),
        },
    }
