"""`GET /api/tracks/{track_rowid}/lyrics/synced` and
`PATCH /api/tracks/{track_rowid}/lyrics/offset`.

Same shape as `tests/test_api_recommend.py`: no TestClient, the endpoint
function is pulled off `app.routes` and called directly.
"""

import pytest
from fastapi import HTTPException

from muse.api import create_app
from muse.library import lyrics_state as library_lyrics_state
from tests.conftest import insert_track


def _config(tmp_path):
    from tests.conftest import make_config
    return make_config(tmp_path)


def route(app, path):
    for candidate in app.routes:
        if getattr(candidate, "path", None) == path:
            return candidate.endpoint
    raise AssertionError(f"no route {path}")


@pytest.fixture
def app(tmp_path):
    application = create_app(_config(tmp_path))
    from muse.library import db
    application.state.conn = db.connect(tmp_path / "library.sqlite3")
    return application


def _seed(app, tmp_path, rowid=1, *, lrc: str | None = None, lyrics: str | None = "some words"):
    insert_track(app.state.conn, rowid)
    base = tmp_path / "MUSIC" / f"{rowid}"
    base.parent.mkdir(parents=True, exist_ok=True)
    file_path = base.with_suffix(".m4a")
    lyrics_path = base.with_suffix(".lyrics.txt")
    if lyrics is not None:
        lyrics_path.write_text(lyrics, encoding="utf-8")
    if lrc is not None:
        base.with_suffix(".lrc").write_text(lrc, encoding="utf-8")
    app.state.conn.execute(
        "UPDATE tracks SET file_path = ?, lyrics_path = ? WHERE track_rowid = ?",
        (str(file_path), str(lyrics_path) if lyrics is not None else None, rowid),
    )
    app.state.conn.commit()


SYNCED_LRC = "[00:04.00] first line\n[00:08.00] second line\n"


def test_synced_track_returns_lines_and_no_plain(app, tmp_path):
    _seed(app, tmp_path, lrc=SYNCED_LRC, lyrics="first line\nsecond line")
    out = route(app, "/api/tracks/{track_rowid}/lyrics/synced")(1)
    assert out["synced"] is True
    assert out["plain"] is None
    assert out["lines"] == [
        {"ms": 4000, "text": "first line"},
        {"ms": 8000, "text": "second line"},
    ]
    assert out["offset_ms"] == 0


def test_unsynced_track_returns_plain_and_no_lines(app, tmp_path):
    _seed(app, tmp_path, lrc=None, lyrics="just plain words here")
    out = route(app, "/api/tracks/{track_rowid}/lyrics/synced")(1)
    assert out["synced"] is False
    assert out["lines"] is None
    assert out["plain"] == "just plain words here"


def test_an_lrc_with_no_parseable_lines_falls_back_to_unsynced(app, tmp_path):
    """A `.lrc` holding only metadata tags parses to zero lines -- treated
    the same as no `.lrc` at all, not as an empty synced view."""
    _seed(app, tmp_path, lrc="[ar:Someone]\n[ti:Something]\n", lyrics="plain fallback")
    out = route(app, "/api/tracks/{track_rowid}/lyrics/synced")(1)
    assert out["synced"] is False
    assert out["plain"] == "plain fallback"


def test_no_lyrics_at_all_404s(app, tmp_path):
    _seed(app, tmp_path, lrc=None, lyrics=None)
    with pytest.raises(HTTPException) as exc:
        route(app, "/api/tracks/{track_rowid}/lyrics/synced")(1)
    assert exc.value.status_code == 404


def test_unknown_track_404s(app):
    with pytest.raises(HTTPException) as exc:
        route(app, "/api/tracks/{track_rowid}/lyrics/synced")(999)
    assert exc.value.status_code == 404


def test_offset_reflects_a_prior_nudge(app, tmp_path):
    _seed(app, tmp_path, lrc=SYNCED_LRC)
    library_lyrics_state.upsert(
        app.state.conn, 1, state="synced", synced=True, checked_at=0,
    )
    library_lyrics_state.set_offset(app.state.conn, 1, -250)
    out = route(app, "/api/tracks/{track_rowid}/lyrics/synced")(1)
    assert out["offset_ms"] == -250


# --- PATCH .../lyrics/offset ----------------------------------------------


class _Write:
    def __init__(self, offset_ms):
        self.offset_ms = offset_ms


def test_patch_offset_updates_an_existing_row(app, tmp_path):
    _seed(app, tmp_path, lrc=SYNCED_LRC)
    library_lyrics_state.upsert(
        app.state.conn, 1, state="synced", synced=True, checked_at=0,
    )
    out = route(app, "/api/tracks/{track_rowid}/lyrics/offset")(1, _Write(150))
    assert out == {"offset_ms": 150}
    assert library_lyrics_state.get_offset_ms(app.state.conn, 1) == 150


def test_patch_offset_404s_with_no_lyrics_state_row(app, tmp_path):
    """A track the sync batch has never timed has no `.lrc` for an offset
    to apply to."""
    _seed(app, tmp_path, lrc=None, lyrics=None)
    with pytest.raises(HTTPException) as exc:
        route(app, "/api/tracks/{track_rowid}/lyrics/offset")(1, _Write(100))
    assert exc.value.status_code == 404
