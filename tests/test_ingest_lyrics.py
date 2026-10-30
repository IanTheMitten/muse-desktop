from muse.ingest import fetch_lyrics


class FakeLrclibSearch:
    def __init__(self, results):
        self.results = results

    def __call__(self, track_name, artist_name, album_name=""):
        return self.results


def test_lrclib_hit_wins(monkeypatch):
    import muse.ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "search_lrclib", FakeLrclibSearch(
        [{"duration": 200, "syncedLyrics": "[00:01.00]hello", "instrumental": False}]
    ))

    track = {"track_rowid": 1, "track_name": "T", "artist_name": "A",
             "album_name": "Al", "duration_ms": 200000, "is_instrumental": False,
             "file_path": "/x.m4a"}
    outcome = fetch_lyrics(track)
    assert outcome.state == "synced"


def test_not_found_when_lrclib_misses_never_manual(monkeypatch):
    import muse.ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "search_lrclib", FakeLrclibSearch([]))
    track = {"track_rowid": 1, "track_name": "T", "artist_name": "A",
             "album_name": "Al", "duration_ms": 200000, "is_instrumental": False,
             "file_path": "/x.m4a"}
    outcome = fetch_lyrics(track)
    assert outcome.state == "not_found"
    assert outcome.state != "manual"


def test_genius_is_gone_from_ingest():
    import muse.ingest as ingest_mod

    for name in ("build_genius_client", "_genius_lyrics", "GENIUS_TOKEN"):
        assert not hasattr(ingest_mod, name), name
