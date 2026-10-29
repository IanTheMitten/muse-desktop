"""The migration ladder.

A migration that half-applies leaves a database that works today and cannot be
migrated tomorrow. That failure is silent, which is why it is tested.
"""

import sqlite3

import pytest

from muse.library import LATEST_VERSION, connect, current_version, migrate
from muse.library.schema import MIGRATIONS
from tests.conftest import insert_track


@pytest.fixture
def raw(tmp_path):
    conn = sqlite3.connect(tmp_path / "empty.sqlite3", isolation_level=None)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def test_connect_creates_a_missing_parent_dir(tmp_path):
    conn = connect(tmp_path / "a" / "b" / "library.sqlite3")
    assert (tmp_path / "a" / "b" / "library.sqlite3").exists()
    conn.close()


def test_empty_database_reports_version_zero(raw):
    assert current_version(raw) == 0


def test_migrate_applies_the_whole_ladder(raw):
    assert migrate(raw) == LATEST_VERSION


def test_migrate_creates_every_slice_one_table(raw):
    migrate(raw)
    names = {
        row["name"]
        for row in raw.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        )
    }
    assert {
        "schema_version",
        "tracks",
        "plays",
        "ratings",
        "player_state",
        "jobs",
        "tracks_fts",
    } <= names


def test_migrate_does_not_create_later_slice_tables(raw):
    """Empty tables for a future slice put the schema out of step with the code
    that is supposed to justify it. `embeddings` moved out of this set in
    ticket 01 when slice 2 started; `lyrics_state` moves out the same way in
    migration 12, when slice 4's lyrics-sync batch starts using it."""
    migrate(raw)
    names = {
        row["name"] for row in raw.execute("SELECT name FROM sqlite_master")
    }
    assert {"prompts"}.isdisjoint(names)


def test_migrate_creates_the_lyrics_state_table(raw):
    migrate(raw)
    columns = {
        row["name"] for row in raw.execute("PRAGMA table_info(lyrics_state)")
    }
    assert columns == {
        "track_rowid", "state", "synced", "lrc_offset_ms", "checked_at",
    }


def test_migrate_creates_the_embeddings_table(raw):
    migrate(raw)
    columns = {
        row["name"] for row in raw.execute("PRAGMA table_info(embeddings)")
    }
    assert columns == {
        "track_rowid", "audio_vec", "feature_vec", "lyrics_vec",
        "audio_model", "lyrics_model", "lyrics_hash", "indexed_at",
        "audio_va", "lyrics_va", "emotion_vec", "audio_va_model",
        "lyrics_va_model",
    }


def test_migrate_creates_the_feature_norm_bounds_table(raw):
    migrate(raw)
    columns = {
        row["name"] for row in raw.execute("PRAGMA table_info(feature_norm_bounds)")
    }
    assert columns == {"column", "min_value", "max_value"}


def test_migrate_adds_the_missing_recommender_feature_columns(raw):
    """`track_features` was materialised (m003) for the dashboard's mood bands,
    which only needed valence and energy. The embed batch's 13-dim feature_vec
    needs the rest of what Spotify measured."""
    migrate(raw)
    columns = {
        row["name"] for row in raw.execute("PRAGMA table_info(track_features)")
    }
    assert {"speechiness", "liveness", "key", "time_signature"} <= columns


def test_migrate_creates_the_taste_embeddings_table(raw):
    migrate(raw)
    columns = {
        row["name"] for row in raw.execute("PRAGMA table_info(taste_embeddings)")
    }
    assert columns == {"track_rowid", "taste_vec", "model", "trained_at"}


def test_migrate_is_a_no_op_on_second_run(raw):
    migrate(raw)
    applied_first = raw.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]

    assert migrate(raw) == LATEST_VERSION
    applied_again = raw.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]

    assert applied_first == applied_again == len(MIGRATIONS)


def test_ladder_records_each_version_in_order(raw):
    migrate(raw)
    versions = [
        row[0] for row in raw.execute("SELECT version FROM schema_version ORDER BY rowid")
    ]
    assert versions == list(range(1, len(MIGRATIONS) + 1))


def test_a_failing_migration_leaves_the_last_good_version(raw, monkeypatch):
    """The database must land on a version some code expects, never between two."""

    def explodes(conn):
        conn.execute("CREATE TABLE half_applied (x INTEGER)")
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "muse.library.schema.MIGRATIONS", [*MIGRATIONS, explodes]
    )

    with pytest.raises(RuntimeError):
        migrate(raw)

    assert current_version(raw) == len(MIGRATIONS)
    leftover = raw.execute(
        "SELECT name FROM sqlite_master WHERE name = 'half_applied'"
    ).fetchone()
    assert leftover is None


def test_plays_rejects_a_track_that_does_not_exist(raw):
    """`plays` is the only irreplaceable data here. A row pointing at nothing is
    the kind of corruption that surfaces long after it can be fixed."""
    migrate(raw)
    raw.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute(
            "INSERT INTO plays (track_rowid, started_at, reached_ms, duration_ms,"
            "                   heartbeat_at) VALUES (999, 0, 0, 1000, 0)"
        )


def test_player_state_holds_one_row_only(raw):
    migrate(raw)
    raw.execute(
        "INSERT INTO player_state (id, queue, queue_index, position_ms, updated_at)"
        " VALUES (1, '[]', 0, 0, 0)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute(
            "INSERT INTO player_state (id, queue, queue_index, position_ms, updated_at)"
            " VALUES (2, '[]', 0, 0, 0)"
        )


