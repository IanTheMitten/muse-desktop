"""The MusicBrainz browse path for music the catalog mirror never saw.

This module asks MusicBrainz which artists and records exist, and is the one
MusicBrainz path that runs inline with a request rather than as a background
job, because a person is typing and waiting.

Takes an injected `MusicBrainzLookup`, never a client of its own: tests run
against a fake and never reach the network, the same seam `art.py`'s providers
use. Every function returns plain dicts, and most answer nothing on any
failure, because from most callers' side a timeout and a rate-limit exhaustion
mean the same thing: MusicBrainz did not answer.

**Two functions here separate "did not answer" from "answered with nothing", and
both do it the same way: `None` is silence, and a falsy value of the answer's
own type is emptiness.** `release_groups` and `search_artists` return `None`
against `[]`. The difference is not tidiness, it is what the screen has to
render — "This artist has no releases of that kind." and "MusicBrainz did not
answer." are different sentences, and a browse screen that cannot tell them apart
remembers an outage as a fact about the artist. `search_artists` is the only
artist search, so a person typing into a box must be told an outage from no
match.
"""

from __future__ import annotations

from .musicbrainz import MusicBrainzLookup, quote

# 100 is the browse endpoint's own ceiling; asking for more is silently capped.
RELEASE_GROUP_LIMIT = 100
ARTIST_SEARCH_LIMIT = 10

# The screen's `kind` -> the browse endpoint's `type` filter.
RELEASE_TYPES = {"album": "album", "single": "single|ep"}

# The bound on `release_groups`' paging. Browse returns no useful order, so
# there is no early exit and a prolific artist costs one rate-limited request
# per page. Ten pages is 1,000 release groups and about eleven seconds in the
# worst case, which is the most that belongs in front of someone waiting on a
# search box.
MAX_BROWSE_PAGES = 10

# Lucene's syntax characters, escaped out of a typed artist name before it
# reaches the search index. See `search_artists`.
LUCENE_SPECIALS = frozenset('\\+-!():^[]"{}~*?|&/')


def _escape_lucene(term: str) -> str:
    """Make every character in a typed name mean itself."""
    return "".join(("\\" + ch) if ch in LUCENE_SPECIALS else ch for ch in term)


def _credit_names(credits: object) -> list[str]:
    """The artist names out of a MusicBrainz artist-credit list, in order."""
    if not isinstance(credits, list):
        return []
    return [credit["name"] for credit in credits if isinstance(credit, dict) and credit.get("name")]


def search_artists(
    mb: MusicBrainzLookup, term: str, limit: int = ARTIST_SEARCH_LIMIT
) -> list[dict] | None:
    """Artists matching a typed name, in MusicBrainz's own score order.
    `None` if MusicBrainz never answered; `[]` if it answered with no artists.

    The ordering is the search index's, passed through rather than recomputed
    — `score` rides along so a caller can show how sure the match is.

    The term is Lucene-escaped first, because this endpoint is a query parser
    and band names are full of its operators. Checked against the live
    service rather than assumed: MusicBrainz never returns a parse error, so
    the damage is silent. Searching `Therapy?` raw returns two artists,
    neither of them the band — the `?` is read as a single-character
    wildcard. Escaped, `Therapy\\?` returns the band first at score 100. The
    escape is harmless where it is not needed: `Sunn O)))`, `AC/DC`,
    `Jay-Z` and `Godspeed You! Black Emperor` all return the right artist
    first, escaped or not. Silent wrong answers are the worst failure on a
    path that exists to find artists too obscure to have a catalog row.
    """
    data = mb.get("artist", {"query": _escape_lucene(term), "limit": limit})
    if data is None:
        return None
    return [
        {
            "mbid": artist["id"],
            "name": artist.get("name") or "",
            "disambiguation": artist.get("disambiguation") or "",
            "score": artist.get("score"),
        }
        for artist in data.get("artists", [])
        if artist.get("id")
    ]


