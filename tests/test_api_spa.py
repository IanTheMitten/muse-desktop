"""What the catch-all is allowed to swallow.

The SPA route is registered last so a deep link pasted into the phone works and
a reload on /artists does not 404. The cost of a catch-all is that it also
answers paths nobody meant it to: an `/api` route that does not exist -- an old
server against a new bundle, a typo, a renamed endpoint -- fell through to it
and came back as `200 text/html`. The client asked for JSON, got the shell, and
reported `Unexpected token '<'` on three screens at once, which names neither
the path nor the fact that it is missing.

So the catch-all draws a line at `/api`: below it, a missing route is a 404.
"""

import pytest
from fastapi.testclient import TestClient

from muse.api import create_app
from tests.conftest import make_config


@pytest.fixture
def client(tmp_path):
    dist = tmp_path / "web"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>shell</title>")
    config = make_config(tmp_path, web_dist=dist)
    with TestClient(create_app(config)) as test_client:
        yield test_client


@pytest.mark.parametrize(
    "path",
    [
        # The shape a newer bundle asks for from an older server: a route that
        # is spelled like ours and simply is not here yet.
        "/api/library/playlists",
        "/api/catalog/tracks",
        "/api/nothing-here",
        "/api",
    ],
)
def test_unknown_api_path_is_a_404_not_the_shell(client, path):
    response = client.get(path)

    assert response.status_code == 404
    assert "text/html" not in response.headers["content-type"]


def test_deep_links_still_get_the_shell(client):
    for path in ["/", "/library", "/artists/Ariana%20Grande", "/albums/26288"]:
        response = client.get(path)
        assert response.status_code == 200
        assert "<title>shell</title>" in response.text


def test_a_route_that_does_exist_is_untouched(client):
    assert client.get("/api/health").status_code == 200
