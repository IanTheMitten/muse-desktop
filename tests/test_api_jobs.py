import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from muse.api import create_app
from muse.embed import (
    StubAudioEncoder,
    StubAudioVAEncoder,
    StubLyricsEncoder,
    StubLyricsVAEncoder,
)
from muse.config import Config
from muse.ingest import AudioResult, ResolvedMetadata
from tests.conftest import insert_track


def _config(tmp_path):
    return Config(
        root=tmp_path, library_db=tmp_path / "library.sqlite3",
        manifest=tmp_path / "manifest.json", catalog=tmp_path / "catalog.sqlite3",
        audio_features=tmp_path / "features.sqlite3",
        music_root=tmp_path / "MUSIC", art_root=tmp_path / "art",
        web_dist=tmp_path / "web", backup_dir=tmp_path / "backups",
        backup_target=None, port=8080, host="127.0.0.1",
        art_backfill=False, embed_batch=False, content_embed=False,
        catalog_freeze="2025-11-13",
    )


class FakeMetadataProvider:
    def resolve(self, payload):
        return ResolvedMetadata(
            artist_name="Radiohead", artist_names=["Radiohead"],
            album_name="OK Computer", track_name="Airbag",
            metadata_tier="catalog", track_no=1, disc_no=1,
            duration_ms=284000, isrc="GBAAA1234567", track_id="sp1",
            album_rowid=10, artist_rowid=1,
        )


class FakeAudioSource:
    def fetch(self, *, query, out_dir, base_name):
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{base_name}.m4a"
        path.write_bytes(b"fake audio")
        return AudioResult(file_path=path, source_site_id="yt_xyz")


class FakeArtProvider:
    def cover(self, album):
        return None


