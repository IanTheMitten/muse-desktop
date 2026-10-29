"""Read-only access to the Spotify catalog mirrors.

The only module that opens `spotify_clean.sqlite3` (17.8 GB) and
`spotify_clean_audio_features.sqlite3` (29 GB). They are opened with `mode=ro`
URIs and are never written by anything, ever.

Slice 1 asks the catalog exactly one kind of question: facts about a track by
`rowid`, on the manifest import path. The manifest carries neither `duration_ms`
nor `isrc`, and its `disc_no` is not reliable. Artist search and album browse
are named on `CatalogLookup` because slice 3 needs them; slice 1 does not call
them.

`CatalogLookup` is a Protocol so the import path can be tested against an
injected fake. Tests never open a 17 GB file.
"""

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

DEFAULT_CATALOG_PATH = Path("spotify_clean.sqlite3")

_ISRC_RE = re.compile(r"^[A-Za-z]{2}[A-Za-z0-9]{3}\d{7}$")


def clean_isrc(raw: str | None) -> str | None:
    """Same sanitizer as `clean_lyrics.py`. The mirror's ISRCs carry stray
    quotes and casing from the scrape."""
    if not raw:
        return None
    text = raw.strip().strip("'\"").strip().upper()
    return text if _ISRC_RE.match(text) else None


@dataclass(frozen=True, slots=True)
class TrackFacts:
    track_rowid: int
    duration_ms: int | None
    isrc: str | None
    disc_no: int | None
    track_no: int | None
    album_rowid: int | None


class CatalogLookup(Protocol):
    def track_facts(self, rowids: list[int]) -> dict[int, TrackFacts]:
        """Facts for the given track rowids. Missing rowids are simply absent."""
        ...

    def search_artists(self, term: str, limit: int = 25) -> list[dict]:
        """Artists whose name contains `term`, exact match first, then by
        popularity. Empty list on no match."""
        ...

    def artist_albums(self, artist_rowid: int) -> list[dict]:
        """This artist's albums, newest release first."""
        ...

    def album_tracks(self, album_rowid: int) -> list[dict]:
        """This album's tracks, disc/track order, with a flattened
        `artist_names` string (`" & "`-joined, matching `music_downloader.py`'s
        existing convention) for display, and the same credit as
        `artist_list`, primary artist first, for anything that stores it.
        The string cannot be split back: "Simon & Garfunkel" is one artist."""
        ...

    def track_by_rowid(self, track_rowid: int) -> dict | None:
        """One track by its own rowid, same shape as one `album_tracks` row.
        None if the rowid isn't in the catalog."""
        ...

    def artist_by_rowid(self, artist_rowid: int) -> dict | None:
        """One artist by its own rowid. None if the rowid isn't in the
        catalog."""
        ...

    def track_id_for_isrc(self, isrc: str) -> str | None:
        """Spotify track id for an ISRC: keyed read on `tracks_isrc`, highest
        popularity then lowest rowid, never a search."""
        ...


def _with_artist_list(row: sqlite3.Row) -> dict:
    track = dict(row)
    # A track with no `track_artists` row aggregates to `[null]`.
    track["artist_list"] = [
        name for name in json.loads(track["artist_list"]) if name is not None
    ]
    return track


