import sqlite3

import pytest

from muse.catalog import EmptyCatalog, SqliteCatalog


@pytest.fixture
def catalog_db(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE artists (rowid INTEGER PRIMARY KEY, id TEXT, name TEXT,
            followers_total INTEGER, popularity INTEGER);
        CREATE TABLE albums (rowid INTEGER PRIMARY KEY, id TEXT, name TEXT,
            album_type TEXT, release_date TEXT, release_date_precision TEXT,
            total_tracks INTEGER, popularity INTEGER);
        CREATE TABLE artist_albums (artist_rowid INTEGER, album_rowid INTEGER,
            is_appears_on INTEGER, is_implicit_appears_on INTEGER);
        CREATE TABLE tracks (rowid INTEGER PRIMARY KEY, id TEXT, name TEXT,
            track_number INTEGER, disc_number INTEGER, duration_ms INTEGER,
            explicit INTEGER, preview_url TEXT, popularity INTEGER,
            album_rowid INTEGER);
        CREATE TABLE track_artists (track_rowid INTEGER, artist_rowid INTEGER);

        INSERT INTO artists VALUES (1, 'sp1', 'Radiohead', 5000000, 80);
        INSERT INTO albums VALUES (10, 'al1', 'OK Computer', 'album',
            '1997-05-21', 'day', 12, 75);
        INSERT INTO artist_albums VALUES (1, 10, 0, 0);
        INSERT INTO tracks VALUES (100, 'tr1', 'Airbag', 1, 1, 284000, 0,
            NULL, 60, 10);
        INSERT INTO track_artists VALUES (100, 1);
        """
    )
    conn.commit()
    conn.close()
    return path


def test_search_artists_matches_by_name(catalog_db):
    catalog = SqliteCatalog(catalog_db)
    results = catalog.search_artists("Radio")
    assert [r["name"] for r in results] == ["Radiohead"]
    catalog.close()


def test_search_artists_no_match(catalog_db):
    catalog = SqliteCatalog(catalog_db)
    assert catalog.search_artists("Nonexistent") == []
    catalog.close()


def test_artist_albums(catalog_db):
    catalog = SqliteCatalog(catalog_db)
    albums = catalog.artist_albums(1)
    assert [a["name"] for a in albums] == ["OK Computer"]
    catalog.close()


def test_album_tracks(catalog_db):
    catalog = SqliteCatalog(catalog_db)
    tracks = catalog.album_tracks(10)
    assert len(tracks) == 1
    assert tracks[0]["name"] == "Airbag"
    assert tracks[0]["artist_names"] == "Radiohead"
    catalog.close()


def test_track_by_rowid_found(catalog_db):
    catalog = SqliteCatalog(catalog_db)
    track = catalog.track_by_rowid(100)
    assert track is not None
    assert track["name"] == "Airbag"
    assert track["artist_names"] == "Radiohead"
    assert track["album_name"] == "OK Computer"
    catalog.close()


def test_track_by_rowid_not_found(catalog_db):
    catalog = SqliteCatalog(catalog_db)
    assert catalog.track_by_rowid(99999) is None
    catalog.close()


def test_empty_catalog_browse_methods_return_nothing():
    catalog = EmptyCatalog()
    assert catalog.search_artists("anything") == []
    assert catalog.artist_albums(1) == []
    assert catalog.album_tracks(10) == []
    assert catalog.track_by_rowid(100) is None


@pytest.fixture
def isrc_db(tmp_path):
    path = tmp_path / "isrc.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE tracks (rowid INTEGER PRIMARY KEY, id TEXT,
            popularity INTEGER, external_id_isrc TEXT);
        INSERT INTO tracks VALUES (1, 'low', 10, 'KRA381601245');
        INSERT INTO tracks VALUES (2, 'tie-first', 50, 'KRA381601245');
        INSERT INTO tracks VALUES (3, 'tie-second', 50, 'KRA381601245');
        """
    )
    conn.commit()
    conn.close()
    return path


def test_track_id_for_isrc_picks_popularity_then_lowest_rowid(isrc_db):
    assert SqliteCatalog(isrc_db).track_id_for_isrc("KRA381601245") == "tie-first"


def test_track_id_for_isrc_unknown_is_none(isrc_db):
    assert SqliteCatalog(isrc_db).track_id_for_isrc("USXX00000000") is None


def test_empty_catalog_track_id_for_isrc_is_none():
    assert EmptyCatalog().track_id_for_isrc("KRA381601245") is None
