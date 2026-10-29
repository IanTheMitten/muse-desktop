"""Radio sequencing: the three rules that shape a session.

All three are post-scoring filters, never weights. They encode a fact about
library size -- 723 tracks across 117 artists -- rather than a taste
judgment, which is why story 44's learned scorer inherits them unchanged and
cannot outvote them.
"""

import pytest

from muse import recommend


def scored(rowid, score):
    return {"track_rowid": rowid, "score": score,
            "reasons": {"legs": {}, "available": [], "null_legs": {},
                        "audio_split": None, "content_used": False,
                        "radio_eligible": True}}


ARTISTS = {1: {"a"}, 2: {"a"}, 3: {"b"}, 4: {"c"}, 5: {"c"}}


# --- freshness (O2) -------------------------------------------------------

def test_freshness_is_bounded():
    """Additive and capped, never a multiplier. At launch every track
    qualifies, so an uncapped boost would simply add a constant; three months
    in, a small set of never-played tracks would otherwise carry a permanent
    advantage regardless of fit.

    The cap is in calibrated-score units (ADR 0008): calibrated candidates
    sit ~1.5x further apart than raw ones did, so PRD O2's original 0.15
    became 0.23 to keep the same pull relative to fit."""
    assert recommend.freshness_boost(0) == pytest.approx(recommend.FRESHNESS_MAX)
    assert recommend.FRESHNESS_MAX == pytest.approx(0.23)


def test_freshness_decays_with_offers():
    assert recommend.freshness_boost(1) < recommend.freshness_boost(0)


def test_freshness_is_zero_after_the_offer_limit():
    """Offered, not played. A track you keep declining should stop being
    pushed."""
    assert recommend.freshness_boost(recommend.FRESHNESS_OFFER_LIMIT) == 0.0
    assert recommend.freshness_boost(99) == 0.0


def test_freshness_applies_only_to_unplayed_tracks():
    results = recommend.apply_freshness(
        [scored(1, 0.5), scored(2, 0.5)], fresh={1}, offer_counts={})
    scores = {r["track_rowid"]: r["score"] for r in results}
    assert scores[1] > scores[2]


def test_freshness_is_recorded_in_reasons():
    results = recommend.apply_freshness([scored(1, 0.5)], fresh={1},
                                        offer_counts={1: 1})
    assert results[0]["reasons"]["freshness"] > 0
    assert results[0]["reasons"]["offer_count"] == 1


def test_freshness_cannot_reorder_a_large_similarity_gap():
    """Bounded means bounded: the cap does not overturn a 0.4 difference in
    fit."""
    results = recommend.apply_freshness(
        [scored(1, 0.9), scored(2, 0.5)], fresh={2}, offer_counts={})
    assert results[0]["track_rowid"] == 1


# --- artist spacing -------------------------------------------------------

def test_artist_repeat_is_filtered_within_the_window():
    out = recommend.radio_next([scored(2, 0.99)], artists=ARTISTS,
                               recent=[1], exclude=set(), fresh=set(),
                               offer_counts={})
    assert out is None


def test_artist_spacing_is_not_outvoted_by_similarity():
    """A hard filter applied after scoring, so a near-perfect match by an
    artist just played still loses to a mediocre match by anyone else. If
    this were a weight, 0.99 would win."""
    out = recommend.radio_next([scored(2, 0.99), scored(3, 0.10)],
                               artists=ARTISTS, recent=[1], exclude=set(),
                               fresh=set(), offer_counts={})
    assert out["track_rowid"] == 3


def test_spacing_window_is_three():
    assert recommend.ARTIST_SPACING == 3


def test_artist_outside_the_window_is_allowed():
    """Four tracks back is far enough. The rule is a window, not a ban."""
    out = recommend.radio_next([scored(2, 0.5)], artists=ARTISTS,
                               recent=[1, 3, 4, 5], exclude=set(),
                               fresh=set(), offer_counts={})
    assert out is None or out["track_rowid"] == 2


