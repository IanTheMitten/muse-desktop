"""Cover art.

Missing art is a loud failure — the screen shows a placeholder and you can see
it — so what is tested here is everything that fails *quietly*:

- asking MusicBrainz again for something already answered, which nobody would
  notice except MusicBrainz;
- an album wearing the cover of a greatest-hits compilation it was never on;
- one album's outage silently ending the run for the other eighty-six;
- a transient failure being cached as "this album has no art", permanently.

No test here touches the network. The provider is injected.
"""

import json

import pytest
from conftest import insert_track

from muse import art as art_module
from muse.art import AlbumRef, Cover, CoverArtArchive, RateLimiter, backfill
from muse.library import art as library_art
from muse.library import tracks as library_tracks
from muse.musicbrainz import MusicBrainzClient

PNG = b"\x89PNG\r\n\x1a\n" + b"pretend this is a cover"
JPEG = b"\xff\xd8\xff\xe0" + b"pretend this is a cover"


class FakeProvider:
    """Stands in for MusicBrainz and the Cover Art Archive.

    Counts what it was asked, because "asked once" is the property under test.
    """

    def __init__(self, covers=None, raises=()):
        self.covers = covers or {}
        self.raises = set(raises)
        self.asked: list[int] = []

    def cover(self, album: AlbumRef) -> Cover | None:
        self.asked.append(album.album_rowid)
        if album.album_rowid in self.raises:
            raise RuntimeError("musicbrainz fell over")
        return self.covers.get(album.album_rowid)


def cover(image=JPEG, content_type="image/jpeg", mbid="mbid-1"):
    return Cover(
        image=image, content_type=content_type, source="fake", mbid=mbid
    )


def load(conn, tracks):
    """`tracks` is (track_rowid, album_rowid, isrc)."""
    for rowid, album, isrc in tracks:
        insert_track(conn, rowid, album_rowid=album, album_name=f"Album {album}",
                     isrc=isrc)
    return conn


def art_paths(conn, album_rowid):
    return [
        row["art_path"]
        for row in conn.execute(
            "SELECT art_path FROM tracks WHERE album_rowid = ?", (album_rowid,)
        )
    ]


# --- fetched once, ever ---------------------------------------------------


def test_art_is_looked_up_once_per_album_not_once_per_track(conn, tmp_path):
    """The whole reason the backlog is 87 requests and not 723."""
    load(conn, [(1, 20, "GBAAA0000001"), (2, 20, "GBAAA0000002"),
                (3, 20, "GBAAA0000003")])
    provider = FakeProvider({20: cover()})

    backfill(conn, provider, tmp_path)

    assert provider.asked == [20]


def test_a_second_run_asks_about_nothing(conn, tmp_path):
    """Startup runs this every time. Without the decision cache, every restart
    would be another ninety seconds of requests for art already on disk."""
    load(conn, [(1, 20, "GBAAA0000001")])
    provider = FakeProvider({20: cover()})

    backfill(conn, provider, tmp_path)
    provider.asked.clear()
    backfill(conn, provider, tmp_path)

    assert provider.asked == []


def test_an_album_with_no_art_is_never_asked_about_again(conn, tmp_path):
    """A negative is an answer. Storing only positives would mean the albums
    that cost the most to look up are the ones looked up forever."""
    load(conn, [(1, 20, "GBAAA0000001")])
    provider = FakeProvider({})  # finds nothing

    backfill(conn, provider, tmp_path)
    provider.asked.clear()
    backfill(conn, provider, tmp_path)

    assert provider.asked == []
    assert library_art.album_art_path(conn, 20) is None


def test_a_track_with_no_album_is_skipped_rather_than_crashing(conn, tmp_path):
    load(conn, [(1, 20, "GBAAA0000001")])
    conn.execute("UPDATE tracks SET album_rowid = NULL WHERE track_rowid = 1")
    conn.commit()
    provider = FakeProvider({})

    result = backfill(conn, provider, tmp_path)

    assert result.considered == 0
    assert provider.asked == []


# --- what a found cover writes -------------------------------------------


def test_every_track_on_the_album_gets_the_path(conn, tmp_path):
    """Art is resolved per album but displayed per track, so the path is
    denormalized onto each row. A track left NULL renders a placeholder next to
    eleven siblings that do not."""
    load(conn, [(1, 20, "GBAAA0000001"), (2, 20, "GBAAA0000002")])

    backfill(conn, FakeProvider({20: cover()}), tmp_path)

    paths = art_paths(conn, 20)
    assert len(paths) == 2
    assert all(path and path.endswith(".jpg") for path in paths)


