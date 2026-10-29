from muse.catalog import TrackFacts
from muse.ingest import CATALOG, MUSICBRAINZ, YTDLP, ResolvedMetadata, TieredMetadataProvider


class FakeCatalog:
    def __init__(self, facts=None):
        self.facts = facts or {}

    def track_facts(self, rowids):
        return {r: self.facts[r] for r in rowids if r in self.facts}


class FakeMB:
    def __init__(self, isrc=None, language=None):
        self._isrc = isrc
        self._language = language
        self.calls = []

    def get(self, path, params):
        self.calls.append((path, params))
        return {"stub": True} if self._isrc else None


def test_catalog_tier_wins_when_track_rowid_known():
    catalog = FakeCatalog({100: TrackFacts(
        track_rowid=100, duration_ms=200000, isrc="GBAAA1234567",
        disc_no=1, track_no=3, album_rowid=10,
    )})
    provider = TieredMetadataProvider(catalog=catalog, mb=FakeMB(), ytdlp_info=None)
    result = provider.resolve({
        "track_rowid": 100, "artist_name": "A", "album_name": "B",
        "track_name": "C",
    })
    assert result.metadata_tier == CATALOG
    assert result.duration_ms == 200000
    assert result.isrc == "GBAAA1234567"
    assert result.album_rowid == 10


def test_falls_back_to_ytdlp_info_when_no_track_rowid():
    def fake_ytdlp_info(query_or_url):
        return {"title": "Some Song", "uploader": "Some Artist", "duration": 210,
                 "id": "abc123"}

    provider = TieredMetadataProvider(
        catalog=FakeCatalog(), mb=FakeMB(), ytdlp_info=fake_ytdlp_info,
    )
    result = provider.resolve({"artist_name": "Some Artist", "track_name": "Some Song"})
    assert result.metadata_tier == YTDLP
    assert result.duration_ms == 210000
    assert result.isrc is None


def test_no_result_returns_none():
    provider = TieredMetadataProvider(catalog=FakeCatalog(), mb=FakeMB(), ytdlp_info=None)
    assert provider.resolve({"artist_name": "A", "track_name": "B"}) is None


class ExplodingMb:
    """Any call is a bug: an `mb` payload already carries every fact."""

    def get(self, path, params):
        raise AssertionError(f"resolve() reached MusicBrainz for {path}")


class ExplodingCatalog:
    def track_facts(self, rowids):
        raise AssertionError("resolve() reached the catalog")


MB_PAYLOAD = {
    "kind": "mb",
    "recording_mbid": "rec-1",
    "release_mbid": "rel-1",
    "artist_name": "Artist One",
    "artist_names": ["Artist One", "Guest"],
    "album_name": "The Album",
    "track_name": "One",
    "track_no": 3,
    "disc_no": 1,
    "total_tracks": 12,
    "duration_ms": 214000,
    "isrc": "USXX12345678",
}


def test_mb_payload_resolves_with_no_network_at_all():
    provider = TieredMetadataProvider(
        catalog=ExplodingCatalog(), mb=ExplodingMb(), ytdlp_info=None
    )

    resolved = provider.resolve(MB_PAYLOAD)

    assert resolved.metadata_tier == MUSICBRAINZ
    assert resolved.track_name == "One"
    assert resolved.track_no == 3
    assert resolved.isrc == "USXX12345678"
    assert resolved.artist_names == ["Artist One", "Guest"]
    assert resolved.recording_mbid == "rec-1"
    assert resolved.release_mbid == "rel-1"


def test_a_retried_mb_payload_resolves_identically():
    """Retry replays `job["payload"]` verbatim without going through
    create_job, so the payload has to be self-sufficient."""
    provider = TieredMetadataProvider(
        catalog=ExplodingCatalog(), mb=ExplodingMb(), ytdlp_info=None
    )

    first = provider.resolve(MB_PAYLOAD)
    assert first.metadata_tier == MUSICBRAINZ
    assert first == provider.resolve(dict(MB_PAYLOAD))


def test_mb_payload_with_no_isrc_resolves_fine():
    """MusicBrainz frequently has no ISRC for a new release -- this must not
    be treated as a failure."""
    payload = {k: v for k, v in MB_PAYLOAD.items() if k != "isrc"}
    provider = TieredMetadataProvider(
        catalog=ExplodingCatalog(), mb=ExplodingMb(), ytdlp_info=None
    )

    resolved = provider.resolve(payload)

    assert resolved.metadata_tier == MUSICBRAINZ
    assert resolved.isrc is None


def test_mb_payload_with_no_total_tracks_or_duration_resolves_fine():
    payload = {
        k: v for k, v in MB_PAYLOAD.items()
        if k not in ("total_tracks", "duration_ms")
    }
    provider = TieredMetadataProvider(
        catalog=ExplodingCatalog(), mb=ExplodingMb(), ytdlp_info=None
    )

    resolved = provider.resolve(payload)

    assert resolved.metadata_tier == MUSICBRAINZ
    assert resolved.total_tracks is None
    assert resolved.duration_ms is None


def test_catalog_tier_still_wins_over_track_rowid_and_recording_mbid():
    """The new mb branch must not intercept a catalog payload, even one
    that (implausibly) also carries a recording_mbid."""
    catalog = FakeCatalog({100: TrackFacts(
        track_rowid=100, duration_ms=200000, isrc="GBAAA1234567",
        disc_no=1, track_no=3, album_rowid=10,
    )})
    provider = TieredMetadataProvider(catalog=catalog, mb=ExplodingMb(), ytdlp_info=None)
    result = provider.resolve({
        "track_rowid": 100, "recording_mbid": "rec-1", "artist_name": "A",
        "album_name": "B", "track_name": "C",
    })
    assert result.metadata_tier == CATALOG
    assert result.duration_ms == 200000


def test_payload_with_neither_track_rowid_nor_recording_mbid_falls_through_to_ytdlp():
    def fake_ytdlp_info(query_or_url):
        return {"title": "Some Song", "uploader": "Some Artist", "duration": 210,
                 "id": "abc123"}

    provider = TieredMetadataProvider(
        catalog=FakeCatalog(), mb=ExplodingMb(), ytdlp_info=fake_ytdlp_info,
    )
    result = provider.resolve({"artist_name": "Some Artist", "track_name": "Some Song"})
    assert result.metadata_tier == YTDLP


def test_mb_payload_track_id_is_carried_onto_the_resolved_metadata():
    provider = TieredMetadataProvider(
        catalog=ExplodingCatalog(), mb=ExplodingMb(), ytdlp_info=None
    )

    resolved = provider.resolve(dict(MB_PAYLOAD, track_id="sp9"))

    assert resolved.track_id == "sp9"