class SqliteCatalog:
    """The real thing. Read-only, one connection held for the process lifetime.

    Opening a 17 GB file is not free and slice 1 does it rarely.
    """

    def __init__(self, path: str | Path = DEFAULT_CATALOG_PATH):
        self.path = Path(path)
        uri = f"file:{self.path.as_posix()}?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    def track_facts(self, rowids: list[int]) -> dict[int, TrackFacts]:
        out: dict[int, TrackFacts] = {}
        if not rowids:
            return out
        # `tracks` is 24.9 M rows; query only the rowids asked for, in chunks
        # that stay under SQLITE_MAX_VARIABLE_NUMBER.
        for start in range(0, len(rowids), 500):
            chunk = rowids[start : start + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = self._conn.execute(
                "SELECT rowid, duration_ms, external_id_isrc, disc_number,"
                "       track_number, album_rowid"
                f"  FROM tracks WHERE rowid IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                out[row["rowid"]] = TrackFacts(
                    track_rowid=row["rowid"],
                    duration_ms=row["duration_ms"],
                    isrc=clean_isrc(row["external_id_isrc"]),
                    disc_no=row["disc_number"],
                    track_no=row["track_number"],
                    album_rowid=row["album_rowid"],
                )
        return out

    def search_artists(self, term: str, limit: int = 25) -> list[dict]:
        like = f"%{term.strip()}%"
        rows = self._conn.execute(
            """
            SELECT rowid, id, name, followers_total, popularity
            FROM artists
            WHERE name LIKE ?
            ORDER BY
                CASE WHEN lower(name) = lower(?) THEN 0 ELSE 1 END,
                popularity DESC,
                followers_total DESC,
                name ASC
            LIMIT ?
            """,
            (like, term.strip(), limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def artist_albums(self, artist_rowid: int) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT
                a.rowid, a.id, a.name, a.album_type, a.release_date,
                a.release_date_precision, a.total_tracks, a.popularity,
                aa.is_appears_on, aa.is_implicit_appears_on
            FROM artist_albums aa
            JOIN albums a ON a.rowid = aa.album_rowid
            WHERE aa.artist_rowid = ?
            ORDER BY a.release_date DESC, a.name ASC
            """,
            (artist_rowid,),
        ).fetchall()
        return [dict(row) for row in rows]

    def album_tracks(self, album_rowid: int) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT
                t.rowid, t.id, t.name, t.track_number, t.disc_number,
                t.duration_ms, t.explicit, t.preview_url, t.popularity,
                t.album_rowid,
                GROUP_CONCAT(ar.name, ' & ') AS artist_names,
                json_group_array(ar.name) AS artist_list,
                alb.name AS album_name, alb.total_tracks AS album_total_tracks
            FROM tracks t
            JOIN albums alb ON alb.rowid = t.album_rowid
            LEFT JOIN track_artists ta ON ta.track_rowid = t.rowid
            LEFT JOIN artists ar ON ar.rowid = ta.artist_rowid
            WHERE t.album_rowid = ?
            GROUP BY t.rowid
            ORDER BY t.disc_number ASC, t.track_number ASC, t.rowid ASC
            """,
            (album_rowid,),
        ).fetchall()
        return [_with_artist_list(row) for row in rows]

    def track_by_rowid(self, track_rowid: int) -> dict | None:
        """One track's name plus its flattened `artist_names`/`album_name`
        -- the same shape `album_tracks` returns per row, for a caller that
        only has a bare `track_rowid` (POST /api/jobs's "track" kind) and
        needs the names `TieredMetadataProvider`'s catalog tier expects on
        the payload, not just the facts `track_facts` returns."""
        row = self._conn.execute(
            """
            SELECT
                t.rowid, t.id, t.name, t.track_number, t.disc_number,
                t.duration_ms, t.explicit, t.preview_url, t.popularity,
                t.album_rowid,
                GROUP_CONCAT(ar.name, ' & ') AS artist_names,
                json_group_array(ar.name) AS artist_list,
                alb.name AS album_name, alb.total_tracks AS album_total_tracks
            FROM tracks t
            JOIN albums alb ON alb.rowid = t.album_rowid
            LEFT JOIN track_artists ta ON ta.track_rowid = t.rowid
            LEFT JOIN artists ar ON ar.rowid = ta.artist_rowid
            WHERE t.rowid = ?
            GROUP BY t.rowid
            """,
            (track_rowid,),
        ).fetchone()
        return _with_artist_list(row) if row is not None else None

    def artist_by_rowid(self, artist_rowid: int) -> dict | None:
        """One artist by its own rowid, for a caller holding nothing but a
        rowid off a browse screen.

        `id` is the point of it: the mirror's `artists.id` is the Spotify
        artist id, and MusicBrainz stores streaming links as URL relations,
        so that column is what identifies a catalog artist exactly rather than by
        guessing at the name.
        """
        row = self._conn.execute(
            "SELECT rowid, id, name FROM artists WHERE rowid = ?",
            (artist_rowid,),
        ).fetchone()
        return dict(row) if row is not None else None

    def track_id_for_isrc(self, isrc: str) -> str | None:
        row = self._conn.execute(
            "SELECT id FROM tracks WHERE external_id_isrc = ?"
            " ORDER BY popularity DESC, rowid ASC LIMIT 1",
            (isrc,),
        ).fetchone()
        return row["id"] if row is not None else None

    def close(self) -> None:
        self._conn.close()


class EmptyCatalog:
    """A catalog that knows nothing. Lets the import run on a machine without
    the mirrors — the rows land with NULL duration and ISRC rather than
    failing."""

    def track_facts(self, rowids: list[int]) -> dict[int, TrackFacts]:
        return {}

    def search_artists(self, term: str, limit: int = 25) -> list[dict]:
        return []

    def artist_albums(self, artist_rowid: int) -> list[dict]:
        return []

    def album_tracks(self, album_rowid: int) -> list[dict]:
        return []

    def track_by_rowid(self, track_rowid: int) -> dict | None:
        return None

    def artist_by_rowid(self, artist_rowid: int) -> dict | None:
        return None

    def track_id_for_isrc(self, isrc: str) -> str | None:
        return None