def test_the_cached_file_holds_the_image(conn, tmp_path):
    load(conn, [(1, 20, "GBAAA0000001")])

    backfill(conn, FakeProvider({20: cover(image=JPEG)}), tmp_path)

    path = library_art.album_art_path(conn, 20)
    assert path is not None
    from pathlib import Path

    assert Path(path).read_bytes() == JPEG


def test_the_extension_follows_the_content_type(conn, tmp_path):
    """The Cover Art Archive serves PNG as readily as JPEG. Naming a PNG .jpg
    works in a browser and then breaks anything that trusts the name."""
    load(conn, [(1, 20, "GBAAA0000001")])

    backfill(
        conn,
        FakeProvider({20: cover(image=PNG, content_type="image/png")}),
        tmp_path,
    )

    assert library_art.album_art_path(conn, 20).endswith(".png")


def test_no_partial_file_is_left_in_the_cache(conn, tmp_path):
    load(conn, [(1, 20, "GBAAA0000001")])

    backfill(conn, FakeProvider({20: cover()}), tmp_path)

    assert list(tmp_path.glob("*.part")) == []


# --- failure ---------------------------------------------------------------


def test_one_albums_failure_does_not_end_the_run(conn, tmp_path):
    """Eighty-seven albums behind one flaky lookup. Raising out of the loop
    would leave the rest of the library grey with nothing to say why."""
    load(conn, [(1, 20, "GBAAA0000001"), (2, 30, "GBAAA0000002")])
    provider = FakeProvider({30: cover()}, raises={20})

    result = backfill(conn, provider, tmp_path)

    assert result.failed == 1
    assert result.found == 1
    assert art_paths(conn, 30)[0]


def test_a_failure_is_retried_next_run(conn, tmp_path):
    """An outage is not an answer. Recording it as "no art" would make a bad
    afternoon permanent."""
    load(conn, [(1, 20, "GBAAA0000001")])
    provider = FakeProvider({}, raises={20})

    backfill(conn, provider, tmp_path)
    provider.asked.clear()
    provider.raises.clear()
    provider.covers = {20: cover()}
    backfill(conn, provider, tmp_path)

    assert provider.asked == [20]
    assert library_art.album_art_path(conn, 20)


def test_the_run_can_be_stopped_partway(conn, tmp_path):
    load(conn, [(1, 20, "GBAAA0000001"), (2, 30, "GBAAA0000002")])
    provider = FakeProvider({20: cover(), 30: cover()})

    backfill(conn, provider, tmp_path, should_stop=lambda: len(provider.asked) >= 1)

    assert len(provider.asked) == 1


def test_progress_is_reported_for_every_album(conn, tmp_path):
    load(conn, [(1, 20, "GBAAA0000001"), (2, 30, "GBAAA0000002")])
    seen = []

    backfill(
        conn,
        FakeProvider({20: cover()}),
        tmp_path,
        on_progress=lambda done, total, message: seen.append((done, total)),
    )

    assert seen == [(1, 2), (2, 2)]


# --- the rate limit --------------------------------------------------------


def test_the_limiter_waits_between_calls():
    """MusicBrainz asks for one request a second and enforces it with 503s.
    Exceeding it does not fail loudly — it just makes the backlog slower and
    ruder."""
    slept = []
    limiter = RateLimiter(1.1, sleep=slept.append)

    limiter.wait()
    limiter.wait()

    assert slept and slept[0] > 1.0


def test_the_first_call_does_not_wait():
    slept = []
    RateLimiter(1.1, sleep=slept.append).wait()
    assert slept == []


