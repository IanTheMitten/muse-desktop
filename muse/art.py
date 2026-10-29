"""Cover art: where it comes from, and how it gets cached.

The catalog mirror cannot help — its `album_images` and `artist_images` tables
hold zero rows — so art comes from the Cover Art Archive, reached directly by
release MBID when one is already known, and otherwise through a MusicBrainz
lookup by ISRC, or by a title-and-artist recording search when there is none.
The chain is Cover Art Archive, iTunes, then the yt-dlp thumbnail.

Three properties this module exists to hold:

**Per album, not per track.** 723 tracks are 87 albums. At MusicBrainz's 1.1 s
rate limit that is a ninety-second backlog, once, rather than a thirteen-minute
one.

**Never inline with a request.** This runs as a background job. A slow or
unreachable art service is allowed to change how the app looks and never whether
it plays — which is why nothing here is awaited by an endpoint.

**Fetched exactly once, ever.** The image caches on disk and the decision caches
in `album_art`, including the decision that an album has no cover at all.

`ArtProvider` is a Protocol so a tier can be added without touching a caller,
and so tests can inject a fake and never touch the network.
"""

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import requests

from .library import art as library_art
from .musicbrainz import MB_USER_AGENT
from .musicbrainz import MusicBrainzClient
from .musicbrainz import RateLimiter
from .musicbrainz import quote as mb_quote
from .titles import normalize_title
from .titles import qualifiers

log = logging.getLogger("muse.art")

CAA_BASE = "https://coverartarchive.org"

# How far to keep trying before accepting that an album has no art.
#
# ISRCs and recordings are the expensive axis — each one is a rate-limited
# MusicBrainz request — so they stay small: an album whose first two tracks
# find nothing is very unlikely to be rescued by its ninth. Releases are the
# cheap axis, because the Cover Art Archive is not rate limited and a release
# with no cover answers 404 immediately. A popular album carries twenty-five
# releases — every regional pressing, every reissue — and only some of them
# have art, so the limit has to be high enough that the title-matched ones are
# not truncated before the one with a cover is reached.
MAX_ISRCS = 3
MAX_RECORDINGS = 2
MAX_RELEASES = 15

ITUNES_SEARCH = "https://itunes.apple.com/search"
ITUNES_MIN_INTERVAL = 3.0
ITUNES_STOREFRONTS = ("us", "jp")
ITUNES_TRACKS = 2
ITUNES_WINDOW_MS = 3000

REQUEST_TIMEOUT = 20

EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif"}


@dataclass(frozen=True, slots=True)
class AlbumRef:
    """An album, as the art layer needs to see it."""

    album_rowid: int
    album_name: str
    artist_name: str
    isrcs: tuple[str, ...]
    # Set for post-freeze albums, where MusicBrainz frequently has no ISRC to
    # start the recording walk from -- and where the release MBID is already
    # known from the browse screen that queued the download.
    release_mbid: str | None = None
    # (title, duration_ms) per track, for the no-ISRC recording search and iTunes.
    tracks: tuple[tuple[str, int | None], ...] = ()

    @classmethod
    def from_row(cls, row: dict) -> "AlbumRef":
        return cls(
            album_rowid=row["album_rowid"],
            album_name=row["album_name"],
            artist_name=row["artist_name"],
            isrcs=tuple(row.get("isrcs") or ()),
            release_mbid=row.get("release_mbid"),
            tracks=tuple(tuple(t) for t in row.get("tracks") or ()),
        )


@dataclass(frozen=True, slots=True)
class Cover:
    image: bytes
    content_type: str
    source: str
    mbid: str | None = None


class ArtProvider(Protocol):
    def cover(self, album: AlbumRef) -> Cover | None:
        """The album's front cover, or None if this provider has none."""
        ...


