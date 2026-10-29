from pathlib import Path

import pytest

from muse.catalog import TrackFacts
from muse.ingest import ResolvedMetadata, TieredMetadataProvider, run_download
from muse.library import art as library_art
from muse.library import lyrics_state as library_lyrics_state
from muse.lyrics_lrclib import SyncOutcome
from tests.conftest import insert_track


class FakeMetadataProvider:
    def __init__(self, result):
        self._result = result

    def resolve(self, payload):
        return self._result


class FakeAudioSource:
    def __init__(self, tmp_path, should_fail=False, duration_s=None):
        self.tmp_path = tmp_path
        self.should_fail = should_fail
        # `None` is what a source that cannot say reports, and the default
        # here for the same reason it is the default on `AudioResult`: every
        # test that is not about the length check should skip it.
        self.duration_s = duration_s

    def fetch(self, *, query, out_dir, base_name):
        if self.should_fail:
            raise RuntimeError("no video found")
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{base_name}.m4a"
        path.write_bytes(b"fake audio")
        from muse.ingest import AudioResult
        return AudioResult(
            file_path=path, source_site_id="yt_xyz", duration_s=self.duration_s
        )


class FakeArtProvider:
    """Stands in for a ChainedArtProvider — always misses, since the art
    step's own failure-isolation (not the art-source's behavior) is what
    these pipeline tests are checking."""

    def cover(self, album):
        return None


def _metadata(duration_ms=284000):
    return ResolvedMetadata(
        artist_name="Radiohead", artist_names=["Radiohead"],
        album_name="OK Computer", track_name="Airbag",
        metadata_tier="catalog", track_no=1, disc_no=1,
        duration_ms=duration_ms, isrc="GBAAA1234567", track_id="sp1",
        album_rowid=10, artist_rowid=1,
    )


def test_full_pipeline_success(conn, tmp_path, monkeypatch):
    import muse.ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "tag_m4a", lambda *a, **k: None)
    monkeypatch.setattr(
        ingest_mod, "fetch_lyrics",
        lambda track, genius_client: SyncOutcome(state="not_found", synced=False),
    )

    result = run_download(
        conn,
        payload={"track_rowid": 100},
        metadata_provider=FakeMetadataProvider(_metadata()),
        audio_source=FakeAudioSource(tmp_path),
        genius_client=None,
        music_root=tmp_path / "MUSIC",
        art_root=tmp_path / "art",
        art_provider=FakeArtProvider(),
        embed_backfill=lambda: None,
    )
    assert result.status == "done"
    row = conn.execute(
        "SELECT * FROM tracks WHERE track_rowid = 100"
    ).fetchone()
    assert row["status"] == "done"
    assert row["file_path"] is not None
    assert Path(row["file_path"]).exists()
    assert row["metadata_tier"] == "catalog"
    assert row["source_site_id"] == "yt_xyz"

    fts_row = conn.execute(
        "SELECT rowid FROM tracks_fts WHERE tracks_fts MATCH 'Airbag'"
    ).fetchone()
    assert fts_row is not None
    assert fts_row["rowid"] == 100


def test_idempotent_skip_when_already_done(conn, tmp_path):
    from muse.library import tracks as library_tracks

    library_tracks.write_pending(
        conn, track_rowid=100, origin="catalog", metadata_tier="catalog",
        artist_name="A", artist_names=["A"], album_name="B", track_name="C",
        source_site_id=None, isrc=None,
    )
    library_tracks.mark_done(conn, 100, file_path="/already/there.m4a",
                               lyrics_path=None, art_path=None)

    result = run_download(
        conn,
        payload={"track_rowid": 100},
        metadata_provider=FakeMetadataProvider(_metadata()),
        audio_source=FakeAudioSource(tmp_path),
        genius_client=None,
        music_root=tmp_path / "MUSIC",
        art_root=tmp_path / "art",
        art_provider=FakeArtProvider(),
        embed_backfill=lambda: None,
    )
    assert result.status == "done"
    assert result.message == "already in library"


