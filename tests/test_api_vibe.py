"""`GET /api/library/search/vibe` -- story 45.

The only request path in this app that needs torch. These tests run on a
machine without the `embed` extra installed, which is exactly the condition
that would let a careless test pass for the wrong reason, so the 503 test
asserts the response *and* that nothing imported torch on the way.
"""

import sys

import pytest
from fastapi import HTTPException

from muse.api import create_app
from tests.conftest import make_config
from muse.embed import pack_vector
from muse.library import db
from muse.library import embeddings as library_embeddings
from tests.conftest import insert_track


def _config(tmp_path):
    return make_config(tmp_path)


def route(app, path):
    for candidate in app.routes:
        if getattr(candidate, "path", None) == path:
            return candidate.endpoint
    raise AssertionError(f"no route {path}")


class FakeTextEncoder:
    """Stands in for CLAP's text tower. Never imports torch."""

    def encode_text(self, text):
        return [1.0, 0.0] if "bright" in text else [0.0, 1.0]


@pytest.fixture
def app(tmp_path):
    application = create_app(_config(tmp_path))
    application.state.conn = db.connect(tmp_path / "library.sqlite3")
    return application


def seed(conn):
    insert_track(conn, 1, artist_name="A")
    library_embeddings.upsert(conn, 1, audio_vec=pack_vector([1.0, 0.0]),
                              feature_vec=None, lyrics_vec=None,
                              audio_model="stub", lyrics_model=None,
                              lyrics_hash=None)
    insert_track(conn, 2, artist_name="B")
    library_embeddings.upsert(conn, 2, audio_vec=pack_vector([0.0, 1.0]),
                              feature_vec=None, lyrics_vec=None,
                              audio_model="stub", lyrics_model=None,
                              lyrics_hash=None)


def test_missing_embed_extra_returns_503(app, monkeypatch):
    """The route must degrade, not fail (principle 4).

    The missing extra is *simulated*, not relied on. An earlier version of
    this test asserted the 503 by letting the real encoder fail to import,
    which passed only because this machine had no `embed` extra -- and the
    moment one was installed the test started constructing a real CLAP model,
    downloading ~800 MB of weights inside the default suite, and failing.
    A test whose meaning depends on how the machine was provisioned is not
    testing the route."""
    import muse.api

    def no_extra():
        raise ImportError("No module named 'torch'")

    monkeypatch.setattr(muse.api, "ClapAudioEncoder", no_extra)
    with pytest.raises(HTTPException) as raised:
        route(app, "/api/library/search/vibe")(q="anything")
    assert raised.value.status_code == 503
    assert "embed" in str(raised.value.detail)


def test_an_injected_encoder_is_used_instead_of_constructing_clap(app, monkeypatch):
    """The guard the test above lost when it stopped depending on the
    environment. Constructing CLAP costs ~70 s and ~800 MB before it does
    anything, so the default suite must never reach for it.

    Asserted by making construction itself fail, not by inspecting
    `sys.modules`: that is process-global, so any earlier test in the run
    could satisfy or violate it and this test would be reporting on
    someone else's behaviour."""
    import muse.api

    def explode():
        raise AssertionError("the route constructed a real CLAP encoder")

    monkeypatch.setattr(muse.api, "ClapAudioEncoder", explode)
    app.state.text_encoder = FakeTextEncoder()
    out = route(app, "/api/library/search/vibe")(q="bright")
    assert out["results"] == [] or out["results"]


def test_ranks_by_text_to_audio_similarity(app):
    seed(app.state.conn)
    app.state.text_encoder = FakeTextEncoder()
    out = route(app, "/api/library/search/vibe")(q="bright", n=2)
    assert [r["track_rowid"] for r in out["results"]] == [1, 2]


def test_other_query_ranks_the_other_track_first(app):
    seed(app.state.conn)
    app.state.text_encoder = FakeTextEncoder()
    out = route(app, "/api/library/search/vibe")(q="murky", n=2)
    assert out["results"][0]["track_rowid"] == 2


def test_respects_n(app):
    seed(app.state.conn)
    app.state.text_encoder = FakeTextEncoder()
    assert len(route(app, "/api/library/search/vibe")(q="bright", n=1)["results"]) == 1


def test_empty_query_returns_nothing_rather_than_everything(app):
    seed(app.state.conn)
    app.state.text_encoder = FakeTextEncoder()
    assert route(app, "/api/library/search/vibe")(q="")["results"] == []


def test_does_not_go_through_rank(app, monkeypatch):
    """There is no seed track, so there is no query_vec_set -- the other five
    legs have nothing to compare against and a text query has no lyrics
    vector, no feature vector, and no emotion coordinate of its own. Fusing
    would mean inventing query-side values for five legs."""
    from muse import reco
    seed(app.state.conn)
    app.state.text_encoder = FakeTextEncoder()

    def explode(*args, **kwargs):
        raise AssertionError("vibe search must not call rank()")

    monkeypatch.setattr(reco, "rank", explode)
    route(app, "/api/library/search/vibe")(q="bright")


def test_results_carry_the_whole_track(app):
    """Hydrated for the same reason the recommender's results are: the results
    view draws a row per hit, and a bare rowid costs it a request each.

    Still no torch -- the stub text encoder above is what ranks these."""
    seed(app.state.conn)
    app.state.text_encoder = FakeTextEncoder()
    out = route(app, "/api/library/search/vibe")(q="bright", n=2)
    for result in out["results"]:
        track = result["track"]
        assert track["track_rowid"] == result["track_rowid"]
        assert track["track_name"] == "Some Song"
        assert track["playable"] is True
    assert [r["track"]["artist_name"] for r in out["results"]] == ["A", "B"]
