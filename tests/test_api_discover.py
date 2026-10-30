"""The discovery surface: browsing MusicBrainz for what the mirror never saw.

The catalog mirror froze on 2025-11-13, so `/api/catalog/*` cannot offer a
record released since. These routes ask MusicBrainz the same four questions and
end at the same tap -- a `POST /api/jobs` -- except that the payload carries
MusicBrainz ids instead of a `track_rowid`.

Nothing here reaches the network. `create_app(mb=...)` takes the one client the
process shares, and every test hands it a fake that replays canned answers and
records what was asked.
"""

import asyncio
import copy
import time

import pytest
from fastapi.testclient import TestClient

from muse.api import create_app
from muse.embed import (
    StubAudioEncoder,
    StubAudioVAEncoder,
    StubLyricsEncoder,
    StubLyricsVAEncoder,
)
from tests.conftest import make_config
from muse.library import jobs as job_rows

from tests.conftest import insert_track


class MirrorOffCatalog:
    """The mirror, switched off: every MusicBrainz path must work without it.

    Mirror reads raise, so a route that still reaches for the mirror fails
    loudly. `track_id_for_isrc` is the one lookup a library-side join may do.
    """

    def __init__(self, isrc_ids=None):
        self.isrc_ids = isrc_ids or {}

    def _refuse(self, *args, **kwargs):
        raise AssertionError("mirror search on a MusicBrainz path")

    search_artists = artist_albums = album_tracks = _refuse
    track_by_rowid = artist_by_rowid = _refuse

    def track_facts(self, rowids):
        return {}

    def track_id_for_isrc(self, isrc):
        return self.isrc_ids.get(isrc)


class FakeMusicBrainz:
    """Canned answers by path, and a log of what was asked.

    An answer may be a dict (replayed as-is -- `{}` being what the real
    client returns for a 404, i.e. MusicBrainz answering that it holds no
    such thing), None (MusicBrainz did not answer at all), or a callable
    taking the params, for the endpoints that answer differently depending on
    how they were asked.
    """

    def __init__(self, answers):
        self.answers = dict(answers)
        self.calls: list[tuple[str, dict]] = []

    def get(self, path, params):
        self.calls.append((path, dict(params)))
        answer = self.answers.get(path)
        return answer(params) if callable(answer) else answer

    def params_for(self, path):
        return [params for asked, params in self.calls if asked == path]


# Real MBID shapes, not readable stand-ins: two of these travel as path
# parameters that now refuse anything that is not a UUID, so a test using
# `rg-new` would be testing the 422 rather than the route.
ARTIST_MBID = "11111111-1111-4111-8111-111111111111"
OTHER_ARTIST_MBID = "1e1e1e1e-1e1e-4e1e-8e1e-1e1e1e1e1e1e"
NEW_GROUP_MBID = "22222222-2222-4222-8222-222222222222"
OLD_GROUP_MBID = "33333333-3333-4333-8333-333333333333"
DELUXE_MBID = "44444444-4444-4444-8444-444444444444"
SINGLE_DISC_MBID = "55555555-5555-4555-8555-555555555555"
RECORDING_ONE = "66666666-6666-4666-8666-666666666666"
RECORDING_TWO = "77777777-7777-4777-8777-777777777777"

ARTIST_SEARCH = {
    "artists": [
        {"id": ARTIST_MBID, "name": "Post Malone", "disambiguation": "",
         "score": 100},
        {"id": OTHER_ARTIST_MBID, "name": "Post Malone Zombie",
         "disambiguation": "tribute act", "score": 62},
    ]
}

SPOTIFY_URL_RELS = {
    "relations": [
        {"target-type": "release", "release": {"id": "not-an-artist"}},
        {"target-type": "artist", "artist": {"id": ARTIST_MBID,
                                             "name": "Post Malone"}},
    ]
}

NEW_GROUP = {"id": NEW_GROUP_MBID, "title": "Acts of Faith",
             "first-release-date": "2025-12-05", "primary-type": "Album"}
OLD_GROUP = {"id": OLD_GROUP_MBID, "title": "Before The Freeze",
             "first-release-date": "2019-06-01", "primary-type": "Album"}


