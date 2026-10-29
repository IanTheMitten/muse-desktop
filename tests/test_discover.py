"""The MusicBrainz browse path, against a fake lookup. Never the network."""

from muse import discover


class FakeMb:
    """Records every (path, params) asked for and replays canned answers."""

    def __init__(self, answers: dict):
        self.answers = answers
        self.calls: list[tuple[str, dict]] = []

    def get(self, path: str, params: dict) -> dict | None:
        self.calls.append((path, params))
        return self.answers.get(path)


def test_search_artists_returns_name_and_mbid():
    mb = FakeMb({
        "artist": {"artists": [
            {"id": "mb-1", "name": "Artist One", "disambiguation": "the band",
             "score": 100},
            {"id": "mb-2", "name": "Artist One", "disambiguation": "",
             "score": 71},
        ]}
    })

    found = discover.search_artists(mb, "artist one")

    assert [a["mbid"] for a in found] == ["mb-1", "mb-2"]
    assert found[0]["disambiguation"] == "the band"


def test_search_artists_escapes_lucene_syntax_in_the_typed_name():
    """Verified live: `Therapy?` sent raw returns two artists, neither the
    band, because `?` is a single-character wildcard. MusicBrainz reports no
    error, so an unescaped name fails as "no such artist"."""
    mb = FakeMb({"artist": {"artists": []}})

    discover.search_artists(mb, "Sunn O))) & Therapy?")

    _, params = mb.calls[0]
    assert params["query"] == "Sunn O\\)\\)\\) \\& Therapy\\?"


def test_search_artists_leaves_an_ordinary_name_alone():
    mb = FakeMb({"artist": {"artists": []}})

    discover.search_artists(mb, "radiohead")

    assert mb.calls[0][1]["query"] == "radiohead"


def test_release_groups_filters_on_the_freeze_date():
    mb = FakeMb({
        "release-group": {"release-groups": [
            {"id": "rg-new", "title": "New", "first-release-date": "2026-06-19",
             "primary-type": "Album"},
            {"id": "rg-old", "title": "Old", "first-release-date": "2024-01-01",
             "primary-type": "Album"},
            {"id": "rg-undated", "title": "Undated", "first-release-date": "",
             "primary-type": "Single"},
        ]}
    })

    found = discover.release_groups(mb, "mb-1", since="2025-11-13")

    assert [g["mbid"] for g in found] == ["rg-new"]


def test_release_groups_unfiltered_keeps_everything_newest_first():
    mb = FakeMb({
        "release-group": {"release-groups": [
            {"id": "rg-old", "title": "Old", "first-release-date": "2024-01-01",
             "primary-type": "Album"},
            {"id": "rg-new", "title": "New", "first-release-date": "2026-06-19",
             "primary-type": "Album"},
        ]}
    })

    found = discover.release_groups(mb, "mb-1")

    assert [g["mbid"] for g in found] == ["rg-new", "rg-old"]


# The partial-date cases. MusicBrainz `first-release-date` is ISO but may be
# truncated to a year or a year and a month, and the filter is a plain string
# comparison -- which is a decision, not an accident. Each fake below carries
# a control group on the other side of the freeze, so a test fails both when
# the filter stops filtering and when it filters everything out.
#
# These all pass `since`, so they run through the indexed-search path, and
# they are the reason the client-side comparison survives that move: the
# search index is looser here. Verified live, `firstreleasedate:[2025-11-13
# TO *]` returns groups dated "2025" -- MusicBrainz reads a partial date as a
# period overlapping the range. The fakes below hand back exactly what the
# index would, and these assertions pin that we drop them anyway.


def test_release_groups_keeps_a_year_only_date_after_the_freeze():
    mb = FakeMb({
        "release-group": {"release-groups": [
            {"id": "rg-year", "title": "Year Only", "first-release-date": "2026",
             "primary-type": "Album"},
            {"id": "rg-old", "title": "Old", "first-release-date": "2024-01-01",
             "primary-type": "Album"},
        ]}
    })

    found = discover.release_groups(mb, "mb-1", since="2025-11-13")

    assert [g["mbid"] for g in found] == ["rg-year"]