# --- the real provider, without a network ---------------------------------


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content=b"",
                 content_type="image/jpeg"):
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.headers = {"content-type": content_type}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Records every URL asked for and answers from a routing table."""

    def __init__(self, routes):
        self.routes = routes
        self.urls: list[str] = []
        self.params: list = []

    def get(self, url, params=None, headers=None, timeout=None,
            allow_redirects=True):
        self.urls.append(url)
        self.params.append(params)
        for fragment, response in self.routes.items():
            if fragment in url:
                return response
        return FakeResponse(status_code=404)


def isrc_payload(*recording_ids):
    return {"recordings": [{"id": mbid} for mbid in recording_ids]}


def recording_payload(*releases):
    return {"releases": list(releases)}


def test_releases_are_fetched_by_recording_not_from_the_isrc_lookup():
    """`/isrc/{isrc}?inc=releases` accepts the include and returns recordings
    with no releases anyway — no error, just an empty list that looks exactly
    like an album nobody has art for. This is the shape that regressed a whole
    backfill to zero covers; it is pinned here so it cannot come back."""
    session = FakeSession(
        {
            "/isrc/": FakeResponse(payload=isrc_payload("rec-1")),
            "/recording/rec-1": FakeResponse(
                payload=recording_payload({"id": "album-mbid", "title": "Album"})
            ),
            "/release/album-mbid/": FakeResponse(content=JPEG),
        }
    )
    provider = CoverArtArchive(session=session, mb=MusicBrainzClient(session=session, limiter=RateLimiter(0)))

    found = provider.cover(AlbumRef(20, "Album", "Artist", ("GBAAA0000001",)))

    assert found is not None and found.mbid == "album-mbid"
    assert any("/recording/rec-1" in url for url in session.urls)


def test_the_release_matching_the_album_is_preferred():
    """One recording sits on the album, three compilations and a deluxe
    edition. Taking whichever came back first is how a track ends up wearing a
    greatest-hits cover — wrong, and wrong silently."""
    session = FakeSession(
        {
            "/isrc/": FakeResponse(payload=isrc_payload("rec-1")),
            "/recording/rec-1": FakeResponse(
                payload=recording_payload(
                    {"id": "compilation-mbid", "title": "Now That's What I Call Music"},
                    {"id": "album-mbid", "title": "Doo-Wops & Hooligans"},
                )
            ),
            "/release/album-mbid/": FakeResponse(content=JPEG),
        }
    )
    provider = CoverArtArchive(session=session, mb=MusicBrainzClient(session=session, limiter=RateLimiter(0)))

    found = provider.cover(
        AlbumRef(20, "Doo-Wops & Hooligans", "Bruno Mars", ("GBAAA0000001",))
    )

    assert found is not None
    assert found.mbid == "album-mbid"


def test_a_version_suffixed_release_title_still_matches():
    """`normalize_title` is why `Doo-Wops & Hooligans (Deluxe Edition)` counts
    as the album rather than as a stranger."""
    session = FakeSession(
        {
            "/isrc/": FakeResponse(payload=isrc_payload("rec-1")),
            "/recording/rec-1": FakeResponse(
                payload=recording_payload(
                    {"id": "other-mbid", "title": "Some Compilation"},
                    {
                        "id": "deluxe-mbid",
                        "title": "Doo-Wops & Hooligans (Deluxe Edition)",
                    },
                )
            ),
            "/release/deluxe-mbid/": FakeResponse(content=JPEG),
        }
    )
    provider = CoverArtArchive(session=session, mb=MusicBrainzClient(session=session, limiter=RateLimiter(0)))

    found = provider.cover(
        AlbumRef(20, "Doo-Wops & Hooligans", "Bruno Mars", ("GBAAA0000001",))
    )

    assert found is not None and found.mbid == "deluxe-mbid"


def test_a_release_without_art_does_not_end_the_search():
    """Half of a popular album's twenty-five releases have no cover. Stopping
    at the first 404 would leave the album grey for no reason."""
    session = FakeSession(
        {
            "/isrc/": FakeResponse(payload=isrc_payload("rec-1")),
            "/recording/rec-1": FakeResponse(
                payload=recording_payload(
                    {"id": "bare-mbid", "title": "Album"},
                    {"id": "good-mbid", "title": "Album"},
                )
            ),
            "/release/bare-mbid/": FakeResponse(status_code=404),
            "/release/good-mbid/": FakeResponse(content=JPEG),
        }
    )
    provider = CoverArtArchive(session=session, mb=MusicBrainzClient(session=session, limiter=RateLimiter(0)))

    found = provider.cover(AlbumRef(20, "Album", "Artist", ("GBAAA0000001",)))

    assert found is not None and found.mbid == "good-mbid"


def test_a_second_isrc_is_tried_when_the_first_finds_nothing():
    """An album's first track is often a feature credit whose ISRC belongs to
    someone else's release."""
    session = FakeSession(
        {
            "/isrc/GBAAA0000002": FakeResponse(payload=isrc_payload("rec-2")),
            "/recording/rec-2": FakeResponse(
                payload=recording_payload({"id": "album-mbid", "title": "Album"})
            ),
            "/release/album-mbid/": FakeResponse(content=JPEG),
            "/isrc/GBAAA0000001": FakeResponse(payload={"recordings": []}),
        }
    )
    provider = CoverArtArchive(session=session, mb=MusicBrainzClient(session=session, limiter=RateLimiter(0)))

    found = provider.cover(
        AlbumRef(20, "Album", "Artist", ("GBAAA0000001", "GBAAA0000002"))
    )

    assert found is not None and found.mbid == "album-mbid"


