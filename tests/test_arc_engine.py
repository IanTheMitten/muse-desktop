"""`muse/arc_engine.py` against a real migrated library (spec D5-D8)."""

import pytest

from muse import arc, arc_engine, recommend, request_spec
from muse.embed import pack_vector
from muse.library import embeddings as library_embeddings
from muse.library import lyric_tags as library_lyric_tags
from muse.library import queries as library_queries
from muse.library import ratings as library_ratings
from tests.conftest import insert_track


@pytest.fixture(autouse=True)
def _fresh_caches():
    recommend.clear_calibration_cache()
    arc_engine.clear_tag_cache()
    yield
    recommend.clear_calibration_cache()
    arc_engine.clear_tag_cache()


def tags_for(darkness, theme="love", harm=0.0, heavy=None):
    """One `body` segment whose outlook mixes despair (1.0) and untroubled
    (0.0), so `mood_darkness == darkness` and `glorifying == harm`;
    `heavy` sets `sev_violence` so `heavy_content == heavy`."""
    tags = {"body": {
        "outlook": {"despair": darkness, "untroubled": 1.0 - darkness, "harm_as_fun": harm},
        "theme_broad": {theme: 1.0},
    }}
    if heavy is not None:
        tags["song"] = {"sev_violence": {"graphic": heavy, "absent": 1.0 - heavy}}
    return tags


def add_song(conn, rowid, *, darkness=None, theme="love", harm=0.0, heavy=None,
             artist=None, title=None, text_hash=None, audio=None, arousal=None):
    """A playable track with an audio vector and, when `darkness` is given,
    a tagged lyric set. Distinct artists by default so spacing never walls
    off a test that is not about spacing."""
    insert_track(conn, rowid, artist_name=artist or f"Artist {rowid}",
                 track_name=title or f"Song {rowid}")
    library_embeddings.upsert(
        conn, rowid,
        audio_vec=pack_vector(audio or [1.0, rowid / 1000.0]),
        feature_vec=None, lyrics_vec=None,
        audio_model="stub", lyrics_model=None, lyrics_hash=None,
        **({} if arousal is None else {"audio_va": pack_vector([0.0, arousal])}),
    )
    if darkness is not None:
        library_lyric_tags.write_set(
            conn, track_rowid=rowid, recording_mbid=None, state="tagged",
            text_hash=text_hash or f"h{rowid}", lyrics_hash=None, model="test",
            tags=tags_for(darkness, theme, harm, heavy), taxonomy_version=3,
        )
    return rowid


def ladder_library(conn, *, seed_darkness=0.9):
    """A seed and two love songs at every darkness step from 0.9 to 0.2."""
    add_song(conn, 1, darkness=seed_darkness)
    rowid = 2
    for step in range(15):
        level = round(0.9 - step * 0.05, 2)
        for _ in range(2):
            add_song(conn, rowid, darkness=level)
            rowid += 1
    return 1


def darkness_of(conn, rowid):
    return arc_engine.tag_index(conn)[rowid]["darkness"]


# --- the arc ----------------------------------------------------------------

def test_playlist_tracks_the_curve_within_the_window(conn):
    seed = ladder_library(conn)
    block = arc_engine.arc_block(conn, seed, n=12)
    assert block["arc"] is not None
    assert len(block["curve"]) == 12
    assert len(block["results"]) == 12
    for k, result in enumerate(block["results"]):
        a = result["reasons"]["arc"]
        assert a["target"] == pytest.approx(block["curve"][k])
        assert abs(a["darkness"] - a["target"]) <= arc.DARK_WINDOW + 1e-9
        assert result["reasons"]["relaxed"] == []
    assert block["exhausted"] is False


def test_playlist_ends_lighter_than_it_starts(conn):
    seed = ladder_library(conn)
    results = arc_engine.arc_block(conn, seed, n=12)["results"]
    first = darkness_of(conn, results[0]["track_rowid"])
    last = darkness_of(conn, results[-1]["track_rowid"])
    assert last < first


