"""`/api/health`'s new `content` key, and the config flag that says whether
this machine is expected to populate the leg at all.

Same shape as tests/test_health_taste.py: no TestClient in this repo, so the
route's endpoint function is pulled off `app.routes` and called directly,
since `/api/health` takes no request parameters.
"""

import asyncio

from muse.api import create_app
from muse.config import Config
from tests.conftest import make_config
from muse.content import CONTENT_EMBED, STUB_CONTENT_MODEL
from muse.embed import pack_vector
from muse.library import content as library_content
from muse.library import jobs as job_rows
from tests.conftest import insert_track


def _config(tmp_path, **overrides):
    return make_config(tmp_path, **overrides)


def _health(app):
    [route] = [r for r in app.routes if getattr(r, "path", None) == "/api/health"]
    return route.endpoint()


def test_content_embed_is_off_unless_asked_for(monkeypatch):
    monkeypatch.delenv("MUSE_CONTENT_EMBED", raising=False)
    assert Config.from_env().content_embed is False


def test_content_embed_turns_on_from_the_environment(monkeypatch):
    monkeypatch.setenv("MUSE_CONTENT_EMBED", "1")
    assert Config.from_env().content_embed is True


def test_health_reports_content_coverage_and_last_batch_at(tmp_path):
    app = create_app(_config(tmp_path))

    async def drive():
        async with app.router.lifespan_context(app):
            conn = app.state.conn
            insert_track(conn, 1)
            insert_track(conn, 2)
            library_content.upsert(
                conn, 1, content_vec=pack_vector([0.5]),
                model=STUB_CONTENT_MODEL, now=900,
            )
            library_content.replace_stats(
                conn, mean_vec=pack_vector([0.5]), sim_lo=-0.2, sim_hi=0.8,
                track_count=1, now=950,
            )
            job_id = job_rows.create(conn, CONTENT_EMBED, now=900)
            job_rows.update(conn, job_id, state=job_rows.DONE, now=1000)
            # A later, failed run -- e.g. a dead universal_reco subprocess --
            # must not shadow the last *successful* batch's timestamp, but it
            # must still surface as the last run so a persistently failing
            # batch (same stale last_batch_at every night) is distinguishable
            # from a healthy one with nothing new to embed.
            failed_job_id = job_rows.create(conn, CONTENT_EMBED, now=1100)
            job_rows.update(conn, failed_job_id, state=job_rows.ERROR, now=1200)
            return _health(app)

    result = asyncio.run(drive())

    assert result["content"] == {
        "playable": 2,
        "embedded": 1,
        "track_count": 1,
        "computed_at": 950,
        "expected_on_this_host": False,
        "last_batch_at": 1000,
        "last_run_at": 1200,
        "last_run_state": "error",
    }


def test_health_reports_no_last_batch_before_any_run(tmp_path):
    app = create_app(_config(tmp_path))

    async def drive():
        async with app.router.lifespan_context(app):
            insert_track(app.state.conn, 1)
            return _health(app)

    result = asyncio.run(drive())

    assert result["content"] == {
        "playable": 1,
        "embedded": 0,
        "track_count": None,
        "computed_at": None,
        "expected_on_this_host": False,
        "last_batch_at": None,
        "last_run_at": None,
        "last_run_state": None,
    }


def test_health_reports_content_expected_when_flag_is_on(tmp_path):
    app = create_app(_config(tmp_path, content_embed=True))

    async def drive():
        async with app.router.lifespan_context(app):
            insert_track(app.state.conn, 1)
            return _health(app)

    result = asyncio.run(drive())

    assert result["content"]["expected_on_this_host"] is True