class CoverArtArchive:
    """The Cover Art Archive directly by release when a release MBID is
    already known, then MusicBrainz by ISRC to find one when there is no
    release MBID at all, or the archive has nothing for the one we have.

    The direct path exists because a post-freeze release often has no ISRC on
    MusicBrainz for weeks, so the ISRC walk cannot even start — and because,
    when a release MBID is already in hand, going straight to the archive
    skips two rate-limited MusicBrainz calls the walk would otherwise spend
    finding it.

    Failures are all the same failure — no cover — because from the caller's
    side a 404, a timeout, and a DNS error are indistinguishable in effect and
    all of them mean "show the placeholder".
    """

    name = "coverartarchive"

    def __init__(
        self,
        session: requests.Session | None = None,
        mb: MusicBrainzClient | None = None,
        size: int = 500,
    ):
        self._session = session or requests.Session()
        self._mb = mb or MusicBrainzClient()
        self._size = size

    def cover(self, album: AlbumRef) -> Cover | None:
        # The archive is keyed on releases, so a known release MBID skips the
        # ISRC-to-recording-to-release walk entirely -- two rate-limited
        # MusicBrainz calls saved, and the only path that works at all for a
        # new release MusicBrainz has no ISRC for yet. A miss here is not a
        # reason to give up: it falls through to the walk below, which may
        # still find a different release with art.
        if album.release_mbid:
            image = self._front(album.release_mbid)
            if image is not None:
                return Cover(
                    image=image[0],
                    content_type=image[1],
                    source=self.name,
                    mbid=album.release_mbid,
                )

        for recording in self._candidate_recordings(album):
            for kind, mbid in self._fronts(recording, album):
                image = self._front(mbid, kind)
                if image is not None:
                    return Cover(
                        image=image[0],
                        content_type=image[1],
                        source=self.name,
                        mbid=mbid,
                    )
        return None

    def _candidate_recordings(self, album: AlbumRef):
        for isrc in album.isrcs[:MAX_ISRCS]:
            yield from self._recordings(isrc)
        if not album.isrcs:
            for title, duration_ms in album.tracks[:MAX_RECORDINGS]:
                yield from self._search_recordings(title, album.artist_name, duration_ms)

    # --- MusicBrainz ------------------------------------------------------

    def _recordings(self, isrc: str) -> list[str]:
        """Recording MBIDs for an ISRC.

        Two requests are needed rather than one, and the reason is a quiet
        MusicBrainz behaviour: `/isrc/{isrc}?inc=releases` accepts the include
        and returns recordings *without* releases anyway. No error, no warning
        — just an empty answer that looks exactly like an album nobody has
        cover art for. The releases have to be asked for by recording.
        """
        data = self._mb.get(f"isrc/{mb_quote(isrc)}", {})
        if not data:
            return []
        found = [
            recording["id"]
            for recording in data.get("recordings", [])
            if recording.get("id")
        ]
        return found[:MAX_RECORDINGS]

    def _search_recordings(
        self, title: str, artist: str, duration_ms: int | None
    ) -> list[str]:
        """Recording MBIDs by title and artist, for an album with no ISRC."""
        from .discover import _escape_lucene

        query = f'recording:"{_escape_lucene(title)}" AND artist:"{_escape_lucene(artist)}"'
        data = self._mb.get("recording", {"query": query, "limit": 10})
        if not data:
            return []
        found = []
        for recording in data.get("recordings", []):
            length = recording.get("length")
            if not recording.get("id"):
                continue
            if duration_ms and length and abs(length - duration_ms) > 3000:
                continue
            found.append(recording["id"])
        return found[:MAX_RECORDINGS]

    def _fronts(self, recording: str, album: AlbumRef) -> list[tuple[str, str]]:
        """(kind, mbid) pairs to try for a recording, best guess first.

        Release groups come first: a group's front is the archive's pick across
        every pressing, so it has a cover when an individual release does not.
        One recording appears on a dozen releases — the album, three
        compilations, a deluxe edition, and every regional pressing. Preferring
        the one whose title matches the album we asked about is what keeps a
        track from wearing a greatest-hits cover it was never on.
        """
        data = self._mb.get(
            f"recording/{mb_quote(recording)}", {"inc": "releases+release-groups"}
        )
        if not data:
            return []

        wanted = normalize_title(album.album_name).lower()
        exact, rest = [], []
        groups_exact, groups_rest = [], []
        for release in data.get("releases", []):
            mbid = release.get("id")
            if mbid:
                title = normalize_title(release.get("title") or "").lower()
                (exact if title == wanted else rest).append(mbid)
            group = release.get("release-group") or {}
            if group.get("id"):
                title = normalize_title(group.get("title") or "").lower()
                (groups_exact if title == wanted else groups_rest).append(group["id"])

        def dedupe(ids):
            return list(dict.fromkeys(ids))[:MAX_RELEASES]

        return [("release-group", g) for g in dedupe((*groups_exact, *groups_rest))] + [
            ("release", r) for r in dedupe((*exact, *rest))
        ]

    # --- Cover Art Archive ------------------------------------------------

    def _front(self, mbid: str, kind: str = "release") -> tuple[bytes, str] | None:
        response = self._request(
            f"{CAA_BASE}/{kind}/{mbid}/front-{self._size}", {}, {}
        )
        if response is None or response.status_code != 200 or not response.content:
            return None
        content_type = response.headers.get("content-type", "image/jpeg")
        return response.content, content_type.split(";")[0].strip()

    # --- transport --------------------------------------------------------

    def _request(
        self,
        url: str,
        params: dict,
        headers: dict,
        limiter: RateLimiter | None = None,
    ):
        if limiter is not None:
            limiter.wait()
        try:
            response = self._session.get(
                url,
                params=params,
                headers={"User-Agent": MB_USER_AGENT, **headers},
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )
        except requests.RequestException as error:
            log.info("Art request failed: %s (%s)", url, error)
            return None

        if response.status_code == 503 and limiter is not None:
            # Rate limited despite the interval. One backoff, then give up —
            # this album can be missing a cover.
            time.sleep(2)
            limiter.wait()
            try:
                return self._session.get(
                    url,
                    params=params,
                    headers={"User-Agent": MB_USER_AGENT, **headers},
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=True,
                )
            except requests.RequestException:
                return None
        return response


