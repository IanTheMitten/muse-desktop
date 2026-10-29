"""`GET /api/recommend/{track_rowid}`.

Same shape as the other API tests here: no TestClient in this repo, so the
endpoint function is pulled off `app.routes` and called directly.
"""

import pytest
from fastapi import HTTPException

from muse.api import create_app
from muse.embed import pack_vector
from muse.library import embeddings as library_embeddings
from muse.library import ratings as library_ratings
from tests.conftest import insert_track


def _config(tmp_path):
    from muse.config import Config
    return Config(
        root=tmp_path,
        library_db=tmp_path / "library.sqlite3",
        manifest=tmp_path / "manifest.json",
        catalog=tmp_path / "catalog.sqlite3",
        audio_features=tmp_path / "features.sqlite3",
        music_root=tmp_path / "MUSIC",
        art_root=tmp_path / "art",
        web_dist=tmp_path / "web",
        backup_dir=tmp_path / "backups",
        backup_target=None,
        port=8080,
        host="127.0.0.1",
        art_backfill=False,
        embed_batch=False,
        content_embed=False,
        catalog_freeze="2025-11-13",
    )


def route(app, path):
    for candidate in app.routes:
        if getattr(candidate, "path", None) == path:
            return candidate.endpoint
    raise AssertionError(f"no route {path}")


@pytest.fixture
def app(tmp_path):
    application = create_app(_config(tmp_path))
    from muse.library import db
    application.state.conn = db.connect(tmp_path / "library.sqlite3")
    return application


def seed_library(conn, count=6):
    for rowid in range(1, count + 1):
        insert_track(conn, rowid, artist_name=f"Artist {rowid}")
        library_embeddings.upsert(
            conn, rowid,
            audio_vec=pack_vector([1.0, float(rowid) / 100.0]),
            feature_vec=None, lyrics_vec=None,
            audio_model="stub", lyrics_model=None, lyrics_hash=None)


def test_returns_results_with_reasons(app):
    seed_library(app.state.conn)
    out = route(app, "/api/recommend/{track_rowid}")(1, n=3)
    assert len(out["results"]) == 3
    assert "legs" in out["results"][0]["reasons"]


def test_seed_is_never_recommended(app):
    seed_library(app.state.conn)
    out = route(app, "/api/recommend/{track_rowid}")(1, n=5)
    assert 1 not in [r["track_rowid"] for r in out["results"]]


def test_exclude_removes_tracks(app):
    """This is how radio works -- there is no radio endpoint. The client owns
    the queue and passes back what it has already played."""
    seed_library(app.state.conn)
    out = route(app, "/api/recommend/{track_rowid}")(1, n=2, exclude="2,3")
    assert {2, 3}.isdisjoint(r["track_rowid"] for r in out["results"])


def test_unknown_track_404s(app):
    seed_library(app.state.conn)
    with pytest.raises(HTTPException) as raised:
        route(app, "/api/recommend/{track_rowid}")(999)
    assert raised.value.status_code == 404


def test_weight_overrides_change_the_ordering(app):
    """Story 38. Asking for more like this sound has to actually move
    something, not merely be accepted."""
    conn = app.state.conn
    insert_track(conn, 1, artist_name="A")
    library_embeddings.upsert(conn, 1, audio_vec=pack_vector([1.0, 0.0]),
                              feature_vec=pack_vector([1.0, 0.0]),
                              lyrics_vec=None, audio_model="stub",
                              lyrics_model=None, lyrics_hash=None)
    insert_track(conn, 2, artist_name="B")   # audio twin, feature opposite
    library_embeddings.upsert(conn, 2, audio_vec=pack_vector([1.0, 0.0]),
                              feature_vec=pack_vector([0.0, 1.0]),
                              lyrics_vec=None, audio_model="stub",
                              lyrics_model=None, lyrics_hash=None)
    insert_track(conn, 3, artist_name="C")   # feature twin, audio opposite
    library_embeddings.upsert(conn, 3, audio_vec=pack_vector([0.0, 1.0]),
                              feature_vec=pack_vector([1.0, 0.0]),
                              lyrics_vec=None, audio_model="stub",
                              lyrics_model=None, lyrics_hash=None)
    endpoint = route(app, "/api/recommend/{track_rowid}")
    by_sound = endpoint(1, n=1, w_audio=1.0, w_feat=0.0)
    by_features = endpoint(1, n=1, w_audio=0.0, w_feat=1.0)
    assert by_sound["results"][0]["track_rowid"] == 2
    assert by_features["results"][0]["track_rowid"] == 3


def test_no_independent_audio_leg_weights_are_accepted(app):
    """ADR 0004 clause 1 at the API boundary: there is one w_audio, and no
    way to weight CLAP and MERT separately from outside."""
    import inspect
    params = inspect.signature(route(app, "/api/recommend/{track_rowid}")).parameters
    assert "w_clap" not in params
    assert "w_content" not in params
    assert "w_audio" in params


def test_a_missing_leg_never_errors(app):
    """Principle 4. No content_stats, no taste rows, no lyrics anywhere --
    each narrows the evidence and none of them is a failure."""
    seed_library(app.state.conn)
    out = route(app, "/api/recommend/{track_rowid}")(1, n=3)
    assert out["results"]
    assert out["results"][0]["reasons"]["content_used"] is False


