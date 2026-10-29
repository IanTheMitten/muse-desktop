"""ItunesArt: a wrong cover is worse than the thumbnail, so what is pinned here
is every false accept the spec names (Decisions 4, 6, 7). No network."""

import pytest
import requests

from muse.art import ItunesArt, AlbumRef, ITUNES_SEARCH  # noqa: I001
from muse.musicbrainz import RateLimiter

IMG = b"\xff\xd8\xff\xe0" + b"itunes cover"


class Response:
    def __init__(self, status_code=200, payload=None, content=b""):
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.headers = {"content-type": "image/jpeg"}

    def json(self):
        return self._payload


class Session:
    """Search URLs answer by params["country"]; 600x600bb URLs return image bytes."""

    def __init__(self, by_country=None, status=200, error=None):
        self.by_country = by_country or {}
        self.status = status
        self.error = error
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, url, params=None, **kwargs):
        self.calls.append((url, params))
        if self.error:
            raise self.error
        if url == ITUNES_SEARCH:
            results = self.by_country.get(params["country"], [])
            return Response(self.status, {"results": results})
        if "600x600bb" in url:
            return Response(content=IMG)
        return Response(404)

    @property
    def searches(self):
        return [p for u, p in self.calls if u == ITUNES_SEARCH]

    @property
    def images(self):
        return [u for u, _ in self.calls if u != ITUNES_SEARCH]


class CountingLimiter(RateLimiter):
    def __init__(self):
        super().__init__(0)
        self.waits = 0

    def wait(self):
        self.waits += 1
        super().wait()


def hit(title="Dynamite", artist="BTS", album="Dynamite - Single",
        ms=199000, art="https://x/a/100x100bb.jpg"):
    return {
        "trackName": title,
        "artistName": artist,
        "collectionName": album,
        "trackTimeMillis": ms,
        "artworkUrl100": art,
    }


def album(*tracks, artist="BTS", name="Dynamite - Single"):
    return AlbumRef(
        album_rowid=1, album_name=name, artist_name=artist, isrcs=(),
        tracks=tuple(tracks),
    )


def run(alb, by_country=None, **session_kw):
    session = Session(by_country, **session_kw)
    cover = ItunesArt(session=session, limiter=RateLimiter(0)).cover(alb)
    return cover, session


def test_name():
    assert ItunesArt(session=Session(), limiter=RateLimiter(0)).name == "itunes"


def test_accepts_native_korean_title():
    alb = album(("좋은 날", 233000), artist="IU", name="Real")
    cover, s = run(alb, {"us": [hit("좋은 날", "IU", "Real", 233500)]})
    assert cover.source == "itunes" and cover.mbid is None
    assert cover.image == IMG
    assert s.images == ["https://x/a/600x600bb.jpg"]


def test_localised_korean_title_rejected():
    """Known gap, Decision 4 A: iTunes returns 밤편지 as `Through the Night`."""
    alb = album(("밤편지", 253000), artist="IU")
    cover, s = run(alb, {
        "us": [hit("Through the Night", "IU", ms=253293)],
        "jp": [hit("Through the Night", "IU", ms=253293)],
    })
    assert cover is None and s.images == []


def test_japanese_found_on_jp_after_empty_us():
    alb = album(("夜に駆ける", 258000), artist="YOASOBI")
    cover, s = run(alb, {"jp": [hit("夜に駆ける", "YOASOBI", ms=258840)]})
    assert cover is not None
    assert [p["country"] for p in s.searches] == ["us", "jp"]
    assert s.searches[0] == {
        "term": "YOASOBI 夜に駆ける", "entity": "song", "country": "us", "limit": 10,
    }


@pytest.mark.parametrize(
    "library, candidate",
    [
        ("Dynamite", "Dynamite (Live)"),
        ("Dynamite", "Dynamite - Live"),
        ("Up", "Up (Remix)"),
        ("Speed of Summer", "Speed of Summer (Inst.)"),
    ],
)
def test_qualified_variant_rejected(library, candidate):
    alb = album((library, 199000))
    cover, s = run(alb, {"us": [hit(candidate)], "jp": [hit(candidate)]})
    assert cover is None and s.images == []


def test_title_that_itself_contains_qualifier_accepted():
    alb = album(("Live Your Life", 199000))
    cover, _ = run(alb, {"us": [hit("Live Your Life")]})
    assert cover is not None


def test_duration_tolerance_edge():
    alb = album(("Dynamite", 199000))
    assert run(alb, {"us": [hit(ms=202001)], "jp": [hit(ms=202001)]})[0] is None
    assert run(alb, {"us": [hit(ms=202000)]})[0] is not None


def test_prefers_hit_matching_album():
    alb = album(("Dynamite", 199000), name="Be")
    cover, s = run(alb, {"us": [
        hit(album="Greatest Hits", art="https://x/first/100x100bb.jpg"),
        hit(album="Be", art="https://x/second/100x100bb.jpg"),
    ]})
    assert cover is not None
    assert s.images == ["https://x/second/600x600bb.jpg"]


def test_same_title_other_artist_rejected():
    alb = album(("Dynamite", 199000))
    other = hit(artist="Karaoke Kings")
    cover, s = run(alb, {"us": [other], "jp": [other]})
    assert cover is None and s.images == []


@pytest.mark.parametrize("credit", ["Coldplay & BTS", "BTS feat. Halsey"])
def test_artist_credit_split_accepted(credit):
    alb = album(("Dynamite", 199000))
    cover, _ = run(alb, {"us": [hit(artist=credit)]})
    assert cover is not None


def test_track_without_duration_makes_no_request():
    cover, s = run(album(("Dynamite", None)))
    assert cover is None and s.calls == []


def test_three_tracks_at_most_four_searches_never_kr():
    alb = album(("A", 1000), ("B", 2000), ("C", 3000))
    cover, s = run(alb)
    assert cover is None
    assert len(s.searches) <= 4
    assert "kr" not in {p["country"] for p in s.searches}


@pytest.mark.parametrize(
    "kw", [{"status": 403}, {"error": requests.ConnectionError("down")}]
)
def test_failure_returns_none(kw):
    cover, _ = run(album(("Dynamite", 199000)), {"us": [hit()]}, **kw)
    assert cover is None


def test_limiter_waits_once_per_search_not_per_image():
    session = Session({"us": [hit()]})
    limiter = CountingLimiter()
    cover = ItunesArt(session=session, limiter=limiter).cover(
        album(("Dynamite", 199000))
    )
    assert cover is not None and len(session.images) == 1
    assert limiter.waits == len(session.searches) >= 1
