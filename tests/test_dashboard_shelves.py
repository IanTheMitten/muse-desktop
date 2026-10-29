"""Home shelves on the dashboard payload: On repeat and Recently added."""

from muse.library import insights
from tests.conftest import insert_track
from tests.test_dashboard import DAY_MS, play

NOW = 1_800_000_000_000


def test_on_repeat_counts_recent_plays_only(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    insert_track(conn, 3)
    insert_track(conn, 4, status="missing_file")
    insert_track(conn, 5)
    for t in (NOW - DAY_MS, NOW - 2 * DAY_MS):
        play(conn, 1, t)
    for t in (NOW - DAY_MS, NOW - 2 * DAY_MS, NOW - 3 * DAY_MS):
        play(conn, 2, t)
    play(conn, 3, NOW - DAY_MS)  # once only
    play(conn, 4, NOW - DAY_MS)
    play(conn, 4, NOW - 2 * DAY_MS)
    play(conn, 5, NOW - 8 * DAY_MS)  # older than 7 days
    play(conn, 5, NOW - 9 * DAY_MS)
    play(conn, 5, NOW - DAY_MS)

    shelf = insights.dashboard(conn, NOW)["on_repeat"]

    assert [(t["track_rowid"], t["plays"]) for t in shelf] == [(2, 3), (1, 2)]


def test_recently_added_albums_newest_first_capped_at_eight(conn):
    for i in range(10):
        insert_track(conn, i + 1, album_rowid=100 + i, added_at=1000 + i)
    insert_track(conn, 11, album_rowid=100, added_at=5000)  # bumps album 100 to top

    shelf = insights.dashboard(conn, NOW)["recently_added"]

    assert len(shelf) == 8
    assert [a["album_rowid"] for a in shelf] == [100, 109, 108, 107, 106, 105, 104, 103]


def test_existing_keys_survive(conn):
    insert_track(conn, 1)
    board = insights.dashboard(conn, NOW)
    assert {"resume", "sounds_like", "stats", "mixes"} <= set(board)
    assert len(board["mixes"]) == 4


def test_startup_warms_the_home_shelf_scores(tmp_path, monkeypatch):
    """The first Home load must not pay for the scoring: startup does."""
    import threading

    from fastapi.testclient import TestClient

    from muse import nearest
    from muse.api import create_app
    from muse.config import Config

    warmed = threading.Event()
    monkeypatch.setattr(nearest, "recommended_albums",
                        lambda *a, **k: warmed.set() or [])
    config = Config(
        root=tmp_path, library_db=tmp_path / "library.sqlite3",
        manifest=tmp_path / "manifest.json", catalog=tmp_path / "catalog.sqlite3",
        audio_features=tmp_path / "features.sqlite3",
        music_root=tmp_path / "MUSIC", art_root=tmp_path / "art",
        web_dist=tmp_path / "web", backup_dir=tmp_path / "backups",
        backup_target=None, port=8080, host="127.0.0.1",
        art_backfill=False, embed_batch=False, content_embed=False,
        catalog_freeze="2025-11-13",
    )
    with TestClient(create_app(config)):
        assert warmed.wait(5)