def test_only_a_few_isrcs_are_tried():
    """Every ISRC is a rate-limited request. An album with no art anywhere must
    not spend twelve of them proving it."""
    session = FakeSession({"/isrc/": FakeResponse(payload={"recordings": []})})
    provider = CoverArtArchive(session=session, mb=MusicBrainzClient(session=session, limiter=RateLimiter(0)))

    provider.cover(
        AlbumRef(20, "Album", "Artist", tuple(f"GBAAA000000{n}" for n in range(1, 9)))
    )

    assert len(session.urls) == art_module.MAX_ISRCS


def test_a_musicbrainz_outage_is_no_cover_and_not_an_exception():
    """From the caller's side a timeout and a 404 mean the same thing: show the
    placeholder. An exception here would end the whole backfill."""

    class ExplodingSession:
        def get(self, *args, **kwargs):
            import requests

            raise requests.RequestException("connection reset")

    provider = CoverArtArchive(session=ExplodingSession(), mb=MusicBrainzClient(session=ExplodingSession(), limiter=RateLimiter(0)))

    assert provider.cover(AlbumRef(20, "Album", "Artist", ("GBAAA0000001",))) is None


def test_an_empty_body_is_not_a_cover():
    """A 200 with no bytes would otherwise cache as a zero-byte image, which
    renders as a broken picture and looks exactly like a bug in the UI."""
    session = FakeSession(
        {
            "/isrc/": FakeResponse(payload=isrc_payload("rec-1")),
            "/recording/rec-1": FakeResponse(
                payload=recording_payload({"id": "m", "title": "Album"})
            ),
            "/release/m/": FakeResponse(content=b""),
        }
    )
    provider = CoverArtArchive(session=session, mb=MusicBrainzClient(session=session, limiter=RateLimiter(0)))

    assert provider.cover(AlbumRef(20, "Album", "Artist", ("GBAAA0000001",))) is None


def test_an_album_with_no_isrcs_asks_nothing():
    session = FakeSession({})
    provider = CoverArtArchive(session=session, mb=MusicBrainzClient(session=session, limiter=RateLimiter(0)))

    assert provider.cover(AlbumRef(20, "Album", "Artist", ())) is None
    assert session.urls == []


# --- the release-mbid direct path -------------------------------------------
#
# These drive the same seam as every other `cover()` test in this file: a
# `FakeSession` routed by URL fragment, with a real `MusicBrainzClient` on
# top of it. Stubbing the private `_front`/`_recordings` methods would pin
# their names against a harmless refactor, and — as the composed test below
# found the hard way — makes it too easy to stub away the very path under
# test.


def test_release_mbid_goes_straight_to_the_archive():
    """No ISRC, no recording walk, two fewer rate-limited MusicBrainz calls."""
    session = FakeSession({"/release/rel-1/": FakeResponse(content=JPEG)})
    provider = CoverArtArchive(
        session=session, mb=MusicBrainzClient(session=session, limiter=RateLimiter(0))
    )

    found = provider.cover(AlbumRef(
        album_rowid=1_000_000_000, album_name="The Album",
        artist_name="Artist One", isrcs=(), release_mbid="rel-1",
    ))

    assert found is not None
    assert found.mbid == "rel-1"
    assert session.urls == [f"{art_module.CAA_BASE}/release/rel-1/front-500"]


def test_without_a_release_mbid_the_isrc_walk_still_runs():
    session = FakeSession({"/isrc/": FakeResponse(payload={"recordings": []})})
    provider = CoverArtArchive(
        session=session, mb=MusicBrainzClient(session=session, limiter=RateLimiter(0))
    )

    found = provider.cover(AlbumRef(
        album_rowid=42, album_name="The Album", artist_name="Artist One",
        isrcs=("USXX12345678",),
    ))

    assert found is None
    assert any("/isrc/" in url for url in session.urls)


