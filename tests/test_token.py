from fastapi.testclient import TestClient

from muse.api import create_app

from tests.conftest import insert_track, make_config

H = {"x-muse-token": "s3cret"}


def _client(tmp_path, **kw):
    return TestClient(create_app(make_config(tmp_path, **kw)))


def test_api_requires_token(tmp_path):
    with _client(tmp_path, token="s3cret") as c:
        assert c.get("/api/health").status_code == 401
        assert c.get("/api/health", headers=H).status_code == 200
        assert c.get("/api/health?t=s3cret").status_code == 200
        assert c.get("/api/health", headers={"x-muse-token": "nope"}).status_code == 401
        assert c.get("/").status_code == 200


def _sse_get(app, query):
    """Open the SSE route over raw ASGI and disconnect after the first body chunk;
    TestClient cannot close an endless stream."""
    import asyncio

    async def run():
        first_chunk = asyncio.Event()
        sent = []

        async def receive():
            if sent and any(m["type"] == "http.response.body" for m in sent):
                return {"type": "http.disconnect"}
            await first_chunk.wait()
            return {"type": "http.disconnect"}

        async def send(m):
            sent.append(m)
            if m["type"] == "http.response.body":
                first_chunk.set()

        scope = {"type": "http", "method": "GET", "path": "/api/jobs/stream", "raw_path": b"/api/jobs/stream",
                 "query_string": query.encode(), "headers": [], "scheme": "http", "http_version": "1.1",
                 "server": ("t", 80), "client": ("c", 1), "root_path": ""}
        await asyncio.wait_for(app(scope, receive, send), 10)
        start = next(m for m in sent if m["type"] == "http.response.start")
        return start["status"], dict(start["headers"])

    return asyncio.run(run())


def test_sse_stream_token(tmp_path):
    app = create_app(make_config(tmp_path, token="s3cret"))
    with TestClient(app):
        assert _sse_get(app, "t=wrong")[0] == 401
        status, headers = _sse_get(app, "t=s3cret")
        assert status == 200
        assert headers[b"content-type"].startswith(b"text/event-stream")


def test_no_token_configured_is_open(tmp_path):
    with _client(tmp_path) as c:
        assert c.get("/api/health").status_code == 200


def test_stream_mime_and_range(tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x" * 100)
    app = create_app(make_config(tmp_path, token="s3cret"))
    with TestClient(app) as c:
        insert_track(app.state.conn, 1, file_path=str(f))
        app.state.conn.commit()
        r = c.get("/api/tracks/1/stream?t=s3cret")
        assert r.status_code == 200
        assert r.headers["content-type"] == "audio/mpeg"
        r = c.get("/api/tracks/1/stream?t=s3cret", headers={"Range": "bytes=0-1"})
        assert r.status_code == 206