def _release_groups(params):
    """The two endpoints `discover.release_groups` picks between.

    `query` means the indexed search (the filtered path), `artist` means the
    paged browse (the unfiltered one). The envelopes differ in exactly one
    key, which is the only way a caller could tell which answered -- so the
    fake keeps them different too, or the tests could not tell either.
    """
    if "query" in params:
        return {"count": 2, "release-groups": [NEW_GROUP, OLD_GROUP]}
    return {"release-group-count": 2, "release-groups": [OLD_GROUP, NEW_GROUP]}


# Two pressings of one record. The deluxe has both tracks; picking the other
# would silently hide a song, which is the whole point of `pick_release_mbid`.
RELEASES = {
    "releases": [
        {"id": SINGLE_DISC_MBID, "status": "Official", "date": "2025-12-05",
         "media": [{"format": "CD", "track-count": 1}]},
        {"id": DELUXE_MBID, "status": "Official", "date": "2025-12-05",
         "media": [{"format": "Digital Media", "track-count": 2}]},
    ]
}

# A compilation-shaped release: the record is credited to one name, and the
# second track is credited to another. That difference is what the merge order
# in `create_job`'s `mb_release` branch exists to preserve.
RELEASE_TRACKS = {
    "id": DELUXE_MBID,
    "title": "Acts of Faith",
    "date": "2025-12-05",
    "artist-credit": [{"name": "Various Artists"}],
    "media": [
        {
            "position": 1,
            "track-count": 2,
            "tracks": [
                {"title": "Opening", "position": 1, "length": 200_000,
                 "recording": {"id": RECORDING_ONE, "isrcs": ["GBAAA0000001"]}},
                {"title": "Guest Spot", "position": 2, "length": 210_000,
                 "artist-credit": [{"name": "Someone Else"}],
                 "recording": {"id": RECORDING_TWO, "isrcs": []}},
            ],
        }
    ],
}


def _answers():
    return {
        "artist": ARTIST_SEARCH,
        "url": SPOTIFY_URL_RELS,
        "release-group": _release_groups,
        "release": RELEASES,
        f"release/{DELUXE_MBID}": RELEASE_TRACKS,
    }


class FakeAudioSource:
    """The download worker is a live consumer of the queue these tests post
    to, so it will actually run the jobs they create. Without this it would
    run the real yt-dlp -- an `mb` payload resolves cleanly through the real
    `TieredMetadataProvider`'s MusicBrainz tier, so nothing stops it earlier.
    """

    def fetch(self, *, query, out_dir, base_name):
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{base_name}.m4a"
        path.write_bytes(b"fake audio")
        from muse.ingest import AudioResult
        return AudioResult(file_path=path, source_site_id="yt_fake")


class FakeArtProvider:
    def cover(self, album):
        return None


def _config(tmp_path, **overrides):
    return make_config(tmp_path, **overrides)


