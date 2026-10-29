"""`/api/health`'s new `taste` key: coverage and last-retrain-time, added
the same way `embeddings`/`emotion` coverage already are (ticket 01/02).
No `TestClient` dependency in this repo yet -- `tests/test_embed_startup_wiring.py`
already drives the app's lifespan directly for the same reason, though it
only asserts on DB state, not a route's response. Here the route's endpoint
function is pulled off `app.routes` and called directly, since `/api/health`
takes no request parameters.
"""

import asyncio

from muse.api import create_app
from muse.config import Config
from muse.library import jobs as job_rows
from muse.library import taste as library_taste
from muse.taste import TASTE_RETRAIN
from tests.conftest import insert_track


def _config(tmp_path, **overrides):
    defaults = dict(
        root=tmp_path,
        library_db=tmp_path / "library.sqlite3",
        manifest=tmp_path / "manifest.json",
        catalog=tmp_path / "catalog.sqlite3",
        audio_features=tmp_path / "features.sqlite3",
        music_root=tmp_path / "MUSIC",
        art_root=tmp_path / "art",
        web_dist=tmp_path / "web",
        backup_dir=tmp_path / "backups",
        backup_target=None,
        port=8080,
        host="127.0.0.1",
        art_backfill=False,
        embed_batch=False,
        content_embed=False,
        catalog_freeze="2025-11-13",
    )
    defaults.update(overrides)
    return Config(**defaults)


def _health(app):
    [route] = [r for r in app.routes if getattr(r, "path", None) == "/api/health"]
    return route.endpoint()


def test_health_reports_taste_coverage_and_last_retrain_at(tmp_path):
    config = _config(tmp_path)
    app = create_app(config)

    async def drive():
        async with app.router.lifespan_context(app):
            conn = app.state.conn
            insert_track(conn, 1)
            insert_track(conn, 2)
            library_taste.replace_all(
                conn, [(1, b"v" * 4, "stub-als"), (2, None, None)], now=1000
            )
            job_id = job_rows.create(conn, TASTE_RETRAIN, now=900)
            job_rows.update(conn, job_id, state=job_rows.DONE, now=1000)
            return _health(app)

    result = asyncio.run(drive())

    assert result["taste"] == {"playable": 2, "embedded": 1, "last_retrain_at": 1000}


def test_health_reports_no_last_retrain_before_any_run(tmp_path):
    config = _config(tmp_path)
    app = create_app(config)

    async def drive():
        async with app.router.lifespan_context(app):
            insert_track(app.state.conn, 1)
            return _health(app)

    result = asyncio.run(drive())

    assert result["taste"] == {"playable": 1, "embedded": 0, "last_retrain_at": None}