def _write_catalog_db(path):
    """A real catalog.sqlite3, not a fake -- these two regression tests
    exercise the real TieredMetadataProvider (constructed by create_app
    itself, not injected) against the real POST /api/jobs "track"/"url"
    branches. A prior version of this test suite always injected a fake
    metadata_provider that ignored its payload entirely, which is exactly
    what let two of the four job kinds ship broken (payload missing the
    names TieredMetadataProvider's catalog tier needs; url kind's yt-dlp
    tier never wired to a real probe) despite a fully green suite.
    """
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE artists (rowid INTEGER PRIMARY KEY, id TEXT, name TEXT,
            followers_total INTEGER, popularity INTEGER);
        CREATE TABLE albums (rowid INTEGER PRIMARY KEY, id TEXT, name TEXT,
            album_type TEXT, release_date TEXT, release_date_precision TEXT,
            total_tracks INTEGER, popularity INTEGER);
        CREATE TABLE artist_albums (artist_rowid INTEGER, album_rowid INTEGER,
            is_appears_on INTEGER, is_implicit_appears_on INTEGER);
        CREATE TABLE tracks (rowid INTEGER PRIMARY KEY, id TEXT, name TEXT,
            track_number INTEGER, disc_number INTEGER, duration_ms INTEGER,
            explicit INTEGER, preview_url TEXT, popularity INTEGER,
            album_rowid INTEGER, external_id_isrc TEXT);
        CREATE TABLE track_artists (track_rowid INTEGER, artist_rowid INTEGER);

        INSERT INTO artists VALUES (1, 'sp1', 'Radiohead', 5000000, 80);
        INSERT INTO albums VALUES (10, 'al1', 'OK Computer', 'album',
            '1997-05-21', 'day', 12, 75);
        INSERT INTO artist_albums VALUES (1, 10, 0, 0);
        INSERT INTO tracks VALUES (100, 'tr1', 'Airbag', 1, 1, 284000, 0,
            NULL, 60, 10, 'GBAAA1234567');
        INSERT INTO track_artists VALUES (100, 1);
        """
    )
    conn.commit()
    conn.close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    import muse.ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "tag_m4a", lambda *a, **k: None)
    from muse.lyrics_lrclib import SyncOutcome
    monkeypatch.setattr(
        ingest_mod, "fetch_lyrics",
        lambda track, genius_client: SyncOutcome(state="not_found", synced=False),
    )

    # create_job's "track" branch looks up the track's name via the real
    # catalog before metadata_provider ever sees the payload, so track_rowid
    # 100 needs to exist there even though FakeMetadataProvider itself
    # ignores what it's given -- an EmptyCatalog (the default for a
    # catalog path that doesn't exist) would 404 the request before
    # FakeMetadataProvider gets a chance to run.
    config = _config(tmp_path)
    _write_catalog_db(config.catalog)

    application = create_app(
        config,
        metadata_provider=FakeMetadataProvider(),
        audio_source=FakeAudioSource(),
        genius_client=None,
        # Without this, create_app's default is the real ChainedArtProvider
        # (CoverArtArchive + YtdlpThumbnailArt), which would reach out to
        # MusicBrainz/yt-dlp the moment run_download's art step runs.
        art_provider=FakeArtProvider(),
        # Stub encoders for the same reason `art_provider` is a fake: the
        # defaults are the real CLAP/MiniLM/MER/TextVA, and `run_download`'s
        # final step is an embed backfill that runs whether or not
        # `config.embed_batch` is set. Without these the test reaches Hugging
        # Face for ~1 GB of weights, and the download job it is asserting on
        # is still `running` when the fixture tears the connection down.
        audio_encoder=StubAudioEncoder(),
        lyrics_encoder=StubLyricsEncoder(),
        audio_va_encoder=StubAudioVAEncoder(),
        lyrics_va_encoder=StubLyricsVAEncoder(),
    )
    with TestClient(application) as test_client:
        yield test_client, application


MB_PAYLOAD = {
    "kind": "mb",
    "recording_mbid": "rec-1",
    "release_mbid": "rel-1",
    "artist_name": "Radiohead",
    "artist_names": ["Radiohead"],
    "album_name": "OK Computer",
    "track_name": "Airbag",
    "track_no": 1,
    "disc_no": 1,
    "total_tracks": 12,
    "duration_ms": 284000,
    "isrc": None,
}

CATALOG_RETRY_PAYLOAD = {
    "track_rowid": 100, "artist_name": "Radiohead",
    "artist_names": ["Radiohead"], "album_name": "OK Computer",
    "track_name": "Airbag",
}


def _wait(test_client, job_id):
    status = {}
    for _ in range(50):
        status = test_client.get(f"/api/jobs/{job_id}").json()
        if status["state"] in ("done", "error"):
            break
        time.sleep(0.05)
    return status


def _failed_download(conn, payload):
    from muse.library import jobs as job_rows

    job_id = job_rows.create(conn, "download", payload)
    job_rows.update(conn, job_id, state=job_rows.ERROR)
    return job_id


def test_post_jobs_track_then_worker_completes_it(client):
    test_client, application = client

    response = test_client.post("/api/jobs", json=MB_PAYLOAD)
    assert response.status_code == 200
    status = _wait(test_client, response.json()["job_ids"][0])
    assert status["state"] == "done"

    track = application.state.conn.execute(
        "SELECT * FROM tracks WHERE origin = 'local'"
    ).fetchone()
    assert track["status"] == "done"
    assert track["track_rowid"] >= 1_000_000_000


def test_retry_of_error_track_via_api(client):
    test_client, application = client
    from muse.library import tracks as library_tracks

    library_tracks.write_pending(
        application.state.conn, track_rowid=100, origin="catalog",
        metadata_tier="catalog", artist_name="A", artist_names=["A"],
        album_name="B", track_name="C", source_site_id=None, isrc=None,
    )
    failed = _failed_download(application.state.conn, CATALOG_RETRY_PAYLOAD)
    response = test_client.post(f"/api/jobs/{failed}/retry")
    job_id = response.json()["job_ids"][0]

    status = _wait(test_client, job_id)
    # The pipeline ran again rather than short-circuiting -- confirms
    # Task 10's find_done(status='done' only) check treats an existing
    # 'error' row as a retry target, not a reason to skip. Asserting only
    # state=="done" wouldn't prove this: find_done's own short-circuit also
    # returns state="done", making a wrongly-skipped retry indistinguishable
    # from a genuine one at the job-state level alone -- file_path is only
    # ever set by mark_done, at the end of a genuine run.
    assert status["state"] == "done"
    track = application.state.conn.execute(
        "SELECT * FROM tracks WHERE track_rowid = 100"
    ).fetchone()
    assert track["status"] == "done"
    assert track["file_path"] is not None
    assert Path(track["file_path"]).exists()


def test_post_jobs_track_kind_resolves_via_real_metadata_provider(tmp_path, monkeypatch):
    """Regression test: kind="track" payloads only ever carried a bare
    track_rowid, but TieredMetadataProvider's catalog tier indexes
    payload["artist_name"]/["album_name"]/["track_name"] directly with no
    fallback -- a KeyError, silently caught by run_download_job and
    surfaced only as state="error". Every existing test injected a fake
    metadata_provider that ignores its payload, so this was never caught.
    This test does NOT inject metadata_provider -- create_app builds the
    real TieredMetadataProvider against a real (tiny) catalog.sqlite3, so
    the actual create_job -> catalog.track_by_rowid -> resolve() path
    runs end to end.
    """
    import muse.ingest as ingest_mod
    from muse.lyrics_lrclib import SyncOutcome

    monkeypatch.setattr(ingest_mod, "tag_m4a", lambda *a, **k: None)
    monkeypatch.setattr(
        ingest_mod, "fetch_lyrics",
        lambda track, genius_client: SyncOutcome(state="not_found", synced=False),
    )

    catalog_path = tmp_path / "catalog.sqlite3"
    _write_catalog_db(catalog_path)
    config = Config(
        root=tmp_path, library_db=tmp_path / "library.sqlite3",
        manifest=tmp_path / "manifest.json", catalog=catalog_path,
        audio_features=tmp_path / "features.sqlite3",
        music_root=tmp_path / "MUSIC", art_root=tmp_path / "art",
        web_dist=tmp_path / "web", backup_dir=tmp_path / "backups",
        backup_target=None, port=8080, host="127.0.0.1",
        art_backfill=False, embed_batch=False, content_embed=False,
        catalog_freeze="2025-11-13",
    )

    application = create_app(
        config,
        audio_source=FakeAudioSource(),
        genius_client=None,
        art_provider=FakeArtProvider(),
        # Stub encoders for the same reason `art_provider` is a fake: the
        # defaults are the real CLAP/MiniLM/MER/TextVA, and `run_download`'s
        # final step is an embed backfill that runs whether or not
        # `config.embed_batch` is set. Without these the test reaches Hugging
        # Face for ~1 GB of weights, and the download job it is asserting on
        # is still `running` when the fixture tears the connection down.
        audio_encoder=StubAudioEncoder(),
        lyrics_encoder=StubLyricsEncoder(),
        audio_va_encoder=StubAudioVAEncoder(),
        lyrics_va_encoder=StubLyricsVAEncoder(),
    )
    with TestClient(application) as test_client:
        failed = _failed_download(application.state.conn, CATALOG_RETRY_PAYLOAD)
        response = test_client.post(f"/api/jobs/{failed}/retry")
        assert response.status_code == 200
        job_id = response.json()["job_ids"][0]

        status = _wait(test_client, job_id)
        assert status["state"] == "done", status.get("message")

        track = application.state.conn.execute(
            "SELECT * FROM tracks WHERE track_rowid = 100"
        ).fetchone()
        assert track["status"] == "done"
        assert track["metadata_tier"] == "catalog"
        assert track["artist_name"] == "Radiohead"
        assert track["album_name"] == "OK Computer"
        assert track["track_name"] == "Airbag"


def test_post_jobs_url_kind_resolves_via_real_ytdlp_probe(tmp_path, monkeypatch):
    """Regression test: create_app wired TieredMetadataProvider's yt-dlp
    tier with ytdlp_info=None, so resolve()'s `if self._ytdlp_info is not
    None` guard was always False -- every url-kind payload (no
    track_rowid, so the catalog tier never fires either) failed to
    resolve, unconditionally. FakeYdlProbe stands in for the real
    YoutubeDL this test monkeypatches muse.ingest.YoutubeDL to, so no
    network call happens, but the real TieredMetadataProvider and the
    real _ytdlp_probe_info callback (created by create_app, not injected)
    are what's under test.
    """
    import muse.ingest as ingest_mod
    from muse.lyrics_lrclib import SyncOutcome

    monkeypatch.setattr(ingest_mod, "tag_m4a", lambda *a, **k: None)
    monkeypatch.setattr(
        ingest_mod, "fetch_lyrics",
        lambda track, genius_client: SyncOutcome(state="not_found", synced=False),
    )

    class FakeYdlProbe:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, query, download=False):
            return {"title": "Some Song", "uploader": "Some Artist",
                     "duration": 210, "id": "abc123"}

    monkeypatch.setattr(ingest_mod, "YoutubeDL", FakeYdlProbe)

    application = create_app(
        _config(tmp_path),
        audio_source=FakeAudioSource(),
        genius_client=None,
        art_provider=FakeArtProvider(),
        # Stub encoders for the same reason `art_provider` is a fake: the
        # defaults are the real CLAP/MiniLM/MER/TextVA, and `run_download`'s
        # final step is an embed backfill that runs whether or not
        # `config.embed_batch` is set. Without these the test reaches Hugging
        # Face for ~1 GB of weights, and the download job it is asserting on
        # is still `running` when the fixture tears the connection down.
        audio_encoder=StubAudioEncoder(),
        lyrics_encoder=StubLyricsEncoder(),
        audio_va_encoder=StubAudioVAEncoder(),
        lyrics_va_encoder=StubLyricsVAEncoder(),
    )
    with TestClient(application) as test_client:
        response = test_client.post("/api/jobs", json={
            "kind": "url", "url": "https://youtube.com/watch?v=xyz",
            "artist_name": "Some Artist", "track_name": "Some Song",
        })
        assert response.status_code == 200
        job_id = response.json()["job_ids"][0]

        status = {}
        for _ in range(50):
            status = test_client.get(f"/api/jobs/{job_id}").json()
            if status["state"] in ("done", "error"):
                break
            time.sleep(0.05)
        assert status["state"] == "done", status.get("message")

        track = application.state.conn.execute(
            "SELECT * FROM tracks WHERE origin = 'local'"
        ).fetchone()
        assert track is not None
        assert track["status"] == "done"
        assert track["metadata_tier"] == "ytdlp"

@pytest.mark.parametrize("payload", [
    {"kind": "track", "track_rowid": 100},
    {"kind": "album", "album_rowid": 10},
    {"kind": "artist", "artist_rowid": 1},
    {"track_rowid": 100},
])
def test_catalog_job_kinds_are_refused(client, payload):
    test_client, application = client

    response = test_client.post("/api/jobs", json=payload)
    # A wrongly accepted job is left to finish, not torn down mid-run (that
    # segfaults the shared connection and takes the whole session with it).
    for job_id in response.json().get("job_ids", []):
        _wait(test_client, job_id)

    assert response.status_code == 400
    assert application.state.conn.execute(
        "SELECT COUNT(*) FROM jobs"
    ).fetchone()[0] == 0


@pytest.fixture
def stamp_client(tmp_path, monkeypatch):
    """The real provider (no `metadata_provider`), over a mirror where two
    rows share one ISRC: the stamp must pick the more popular."""
    import muse.ingest as ingest_mod
    from muse.lyrics_lrclib import SyncOutcome

    monkeypatch.setattr(ingest_mod, "tag_m4a", lambda *a, **k: None)
    monkeypatch.setattr(
        ingest_mod, "fetch_lyrics",
        lambda track, genius_client: SyncOutcome(state="not_found", synced=False),
    )
    config = _config(tmp_path)
    _write_catalog_db(config.catalog)
    mirror = sqlite3.connect(config.catalog)
    mirror.executescript(
        """
        INSERT INTO tracks VALUES (101, 'tr9', 'Airbag', 1, 1, 284000, 0,
            NULL, 60, 10, 'GBAAA1234567');
        INSERT INTO tracks VALUES (102, 'tr-low', 'Airbag', 1, 1, 284000, 0,
            NULL, 10, 10, 'GBAAA1234567');
        """
    )
    mirror.commit()
    mirror.close()
    application = create_app(
        config,
        audio_source=FakeAudioSource(),
        genius_client=None,
        art_provider=FakeArtProvider(),
        audio_encoder=StubAudioEncoder(),
        lyrics_encoder=StubLyricsEncoder(),
        audio_va_encoder=StubAudioVAEncoder(),
        lyrics_va_encoder=StubLyricsVAEncoder(),
    )
    with TestClient(application) as test_client:
        yield test_client, application


def _local_rows(application):
    return application.state.conn.execute(
        "SELECT * FROM tracks WHERE origin = 'local'"
    ).fetchall()


def test_an_mb_job_with_an_isrc_stamps_the_mirrors_track_id(stamp_client):
    test_client, application = stamp_client

    response = test_client.post("/api/jobs", json=dict(MB_PAYLOAD, isrc="GBAAA1234567"))
    status = _wait(test_client, response.json()["job_ids"][0])

    assert status["state"] == "done", status.get("message")
    assert [r["track_id"] for r in _local_rows(application)] == ["tr1"]


def test_a_client_sent_track_id_is_not_trusted(stamp_client):
    test_client, application = stamp_client

    response = test_client.post(
        "/api/jobs", json=dict(MB_PAYLOAD, isrc=None, track_id="evil")
    )
    status = _wait(test_client, response.json()["job_ids"][0])

    assert status["state"] == "done", status.get("message")
    assert [r["track_id"] for r in _local_rows(application)] == [None]


def test_an_mb_job_whose_track_id_is_already_done_is_skipped(stamp_client):
    test_client, application = stamp_client
    insert_track(application.state.conn, 7, track_id="tr1", status="done")

    response = test_client.post("/api/jobs", json=dict(MB_PAYLOAD, isrc="GBAAA1234567"))
    status = _wait(test_client, response.json()["job_ids"][0])

    assert status["state"] == "done"
    assert status["message"] == "already in library"
    assert _local_rows(application) == []


def test_an_mb_job_whose_track_id_holder_errored_downloads_anew(stamp_client):
    test_client, application = stamp_client
    insert_track(application.state.conn, 7, track_id="tr1", status="error")
    application.state.conn.execute("UPDATE tracks SET origin='local' WHERE track_rowid=7")
    application.state.conn.commit()

    response = test_client.post("/api/jobs", json=dict(MB_PAYLOAD, isrc="GBAAA1234567"))
    status = _wait(test_client, response.json()["job_ids"][0])

    assert status["state"] == "done", status.get("message")
    assert sorted(r["track_id"] or "" for r in _local_rows(application)) == ["", "tr1"]
    old = application.state.conn.execute(
        "SELECT track_id FROM tracks WHERE track_rowid = 7"
    ).fetchone()
    assert old["track_id"] is None