def test_lift_off_holds_the_start_darkness(conn):
    seed = ladder_library(conn)
    block = arc_engine.arc_block(conn, seed, n=6, lift=False)
    assert block["curve"] == [0.9] * 6
    for result in block["results"]:
        assert result["reasons"]["arc"]["darkness"] >= 0.9 - arc.DARK_WINDOW - 1e-9


def test_theme_holds_to_the_anchor(conn):
    seed = ladder_library(conn)
    # A faith song at exactly the right darkness with the best audio match.
    add_song(conn, 99, darkness=0.9, theme="faith", audio=[1.0, 0.001])
    picks = [r["track_rowid"] for r in arc_engine.arc_block(conn, seed, n=12)["results"]]
    assert 99 not in picks


# --- never relaxed --------------------------------------------------------------

def test_no_two_versions_of_one_song(conn):
    add_song(conn, 1, darkness=0.9, title="Raised to Life", artist="A")
    add_song(conn, 2, darkness=0.9, title="Raised to Life (live)", artist="A")
    add_song(conn, 3, darkness=0.9, title="Holy (live)", artist="B")
    add_song(conn, 4, darkness=0.9, title="Holy", artist="B")
    add_song(conn, 5, darkness=0.9, title="Other", artist="C", text_hash="same")
    add_song(conn, 6, darkness=0.9, title="Cover", artist="D", text_hash="same")
    for rowid in range(7, 15):
        add_song(conn, rowid, darkness=0.9)
    picks = [r["track_rowid"] for r in
             arc_engine.arc_block(conn, 1, n=10, lift=False)["results"]]
    assert 2 not in picks                     # the seed's own live version
    assert not {3, 4} <= set(picks)           # one Holy
    assert not {5, 6} <= set(picks)           # one lyric


def test_excluded_versions_stay_out(conn):
    add_song(conn, 1, darkness=0.9)
    add_song(conn, 2, darkness=0.9, title="Holy", artist="B")
    add_song(conn, 3, darkness=0.9, title="Holy (live)", artist="B")
    add_song(conn, 4, darkness=0.9)
    picks = [r["track_rowid"] for r in
             arc_engine.arc_block(conn, 1, n=3, lift=False, exclude={2})["results"]]
    assert 2 not in picks and 3 not in picks


def test_glorifying_is_never_picked_while_lifting(conn):
    seed = ladder_library(conn)
    add_song(conn, 99, darkness=0.9, harm=0.9, audio=[1.0, 0.001])
    lifting = [r["track_rowid"] for r in arc_engine.arc_block(conn, seed, n=12)["results"]]
    assert 99 not in lifting
    arc_engine.clear_tag_cache()
    held = [r["track_rowid"] for r in
            arc_engine.arc_block(conn, seed, n=12, lift=False)["results"]]
    assert 99 in held


def test_heavy_content_never_rises_while_lifting(conn):
    seed = ladder_library(conn)
    add_song(conn, 99, darkness=0.9, heavy=0.9, audio=[1.0, 0.001])
    picks = [r["track_rowid"] for r in arc_engine.arc_block(conn, seed, n=12)["results"]]
    assert 99 not in picks


def test_thumbs_down_is_never_picked(conn):
    seed = ladder_library(conn)
    library_ratings.set_rating(conn, 2, -1)
    picks = [r["track_rowid"] for r in arc_engine.arc_block(conn, seed, n=12)["results"]]
    assert 2 not in picks


# --- filler -----------------------------------------------------------------