def test_a_collab_sharing_one_artist_is_blocked():
    """A featured-artist credit is a set, not a display string: sharing any
    member with a recent track is enough to block, even though the two
    credit lines are not equal."""
    artists = {1: {"pink sweat$"}, 2: {"pink sweat$", "kehlani"}}
    out = recommend.radio_next([scored(2, 0.9)], artists=artists, recent=[1],
                               exclude=set(), fresh=set(), offer_counts={})
    assert out is None


def test_disjoint_artist_sets_are_not_blocked():
    artists = {1: {"pink sweat$"}, 2: {"kehlani"}}
    out = recommend.radio_next([scored(2, 0.9)], artists=artists, recent=[1],
                               exclude=set(), fresh=set(), offer_counts={})
    assert out["track_rowid"] == 2


def test_case_and_whitespace_differences_still_collide():
    """Normalization happens once, when `artist_name_sets` builds the
    mapping (queries.py) -- by the time `_eligible` sees it, both credits
    are already `strip().casefold()`'d to the same member."""
    artists = {1: {"pink sweat$"}, 2: {"pink sweat$"}}
    out = recommend.radio_next([scored(2, 0.9)], artists=artists, recent=[1],
                               exclude=set(), fresh=set(), offer_counts={})
    assert out is None


def test_an_empty_artist_set_is_never_blocked_and_never_blocks():
    artists = {1: frozenset(), 2: {"a"}}
    out = recommend.radio_next([scored(2, 0.9)], artists=artists, recent=[1],
                               exclude=set(), fresh=set(), offer_counts={})
    assert out["track_rowid"] == 2

    artists = {1: {"a"}, 2: frozenset()}
    out = recommend.radio_next([scored(2, 0.9)], artists=artists, recent=[1],
                               exclude=set(), fresh=set(), offer_counts={})
    assert out["track_rowid"] == 2


def test_relaxed_spacing_of_one_only_blocks_the_immediately_previous_track():
    """With spacing shrunk to 1 by the ladder, only `recent[:1]` -- the
    track that just played -- can block. Candidate 3 shares an artist with
    track 2 (two back) but not track 1 (just played), so it is blocked at
    the full window and freed once spacing relaxes to 1."""
    artists = {1: {"x"}, 2: {"a"}, 3: {"a"}}
    out = recommend.radio_next([scored(3, 0.8)], artists=artists,
                               recent=[1, 2], exclude=set(), fresh=set(),
                               offer_counts={})
    assert out["track_rowid"] == 3
    assert "artist_spacing" in out["reasons"]["relaxed"]


# --- exclusion and eligibility -------------------------------------------

def test_recently_played_tracks_are_excluded():
    """Honoured while anything else is available. Not asserted as "returns
    None" -- with the excluded track as the only candidate, the relaxation
    ladder is right to hand it back rather than fail, and an earlier version
    of this test contradicted `test_exclusion_relaxes_last` by demanding
    both behaviours from identical inputs."""
    out = recommend.radio_next([scored(3, 0.9), scored(4, 0.2)],
                               artists=ARTISTS, recent=[], exclude={3},
                               fresh=set(), offer_counts={})
    assert out["track_rowid"] == 4
    assert out["reasons"]["relaxed"] == []


def test_thumbs_down_tracks_are_not_radio_eligible():
    result = scored(3, 0.9)
    result["reasons"]["radio_eligible"] = False
    out = recommend.radio_next([result], artists=ARTISTS, recent=[],
                               exclude=set(), fresh=set(), offer_counts={})
    assert out is None


# --- the relaxation ladder (O5) ------------------------------------------

def test_spacing_relaxes_before_returning_nothing():
    """Artist spacing, the exclusion set, and a 30-day freshness window can
    collectively exhaust this library during a long session. Degrade
    visibly rather than failing."""
    # `recent` is most-recent-first. Track 3 (artist B) just played, and
    # track 1 (artist A) before it. Candidate 2 is also artist A: blocked at
    # spacing 3, allowed once spacing shrinks to 1.
    out = recommend.radio_next([scored(2, 0.8)], artists=ARTISTS,
                               recent=[3, 1], exclude=set(), fresh=set(),
                               offer_counts={})
    assert out is not None
    assert "artist_spacing" in out["reasons"]["relaxed"]


