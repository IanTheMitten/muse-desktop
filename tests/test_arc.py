"""`muse/arc.py` -- the arc engine's pure half (spec D3-D6)."""

import pytest

from muse import arc


# --- curve ------------------------------------------------------------------

def test_curve_holds_then_lands_on_the_end_target():
    c = arc.curve(0.9, 30)
    hold = round(arc.HOLD_FRAC * 30)
    assert c[:hold] == [0.9] * hold
    assert c[-1] == pytest.approx(arc.END_TARGET)
    assert len(c) == 30


def test_curve_never_rises_and_never_drops_more_than_the_step_cap():
    for d0 in (0.4, 0.7, 1.0):
        for span in (2, 5, 12, 30):
            c = arc.curve(d0, span)
            for a, b in zip(c, c[1:]):
                assert b <= a + 1e-12
                assert a - b <= arc.STEP_CAP + 1e-12


def test_short_curve_is_held_back_by_the_step_cap():
    """Twelve songs from 1.0 cannot reach 0.25 in seven capped steps of a
    smoothstep; the cap wins over landing exactly."""
    c = arc.curve(1.0, 12)
    assert c[-1] >= arc.END_TARGET
    assert max(a - b for a, b in zip(c, c[1:])) <= arc.STEP_CAP + 1e-12


def test_curve_is_flat_without_lift():
    assert arc.curve(0.9, 12, lift=False) == [0.9] * 12


def test_curve_is_flat_when_the_seed_is_already_light():
    assert arc.curve(0.2, 12) == [0.2] * 12
    assert arc.curve(arc.END_TARGET, 5) == [arc.END_TARGET] * 5


def test_curve_of_one_or_zero():
    assert arc.curve(0.9, 1) == [0.9]
    assert arc.curve(0.9, 0) == []


def test_target_holds_the_last_value_past_the_span():
    c = arc.curve(0.9, arc.RADIO_HORIZON)
    assert arc.target(c, 0) == 0.9
    assert arc.target(c, arc.RADIO_HORIZON + 50) == c[-1]


def test_lifting_is_a_curve_that_ends_below_its_start():
    assert arc.lifting(arc.curve(0.9, 12)) is True
    assert arc.lifting(arc.curve(0.9, 12, lift=False)) is False
    assert arc.lifting([]) is False


# --- subject vector and theme similarity -------------------------------------

def test_subject_vector_drops_the_mood_groups():
    vec = {
        "theme_broad:love": 0.8, "detail_love:devotion": 0.6, "address:lover": 0.5,
        "time:present": 0.4, "setting:night": 1.0, "mode:letter": 1.0,
        "outlook:despair": 0.9, "emotion:sadness": 0.7, "stance:pleading": 0.6,
        "sev_violence:absent": 1.0,
    }
    assert arc.subject_vector(vec) == {
        "theme_broad:love": 0.8, "detail_love:devotion": 0.6, "address:lover": 0.5,
        "time:present": 0.4, "setting:night": 1.0, "mode:letter": 1.0,
    }


def test_theme_sim_is_cosine_over_shared_keys():
    assert arc.theme_sim({"a": 1.0}, {"a": 2.0}) == pytest.approx(1.0)
    assert arc.theme_sim({"a": 1.0}, {"b": 1.0}) == 0.0
    assert arc.theme_sim({"a": 1.0, "b": 1.0}, {"a": 1.0}) == pytest.approx(2 ** -0.5)


def test_theme_sim_of_an_empty_vector_is_zero():
    assert arc.theme_sim({}, {"a": 1.0}) == 0.0
    assert arc.theme_sim({"a": 0.0}, {"a": 1.0}) == 0.0


# --- filters ----------------------------------------------------------------

def test_darkness_ok_is_inclusive_at_the_window():
    assert arc.darkness_ok(0.75, 0.9, arc.DARK_WINDOW)
    assert not arc.darkness_ok(0.74, 0.9, arc.DARK_WINDOW)


def test_glorify_only_blocks_while_lifting():
    assert not arc.glorify_ok(arc.GLORIFY_MAX, lifting=True)
    assert arc.glorify_ok(arc.GLORIFY_MAX - 0.01, lifting=True)
    assert arc.glorify_ok(0.99, lifting=False)


def test_heavy_may_rise_only_by_the_slack_while_lifting():
    assert arc.heavy_ok(0.4, 0.3, lifting=True)
    assert not arc.heavy_ok(0.41, 0.3, lifting=True)
    assert arc.heavy_ok(1.0, 0.0, lifting=False)
    assert arc.heavy_ok(1.0, None, lifting=True)


