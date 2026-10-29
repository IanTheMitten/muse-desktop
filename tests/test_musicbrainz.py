import threading
import time

import pytest
import requests

from muse.musicbrainz import (
    MAX_ATTEMPTS,
    MAX_BACKOFF_SECONDS,
    MusicBrainzClient,
    RateLimiter,
)


class FakeResponse:
    def __init__(self, status_code, json_data=None, headers=None):
        self.status_code = status_code
        self._json = json_data or {}
        # A real `requests.Response` always carries headers; the 503 path
        # reads `Retry-After` off them.
        self.headers = headers or {}

    def json(self):
        return self._json


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params, headers, timeout):
        self.calls.append((url, params, headers, timeout))
        return self._responses.pop(0)


def test_get_returns_json_on_200():
    session = FakeSession([FakeResponse(200, {"ok": True})])
    client = MusicBrainzClient(session=session, limiter=RateLimiter(0, sleep=lambda s: None))
    assert client.get("isrc/ABC123", {"inc": "releases"}) == {"ok": True}
    url, params, headers, _ = session.calls[0]
    assert url == "https://musicbrainz.org/ws/2/isrc/ABC123"
    assert params["fmt"] == "json"
    assert "User-Agent" in headers


def test_get_answers_an_empty_dict_on_404():
    """A 404 is MusicBrainz answering: the thing asked for is not in the
    database. Falsy, so every `if not data:` caller is unchanged, but not
    `None` -- a 404 is the answer a caller must tell apart from silence."""
    session = FakeSession([FakeResponse(404)])
    client = MusicBrainzClient(session=session, limiter=RateLimiter(0, sleep=lambda s: None))
    answer = client.get("isrc/nope", {})
    assert answer == {}
    assert answer is not None


def test_get_returns_none_on_a_server_error():
    """Every non-200 that is not a 404 is silence: a 500 says nothing about
    whether the thing asked for exists."""
    session = FakeSession([FakeResponse(500)])
    client = MusicBrainzClient(session=session, limiter=RateLimiter(0, sleep=lambda s: None))
    assert client.get("isrc/ABC123", {}) is None


def test_get_returns_none_when_the_transport_fails():
    class Exploding:
        calls: list = []

        def get(self, url, params, headers, timeout):
            raise requests.ConnectionError("no route to host")

    client = MusicBrainzClient(
        session=Exploding(), limiter=RateLimiter(0, sleep=lambda s: None)
    )
    assert client.get("isrc/ABC123", {}) is None


def test_get_retries_once_on_503_then_succeeds():
    session = FakeSession([FakeResponse(503), FakeResponse(200, {"ok": True})])
    sleeps = []
    client = MusicBrainzClient(
        session=session,
        limiter=RateLimiter(0, sleep=lambda s: None),
        backoff_sleep=lambda s: sleeps.append(s),
    )
    assert client.get("isrc/ABC123", {}) == {"ok": True}
    assert len(session.calls) == 2
    assert sleeps == [2]


def test_get_backs_off_further_on_each_repeated_503():
    """MusicBrainz 503s roughly one request in three at this call volume, and
    two of its calls in a row are what `_release_for_group` needs before a
    record can be queued. One retry at a flat 2 s left that pair failing about
    a fifth of the time, and every one of those failures reached the browse
    screen as a 502. The gap grows between attempts rather than repeating,
    because a busy server that just refused is likelier to refuse again 2 s
    later than 8 s later."""
    session = FakeSession([
        FakeResponse(503),
        FakeResponse(503),
        FakeResponse(503),
        FakeResponse(200, {"ok": True}),
    ])
    sleeps = []
    client = MusicBrainzClient(
        session=session,
        limiter=RateLimiter(0, sleep=lambda s: None),
        backoff_sleep=lambda s: sleeps.append(s),
    )
    assert client.get("isrc/ABC123", {}) == {"ok": True}
    assert len(session.calls) == 4
    assert sleeps == [2, 4, 8]


def test_get_gives_up_after_the_last_attempt_503s():
    """Bounded, not indefinite: a person is waiting on the browse screen, and
    `MAX_ATTEMPTS` is what keeps the worst case a wait rather than a hang."""
    session = FakeSession([FakeResponse(503) for _ in range(MAX_ATTEMPTS)])
    client = MusicBrainzClient(
        session=session,
        limiter=RateLimiter(0, sleep=lambda s: None),
        backoff_sleep=lambda s: None,
    )
    assert client.get("isrc/ABC123", {}) is None
    assert len(session.calls) == MAX_ATTEMPTS