def test_a_release_mbid_miss_still_falls_through_to_the_isrc_walk():
    """An album can have both a release MBID and ISRCs. A release with no
    image at the archive is not a reason to give up on the ISRC walk finding
    a different release with one."""
    session = FakeSession(
        {
            "/release/rel-miss/": FakeResponse(status_code=404),
            "/isrc/": FakeResponse(payload={"recordings": []}),
        }
    )
    provider = CoverArtArchive(
        session=session, mb=MusicBrainzClient(session=session, limiter=RateLimiter(0))
    )

    found = provider.cover(AlbumRef(
        album_rowid=7, album_name="The Album", artist_name="Artist One",
        isrcs=("USXX12345678",), release_mbid="rel-miss",
    ))

    assert found is None
    assert any("/isrc/" in url for url in session.urls)


def test_a_release_mbid_miss_falls_through_all_the_way_to_a_walk_that_finds_art():
    """The composed path, end to end: the release MBID we already had misses
    at the archive, the ISRC walk finds a *different* release that has art,
    and the cover that comes back is credited to the release the walk found —
    not the one that missed. A cover mislabeled with the wrong MBID would
    poison `album_art`'s provenance silently; nothing about the image itself
    would look wrong."""
    session = FakeSession(
        {
            "/release/rel-miss/": FakeResponse(status_code=404),
            "/isrc/": FakeResponse(payload=isrc_payload("rec-1")),
            "/recording/rec-1": FakeResponse(
                payload=recording_payload({"id": "rel-hit", "title": "The Album"})
            ),
            "/release/rel-hit/": FakeResponse(content=JPEG),
        }
    )
    provider = CoverArtArchive(
        session=session, mb=MusicBrainzClient(session=session, limiter=RateLimiter(0))
    )

    found = provider.cover(AlbumRef(
        album_rowid=8, album_name="The Album", artist_name="Artist One",
        isrcs=("USXX12345678",), release_mbid="rel-miss",
    ))

    assert found is not None
    assert found.mbid == "rel-hit"


def test_from_row_carries_the_release_mbid_through():
    row = {
        "album_rowid": 1, "album_name": "Album", "artist_name": "Artist",
        "isrcs": [], "release_mbid": "rel-9",
    }
    assert AlbumRef.from_row(row).release_mbid == "rel-9"


def test_from_row_defaults_the_release_mbid_to_none_when_absent():
    row = {"album_rowid": 1, "album_name": "Album", "artist_name": "Artist", "isrcs": []}
    assert AlbumRef.from_row(row).release_mbid is None


# --- release group first, recording search without an ISRC ------------------


def _provider(session, mb=None):
    return CoverArtArchive(
        session=session,
        mb=mb or MusicBrainzClient(session=session, limiter=RateLimiter(0)),
    )


def _release(mbid, title, group=None, group_title=None):
    release = {"id": mbid, "title": title}
    if group:
        release["release-group"] = {"id": group, "title": group_title or title}
    return release


def test_the_release_group_front_is_tried_before_the_release_front():
    session = FakeSession(
        {
            "/isrc/": FakeResponse(payload=isrc_payload("rec-1")),
            "/recording/rec-1": FakeResponse(
                payload=recording_payload(_release("rel-1", "Album", "grp-1"))
            ),
            "/release-group/grp-1/": FakeResponse(content=JPEG),
            "/release/rel-1/": FakeResponse(content=JPEG),
        }
    )

    found = _provider(session).cover(
        AlbumRef(1, "Album", "Artist", ("GBAAA0000001",))
    )

    assert found is not None
    assert found.mbid == "grp-1"
    assert session.urls[2:] == [f"{art_module.CAA_BASE}/release-group/grp-1/front-500"]
    assert session.params[1]["inc"] == "releases+release-groups"


def test_of_two_groups_the_album_titled_one_wins_though_listed_second():
    session = FakeSession(
        {
            "/isrc/": FakeResponse(payload=isrc_payload("rec-1")),
            "/recording/rec-1": FakeResponse(
                payload=recording_payload(
                    _release("rel-c", "Greatest Hits", "grp-comp"),
                    _release("rel-a", "Album", "grp-album"),
                )
            ),
            "/release-group/grp-album/": FakeResponse(content=JPEG),
            "/release-group/grp-comp/": FakeResponse(content=JPEG),
        }
    )

    found = _provider(session).cover(
        AlbumRef(1, "Album", "Artist", ("GBAAA0000001",))
    )

    assert found.mbid == "grp-album"
    assert not any("grp-comp" in url for url in session.urls)


