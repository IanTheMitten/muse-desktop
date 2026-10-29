"""Catalog-tier rows downloaded in-app once stored the mirror's `" & "`-joined
credit line as their `artist_name` and as a one-element `artist_names`, so
the library listed "Justin Bieber & Ed Sheeran" as an artist of its own.
`repair_joined_credits` splits them back with the mirror's own artist list --
never by splitting the string, which would break "Simon & Garfunkel"."""

import sqlite3

from muse.catalog import SqliteCatalog
from muse.library.tracks import repair_joined_credits
from tests.conftest import artist_names, insert_track, track_row


class CreditCatalog:
    def __init__(self, credits: dict[int, list[str]]):
        self.credits = credits
        self.asked_for: list[int] = []

    def track_by_rowid(self, track_rowid):
        self.asked_for.append(track_rowid)
        names = self.credits.get(track_rowid)
        if names is None:
            return None
        return {"rowid": track_rowid, "artist_names": " & ".join(names),
                "artist_list": names}


def test_joined_credit_is_split_into_primary_and_list(conn):
    insert_track(conn, 1, artist_name="Justin Bieber & Ed Sheeran")
    catalog = CreditCatalog({1: ["Justin Bieber", "Ed Sheeran"]})

    assert repair_joined_credits(conn, catalog) == 1

    assert track_row(conn, 1)["artist_name"] == "Justin Bieber"
    assert artist_names(conn, 1) == ["Justin Bieber", "Ed Sheeran"]


def test_a_single_artist_with_an_ampersand_is_left_alone(conn):
    insert_track(conn, 1, artist_name="Simon & Garfunkel")
    catalog = CreditCatalog({1: ["Simon & Garfunkel"]})

    assert repair_joined_credits(conn, catalog) == 0

    assert track_row(conn, 1)["artist_name"] == "Simon & Garfunkel"
    assert artist_names(conn, 1) == ["Simon & Garfunkel"]


def test_rows_already_carrying_a_list_are_not_asked_about(conn):
    insert_track(conn, 1, artist_name="Justin Bieber")
    insert_track(conn, 2, artist_name="Daniel Caesar & H.E.R.")
    conn.execute(
        "UPDATE tracks SET artist_name = 'Daniel Caesar',"
        " artist_names = '[\"Daniel Caesar\", \"H.E.R.\"]' WHERE track_rowid = 2"
    )
    catalog = CreditCatalog({})

    assert repair_joined_credits(conn, catalog) == 0
    assert catalog.asked_for == []


def test_a_track_the_mirror_does_not_know_is_left_alone(conn):
    insert_track(conn, 1, artist_name="A & B")

    assert repair_joined_credits(conn, CreditCatalog({})) == 0

    assert track_row(conn, 1)["artist_name"] == "A & B"


def test_sqlite_catalog_returns_the_credit_as_a_list(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    mirror = sqlite3.connect(path)
    mirror.executescript(
        """
        CREATE TABLE artists (rowid INTEGER PRIMARY KEY, id TEXT, name TEXT);
        CREATE TABLE albums (rowid INTEGER PRIMARY KEY, id TEXT, name TEXT,
            total_tracks INTEGER);
        CREATE TABLE tracks (rowid INTEGER PRIMARY KEY, id TEXT, name TEXT,
            track_number INTEGER, disc_number INTEGER, duration_ms INTEGER,
            explicit INTEGER, preview_url TEXT, popularity INTEGER,
            album_rowid INTEGER);
        CREATE TABLE track_artists (track_rowid INTEGER, artist_rowid INTEGER);
        INSERT INTO artists VALUES (1, 'a1', 'Justin Bieber'),
                                   (2, 'a2', 'Ed Sheeran');
        INSERT INTO albums VALUES (10, 'al1', 'Single', 1);
        INSERT INTO tracks VALUES (100, 't1', 'I Don''t Care', 1, 1, 200000,
            0, NULL, 50, 10);
        INSERT INTO track_artists VALUES (100, 1), (100, 2);
        """
    )
    mirror.commit()
    mirror.close()

    catalog = SqliteCatalog(path)
    track = catalog.track_by_rowid(100)
    assert track["artist_names"] == "Justin Bieber & Ed Sheeran"
    assert track["artist_list"] == ["Justin Bieber", "Ed Sheeran"]
    assert catalog.album_tracks(10)[0]["artist_list"] == [
        "Justin Bieber", "Ed Sheeran",
    ]
