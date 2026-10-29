"""Read-only access to the Spotify audio-features mirror.

The only module that opens `spotify_clean_audio_features.sqlite3` (29 GB). Like
`catalog`, it is opened with a `mode=ro` URI and is never written by anything,
ever.

It answers one question: what does this track sound like, in the seven numbers
Spotify measured. `track_id` is the key — the same Spotify id the library
already stores on every imported row — and the mirror carries a unique index on
it, so 723 lookups are half a second even though the table is 100 M rows.

Nothing here interprets the numbers. Deciding that valence 0.7 with energy 0.8
is "bright" belongs to `library.features`, next to the database that stores it.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TrackFeatures:
    """What the mirror measured. Every field is nullable: the mirror stores
    rows for tracks Spotify answered `null` for, and those are a real answer —
    "asked, and there is nothing" — rather than a missing row.

    `speechiness`, `liveness`, `key`, and `time_signature` are unused by
    slice 1's mood bands — only the embed batch's 13-dim feature_vec (ticket
    01) reads them — but they come from the mirror alongside everything else,
    so they are carried here rather than fetched a second way."""

    track_id: str
    valence: float | None
    energy: float | None
    danceability: float | None
    acousticness: float | None
    instrumentalness: float | None
    tempo: float | None
    loudness: float | None
    mode: int | None
    speechiness: float | None = None
    liveness: float | None = None
    key: int | None = None
    time_signature: int | None = None


class FeatureLookup(Protocol):
    def features(self, track_ids: list[str]) -> dict[str, TrackFeatures]:
        """Features for the given Spotify ids. Missing ids are simply absent."""
        ...


class EmptyFeatures:
    """Stands in when the mirror is not on this machine.

    The dashboard degrades to what the library alone knows rather than failing:
    principle 4, the same reason the catalog is optional at startup.
    """

    def features(self, track_ids: list[str]) -> dict[str, TrackFeatures]:
        return {}