def test_error_row_written_before_download_starts(conn, tmp_path):
    """The tracks row exists as status='error' even if we inspect it mid-job
    -- proven here by making the audio source raise, then checking the row
    survived as error rather than never having been written."""
    result = run_download(
        conn,
        payload={"track_rowid": 100},
        metadata_provider=FakeMetadataProvider(_metadata()),
        audio_source=FakeAudioSource(tmp_path, should_fail=True),
        genius_client=None,
        music_root=tmp_path / "MUSIC",
        art_root=tmp_path / "art",
        art_provider=FakeArtProvider(),
        embed_backfill=lambda: None,
    )
    assert result.status == "error"
    row = conn.execute(
        "SELECT * FROM tracks WHERE track_rowid = 100"
    ).fetchone()
    assert row is not None
    assert row["status"] == "error"
    assert row["file_path"] is None


def test_retry_of_error_row_proceeds_into_pipeline(conn, tmp_path, monkeypatch):
    import muse.ingest as ingest_mod
    from muse.library import tracks as library_tracks

    monkeypatch.setattr(ingest_mod, "tag_m4a", lambda *a, **k: None)
    monkeypatch.setattr(
        ingest_mod, "fetch_lyrics",
        lambda track, genius_client: SyncOutcome(state="not_found", synced=False),
    )

    library_tracks.write_pending(
        conn, track_rowid=100, origin="catalog", metadata_tier="catalog",
        artist_name="A", artist_names=["A"], album_name="B", track_name="C",
        source_site_id=None, isrc=None,
    )  # status is 'error' -- simulates a prior failed attempt

    result = run_download(
        conn,
        payload={"track_rowid": 100},
        metadata_provider=FakeMetadataProvider(_metadata()),
        audio_source=FakeAudioSource(tmp_path),
        genius_client=None,
        music_root=tmp_path / "MUSIC",
        art_root=tmp_path / "art",
        art_provider=FakeArtProvider(),
        embed_backfill=lambda: None,
    )
    assert result.status == "done"
    assert result.message != "already in library"


def test_lyrics_or_art_failure_still_lands_done(conn, tmp_path, monkeypatch):
    import muse.ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "tag_m4a", lambda *a, **k: None)
    monkeypatch.setattr(
        ingest_mod, "fetch_lyrics",
        lambda track, genius_client: (_ for _ in ()).throw(RuntimeError("lyrics API down")),
    )

    class RaisingArtProvider:
        def cover(self, album):
            raise RuntimeError("Cover Art Archive is down")

    result = run_download(
        conn,
        payload={"track_rowid": 100},
        metadata_provider=FakeMetadataProvider(_metadata()),
        audio_source=FakeAudioSource(tmp_path),
        genius_client=None,
        music_root=tmp_path / "MUSIC",
        art_root=tmp_path / "art",
        art_provider=RaisingArtProvider(),
        embed_backfill=lambda: None,
    )
    assert result.status == "done"


def test_embed_backfill_called_after_indexing(conn, tmp_path, monkeypatch):
    import muse.ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "tag_m4a", lambda *a, **k: None)
    monkeypatch.setattr(
        ingest_mod, "fetch_lyrics",
        lambda track, genius_client: SyncOutcome(state="not_found", synced=False),
    )

    calls = []
    run_download(
        conn,
        payload={"track_rowid": 100},
        metadata_provider=FakeMetadataProvider(_metadata()),
        audio_source=FakeAudioSource(tmp_path),
        genius_client=None,
        music_root=tmp_path / "MUSIC",
        art_root=tmp_path / "art",
        art_provider=FakeArtProvider(),
        embed_backfill=lambda: calls.append(1),
    )
    assert calls == [1]


# --- Post-freeze (MusicBrainz) downloads -------------------------------------
#
# These run against the real TieredMetadataProvider rather than
# FakeMetadataProvider: what is under test is the whole composed path from a
# job payload through resolve() to the tracks row, and a hand-built
# ResolvedMetadata could silently disagree with what resolve() actually
# returns for the same payload.


class ExplodingMb:
    """Any call is a bug: an `mb` payload already carries every fact."""

    def get(self, path, params):
        raise AssertionError(f"resolve() reached MusicBrainz for {path}")