def test_a_group_miss_falls_through_to_a_release_front():
    session = FakeSession(
        {
            "/isrc/": FakeResponse(payload=isrc_payload("rec-1")),
            "/recording/rec-1": FakeResponse(
                payload=recording_payload(_release("rel-1", "Album", "grp-1"))
            ),
            "/release-group/grp-1/": FakeResponse(status_code=404),
            "/release/rel-1/": FakeResponse(content=JPEG),
        }
    )

    found = _provider(session).cover(
        AlbumRef(1, "Album", "Artist", ("GBAAA0000001",))
    )

    assert found.mbid == "rel-1"
    assert session.urls[2:] == [
        f"{art_module.CAA_BASE}/release-group/grp-1/front-500",
        f"{art_module.CAA_BASE}/release/rel-1/front-500",
    ]


def test_releases_without_a_release_group_key_still_walk():
    session = FakeSession(
        {
            "/isrc/": FakeResponse(payload=isrc_payload("rec-1")),
            "/recording/rec-1": FakeResponse(
                payload=recording_payload(_release("rel-1", "Album"))
            ),
            "/release/rel-1/": FakeResponse(content=JPEG),
        }
    )

    found = _provider(session).cover(
        AlbumRef(1, "Album", "Artist", ("GBAAA0000001",))
    )

    assert found.mbid == "rel-1"


def _search_routes(length, front=JPEG):
    return {
        "/recording/rec-s": FakeResponse(
            payload=recording_payload(_release("rel-s", "밤편지", "grp-s"))
        ),
        "/recording": FakeResponse(
            payload={"recordings": [{"id": "rec-s", "length": length}]}
        ),
        "/release-group/grp-s/": FakeResponse(content=front),
    }


def test_no_isrcs_searches_recordings_by_title_and_artist():
    session = FakeSession(_search_routes(253000))

    found = _provider(session).cover(
        AlbumRef(1, "밤편지", "IU", (), tracks=(("밤편지", 253000),))
    )

    assert session.urls[0].endswith("/recording")
    assert session.params[0]["query"] == 'recording:"밤편지" AND artist:"IU"'
    assert session.params[0]["limit"] == 10
    assert found is not None
    assert found.mbid == "grp-s"


def test_a_search_hit_whose_length_differs_by_over_three_seconds_is_not_walked():
    session = FakeSession(_search_routes(260000))

    found = _provider(session).cover(
        AlbumRef(1, "밤편지", "IU", (), tracks=(("밤편지", 253000),))
    )

    assert found is None
    assert not any("/recording/rec-s" in url for url in session.urls)


def test_a_counting_mb_receives_every_call_and_the_session_sees_no_musicbrainz():
    class CountingMb:
        def __init__(self):
            self.paths: list[str] = []

        def get(self, path, params):
            self.paths.append(path)
            if path.startswith("isrc/"):
                return isrc_payload("rec-1")
            if path == "recording/rec-1":
                return recording_payload(_release("rel-1", "A", "g1"))
            if path == "recording":
                return {"recordings": [{"id": "rec-1", "length": 1000}]}
            return {}

    session = FakeSession({"/release-group/g1/": FakeResponse(content=JPEG)})
    mb = CountingMb()
    provider = _provider(session, mb)

    provider.cover(AlbumRef(1, "A", "X", ("GBAAA0000001",)))
    provider.cover(AlbumRef(2, "A", "X", (), tracks=(("T", 1000),)))

    assert mb.paths == [
        "isrc/GBAAA0000001", "recording/rec-1", "recording", "recording/rec-1",
    ]
    assert not any("musicbrainz.org" in url for url in session.urls)


def test_from_row_carries_the_tracks_through():
    row = {
        "album_rowid": 1, "album_name": "Album", "artist_name": "Artist",
        "isrcs": [], "tracks": [("One", 1000), ("Two", None)],
    }
    assert AlbumRef.from_row(row).tracks == (("One", 1000), ("Two", None))


def test_from_row_defaults_the_tracks_to_empty_when_absent():
    row = {"album_rowid": 1, "album_name": "Album", "artist_name": "Artist", "isrcs": []}
    assert AlbumRef.from_row(row).tracks == ()



def _write_post_freeze_album(conn, *, album_rowid, release_mbid, isrc=None):
    """One downloaded release, written the way `run_download` writes it."""
    for offset, track_name in enumerate(("One", "Two")):
        track_rowid = album_rowid + offset
        library_tracks.write_pending(
            conn,
            track_rowid=track_rowid,
            origin="local",
            metadata_tier="musicbrainz",
            artist_name="Artist",
            artist_names=["Artist"],
            album_name="Post-Freeze Record",
            track_name=track_name,
            source_site_id=None,
            isrc=isrc,
            album_rowid=album_rowid,
            track_no=offset + 1,
            disc_no=1,
            recording_mbid=f"rec-{track_rowid}",
            release_mbid=release_mbid,
        )
        library_tracks.mark_done(
            conn,
            track_rowid,
            file_path=f"{track_name}.opus",
            lyrics_path=None,
            art_path=None,
        )