@pytest.fixture
def client(tmp_path, monkeypatch):
    import muse.ingest as ingest_mod
    from muse.lyrics_lrclib import SyncOutcome

    monkeypatch.setattr(ingest_mod, "tag_m4a", lambda *a, **k: None)
    monkeypatch.setattr(
        ingest_mod, "fetch_lyrics",
        lambda track: SyncOutcome(state="not_found", synced=False),
    )
    monkeypatch.setattr("muse.api._open_catalog", lambda config: MirrorOffCatalog())

    config = _config(tmp_path)
    # `metadata_provider` is deliberately *not* injected: these tests are
    # about the payloads `create_job` builds, and only the real
    # `TieredMetadataProvider` proves a payload resolves at all.
    app = create_app(
        config,
        mb=FakeMusicBrainz(_answers()),
        audio_source=FakeAudioSource(),
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
    with TestClient(app) as test_client:
        yield test_client, app


def _settle(test_client, job_ids):
    """Let the worker finish the jobs a test queued, and say how each ended.

    Not politeness: the worker is a live consumer of the same queue, so a test
    that returns the moment it has posted leaves `run_download` executing on a
    threadpool thread while the fixture's teardown closes the connection under
    it. It also means every queueing test below runs its payload through the
    real `TieredMetadataProvider` rather than only asserting the row we wrote.
    """
    states = []
    for job_id in job_ids:
        for _ in range(100):
            state = test_client.get(f"/api/jobs/{job_id}").json()["state"]
            if state in ("done", "error"):
                break
            time.sleep(0.05)
        states.append(state)
    return states


def _payloads(conn, job_ids):
    return [job_rows.get(conn, job_id)["payload"] for job_id in job_ids]


# --- browse ---------------------------------------------------------------


def test_artist_search_passes_musicbrainz_order_through(client):
    test_client, app = client

    results = test_client.get(
        "/api/discover/artists", params={"q": "Post Malone", "limit": 5}
    ).json()

    assert [r["mbid"] for r in results] == [ARTIST_MBID, OTHER_ARTIST_MBID]
    assert results[1]["disambiguation"] == "tribute act"
    assert results[0]["score"] == 100
    assert app.state.mb.params_for("artist")[0]["limit"] == 5


def test_the_default_kind_is_albums_newest_first(client):
    """Browse has no order of its own, so ours is the only one."""
    test_client, app = client

    groups = test_client.get(f"/api/discover/artists/{ARTIST_MBID}/releases").json()

    assert [g["mbid"] for g in groups] == [NEW_GROUP_MBID, OLD_GROUP_MBID]
    asked = app.state.mb.params_for("release-group")[0]
    assert asked["artist"] == ARTIST_MBID and asked["type"] == "album"
    assert "query" not in asked


def test_kind_single_asks_for_singles_and_eps(client):
    test_client, app = client

    test_client.get(
        f"/api/discover/artists/{ARTIST_MBID}/releases", params={"kind": "single"}
    )

    assert app.state.mb.params_for("release-group")[0]["type"] == "single|ep"


def test_kind_appears_on_is_refused(client):
    test_client, _ = client

    assert test_client.get(
        f"/api/discover/artists/{ARTIST_MBID}/releases",
        params={"kind": "appears_on"},
    ).status_code == 422


def test_nothing_released_and_no_answer_are_different_answers(client):
    """Empty browse is `200 []`; silence is a 502, so the screen can word the
    two differently."""
    test_client, app = client

    app.state.mb.answers["release-group"] = {"release-group-count": 0, "release-groups": []}
    empty = test_client.get(f"/api/discover/artists/{ARTIST_MBID}/releases")
    assert empty.status_code == 200
    assert empty.json() == []

    app.state.mb.answers["release-group"] = None
    assert test_client.get(
        f"/api/discover/artists/{ARTIST_MBID}/releases"
    ).status_code == 502


def test_artist_search_502s_when_musicbrainz_does_not_answer(client):
    test_client, app = client
    app.state.mb.answers["artist"] = None

    assert test_client.get("/api/discover/artists", params={"q": "x"}).status_code == 502


def test_artist_search_with_no_matches_is_an_empty_200(client):
    test_client, app = client
    app.state.mb.answers["artist"] = {}

    response = test_client.get("/api/discover/artists", params={"q": "x"})

    assert response.status_code == 200
    assert response.json() == []


def test_artist_search_says_what_we_already_hold(client):
    test_client, app = client
    conn = app.state.conn
    insert_track(conn, 100, artist_name="post malone")
    insert_track(conn, 101, artist_name="Post Malone", status="missing_file")
    insert_track(conn, 102, artist_name="Post Malone", status="error")

    results = test_client.get("/api/discover/artists", params={"q": "post"}).json()

    assert results[0]["in_library"] == 2
    assert results[1]["in_library"] == 0


def test_same_named_artists_do_not_both_claim_our_tracks(client):
    test_client, app = client
    app.state.mb.answers["artist"] = {"artists": [
        {"id": ARTIST_MBID, "name": "Post Malone", "disambiguation": "",
         "score": 100},
        {"id": OTHER_ARTIST_MBID, "name": "Post Malone",
         "disambiguation": "screamo side project", "score": 90},
    ]}
    insert_track(app.state.conn, 100, artist_name="Post Malone")

    results = test_client.get("/api/discover/artists", params={"q": "post"}).json()

    assert [r["in_library"] for r in results] == [1, 0]


@pytest.mark.parametrize(
    "path",
    [
        "/api/catalog/artists?q=x",
        "/api/catalog/artists/1/albums",
        "/api/catalog/albums/10/tracks",
        "/api/discover/artists/1/mb",
    ],
)
def test_the_mirror_routes_are_gone(client, path):
    test_client, _ = client

    assert test_client.get(path).status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/api/discover/artists/5/releases",
        "/api/discover/artists/not-a-uuid/releases",
        "/api/discover/release-groups/rg-new/tracks",
    ],
)
def test_an_id_that_is_not_an_mbid_is_refused(client, path):
    """Two id spaces sit at the same place on `/api/discover/artists/{...}`:
    a catalog rowid before `/mb`, an MBID before `/releases`. Sending a rowid
    to the second is a real mistake with a silent failure -- MusicBrainz is
    browsed for an artist called "5" and answers with nothing -- so it is
    refused at the door instead."""
    test_client, app = client

    assert test_client.get(path).status_code == 422
    assert app.state.mb.calls == []