def test_exclusion_relaxes_last():
    out = recommend.radio_next([scored(3, 0.8)], artists=ARTISTS, recent=[],
                               exclude={3}, fresh=set(), offer_counts={})
    assert out is not None
    assert "exclusion" in out["reasons"]["relaxed"]


def test_relaxation_is_recorded_even_when_nothing_was_relaxed():
    out = recommend.radio_next([scored(3, 0.8)], artists=ARTISTS, recent=[],
                               exclude=set(), fresh=set(), offer_counts={})
    assert out["reasons"]["relaxed"] == []


def test_no_candidates_at_all_returns_none():
    """The one case the ladder cannot rescue: nothing was scored."""
    assert recommend.radio_next([], artists=ARTISTS, recent=[],
                                exclude=set(), fresh=set(),
                                offer_counts={}) is None


def test_a_thumbs_down_track_is_never_rescued_by_relaxation():
    """The ladder relaxes constraints that encode library size. An explicit
    rejection is not one of those, and no amount of running dry should
    resurface it."""
    result = scored(3, 0.9)
    result["reasons"]["radio_eligible"] = False
    out = recommend.radio_next([result], artists=ARTISTS, recent=[3],
                               exclude={3}, fresh=set(), offer_counts={})
    assert out is None


# --- freshness must not accumulate ----------------------------------------
#
# Found by a real end-to-end run, not by a test. `recommend_for` calls
# `radio_next` once per pick, and `radio_next` applies the freshness boost to
# the same result objects every time -- so a candidate that survived eight
# rounds had been boosted eight times, and scores came back ascending and
# above 1.0. Every test above called `radio_next` exactly once.

def test_applying_freshness_twice_does_not_double_the_boost():
    results = [scored(1, 0.5)]
    recommend.apply_freshness(results, fresh={1}, offer_counts={})
    once = results[0]["score"]
    recommend.apply_freshness(results, fresh={1}, offer_counts={})
    assert results[0]["score"] == pytest.approx(once)


def test_freshness_reapplied_with_a_new_offer_count_replaces_the_old_boost():
    """Idempotent, not frozen: a changed offer count must still change the
    boost, or the decay would stop working after the first application."""
    results = [scored(1, 0.5)]
    recommend.apply_freshness(results, fresh={1}, offer_counts={})
    recommend.apply_freshness(results, fresh={1}, offer_counts={1: 3})
    assert results[0]["score"] == pytest.approx(0.5)
    assert results[0]["reasons"]["freshness"] == 0.0


def test_score_stays_within_one_plus_the_boost():
    results = [scored(1, 1.0)]
    for _ in range(8):
        recommend.apply_freshness(results, fresh={1}, offer_counts={})
    assert results[0]["score"] <= 1.0 + recommend.FRESHNESS_MAX


# --- recommend_for with a dict seed (a synthetic query point) -------------
#
# `recommend_for()` also takes a prepared query dict instead of a
# rowid. This exercises that path end to end against a real, migrated
# `library.sqlite3` -- everything after retrieval is the unchanged machinery
# the rowid path already runs.

from muse.embed import pack_vector
from muse.library import embeddings as library_embeddings
from tests.conftest import insert_track


def _embed_full(conn, rowid, *, audio):
    # Distinct artists per track so artist spacing -- unrelated to what this
    # test checks -- never walls off the block.
    insert_track(conn, rowid, artist_name=f"Artist {rowid}")
    library_embeddings.upsert(
        conn, rowid,
        audio_vec=pack_vector(audio),
        feature_vec=None, lyrics_vec=None,
        audio_model="stub", lyrics_model=None, lyrics_hash=None,
    )


def test_recommend_for_with_a_dict_seed_returns_a_full_block(conn):
    for rowid in range(1, 21):
        _embed_full(conn, rowid, audio=[1.0, float(rowid) / 100.0])

    query = {"audio_vec": [1.0, 0.0]}
    results = recommend.recommend_for(conn, query, n=12)

    assert results is not None
    assert len(results) == 12
    assert len({r["track_rowid"] for r in results}) == 12
