import json
import time

import pytest

from muse.catalog import TrackFacts
from muse.features import TrackFeatures
from muse.library import connect
from muse.library.tracks import _index_track


@pytest.fixture
def conn(tmp_path):
    """A real library.sqlite3 in a temp directory, migrated.

    A real database rather than a mock: SQLite in a temp file is fast, and every
    behaviour worth testing here is a database behaviour that a mock would not
    have.
    """
    connection = connect(tmp_path / "library.sqlite3")
    yield connection
    connection.close()


class FakeCatalog:
    """Stands in for the 17 GB mirror. Tests never open the real one."""

    def __init__(self, facts: dict[int, TrackFacts] | None = None,
                 isrc_ids: dict[str, str] | None = None):
        self.facts = facts or {}
        self.isrc_ids = isrc_ids or {}
        self.asked_for: list[int] = []

    def track_id_for_isrc(self, isrc):
        return self.isrc_ids.get(isrc)

    def track_facts(self, rowids):
        self.asked_for.extend(rowids)
        return {rowid: self.facts[rowid] for rowid in rowids if rowid in self.facts}


def make_facts(rowid, duration_ms=180_000, isrc="GBAAA1234567", disc_no=1,
               track_no=1, album_rowid=None):
    return TrackFacts(
        track_rowid=rowid,
        duration_ms=duration_ms,
        isrc=isrc,
        disc_no=disc_no,
        track_no=track_no,
        album_rowid=album_rowid,
    )


def make_manifest(*records):
    return {
        "version": 1,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
        "downloads": {str(r["track_rowid"]): r for r in records},
    }


def make_record(track_rowid, status="done", file_path=None, **overrides):
    record = {
        "track_rowid": track_rowid,
        "track_id": f"spotify{track_rowid}",
        "artist_rowid": 10,
        "album_rowid": 20,
        "artist_name": "Some Artist",
        "artist_names": ["Some Artist"],
        "album_name": "Some Album",
        "track_name": "Some Song",
        "track_no": 1,
        "disc_no": 1,
        "file_path": file_path,
        "lyrics_path": None,
        "source_query": "some artist some song",
        "source_site_id": "abc123",
        "status": status,
        "error": None,
        "downloaded_at": "2026-01-01T00:00:00",
    }
    record.update(overrides)
    return record


class FakeFeatures:
    """Stands in for the 29 GB audio-features mirror."""

    def __init__(self, rows: dict[str, TrackFeatures] | None = None):
        self.rows = rows or {}
        self.asked_for: list[str] = []

    def features(self, track_ids):
        self.asked_for.extend(track_ids)
        return {tid: self.rows[tid] for tid in track_ids if tid in self.rows}


def make_features(track_id, valence=0.5, energy=0.5, danceability=0.5,
                  acousticness=0.2, instrumentalness=0.0, tempo=120.0,
                  loudness=-8.0, mode=1, speechiness=0.05, liveness=0.1,
                  key=0, time_signature=4):
    return TrackFeatures(
        track_id=track_id,
        valence=valence,
        energy=energy,
        danceability=danceability,
        acousticness=acousticness,
        instrumentalness=instrumentalness,
        tempo=tempo,
        loudness=loudness,
        mode=mode,
        speechiness=speechiness,
        liveness=liveness,
        key=key,
        time_signature=time_signature,
    )


def insert_track(conn, rowid, *, track_id="auto", status="done", album_rowid=20,
                 artist_name="Some Artist", album_name="Some Album",
                 track_name="Some Song", duration_ms=180_000, added_at=None,
                 isrc=None, track_no=1, disc_no=1, file_path="auto"):
    """A library row, straight in. Faster and clearer than importing a manifest
    when the test is about something downstream of the import."""
    conn.execute(
        "INSERT INTO tracks (track_rowid, track_id, origin, metadata_tier,"
        "  artist_rowid, album_rowid, artist_name, artist_names, album_name,"
        "  track_name, track_no, disc_no, duration_ms, file_path, status,"
        "  added_at, isrc)"
        " VALUES (?,?,'catalog','catalog',10,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            rowid,
            f"spotify{rowid}" if track_id == "auto" else track_id,
            album_rowid,
            artist_name,
            json.dumps([artist_name]),
            album_name,
            track_name,
            track_no,
            disc_no,
            duration_ms,
            (f"MUSIC/{rowid}.m4a" if status == "done" else None)
            if file_path == "auto" else file_path,
            status,
            added_at if added_at is not None else int(time.time()),
            isrc,
        ),
    )
    _index_track(conn, rowid, track_name, artist_name, album_name)
    return rowid


def track_row(conn, rowid):
    row = conn.execute(
        "SELECT * FROM tracks WHERE track_rowid = ?", (rowid,)
    ).fetchone()
    return dict(row) if row else None


def artist_names(conn, rowid):
    return json.loads(track_row(conn, rowid)["artist_names"])