def test_get_honours_retry_after_when_musicbrainz_sends_one():
    """The server saying how long to wait beats this client guessing."""
    session = FakeSession([
        FakeResponse(503, headers={"Retry-After": "5"}),
        FakeResponse(200, {"ok": True}),
    ])
    sleeps = []
    client = MusicBrainzClient(
        session=session,
        limiter=RateLimiter(0, sleep=lambda s: None),
        backoff_sleep=lambda s: sleeps.append(s),
    )
    assert client.get("isrc/ABC123", {}) == {"ok": True}
    assert sleeps == [5]


def test_a_long_retry_after_is_capped():
    """`Retry-After` is a hint from a busy server, not a licence to hold a
    request handler open for minutes. Past the cap this client stops waiting
    on the header and falls back to its own schedule."""
    session = FakeSession([
        FakeResponse(503, headers={"Retry-After": "600"}),
        FakeResponse(200, {"ok": True}),
    ])
    sleeps = []
    client = MusicBrainzClient(
        session=session,
        limiter=RateLimiter(0, sleep=lambda s: None),
        backoff_sleep=lambda s: sleeps.append(s),
    )
    assert client.get("isrc/ABC123", {}) == {"ok": True}
    assert sleeps == [MAX_BACKOFF_SECONDS]


def test_an_unparseable_retry_after_falls_back_to_the_schedule():
    """`Retry-After` is also allowed to be an HTTP date. Rather than parse one,
    fall back -- the schedule is a correct answer for every value this client
    cannot read as seconds."""
    session = FakeSession([
        FakeResponse(503, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
        FakeResponse(200, {"ok": True}),
    ])
    sleeps = []
    client = MusicBrainzClient(
        session=session,
        limiter=RateLimiter(0, sleep=lambda s: None),
        backoff_sleep=lambda s: sleeps.append(s),
    )
    assert client.get("isrc/ABC123", {}) == {"ok": True}
    assert sleeps == [2]


def test_rate_limiter_waits_between_calls():
    sleeps = []
    limiter = RateLimiter(1.1, sleep=lambda s: sleeps.append(s))
    limiter.wait()
    limiter.wait()
    assert sleeps == [pytest.approx(1.1, abs=0.05)]


def test_two_threads_cannot_both_skip_the_wait():
    """The lock must cover read-sleep-write as one critical section: a second
    caller must not begin its own gap computation while the first is still
    inside its sleep. Without the lock, the second caller reads the same
    stale `_last` the first read, computes its own gap, and starts sleeping
    concurrently -- two requests fire close together instead of one clean
    after another.

    This is deliberately not a "did sleep get called twice" check: with the
    injected `sleep` never consuming real time, an unlocked `wait()` also
    calls `sleep` exactly twice, so that assertion passes even with no lock
    at all. Instead this test holds the first caller inside its `sleep` on a
    controllable gate and asserts the second caller cannot reach its own
    `sleep` call until the first has fully released the critical section.
    """
    order: list[str] = []
    first_entered = threading.Event()
    release_first = threading.Event()

    def sleep(gap: float) -> None:
        order.append("start")
        first_entered.set()
        release_first.wait(timeout=2)
        order.append("end")

    limiter = RateLimiter(1.0, sleep=sleep)
    limiter.wait()  # prime `_last`; gap is deeply negative on this first-ever
    # call (there is no prior `_last`), so `sleep` is not invoked here and
    # `order` stays empty.

    first = threading.Thread(target=limiter.wait)
    first.start()
    assert first_entered.wait(timeout=2), "first caller never reached its sleep"

    second = threading.Thread(target=limiter.wait)
    second.start()
    # Give an unsynchronized second caller every chance to race ahead.
    second.join(timeout=0.2)

    assert order == ["start"], (
        "second caller must not begin its own wait while the first is still "
        f"mid-sleep -- got order={order!r}"
    )

    release_first.set()
    first.join()
    second.join()
    assert order == ["start", "end", "start", "end"]
