from muse.catalog import EmptyCatalog


def test_empty_catalog_browse_methods_return_nothing():
    catalog = EmptyCatalog()
    assert catalog.search_artists("anything") == []
    assert catalog.artist_albums(1) == []
    assert catalog.album_tracks(10) == []
    assert catalog.track_by_rowid(100) is None


def test_empty_catalog_track_id_for_isrc_is_none():
    assert EmptyCatalog().track_id_for_isrc("KRA381601245") is None
