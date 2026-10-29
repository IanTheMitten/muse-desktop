"""Nearest albums: centroids, similarity, the cache, and the two shelves."""

import pytest

from muse import nearest, recommend
from muse.catalog import EmptyCatalog
from muse.embed import pack_vector
from muse.library import embeddings as library_embeddings
from muse.library import plays as library_plays
from muse.library import taste as library_taste
from tests.conftest import insert_track

NOW = 10**12
DAY = 86_400_000

# album_rowid -> (artist, [track rowids], audio vec)
ALBUMS = {
    100: ("A", [1, 2], [1.0, 0.0]),
    200: ("B", [3], [0.9, 0.1]),
    300: ("C", [4], [0.0, 1.0]),
    400: ("D", [5], [-1.0, 0.0]),
}


@pytest.fixture
def lib(conn):
    for album, (artist, rowids, vec) in ALBUMS.items():
        for rowid in rowids:
            insert_track(conn, rowid, album_rowid=album, artist_name=artist,
                         album_name=f"Album {artist}")
            library_embeddings.upsert(
                conn, rowid, audio_vec=pack_vector(vec), feature_vec=None,
                lyrics_vec=None, audio_model="stub", lyrics_model=None,
                lyrics_hash=None)
    return conn


def play(conn, rowid, days_ago=1):
    library_plays.open_span(conn, rowid, 180_000, "library",
                            now_ms=NOW - days_ago * DAY)
    conn.commit()


def test_similarity_identical_is_one_and_uses_shared_legs():
    c = {"audio_vec": [1.0, 0.0], "taste_vec": None}
    assert nearest.album_similarity(c, dict(c)) == pytest.approx(1.0)
    a = {"audio_vec": [1.0, 0.0], "taste_vec": [1.0, 0.0]}
    b = {"audio_vec": [1.0, 0.0], "taste_vec": None}
    assert nearest.album_similarity(a, b) == pytest.approx(1.0)
    assert nearest.album_similarity(
        {"audio_vec": None, "taste_vec": [1.0]},
        {"audio_vec": [1.0], "taste_vec": None}) is None


def test_nearest_albums_excludes_orders_and_breaks_ties_by_rowid():
    same = {"audio_vec": [1.0, 0.0], "taste_vec": None}
    centroids = {
        5: same, 3: same, 9: same,
        7: {"audio_vec": [0.0, 1.0], "taste_vec": None},
        1: same,
    }
    out = nearest.nearest_albums(centroids, same, exclude={1}, n=10)
    assert [rowid for rowid, _ in out] == [3, 5, 9, 7]
    assert out[0][1] >= out[-1][1]


def test_centroid_cache_follows_vector_fingerprint(lib, monkeypatch):
    calls = []
    real = recommend.load_vectors
    monkeypatch.setattr(recommend, "load_vectors",
                        lambda c: calls.append(1) or real(c))
    nearest.album_centroids(lib)
    nearest.album_centroids(lib)
    assert len(calls) == 1
    library_taste.replace_all(lib, [(1, pack_vector([1.0, 0.0]), "stub")])
    lib.commit()
    nearest.album_centroids(lib)
    assert len(calls) == 2


def test_for_fans_of_none_without_recent_plays(lib):
    assert nearest.for_fans_of(lib, NOW) is None
    play(lib, 1, days_ago=40)
    assert nearest.for_fans_of(lib, NOW) is None


def test_for_fans_of_top_artist_excludes_own_albums(lib):
    play(lib, 1)
    play(lib, 2)
    play(lib, 3)
    shelf = nearest.for_fans_of(lib, NOW)
    assert shelf["artist_name"] == "A"
    ids = [a["album_rowid"] for a in shelf["albums"]]
    assert ids == [200, 300, 400]
    assert set(shelf["albums"][0]) >= {
        "album_rowid", "album_name", "artist_name", "track_count",
        "runtime_ms", "has_art"}


class FakeCatalog:
    def search_artists(self, term, limit=25):
        return [{"rowid": 5000 + ord(term[0]), "name": term}]

    def artist_albums(self, artist_rowid):
        # An owned rowid first: it must be skipped for the next album.
        return [
            {"rowid": 200, "name": "Owned", "album_type": "album"},
            {"rowid": 900, "name": "A Single", "album_type": "single"},
            {"rowid": artist_rowid, "name": f"New {artist_rowid}",
             "album_type": "album"},
        ]


def test_recommended_albums_reasons_and_catalog_cap(lib):
    play(lib, 1)
    cards = nearest.recommended_albums(lib, NOW, FakeCatalog())
    owned = [c for c in cards if c["owned"]]
    extra = [c for c in cards if not c["owned"]]
    assert owned[0]["album_rowid"] == 200
    assert owned[0]["reason"] == "Because you played Album A"
    assert 100 not in [c["album_rowid"] for c in owned]
    assert len(extra) == 2
    assert all(c["reason"].startswith("More from ") for c in extra)
    assert "Owned" not in [c["album_name"] for c in extra]
    assert all(c["album_name"].startswith("New ") for c in extra)


def test_recommended_albums_empty_catalog_has_no_cards(lib):
    play(lib, 1)
    cards = nearest.recommended_albums(lib, NOW, EmptyCatalog())
    assert cards and all(c["owned"] for c in cards)


def test_recommended_ranking_is_cached_on_fingerprint_and_seeds(lib, monkeypatch):
    play(lib, 1)
    calls = []
    real = nearest.nearest_albums
    monkeypatch.setattr(nearest, "nearest_albums",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    first = nearest.recommended_albums(lib, NOW, EmptyCatalog())
    scored = len(calls)
    assert scored > 0
    assert nearest.recommended_albums(lib, NOW, EmptyCatalog()) == first
    assert len(calls) == scored  # same fingerprint and seeds: no rescoring
    play(lib, 3)  # a new played album changes the seed set
    nearest.recommended_albums(lib, NOW, EmptyCatalog())
    assert len(calls) > scored


def test_catalog_artist_lookup_is_memoised_across_catalog_instances(lib):
    class Counting(FakeCatalog):
        searches = 0

        def search_artists(self, term, limit=25):
            Counting.searches += 1
            return super().search_artists(term, limit)

    play(lib, 1)
    nearest.recommended_albums(lib, NOW, Counting())
    first = Counting.searches
    assert first > 0
    # The API opens a fresh catalog per request; the frozen mirror's answer
    # for an artist name is reused anyway.
    nearest.recommended_albums(lib, NOW, Counting())
    assert Counting.searches == first
