import pytest

from muse.embed import pack_vector
from muse.library import embeddings as library_embeddings
from muse.library import prompts
from tests.conftest import insert_track


def _track(conn, rowid, vec, model="stub"):
    insert_track(conn, rowid)
    library_embeddings.upsert(
        conn, rowid, audio_vec=pack_vector(vec), feature_vec=None,
        audio_model=model, lyrics_hash=None)


def test_put_get_round_trip(conn):
    prompts.put(conn, "stub", "calm", [1, 0])
    assert [float(x) for x in prompts.get(conn, "stub", "calm")] == pytest.approx([1.0, 0.0])


def test_current_audio_model_is_the_most_common_ties_by_name(conn):
    assert prompts.current_audio_model(conn) is None
    _track(conn, 1, [1.0, 0.0], model="beta")
    _track(conn, 2, [1.0, 0.0], model="alpha")
    assert prompts.current_audio_model(conn) == "alpha"
    _track(conn, 3, [1.0, 0.0], model="beta")
    assert prompts.current_audio_model(conn) == "beta"


def test_audio_energy_empty_without_prompts(conn):
    _track(conn, 1, [1.0, 0.0])
    assert library_embeddings.audio_energy(conn) == {}


def test_audio_energy_spans_minus_one_to_one_and_skips_other_models(conn):
    prompts.put(conn, "stub", "calm", [1, 0])
    prompts.put(conn, "stub", "energetic", [0, 1])
    _track(conn, 1, [1.0, 0.0])
    _track(conn, 2, [0.7, 0.7])
    _track(conn, 3, [0.0, 1.0])
    _track(conn, 4, [0.0, 1.0], model="other")
    energy = library_embeddings.audio_energy(conn)
    assert energy[1] == pytest.approx(-1.0)
    assert energy[3] == pytest.approx(1.0)
    assert -1.0 < energy[2] < 1.0
    assert 4 not in energy


def test_audio_energy_cache_follows_the_prompt_stamp(conn):
    _track(conn, 1, [1.0, 0.0])
    _track(conn, 2, [0.0, 1.0])
    assert library_embeddings.audio_energy(conn) == {}
    prompts.put(conn, "stub", "calm", [1, 0])
    prompts.put(conn, "stub", "energetic", [0, 1])
    assert set(library_embeddings.audio_energy(conn)) == {1, 2}
