"""The impure half of the recommender: the content coverage gate.

Retrieval and the radio rules land in later tasks; this file starts with the
gate because it is the one piece whose absence is a correctness bug rather
than a missing feature.
"""

from muse import content
from muse.embed import pack_vector
from muse.library import content as library_content
from tests.conftest import insert_track


def embed_tracks(conn, count, *, start=1):
    for rowid in range(start, start + count):
        insert_track(conn, rowid)
        library_content.upsert(conn, rowid, content_vec=pack_vector([1.0, 0.0]),
                               model="stub-content")


def set_stats(conn, track_count):
    library_content.replace_stats(
        conn, mean_vec=pack_vector([0.0, 0.0]), sim_lo=-0.5, sim_hi=0.5,
        track_count=track_count,
    )


def test_no_stats_row_means_unavailable(conn):
    """Before any batch has finished there is nothing to normalize through."""
    embed_tracks(conn, 10)
    assert content.content_available(conn) is False


def test_below_threshold_is_unavailable(conn):
    """23 of 723 is where the live library sat when this was written."""
    embed_tracks(conn, 23)
    for rowid in range(24, 724):
        insert_track(conn, rowid)
    set_stats(conn, 23)
    assert content.content_available(conn) is False


def test_at_threshold_is_available(conn):
    embed_tracks(conn, 90)
    for rowid in range(91, 101):
        insert_track(conn, rowid)
    set_stats(conn, 90)
    assert content.content_available(conn) is True


def test_full_coverage_is_available(conn):
    embed_tracks(conn, 10)
    set_stats(conn, 10)
    assert content.content_available(conn) is True


def test_gate_reads_track_count_not_the_embedded_count(conn):
    """The batch commits per track but rewrites `content_stats` only at the
    end, so a run that dies late leaves a nearly-complete set of vectors
    normalized against anchors measured over a handful of tracks. The vectors
    are fine; the anchors are the noisy thing, and the anchors are what the
    gate exists to protect. Ninety-nine vectors against a 22-track mean stays
    dark."""
    embed_tracks(conn, 99)
    insert_track(conn, 100)
    set_stats(conn, 22)
    assert content.content_available(conn) is False


def test_threshold_constant_is_a_ratio(conn):
    assert 0.0 < content.CONTENT_MIN_COVERAGE <= 1.0


def test_no_playable_tracks_does_not_divide_by_zero(conn):
    set_stats(conn, 0)
    assert content.content_available(conn) is False


# --- vector loading and retrieval -----------------------------------------

from muse import recommend  # noqa: E402
from muse.library import embeddings as library_embeddings  # noqa: E402
from muse.library import taste as library_taste  # noqa: E402


def embed_full(conn, rowid, *, audio, feature=None, lyrics=None):
    insert_track(conn, rowid)
    library_embeddings.upsert(
        conn, rowid,
        audio_vec=pack_vector(audio),
        feature_vec=pack_vector(feature) if feature else None,
        lyrics_vec=pack_vector(lyrics) if lyrics else None,
        audio_model="stub", lyrics_model=None, lyrics_hash=None,
    )


def test_load_vectors_returns_one_entry_per_embedded_track(conn):
    embed_full(conn, 1, audio=[1.0, 0.0])
    embed_full(conn, 2, audio=[0.0, 1.0])
    loaded = recommend.load_vectors(conn)
    assert set(loaded) == {1, 2}


def test_load_vectors_unpacks_each_leg(conn):
    embed_full(conn, 1, audio=[1.0, 0.0], lyrics=[0.25, 0.25])
    loaded = recommend.load_vectors(conn)
    assert loaded[1]["audio_vec"] == [1.0, 0.0]
    assert loaded[1]["lyrics_vec"] == [0.25, 0.25]
    assert loaded[1]["feature_vec"] is None


def test_load_vectors_includes_content_when_present(conn):
    embed_full(conn, 1, audio=[1.0, 0.0])
    library_content.upsert(conn, 1, content_vec=pack_vector([0.5, 0.5]),
                           model="stub-content")
    assert recommend.load_vectors(conn)[1]["content_vec"] == [0.5, 0.5]


def test_load_vectors_includes_taste_when_present(conn):
    embed_full(conn, 1, audio=[1.0, 0.0])
    library_taste.replace_all(conn, [(1, pack_vector([0.25, 0.5]), "stub-als")])
    assert recommend.load_vectors(conn)[1]["taste_vec"] == [0.25, 0.5]


def test_retrieval_excludes_the_seed(conn):
    for rowid in range(1, 6):
        embed_full(conn, rowid, audio=[1.0, 0.0])
    loaded = recommend.load_vectors(conn)
    assert 1 not in recommend.retrieve(loaded, 1)


