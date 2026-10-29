"""Keyless chart proxy (muse/charts.py) and its route. Test-first."""

import pytest
from fastapi.testclient import TestClient

from muse.api import create_app
from muse.charts import CHART_TTL_S, FAILURE_TTL_S, ChartCache, chart_url
from tests.conftest import make_config
from tests.conftest import insert_track

PAYLOAD = {"feed": {"results": [{"name": "Pray", "artistName": "Justin Bieber", "url": "u"}]}}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(fetch):
    clock = Clock()
    return ChartCache(fetch, "https://h", "us", clock=clock), clock


def counting(result=PAYLOAD, error=None):
    calls = []

    def fetch(url):
        calls.append(url)
        if error:
            raise error
        return result

    return fetch, calls


def test_songs_parsed():
    fetch, _ = counting()
    cache, _ = make(fetch)
    assert cache.songs() == [
        {"rank": 1, "name": "Pray", "artist_name": "Justin Bieber", "url": "u"}]


def test_second_call_within_ttl_is_cached():
    fetch, calls = counting()
    cache, clock = make(fetch)
    cache.songs()
    clock.t += CHART_TTL_S - 1
    cache.songs()
    assert len(calls) == 1


def test_refetch_after_ttl():
    fetch, calls = counting()
    cache, clock = make(fetch)
    cache.songs()
    clock.t += CHART_TTL_S + 1
    cache.songs()
    assert len(calls) == 2


def test_failure_returns_none_and_backs_off():
    fetch, calls = counting(error=RuntimeError("down"))
    cache, clock = make(fetch)
    assert cache.songs() is None
    clock.t += FAILURE_TTL_S - 1
    assert cache.songs() is None
    assert len(calls) == 1


def test_malformed_returns_none():
    fetch, _ = counting(result={"nope": {}})
    cache, _ = make(fetch)
    assert cache.songs() is None


def test_chart_url():
    assert chart_url("https://h", "kr", 10) == "https://h/api/v2/kr/music/most-played/10/songs.json"


def app_client(tmp_path, fetch):
    config = make_config(tmp_path)
    return create_app(config, chart_fetch=fetch)


def test_route_matches_library_case_insensitively(tmp_path):
    app = app_client(tmp_path, lambda url: {"feed": {"results": [
        {"name": "pray", "artistName": "JUSTIN BIEBER", "url": "u"},
        {"name": "Other", "artistName": "Nobody", "url": "v"}]}})
    with TestClient(app) as c:
        insert_track(app.state.conn, 1, track_name="Pray", artist_name="Justin Bieber")
        app.state.conn.commit()
        songs = c.get("/api/charts/top").json()["songs"]
    assert songs[0]["track"]["track_rowid"] == 1
    assert songs[1]["track"] is None


def test_route_empty_when_fetch_raises(tmp_path):
    def boom(url):
        raise RuntimeError("down")

    with TestClient(app_client(tmp_path, boom)) as c:
        r = c.get("/api/charts/top")
    assert r.status_code == 200
    assert r.json() == {"songs": []}