class FakeCatalog:
    def __init__(self, facts=None):
        self.facts = facts or {}

    def track_facts(self, rowids):
        return {r: self.facts[r] for r in rowids if r in self.facts}


@pytest.fixture
def no_network(monkeypatch):
    """`tag_m4a` needs mutagen and `fetch_lyrics` calls LRCLIB. Both are
    already isolated behind run_download's try/except -- but a swallowed
    exception still means the request went out, so these tests stub them the
    same way every other test in this file does."""
    import muse.ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "tag_m4a", lambda *a, **k: None)
    monkeypatch.setattr(
        ingest_mod, "fetch_lyrics",
        lambda track, genius_client: SyncOutcome(state="not_found", synced=False),
    )


def _deps(tmp_path, *, catalog=None, metadata_provider=None, audio_source=None):
    """Everything run_download needs except `conn` and `payload`."""
    return {
        "metadata_provider": metadata_provider or TieredMetadataProvider(
            catalog=catalog or FakeCatalog(), mb=ExplodingMb(), ytdlp_info=None,
        ),
        "audio_source": audio_source or FakeAudioSource(tmp_path),
        "genius_client": None,
        "music_root": tmp_path / "MUSIC",
        "art_root": tmp_path / "art",
        "art_provider": FakeArtProvider(),
        "embed_backfill": lambda: None,
    }


MB_PAYLOAD = {
    "kind": "mb",
    "recording_mbid": "rec-1",
    "release_mbid": "rel-1",
    "artist_name": "Artist One",
    "artist_names": ["Artist One"],
    "album_name": "The Album",
    "track_name": "One",
    "track_no": 1,
    "disc_no": 1,
    "total_tracks": 2,
    "duration_ms": 214000,
    "isrc": None,
}


def test_an_mb_download_stores_both_mbids_and_a_local_album_rowid(
    conn, tmp_path, no_network
):
    result = run_download(conn, payload=dict(MB_PAYLOAD), **_deps(tmp_path))

    assert result.status == "done"
    row = conn.execute(
        "SELECT * FROM tracks WHERE track_rowid = ?", (result.track_rowid,)
    ).fetchone()
    assert row["recording_mbid"] == "rec-1"
    assert row["release_mbid"] == "rel-1"
    assert row["origin"] == "local"
    assert row["metadata_tier"] == "musicbrainz"
    assert row["album_rowid"] >= 1_000_000_000


def test_two_tracks_of_one_release_land_in_one_album(conn, tmp_path, no_network):
    first = run_download(conn, payload=dict(MB_PAYLOAD), **_deps(tmp_path))
    second_payload = dict(MB_PAYLOAD, recording_mbid="rec-2",
                          track_name="Two", track_no=2)
    second = run_download(conn, payload=second_payload, **_deps(tmp_path))

    rowids = {
        conn.execute(
            "SELECT album_rowid FROM tracks WHERE track_rowid = ?", (rowid,)
        ).fetchone()["album_rowid"]
        for rowid in (first.track_rowid, second.track_rowid)
    }
    assert len(rowids) == 1
    # `{None}` is also one value -- and is what passing metadata.album_rowid
    # straight through would produce, so the shared id has to be a real one.
    assert rowids.pop() >= 1_000_000_000
    assert first.track_rowid != second.track_rowid


def test_queueing_the_same_recording_twice_downloads_once(conn, tmp_path, no_network):
    first = run_download(conn, payload=dict(MB_PAYLOAD), **_deps(tmp_path))
    second = run_download(conn, payload=dict(MB_PAYLOAD), **_deps(tmp_path))

    assert second.status == "done"
    assert second.message == "already in library"
    assert second.track_rowid == first.track_rowid
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM tracks WHERE recording_mbid = 'rec-1'"
    ).fetchone()["n"] == 1


