"""Mood target: the library-relative (valence, arousal) mapping and the
`mood=v,a` parameter of `GET /api/recommend/{track_rowid}`."""

import pytest
from fastapi import HTTPException

from muse import recommend
from muse.embed import pack_vector
from muse.library import embeddings as library_embeddings
from tests.conftest import insert_track
from tests.test_api_recommend import app, route  # noqa: F401  (app is a fixture)

PATH = "/api/recommend/{track_rowid}"


def _vectors(emotions):
    return {i + 1: {"emotion_vec": e} for i, e in enumerate(emotions)}


def _pct(values, p):
    s = sorted(values)
    return s[int(p * (len(s) - 1))]


EMOTIONS = [(-0.8, -0.6), (-0.2, 0.1), (0.0, 0.2), (0.3, 0.5), (0.6, 0.4),
            (0.9, 0.8), (0.1, -0.3)]


def test_zero_mood_is_per_axis_median():
    target = recommend.mood_target(_vectors(EMOTIONS), 0.0, 0.0)
    assert target == (_pct([e[0] for e in EMOTIONS], 0.5),
                      _pct([e[1] for e in EMOTIONS], 0.5))


def test_extreme_mood_hits_p95_valence_p05_arousal():
    target = recommend.mood_target(_vectors(EMOTIONS), 1.0, -1.0)
    assert target == (_pct([e[0] for e in EMOTIONS], 0.95),
                      _pct([e[1] for e in EMOTIONS], 0.05))


def test_no_emotion_gives_none():
    assert recommend.mood_target({}, 0.5, 0.5) is None
    assert recommend.mood_target({1: {"emotion_vec": None}}, 0.5, 0.5) is None


def _seed_emotions(conn, emotions):
    for rowid, emotion in enumerate(emotions, start=1):
        insert_track(conn, rowid, artist_name=f"Artist {rowid}")
        library_embeddings.upsert(
            conn, rowid, audio_vec=pack_vector([1.0, 0.0]), feature_vec=None,
            lyrics_vec=None, audio_model="stub", lyrics_model=None,
            lyrics_hash=None, emotion_vec=pack_vector(list(emotion)))


@pytest.fixture
def spy(monkeypatch):
    calls = []

    def fake(conn, seed, **kwargs):
        calls.append((seed, kwargs))
        return []

    monkeypatch.setattr(recommend, "recommend_for", fake)
    return calls


def test_route_without_mood_passes_int_seed(app, spy):  # noqa: F811
    _seed_emotions(app.state.conn, EMOTIONS)
    route(app, PATH)(1, n=3, exclude="4,5", recent="2")
    seed, kwargs = spy[0]
    assert seed == 1
    assert kwargs == {"n": 3, "exclude": {4, 5}, "recent": [2], "weights": None}


def test_route_with_mood_passes_target_dict(app, spy):  # noqa: F811
    conn = app.state.conn
    _seed_emotions(conn, EMOTIONS)
    route(app, PATH)(1, n=3, exclude="4", mood="0.9,0.9")
    seed, kwargs = spy[0]
    vectors = recommend.load_vectors(conn)
    assert isinstance(seed, dict)
    assert tuple(seed["emotion_vec"]) == recommend.mood_target(vectors, 0.9, 0.9)
    for leg, value in vectors[1].items():
        if leg != "emotion_vec":
            assert seed[leg] == value
    assert {1, 4} <= kwargs["exclude"]


def test_high_valence_candidate_outranks_seed_like_one(app):  # noqa: F811
    conn = app.state.conn
    # Seed, a near-target candidate (2), a seed-like candidate (3), and
    # seven filler tracks that pull the library percentiles high.
    _seed_emotions(conn, [(-0.5, -0.5), (0.9, 0.9), (-0.5, -0.4)]
                   + [(0.8, 0.8)] * 7)
    out = route(app, PATH)(1, n=10, mood="0.9,0.9")
    order = [r["track_rowid"] for r in out["results"]]
    assert order.index(2) < order.index(3)


@pytest.mark.parametrize("mood", ["abc", "2,0", "0.1"])
def test_bad_mood_is_422(app, mood):  # noqa: F811
    _seed_emotions(app.state.conn, EMOTIONS)
    with pytest.raises(HTTPException) as err:
        route(app, PATH)(1, mood=mood)
    assert err.value.status_code == 422


def test_unknown_seed_with_mood_is_404(app):  # noqa: F811
    _seed_emotions(app.state.conn, EMOTIONS)
    with pytest.raises(HTTPException) as err:
        route(app, PATH)(999, mood="0.5,0.5")
    assert err.value.status_code == 404