def release_groups(
    mb: MusicBrainzLookup,
    artist_mbid: str,
    release_type: str | None = None,
) -> list[dict] | None:
    """This artist's release groups, newest first. `None` if MusicBrainz
    never answered. `release_type` adds a `type` param to the browse.

    **The one function here that distinguishes silence from emptiness**, and
    the module docstring's general "every failure is the same failure" is
    narrowed for it deliberately. Its caller is a screen that has to choose
    between two sentences -- "This artist has no releases of that kind."
    and "MusicBrainz did not answer." -- and an empty list for both leaves it
    guessing. Only the first page decides: a walk that got some pages and
    then lost the service still keeps what it holds, for the reason spelled
    out below.

    Release *groups*, not releases: a group holds every pressing of one record
    — regional editions, digital against CD, the deluxe — and surfacing those
    would make the screen an edition picker. `pick_release_mbid` chooses among
    them instead.

    **Paged browse, because browse has no order.** Checked, not
    assumed: `release-group?artist=` for Dylan opens 2009, 2002-08,
    2006-08-08, 1989 and ends on 1976 — neither ascending nor descending. So
    there is no page at which the walk may stop early, and one page would
    surface an arbitrary 8% of that artist while quietly omitting their newest
    record. The walk is bounded at `MAX_BROWSE_PAGES`: past that an artist
    comes back truncated by an arbitrary slice rather than by age, since there
    is no order to truncate along, and a mid-walk failure keeps the pages
    already in hand. Both are silent, and both beat a request that never
    returns. The sort below is ours and is the only reason either result is
    ordered.
    """
    def fetch(offset: int) -> dict | None:
        params = {"artist": artist_mbid, "limit": RELEASE_GROUP_LIMIT, "offset": offset}
        if release_type:
            params["type"] = release_type
        return mb.get("release-group", params)

    found = []
    offset = 0
    for page_number in range(MAX_BROWSE_PAGES):
        data = fetch(offset)
        if not data:
            # Nothing at all came back, so there is nothing to be said about
            # this artist -- not even that they have no new records. A
            # failure part-way through is different: those pages are real,
            # and the docstring above explains why keeping them beats a
            # request that never returns.
            if page_number == 0:
                return None
            break

        page = data.get("release-groups") or []
        for group in page:
            if not group.get("id"):
                continue
            date = group.get("first-release-date") or ""
            found.append({
                "mbid": group["id"],
                "title": group.get("title") or "",
                "release_date": date,
                "primary_type": group.get("primary-type") or "",
            })

        offset += len(page)
        total = data.get("release-group-count")
        if len(page) < RELEASE_GROUP_LIMIT or (isinstance(total, int) and offset >= total):
            break

    found.sort(key=lambda group: group["release_date"], reverse=True)
    return found


def pick_release_mbid(mb: MusicBrainzLookup, release_group_mbid: str) -> str | None:
    """One release out of a group, chosen the way story 97 collapses editions.

    Official first: a promo or a bootleg is not the record. Then the most
    tracks, which is PRD open question O12's rule — deluxe editions are
    supersets, and the release chosen here decides which tracklist is shown
    and can be queued, so preferring the standard edition would silently hide
    songs that exist. Then O12's own tie-break, the earliest date, which
    favours the first pressing over a later reissue of the same thing.

    Format comes last, and only as a tie-break. Preferring Digital Media
    looks like it should matter — it is the pressing closest to what a
    download produces — but it does not: yt-dlp pulls audio from YouTube
    regardless of which pressing MusicBrainz calls canonical, so the format
    says nothing about what will arrive. It stays in the tuple so that two
    otherwise identical pressings resolve to the same release every time
    rather than to whichever MusicBrainz happened to list first.

    `inc=media` is what carries the formats and track counts that three of
    those four keys read; without it the ranking silently degrades to
    whichever Official release MusicBrainz listed first.
    """
    data = mb.get(
        "release",
        {"release-group": release_group_mbid, "inc": "media", "limit": 100},
    )
    if not data:
        return None

    releases = [r for r in data.get("releases", []) if r.get("id")]
    if not releases:
        return None

    def rank(release: dict) -> tuple:
        media = release.get("media") or []
        formats = {medium.get("format") for medium in media}
        tracks = sum(medium.get("track-count") or 0 for medium in media)
        return (
            0 if release.get("status") == "Official" else 1,
            -tracks,
            release.get("date") or "9999",
            0 if "Digital Media" in formats else 1,
        )

    return min(releases, key=rank)["id"]