def test_filler_ok_blocks_a_filler_within_the_last_three():
    assert arc.filler_ok([])
    assert arc.filler_ok([False, False, False, True])
    assert not arc.filler_ok([False, False, True])
    assert not arc.filler_ok([True])


# --- version key --------------------------------------------------------------

@pytest.mark.parametrize("title", [
    "Raised to Life (live)", "Raised to Life [Live]", "Raised To Life",
    "Raised to Life - Live", "Raised to Life (2011 Remaster)",
    "Raised to Life - 2011 Remastered", "Raised to Life (feat. Someone)",
    "Raised to Life feat. Someone", "Raised to Life (Acoustic Version)",
    "Raised  to   Life (Radio Edit)",
])
def test_title_key_strips_version_markers(title):
    assert arc.title_key(title) == "raised to life"


def test_title_key_keeps_other_brackets():
    assert arc.title_key("Revelation 19:1 (Hallelujah)") == "revelation 19:1 (hallelujah)"


def test_same_version_by_title_needs_a_shared_artist():
    seen_titles = {"holy": {"artist b"}}
    assert arc.same_version("holy", frozenset({"artist b"}), None, seen_titles, set())
    assert not arc.same_version("holy", frozenset({"artist c"}), None, seen_titles, set())
    assert not arc.same_version("holy", frozenset(), None, seen_titles, set())


def test_same_version_by_text_hash_ignores_the_artist():
    assert arc.same_version("cover", frozenset({"x"}), "h1", {}, {"h1"})
    assert not arc.same_version("cover", frozenset({"x"}), None, {}, {"h1"})


# --- ladder -----------------------------------------------------------------

def test_ladder_order_and_names():
    assert [r.relaxed for r in arc.LADDER] == [
        (),
        ("filler",),
        ("filler", "freshness"),
        ("filler", "freshness", "artist_spacing"),
    ]
    assert [r.relaxed for r in arc.PLAIN_LADDER] == [
        (), ("freshness",), ("freshness", "artist_spacing"),
    ]


def test_ladder_rungs_only_ever_loosen():
    for a, b in zip(arc.LADDER, arc.LADDER[1:]):
        assert b.filler_cap <= a.filler_cap
        assert b.freshness <= a.freshness
        assert b.relax_spacing >= a.relax_spacing


# --- curve end / hold ---------------------------------------------------------

def test_curve_end_sets_the_landing_value():
    assert arc.curve(0.9, 10, end=0.5)[-1] == pytest.approx(0.5)


def test_curve_hold_sets_the_held_share():
    c = arc.curve(0.9, 10, hold=0.7)
    assert c[:7] == [0.9] * 7
    assert c[7] < 0.9


def test_curve_default_call_equals_the_explicit_defaults():
    assert arc.curve(0.9, 10) == arc.curve(
        0.9, 10, lift=True, end=arc.END_TARGET, hold=arc.HOLD_FRAC)


# --- progress -----------------------------------------------------------------

def test_progress_is_zero_on_a_flat_curve():
    assert arc.progress([0.5] * 5, 3) == 0.0


def test_progress_runs_from_zero_to_one():
    c = arc.curve(0.9, 10)
    assert arc.progress(c, 0) == 0.0
    assert arc.progress(c, len(c) - 1) == pytest.approx(1.0)
    assert arc.progress(c, len(c) + 20) == pytest.approx(1.0)


# --- blend --------------------------------------------------------------------

def test_blend_without_a_goal_is_the_start():
    a = {"x": 1.0, "y": 2.0}
    assert arc.blend(a, None, 0.5) == a


def test_blend_of_equal_vectors_is_the_start():
    a = {"x": 1.0, "y": 2.0}
    assert arc.blend(a, a, 0.7) == a


def test_blend_at_one_points_at_the_goal():
    a = {"x": 1.0}
    b = {"y": 1.0, "z": 2.0}
    assert arc.theme_sim(arc.blend(a, b, 1.0), b) == pytest.approx(1.0)


def test_blend_of_empty_vectors_is_empty():
    assert arc.blend({}, {}, 0.5) == {}


# --- sound_gap ----------------------------------------------------------------

def test_sound_gap():
    assert arc.sound_gap(None, "calm") == 0
    assert arc.sound_gap(0.9, "any") == 0
    assert arc.sound_gap(0.0, "calm") == 0
    assert arc.sound_gap(0.9, "calm") == pytest.approx(0.65)