def test_release_groups_keeps_a_year_and_month_date_after_the_freeze():
    mb = FakeMb({
        "release-group": {"release-groups": [
            {"id": "rg-month", "title": "Year Month",
             "first-release-date": "2026-06", "primary-type": "Album"},
            {"id": "rg-old", "title": "Old", "first-release-date": "2024-01-01",
             "primary-type": "Album"},
        ]}
    })

    found = discover.release_groups(mb, "mb-1", since="2025-11-13")

    assert [g["mbid"] for g in found] == ["rg-month"]


def test_release_groups_drops_the_freezes_own_month_when_the_day_is_unknown():
    mb = FakeMb({
        "release-group": {"release-groups": [
            {"id": "rg-freeze-month", "title": "November",
             "first-release-date": "2025-11", "primary-type": "Album"},
            {"id": "rg-clear", "title": "Clearly After",
             "first-release-date": "2026-01-05", "primary-type": "Album"},
        ]}
    })

    found = discover.release_groups(mb, "mb-1", since="2025-11-13")

    assert [g["mbid"] for g in found] == ["rg-clear"]


def test_release_groups_drops_the_freezes_own_year_when_only_the_year_is_known():
    mb = FakeMb({
        "release-group": {"release-groups": [
            {"id": "rg-freeze-year", "title": "Sometime in 2025",
             "first-release-date": "2025", "primary-type": "Album"},
            {"id": "rg-clear", "title": "Clearly After",
             "first-release-date": "2026-01-05", "primary-type": "Album"},
        ]}
    })

    found = discover.release_groups(mb, "mb-1", since="2025-11-13")

    assert [g["mbid"] for g in found] == ["rg-clear"]


def test_release_groups_drops_a_group_dated_to_the_freeze_day_itself():
    """The scrape ran through 2025-11-13, so a release dated to that day is
    one the mirror should already hold — the boundary is exclusive."""
    mb = FakeMb({
        "release-group": {"release-groups": [
            {"id": "rg-freeze-day", "title": "Freeze Day",
             "first-release-date": "2025-11-13", "primary-type": "Album"},
            {"id": "rg-clear", "title": "Clearly After",
             "first-release-date": "2025-11-14", "primary-type": "Album"},
        ]}
    })

    found = discover.release_groups(mb, "mb-1", since="2025-11-13")

    assert [g["mbid"] for g in found] == ["rg-clear"]


def test_release_groups_filtered_asks_one_indexed_search():
    """The filtered path is a single Lucene query, not a walk. Verified live:
    Bob Dylan since 2025-11-13 reports 27 matches in one page, against the
    twelve browse pages his 1,194 groups would otherwise cost."""
    mb = FakeMb({"release-group": {"count": 1, "release-groups": [
        {"id": "rg-new", "title": "New", "first-release-date": "2026-06-19",
         "primary-type": "Album"},
    ]}})

    found = discover.release_groups(mb, "mb-1", since="2025-11-13")

    assert [g["mbid"] for g in found] == ["rg-new"]
    assert len(mb.calls) == 1
    path, params = mb.calls[0]
    assert path == "release-group"
    assert params["query"] == 'arid:"mb-1" AND firstreleasedate:[2025-11-13 TO *]'
    assert "artist" not in params


def test_release_groups_unfiltered_browses_by_artist():
    mb = FakeMb({"release-group": {"release-group-count": 1, "release-groups": [
        {"id": "rg-1", "title": "Old", "first-release-date": "2024-01-01",
         "primary-type": "Album"},
    ]}})

    discover.release_groups(mb, "mb-1")

    _, params = mb.calls[0]
    assert params["artist"] == "mb-1"
    assert "query" not in params


def test_release_groups_asks_for_the_release_type_when_given():
    mb = FakeMb({"release-group": {"release-group-count": 0, "release-groups": []}})

    discover.release_groups(mb, "mb-1", release_type="single|ep")

    _, params = mb.calls[0]
    assert params["type"] == "single|ep"
    assert params["artist"] == "mb-1"


def test_release_groups_sends_no_type_without_one():
    mb = FakeMb({"release-group": {"release-group-count": 0, "release-groups": []}})

    discover.release_groups(mb, "mb-1")

    _, params = mb.calls[0]
    assert "type" not in params


def test_search_artists_is_none_when_mb_did_not_answer():
    assert discover.search_artists(FakeMb({}), "x") is None


def test_search_artists_is_empty_when_mb_answered_with_nothing():
    assert discover.search_artists(FakeMb({"artist": {}}), "x") == []