def test_the_art_provider_is_handed_the_release_mbid(conn, tmp_path, no_network):
    """Two tracks, because one proves nothing: the release's first track
    allocates the 1e9 floor for both its track rowid and its album rowid, so
    a single-track assertion is green whether the AlbumRef was keyed on the
    allocated album or fell back to `resolved_rowid`. The second track is
    where those two numbers diverge -- and keying on the track rowid means
    one Cover Art Archive fetch and one cached sleeve per track, which is
    the whole cost this task exists to avoid."""
    seen: list = []

    class RecordingArt:
        def cover(self, album):
            seen.append(album)
            return None

    def _run(payload):
        deps = _deps(tmp_path)
        deps["art_provider"] = RecordingArt()
        return run_download(conn, payload=payload, **deps)

    first = _run(dict(MB_PAYLOAD))
    second = _run(dict(MB_PAYLOAD, recording_mbid="rec-2", track_name="Two",
                       track_no=2))

    assert [ref.release_mbid for ref in seen] == ["rel-1", "rel-1"]
    assert seen[0].album_rowid == seen[1].album_rowid
    assert seen[0].album_rowid >= 1_000_000_000
    assert seen[0].tracks == (("One", 214000),)
    # ...and it is the album the rows actually landed in, not some third id.
    assert first.track_rowid != second.track_rowid
    for ref, result in zip(seen, (first, second)):
        row = conn.execute(
            "SELECT album_rowid FROM tracks WHERE track_rowid = ?",
            (result.track_rowid,),
        ).fetchone()
        assert ref.album_rowid == row["album_rowid"]


def test_a_ytdlp_download_allocates_no_album_at_all(conn, tmp_path, no_network):
    """The guard on the allocator is `album_rowid is None AND release_mbid is
    not None`, and the second conjunct is load-bearing here rather than on the
    catalog path: a free-text payload has both as None, so dropping it would
    call album_rowid_for_release(conn, None), whose `WHERE release_mbid = NULL`
    matches nothing, and every free-text single would allocate a fresh phantom
    album into the gallery."""
    provider = TieredMetadataProvider(
        catalog=FakeCatalog(), mb=ExplodingMb(),
        ytdlp_info=lambda query: {
            "title": "Some Song", "uploader": "Some Artist", "duration": 210,
            "id": "abc123",
        },
    )
    result = run_download(
        conn, payload={"artist_name": "Some Artist", "track_name": "Some Song"},
        **_deps(tmp_path, metadata_provider=provider),
    )

    assert result.status == "done"
    row = conn.execute(
        "SELECT * FROM tracks WHERE track_rowid = ?", (result.track_rowid,)
    ).fetchone()
    assert row["metadata_tier"] == "ytdlp"
    assert row["origin"] == "local"
    assert row["album_rowid"] is None
    assert row["release_mbid"] is None


def test_a_catalog_download_keeps_its_catalog_album_and_never_allocates(
    conn, tmp_path, no_network, monkeypatch
):
    """The regression that matters most: the pre-existing catalog path must
    be untouched. Entering the allocator at all is the failure -- the call
    site is not inside a try/except, so a raising stub fails the test."""
    from muse.library import tracks as library_tracks

    def exploding_allocator(*args, **kwargs):
        raise AssertionError("a catalog payload reached the local allocator")

    monkeypatch.setattr(library_tracks, "album_rowid_for_release",
                        exploding_allocator)

    catalog = FakeCatalog({100: TrackFacts(
        track_rowid=100, duration_ms=284000, isrc="GBAAA1234567",
        disc_no=1, track_no=1, album_rowid=10,
    )})
    payload = {"track_rowid": 100, "artist_name": "Radiohead",
               "album_name": "OK Computer", "track_name": "Airbag"}

    result = run_download(conn, payload=payload, **_deps(tmp_path, catalog=catalog))

    assert result.status == "done"
    assert result.track_rowid == 100
    row = conn.execute(
        "SELECT * FROM tracks WHERE track_rowid = 100"
    ).fetchone()
    assert row["origin"] == "catalog"
    assert row["metadata_tier"] == "catalog"
    assert row["album_rowid"] == 10
    assert row["recording_mbid"] is None
    assert row["release_mbid"] is None


