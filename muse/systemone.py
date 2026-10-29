"""One HTTP client for both curator backends -- hosted Jev and local Laya.

D4: Laya serves the same wire protocol Jev does (`POST /v1/systemone`,
`{state, questions, model}` in, `{model, answers, usage}` out), so the A/B
between them is one class and two base URLs, not two clients that could
quietly drift apart. `jev_backend`/`laya_backend` are the only two
constructors anything should call; everything else in this module is the
shared plumbing they configure.

No retry here -- the spec's retry, if any, belongs to the caller, not to
every request this client makes.
"""

import logging
import time

import requests

log = logging.getLogger(__name__)


class SystemOneError(Exception):
    """The one thing this client raises, for a non-2xx status, a timeout, a
    connection error, or a 200 body with no `answers` key. The curator only
    needs to catch one type."""


class HttpSystemOneBackend:
    """POSTs `{state, questions}` (plus `model` when set) to
    `{base_url}/v1/systemone` and returns `answers`. One class serves both
    Jev and Laya -- see `jev_backend`/`laya_backend` below."""

    def __init__(self, *, base_url, timeout_ms, model=None, api_key=None):
        self.base_url = base_url
        self.timeout_ms = timeout_ms
        self.model = model
        self.api_key = api_key
        self.name = "jev" if api_key is not None else "laya"

    def system_one(self, state: dict, questions: dict) -> dict:
        body = {"state": state, "questions": questions}
        if self.model is not None:
            body["model"] = self.model
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = f"{self.base_url}/v1/systemone"
        started = time.monotonic()
        try:
            response = requests.post(
                url, json=body, headers=headers, timeout=self.timeout_ms / 1000
            )
        except requests.RequestException as error:
            elapsed_ms = (time.monotonic() - started) * 1000
            log.warning(
                "System One %s failed to answer after %.0fms: %s",
                self.base_url, elapsed_ms, type(error).__name__,
            )
            raise SystemOneError(f"{type(error).__name__} calling {self.base_url}") from error
        elapsed_ms = (time.monotonic() - started) * 1000
        log.info(
            "System One %s answered %d in %.0fms",
            self.base_url, response.status_code, elapsed_ms,
        )
        if not response.ok:
            raise SystemOneError(f"{self.base_url} answered {response.status_code}")
        data = response.json()
        if "answers" not in data:
            raise SystemOneError(f"{self.base_url} answered with no 'answers' key")
        return data["answers"]


def jev_backend(config) -> HttpSystemOneBackend:
    """Hosted Jev. Needs a model -- hosted Jev returns 422 without one --
    and a key, read here rather than stored on `Config` (see the comment
    there)."""
    import os

    api_key = os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY")
    return HttpSystemOneBackend(
        base_url=config.curator_jev_url,
        timeout_ms=config.curator_timeout_ms,
        model=config.curator_model or "jev-latest",
        api_key=api_key,
    )


def laya_backend(config) -> HttpSystemOneBackend:
    """Local Laya. No key. `model` is sent because a model-less request
    auto-routes Latin text to `english`, which would misparse Korean/Japanese
    prompts' romanised forms; the configured checkpoint is always named."""
    return HttpSystemOneBackend(
        base_url=config.laya_url,
        timeout_ms=config.laya_timeout_ms,
        model=config.laya_model,
        api_key=None,
    )