def test_release_groups_reads_either_endpoints_envelope_the_same_way():
    """Search reports its total as `count`, browse as `release-group-count`.
    That is the only difference a caller could notice, and it must not be
    one."""
    group = {"id": "rg-1", "title": "New", "first-release-date": "2026-06-19",
             "primary-type": "Album"}

    searched = discover.release_groups(
        FakeMb({"release-group": {"count": 1, "release-groups": [group]}}),
        "mb-1", since="2025-11-13")
    browsed = discover.release_groups(
        FakeMb({"release-group": {"release-group-count": 1, "release-groups": [group]}}),
        "mb-1")

    assert searched == browsed == [{
        "mbid": "rg-1", "title": "New", "release_date": "2026-06-19",
        "primary_type": "Album",
    }]


class PagedFakeMb:
    """Serves one browse page per `offset`, the way the real endpoint does."""

    def __init__(self, groups: list[dict], page_size: int = 100):
        self.groups = groups
        self.page_size = page_size
        self.offsets: list[int] = []

    def get(self, path: str, params: dict) -> dict | None:
        offset = params.get("offset", 0)
        self.offsets.append(offset)
        return {
            "release-group-count": len(self.groups),
            "release-groups": self.groups[offset:offset + self.page_size],
        }


def _group(name, date: str = "2026-01-01") -> dict:
    return {"id": f"rg-{name}", "title": f"Title {name}",
            "first-release-date": date, "primary-type": "Album"}


def test_release_groups_unfiltered_walks_past_the_first_page():
    """The unfiltered path browses, and browse comes back in no useful order
    — verified live, Bob Dylan's 1,194 groups are neither ascending nor
    descending by date — so the newest record can sit on any page and one
    page would silently miss it."""
    groups = [_group(i, "2024-01-01") for i in range(100)] + [_group("new", "2026-06-19")]
    mb = PagedFakeMb(groups)

    found = discover.release_groups(mb, "mb-1")

    assert len(found) == 101
    assert found[0]["mbid"] == "rg-new"
    assert mb.offsets == [0, 100]


def test_release_groups_stops_on_a_full_last_page_because_the_total_says_so():
    """A page can be exactly full and still be the last one. Both endpoints
    say so in different words — browse as `release-group-count`, search as
    `count` — and reading either is what saves the wasted request that the
    short-page rule would otherwise need to discover the hard way."""
    groups = [_group(i) for i in range(100)]
    browse = FakeMb({"release-group": {"release-group-count": 100, "release-groups": groups}})
    search = FakeMb({"release-group": {"count": 100, "release-groups": groups}})

    assert len(discover.release_groups(browse, "mb-1")) == 100
    assert len(discover.release_groups(search, "mb-1", since="2020-01-01")) == 100
    assert len(browse.calls) == 1
    assert len(search.calls) == 1


def test_release_groups_tells_silence_apart_from_emptiness():
    """The one function here that does not collapse the two.

    Its caller is a screen that has to pick between "Nothing newer than the
    mirror." and "MusicBrainz did not answer.", and an empty list for both
    leaves it guessing. Everything else in this module may collapse them
    because nothing downstream has a second sentence to render.
    """
    silent = discover.release_groups(FakeMb({}), "mb-1")
    empty = discover.release_groups(
        FakeMb({"release-group": {"release-group-count": 0, "release-groups": []}}),
        "mb-1",
    )

    assert silent is None
    assert empty == []


def test_release_groups_keeps_the_pages_it_already_walked():
    """Only the first page decides. A walk that got pages and then lost the
    service still returns them -- a partial discography beats a request that
    never comes back, and it is not the same event as never being answered."""

    class FailsOnTheSecondPage:
        def __init__(self):
            self.offsets: list[int] = []

        def get(self, path: str, params: dict) -> dict | None:
            offset = params.get("offset", 0)
            self.offsets.append(offset)
            if offset:
                return None
            return {"release-group-count": 150,
                    "release-groups": [_group(i) for i in range(100)]}

    mb = FailsOnTheSecondPage()

    found = discover.release_groups(mb, "mb-1")

    assert mb.offsets == [0, 100]
    assert found is not None and len(found) == 100