def test_migrate_creates_the_content_embeddings_table(raw):
    migrate(raw)
    columns = {
        row["name"] for row in raw.execute("PRAGMA table_info(content_embeddings)")
    }
    assert columns == {"track_rowid", "content_vec", "model", "embedded_at"}


def test_migrate_creates_the_content_stats_table(raw):
    migrate(raw)
    columns = {
        row["name"] for row in raw.execute("PRAGMA table_info(content_stats)")
    }
    assert columns == {
        "id", "mean_vec", "sim_lo", "sim_hi", "track_count", "computed_at",
    }


def test_content_stats_holds_at_most_one_row(raw):
    """The CHECK is what makes `WHERE id = 1` the whole read path."""
    migrate(raw)
    raw.execute(
        "INSERT INTO content_stats (id, mean_vec, sim_lo, sim_hi,"
        " track_count, computed_at) VALUES (1, X'00', 0.0, 1.0, 1, 0)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute(
            "INSERT INTO content_stats (id, mean_vec, sim_lo, sim_hi,"
            " track_count, computed_at) VALUES (2, X'00', 0.0, 1.0, 1, 0)"
        )


def test_migrate_adds_the_track_mbid_columns(raw):
    migrate(raw)
    columns = {
        row["name"] for row in raw.execute("PRAGMA table_info(tracks)")
    }
    assert {"recording_mbid", "release_mbid"} <= columns


def test_recording_and_release_mbid_are_indexed(raw):
    migrate(raw)
    names = {
        row["name"]
        for row in raw.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }
    assert "tracks_recording_mbid" in names
    assert "tracks_release_mbid" in names


def test_migration_11_reopens_audio_va_only_where_it_was_never_asked(raw):
    """The `unavailable` stamp was recorded against a model id that does not
    exist, so it marks a question that was never actually put to a model.
    Clearing it is what lets the corrected encoder be tried; a row answered by
    a real model must keep the name of whatever answered it."""
    for migration in MIGRATIONS[:10]:
        migration(raw)
    raw.execute(
        "INSERT INTO embeddings (track_rowid, indexed_at, audio_va_model)"
        " VALUES (1, 0, 'unavailable'), (2, 0, 'audeering/real-model')"
    )

    MIGRATIONS[10](raw)

    stamped = dict(raw.execute(
        "SELECT track_rowid, audio_va_model FROM embeddings"
    ).fetchall())
    assert stamped[1] is None
    assert stamped[2] == "audeering/real-model"


def test_migration_18_drops_the_retired_tables(raw):
    """ADR 0009: archetypes, drift, curator, profiles and one-sentence themes
    are retired; their tables go and nothing else does."""
    migrate(raw)
    names = {
        row["name"]
        for row in raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert names.isdisjoint({
        "archetypes", "archetype_seeds", "archetype_profiles",
        "lyric_themes", "curator_blocks",
    })
    assert {"tracks", "plays", "lyric_tag_sets", "lyric_tags", "embeddings"} <= names


def test_migration_18_drops_tables_holding_rows(raw, monkeypatch):
    """The live library has seeds in `archetype_seeds`; the drop must not trip
    a foreign key (they are on and immediate). `migrate` reads the module's
    `MIGRATIONS` at call time, so slicing it stops the ladder at 17."""
    import muse.library.schema as schema

    full = list(schema.MIGRATIONS)
    monkeypatch.setattr(schema, "MIGRATIONS", full[:17])
    assert migrate(raw) == 17
    raw.execute("PRAGMA foreign_keys = ON")
    insert_track(raw, 1)
    raw.execute(
        "INSERT INTO archetypes (archetype_id, prompt, drift, position, created_at, updated_at)"
        " VALUES ('sad', 'Something sad', 'hold', 1, 0, 0)")
    raw.execute(
        "INSERT INTO archetype_seeds (archetype_id, track_rowid, source, added_at)"
        " VALUES ('sad', 1, 'user', 0)")

    monkeypatch.setattr(schema, "MIGRATIONS", full)
    assert migrate(raw) == len(full)
    assert raw.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name = 'archetypes'").fetchone()[0] == 0


def test_migration_20_forgets_missing_art_only(raw, monkeypatch):
    import muse.library.schema as schema

    full = list(schema.MIGRATIONS)
    monkeypatch.setattr(schema, "MIGRATIONS", full[:19])
    assert migrate(raw) == 19
    raw.execute(
        "INSERT INTO album_art (album_rowid, art_path, source, mbid, checked_at)"
        " VALUES (1, NULL, NULL, NULL, 0)")
    raw.execute(
        "INSERT INTO album_art (album_rowid, art_path, source, mbid, checked_at)"
        " VALUES (2, 'art/2.jpg', 'coverartarchive', NULL, 0)")

    monkeypatch.setattr(schema, "MIGRATIONS", full)
    migrate(raw)

    assert [r[0] for r in raw.execute("SELECT album_rowid FROM album_art")] == [2]


def test_migrate_adds_the_local_file_columns(raw):
    migrate(raw)
    cols = {r["name"] for r in raw.execute("PRAGMA table_info(tracks)")}
    assert {"file_size", "file_mtime", "format", "album_key", "removed_at"} <= cols
    idx = {r["name"] for r in raw.execute("PRAGMA index_list(tracks)")}
    assert {"tracks_file_path", "tracks_album_key"} <= idx
