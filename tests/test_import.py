"""Manifest import.

An import that is not quite idempotent duplicates rows on the second startup
and nobody notices until the counts are wrong. Silent, so tested.
"""

from conftest import (
    FakeCatalog,
    artist_names,
    make_facts,
    make_manifest,
    make_record,
    track_row,
)

from muse.library import import_manifest


def test_every_entry_imports(conn):
    manifest = make_manifest(
        make_record(1, status="done", file_path=None),
        make_record(2, status="error", error="no result"),
        make_record(3, status="done", file_path=None),
    )
    result = import_manifest(manifest, FakeCatalog(), conn)

    assert result.inserted == 3
    assert result.updated == 0
    assert conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 3


def test_import_is_idempotent(conn):
    manifest = make_manifest(make_record(1), make_record(2), make_record(3))

    first = import_manifest(manifest, FakeCatalog(), conn)
    before = conn.execute(
        "SELECT track_rowid, track_name, status FROM tracks ORDER BY track_rowid"
    ).fetchall()

    second = import_manifest(manifest, FakeCatalog(), conn)
    after = conn.execute(
        "SELECT track_rowid, track_name, status FROM tracks ORDER BY track_rowid"
    ).fetchall()

    assert first.inserted == 3 and first.updated == 0
    assert second.inserted == 0 and second.updated == 3
    assert [tuple(r) for r in before] == [tuple(r) for r in after]
    assert conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 3


def test_reimport_does_not_duplicate_search_entries(conn):
    manifest = make_manifest(make_record(1))
    import_manifest(manifest, FakeCatalog(), conn)
    import_manifest(manifest, FakeCatalog(), conn)

    assert conn.execute("SELECT COUNT(*) FROM tracks_fts").fetchone()[0] == 1


def test_error_entries_are_kept_with_their_error_text(conn):
    """Not dropped. They are the memory of what was tried and failed, and slice
    3 turns them into retryable jobs."""
    manifest = make_manifest(
        make_record(7, status="error", error="youtube: no matching result")
    )
    result = import_manifest(manifest, FakeCatalog(), conn)

    row = track_row(conn, 7)
    assert row["status"] == "error"
    assert row["error"] == "youtube: no matching result"
    assert result.by_status["error"] == 1


def test_an_error_entry_with_audio_on_disk_is_playable(conn, tmp_path):
    """Twelve of the manifest's 64 error entries have working audio and failed
    only at the Genius lyrics step. Disk decides, not the label — otherwise
    twelve playable tracks stay invisible forever."""
    audio = tmp_path / "02 - Mistletoe.m4a"
    audio.write_bytes(b"x" * 1024)
    manifest = make_manifest(
        make_record(
            30,
            status="error",
            file_path=str(audio),
            error="Request timed out: HTTPSConnectionPool(host='genius.com'...)",
        )
    )
    result = import_manifest(manifest, FakeCatalog(), conn)

    row = track_row(conn, 30)
    assert row["status"] == "done"
    assert row["error"].startswith("Request timed out")  # why lyrics are absent
    assert result.by_status == {"done": 1}


def test_done_entry_with_a_missing_file_becomes_missing_file(conn):
    manifest = make_manifest(
        make_record(8, status="done", file_path="MUSIC/Gone/Gone/01 - Gone.m4a")
    )
    result = import_manifest(manifest, FakeCatalog(), conn)

    assert track_row(conn, 8)["status"] == "missing_file"
    assert result.missing_files == 1


def test_done_entry_whose_file_exists_stays_done(conn, tmp_path):
    audio = tmp_path / "01 - Here.m4a"
    audio.write_bytes(b"not really audio")
    manifest = make_manifest(make_record(9, status="done", file_path=str(audio)))

    result = import_manifest(manifest, FakeCatalog(), conn)

    assert track_row(conn, 9)["status"] == "done"
    assert result.missing_files == 0


def test_relative_paths_resolve_against_the_project_not_the_cwd(conn, tmp_path,
                                                                monkeypatch):
    """The failure this prevents is total and silent: launched from anywhere
    but the project directory, every relative path misses, and the import marks
    the whole library `missing_file` while reporting complete success."""
    project = tmp_path / "project"
    (project / "MUSIC" / "Artist" / "Album").mkdir(parents=True)
    audio = project / "MUSIC" / "Artist" / "Album" / "01 - Song.m4a"
    audio.write_bytes(b"audio")

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    manifest = make_manifest(
        make_record(
            40,
            status="done",
            file_path="MUSIC/Artist/Album/01 - Song.m4a",
            lyrics_path="MUSIC/Artist/Album/01 - Song.lyrics.txt",
        )
    )
    import_manifest(manifest, FakeCatalog(), conn, root=project)

    row = track_row(conn, 40)
    assert row["status"] == "done"
    # Stored absolute, so nothing downstream has to know this rule or be
    # launched from the right place.
    assert row["file_path"] == str(audio)
    assert row["lyrics_path"] == str(
        project / "MUSIC" / "Artist" / "Album" / "01 - Song.lyrics.txt"
    )