# --- one release's tracklist ----------------------------------------------


def test_the_tracklist_comes_from_the_fullest_official_pressing(client):
    test_client, app = client

    body = test_client.get(
        f"/api/discover/release-groups/{NEW_GROUP_MBID}/tracks"
    ).json()

    assert body["release_mbid"] == DELUXE_MBID
    assert body["album_name"] == "Acts of Faith"
    assert [t["track_name"] for t in body["tracks"]] == ["Opening", "Guest Spot"]
    assert [t["in_library"] for t in body["tracks"]] == [False, False]
    assert body["tracks"][0]["isrc"] == "GBAAA0000001"
    assert body["tracks"][0]["total_tracks"] == 2


def test_every_track_row_is_queueable_as_it_stands(client):
    """A client must never have to merge the release into the track.

    `{...track, ...release}` -- the natural way round to write it in
    TypeScript -- would overwrite a track's own artist credit with the
    record's, which is exactly the `fix_manifest_artists.py` bug the server's
    merge order is pinned against. Rather than trust the next task to spread
    them the right way round, the rows arrive already flat.
    """
    test_client, _ = client

    body = test_client.get(
        f"/api/discover/release-groups/{NEW_GROUP_MBID}/tracks"
    ).json()

    for track in body["tracks"]:
        assert track["release_mbid"] == DELUXE_MBID
        assert track["album_name"] == "Acts of Faith"
        assert track["artist_name"]
    # ...and on this compilation the second track's own credit survives the
    # flattening rather than being replaced by the record's.
    assert body["artist_name"] == "Various Artists"
    assert body["tracks"][0]["artist_name"] == "Various Artists"
    assert body["tracks"][1]["artist_name"] == "Someone Else"
    assert body["tracks"][1]["artist_names"] == ["Someone Else"]


def test_a_recording_we_already_hold_is_flagged_before_the_tap(client):
    """`in_library` is the recording mbid join, and it counts only rows that
    are actually holdings. A failed attempt leaves a row behind carrying the
    same `recording_mbid`; treating that as owned would hide the one track
    the person came back to retry."""
    test_client, app = client
    conn = app.state.conn
    insert_track(conn, 100, status="done")
    insert_track(conn, 200, status="error")
    conn.execute("UPDATE tracks SET recording_mbid = ?"
                 " WHERE track_rowid = 100", (RECORDING_ONE,))
    conn.execute("UPDATE tracks SET recording_mbid = ?"
                 " WHERE track_rowid = 200", (RECORDING_TWO,))

    body = test_client.get(f"/api/discover/release-groups/{NEW_GROUP_MBID}/tracks").json()

    assert [t["in_library"] for t in body["tracks"]] == [True, False]


def _tracks(test_client):
    return test_client.get(
        f"/api/discover/release-groups/{NEW_GROUP_MBID}/tracks"
    ).json()["tracks"]


def test_a_held_track_matches_by_artist_title_and_duration(client):
    test_client, app = client
    insert_track(app.state.conn, 100, artist_name="Someone Else",
                 track_name="Guest Spot", duration_ms=209_000)
    app.state.conn.execute("UPDATE tracks SET recording_mbid = NULL")

    assert _tracks(test_client)[1]["in_library"] is True


