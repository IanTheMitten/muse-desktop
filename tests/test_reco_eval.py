"""The offline harness (PRD O8): replay real transitions from `plays` and ask
where the recommender would have ranked what was actually played next."""

import math

import pytest

from muse import reco_eval, recommend
from muse.embed import pack_vector
from muse.library import embeddings as library_embeddings
from muse.library import plays as library_plays
from tests.conftest import insert_track

MIN = 60_000


def play(conn, rowid, start_ms, *, reason="track_end", reached=180_000,
         length_ms=180_000, source=None):
    play_id = library_plays.open_span(conn, rowid, 180_000, source=source,
                                      now_ms=start_ms)
    library_plays.close_span(conn, play_id, reason, position_ms=reached,
                             now_ms=start_ms + length_ms)


def pairs(conn):
    return [(t["seed"], t["target"]) for t in reco_eval.transitions(conn)]


# --- transitions ----------------------------------------------------------

def test_consecutive_plays_form_a_transition(conn):
    for rowid in (1, 2, 3):
        insert_track(conn, rowid)
    play(conn, 1, 0)
    play(conn, 2, 3 * MIN)
    play(conn, 3, 6 * MIN)
    assert pairs(conn) == [(1, 2), (2, 3)]


def test_long_gap_starts_a_new_session(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    play(conn, 1, 0)
    play(conn, 2, 3 * MIN + reco_eval.SESSION_GAP_MS + 1)
    assert pairs(conn) == []


def test_lost_span_is_not_a_seed(conn):
    """`lost` means nobody chose anything (ADR 0001); what followed was not a
    response to it."""
    insert_track(conn, 1)
    insert_track(conn, 2)
    play(conn, 1, 0, reason="lost")
    play(conn, 2, 3 * MIN)
    assert pairs(conn) == []


def test_repeat_of_the_same_track_is_dropped(conn):
    insert_track(conn, 1)
    play(conn, 1, 0)
    play(conn, 1, 3 * MIN)
    assert pairs(conn) == []


def test_outcome_is_the_target_spans_classification(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    play(conn, 1, 0)
    play(conn, 2, 3 * MIN, reason="user_next", reached=5_000)
    (t,) = reco_eval.transitions(conn)
    assert t["outcome"] == library_plays.SKIP


def test_source_is_carried_through(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    play(conn, 1, 0)
    play(conn, 2, 3 * MIN, source="radio")
    (t,) = reco_eval.transitions(conn)
    assert t["source"] == "radio"


# --- evaluate -------------------------------------------------------------

def unit(angle):
    return [math.cos(angle), math.sin(angle)]


def embed(conn, rowid, angle):
    insert_track(conn, rowid)
    library_embeddings.upsert(
        conn, rowid, audio_vec=pack_vector(unit(angle)), feature_vec=None,
        lyrics_vec=None, audio_model="stub", lyrics_model=None, lyrics_hash=None,
    )


def T(seed, target, outcome=library_plays.FULL_LISTEN, source=None):
    return {"seed": seed, "target": target, "outcome": outcome, "source": source}


def test_nearest_target_ranks_first(conn):
    embed(conn, 1, 0.0)
    embed(conn, 2, 0.1)
    embed(conn, 3, 2.0)
    vectors = recommend.load_vectors(conn)
    out = reco_eval.evaluate(vectors, [T(1, 2)], k_values=(1,))
    assert out["accepted"]["n"] == 1
    assert out["accepted"]["hit@1"] == 1.0
    assert out["accepted"]["mrr"] == 1.0


def test_second_place_scores_half_mrr(conn):
    embed(conn, 1, 0.0)
    embed(conn, 2, 0.1)
    embed(conn, 3, 0.5)
    vectors = recommend.load_vectors(conn)
    out = reco_eval.evaluate(vectors, [T(1, 3)], k_values=(1, 2))
    assert out["accepted"]["hit@1"] == 0.0
    assert out["accepted"]["hit@2"] == 1.0
    assert out["accepted"]["mrr"] == pytest.approx(0.5)


def test_target_never_retrieved_is_a_miss(conn):
    """Full-pipeline ranking: a track outside the top-k-per-leg union is not
    ranked at all, which is what a listener would have experienced."""
    embed(conn, 1, 0.0)
    for rowid in range(2, 2 + recommend.K_PER_LEG + 5):
        embed(conn, rowid, 0.001 * rowid)
    embed(conn, 999, 3.0)
    vectors = recommend.load_vectors(conn)
    out = reco_eval.evaluate(vectors, [T(1, 999)], k_values=(10,))
    assert out["accepted"]["retrieved"] == 0.0
    assert out["accepted"]["mrr"] == 0.0


def test_skips_are_scored_separately(conn):
    embed(conn, 1, 0.0)
    embed(conn, 2, 0.1)
    vectors = recommend.load_vectors(conn)
    out = reco_eval.evaluate(
        vectors, [T(1, 2, outcome=library_plays.SKIP)], k_values=(1,))
    assert out["accepted"]["n"] == 0
    assert out["skipped"]["n"] == 1


def test_user_chosen_group_excludes_recommender_sources(conn):
    """A track the recommender itself queued was put there by the current
    algorithm; it is reported apart so it cannot flatter the baseline."""
    embed(conn, 1, 0.0)
    embed(conn, 2, 0.1)
    vectors = recommend.load_vectors(conn)
    out = reco_eval.evaluate(
        vectors, [T(1, 2, source="radio"), T(1, 2, source="library")],
        k_values=(1,))
    assert out["accepted"]["n"] == 2
    assert out["accepted_user_chosen"]["n"] == 1


def test_transitions_without_vectors_are_counted_not_scored(conn):
    embed(conn, 1, 0.0)
    insert_track(conn, 2)
    vectors = recommend.load_vectors(conn)
    out = reco_eval.evaluate(vectors, [T(1, 2)], k_values=(1,))
    assert out["accepted"]["n"] == 0
    assert out["unscorable"] == 1


def test_retrieval_is_shared_through_the_cache(conn):
    """Retrieval dominates the harness's cost and is the same for every
    variant; a caller-held cache lets four variants pay for it once."""
    embed(conn, 1, 0.0)
    embed(conn, 2, 0.1)
    vectors = recommend.load_vectors(conn)
    cache: dict = {}
    reco_eval.evaluate(vectors, [T(1, 2)], k_values=(1,), retrieved=cache)
    assert cache == {1: {2}}
    cache[1] = set()  # a doctored entry proves the second call reads it
    out = reco_eval.evaluate(vectors, [T(1, 2)], k_values=(1,), retrieved=cache)
    assert out["accepted"]["retrieved"] == 0.0


def test_run_includes_the_no_lyrics_variant():
    import inspect
    from muse import reco_eval
    assert '"calibrated, no lyrics"' in inspect.getsource(reco_eval.run)