def test_the_backfill_query_carries_a_release_mbid_to_album_ref(conn):
    """The whole way: a download's own writes, the query the backfill calls,
    and `from_row`.

    The two tests above hand `from_row` a dict, which is why neither caught
    the defect this one guards: `albums_needing_art` is the only feeder of
    `from_row`, and it did not select the column at all. Every `AlbumRef` the
    backfill built therefore carried `release_mbid=None` -- and a post-freeze
    album usually has no ISRC either, so the walk had nothing left to try.
    `record_missing` then writes a NULL `album_art` row, which is an answer,
    and this query never returns that album again.
    """
    _write_post_freeze_album(
        conn, album_rowid=1_000_000_000, release_mbid="rel-post-freeze"
    )

    rows = library_art.albums_needing_art(conn)

    assert [row["album_rowid"] for row in rows] == [1_000_000_000]
    assert AlbumRef.from_row(rows[0]).release_mbid == "rel-post-freeze"


def test_the_backfill_query_leaves_a_catalog_album_without_a_release_mbid(conn):
    """`MIN` over a column that is NULL on every row of the group is NULL,
    not an error and not an arbitrary value -- so a catalog album still
    reaches the ISRC walk exactly as before."""
    _write_post_freeze_album(
        conn, album_rowid=1_000_000_010, release_mbid=None, isrc="USXX12345678"
    )

    rows = library_art.albums_needing_art(conn)

    assert AlbumRef.from_row(rows[0]).release_mbid is None
    assert AlbumRef.from_row(rows[0]).isrcs == ("USXX12345678",)


def test_the_backfill_query_carries_two_tracks_per_album(conn):
    # Rowids ascend in a different order from track_no, so the order asserted
    # is disc/track and not insertion.
    for track_rowid, name, track_no, duration in (
        (1_000_000_100, "Three", 3, 3000),
        (1_000_000_101, "One", 1, 1000),
        (1_000_000_102, "Two", 2, 2000),
        (1_000_000_103, "Four", 4, 4000),
    ):
        library_tracks.write_pending(
            conn,
            track_rowid=track_rowid, origin="local", metadata_tier="musicbrainz",
            artist_name="Artist", artist_names=["Artist"],
            album_name="Post-Freeze Record", track_name=name,
            source_site_id=None, isrc=None, album_rowid=1_000_000_100,
            track_no=track_no, disc_no=1, duration_ms=duration,
        )
        if name != "Four":
            library_tracks.mark_done(
                conn, track_rowid,
                file_path=f"{name}.opus", lyrics_path=None, art_path=None,
            )

    rows = library_art.albums_needing_art(conn)

    assert rows[0]["tracks"] == [("One", 1000), ("Two", 2000)]


# --- jobs ------------------------------------------------------------------


def test_a_job_records_its_progress(conn):
    from muse.library import jobs

    job_id = jobs.create(conn, "art_backfill", {"albums": 87})
    jobs.update(conn, job_id, state=jobs.RUNNING, progress=0.5, message="halfway")

    job = jobs.get(conn, job_id)
    assert job["state"] == jobs.RUNNING
    assert job["progress"] == 0.5
    assert job["payload"] == {"albums": 87}


def test_a_finished_job_cannot_be_dragged_back_to_running(conn):
    """A progress callback in flight when the run fails would otherwise leave a
    job that says `running` and never ends — and the UI would show a bar that
    never moves again."""
    from muse.library import jobs

    job_id = jobs.create(conn, "art_backfill")
    jobs.update(conn, job_id, state=jobs.ERROR, message="fell over")
    jobs.update(conn, job_id, state=jobs.RUNNING, progress=0.9)

    assert jobs.get(conn, job_id)["state"] == jobs.ERROR


def test_only_one_backfill_is_active_at_a_time(conn):
    from muse.library import jobs

    first = jobs.create(conn, "art_backfill")
    jobs.update(conn, first, state=jobs.RUNNING)

    assert jobs.active(conn, "art_backfill")["id"] == first

    jobs.update(conn, first, state=jobs.DONE)
    assert jobs.active(conn, "art_backfill") is None