def test_a_track_credited_to_the_record_artist_is_not_held_by_title_alone(client):
    test_client, app = client
    insert_track(app.state.conn, 100, artist_name="Various Artists",
                 track_name="Opening - Live", duration_ms=200_000)

    assert _tracks(test_client)[0]["in_library"] is False


def test_a_missing_file_row_with_the_recording_mbid_counts_as_held(client):
    test_client, app = client
    insert_track(app.state.conn, 100, status="missing_file")
    app.state.conn.execute("UPDATE tracks SET recording_mbid = ?"
                           " WHERE track_rowid = 100", (RECORDING_ONE,))

    assert _tracks(test_client)[0]["in_library"] is True


def test_the_tracklist_502s_when_musicbrainz_does_not_answer(client):
    """Both halves: the release pick, and the tracklist read after it."""
    test_client, app = client

    app.state.mb.answers["release"] = None
    assert test_client.get(
        f"/api/discover/release-groups/{NEW_GROUP_MBID}/tracks"
    ).status_code == 502

    app.state.mb.answers["release"] = RELEASES
    app.state.mb.answers[f"release/{DELUXE_MBID}"] = None
    assert test_client.get(
        f"/api/discover/release-groups/{NEW_GROUP_MBID}/tracks"
    ).status_code == 502


# --- queueing -------------------------------------------------------------


MB_TRACK_PAYLOAD = {
    "kind": "mb",
    "recording_mbid": RECORDING_TWO,
    "release_mbid": DELUXE_MBID,
    "artist_name": "Someone Else",
    "artist_names": ["Someone Else"],
    "album_name": "Acts of Faith",
    "track_name": "Guest Spot",
    "track_no": 2,
    "disc_no": 1,
    "total_tracks": 2,
    "duration_ms": 210_000,
    "isrc": None,
}


def test_queueing_one_musicbrainz_track_copies_what_the_screen_already_had(client):
    """This branch resolves nothing: the browse screen fetched the release to
    render it, so asking MusicBrainz again would cost a rate-limited request
    per track for facts already in hand."""
    test_client, app = client

    response = test_client.post("/api/jobs", json=MB_TRACK_PAYLOAD)

    assert response.status_code == 200
    job_ids = response.json()["job_ids"]
    assert len(job_ids) == 1
    # It resolves end to end through the real TieredMetadataProvider, whose
    # MusicBrainz tier reads exactly these keys -- a payload missing one of
    # them would surface only as state="error".
    assert _settle(test_client, job_ids) == ["done"]
    payload = _payloads(app.state.conn, job_ids)[0]
    assert payload["recording_mbid"] == RECORDING_TWO
    assert payload["release_mbid"] == DELUXE_MBID
    assert payload["track_name"] == "Guest Spot"
    assert payload["track_no"] == 2
    assert payload["total_tracks"] == 2
    assert payload["duration_ms"] == 210_000
    # Nothing was asked of MusicBrainz to build it.
    assert app.state.mb.calls == []


def test_a_whole_release_fans_out_to_one_job_per_track(client):
    test_client, app = client

    response = test_client.post(
        "/api/jobs", json={"kind": "mb_release", "release_group_mbid": NEW_GROUP_MBID}
    )

    assert response.status_code == 200
    job_ids = response.json()["job_ids"]
    assert len(job_ids) == 2
    assert _settle(test_client, job_ids) == ["done", "done"]
    payloads = _payloads(app.state.conn, job_ids)
    assert [p["recording_mbid"] for p in payloads] == [RECORDING_ONE, RECORDING_TWO]
    assert [p["track_no"] for p in payloads] == [1, 2]
    # Release-level facts reach every track, so the download never re-asks.
    assert {p["release_mbid"] for p in payloads} == {DELUXE_MBID}
    assert {p["album_name"] for p in payloads} == {"Acts of Faith"}


