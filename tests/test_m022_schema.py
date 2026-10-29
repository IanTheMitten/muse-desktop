import sqlite3

from muse.library import migrate
from muse.library.schema import MIGRATIONS


def _cols(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_m022_adds_edit_tables_year_and_clears_local_mtime(tmp_path):
    raw = sqlite3.connect(tmp_path / "db.sqlite3", isolation_level=None)
    raw.row_factory = sqlite3.Row
    raw.execute("CREATE TABLE schema_version (version INTEGER NOT NULL, applied_at INTEGER NOT NULL)")
    for i, fn in enumerate(MIGRATIONS[:21]):
        fn(raw)
        raw.execute("INSERT INTO schema_version VALUES (?, 0)", (i + 1,))
    raw.execute(
        "INSERT INTO tracks (track_rowid, origin, metadata_tier, artist_name, artist_names,"
        " album_name, track_name, status, added_at, file_mtime)"
        " VALUES (1000000001, 'local', 'tags', 'A', '[\"A\"]', 'B', 'T', 'done', 0, 5)")
    assert migrate(raw) == 22
    for table in ("track_overrides", "hidden_artists", "artist_images"):
        assert _cols(raw, table)
    assert "year" in _cols(raw, "tracks")
    assert raw.execute("SELECT file_mtime FROM tracks").fetchone()["file_mtime"] is None
    raw.close()
