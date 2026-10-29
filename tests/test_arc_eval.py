"""`muse/arc_eval.py` -- the read-only arc harness (spec "Measurement")."""

import io

import pytest

from muse import arc, arc_eval
from muse.library import offers as library_offers
from tests.test_arc_engine import _fresh_caches, add_song, ladder_library  # noqa: F401


def test_run_reports_every_metric_and_writes_nothing(conn):
    ladder_library(conn)
    add_song(conn, 90, darkness=0.2)       # light seed: not a dark seed, skipped
    result = arc_eval.run(conn, n=6)
    assert result["seeds"] >= 1
    for key in ("mean_target_error", "mean_theme_sim", "filler_share",
                "duplicate_rate", "exhausted_rate", "relax_rate",
                "random_subject_sim_quantiles"):
        assert key in result
    assert result["duplicate_rate"] == 0.0
    assert library_offers.counts(conn) == {}


def test_report_prints(conn):
    ladder_library(conn)
    out = io.StringIO()
    arc_eval.report(arc_eval.run(conn, n=6), out=out)
    assert "mean_target_error" in out.getvalue()


def test_run_reports_latency(conn):
    ladder_library(conn)
    result = arc_eval.run(conn, n=6)
    assert result["latency_p50"] >= 0.0
    assert result["latency_p95"] >= 0.0


def test_sample_uses_one_seed(conn):
    ladder_library(conn)
    add_song(conn, 91, darkness=0.8)
    assert arc_eval.run(conn, n=4, sample=1)["seeds"] == 1


def test_prompt_sessions_report_all_six_and_write_nothing(conn):
    ladder_library(conn)
    result = arc_eval.prompt_sessions(conn, n=4)
    assert set(result) == {name for name, _ in arc_eval.PROMPT_SPECS}
    assert len(result) == 6
    assert all("results" in v and "exhausted" in v for v in result.values())
    assert library_offers.counts(conn) == {}
    arc_eval.report(result, out=io.StringIO())


def test_weights_parse_sets_constants(conn):
    saved = (arc.ALPHA, arc.BETA, arc.GAMMA, arc.DELTA)
    try:
        arc_eval.set_weights("1,2,3,4")
        assert (arc.ALPHA, arc.BETA, arc.GAMMA, arc.DELTA) == (1.0, 2.0, 3.0, 4.0)
    finally:
        arc.ALPHA, arc.BETA, arc.GAMMA, arc.DELTA = saved
    with pytest.raises(SystemExit):
        arc_eval.set_weights("1,2")
