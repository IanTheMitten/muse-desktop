"""`POST /api/tracks/align`, `PUT /api/tracks/{id}/lyrics/lrc` and the synced
route's `timing_lines` (red until Task 12). Same direct-call pattern as
`tests/test_api_lyrics_synced.py`."""

import asyncio
import inspect

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from muse.api import AlignRequest, TappedTimings, create_app
from muse.library import db
from muse.library import jobs as job_rows
from tests.conftest import insert_track, make_config

SYNCED_LRC = "[00:04.00] one\n[00:08.00] two\n[00:09.00] three\n"


def route(app, path):
    for candidate in app.routes:
        if getattr(candidate, "path", None) == path:
            return candidate.endpoint
    raise AssertionError(f"no route {path}")


async def call(fn, *args):
    out = fn(*args)
    return await out if inspect.isawaitable(out) else out


class FakeWorker:
    def __init__(self):
        self.calls = []

    def begin(self):
        pass

    def request(self, op, **args):
        self.calls.append((op, args))
        return {"starts": [1.0, 2.0, 3.0]}


def make_app(tmp_path, **overrides):
    config = make_config(tmp_path, **overrides)
    app = create_app(config)
    app.state.config = config  # set by the lifespan, which tests don't run
    app.state.conn = db.connect(tmp_path / "library.sqlite3")
    app.state.worker = FakeWorker()
    return app


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path, embed_batch=True)


def _seed(app, tmp_path, rowid=1, *, lrc=None, lyrics="one\ntwo\nthree"):
    insert_track(app.state.conn, rowid)
    base = tmp_path / "MUSIC" / f"{rowid}"
    base.parent.mkdir(parents=True, exist_ok=True)
    file_path = base.with_suffix(".m4a")
    file_path.write_bytes(b"x")
    lyrics_path = base.with_suffix(".lyrics.txt")
    if lyrics is not None:
        lyrics_path.write_text(lyrics, encoding="utf-8")
    if lrc is not None:
        (tmp_path / "lyrics").mkdir(exist_ok=True)
        (tmp_path / "lyrics" / f"{rowid}.lrc").write_text(lrc, encoding="utf-8")
    app.state.conn.execute(
        "UPDATE tracks SET file_path = ?, lyrics_path = ? WHERE track_rowid = ?",
        (str(file_path), str(lyrics_path) if lyrics is not None else None, rowid),
    )
    app.state.conn.commit()
    return str(file_path)


ALIGN = "/api/tracks/align"
PUT_LRC = "/api/tracks/{track_rowid}/lyrics/lrc"
SYNCED = "/api/tracks/{track_rowid}/lyrics/synced"


async def test_align_without_smart_features_is_503(tmp_path):
    app = make_app(tmp_path)  # embed_batch off, no installed.json
    _seed(app, tmp_path)
    with pytest.raises(HTTPException) as err:
        await call(route(app, ALIGN), AlignRequest(track_rowids=[1]))
    assert err.value.status_code == 503
    assert err.value.detail == "Auto-align needs smart features"


async def test_align_while_aligning_is_409(app, tmp_path):
    _seed(app, tmp_path)
    job_rows.create(app.state.conn, "align", {})
    with pytest.raises(HTTPException) as err:
        await call(route(app, ALIGN), AlignRequest(track_rowids=[1]))
    assert err.value.status_code == 409
    assert err.value.detail == "Already aligning other songs"


async def test_track_without_lyrics_is_skipped(app, tmp_path):
    _seed(app, tmp_path, lyrics=None)
    out = await call(route(app, ALIGN), AlignRequest(track_rowids=[1]))
    assert out["skipped"] == [{"track_rowid": 1, "reason": "no_lyrics"}]
    assert out["job_id"] is None


async def test_track_with_current_lrc_is_skipped_as_synced(app, tmp_path):
    _seed(app, tmp_path, lrc=SYNCED_LRC)
    out = await call(route(app, ALIGN), AlignRequest(track_rowids=[1]))
    assert out["skipped"] == [{"track_rowid": 1, "reason": "synced"}]
    assert out["job_id"] is None


async def test_replace_aligns_a_synced_track(app, tmp_path):
    file_path = _seed(app, tmp_path, lrc=SYNCED_LRC)
    out = await call(route(app, ALIGN), AlignRequest(track_rowids=[1], replace=True))
    await asyncio.gather(*app.state.align_tasks)
    assert out["skipped"] == []
    assert job_rows.get(app.state.conn, out["job_id"])["payload"]["aligned"] == [1]
    assert app.state.worker.calls == [
        ("align", {"path": file_path, "lines": ["one", "two", "three"]})]


async def test_put_lrc_wrong_count_is_400(app, tmp_path):
    _seed(app, tmp_path)
    with pytest.raises(HTTPException) as err:
        await call(route(app, PUT_LRC), 1, TappedTimings(starts_ms=[1000, 2000]))
    assert err.value.status_code == 400
    assert err.value.detail == "Timings do not match the lyrics"


async def test_put_lrc_decreasing_is_400(app, tmp_path):
    _seed(app, tmp_path)
    with pytest.raises(HTTPException) as err:
        await call(route(app, PUT_LRC), 1, TappedTimings(starts_ms=[1000, 3000, 2000]))
    assert err.value.status_code == 400
    assert err.value.detail == "Timings must not go backwards"


def test_tapped_timings_negative_rejected():
    with pytest.raises(ValidationError):
        TappedTimings(starts_ms=[-1, 0])


async def test_put_lrc_unknown_track_is_404(app, tmp_path):
    with pytest.raises(HTTPException) as err:
        await call(route(app, PUT_LRC), 99, TappedTimings(starts_ms=[1000]))
    assert err.value.status_code == 404


async def test_put_lrc_good_makes_track_synced(app, tmp_path):
    _seed(app, tmp_path)
    out = await call(route(app, PUT_LRC), 1, TappedTimings(starts_ms=[1000, 2000, 3000]))
    assert out == {"ok": True}
    synced = await call(route(app, SYNCED), 1)
    assert synced["synced"] is True
    assert synced["timing_lines"] == ["one", "two", "three"]


async def test_synced_route_on_plain_track_has_timing_lines(app, tmp_path):
    _seed(app, tmp_path)
    synced = await call(route(app, SYNCED), 1)
    assert synced["synced"] is False
    assert synced["timing_lines"] == ["one", "two", "three"]
