"""Migration 17 and the lyric tag storage layer
(docs/specs/2026-10-12-lyric-tag-store.md D1, D3, D4)."""

import sqlite3

import pytest

from muse.library import embeddings as library_embeddings
from muse.library import lyric_tags as store
from tests.conftest import insert_track


def test_migration_creates_both_tables(conn):
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert {"lyric_tag_sets", "lyric_tags"} <= names


def test_deleting_a_set_cascades_to_its_tags(conn):
    insert_track(conn, 1)
    conn.execute(
        "INSERT INTO lyric_tag_sets (id, track_rowid, state, text_hash,"
        " taxonomy_version, model, written_at) VALUES (7, 1, 'tagged', 'h', 3, 'm', 0)")
    conn.execute("INSERT INTO lyric_tags VALUES (7, 'song', 'mode', 'story', 0.9)")
    conn.execute("DELETE FROM lyric_tag_sets WHERE id = 7")
    assert conn.execute("SELECT COUNT(*) FROM lyric_tags").fetchone()[0] == 0


def test_one_set_per_track(conn):
    insert_track(conn, 1)
    insert = ("INSERT INTO lyric_tag_sets (track_rowid, state, text_hash,"
              " taxonomy_version, model, written_at) VALUES (1, 'tagged', 'h', 3, 'm', 0)")
    conn.execute(insert)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert)


# --- storage: muse/library/lyric_tags.py ------------------------------------

TAGS = {"song": {"mode": {"story": 0.9, "none": 0.1}},
        "start": {"outlook": {"despair": 1.0}}}


def _ready(conn, rowid, *, lyrics_hash="H", mbid=None):
    insert_track(conn, rowid)
    conn.execute("UPDATE tracks SET lyrics_path = ?, recording_mbid = ? WHERE track_rowid = ?",
                 (f"/x/{rowid}.lyrics.txt", mbid, rowid))
    library_embeddings.upsert(conn, rowid, audio_vec=b"a" * 4, feature_vec=None,
                              lyrics_vec=b"l" * 4, audio_model="clap",
                              lyrics_model="minilm", lyrics_hash=lyrics_hash)


def _write(conn, rowid, **overrides):
    args = dict(track_rowid=rowid, recording_mbid=None, state="tagged", text_hash="T",
                lyrics_hash="H", model="jev", tags=TAGS, taxonomy_version=3)
    args.update(overrides)
    return store.write_set(conn, **args)


def test_write_then_read_round_trips(conn):
    _ready(conn, 1)
    _write(conn, 1)
    assert store.read(conn, [1]) == {1: TAGS}


def test_rewrite_replaces_old_rows(conn):
    _ready(conn, 1)
    _write(conn, 1)
    _write(conn, 1, tags={"song": {"mode": {"rant": 1.0}}})
    assert store.read(conn) == {1: {"song": {"mode": {"rant": 1.0}}}}
    assert conn.execute("SELECT COUNT(*) FROM lyric_tag_sets").fetchone()[0] == 1


def test_no_lines_set_reads_empty(conn):
    _ready(conn, 1)
    _write(conn, 1, state="no_lines", tags={})
    assert store.read(conn, [1]) == {1: {}}


def test_unlinked_sets_are_not_read(conn):
    _write(conn, None, recording_mbid="mb-1")
    assert store.read(conn) == {}


def test_pending_rules(conn):
    for rid in (1, 2, 3, 4):
        _ready(conn, rid)
    _write(conn, 2)                      # current: not pending
    _write(conn, 3, lyrics_hash="OLD")   # lyrics repaired since
    _write(conn, 4, taxonomy_version=2)  # older taxonomy
    assert store.pending(conn, 3) == [1, 3, 4]


def test_instrumental_and_lyricless_tracks_are_not_pending(conn):
    _ready(conn, 1)
    conn.execute("UPDATE tracks SET is_instrumental = 1 WHERE track_rowid = 1")
    insert_track(conn, 2)
    assert store.pending(conn, 3) == []


def test_unlinked_set_becomes_a_link_candidate_once_its_track_lands(conn):
    _write(conn, None, recording_mbid="mb-1")
    assert store.link_candidates(conn) == []
    _ready(conn, 5, mbid="mb-1", lyrics_hash="E")
    [c] = store.link_candidates(conn)
    assert (c["track_rowid"], c["e_lyrics_hash"], c["text_hash"]) == (5, "E", "T")
    store.attach(conn, c["set_id"], 5, "E")
    assert store.read(conn, [5]) == {5: TAGS}
    assert store.pending(conn, 3) == []
    assert store.link_candidates(conn) == []


def test_a_track_with_its_own_set_is_not_a_link_candidate(conn):
    _ready(conn, 5, mbid="mb-1")
    _write(conn, 5)
    _write(conn, None, recording_mbid="mb-1")
    assert store.link_candidates(conn) == []


def test_delete_and_find(conn):
    set_id = _write(conn, None, recording_mbid="mb-1")
    assert store.find_set(conn, recording_mbid="mb-1")["id"] == set_id
    store.delete_set(conn, set_id)
    assert store.find_set(conn, recording_mbid="mb-1") is None


# --- reads for the arc engine (docs/specs/2026-10-17-arc-engine.md D9) -------

from muse.library import lyric_tags as _store
from tests.conftest import insert_track as _insert_track


def _tagged_set(conn, rowid, *, state="tagged", text_hash="h"):
    return _store.write_set(
        conn, track_rowid=rowid, recording_mbid=None, state=state,
        text_hash=text_hash, lyrics_hash=None, model="test",
        tags={} if state == "no_lines" else {"body": {"outlook": {"untroubled": 1.0}}},
        taxonomy_version=3,
    )


def test_text_hashes_covers_linked_tagged_sets_only(conn):
    _insert_track(conn, 1)
    _insert_track(conn, 2)
    _tagged_set(conn, 1, text_hash="a")
    _tagged_set(conn, 2, state="no_lines", text_hash="empty")
    _store.write_set(conn, track_rowid=None, recording_mbid="mb-1", state="tagged",
                     text_hash="b", lyrics_hash=None, model="test",
                     tags={"body": {"outlook": {"untroubled": 1.0}}}, taxonomy_version=3)
    assert _store.text_hashes(conn) == {1: "a"}


def test_stamp_moves_on_every_write(conn):
    _insert_track(conn, 1)
    before = _store.stamp(conn)
    _tagged_set(conn, 1, text_hash="a")
    after_first = _store.stamp(conn)
    _tagged_set(conn, 1, text_hash="b")
    assert before != after_first != _store.stamp(conn)


def test_stamp_ignores_unlinked_sets_and_moves_on_link(conn):
    _insert_track(conn, 1)
    before = _store.stamp(conn)
    set_id = _store.write_set(
        conn, track_rowid=None, recording_mbid="mb-x", state="tagged",
        text_hash="c", lyrics_hash=None, model="test",
        tags={"body": {"outlook": {"untroubled": 1.0}}}, taxonomy_version=3)
    assert _store.stamp(conn) == before
    _store.attach(conn, set_id, 1, None)
    assert _store.stamp(conn) != before