# --- limits_ok ----------------------------------------------------------------

def test_limits_ok():
    assert arc.limits_ok(None, "anything") is True
    assert arc.limits_ok(None, "clean") is False
    assert arc.limits_ok(0.2, "clean") is True
    assert arc.limits_ok(0.3, "clean") is False
    assert arc.limits_ok(0.5, "no_explicit") is True


# --- in_bounds ----------------------------------------------------------------

def test_in_bounds_darkness_edge_is_inclusive():
    tgt = 0.9
    assert arc.in_bounds(tgt - arc.DARK_WINDOW_WIDE, tgt, 1.0, theme_bound=True)
    assert not arc.in_bounds(tgt - arc.DARK_WINDOW_WIDE - 0.01, tgt, 1.0, theme_bound=True)


def test_in_bounds_without_a_target_ignores_darkness():
    assert arc.in_bounds(0.0, None, 1.0, theme_bound=True)


def test_in_bounds_theme_bound_gates_the_similarity():
    low = arc.THEME_FLOOR / 2 - 0.01
    assert not arc.in_bounds(0.5, 0.5, low, theme_bound=True)
    assert arc.in_bounds(0.5, 0.5, low, theme_bound=False)
    assert arc.in_bounds(0.5, 0.5, arc.THEME_FLOOR / 2, theme_bound=True)


# --- pick_cost ----------------------------------------------------------------

def test_pick_cost_without_an_arc_is_continuity_and_sound_only():
    cost = arc.pick_cost(arc_on=False, filler=False, darkness=0.9, tgt=0.5,
                         sim=0.1, continuity=0.75, gap=0.2)
    assert cost == pytest.approx(arc.GAMMA * 0.25 + arc.DELTA * 0.2)


def test_pick_cost_filler_takes_the_filler_cost():
    cost = arc.pick_cost(arc_on=True, filler=True, darkness=None, tgt=0.5,
                         sim=None, continuity=0.75, gap=0.2)
    assert cost == pytest.approx(arc.FILLER_COST + arc.GAMMA * 0.25 + arc.DELTA * 0.2)


def test_pick_cost_tagged_with_a_target():
    cost = arc.pick_cost(arc_on=True, filler=False, darkness=0.7, tgt=0.5,
                         sim=0.6, continuity=0.75, gap=0.2)
    assert cost == pytest.approx(
        arc.ALPHA * 0.2 ** 2 + arc.BETA * 0.4 + arc.GAMMA * 0.25 + arc.DELTA * 0.2)


def test_pick_cost_tagged_without_a_target_has_no_darkness_term():
    cost = arc.pick_cost(arc_on=True, filler=False, darkness=0.7, tgt=None,
                         sim=0.6, continuity=0.75, gap=0.2)
    assert cost == pytest.approx(arc.BETA * 0.4 + arc.GAMMA * 0.25 + arc.DELTA * 0.2)


# --- beam_search --------------------------------------------------------------

# r -> A (1) or B (2); A -> C (10); B -> D (1). Greedy: A, C = 11. Best: B, D = 3.
GRAPH = {
    "r": [(1.0, "A", "A"), (2.0, "B", "B")],
    "A": [(10.0, "C", "C")],
    "B": [(1.0, "D", "D")],
}


def _expand(state, k):
    return GRAPH.get(state, [])


def test_beam_width_one_is_the_stepwise_argmin():
    assert arc.beam_search("r", 2, 1, _expand) == (["A", "C"], False)


def test_beam_width_two_finds_the_cheaper_total_path():
    assert arc.beam_search("r", 2, 2, _expand) == (["B", "D"], False)


def test_beam_dead_end_returns_the_short_path_exhausted():
    graph = {"r": [(1.0, "A", "A")], "A": [(1.0, "B", "B")]}
    out = arc.beam_search("r", 3, 2, lambda state, k: graph.get(state, []))
    assert out == (["A", "B"], True)


def test_beam_of_zero_steps_is_empty():
    assert arc.beam_search("r", 0, 2, _expand) == ([], False)


@pytest.mark.parametrize("title", [
    "Love - Demon Days",
    "Hello (Demon)",
    "Song (Monotonous)",
    "Song - Demonstration",
])
def test_title_key_leaves_words_that_only_start_with_a_marker(title):
    assert arc.title_key(title) == title.lower()


@pytest.mark.parametrize("title", ["Song (Edited)", "Song - Deluxe Edition"])
def test_title_key_strips_edited_and_edition(title):
    assert arc.title_key(title) == "song"