def test_release_groups_stops_at_the_page_ceiling():
    """A pathological artist must not hold the request open indefinitely."""
    mb = PagedFakeMb([_group(i) for i in range(100 * (discover.MAX_BROWSE_PAGES + 3))])

    found = discover.release_groups(mb, "mb-1")

    assert len(mb.offsets) == discover.MAX_BROWSE_PAGES
    assert len(found) == 100 * discover.MAX_BROWSE_PAGES


def test_pick_release_mbid_takes_official_and_settles_a_dead_tie_on_digital_media():
    """The promo is earlier and would win on date alone; status outranks it.
    The CD and the digital pressing then tie on everything but format."""
    mb = FakeMb({
        "release": {"releases": [
            {"id": "rel-cd", "status": "Official", "date": "2026-06-19",
             "media": [{"format": "CD", "track-count": 12}]},
            {"id": "rel-digital", "status": "Official", "date": "2026-06-19",
             "media": [{"format": "Digital Media", "track-count": 12}]},
            {"id": "rel-promo", "status": "Promotion", "date": "2026-05-01",
             "media": [{"format": "Digital Media", "track-count": 12}]},
        ]}
    })

    assert discover.pick_release_mbid(mb, "rg-1") == "rel-digital"


def test_pick_release_mbid_ranks_official_then_track_count_then_date():
    """PRD O12: deluxe editions are supersets, so the superset is the one
    worth showing. Every rival here loses on exactly one level, so each
    adjacent pair of the rank tuple is in genuine conflict and swapping any
    two of them changes the answer.

    The promo carries the most tracks, so status must outrank track count.
    The standard edition is the earliest, so track count must outrank date.
    The reissue ties the deluxe on tracks and is the Digital Media pressing,
    so date must outrank format. And the deluxe is the CD, so track count
    must outrank format too."""
    mb = FakeMb({
        "release": {"releases": [
            {"id": "rel-promo", "status": "Promotion", "date": "2026-04-01",
             "media": [{"format": "Digital Media", "track-count": 20}]},
            {"id": "rel-standard", "status": "Official", "date": "2026-05-01",
             "media": [{"format": "Digital Media", "track-count": 12}]},
            {"id": "rel-deluxe", "status": "Official", "date": "2026-06-19",
             "media": [{"format": "CD", "track-count": 17}]},
            {"id": "rel-deluxe-reissue", "status": "Official", "date": "2028-03-01",
             "media": [{"format": "Digital Media", "track-count": 17}]},
        ]}
    })

    assert discover.pick_release_mbid(mb, "rg-1") == "rel-deluxe"
    path, params = mb.calls[0]
    assert path == "release"
    # Without this the response carries no `media`, so three of the four rank
    # keys read nothing and the choice degrades to MusicBrainz's own order.
    assert params["inc"] == "media"
    assert params["release-group"] == "rg-1"


def test_pick_release_mbid_is_none_when_the_group_has_no_release():
    assert discover.pick_release_mbid(FakeMb({"release": {"releases": []}}), "rg-1") is None


def test_release_tracks_reads_numbers_length_and_isrc():
    mb = FakeMb({
        "release/rel-1": {
            "id": "rel-1",
            "title": "The Album",
            "date": "2026-06-19",
            "artist-credit": [{"name": "Artist One"}],
            "media": [{
                "position": 1,
                "track-count": 2,
                "tracks": [
                    {"position": 1, "title": "One", "length": 214000,
                     "recording": {"id": "rec-1", "isrcs": ["USXX12345678"]}},
                    {"position": 2, "title": "Two", "length": None,
                     "recording": {"id": "rec-2", "isrcs": []}},
                ],
            }],
        }
    })

    release = discover.release_tracks(mb, "rel-1")

    assert release["album_name"] == "The Album"
    assert release["artist_name"] == "Artist One"
    assert release["release_mbid"] == "rel-1"
    assert release["release_date"] == "2026-06-19"
    first, second = release["tracks"]
    assert first == {
        "recording_mbid": "rec-1", "track_name": "One", "track_no": 1,
        "disc_no": 1, "duration_ms": 214000, "isrc": "USXX12345678",
        "total_tracks": 2, "artist_name": "Artist One",
        "artist_names": ["Artist One"],
    }
    assert second["isrc"] is None
    assert second["duration_ms"] is None
    path, params = mb.calls[0]
    assert path == "release/rel-1"
    assert params["inc"] == "recordings+artist-credits+isrcs"


