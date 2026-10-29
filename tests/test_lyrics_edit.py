from muse.library import lyrics_edit, lyrics_state


def _track(conn, rowid=1):
    conn.execute(
        "INSERT INTO tracks (track_rowid, origin, metadata_tier, artist_name,"
        " artist_names, album_name, track_name, status, added_at)"
        " VALUES (?, 'local', 'ytdlp', 'A', '[]', 'B', 'C', 'done', 0)",
        (rowid,),
    )
    conn.commit()


def _lyrics_path(conn):
    return conn.execute("SELECT lyrics_path FROM tracks WHERE track_rowid = 1").fetchone()[0]


def test_save_writes_file_and_path(conn, tmp_path):
    _track(conn)
    root = tmp_path / "lyrics"
    assert lyrics_edit.save_text(conn, 1, "hello", lyrics_root=root) == {"lrc_stale": False}
    assert (root / "1.txt").read_text(encoding="utf-8") == "hello"
    assert _lyrics_path(conn) == str(root / "1.txt")


def test_save_invalidates_embedding_tags_and_state(conn, tmp_path):
    _track(conn)
    conn.execute(
        "INSERT INTO embeddings (track_rowid, audio_vec, lyrics_vec, lyrics_hash,"
        " lyrics_model, indexed_at) VALUES (1, x'01', x'02', 'h', 'm', 0)"
    )
    conn.execute(
        "INSERT INTO lyric_tag_sets (id, track_rowid, state, text_hash,"
        " taxonomy_version, model, written_at) VALUES (5, 1, 'tagged', 'x', 1, 'm', 0)"
    )
    conn.execute(
        "INSERT INTO lyric_tags (set_id, segment, grp, label, prob)"
        " VALUES (5, 'song', 'g', 'l', 0.5)"
    )
    conn.commit()
    lyrics_edit.save_text(conn, 1, "new", lyrics_root=tmp_path / "lyrics", now=7)
    row = conn.execute("SELECT * FROM embeddings WHERE track_rowid = 1").fetchone()
    assert row["audio_vec"] == b"\x01"
    assert row["lyrics_vec"] is None and row["lyrics_hash"] is None
    assert row["lyrics_model"] is None
    assert conn.execute("SELECT COUNT(*) FROM lyric_tag_sets").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM lyric_tags").fetchone()[0] == 0
    st = conn.execute("SELECT state, synced FROM lyrics_state WHERE track_rowid = 1").fetchone()
    assert (st["state"], st["synced"]) == ("manual", 0)
    assert 1 not in [r["track_rowid"] for r in lyrics_state.pending(conn)]


def test_stale_lrc_keep_and_drop(conn, tmp_path):
    _track(conn)
    root = tmp_path / "lyrics"
    root.mkdir()
    (root / "1.lrc").write_text("[00:01.00]x")
    assert lyrics_edit.save_text(conn, 1, "t", lyrics_root=root) == {"lrc_stale": True}
    assert lyrics_edit.is_stale(conn, 1, lyrics_root=root)
    assert lyrics_edit.keep_timings(conn, 1, lyrics_root=root)
    assert not lyrics_edit.is_stale(conn, 1, lyrics_root=root)
    assert not lyrics_edit.keep_timings(conn, 1, lyrics_root=root)
    assert lyrics_edit.drop_timings(conn, 1, lyrics_root=root)
    assert not (root / "1.lrc").exists()
    assert not lyrics_edit.drop_timings(conn, 1, lyrics_root=root)


def test_blank_deletes_files(conn, tmp_path):
    _track(conn)
    root = tmp_path / "lyrics"
    lyrics_edit.save_text(conn, 1, "x", lyrics_root=root)
    (root / "1.lrc").write_text("y")
    lyrics_edit.save_text(conn, 1, "  \n", lyrics_root=root)
    assert not (root / "1.txt").exists() and not (root / "1.lrc").exists()
    assert _lyrics_path(conn) is None


def test_unknown_track(conn, tmp_path):
    assert lyrics_edit.save_text(conn, 99, "x", lyrics_root=tmp_path) is None