def test_an_absolute_path_in_the_manifest_is_left_alone(conn, tmp_path):
    audio = tmp_path / "01 - Absolute.m4a"
    audio.write_bytes(b"audio")

    import_manifest(
        make_manifest(make_record(41, status="done", file_path=str(audio))),
        FakeCatalog(),
        conn,
        root=tmp_path / "irrelevant",
    )

    assert track_row(conn, 41)["file_path"] == str(audio)
    assert track_row(conn, 41)["status"] == "done"


def test_missing_artist_names_falls_back_to_artist_name(conn):
    """774 of 775 entries carry `artist_names`. The one that does not must not
    import as an empty list."""
    record = make_record(11, artist_name="Solo Act")
    del record["artist_names"]
    result = import_manifest(make_manifest(record), FakeCatalog(), conn)

    assert artist_names(conn, 11) == ["Solo Act"]
    assert result.total == 1


def test_duration_and_isrc_come_from_the_catalog(conn):
    """Neither is in the manifest."""
    catalog = FakeCatalog({12: make_facts(12, duration_ms=214_000, isrc="USUM71234567")})
    import_manifest(make_manifest(make_record(12)), catalog, conn)

    row = track_row(conn, 12)
    assert row["duration_ms"] == 214_000
    assert row["isrc"] == "USUM71234567"


def test_catalog_disc_number_wins_over_the_manifest(conn):
    """The manifest's disc number is not reliable; the catalog's is."""
    catalog = FakeCatalog({13: make_facts(13, disc_no=2)})
    import_manifest(make_manifest(make_record(13, disc_no=1)), catalog, conn)

    assert track_row(conn, 13)["disc_no"] == 2


def test_reimport_without_the_catalog_keeps_what_the_catalog_gave(conn):
    """The catalog mirror is optional and may be deleted. Every startup
    re-imports the manifest, so without this the first restart after the
    deletion would null duration, ISRC, album and track/disc numbers on
    every manifest track -- facts that have no other source on disk."""
    record = make_record(15)
    for field in ("album_rowid", "track_no", "disc_no"):  # catalog-only, as for real
        record.pop(field)
    catalog = FakeCatalog({15: make_facts(15, duration_ms=201_000, isrc="USAAA2600001",
                                          disc_no=2, track_no=7, album_rowid=900)})
    import_manifest(make_manifest(record), catalog, conn)

    import_manifest(make_manifest(record), FakeCatalog(), conn)

    row = track_row(conn, 15)
    assert row["duration_ms"] == 201_000
    assert row["isrc"] == "USAAA2600001"
    assert row["album_rowid"] == 900
    assert row["track_no"] == 7
    assert row["disc_no"] == 2


def test_a_fresh_catalog_value_still_replaces_the_stored_one(conn):
    """Keeping on absence is not freezing: when the catalog does answer, its
    answer is what is stored."""
    import_manifest(make_manifest(make_record(16)),
                    FakeCatalog({16: make_facts(16, duration_ms=100_000)}), conn)
    import_manifest(make_manifest(make_record(16)),
                    FakeCatalog({16: make_facts(16, duration_ms=150_000)}), conn)

    assert track_row(conn, 16)["duration_ms"] == 150_000


def test_a_track_the_catalog_does_not_know_still_imports(conn):
    result = import_manifest(make_manifest(make_record(14)), FakeCatalog(), conn)

    row = track_row(conn, 14)
    assert row is not None
    assert row["duration_ms"] is None
    assert result.missing_from_catalog == 1


def test_instrumental_is_detected_from_the_filename(conn, tmp_path):
    inst = tmp_path / "03 - Song - Inst.m4a"
    inst.write_bytes(b"")
    vocal = tmp_path / "04 - Song.m4a"
    vocal.write_bytes(b"")

    import_manifest(
        make_manifest(
            make_record(15, file_path=str(inst)),
            make_record(16, file_path=str(vocal)),
        ),
        FakeCatalog(),
        conn,
    )

    assert track_row(conn, 15)["is_instrumental"] == 1
    assert track_row(conn, 16)["is_instrumental"] == 0


def test_import_never_overwrites_app_owned_columns(conn):
    """`art_path` is filled by the art backfill and `gain_db` by slice 2. A
    re-import that wiped them would silently undo minutes of MusicBrainz work
    and, later, an entire loudness pass."""
    manifest = make_manifest(make_record(17))
    import_manifest(manifest, FakeCatalog(), conn)

    conn.execute(
        "UPDATE tracks SET art_path = 'assets/art/20.jpg', gain_db = -7.5"
        " WHERE track_rowid = 17"
    )
    original_added_at = track_row(conn, 17)["added_at"]

    import_manifest(manifest, FakeCatalog(), conn, now=original_added_at + 5000)

    row = track_row(conn, 17)
    assert row["art_path"] == "assets/art/20.jpg"
    assert row["gain_db"] == -7.5
    assert row["added_at"] == original_added_at