class ItunesArt:
    """A keyless second tier (Decisions 4, 6, 7): the iTunes Search API, accepted
    only on an exact title, version, artist and duration match, because a wrong
    cover is worse than the thumbnail. Native-script titles only -- no
    transliteration; a localised store title simply does not match."""

    name = "itunes"

    def __init__(
        self,
        session: requests.Session | None = None,
        limiter: RateLimiter | None = None,
    ):
        self._session = session or requests.Session()
        self._limiter = limiter or RateLimiter(ITUNES_MIN_INTERVAL)

    def cover(self, album: AlbumRef) -> Cover | None:
        for title, duration_ms in album.tracks[:ITUNES_TRACKS]:
            if not duration_ms:
                continue
            for country in ITUNES_STOREFRONTS:
                hit = self._best(album, title, duration_ms, self._search(album, title, country))
                if hit is None:
                    continue
                url = hit["artworkUrl100"].replace("100x100bb", "600x600bb")
                try:
                    response = self._session.get(url, timeout=REQUEST_TIMEOUT)
                except requests.RequestException:
                    return None
                if response.status_code != 200 or not response.content:
                    return None
                content_type = response.headers.get("content-type", "image/jpeg")
                return Cover(
                    image=response.content,
                    content_type=content_type.split(";")[0].strip(),
                    source=self.name,
                )
        return None

    def _search(self, album: AlbumRef, title: str, country: str) -> list[dict]:
        self._limiter.wait()
        try:
            response = self._session.get(
                ITUNES_SEARCH,
                params={
                    "term": f"{album.artist_name} {title}",
                    "entity": "song",
                    "country": country,
                    "limit": 10,
                },
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code != 200:
                return []
            return response.json()["results"]
        except (requests.RequestException, ValueError, KeyError, TypeError):
            return []

    def _best(self, album: AlbumRef, title: str, duration_ms: int, results: list[dict]):
        artist = album.artist_name.casefold().strip()
        wanted_album = normalize_title(album.album_name)
        accepted = []
        for hit in results:
            name = hit.get("trackName") or ""
            length = hit.get("trackTimeMillis")
            credit = hit.get("artistName") or ""
            names = {credit.casefold().strip()} | {
                p.casefold().strip()
                for p in re.split(r"\s*(?:,|&|\bfeat\.)\s*", credit, flags=re.I)
            }
            if (
                not (qualifiers(name) - qualifiers(title))
                and normalize_title(name) == normalize_title(title)
                and length is not None
                and abs(length - duration_ms) <= ITUNES_WINDOW_MS
                and artist in names
                and hit.get("artworkUrl100")
            ):
                accepted.append(hit)
        for hit in accepted:
            if normalize_title(hit.get("collectionName") or "") == wanted_album:
                return hit
        return accepted[0] if accepted else None


class YtdlpThumbnailArt:
    """The PRD's last-resort art tier: whatever thumbnail yt-dlp reported
    for the video a track was downloaded from. `thumbnail_url` is injected
    rather than this class calling yt-dlp itself — ingest.py already has
    the yt-dlp `info` dict from the download step and can hand over its
    `thumbnail` field directly, so this stays a plain HTTP fetch with no
    yt-dlp dependency of its own."""

    name = "ytdlp_thumbnail"

    def __init__(
        self,
        session: requests.Session | None,
        thumbnail_url: Callable[[str, str], str | None],
    ):
        self._session = session or requests.Session()
        self._thumbnail_url = thumbnail_url

    def cover(self, album: AlbumRef) -> Cover | None:
        url = self._thumbnail_url(album.artist_name, album.album_name)
        if not url:
            return None
        try:
            response = self._session.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException:
            return None
        if response.status_code != 200 or not response.content:
            return None
        content_type = response.headers.get("content-type", "image/jpeg")
        return Cover(
            image=response.content,
            content_type=content_type.split(";")[0].strip(),
            source=self.name,
        )


class ChainedArtProvider:
    """Tries each provider in order, first non-None cover wins. The PRD's
    fallback chain (Cover Art Archive -> iTunes -> yt-dlp thumbnail -> none) as one
    object, so a caller (run_download, art_backfill) makes one `.cover()`
    call instead of duplicating the fallback logic at every call site."""

    def __init__(self, providers: list):
        self._providers = providers

    def cover(self, album: AlbumRef) -> Cover | None:
        for provider in self._providers:
            cover = provider.cover(album)
            if cover is not None:
                return cover
        return None


class NoArt:
    """A provider that finds nothing. What runs when art is switched off."""

    name = "none"

    def cover(self, album: AlbumRef) -> Cover | None:
        return None


# --- the backfill ---------------------------------------------------------


@dataclass(slots=True)
class BackfillResult:
    considered: int = 0
    found: int = 0
    missing: int = 0
    failed: int = 0


def cache_cover(cover: Cover, album: AlbumRef, art_root: Path) -> Path:
    """Write the image next to its album id and return where it landed."""
    art_root.mkdir(parents=True, exist_ok=True)
    path = art_root / f"{album.album_rowid}{EXTENSIONS.get(cover.content_type, '.jpg')}"
    # A partial file left by a killed process would be served as a broken
    # image forever, so the visible name only ever appears complete.
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(cover.image)
    temporary.replace(path)
    return path


def backfill(
    conn,
    provider: ArtProvider,
    art_root: Path,
    *,
    on_progress: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> BackfillResult:
    """Resolve every album that has not been asked about yet.

    Blocking on purpose: it spends its life waiting on a rate limiter, so the
    caller runs it on a worker thread. Keeping asyncio out of here is what lets
    the whole thing be tested with a fake provider and no event loop.

    One album's failure never ends the run. An album that raises is counted and
    left unrecorded, so a transient outage is retried on the next start while a
    genuine absence of art is not.
    """
    result = BackfillResult()
    albums = [AlbumRef.from_row(row) for row in library_art.albums_needing_art(conn)]
    result.considered = len(albums)

    for done, album in enumerate(albums, start=1):
        if should_stop is not None and should_stop():
            break
        try:
            cover = provider.cover(album)
        except Exception:
            log.exception("Art lookup failed for album %d", album.album_rowid)
            result.failed += 1
            cover = None
        else:
            if cover is None:
                library_art.record_missing(conn, album.album_rowid)
                result.missing += 1
            else:
                path = cache_cover(cover, album, art_root)
                library_art.record_cover(
                    conn,
                    album.album_rowid,
                    str(path),
                    source=cover.source,
                    mbid=cover.mbid,
                )
                result.found += 1

        if on_progress is not None:
            on_progress(done, len(albums), f"{album.artist_name} — {album.album_name}")

    return result
