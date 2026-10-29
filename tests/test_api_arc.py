"""`GET /api/arc/{seed_rowid}` (spec D7). Same shape as the other API
tests: no TestClient, the endpoint is pulled off `app.routes`."""

import pytest
from fastapi import HTTPException

from muse import request_spec
from tests.test_api_recommend import app, route  # noqa: F401  (fixture + helper)
from tests.test_arc_engine import _fresh_caches, add_song, ladder_library  # noqa: F401


def arc_route(app):
    return route(app, "/api/arc/{seed_rowid}")


def call(app, seed, **params):
    defaults = dict(mode="playlist", n=12, pos=0, lift=1, exclude="", recent="")
    return arc_route(app)(seed, **{**defaults, **params})


def test_playlist_shape(app):
    seed = ladder_library(app.state.conn)
    body = call(app, seed, n=6)
    assert body["seed"] == seed
    assert body["mode"] == "playlist"
    assert len(body["results"]) == 6
    assert len(body["curve"]) == 6
    assert body["arc"]["start"] == pytest.approx(0.9)
    assert body["reason"] is None
    assert body["exhausted"] is False
    first = body["results"][0]
    assert first["track"]["track_rowid"] == first["track_rowid"]
    assert set(first["reasons"]["arc"]) == {"target", "darkness", "theme_sim", "filler"}
    assert first["reasons"]["relaxed"] == []


def test_radio_continues_from_pos(app):
    seed = ladder_library(app.state.conn)
    body = call(app, seed, mode="radio", n=3, pos=3, exclude="2,3,4", recent="4,3,2")
    assert body["mode"] == "radio"
    assert {r["track_rowid"] for r in body["results"]}.isdisjoint({2, 3, 4})


def test_untagged_seed(app):
    add_song(app.state.conn, 1)
    add_song(app.state.conn, 2)
    body = call(app, 1, n=1)
    assert body["arc"] is None
    assert body["reason"]


def test_unknown_seed_is_404(app):
    with pytest.raises(HTTPException) as caught:
        call(app, 999)
    assert caught.value.status_code == 404


@pytest.mark.parametrize("params", [
    dict(n=0), dict(n=101), dict(pos=-1), dict(mode="mix"), dict(lift=2),
])
def test_bad_params_are_422(app, params):
    ladder_library(app.state.conn)
    with pytest.raises(HTTPException) as caught:
        call(app, 1, **params)
    assert caught.value.status_code == 422


def plan_route(app):
    for candidate in app.routes:
        if (getattr(candidate, "path", None) == "/api/arc"
                and "POST" in getattr(candidate, "methods", ())):
            return candidate.endpoint
    raise AssertionError("no POST /api/arc")


def post(app, spec, **params):
    from muse.api import ArcPlan
    return plan_route(app)(ArcPlan(spec=spec, **params))


def mood(**over):
    return {
        "version": 1, "kind": "mood",
        "start": {"subject": None, "darkness": 0.9},
        "goal": {"subject": None, "darkness": 0.25},
        **over,
    }


def test_post_seed_spec_round_trips(app):
    seed = ladder_library(app.state.conn)
    sent = request_spec.seed_spec(seed)
    body = post(app, sent, n=6)
    assert body["spec"] == request_spec.check(sent)
    assert body["mode"] == "playlist"
    assert len(body["results"]) == 6
    assert body["results"][0]["track"]["track_rowid"] == body["results"][0]["track_rowid"]


def test_post_seedless_mood_spec_plays(app):
    ladder_library(app.state.conn)
    body = post(app, mood(), n=4)
    assert body["spec"]["seed"] is None
    assert body["results"]


@pytest.mark.parametrize("spec, field", [
    (dict(mood(), version=0), "spec.version"),
    (mood(start={"subject": None, "darkness": 2}), "spec.start.darkness"),
])
def test_post_bad_spec_is_422(app, spec, field):
    ladder_library(app.state.conn)
    with pytest.raises(HTTPException) as caught:
        post(app, spec)
    assert caught.value.status_code == 422
    assert field in caught.value.detail


def test_post_unknown_seed_is_404(app):
    ladder_library(app.state.conn)
    with pytest.raises(HTTPException) as caught:
        post(app, request_spec.seed_spec(999))
    assert caught.value.status_code == 404


def test_post_n_zero_is_422(app):
    ladder_library(app.state.conn)
    with pytest.raises(HTTPException) as caught:
        post(app, request_spec.seed_spec(1), n=0)
    assert caught.value.status_code == 422