def test_filler_is_at_most_one_in_four(conn):
    add_song(conn, 1, darkness=0.9)
    for rowid in range(2, 8):
        add_song(conn, rowid, darkness=0.9)
    for rowid in range(100, 110):   # untagged, identical in sound to the seed
        add_song(conn, rowid, audio=[1.0, 0.001])
    results = arc_engine.arc_block(conn, 1, n=8, lift=False)["results"]
    flags = [r["reasons"]["arc"]["filler"] for r in results]
    assert any(flags)
    for i, flag in enumerate(flags):
        if flag:
            assert not any(flags[max(0, i - 3):i])


# --- the ladder -------------------------------------------------------------

def test_a_darkness_miss_is_cost_not_a_rung(conn):
    add_song(conn, 1, darkness=0.9)
    for rowid in range(2, 6):
        add_song(conn, rowid, darkness=0.65)
    block = arc_engine.arc_block(conn, 1, n=1, lift=False)
    assert len(block["results"]) == 1
    result = block["results"][0]
    assert result["reasons"]["relaxed"] == []
    assert result["reasons"]["arc"]["darkness"] == 0.65


def test_outside_the_outer_bound_is_never_picked(conn):
    add_song(conn, 1, darkness=0.9)
    for rowid in range(2, 6):
        add_song(conn, rowid, darkness=0.5)
    block = arc_engine.arc_block(conn, 1, n=3, lift=False)
    assert block["results"] == []
    assert block["exhausted"] is True


def test_a_dry_library_reports_exhausted(conn):
    add_song(conn, 1, darkness=0.9)
    add_song(conn, 2, darkness=0.9)
    block = arc_engine.arc_block(conn, 1, n=5, lift=False)
    assert [r["track_rowid"] for r in block["results"]] == [2]
    assert block["exhausted"] is True


# --- seeds ------------------------------------------------------------------

def test_an_untagged_seed_falls_back_to_plain_continuity(conn):
    add_song(conn, 1)  # untagged
    add_song(conn, 2, title="Holy", artist="B", darkness=0.9)
    add_song(conn, 3, title="Holy (live)", artist="B", darkness=0.1)
    add_song(conn, 4, darkness=0.5)
    block = arc_engine.arc_block(conn, 1, n=3)
    assert block["arc"] is None
    assert block["reason"]
    assert block["curve"] == []
    picks = [r["track_rowid"] for r in block["results"]]
    assert not {2, 3} <= set(picks)           # dedupe still holds
    assert all(r["reasons"]["arc"] is None for r in block["results"])


def test_an_unknown_seed_returns_none(conn):
    assert arc_engine.arc_block(conn, 12345) is None


# --- radio ------------------------------------------------------------------

def test_radio_blocks_continue_the_same_curve(conn):
    seed = ladder_library(conn)
    first = arc_engine.arc_block(conn, seed, mode="radio", n=4)
    assert len(first["curve"]) == arc.RADIO_HORIZON
    picked = [r["track_rowid"] for r in first["results"]]
    second = arc_engine.arc_block(conn, seed, mode="radio", n=4, pos=4,
                                  exclude=set(picked), recent=list(reversed(picked)))
    assert second["curve"] == first["curve"]
    for i, result in enumerate(second["results"]):
        assert result["reasons"]["arc"]["target"] == pytest.approx(
            arc.target(first["curve"], 4 + i))
    assert not set(picked) & {r["track_rowid"] for r in second["results"]}


def test_radio_heavy_limit_comes_from_the_last_tagged_pick(conn):
    # The seed is heavy; the session has since come down to a light tagged
    # pick, then a filler. The filler at the head of `recent` must not hand
    # the heavy limit back to the seed (D5).
    add_song(conn, 1, darkness=0.9, heavy=0.9)    # seed
    add_song(conn, 2, darkness=0.9, heavy=0.2)    # last tagged pick
    add_song(conn, 3)                             # filler, head of recent
    add_song(conn, 4, darkness=0.9, heavy=0.9)    # back up to the seed's heaviness
    add_song(conn, 5, darkness=0.9, heavy=0.2)
    block = arc_engine.arc_block(conn, 1, mode="radio", n=2, pos=2,
                                 recent=[3, 2], record=False)
    assert arc.lifting(block["curve"])
    picks = [r["track_rowid"] for r in block["results"]]
    assert 5 in picks
    assert 4 not in picks