def test_a_retried_mb_track_rejoins_its_siblings_album(conn, tmp_path, no_network):
    """A retry replays the payload verbatim and allocates a *fresh* local
    rowid (migration 13's docstring: no UNIQUE on recording_mbid, precisely
    so this is legal). The new row still has to land in the album its
    siblings are already in."""
    failed = run_download(
        conn, payload=dict(MB_PAYLOAD),
        **_deps(tmp_path, audio_source=FakeAudioSource(tmp_path, should_fail=True)),
    )
    assert failed.status == "error"

    # Hazard A: the failed placeholder must already carry release_mbid AND a
    # non-null album_rowid. A row with the mbid but a null album_rowid is
    # invisible to the allocator's `album_rowid IS NOT NULL` reuse query, so
    # the sibling below would allocate a second album for the same release.
    failed_row = conn.execute(
        "SELECT * FROM tracks WHERE track_rowid = ?", (failed.track_rowid,)
    ).fetchone()
    assert failed_row["status"] == "error"
    assert failed_row["release_mbid"] == "rel-1"
    assert failed_row["album_rowid"] is not None

    sibling = run_download(
        conn,
        payload=dict(MB_PAYLOAD, recording_mbid="rec-2", track_name="Two",
                     track_no=2),
        **_deps(tmp_path),
    )
    retried = run_download(conn, payload=dict(MB_PAYLOAD), **_deps(tmp_path))

    assert sibling.status == "done"
    assert retried.status == "done"
    assert retried.message != "already in library"
    assert retried.track_rowid != failed.track_rowid  # a fresh local rowid

    albums = {
        conn.execute(
            "SELECT album_rowid FROM tracks WHERE track_rowid = ?", (rowid,)
        ).fetchone()["album_rowid"]
        for rowid in (failed.track_rowid, sibling.track_rowid, retried.track_rowid)
    }
    assert len(albums) == 1
    assert albums.pop() >= 1_000_000_000


def test_audio_far_longer_than_the_track_is_refused(conn, tmp_path, monkeypatch):
    """`ytsearch1:Artist - Title` answers with the whole record whenever a
    title track shares the album's name, and nothing downstream noticed: the
    real case wrote 52 minutes of audio into the library as a 20-second
    track, tagged, indexed, and embedded -- CLAP over the wrong 52 minutes,
    feeding the recommender.

    Refused rather than kept, because there is a designed way to say what to
    download instead: `POST /api/jobs {"kind": "url", ...}` (PRD story 86).
    The error message names it.
    """
    import muse.ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "tag_m4a", lambda *a, **k: None)
    monkeypatch.setattr(
        ingest_mod, "fetch_lyrics",
        lambda track, genius_client: SyncOutcome(state="not_found", synced=False),
    )

    result = run_download(
        conn,
        payload={"track_rowid": 100},
        metadata_provider=FakeMetadataProvider(_metadata(duration_ms=20502)),
        audio_source=FakeAudioSource(tmp_path, duration_s=3130),
        genius_client=None,
        music_root=tmp_path / "MUSIC",
        art_root=tmp_path / "art",
        art_provider=FakeArtProvider(),
        embed_backfill=lambda: None,
    )

    assert result.status == "error"
    assert "3130s" in result.message and "21s" in result.message
    assert "url" in result.message
    row = conn.execute("SELECT * FROM tracks WHERE track_rowid = 100").fetchone()
    assert row["status"] == "error"
    assert row["file_path"] is None
    # The file has to go, not just the row: yt-dlp runs with
    # `overwrites: False`, so a retry left staring at the wrong 52 minutes
    # would skip the download and accept them a second time.
    assert list((tmp_path / "MUSIC").rglob("*.m4a")) == []


def test_a_download_a_little_longer_than_the_track_is_kept(conn, tmp_path, monkeypatch):
    """Real uploads run over: of the thirteen songs on the record that
    exposed this, one arrived 25 s longer than MusicBrainz's length and
    another 17 s, both correct. The check is for a different result
    altogether, not for a lead-in or a trailing silence."""
    import muse.ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "tag_m4a", lambda *a, **k: None)
    monkeypatch.setattr(
        ingest_mod, "fetch_lyrics",
        lambda track, genius_client: SyncOutcome(state="not_found", synced=False),
    )

    result = run_download(
        conn,
        payload={"track_rowid": 100},
        metadata_provider=FakeMetadataProvider(_metadata(duration_ms=225000)),
        audio_source=FakeAudioSource(tmp_path, duration_s=250),
        genius_client=None,
        music_root=tmp_path / "MUSIC",
        art_root=tmp_path / "art",
        art_provider=FakeArtProvider(),
        embed_backfill=lambda: None,
    )

    assert result.status == "done", result.message


