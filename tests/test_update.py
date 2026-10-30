import asyncio

from muse import update
from muse.api import create_app
from tests.conftest import make_config


def _release(tag):
    return lambda url: {"tag_name": tag, "html_url": f"https://example.test/{tag}"}


def test_newer_tag_reports_latest_and_url():
    assert update.check("0.1.0", fetch=_release("v0.2.0")) == {
        "current": "0.1.0", "latest": "0.2.0", "url": "https://example.test/v0.2.0"}


def test_same_tag_is_up_to_date():
    assert update.check("0.2.0", fetch=_release("v0.2.0")) == {
        "current": "0.2.0", "latest": None}


def test_worker_tag_is_ignored():
    assert update.check("0.1.0", fetch=_release("worker-v2"))["latest"] is None


def test_failing_fetch_is_silent():
    def boom(url):
        raise OSError("offline")

    assert update.check("0.1.0", fetch=boom) == {"current": "0.1.0", "latest": None}


def test_route_answers_the_background_check(tmp_path):
    app = create_app(make_config(tmp_path, update_check=True),
                     update_fetch=_release("v999.0.0"))
    [route] = [r for r in app.routes if getattr(r, "path", None) == "/api/update"]

    async def drive():
        async with app.router.lifespan_context(app):
            for _ in range(40):
                if route.endpoint()["latest"]:
                    break
                await asyncio.sleep(0.05)
            return route.endpoint()

    got = asyncio.run(drive())

    assert got["latest"] == "999.0.0"
    assert got["url"] == "https://example.test/v999.0.0"
