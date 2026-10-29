from muse.ingest import fetch_lyrics


class FakeLrclibSearch:
    def __init__(self, results):
        self.results = results

    def __call__(self, track_name, artist_name, album_name=""):
        return self.results


def test_lrclib_hit_wins_and_nothing_else_is_called(monkeypatch):
    import muse.ingest as ingest_mod

    called = {"genius": False}

    def fake_genius(*a, **k):
        called["genius"] = True
        return None

    monkeypatch.setattr(ingest_mod, "search_lrclib", FakeLrclibSearch(
        [{"duration": 200, "syncedLyrics": "[00:01.00]hello", "instrumental": False}]
    ))
    monkeypatch.setattr(ingest_mod, "_genius_lyrics", fake_genius)

    track = {"track_rowid": 1, "track_name": "T", "artist_name": "A",
             "album_name": "Al", "duration_ms": 200000, "is_instrumental": False,
             "file_path": "/x.m4a"}
    outcome = fetch_lyrics(track, genius_client=None)
    assert outcome.state == "synced"
    assert called["genius"] is False


def test_falls_through_to_genius_when_lrclib_misses(monkeypatch):
    import muse.ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "search_lrclib", FakeLrclibSearch([]))

    def fake_genius(track, genius_client):
        return "some real lyrics text " * 20

    monkeypatch.setattr(ingest_mod, "_genius_lyrics", fake_genius)
    track = {"track_rowid": 1, "track_name": "T", "artist_name": "A",
             "album_name": "Al", "duration_ms": 200000, "is_instrumental": False,
             "file_path": "/x.m4a"}
    outcome = fetch_lyrics(track, genius_client=object())
    assert outcome.state == "plain"
    assert outcome.synced is False


def test_not_found_when_every_tier_misses_never_manual(monkeypatch):
    import muse.ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "search_lrclib", FakeLrclibSearch([]))
    monkeypatch.setattr(ingest_mod, "_genius_lyrics", lambda track, client: None)
    track = {"track_rowid": 1, "track_name": "T", "artist_name": "A",
             "album_name": "Al", "duration_ms": 200000, "is_instrumental": False,
             "file_path": "/x.m4a"}
    outcome = fetch_lyrics(track, genius_client=None)
    assert outcome.state == "not_found"
    assert outcome.state != "manual"