def test_retrieval_default_k_is_thirty():
    """O4. The PRD's top-50 puts 7% of the library into each leg's slice and
    lets the union approach a fifth of everything, which averages the re-rank
    weights into noise."""
    assert recommend.K_PER_LEG == 30


def test_retrieval_caps_each_leg_at_k(conn):
    for rowid in range(1, 51):
        embed_full(conn, rowid, audio=[1.0, float(rowid) / 100.0])
    loaded = recommend.load_vectors(conn)
    assert len(recommend.retrieve(loaded, 1, k=5)) <= 5


def test_retrieval_unions_across_legs(conn):
    """Two candidates, each the best on a different leg, with k=1. A union
    returns both; a single-leg retrieval returns one."""
    insert_track(conn, 1)
    library_embeddings.upsert(
        conn, 1, audio_vec=pack_vector([1.0, 0.0]),
        feature_vec=pack_vector([1.0, 0.0]), lyrics_vec=None,
        audio_model="stub", lyrics_model=None, lyrics_hash=None)
    insert_track(conn, 2)  # audio twin, feature opposite
    library_embeddings.upsert(
        conn, 2, audio_vec=pack_vector([1.0, 0.0]),
        feature_vec=pack_vector([0.0, 1.0]), lyrics_vec=None,
        audio_model="stub", lyrics_model=None, lyrics_hash=None)
    insert_track(conn, 3)  # feature twin, audio opposite
    library_embeddings.upsert(
        conn, 3, audio_vec=pack_vector([0.0, 1.0]),
        feature_vec=pack_vector([1.0, 0.0]), lyrics_vec=None,
        audio_model="stub", lyrics_model=None, lyrics_hash=None)
    loaded = recommend.load_vectors(conn)
    assert recommend.retrieve(loaded, 1, k=1) == {2, 3}


def test_seed_without_a_leg_contributes_no_candidates_from_it(conn):
    """An instrumental seed retrieves on audio alone. It must not retrieve a
    lyrics slice full of arbitrary tracks, and it must not shrink the other
    legs' K to compensate.

    The tracks that *do* have lyrics are deliberately the audio-farthest
    ones, so a fallback that retrieved on the missing leg would pull in a
    track the audio leg would never have offered. An earlier version of this
    test let the lyrics-bearing track double as the audio-nearest, which made
    the two outcomes identical and the test vacuous."""
    embed_full(conn, 1, audio=[1.0, 0.0])                       # seed, no lyrics
    embed_full(conn, 2, audio=[1.0, 0.0])                       # audio twin
    embed_full(conn, 3, audio=[0.0, 1.0], lyrics=[1.0, 0.0])    # far, has lyrics
    embed_full(conn, 4, audio=[0.0, 1.0], lyrics=[0.0, 1.0])    # far, has lyrics
    loaded = recommend.load_vectors(conn)
    assert recommend.retrieve(loaded, 1, k=1) == {2}


def test_retrieval_on_an_empty_library_returns_nothing(conn):
    assert recommend.retrieve({}, 1) == set()


# --- a prepared query dict as the seed, alongside a rowid ------------------


def test_retrieve_accepts_a_dict_seed_and_excludes_nothing(conn):
    """A synthetic query point is no track,
    so there is no rowid to discard from the candidate set the way an int
    seed's own rowid is."""
    for rowid in range(1, 4):
        embed_full(conn, rowid, audio=[1.0, float(rowid) / 10.0])
    loaded = recommend.load_vectors(conn)
    query = {"audio_vec": [1.0, 0.0]}
    candidates = recommend.retrieve(loaded, query, k=5)
    assert candidates == {1, 2, 3}


def test_top_k_with_a_dict_seed_missing_a_leg_is_empty(conn):
    """The dict-seed path mirrors the rowid path: a leg absent from the seed
    contributes no candidates, rather than falling back to some arbitrary
    ordering."""
    embed_full(conn, 1, audio=[1.0, 0.0], lyrics=[1.0, 0.0])
    loaded = recommend.load_vectors(conn)
    query = {"audio_vec": [1.0, 0.0]}  # no lyrics_vec at all
    assert recommend._top_k(loaded, query, "lyrics_vec", 5) == set()


# --- ratings as a constraint, not a leg -----------------------------------

from muse.library import ratings as library_ratings  # noqa: E402


def scored(rowid, score, **reasons):
    base = {"legs": {}, "available": [], "null_legs": {}, "audio_split": None,
            "content_used": False}
    base.update(reasons)
    return {"track_rowid": rowid, "score": score, "reasons": base}


def test_thumbs_down_clamps_the_score_to_zero():
    out = recommend.apply_ratings([scored(1, 0.92)], {1: -1})
    assert out[0]["score"] <= 0.0