def test_a_track_keeps_its_own_credit_rather_than_the_records(client):
    """The compilation bug, pinned. `release_tracks` gives each track its own
    artist credit and falls back to the release's only when MusicBrainz has
    none -- so the release-level fields must be spread *first* and the
    track's own keys must win. Spreading the release last would stamp every
    track on this compilation with "Various Artists", which is exactly what
    `fix_manifest_artists.py` had to be written to repair once already.
    """
    test_client, app = client

    job_ids = test_client.post(
        "/api/jobs", json={"kind": "mb_release", "release_group_mbid": NEW_GROUP_MBID}
    ).json()["job_ids"]
    _settle(test_client, job_ids)
    payloads = _payloads(app.state.conn, job_ids)

    # Track one has no credit of its own, so it inherits the record's.
    assert payloads[0]["artist_name"] == "Various Artists"
    assert payloads[0]["artist_names"] == ["Various Artists"]
    # Track two has one, and it must survive the merge.
    assert payloads[1]["artist_name"] == "Someone Else"
    assert payloads[1]["artist_names"] == ["Someone Else"]


def _release_answer(app, **changes):
    """The canned release, altered. Deep-copied so one test's hole in
    MusicBrainz's data is not every later test's."""
    release = copy.deepcopy(RELEASE_TRACKS)
    release.update(changes)
    app.state.mb.answers[f"release/{DELUXE_MBID}"] = release
    return release


def test_a_whole_release_reports_nothing_skipped_when_nothing_is(client):
    """`skipped` is always there, on every kind, so no client has to ask
    whether the key exists before reading it."""
    test_client, _ = client

    fanned = test_client.post(
        "/api/jobs", json={"kind": "mb_release", "release_group_mbid": NEW_GROUP_MBID}
    ).json()
    one = test_client.post("/api/jobs", json=MB_TRACK_PAYLOAD).json()

    assert fanned["skipped"] == []
    assert one["skipped"] == []
    # Everything posted here is queued for real, so it is waited on for real
    # -- see `_settle`.
    _settle(test_client, fanned["job_ids"] + one["job_ids"])


def test_a_release_track_missing_a_required_field_is_skipped_not_refused(client):
    """`mb_release` builds payloads for the same `run_download` the `mb` kind
    does, and until now applied none of its guard -- so a track MusicBrainz
    has no title for was refused through one route and written as
    `track_name=""` through the other.

    Skipped rather than refusing the record, because the hole is in
    MusicBrainz's data and the request named a release that exists: one
    untitled track must not take the other eleven with it. Silence is the
    thing ruled out -- the response says which track did not go, and what it
    was missing, so the screen can say so too.
    """
    test_client, app = client
    release = _release_answer(app)
    release["media"][0]["tracks"][0]["title"] = ""

    response = test_client.post(
        "/api/jobs", json={"kind": "mb_release", "release_group_mbid": NEW_GROUP_MBID}
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["job_ids"]) == 1
    assert body["skipped"] == [{
        "recording_mbid": RECORDING_ONE,
        "track_name": "",
        "track_no": 1,
        "disc_no": 1,
        "missing": ["track_name"],
    }]
    _settle(test_client, body["job_ids"])
    payloads = _payloads(app.state.conn, body["job_ids"])
    assert [p["recording_mbid"] for p in payloads] == [RECORDING_TWO]