def test_theme_pool_skips_blocked_tracks(conn):
    # Forty on-theme songs that sound nothing like the seed, and enough
    # off-theme sound-alikes to fill the sonic pool. With the thirty best
    # theme matches already played, the next ten must still be reachable.
    add_song(conn, 1, darkness=0.9, audio=[1.0, 0.0])
    for rowid in range(2, 42):
        add_song(conn, rowid, darkness=0.9, audio=[0.0, 1.0])
    for rowid in range(100, 140):
        add_song(conn, rowid, darkness=0.9, theme="faith", audio=[1.0, rowid / 10000.0])
    block = arc_engine.arc_block(conn, 1, n=3, lift=False,
                                 exclude=set(range(2, 32)), record=False)
    assert block["exhausted"] is False
    picks = [r["track_rowid"] for r in block["results"]]
    assert len(picks) == 3
    assert all(32 <= rowid < 42 for rowid in picks)


# --- side effects -----------------------------------------------------------

def test_picks_are_recorded_as_offers_unless_asked_not_to(conn):
    seed = ladder_library(conn)
    from muse.library import offers as library_offers
    arc_engine.arc_block(conn, seed, n=3, record=False)
    assert library_offers.counts(conn) == {}
    picks = [r["track_rowid"] for r in arc_engine.arc_block(conn, seed, n=3)["results"]]
    assert set(library_offers.counts(conn)) == set(picks)


def test_track_names_reads_playable_titles(conn):
    add_song(conn, 1, title="Holy")
    insert_track(conn, 2, status="error", track_name="Gone")
    assert library_queries.track_names(conn) == {1: "Holy"}


# --- plan -------------------------------------------------------------------

def spec_of(base, **over):
    return request_spec.check({**base, **over})


def mood_spec(start_subject, goal_subject, d0, d1, **over):
    return request_spec.check({
        "version": 1, "kind": "mood",
        "start": {"subject": start_subject, "darkness": d0},
        "goal": {"subject": goal_subject, "darkness": d1},
        **over,
    })


def pick_ids(block):
    return [r["track_rowid"] for r in block["results"]]


def test_plan_of_a_seed_spec_matches_arc_block(conn):
    seed = ladder_library(conn)
    block = arc_engine.arc_block(conn, seed, n=12, record=False)
    planned = arc_engine.plan(conn, request_spec.seed_spec(seed), n=12, record=False)
    assert pick_ids(planned) == pick_ids(block)


def test_plan_of_an_unknown_seed_returns_none(conn):
    assert arc_engine.plan(conn, request_spec.seed_spec(12345), record=False) is None


def test_plan_artist_filter_picks_only_that_artist(conn):
    ladder_library(conn)
    for rowid in (200, 201, 202):
        add_song(conn, rowid, darkness=0.5, artist="Artist X")
    spec = request_spec.check({
        "version": 1, "kind": "artist", "artist": "artist x",
        "start": {"subject": None, "darkness": None},
        "goal": {"subject": None, "darkness": None},
    })
    block = arc_engine.plan(conn, spec, n=6, record=False)
    assert sorted(pick_ids(block)) == [200, 201, 202]
    assert block["exhausted"] is True


def test_plan_clean_limit_skips_heavy_and_untagged(conn):
    add_song(conn, 1, darkness=0.9, heavy=0.1)
    for rowid in range(2, 10):
        add_song(conn, rowid, darkness=0.9, heavy=0.1)
    add_song(conn, 99, darkness=0.9, heavy=0.9, audio=[1.0, 0.001])
    add_song(conn, 100, audio=[1.0, 0.001])
    spec = spec_of(request_spec.seed_spec(1, lift=False), limits="clean")
    picks = pick_ids(arc_engine.plan(conn, spec, n=8, record=False))
    assert picks
    assert 99 not in picks and 100 not in picks


