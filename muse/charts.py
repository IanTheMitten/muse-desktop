"""Keyless top-songs chart proxy: one Apple RSS fetch per 6 h, shared by every client."""

import logging
import time

import requests

log = logging.getLogger(__name__)

CHART_TTL_S = 6 * 3600
FAILURE_TTL_S = 15 * 60


def chart_url(base_url: str, storefront: str, n: int = 10) -> str:
    return f"{base_url}/api/v2/{storefront}/music/most-played/{n}/songs.json"


def default_fetch(url: str) -> dict:
    response = requests.get(url, timeout=5)
    response.raise_for_status()
    return response.json()


class ChartCache:
    def __init__(self, fetch, base_url: str, storefront: str, *, clock=time.time):
        self._fetch = fetch
        self._url = chart_url(base_url, storefront, 10)
        self._clock = clock
        self._songs: list[dict] | None = None
        self._expires = 0.0

    def songs(self) -> list[dict] | None:
        now = self._clock()
        if now < self._expires:
            return self._songs
        try:
            results = self._fetch(self._url)["feed"]["results"]
            self._songs = [
                {"rank": i, "name": r["name"], "artist_name": r["artistName"], "url": r["url"]}
                for i, r in enumerate(results, 1)
            ]
            self._expires = now + CHART_TTL_S
        except Exception:
            log.warning("chart fetch failed", exc_info=True)
            self._songs = None
            self._expires = now + FAILURE_TTL_S
        return self._songs
