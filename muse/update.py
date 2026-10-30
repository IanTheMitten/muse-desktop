"""The once-per-launch "is there a newer release" check.

Only app tags (`v1.2.3`) count: the worker's `worker-v2` tags never match, so
they cannot read as an app update.
"""

from __future__ import annotations

import importlib.metadata
import logging
import os
import re

import requests

log = logging.getLogger(__name__)

RELEASES_URL = "https://api.github.com/repos/IanTheMitten/muse-desktop/releases/latest"

_TAG = re.compile(r"^v(\d+(?:\.\d+)*)$")


def app_version() -> str:
    env = os.environ.get("MUSE_APP_VERSION")
    if env:
        return env
    try:
        return importlib.metadata.version("muse")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"


def _get(url: str) -> dict:
    resp = requests.get(url, timeout=10, headers={"Accept": "application/vnd.github+json"})
    resp.raise_for_status()
    return resp.json()


def _tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def check(current: str, *, fetch=None) -> dict:
    """`latest` is None when up to date, unparseable, or the fetch failed."""
    none = {"current": current, "latest": None}
    try:
        data = (fetch or _get)(RELEASES_URL)
        match = _TAG.match(data["tag_name"])
        if not match or _tuple(match.group(1)) <= _tuple(current):
            return none
        return {"current": current, "latest": match.group(1), "url": data["html_url"]}
    except Exception as exc:
        log.info("Update check failed: %s", exc)
        return none