def test_plan_calm_sound_prefers_the_quiet_twin(conn):
    add_song(conn, 1, darkness=0.9)
    add_song(conn, 2, darkness=0.9, audio=[1.0, 0.0], arousal=0.9)
    add_song(conn, 3, darkness=0.9, audio=[1.0, 0.0], arousal=-0.5)
    spec = spec_of(request_spec.seed_spec(1, lift=False), sound="calm")
    picks = pick_ids(arc_engine.plan(conn, spec, n=2, record=False))
    assert picks == [3, 2]


def test_plan_seedless_mood_picks_only_the_subject(conn):
    rowid = 1
    for step in range(15):
        level = round(0.9 - step * 0.05, 2)
        add_song(conn, rowid, darkness=level, theme="love")
        add_song(conn, rowid + 100, darkness=level, theme="faith")
        rowid += 1
    spec = mood_spec({"theme_broad:love": 1.0}, None, 0.9, 0.25)
    block = arc_engine.plan(conn, spec, n=8, record=False)
    picks = pick_ids(block)
    assert picks
    assert all(p < 100 for p in picks)


def test_plan_blends_the_theme_from_start_to_goal(conn):
    rowid = 1
    for step in range(15):
        level = round(0.9 - step * 0.05, 2)
        add_song(conn, rowid, darkness=level, theme="love")
        add_song(conn, rowid + 100, darkness=level, theme="faith")
        rowid += 1
    spec = mood_spec({"theme_broad:love": 1.0}, {"theme_broad:faith": 1.0}, 0.9, 0.25)
    picks = pick_ids(arc_engine.plan(conn, spec, n=10, record=False))
    assert len(picks) == 10
    assert picks[0] < 100
    assert picks[-1] >= 100


def test_plan_radio_returns_exactly_n(conn):
    seed = ladder_library(conn)
    block = arc_engine.plan(conn, request_spec.seed_spec(seed), mode="radio", n=3,
                            record=False)
    assert len(block["results"]) == 3
    assert block["exhausted"] is False


def test_plan_off_topic_has_no_curve(conn):
    for rowid in range(1, 8):
        add_song(conn, rowid, darkness=0.5)
    spec = request_spec.check({
        "version": 1, "kind": "off_topic",
        "start": {"subject": {"setting:night": 1.0}, "darkness": None},
        "goal": {"subject": None, "darkness": None},
    })
    block = arc_engine.plan(conn, spec, n=4, record=False)
    assert block["results"]
    assert block["curve"] == []
    assert block["arc"]["start"] is None
    for result in block["results"]:
        assert result["reasons"]["arc"]["target"] is None


def test_plan_width_one_still_fills_the_list(conn):
    seed = ladder_library(conn)
    block = arc_engine.plan(conn, request_spec.seed_spec(seed), n=12, width=1,
                            record=False)
    assert len(block["results"]) == 12


def test_plan_retrieves_the_sonic_pool_once_per_anchor(conn, monkeypatch):
    calls = []
    real = recommend.retrieve

    def counting(*args, **kwargs):
        calls.append(args[1] if len(args) > 1 else None)
        return real(*args, **kwargs)

    monkeypatch.setattr(recommend, "retrieve", counting)
    seed = ladder_library(conn)
    arc_engine.arc_block(conn, seed, n=6, record=False)
    assert len(calls) == 1

    calls.clear()
    rowid = 500
    for step in range(15):
        level = round(0.9 - step * 0.05, 2)
        add_song(conn, rowid, darkness=level, theme="love")
        rowid += 1
    spec = mood_spec({"theme_broad:love": 1.0}, None, 0.9, 0.25)
    arc_engine.plan(conn, spec, n=8, record=False)
    assert len(calls) <= arc.BEAM_WIDTH
