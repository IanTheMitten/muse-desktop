from conftest import insert_track
from muse.library import tracks as library_tracks


def test_next_local_rowid_starts_at_one_billion(conn):
    assert library_tracks.next_local_rowid(conn) == 1_000_000_000


def test_next_local_rowid_increments_past_existing_local_rows(conn):
    conn.execute(
        "INSERT INTO tracks (track_rowid, track_id, origin, metadata_tier,"
        " artist_name, artist_names, album_name, track_name, status, added_at)"
        " VALUES (1000000005, NULL, 'local', 'ytdlp', 'A', '[]', 'B', 'C',"
        " 'done', 0)"
    )
    conn.commit()
    assert library_tracks.next_local_rowid(conn) == 1_000_000_006


def test_next_local_rowid_ignores_catalog_rows(conn):
    conn.execute(
        "INSERT INTO tracks (track_rowid, track_id, origin, metadata_tier,"
        " artist_name, artist_names, album_name, track_name, status, added_at)"
        " VALUES (99999999, 'sp1', 'catalog', 'catalog', 'A', '[]', 'B', 'C',"
        " 'done', 0)"
    )
    conn.commit()
    assert library_tracks.next_local_rowid(conn) == 1_000_000_000


def test_find_done_by_track_rowid(conn):
    library_tracks.write_pending(
        conn, track_rowid=42, origin="catalog", metadata_tier="catalog",
        artist_name="A", artist_names=["A"], album_name="B", track_name="C",
        source_site_id=None, isrc=None,
    )
    library_tracks.mark_done(conn, 42, file_path="/x.m4a", lyrics_path=None,
                              art_path=None)
    found = library_tracks.find_done(conn, track_rowid=42, source_site_id=None,
                                       isrc=None, recording_mbid=None)
    assert found is not None
    assert found["status"] == "done"


def test_find_done_returns_none_for_error_row(conn):
    library_tracks.write_pending(
        conn, track_rowid=43, origin="catalog", metadata_tier="catalog",
        artist_name="A", artist_names=["A"], album_name="B", track_name="C",
        source_site_id=None, isrc=None,
    )
    found = library_tracks.find_done(conn, track_rowid=43, source_site_id=None,
                                       isrc=None, recording_mbid=None)
    assert found is None


def test_find_done_by_source_site_id_for_local_track(conn):
    rowid = library_tracks.next_local_rowid(conn)
    library_tracks.write_pending(
        conn, track_rowid=rowid, origin="local", metadata_tier="ytdlp",
        artist_name="A", artist_names=["A"], album_name="B", track_name="C",
        source_site_id="yt_abc123", isrc=None,
    )
    library_tracks.mark_done(conn, rowid, file_path="/x.m4a", lyrics_path=None,
                              art_path=None)
    found = library_tracks.find_done(conn, track_rowid=None,
                                       source_site_id="yt_abc123", isrc=None,
                                       recording_mbid=None)
    assert found is not None
    assert found["track_rowid"] == rowid


def test_write_pending_indexes_track_for_search(conn):
    library_tracks.write_pending(
        conn, track_rowid=45, origin="local", metadata_tier="ytdlp",
        artist_name="Radiohead", artist_names=["Radiohead"],
        album_name="OK Computer", track_name="Airbag",
        source_site_id=None, isrc=None,
    )
    row = conn.execute(
        "SELECT rowid FROM tracks_fts WHERE tracks_fts MATCH 'Airbag'"
    ).fetchone()
    assert row is not None
    assert row["rowid"] == 45


def test_mark_done_writes_source_site_id_when_provided(conn):
    library_tracks.write_pending(
        conn, track_rowid=46, origin="local", metadata_tier="ytdlp",
        artist_name="A", artist_names=["A"], album_name="B", track_name="C",
        source_site_id=None, isrc=None,
    )
    library_tracks.mark_done(conn, 46, file_path="/x.m4a", lyrics_path=None,
                              art_path=None, source_site_id="yt_xyz")
    row = conn.execute(
        "SELECT source_site_id FROM tracks WHERE track_rowid = 46"
    ).fetchone()
    assert row["source_site_id"] == "yt_xyz"