def test_a_source_that_reports_no_duration_is_not_refused(conn, tmp_path, monkeypatch):
    """An explicit `url` job may point at a source yt-dlp gives no length
    for. Unknown is not a mismatch, and refusing on it would break the very
    escape hatch the refusal message tells people to use."""
    import muse.ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "tag_m4a", lambda *a, **k: None)
    monkeypatch.setattr(
        ingest_mod, "fetch_lyrics",
        lambda track, genius_client: SyncOutcome(state="not_found", synced=False),
    )

    result = run_download(
        conn,
        payload={"track_rowid": 100},
        metadata_provider=FakeMetadataProvider(_metadata(duration_ms=20502)),
        audio_source=FakeAudioSource(tmp_path, duration_s=None),
        genius_client=None,
        music_root=tmp_path / "MUSIC",
        art_root=tmp_path / "art",
        art_provider=FakeArtProvider(),
        embed_backfill=lambda: None,
    )

    assert result.status == "done", result.message


def test_a_track_of_unknown_length_is_not_refused(conn, tmp_path, monkeypatch):
    """The other half of the same rule: MusicBrainz often has no length, and
    a release full of nulls must still be downloadable."""
    import muse.ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "tag_m4a", lambda *a, **k: None)
    monkeypatch.setattr(
        ingest_mod, "fetch_lyrics",
        lambda track, genius_client: SyncOutcome(state="not_found", synced=False),
    )

    result = run_download(
        conn,
        payload={"track_rowid": 100},
        metadata_provider=FakeMetadataProvider(_metadata(duration_ms=None)),
        audio_source=FakeAudioSource(tmp_path, duration_s=3130),
        genius_client=None,
        music_root=tmp_path / "MUSIC",
        art_root=tmp_path / "art",
        art_provider=FakeArtProvider(),
        embed_backfill=lambda: None,
    )

    assert result.status == "done", result.message


def test_a_done_row_for_the_same_song_short_circuits_an_mb_download(
    conn, tmp_path, no_network
):
    insert_track(conn, 100, artist_name="Artist One", track_name="One",
                 duration_ms=215000)

    result = run_download(conn, payload=dict(MB_PAYLOAD), **_deps(tmp_path))

    assert (result.status, result.message, result.track_rowid) == (
        "done", "already in library", 100)


def test_a_different_version_of_the_song_still_downloads(
    conn, tmp_path, no_network
):
    insert_track(conn, 100, artist_name="Artist One", track_name="One - Live",
                 duration_ms=215000)

    result = run_download(conn, payload=dict(MB_PAYLOAD), **_deps(tmp_path))

    assert result.status == "done"
    assert result.message == "ok"
    assert result.track_rowid != 100


def test_an_errored_track_id_holder_is_handed_the_id_by_the_new_row(
    conn, tmp_path, no_network
):
    insert_track(conn, 7, track_id="sp-x", status="error")
    conn.execute("UPDATE tracks SET origin='local' WHERE track_rowid=?", (7,))

    result = run_download(
        conn, payload=dict(MB_PAYLOAD, track_id="sp-x"), **_deps(tmp_path)
    )

    assert result.message == "ok"
    new = conn.execute(
        "SELECT track_id FROM tracks WHERE track_rowid = ?", (result.track_rowid,)
    ).fetchone()
    assert new["track_id"] == "sp-x"


def test_a_done_track_id_holder_short_circuits_the_download(
    conn, tmp_path, no_network
):
    insert_track(conn, 7, track_id="sp-x", status="done")

    result = run_download(
        conn, payload=dict(MB_PAYLOAD, track_id="sp-x"), **_deps(tmp_path)
    )

    assert result.message == "already in library"
    assert result.track_rowid == 7