def test_a_release_with_no_artist_credit_anywhere_is_refused_whole(client):
    """An artist hole is release-wide by construction: `release_tracks` falls
    a track's credit back to the release's, so if the release has none no
    track has one and every track is skipped. Nothing can be queued, and a
    200 with an empty `job_ids` would be a request that queued nothing, said
    nothing and looked like success."""
    test_client, app = client
    release = _release_answer(app, **{"artist-credit": []})
    del release["media"][0]["tracks"][1]["artist-credit"]

    response = test_client.post(
        "/api/jobs", json={"kind": "mb_release", "release_group_mbid": NEW_GROUP_MBID}
    )

    assert response.status_code == 422
    # Both names, not one that happens to be a prefix of the other:
    # `"artist_name" in "artist_names"` is True, so an `in` check here would
    # pass on a detail that named only half the hole.
    detail = response.json()["detail"]
    assert detail.endswith("every track is missing artist_name, artist_names.")
    assert app.state.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_a_release_with_no_tracklist_is_refused_and_says_which_hole_it_is(client):
    """The empty tracklist used to return `{"job_ids": []}` with no signal at
    all. Same refusal as above, different sentence: there is nothing missing
    from these tracks, there are no tracks."""
    test_client, app = client
    _release_answer(app, media=[])

    response = test_client.post(
        "/api/jobs", json={"kind": "mb_release", "release_group_mbid": NEW_GROUP_MBID}
    )

    assert response.status_code == 422
    assert "no tracks" in response.json()["detail"]
    assert app.state.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_no_musicbrainz_payload_ever_carries_a_track_rowid(client):
    """A payload with both a `track_rowid` and a `recording_mbid` writes an
    incoherent row -- `metadata_tier='musicbrainz'` against `origin='catalog'`
    -- whenever the catalog misses on that rowid. It is unreachable only
    because neither branch emits one, including when the client sends one.
    """
    test_client, app = client

    single = test_client.post(
        "/api/jobs", json={**MB_TRACK_PAYLOAD, "track_rowid": 999}
    ).json()["job_ids"]
    fanned = test_client.post(
        "/api/jobs",
        json={"kind": "mb_release", "release_group_mbid": NEW_GROUP_MBID,
              "track_rowid": 999},
    ).json()["job_ids"]

    _settle(test_client, single + fanned)
    for payload in _payloads(app.state.conn, single + fanned):
        assert "track_rowid" not in payload


@pytest.mark.parametrize(
    "payload",
    [
        {k: v for k, v in MB_TRACK_PAYLOAD.items() if k != "recording_mbid"},
        {**MB_TRACK_PAYLOAD, "recording_mbid": ""},
    ],
    ids=["absent", "empty"],
)
def test_an_mb_job_without_a_recording_mbid_is_refused(client, payload):
    """The one field that cannot be null, and the only one.

    Everything else is `.get()` on purpose -- MusicBrainz often has no ISRC
    and a single has no track number worth the name. But `recording_mbid` is
    what the MusicBrainz metadata tier keys on: without it every MusicBrainz
    field lands None, that tier declines, and the job falls through to the
    yt-dlp tier, which downloads *something*, tags it off a YouTube upload
    and records `metadata_tier='ytdlp'` on a track the person asked for by
    name from MusicBrainz. Failing loudly is the better answer.
    """
    test_client, app = client

    response = test_client.post("/api/jobs", json=payload)

    assert response.status_code == 400
    assert "recording_mbid" in response.json()["detail"]
    # And nothing was queued: a refused job leaves no row to wonder about.
    assert app.state.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


@pytest.mark.parametrize(
    "field", ["artist_name", "artist_names", "album_name", "track_name"]
)
def test_an_mb_job_missing_a_not_null_field_is_refused(client, field):
    """The four fields that mirror `tracks`' NOT NULL columns.

    Today the schema already stops these -- the job fails at
    `NOT NULL constraint failed` before the audio source is ever called, so
    nothing wrong gets downloaded. The check is not for today. Nothing at the
    route said the route depended on those columns, and the dependency sits
    three tables away from whoever would relax one; the moment a migration
    does, the same payload writes a row with `artist_name=None` and yt-dlp is
    asked for "None - Guest Spot". This is where that migration fails
    instead, and `MB_REQUIRED_FIELDS` is where it says so.
    """
    test_client, app = client
    payload = {k: v for k, v in MB_TRACK_PAYLOAD.items() if k != field}

    response = test_client.post("/api/jobs", json=payload)

    assert response.status_code == 400
    assert field in response.json()["detail"]
    assert app.state.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_a_complete_mb_payload_is_still_accepted(client):
    """The guard above must refuse only what is actually missing. A payload
    with every required field and nulls in the optional ones -- the shape
    MusicBrainz really returns for a single with no ISRC -- still queues."""
    test_client, app = client
    payload = {**MB_TRACK_PAYLOAD, "isrc": None, "track_no": None,
               "total_tracks": None, "duration_ms": None}

    response = test_client.post("/api/jobs", json=payload)

    assert response.status_code == 200
    job_ids = response.json()["job_ids"]
    assert _settle(test_client, job_ids) == ["done"]


# --- the client itself ----------------------------------------------------