def test_write_pending_is_upsert_for_retry(conn):
    library_tracks.write_pending(
        conn, track_rowid=44, origin="catalog", metadata_tier="catalog",
        artist_name="A", artist_names=["A"], album_name="B", track_name="C",
        source_site_id=None, isrc=None,
    )
    # Simulate a first attempt's failure, then a retry re-entering the pipeline.
    library_tracks.write_pending(
        conn, track_rowid=44, origin="catalog", metadata_tier="musicbrainz",
        artist_name="A", artist_names=["A"], album_name="B", track_name="C",
        source_site_id=None, isrc=None,
    )
    row = conn.execute(
        "SELECT metadata_tier, status FROM tracks WHERE track_rowid = 44"
    ).fetchone()
    assert row["metadata_tier"] == "musicbrainz"
    assert row["status"] == "error"


def test_find_done_matches_on_recording_mbid_alone(conn):
    """The only key a post-freeze track has before it is downloaded.

    No track_rowid (not allocated yet), no source_site_id (yt-dlp has not
    run), no ISRC (MusicBrainz often has none for a new release).
    """
    library_tracks.write_pending(
        conn, track_rowid=1_000_000_000, origin="local",
        metadata_tier="musicbrainz", artist_name="Artist",
        artist_names=["Artist"], album_name="Album", track_name="Track",
        source_site_id=None, isrc=None, recording_mbid="rec-1",
        release_mbid="rel-1",
    )
    library_tracks.mark_done(
        conn, 1_000_000_000, file_path="/x.m4a", lyrics_path=None,
        art_path=None, source_site_id="yt-1",
    )

    found = library_tracks.find_done(
        conn, track_rowid=None, source_site_id=None, isrc=None,
        recording_mbid="rec-1",
    )
    assert found is not None
    assert found["track_rowid"] == 1_000_000_000


def test_find_done_by_recording_mbid_ignores_an_error_row(conn):
    """The whole non-UNIQUE decision rests on this: a failed attempt must
    never be mistaken for a reason to skip a re-download, for this key
    exactly as it already is for track_rowid/source_site_id/isrc."""
    library_tracks.write_pending(
        conn, track_rowid=1_000_000_001, origin="local",
        metadata_tier="musicbrainz", artist_name="Artist",
        artist_names=["Artist"], album_name="Album", track_name="Track",
        source_site_id=None, isrc=None, recording_mbid="rec-1",
        release_mbid="rel-1",
    )
    found = library_tracks.find_done(
        conn, track_rowid=None, source_site_id=None, isrc=None,
        recording_mbid="rec-1",
    )
    assert found is None


def test_find_done_ignores_an_unrelated_recording_mbid(conn):
    library_tracks.write_pending(
        conn, track_rowid=1_000_000_002, origin="local",
        metadata_tier="musicbrainz", artist_name="Artist",
        artist_names=["Artist"], album_name="Album", track_name="Track",
        source_site_id=None, isrc=None, recording_mbid="rec-1",
        release_mbid="rel-1",
    )
    library_tracks.mark_done(
        conn, 1_000_000_002, file_path="/x.m4a", lyrics_path=None,
        art_path=None, source_site_id="yt-2",
    )

    found = library_tracks.find_done(
        conn, track_rowid=None, source_site_id=None, isrc=None,
        recording_mbid="rec-2",
    )
    assert found is None


def test_album_rowid_for_release_allocates_above_the_floor(conn):
    rowid = library_tracks.album_rowid_for_release(conn, "rel-1")
    assert rowid >= library_tracks.LOCAL_ROWID_FLOOR


def test_two_tracks_of_one_release_share_an_album_rowid(conn):
    first = library_tracks.album_rowid_for_release(conn, "rel-1")
    library_tracks.write_pending(
        conn, track_rowid=1_000_000_000, origin="local",
        metadata_tier="musicbrainz", artist_name="Artist",
        artist_names=["Artist"], album_name="Album", track_name="One",
        source_site_id=None, isrc=None, album_rowid=first,
        recording_mbid="rec-1", release_mbid="rel-1",
    )
    library_tracks.mark_done(
        conn, 1_000_000_000, file_path="/x.m4a", lyrics_path=None,
        art_path=None,
    )

    second = library_tracks.album_rowid_for_release(conn, "rel-1")
    assert second == first


def test_a_different_release_gets_a_different_album_rowid(conn):
    first = library_tracks.album_rowid_for_release(conn, "rel-1")
    library_tracks.write_pending(
        conn, track_rowid=1_000_000_000, origin="local",
        metadata_tier="musicbrainz", artist_name="Artist",
        artist_names=["Artist"], album_name="Album", track_name="One",
        source_site_id=None, isrc=None, album_rowid=first,
        recording_mbid="rec-1", release_mbid="rel-1",
    )

    assert library_tracks.album_rowid_for_release(conn, "rel-2") != first


