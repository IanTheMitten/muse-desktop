"""One MusicBrainz client, shared by everything in this codebase that talks
to MusicBrainz — today `art.py`'s cover lookup, and from this module on,
`ingest.py`'s metadata and language fallback too.

Extracted from two implementations that had quietly diverged: `art.py`'s
`CoverArtArchive` used a session plus an injected `RateLimiter`, retrying
once on a 503 after a 2 s sleep and re-waiting on the limiter;
`clean_lyrics.py`'s module-level `_mb_get` used a global last-request
timestamp instead of an injected limiter, with the same one-retry-after-2s
shape. Both already retried once on a 503 — the divergence was in how the
rate limit was tracked (instance vs. module global) and in the CAA leg
sharing `art.py`'s transport but not MusicBrainz's retry. The retry is kept
here — a transient MusicBrainz rate-limit response is a routine event at
this call volume, not a reason to fail the caller's lookup outright.

That single retry has since grown into `MAX_ATTEMPTS` with a doubling
backoff, because one was measurably not enough: see the constant's comment.
"""

import logging
import threading
import time
import urllib.parse
from collections.abc import Callable
from typing import Protocol

import requests

log = logging.getLogger(__name__)

MB_BASE = "https://musicbrainz.org/ws/2"
MB_USER_AGENT = "muse_intelligence/1.0 ( slingstone7@gmail.com )"
MB_MIN_INTERVAL = 1.1
REQUEST_TIMEOUT = 20
# The first backoff, doubled on each further 503: 2 s, 4 s, 8 s.
BACKOFF_SECONDS = 2
# One try plus three retries. Measured against the live service in
# September 2026, roughly one request in three came back 503, and a record
# cannot be queued without two of these calls succeeding in a row
# (`api._release_for_group` picks the pressing, then reads its tracklist).
# At one retry that pair failed about a fifth of the time; at four attempts
# it is under a thousandth, which is the difference between a browse screen
# that works and one that 502s while a person is looking at it.
MAX_ATTEMPTS = 4
# The ceiling on any single wait, including one MusicBrainz asks for by
# `Retry-After`. A request handler is held open for the whole of this, so the
# worst case has to stay a wait somebody will sit through.
MAX_BACKOFF_SECONDS = 8


class RateLimiter:
    """A floor on the gap between calls. A wall-clock sleep on purpose: this
    runs on a worker thread whose entire job is waiting on a polite API.

    The lock is what makes the floor true rather than approximate. Discovery
    routes run on FastAPI's threadpool alongside the download worker, and two
    unsynchronized callers both read the same `_last`, both conclude no gap
    is owed, and both fire -- 2 req/s against a service that asks for 1.
    """

    def __init__(self, min_interval: float, sleep: Callable[[float], None] = time.sleep):
        self.min_interval = min_interval
        self._sleep = sleep
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            gap = self.min_interval - (time.monotonic() - self._last)
            if gap > 0:
                self._sleep(gap)
            self._last = time.monotonic()


class MusicBrainzLookup(Protocol):
    def get(self, path: str, params: dict) -> dict | None:
        """`GET {MB_BASE}/{path}`, `fmt=json` appended.

        Three answers, two of which are falsy on purpose so that every caller
        written as `if not data:` keeps behaving exactly as it did:

        - a dict — MusicBrainz answered, and here is what it said;
        - `{}` — MusicBrainz answered 404. The thing asked for is not in the
          database. That is an answer, not a failure;
        - `None` — MusicBrainz did not answer: a transport error, a 503
          that survived every retry, or any other non-200.

        The 404 was folded into `None` until a caller needed the difference:
        with both collapsed, "nothing there" and "the service is down"
        reached the browse screen as one answer, and the screen caches the
        first of those for the life of the page.
        """
        ...


class MusicBrainzClient:
    """The real thing. One rate limiter shared across every call through
    this instance — the property a shared client exists to guarantee; two
    independent clients in one process cannot jointly enforce one limit."""

    def __init__(
        self,
        session: requests.Session | None = None,
        limiter: RateLimiter | None = None,
        backoff_sleep: Callable[[float], None] = time.sleep,
    ):
        self._session = session or requests.Session()
        self._limiter = limiter or RateLimiter(MB_MIN_INTERVAL)
        self._backoff_sleep = backoff_sleep

    def get(self, path: str, params: dict) -> dict | None:
        backoff = BACKOFF_SECONDS
        for attempt in range(MAX_ATTEMPTS):
            self._limiter.wait()
            try:
                response = self._session.get(
                    f"{MB_BASE}/{path}",
                    params={**params, "fmt": "json"},
                    headers={"User-Agent": MB_USER_AGENT},
                    timeout=REQUEST_TIMEOUT,
                )
            except requests.RequestException as error:
                # Not retried, unlike a 503. A 503 is the service saying it is
                # busy right now, which is exactly the thing waiting fixes; a
                # transport error is a broken route or a request that already
                # burned REQUEST_TIMEOUT, and repeating it mostly spends the
                # caller's time.
                log.warning(
                    "MusicBrainz %s failed to answer: %s: %s",
                    path, type(error).__name__, error,
                )
                return None
            if response.status_code == 503 and attempt < MAX_ATTEMPTS - 1:
                delay = self._retry_delay(response, backoff)
                log.info(
                    "MusicBrainz %s said busy, waiting %.1fs (attempt %d of %d)",
                    path, delay, attempt + 1, MAX_ATTEMPTS,
                )
                self._backoff_sleep(delay)
                backoff *= 2
                continue
            # A 404 is MusicBrainz answering. Every other non-200 is not: a
            # 500, a 503 that survived every retry and a timeout all mean the
            # same nothing, and none of them says the thing asked for is
            # absent.
            if response.status_code == 404:
                return {}
            if response.status_code != 200:
                # The one place that knows why a lookup came back empty. Every
                # caller above collapses this to `None`, and the browse routes
                # turn that into a bare 502 -- so without this line an
                # intermittent failure on somebody's screen leaves nothing in
                # the journal to tell a rate limit from an outage.
                log.warning(
                    "MusicBrainz %s answered %d after %d attempt(s)",
                    path, response.status_code, attempt + 1,
                )
                return None
            try:
                return response.json()
            except ValueError:
                log.warning("MusicBrainz %s answered 200 with no JSON", path)
                return None
        # Unreachable: the last attempt always returns above. Here so that a
        # future edit to the loop bounds cannot fall out of the function
        # returning an implicit None that reads like a transport failure.
        return None

    @staticmethod
    def _retry_delay(response, backoff: float) -> float:
        """How long to wait before the next attempt.

        MusicBrainz's own `Retry-After` wins when it sends one and it reads as
        a number of seconds -- the server knows how busy it is and this client
        does not. RFC 9110 also permits an HTTP date there, and a value large
        enough to hold a browse request open for minutes is not worth honouring
        either; both fall back to the schedule, which is a correct answer for
        any header this client cannot use.
        """
        raw = (response.headers or {}).get("Retry-After")
        try:
            asked = float(raw)
        except (TypeError, ValueError):
            return backoff
        if asked <= 0:
            return backoff
        return min(asked, MAX_BACKOFF_SECONDS)


def quote(value: str) -> str:
    return urllib.parse.quote(value)