def test_thumbs_down_cannot_be_outvoted_by_similarity():
    """The point of making this a constraint rather than a weighted term: a
    near-perfect match on every leg still loses to an explicit rejection."""
    out = recommend.apply_ratings([scored(1, 1.0), scored(2, 0.1)], {1: -1})
    scores = {r["track_rowid"]: r["score"] for r in out}
    assert scores[2] > scores[1]


def test_thumbs_down_removes_the_track_from_radio():
    out = recommend.apply_ratings([scored(1, 0.9)], {1: -1})
    assert out[0]["reasons"]["radio_eligible"] is False


def test_thumbs_up_floors_a_negative_score_at_zero():
    """Cosine runs to -1, so a fused score can be negative. An explicit
    thumbs-up says this track is not disqualified, whatever the legs think."""
    out = recommend.apply_ratings([scored(1, -0.4)], {1: 1})
    assert out[0]["score"] == 0.0


def test_thumbs_up_does_not_inflate_a_good_score():
    """A floor, not a bonus. Explicit signal constrains; it does not
    accumulate, which is what keeps it from being a second weighted term
    wearing a different name."""
    out = recommend.apply_ratings([scored(1, 0.8)], {1: 1})
    assert out[0]["score"] == 0.8


def test_unrated_tracks_are_untouched():
    out = recommend.apply_ratings([scored(1, 0.55)], {})
    assert out[0]["score"] == 0.55
    assert out[0]["reasons"]["radio_eligible"] is True


def test_rating_is_recorded_in_reasons():
    out = recommend.apply_ratings([scored(1, 0.5)], {1: -1})
    assert out[0]["reasons"]["rating"] == -1


def test_rating_works_when_the_taste_leg_is_null(conn):
    """The case story 40 is actually for. taste_sim is null below
    MIN_PLAY_EVENTS, so on a barely-played track a thumbs-down reaches the
    score through no leg at all -- and 'reject it without skipping it
    repeatedly' is precisely the barely-played case. A constraint applied
    after fusion does not care which legs were available."""
    result = scored(1, 0.9, available=["w_audio"], null_legs={
        "w_taste": "below_play_threshold"})
    out = recommend.apply_ratings([result], {1: -1})
    assert out[0]["score"] <= 0.0
    assert out[0]["reasons"]["radio_eligible"] is False


def test_rating_is_not_a_term_in_the_weighted_sum():
    """The weights are untouched by a rating. If this ever fails, ratings
    have become a sixth leg and ADR 0003's one-event-weighting-scheme
    argument has been quietly reopened."""
    result = scored(1, 0.5, legs={"w_audio": {"sim": 0.5, "weight": 1.0,
                                              "contribution": 0.5}},
                    available=["w_audio"])
    out = recommend.apply_ratings([result], {1: -1})
    legs = out[0]["reasons"]["legs"]
    assert sum(leg["weight"] for leg in legs.values()) == 1.0
    assert legs["w_audio"]["weight"] == 1.0


def test_all_ratings_reads_every_rated_track(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    library_ratings.set_rating(conn, 1, 1)
    library_ratings.set_rating(conn, 2, -1)
    assert library_ratings.all_ratings(conn) == {1: 1, 2: -1}


def test_a_track_with_only_a_content_vector_is_still_loaded(conn):
    """Found on the real library, not by a unit test.

    Every test above created an `embeddings` row first, so nothing noticed
    that `load_vectors` was building its keys from that table alone and
    dropping any track absent from it. On the live library -- 97 content
    vectors and no CLAP batch ever run -- that discarded every content vector
    there was and returned nothing at all.

    The null-leg contract says any leg may be missing. That has to include
    the leg the mapping happens to be keyed on."""
    insert_track(conn, 1)
    library_content.upsert(conn, 1, content_vec=pack_vector([1.0, 0.0]),
                           model="stub-content")
    loaded = recommend.load_vectors(conn)
    assert 1 in loaded
    assert loaded[1]["content_vec"] == [1.0, 0.0]
    assert loaded[1]["audio_vec"] is None


def test_a_track_with_only_a_taste_vector_is_still_loaded(conn):
    """The same hole, reached through the other side table."""
    insert_track(conn, 1)
    library_taste.replace_all(conn, [(1, pack_vector([0.5, 0.5]), "stub-als")])
    loaded = recommend.load_vectors(conn)
    assert 1 in loaded
    assert loaded[1]["taste_vec"] == [0.5, 0.5]


def test_unplayable_tracks_stay_out_however_they_are_reached(conn):
    """Widening the key set must not widen it to tracks that cannot be
    played. A missing file still appears in the library and in search
    (principle 3), but recommending one offers something unplayable."""
    insert_track(conn, 1, status="missing_file")
    library_content.upsert(conn, 1, content_vec=pack_vector([1.0, 0.0]),
                           model="stub-content")
    assert recommend.load_vectors(conn) == {}
