"""Read-only access to the Spotify catalog mirrors.

The only module that opens `spotify_clean.sqlite3` (17.8 GB) and
`spotify_clean_audio_features.sqlite3` (29 GB). They are opened with `mode=ro`
URIs and are never written by anything, ever.

Slice 1 asks the catalog exactly one kind of question: facts about a track by
`rowid`, on the manifest import path. The manifest carries neither `duration_ms`
nor `isrc`, and its `disc_no` is not reliable. Artist search and album browse
are named on `CatalogLookup` because slice 3 needs them; slice 1 does not call
them.

`CatalogLookup` is a Protocol so the import path can be tested against an
injected fake. Tests never open a 17 GB file.
"""

import re
from dataclasses import dataclass
from typing import Protocol

_ISRC_RE = re.compile(r"^[A-Za-z]{2}[A-Za-z0-9]{3}\d{7}$")


def clean_isrc(raw: str | None) -> str | None:
    """Same sanitizer as `clean_lyrics.py`. The mirror's ISRCs carry stray
    quotes and casing from the scrape."""
    if not raw:
        return None
    text = raw.strip().strip("'\"").strip().upper()
    return text if _ISRC_RE.match(text) else None


@dataclass(frozen=True, slots=True)
class TrackFacts:
    track_rowid: int
    duration_ms: int | None
    isrc: str | None
    disc_no: int | None
    track_no: int | None
    album_rowid: int | None


class CatalogLookup(Protocol):
    def track_facts(self, rowids: list[int]) -> dict[int, TrackFacts]:
        """Facts for the given track rowids. Missing rowids are simply absent."""
        ...

    def search_artists(self, term: str, limit: int = 25) -> list[dict]:
        """Artists whose name contains `term`, exact match first, then by
        popularity. Empty list on no match."""
        ...

    def artist_albums(self, artist_rowid: int) -> list[dict]:
        """This artist's albums, newest release first."""
        ...

    def album_tracks(self, album_rowid: int) -> list[dict]:
        """This album's tracks, disc/track order, with a flattened
        `artist_names` string (`" & "`-joined, matching `music_downloader.py`'s
        existing convention) for display, and the same credit as
        `artist_list`, primary artist first, for anything that stores it.
        The string cannot be split back: "Simon & Garfunkel" is one artist."""
        ...

    def track_by_rowid(self, track_rowid: int) -> dict | None:
        """One track by its own rowid, same shape as one `album_tracks` row.
        None if the rowid isn't in the catalog."""
        ...

    def artist_by_rowid(self, artist_rowid: int) -> dict | None:
        """One artist by its own rowid. None if the rowid isn't in the
        catalog."""
        ...

    def track_id_for_isrc(self, isrc: str) -> str | None:
        """Spotify track id for an ISRC: keyed read on `tracks_isrc`, highest
        popularity then lowest rowid, never a search."""
        ...


class EmptyCatalog:
    """A catalog that knows nothing. Lets the import run on a machine without
    the mirrors — the rows land with NULL duration and ISRC rather than
    failing."""

    def track_facts(self, rowids: list[int]) -> dict[int, TrackFacts]:
        return {}

    def search_artists(self, term: str, limit: int = 25) -> list[dict]:
        return []

    def artist_albums(self, artist_rowid: int) -> list[dict]:
        return []

    def album_tracks(self, album_rowid: int) -> list[dict]:
        return []

    def track_by_rowid(self, track_rowid: int) -> dict | None:
        return None

    def artist_by_rowid(self, artist_rowid: int) -> dict | None:
        return None

    def track_id_for_isrc(self, isrc: str) -> str | None:
        return None