def test_one_client_reaches_every_consumer(tmp_path, monkeypatch):
    """Identity, not behaviour, is the property worth pinning.

    `RateLimiter` holds its lock across the sleep so the 1 req/s floor
    MusicBrainz asks for holds across threads -- but only within one
    instance. Two clients in this process would each honour a floor of their
    own and jointly ask for twice what we promised, silently, until
    MusicBrainz starts refusing us. So every consumer must hold the *same*
    object, and `is` is the only assertion that says so.

    The three consumers are reached at construction time rather than through
    `app.state`: two of them (the art backfill's own provider, and the
    chained one behind `run_download`'s art step) are locals in the lifespan
    that nothing else can see, and every other fixture in this suite hides
    them by injecting an `art_provider` or leaving `art_backfill` off. This
    is `test_embed_startup_wiring.py`'s trick -- drive the lifespan, assert
    the wiring, never run the thing.
    """
    import muse.api as api_module

    seen: list[tuple[str, object]] = []
    real_cover_art = api_module.CoverArtArchive
    real_tiered = api_module.TieredMetadataProvider

    def recording_cover_art(*args, mb=None, **kwargs):
        seen.append(("art", mb))
        return real_cover_art(*args, mb=mb, **kwargs)

    def recording_tiered(*args, mb=None, **kwargs):
        seen.append(("metadata", mb))
        return real_tiered(*args, mb=mb, **kwargs)

    monkeypatch.setattr(api_module, "CoverArtArchive", recording_cover_art)
    monkeypatch.setattr(api_module, "TieredMetadataProvider", recording_tiered)
    monkeypatch.setattr("muse.api._open_catalog", lambda config: MirrorOffCatalog())

    injected = FakeMusicBrainz(_answers())
    # `art_backfill=True` and no injected `art_provider`: the two art-side
    # constructions only happen on those defaults, and they are exactly the
    # ones no other test reaches. The library is empty, so the backfill has
    # no album to look anything up for.
    app = create_app(_config(tmp_path, art_backfill=True), mb=injected)

    async def drive():
        async with app.router.lifespan_context(app):
            return app.state.mb

    from_state = asyncio.run(drive())

    assert sorted(where for where, _ in seen) == ["art", "art", "metadata"]
    assert [got for _, got in seen] == [injected, injected, injected]
    assert all(got is injected for _, got in seen)
    assert from_state is injected


def test_queueing_a_release_502s_when_musicbrainz_does_not_answer(client):
    test_client, app = client
    app.state.mb.answers["release"] = None

    response = test_client.post(
        "/api/jobs", json={"kind": "mb_release", "release_group_mbid": NEW_GROUP_MBID}
    )

    assert response.status_code == 502


def test_mb_release_stamps_the_track_id_the_isrc_joins_to(tmp_path, monkeypatch):
    """Only the recording with an ISRC the mirror knows gets a `track_id`."""
    import muse.ingest as ingest_mod
    from muse.lyrics_lrclib import SyncOutcome

    monkeypatch.setattr(ingest_mod, "tag_m4a", lambda *a, **k: None)
    monkeypatch.setattr(
        ingest_mod, "fetch_lyrics",
        lambda track: SyncOutcome(state="not_found", synced=False),
    )
    monkeypatch.setattr(
        "muse.api._open_catalog",
        lambda config: MirrorOffCatalog({"GBAAA0000001": "sp-one"}),
    )
    app = create_app(
        _config(tmp_path),
        mb=FakeMusicBrainz(_answers()),
        audio_source=FakeAudioSource(),
        art_provider=FakeArtProvider(),
        audio_encoder=StubAudioEncoder(),
        lyrics_encoder=StubLyricsEncoder(),
        audio_va_encoder=StubAudioVAEncoder(),
        lyrics_va_encoder=StubLyricsVAEncoder(),
    )
    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/jobs",
            json={"kind": "mb_release", "release_group_mbid": NEW_GROUP_MBID},
        )
        assert response.status_code == 200
        assert _settle(test_client, response.json()["job_ids"]) == ["done", "done"]

        ids = {
            r["recording_mbid"]: r["track_id"]
            for r in app.state.conn.execute(
                "SELECT recording_mbid, track_id FROM tracks"
            )
        }
    assert ids == {RECORDING_ONE: "sp-one", RECORDING_TWO: None}