def test_a_changed_manifest_field_updates_on_reimport(conn):
    import_manifest(make_manifest(make_record(18, track_name="Old Title")),
                    FakeCatalog(), conn)
    import_manifest(make_manifest(make_record(18, track_name="New Title")),
                    FakeCatalog(), conn)

    assert track_row(conn, 18)["track_name"] == "New Title"


def test_status_counts_match_the_manifest(conn, tmp_path):
    present = tmp_path / "05 - Present.m4a"
    present.write_bytes(b"")

    result = import_manifest(
        make_manifest(
            make_record(20, status="done", file_path=str(present)),
            make_record(21, status="done", file_path="MUSIC/gone.m4a"),
            make_record(22, status="error", error="boom"),
        ),
        FakeCatalog(),
        conn,
    )

    assert result.by_status == {"done": 1, "missing_file": 1, "error": 1}
    assert result.total == 3


def test_search_index_carries_the_normalized_title(conn):
    import_manifest(
        make_manifest(
            make_record(23, track_name="Talking to the Moon - Acoustic Piano")
        ),
        FakeCatalog(),
        conn,
    )

    hits = conn.execute(
        "SELECT rowid FROM tracks_fts WHERE tracks_fts MATCH ?",
        ('"talking to the moon"',),
    ).fetchall()
    assert [row[0] for row in hits] == [23]

    # The display title is untouched. Normalization is a search trick.
    assert track_row(conn, 23)["track_name"] == "Talking to the Moon - Acoustic Piano"


def _local_holder(conn, track_id):
    """An MB download (origin='local', above the floor) holding a Spotify id,
    with a play and a rating so a fix that loses either is caught."""
    from muse.library import open_span, set_rating
    from muse.library.tracks import LOCAL_ROWID_FLOOR

    rowid = LOCAL_ROWID_FLOOR
    conn.execute(
        "INSERT INTO tracks (track_rowid, track_id, origin, metadata_tier,"
        "  artist_name, artist_names, album_name, track_name, status, added_at)"
        " VALUES (?, ?, 'local', 'musicbrainz', 'A', '[\"A\"]', 'B', 'C', 'done', 1)",
        (rowid, track_id),
    )
    conn.commit()
    open_span(conn, rowid, duration_ms=180_000, now_ms=1_000)
    set_rating(conn, rowid, 1)
    return rowid


def test_a_manifest_entry_takes_a_spotify_id_a_local_download_holds(conn):
    """The downloader adding an entry for an id an MB download already holds
    used to fail UNIQUE(track_id) at the next startup and keep the service
    down. The manifest row takes the id -- the same end state as when the
    manifest entry came first -- and the local row keeps its plays."""
    local = _local_holder(conn, "sp-x")
    manifest = make_manifest(make_record(7, track_id="sp-x"))

    import_manifest(manifest, FakeCatalog(), conn)
    first = [dict(r) for r in conn.execute("SELECT * FROM tracks ORDER BY track_rowid")]
    import_manifest(manifest, FakeCatalog(), conn)
    second = [dict(r) for r in conn.execute("SELECT * FROM tracks ORDER BY track_rowid")]

    assert first == second
    assert track_row(conn, 7)["track_id"] == "sp-x"
    assert track_row(conn, local)["track_id"] is None
    assert track_row(conn, local)["status"] == "done"
    assert conn.execute(
        "SELECT COUNT(*) FROM plays WHERE track_rowid = ?", (local,)
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM ratings WHERE track_rowid = ?", (local,)
    ).fetchone()[0] == 1


def test_two_manifest_entries_with_one_spotify_id_do_not_crash(conn):
    """The first catalog row keeps it; the second imports without, every run."""
    manifest = make_manifest(
        make_record(7, track_id="sp-x"), make_record(8, track_id="sp-x")
    )

    import_manifest(manifest, FakeCatalog(), conn)
    import_manifest(manifest, FakeCatalog(), conn)

    assert track_row(conn, 7)["track_id"] == "sp-x"
    assert track_row(conn, 8)["track_id"] is None


def test_startup_survives_a_manifest_id_a_local_download_holds(tmp_path):
    import json

    from fastapi.testclient import TestClient

    from muse.api import create_app
    from muse.config import Config
    from muse.library import connect, migrate

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
    db = connect(config.library_db)
    migrate(db)
    _local_holder(db, "sp-x")
    db.close()
    config.manifest.write_text(json.dumps(make_manifest(make_record(7, track_id="sp-x"))))

    for _ in range(2):
        with TestClient(create_app(config)) as client:
            assert client.get("/api/health").status_code == 200