def release_tracks(mb: MusicBrainzLookup, release_mbid: str) -> dict | None:
    """One release, flattened to the facts a download job needs.

    Everything here ends up on the job payload, because there is no disk cache
    behind the client: resolving at download time would re-ask for this same
    release once per track, at 1.1 s each.

    Video entries are dropped -- see the guard below. `total_tracks` still
    counts the medium as pressed, videos included: it is written to an m4a
    `trkn` tag beside a `track_no` that is the pressing's own numbering, and
    renumbering one without the other would make the pair say something
    neither MusicBrainz nor the sleeve says.

    **Each track carries its own artist credit**, falling back to the
    release's only when MusicBrainz gives the track none. A compilation, a
    split, or any track with a feature credits somebody the release does not,
    and `inc=artist-credits` already pays for that detail per track — reading
    only the release-level credit would throw it away. PRD line 456 records
    that `fix_manifest_artists.py` had to be written because "the downloader
    stamped every track with its album's artist, losing features and
    collaborators"; that repair is not worth earning twice.
    """
    data = mb.get(
        f"release/{quote(release_mbid)}",
        {"inc": "recordings+artist-credits+isrcs"},
    )
    if not data:
        return None

    artist_names = _credit_names(data.get("artist-credit"))

    tracks: list[dict] = []
    for medium in data.get("media") or []:
        disc_no = medium.get("position") or 1
        # `total_tracks` counts this medium, not the release: on a two-disc
        # set it is the disc's length, not the sum. That is the number its
        # one consumer wants — `tag_m4a` writes `trkn = (track_no,
        # total_tracks)`, and the iTunes field means "track N of M on this
        # disc", which `disc_no` then places within the set.
        total = medium.get("track-count")
        for track in medium.get("tracks") or []:
            recording = track.get("recording") or {}
            if not recording.get("id"):
                continue
            # A video is not a song, and the downloader has no way to want
            # one: it asks yt-dlp for audio by name, and a title-track
            # visualizer is named after the record, so `ytsearch1:` answers
            # with the full-album upload. Queueing "you seem pretty sad for a
            # girl so in love" downloaded 52 minutes of audio and filed it as
            # the 20-second video that ends that pressing. Dropped here, at
            # the point where MusicBrainz says plainly that this entry is not
            # a song, rather than guarded at download time -- a browse screen
            # that offers it is offering something that cannot be had.
            if recording.get("video"):
                continue
            isrcs = recording.get("isrcs") or []
            track_artists = _credit_names(track.get("artist-credit")) or artist_names
            tracks.append({
                "recording_mbid": recording["id"],
                "track_name": track.get("title") or "",
                "track_no": track.get("position"),
                "disc_no": disc_no,
                "duration_ms": track.get("length"),
                "isrc": isrcs[0] if isrcs else None,
                "total_tracks": total,
                "artist_name": track_artists[0] if track_artists else "",
                "artist_names": track_artists,
            })

    return {
        "release_mbid": data.get("id") or release_mbid,
        "album_name": data.get("title") or "",
        "release_date": data.get("date") or "",
        "artist_name": artist_names[0] if artist_names else "",
        "artist_names": artist_names,
        "tracks": tracks,
    }