def test_a_job_left_running_by_a_crash_is_closed_at_startup(conn):
    """Otherwise one hard kill mid-backfill blocks every later run forever, and
    the only symptom is art that never appears again."""
    from muse.library import jobs

    job_id = jobs.create(conn, "art_backfill")
    jobs.update(conn, job_id, state=jobs.RUNNING)

    jobs.abandon_running(conn)

    assert jobs.get(conn, job_id)["state"] == jobs.ERROR
    assert jobs.active(conn, "art_backfill") is None


@pytest.mark.parametrize("progress,expected", [(-1, 0.0), (2, 1.0)])
def test_progress_is_clamped(conn, progress, expected):
    from muse.library import jobs

    job_id = jobs.create(conn, "art_backfill")
    jobs.update(conn, job_id, progress=progress)

    assert jobs.get(conn, job_id)["progress"] == expected


def test_the_bus_hands_a_late_subscriber_the_current_state():
    """A phone unlocked halfway through the backfill must see the bar where it
    is, not a blank until the next album lands."""
    from muse.jobs import JobBus

    bus = JobBus()
    bus.publish({"state": "running", "progress": 0.4})

    async def check():
        queue = bus.subscribe()
        return queue.get_nowait()

    import asyncio

    assert asyncio.run(check()) == {"state": "running", "progress": 0.4}


def test_the_health_endpoint_counts_albums_not_tracks(conn, tmp_path):
    from muse.library.queries import health

    load(conn, [(1, 20, "GBAAA0000001"), (2, 20, "GBAAA0000002"),
                (3, 30, "GBAAA0000003")])
    backfill(conn, FakeProvider({20: cover()}), tmp_path)

    art = health(conn)["art"]
    assert art == {"albums": 2, "with_art": 1, "checked": 2, "none_found": 1}


def test_the_payload_survives_a_round_trip(conn):
    """Stored as JSON text; a dict that came back as a string would break the
    UI's progress numbers quietly."""
    from muse.library import jobs

    job_id = jobs.create(conn, "art_backfill", {"albums": 87, "note": "první"})
    assert json.dumps(jobs.get(conn, job_id)["payload"], ensure_ascii=False)
    assert jobs.get(conn, job_id)["payload"]["note"] == "první"


# --- the yt-dlp thumbnail fallback tier -------------------------------------


def test_ytdlp_thumbnail_provider_downloads_thumbnail_url():
    from muse.art import AlbumRef, YtdlpThumbnailArt

    def fake_thumbnail_url(artist_name, album_name):
        return "https://i.ytimg.com/vi/abc/hqdefault.jpg"

    session = FakeSession(
        {
            "i.ytimg.com": FakeResponse(
                content=b"fake image bytes", content_type="image/jpeg"
            )
        }
    )
    provider = YtdlpThumbnailArt(session=session, thumbnail_url=fake_thumbnail_url)
    album = AlbumRef(album_rowid=1, album_name="B", artist_name="A", isrcs=())
    found = provider.cover(album)
    assert found is not None
    assert found.source == "ytdlp_thumbnail"
    assert found.image == b"fake image bytes"


def test_ytdlp_thumbnail_provider_none_when_no_thumbnail_found():
    from muse.art import AlbumRef, YtdlpThumbnailArt

    provider = YtdlpThumbnailArt(session=None, thumbnail_url=lambda a, b: None)
    album = AlbumRef(album_rowid=1, album_name="B", artist_name="A", isrcs=())
    assert provider.cover(album) is None


# --- the chained provider ---------------------------------------------------


def test_chained_provider_returns_first_non_none_cover():
    from muse.art import ChainedArtProvider

    class Hit:
        def cover(self, album):
            return Cover(image=b"x", content_type="image/jpeg", source="hit")

    class Miss:
        def cover(self, album):
            return None

    chain = ChainedArtProvider([Miss(), Hit(), Miss()])
    album = AlbumRef(album_rowid=1, album_name="B", artist_name="A", isrcs=())
    result = chain.cover(album)
    assert result is not None
    assert result.source == "hit"


def test_chained_provider_none_when_every_tier_misses():
    from muse.art import ChainedArtProvider

    class Miss:
        def cover(self, album):
            return None

    chain = ChainedArtProvider([Miss(), Miss()])
    album = AlbumRef(album_rowid=1, album_name="B", artist_name="A", isrcs=())
    assert chain.cover(album) is None