def test_release_tracks_keeps_a_tracks_own_artist_credit():
    """A feature or a split credits somebody the release does not. Stamping
    the release's artist over it is the defect `fix_manifest_artists.py` was
    written to repair (PRD line 456)."""
    mb = FakeMb({
        "release/rel-1": {
            "id": "rel-1",
            "title": "The Album",
            "artist-credit": [{"name": "Artist One"}],
            "media": [{
                "position": 1,
                "track-count": 2,
                "tracks": [
                    {"position": 1, "title": "Solo", "length": 1000,
                     "recording": {"id": "rec-1", "isrcs": []}},
                    {"position": 2, "title": "The Feature", "length": 2000,
                     "artist-credit": [{"name": "Artist One"}, {"name": "Guest Two"}],
                     "recording": {"id": "rec-2", "isrcs": []}},
                ],
            }],
        }
    })

    solo, feature = discover.release_tracks(mb, "rel-1")["tracks"]

    assert solo["artist_names"] == ["Artist One"]
    assert solo["artist_name"] == "Artist One"
    assert feature["artist_names"] == ["Artist One", "Guest Two"]
    assert feature["artist_name"] == "Artist One"


def test_release_tracks_counts_each_medium_separately():
    """`total_tracks` is the disc's length, not the release's — `tag_m4a`
    writes it into `trkn`, which means "track N of M on this disc"."""
    mb = FakeMb({
        "release/rel-1": {
            "id": "rel-1",
            "title": "The Double",
            "artist-credit": [{"name": "Artist One"}],
            "media": [
                {"position": 1, "track-count": 2, "tracks": [
                    {"position": 1, "title": "A1", "length": 1000,
                     "recording": {"id": "rec-a1", "isrcs": []}},
                    {"position": 2, "title": "A2", "length": 1000,
                     "recording": {"id": "rec-a2", "isrcs": []}},
                ]},
                {"position": 2, "track-count": 3, "tracks": [
                    {"position": 1, "title": "B1", "length": 1000,
                     "recording": {"id": "rec-b1", "isrcs": []}},
                ]},
            ],
        }
    })

    tracks = discover.release_tracks(mb, "rel-1")["tracks"]

    assert [(t["disc_no"], t["track_no"], t["total_tracks"]) for t in tracks] == [
        (1, 1, 2), (1, 2, 2), (2, 1, 3),
    ]


def test_release_tracks_skips_a_video_recording():
    """A pressing's tracklist can carry a video among the songs, and the
    downloader has no way to want one: it asks yt-dlp for audio by name, and
    the name of a title-track visualizer is the name of the record, so
    `ytsearch1:` hands back the full-album upload. That is not hypothetical --
    "you seem pretty sad for a girl so in love" ends in a 20-second video of
    the same title, and queueing the record downloaded 52 minutes of audio and
    filed it as a 20-second track.

    Skipped here rather than guarded downstream because it is not a download
    that went wrong: MusicBrainz says plainly that this entry is not a song,
    and a browse screen offering it is offering something that cannot be had.
    """
    mb = FakeMb({
        "release/rel-1": {
            "id": "rel-1",
            "title": "The Album",
            "artist-credit": [{"name": "Artist One"}],
            "media": [{
                "position": 1,
                "track-count": 3,
                "tracks": [
                    {"position": 1, "title": "One", "length": 214000,
                     "recording": {"id": "rec-1", "isrcs": []}},
                    {"position": 2, "title": "The Album", "length": 20502,
                     "recording": {"id": "rec-2", "isrcs": [], "video": True}},
                    {"position": 3, "title": "Three", "length": 180000,
                     "recording": {"id": "rec-3", "isrcs": []}},
                ],
            }],
        }
    })

    tracks = discover.release_tracks(mb, "rel-1")["tracks"]

    assert [t["recording_mbid"] for t in tracks] == ["rec-1", "rec-3"]
    # `total_tracks` still counts the medium, video included -- it is what
    # the pressing holds, and it is written into an m4a `trkn` tag beside a
    # `track_no` that is likewise the pressing's numbering.
    assert [t["total_tracks"] for t in tracks] == [3, 3]
    assert [t["track_no"] for t in tracks] == [1, 3]


def test_release_tracks_is_none_when_mb_does_not_answer():
    assert discover.release_tracks(FakeMb({}), "rel-1") is None
