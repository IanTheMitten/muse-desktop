import sqlite3

from muse.library import migrate
from muse.library.schema import MIGRATIONS


def _cols(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _raw(tmp_path):
    raw = sqlite3.connect(tmp_path / "db.sqlite3", isolation_level=None)
    raw.row_factory = sqlite3.Row
    raw.execute("CREATE TABLE schema_version (version INTEGER NOT NULL, applied_at INTEGER NOT NULL)")
    return raw


def _build_to_22(raw):
    for i, fn in enumerate(MIGRATIONS[:22]):
        fn(raw)
        raw.execute("INSERT INTO schema_version VALUES (?, 0)", (i + 1,))


def test_m023_drops_retired_embedding_columns_and_adds_radio_and_prompts(tmp_path):
    raw = _raw(tmp_path)
    _build_to_22(raw)
    raw.execute(
        "INSERT INTO tracks (track_rowid, origin, metadata_tier, artist_name, artist_names,"
        " album_name, track_name, status, added_at)"
        " VALUES (1000000001, 'local', 'tags', 'A', '[\"A\"]', 'B', 'T', 'done', 0)")
    cols = _cols(raw, "embeddings")
    values = {c: None for c in cols}
    values.update(track_rowid=1000000001, audio_vec=b"a", feature_vec=b"f",
                  audio_model="stub", lyrics_hash="h", indexed_at=1)
    for c in cols:
        if values[c] is None:
            values[c] = b"x"
    names = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    raw.execute(f"INSERT INTO embeddings ({names}) VALUES ({marks})", tuple(values.values()))

    assert migrate(raw) == 23

    assert _cols(raw, "embeddings") == {
        "track_rowid", "audio_vec", "feature_vec", "audio_model", "lyrics_hash", "indexed_at"}
    assert raw.execute("SELECT lyrics_hash FROM embeddings").fetchone()["lyrics_hash"] == "h"
    info = {r["name"]: r for r in raw.execute("PRAGMA table_info(player_state)")}
    assert info["radio_pos"]["dflt_value"] == "0"
    assert info["radio_lift"]["dflt_value"] == "1"
    assert _cols(raw, "prompt_vectors") == {"audio_model", "name", "vec"}
    raw.close()


def test_fresh_database_reaches_23(tmp_path):
    raw = sqlite3.connect(tmp_path / "empty.sqlite3", isolation_level=None)
    raw.row_factory = sqlite3.Row
    assert migrate(raw) == 23
    raw.close()


def test_m023_is_safe_on_a_database_already_lacking_the_columns(tmp_path):
    raw = _raw(tmp_path)
    _build_to_22(raw)
    MIGRATIONS[22](raw)
    MIGRATIONS[22](raw)
    raw.close()
