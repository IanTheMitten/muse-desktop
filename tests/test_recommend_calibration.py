"""The impure half of calibration (ADR 0008): fitting each leg's knots from
the library's own vectors, and caching them on vector freshness."""

import math

from muse import reco, recommend
from muse.embed import pack_vector
from muse.library import embeddings as library_embeddings
from muse.library import taste as library_taste
from tests.conftest import insert_track


def unit(angle):
    return [math.cos(angle), math.sin(angle)]


def embed(conn, rowid, *, audio, lyrics=None):
    insert_track(conn, rowid)
    library_embeddings.upsert(
        conn, rowid,
        audio_vec=pack_vector(audio), feature_vec=None,
        lyrics_vec=pack_vector(lyrics) if lyrics else None,
        audio_model="stub", lyrics_model=None, lyrics_hash=None,
    )


def library(conn, count=30):
    for rowid in range(1, count + 1):
        embed(conn, rowid, audio=unit(rowid * 0.37))


def setup_function():
    recommend.clear_calibration_cache()


def test_fits_knots_for_a_leg_with_enough_pairs(conn):
    library(conn, 30)  # 435 pairs
    cal = recommend.calibration_for(conn, recommend.load_vectors(conn), None)
    assert len(cal["audio_vec"]) == reco.CALIBRATION_KNOTS


def test_sparse_leg_is_left_uncalibrated(conn):
    """Lyrics on 5 tracks is 10 pairs -- below the floor, so no knots."""
    library(conn, 30)
    for rowid in range(31, 36):
        embed(conn, rowid, audio=unit(rowid), lyrics=unit(rowid))
    cal = recommend.calibration_for(conn, recommend.load_vectors(conn), None)
    assert cal.get("lyrics_vec") is None


def test_content_is_uncalibrated_while_gated(conn):
    library(conn, 30)
    cal = recommend.calibration_for(conn, recommend.load_vectors(conn), None)
    assert cal.get("content_vec") is None


def test_fit_is_deterministic(conn):
    library(conn, 80)  # 3160 pairs, above the sample cap
    vectors = recommend.load_vectors(conn)
    first = recommend.calibration_for(conn, vectors, None)
    recommend.clear_calibration_cache()
    assert recommend.calibration_for(conn, vectors, None) == first


def test_cache_is_reused_while_vectors_are_unchanged(conn):
    library(conn, 30)
    vectors = recommend.load_vectors(conn)
    first = recommend.calibration_for(conn, vectors, None)
    assert recommend.calibration_for(conn, vectors, None) is first


def test_cache_invalidates_when_taste_is_retrained(conn):
    """The nightly retrain rewrites taste_embeddings from another process in
    a new ALS basis; a calibration cached across it would be fitted to the
    old basis (see the vector-freshness note on archetype centroids)."""
    library(conn, 30)
    rows = [(r, pack_vector(unit(r)), "stub-als") for r in range(1, 31)]
    library_taste.replace_all(conn, rows, now=1_000)
    first = recommend.calibration_for(conn, recommend.load_vectors(conn), None)

    rows = [(r, pack_vector(unit(r * 2.1)), "stub-als") for r in range(1, 31)]
    library_taste.replace_all(conn, rows, now=2_000)
    second = recommend.calibration_for(conn, recommend.load_vectors(conn), None)
    assert second is not first
    assert second["taste_vec"] != first["taste_vec"]


def test_recommend_for_reports_calibrated_scores(conn):
    library(conn, 30)
    results = recommend.recommend_for(conn, 1, n=3)
    assert results
    assert all(r["reasons"]["calibrated"] for r in results)


def test_cache_does_not_cross_databases(conn, tmp_path):
    """Two libraries whose fingerprints happen to match -- same counts, same
    second -- must not share a calibration."""
    from muse.library import connect

    other = connect(tmp_path / "other.sqlite3")
    try:
        for rowid in range(1, 31):
            embed(conn, rowid, audio=unit(rowid * 0.37))
            embed(other, rowid, audio=unit(rowid * 0.01))
        first = recommend.calibration_for(conn, recommend.load_vectors(conn), None)
        second = recommend.calibration_for(other, recommend.load_vectors(other), None)
        assert second["audio_vec"] != first["audio_vec"]
    finally:
        other.close()
