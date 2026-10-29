"""`muse/systemone.py` -- one HTTP client for both curator backends.

`requests.post` is monkeypatched throughout; no network. The client is shared by
the lyric tag batch. `tests/fixtures/systemone_jev.json` and
`tests/fixtures/systemone_laya.json` are the two real recorded responses
(`docs/notes/2026-09-24-systemone-smoke.md`) -- the fixture-parity test
proves both parse through `system_one` the same way.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
import requests

from muse.systemone import HttpSystemOneBackend, SystemOneError, jev_backend, laya_backend

FIXTURES = Path(__file__).parent / "fixtures"

DECLARED_FIELDS = {"type", "score", "confidence", "legend", "probabilities"}


@dataclass
class FakeConfig:
    curator_jev_url: str = "https://api.typesafe.ai"
    laya_url: str = "http://127.0.0.1:8000"
    laya_model: str = "multilingual"
    laya_timeout_ms: int = 10000
    curator_model: str | None = None
    curator_timeout_ms: int = 8000


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._body = body if body is not None else {}

    def json(self):
        return self._body


def _capture_post(monkeypatch, response):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return response

    monkeypatch.setattr(requests, "post", fake_post)
    return calls


def test_posted_url_ends_v1_systemone(monkeypatch):
    calls = _capture_post(monkeypatch, FakeResponse(body={"answers": {"t1": {}}}))
    backend = HttpSystemOneBackend(base_url="http://example.test", timeout_ms=1000)
    backend.system_one({"a": 1}, {"t1": {}})
    assert calls[0]["url"] == "http://example.test/v1/systemone"


def test_model_absent_from_body_when_unset(monkeypatch):
    calls = _capture_post(monkeypatch, FakeResponse(body={"answers": {}}))
    backend = HttpSystemOneBackend(base_url="http://example.test", timeout_ms=1000)
    backend.system_one({}, {})
    assert "model" not in calls[0]["json"]


def test_model_present_in_body_when_set(monkeypatch):
    calls = _capture_post(monkeypatch, FakeResponse(body={"answers": {}}))
    backend = HttpSystemOneBackend(base_url="http://example.test", timeout_ms=1000, model="jev-latest")
    backend.system_one({}, {})
    assert calls[0]["json"]["model"] == "jev-latest"


def test_jev_backend_defaults_model_to_jev_latest(monkeypatch):
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    backend = jev_backend(FakeConfig(curator_model=None))
    assert backend.model == "jev-latest"


def test_jev_backend_honors_configured_model(monkeypatch):
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    backend = jev_backend(FakeConfig(curator_model="jev-1.13.0"))
    assert backend.model == "jev-1.13.0"


def test_laya_backend_names_the_configured_checkpoint():
    backend = laya_backend(FakeConfig())
    assert backend.model == "multilingual"
    assert backend.timeout_ms == 10000


def test_authorization_header_absent_when_no_key(monkeypatch):
    calls = _capture_post(monkeypatch, FakeResponse(body={"answers": {}}))
    backend = HttpSystemOneBackend(base_url="http://example.test", timeout_ms=1000, api_key=None)
    backend.system_one({}, {})
    assert "Authorization" not in calls[0]["headers"]


def test_authorization_header_present_when_key_given(monkeypatch):
    calls = _capture_post(monkeypatch, FakeResponse(body={"answers": {}}))
    backend = HttpSystemOneBackend(base_url="http://example.test", timeout_ms=1000, api_key="secret")
    backend.system_one({}, {})
    assert calls[0]["headers"]["Authorization"] == "Bearer secret"


def test_jev_backend_reads_key_from_env(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "envkey")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    backend = jev_backend(FakeConfig())
    assert backend.api_key == "envkey"


def test_laya_backend_never_sends_a_key():
    backend = laya_backend(FakeConfig())
    assert backend.api_key is None


def _fixture_answers(monkeypatch, fixture_name):
    body = json.loads((FIXTURES / fixture_name).read_text())
    _capture_post(monkeypatch, FakeResponse(body=body))
    backend = HttpSystemOneBackend(base_url="http://example.test", timeout_ms=1000)
    return backend.system_one({}, {})


def test_jev_and_laya_fixtures_parse_through_same_path(monkeypatch):
    jev_answers = _fixture_answers(monkeypatch, "systemone_jev.json")
    assert set(jev_answers) == {"t1", "t2"}
    for key in ("t1", "t2"):
        assert DECLARED_FIELDS <= jev_answers[key].keys()

    laya_answers = _fixture_answers(monkeypatch, "systemone_laya.json")
    assert set(laya_answers) == {"t1", "t2"}
    for key in ("t1", "t2"):
        assert DECLARED_FIELDS <= laya_answers[key].keys()


def test_500_raises_systemoneerror(monkeypatch):
    _capture_post(monkeypatch, FakeResponse(status_code=500, body={"answers": {}}))
    backend = HttpSystemOneBackend(base_url="http://example.test", timeout_ms=1000)
    with pytest.raises(SystemOneError):
        backend.system_one({}, {})


def test_timeout_raises_systemoneerror(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr(requests, "post", fake_post)
    backend = HttpSystemOneBackend(base_url="http://example.test", timeout_ms=1000)
    with pytest.raises(SystemOneError):
        backend.system_one({}, {})


def test_connection_error_raises_systemoneerror(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(requests, "post", fake_post)
    backend = HttpSystemOneBackend(base_url="http://example.test", timeout_ms=1000)
    with pytest.raises(SystemOneError):
        backend.system_one({}, {})


def test_empty_body_raises_systemoneerror(monkeypatch):
    _capture_post(monkeypatch, FakeResponse(body={}))
    backend = HttpSystemOneBackend(base_url="http://example.test", timeout_ms=1000)
    with pytest.raises(SystemOneError):
        backend.system_one({}, {})
