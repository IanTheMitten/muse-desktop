"""library.embeddings.audio_arousal: stored audio arousal per playable track."""

import pytest

from muse.embed import pack_vector
from muse.library import embeddings as library_embeddings
from tests.conftest import insert_track


def put(conn, rowid, audio_va):
    library_embeddings.upsert(
        conn, rowid,
        audio_vec=pack_vector([1.0, 0.0]),
        feature_vec=None, lyrics_vec=None,
        audio_model="stub", lyrics_model=None, lyrics_hash=None,
        audio_va=audio_va,
    )


def test_reads_the_second_component(conn):
    insert_track(conn, 1)
    put(conn, 1, pack_vector([0.1, -0.4]))
    assert library_embeddings.audio_arousal(conn) == {1: pytest.approx(-0.4)}


def test_unplayable_track_is_excluded(conn):
    insert_track(conn, 1, status="error")
    put(conn, 1, pack_vector([0.1, -0.4]))
    assert library_embeddings.audio_arousal(conn) == {}


def test_null_audio_va_is_absent(conn):
    insert_track(conn, 1)
    put(conn, 1, None)
    assert library_embeddings.audio_arousal(conn) == {}