def test_thumbs_down_track_is_not_offered(app):
    seed_library(app.state.conn)
    library_ratings.set_rating(app.state.conn, 2, -1)
    out = route(app, "/api/recommend/{track_rowid}")(1, n=5)
    assert 2 not in [r["track_rowid"] for r in out["results"]]


def test_offers_are_recorded(app):
    """O2's decay counts offers, so the endpoint has to be what records
    them."""
    from muse.library import offers
    seed_library(app.state.conn)
    route(app, "/api/recommend/{track_rowid}")(1, n=3)
    assert len(offers.counts(app.state.conn)) == 3


def test_scores_descend_when_no_constraint_intervenes(app):
    """`seed_library` gives every track its own artist, so artist spacing
    never fires and the sequence degenerates to a plain ranking.

    Stated that way on purpose. This caught a freshness boost re-applied once
    per pick -- which left scores *ascending* while the ordering still looked
    musically right -- but descending scores are not an invariant of the
    endpoint in general; see the test below."""
    seed_library(app.state.conn, count=8)
    out = route(app, "/api/recommend/{track_rowid}")(1, n=6)
    scores = [r["score"] for r in out["results"]]
    assert scores == sorted(scores, reverse=True)


def test_artist_spacing_may_defer_a_higher_scoring_track(app):
    """The sequence is spacing-constrained, not sorted. Track 3 outscores
    track 4 but shares an artist with track 2, so it cannot sit second -- and
    a later position legitimately holds a higher score. That is the whole
    point of applying spacing as a filter rather than a weight.

    Observed on the real library, where position five outscored positions two
    through four because its artist had just played."""
    conn = app.state.conn
    vectors = {1: [1.0, 0.0], 2: [1.0, 0.0], 3: [1.0, 0.01], 4: [0.0, 1.0]}
    for rowid, artist in ((1, "Seed"), (2, "Twin"), (3, "Twin"), (4, "Other")):
        insert_track(conn, rowid, artist_name=artist)
        library_embeddings.upsert(
            conn, rowid, audio_vec=pack_vector(vectors[rowid]),
            feature_vec=None, lyrics_vec=None, audio_model="stub",
            lyrics_model=None, lyrics_hash=None)

    out = route(app, "/api/recommend/{track_rowid}")(1, n=3)["results"]
    order = [r["track_rowid"] for r in out]
    assert order[:2] == [2, 4], order
    assert 3 in order
    deferred = next(r for r in out if r["track_rowid"] == 3)
    intervening = next(r for r in out if r["track_rowid"] == 4)
    assert deferred["score"] > intervening["score"]


def test_scores_never_exceed_one_plus_the_freshness_boost(app):
    from muse.recommend import FRESHNESS_MAX
    seed_library(app.state.conn, count=8)
    out = route(app, "/api/recommend/{track_rowid}")(1, n=6)
    assert max(r["score"] for r in out["results"]) <= 1.0 + FRESHNESS_MAX


def test_results_carry_the_whole_track(app):
    """Story 28's play-next and story 35's radio both render a row per result.
    A bare rowid would cost the client one extra request per recommendation --
    twenty on a radio block -- before it can draw anything."""
    seed_library(app.state.conn)
    out = route(app, "/api/recommend/{track_rowid}")(1, n=3)
    for result in out["results"]:
        track = result["track"]
        assert track["track_rowid"] == result["track_rowid"]
        assert track["track_name"] == "Some Song"
        assert track["artist_name"] == f"Artist {result['track_rowid']}"
        assert track["playable"] is True


def test_hydration_is_one_query_not_one_per_result(app, monkeypatch):
    """The point of hydrating here is to remove round trips, so doing it with
    a lookup per result would trade an HTTP N+1 for a SQL one."""
    from muse.library import queries
    seed_library(app.state.conn, count=8)
    calls = []
    original = queries.tracks_by_rowid

    def counted(conn, rowids):
        calls.append(list(rowids))
        return original(conn, rowids)

    monkeypatch.setattr(queries, "tracks_by_rowid", counted)
    out = route(app, "/api/recommend/{track_rowid}")(1, n=6)

    assert len(out["results"]) == 6
    assert calls == [[r["track_rowid"] for r in out["results"]]]


def test_a_result_with_no_track_row_is_dropped_not_nulled(app):
    """`tracks_by_rowid` drops what it cannot find, and hydration follows it:
    a scored row carrying a null track is something every caller would have to
    special-case, and there is nothing to draw for it.

    Driven through the helper rather than the endpoint because every table
    that could name a phantom rowid -- embeddings, offers, ratings -- has a
    foreign key to `tracks`, so no request can reach this state today. The
    branch exists so that if one ever does, the shape stays sound."""
    from muse.api import _with_tracks
    seed_library(app.state.conn)

    hydrated = _with_tracks(app.state.conn, [
        {"track_rowid": 2, "score": 0.9},
        {"track_rowid": 999, "score": 0.8},
    ])

    assert [r["track_rowid"] for r in hydrated] == [2]
    assert hydrated[0]["score"] == 0.9