def test_album_rowid_for_release_reuses_an_error_row_instead_of_reallocating(conn):
    """A failed sibling track's `album_rowid` still counts. Excluding
    `status='error'` rows here (the way `find_done` excludes them) would
    hand a retried release two different album rowids -- one on the first,
    failed attempt and a second, higher one on retry -- splitting a single
    release across two albums in the gallery.
    """
    first = library_tracks.album_rowid_for_release(conn, "rel-1")
    library_tracks.write_pending(
        conn, track_rowid=1_000_000_000, origin="local",
        metadata_tier="musicbrainz", artist_name="Artist",
        artist_names=["Artist"], album_name="Album", track_name="One",
        source_site_id=None, isrc=None, album_rowid=first,
        recording_mbid="rec-1", release_mbid="rel-1",
    )
    # This track is left in its 'error' state -- no mark_done call.

    second = library_tracks.album_rowid_for_release(conn, "rel-1")
    assert second == first


def _facts(**kw):
    base = dict(track_rowid=None, source_site_id=None, isrc=None,
                recording_mbid="rec-x", artist_name="iu", track_name="밤편지",
                duration_ms=256000)
    base.update(kw)
    return base


def test_find_done_fourth_key_matches_within_3s(conn):
    insert_track(conn, 100, artist_name="IU", track_name="밤편지", duration_ms=253000)
    assert library_tracks.find_done(conn, **_facts())["track_rowid"] == 100


def test_find_done_fourth_key_rejects_beyond_3s(conn):
    insert_track(conn, 100, artist_name="IU", track_name="밤편지", duration_ms=253000)
    assert library_tracks.find_done(conn, **_facts(duration_ms=256001)) is None


def test_find_done_fourth_key_rejects_qualifier_titles(conn):
    insert_track(conn, 101, artist_name="BTS", track_name="Dynamite", duration_ms=200000)
    for title in ("Dynamite - Live", "Dynamite (Live)"):
        assert library_tracks.find_done(
            conn, **_facts(artist_name="BTS", track_name=title, duration_ms=200000)
        ) is None


def test_find_done_fourth_key_ignores_error_row(conn):
    insert_track(conn, 100, status="error", artist_name="IU", track_name="밤편지",
                 duration_ms=253000)
    assert library_tracks.find_done(conn, **_facts()) is None


def test_find_done_fourth_key_needs_duration(conn):
    insert_track(conn, 100, artist_name="IU", track_name="밤편지", duration_ms=253000)
    assert library_tracks.find_done(conn, **_facts(duration_ms=None)) is None


def test_find_held_statuses_include_missing_file(conn):
    insert_track(conn, 100, status="missing_file", artist_name="IU",
                 track_name="밤편지", duration_ms=253000)
    held = library_tracks.find_held(
        conn, isrc=None, recording_mbid=None, artist_name="iu",
        track_name="밤편지", duration_ms=256000,
        statuses=("done", "missing_file"),
    )
    assert held["track_rowid"] == 100
    assert library_tracks.find_done(conn, **_facts()) is None


def test_claim_track_id_done_holder_returned(conn):
    insert_track(conn, 100, track_id="sp9", status="done")
    assert library_tracks.claim_track_id(conn, "sp9")["track_rowid"] == 100


def test_claim_track_id_missing_file_holder_returned(conn):
    insert_track(conn, 100, track_id="sp9", status="missing_file")
    assert library_tracks.claim_track_id(conn, "sp9")["track_rowid"] == 100


def test_claim_track_id_error_holder_released(conn):
    insert_track(conn, 100, track_id="sp9", status="error")
    conn.execute("UPDATE tracks SET origin='local' WHERE track_rowid=?", (100,))
    assert library_tracks.claim_track_id(conn, "sp9") is None
    row = conn.execute("SELECT track_id FROM tracks WHERE track_rowid = 100").fetchone()
    assert row["track_id"] is None


def test_claim_track_id_non_local_error_holder_kept(conn):
    insert_track(conn, 100, track_id="sp9", status="error")
    assert library_tracks.claim_track_id(conn, "sp9")["track_rowid"] == 100
    row = conn.execute("SELECT track_id FROM tracks WHERE track_rowid = 100").fetchone()
    assert row["track_id"] == "sp9"


def test_claim_track_id_no_holder(conn):
    assert library_tracks.claim_track_id(conn, "sp9") is None
