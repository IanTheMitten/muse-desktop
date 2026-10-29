"""The download pipeline: resolve -> download -> tag -> lyrics -> art ->
index. See docs/superpowers/specs/2026-09-03-download-ingest-pipeline-design.md
for the design this implements.

Three Protocols, same seam pattern as art.py's ArtProvider: real
implementations need yt-dlp/mutagen/lyricsgenius (the `ingest` extra) and
are constructed lazily; tests use fakes and never touch the network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

CATALOG = "catalog"
MUSICBRAINZ = "musicbrainz"
YTDLP = "ytdlp"

INVALID_FS_CHARS = r'[\\/:*?"<>|\x00-\x1F]'


def safe_component(text: str) -> str:
    """Same sanitizer music_downloader.py already uses for path components."""
    text = re.sub(INVALID_FS_CHARS, "_", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.rstrip(". ")


@dataclass(frozen=True, slots=True)
class ResolvedMetadata:
    artist_name: str
    artist_names: list[str]
    album_name: str
    track_name: str
    metadata_tier: str
    track_no: int | None = None
    disc_no: int | None = None
    total_tracks: int | None = None
    duration_ms: int | None = None
    isrc: str | None = None
    track_id: str | None = None
    album_rowid: int | None = None
    artist_rowid: int | None = None
    language: str | None = None
    recording_mbid: str | None = None
    release_mbid: str | None = None


class MetadataProvider(Protocol):
    def resolve(self, payload: dict) -> ResolvedMetadata | None:
        """`payload` is the job's payload: `{track_rowid}`, or
        `{recording_mbid, release_mbid, artist_name, album_name,
        track_name, ...}`, or `{artist_name, track_name}`, or
        `{url, artist_name, track_name}`.
        None when no tier can answer at all."""
        ...


class TieredMetadataProvider:
    """Catalog (no I/O) -> MusicBrainz (no I/O -- answers from a payload the
    browse screen already fetched) -> yt-dlp `info` dict, last resort.
    `self._mb` itself is rate-limited, but `resolve()` never calls it: this
    tier's metadata answer comes entirely from the payload. `self._mb` is
    reserved for the catalog->MB->language chain (Task 9's lyrics-gate
    language lookup), a separate, rate-limited use of the same client.
    Whichever tier answers is recorded on the result's `metadata_tier` —
    never silently merged as if uniformly catalog-quality (PRD story 111)."""

    def __init__(self, catalog, mb, ytdlp_info):
        self._catalog = catalog
        self._mb = mb
        self._ytdlp_info = ytdlp_info

    def resolve(self, payload: dict) -> ResolvedMetadata | None:
        track_rowid = payload.get("track_rowid")
        if track_rowid is not None:
            facts = self._catalog.track_facts([track_rowid])
            fact = facts.get(track_rowid)
            if fact is not None:
                return ResolvedMetadata(
                    artist_name=payload["artist_name"],
                    artist_names=payload.get("artist_names", [payload["artist_name"]]),
                    album_name=payload["album_name"],
                    track_name=payload["track_name"],
                    metadata_tier=CATALOG,
                    track_no=fact.track_no,
                    disc_no=fact.disc_no,
                    duration_ms=fact.duration_ms,
                    isrc=fact.isrc,
                    track_id=payload.get("track_id"),
                    album_rowid=fact.album_rowid,
                    artist_rowid=payload.get("artist_rowid"),
                )

        # A MusicBrainz-discovered track: the browse screen already fetched
        # this release in order to render it, so every fact is on the payload
        # and this tier answers with no I/O. Resolving here instead would
        # re-ask for one release once per track -- 15 rate-limited lookups
        # for a 15-track album -- because the client has no disk cache.
        recording_mbid = payload.get("recording_mbid")
        if recording_mbid is not None:
            artist_name = payload["artist_name"]
            return ResolvedMetadata(
                artist_name=artist_name,
                artist_names=payload.get("artist_names", [artist_name]),
                album_name=payload["album_name"],
                track_name=payload["track_name"],
                metadata_tier=MUSICBRAINZ,
                track_no=payload.get("track_no"),
                disc_no=payload.get("disc_no"),
                total_tracks=payload.get("total_tracks"),
                duration_ms=payload.get("duration_ms"),
                isrc=payload.get("isrc"),
                track_id=payload.get("track_id"),
                recording_mbid=recording_mbid,
                release_mbid=payload.get("release_mbid"),
            )

        # Beyond the branch above, MusicBrainz needs an ISRC to search by,
        # which nothing in a free-text/URL payload provides ahead of a
        # download -- so this tier otherwise only ever answers on the
        # catalog->MB->language chain (Task 9's language lookup for the
        # lyrics gate), not as a metadata source for a track the catalog has
        # never heard of and the browse screen never discovered.

        if self._ytdlp_info is not None:
            info = self._ytdlp_info(
                payload.get("url")
                or f"ytsearch1:{payload['artist_name']} - {payload['track_name']}"
            )
            if info:
                duration = info.get("duration")
                return ResolvedMetadata(
                    artist_name=payload.get("artist_name") or info.get("uploader", ""),
                    artist_names=[payload.get("artist_name") or info.get("uploader", "")],
                    album_name=payload.get("album_name", "Singles"),
                    track_name=payload.get("track_name") or info.get("title", ""),
                    metadata_tier=YTDLP,
                    duration_ms=int(duration * 1000) if duration else None,
                )
        return None


@dataclass(frozen=True, slots=True)
class AudioResult:
    file_path: Path
    source_site_id: str | None
    # How long the audio that arrived actually is, in seconds, when the
    # source can say. `run_download` compares it against the length the
    # metadata tier gave -- see `_length_mismatch`. `None` means the source
    # does not know, which is not the same as a mismatch and is never a
    # reason to refuse a download.
    duration_s: float | None = None


# How far a download may run over or under the length the metadata tier
# gave before it is treated as a different recording altogether. Real uploads
# routinely run long: on the record that exposed this, one song arrived 25 s
# past MusicBrainz's length and another 17 s, both correct. The floor is what
# makes the percentage safe on a short track -- 20% of a 20-second entry is
# four seconds, which no lead-in would survive.
LENGTH_TOLERANCE_FRACTION = 0.20
LENGTH_TOLERANCE_FLOOR_S = 30.0


def _length_mismatch(expected_ms: int | None, actual_s: float | None) -> bool:
    """Whether what arrived is a different recording from what was asked for.

    Unknown on either side is never a mismatch. MusicBrainz frequently has no
    length, and an explicit `url` job can point at a source yt-dlp reports
    none for -- and that job is the escape hatch a refusal tells people to
    use, so refusing on silence would close the only door out.
    """
    if not expected_ms or not actual_s:
        return False
    expected_s = expected_ms / 1000
    allowed = max(LENGTH_TOLERANCE_FLOOR_S, expected_s * LENGTH_TOLERANCE_FRACTION)
    return abs(actual_s - expected_s) > allowed


class AudioSource(Protocol):
    def fetch(self, *, query: str, out_dir: Path, base_name: str) -> AudioResult:
        """`query` is an explicit URL or a `ytsearch1:` query. Raises on
        total failure (no result at all) -- the caller decides that's a
        job-level `error`, same as any other step-3 failure."""
        ...


try:
    from yt_dlp import YoutubeDL
except ImportError:  # pragma: no cover — exercised only with the `ingest` extra
    YoutubeDL = None


class YtDlpAudioSource:
    """The real thing. Requires the `ingest` extra."""

    def fetch(self, *, query: str, out_dir: Path, base_name: str) -> AudioResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        outtmpl = str(out_dir / f"{base_name}.%(ext)s")
        final_path = out_dir / f"{base_name}.m4a"
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "overwrites": False,
            "windowsfilenames": True,
            "restrictfilenames": False,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "m4a",
                 "preferredquality": "0"},
            ],
        }
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=True)
        picked = (
            info["entries"][0]
            if isinstance(info, dict) and "entries" in info and info["entries"]
            else info or {}
        )
        if not final_path.exists():
            raise FileNotFoundError(f"Expected output not found: {final_path}")
        source_site_id = (
            str(picked.get("id")) if isinstance(picked, dict) and picked.get("id")
            else None
        )
        duration = picked.get("duration") if isinstance(picked, dict) else None
        return AudioResult(
            file_path=final_path, source_site_id=source_site_id,
            duration_s=float(duration) if duration else None,
        )


def tag_m4a(
    audio_path: Path,
    *,
    artist_name: str,
    album_name: str,
    track_title: str,
    track_id: str | None,
    track_no: int | None,
    total_tracks: int | None,
    disc_no: int | None,
) -> None:
    """Ported from music_downloader.py's embed_m4a_tags. Requires the
    `ingest` extra (mutagen)."""
    from mutagen.mp4 import MP4

    mp4 = MP4(audio_path)
    mp4["\xa9nam"] = [track_title]
    mp4["\xa9alb"] = [album_name]
    mp4["\xa9ART"] = [artist_name]
    if track_id:
        mp4["\xa9cmt"] = [f"track_id={track_id}"]
    mp4["trkn"] = [(track_no or 1, total_tracks or 0)]
    if disc_no is not None:
        mp4["disk"] = [(disc_no, 0)]
    mp4.save()


import lyrics_quality
from .lyrics_lrclib import SyncOutcome, decide_outcome, search_lrclib

try:
    import lyricsgenius
except ImportError:  # pragma: no cover — exercised only with the `ingest` extra
    lyricsgenius = None

import os

GENIUS_TOKEN = os.environ.get("GENIUS_TOKEN")


def _genius_lyrics(track: dict, genius_client) -> str | None:
    """Ported from music_downloader.py's fetch_lyrics_from_genius: one
    direct search-and-fetch attempt. Returns validated plain text, or None
    -- inert when genius_client is None (no GENIUS_TOKEN), same as today.
    """
    if genius_client is None:
        return None
    song = genius_client.search_song(track["track_name"], track["artist_name"])
    if song is None:
        return None
    lyrics = getattr(song, "lyrics", None)
    if not lyrics:
        return None
    lyrics = lyrics_quality.clean_genius(lyrics, getattr(song, "title", track["track_name"]))
    ok, _why = lyrics_quality.validate(lyrics, track["track_name"])
    return lyrics if ok else None


def build_genius_client():
    """None when GENIUS_TOKEN is unset -- the chain is then LRCLIB-only,
    same as music_downloader.py's own inert-without-token behavior."""
    if not GENIUS_TOKEN or lyricsgenius is None:
        return None
    return lyricsgenius.Genius(
        GENIUS_TOKEN, skip_non_songs=True,
        excluded_terms=["(Live)", "(Remix)", "(Edit)"],
        remove_section_headers=False,
    )


def fetch_lyrics(track: dict, *, genius_client) -> SyncOutcome:
    """The lyrics chain for one freshly-downloaded track: LRCLIB (synced
    preferred, PRD story 91) -> Genius -> not_found. Returns a SyncOutcome
    so the caller (run_download, Task 10) can apply it through
    lyrics_lrclib._apply -- one write path for every tier, not a
    LRCLIB-specific one plus separate ad hoc writes for Genius.

    Never returns state='manual' -- that state is reserved for a
    listener's own hand-fix (muse/library/lyrics_state.py). A miss here is
    state='not_found', identical to a plain LRCLIB miss, and lands on the
    same lyrics worklist either way.
    """
    results = search_lrclib(track["track_name"], track["artist_name"],
                              track.get("album_name", ""))
    outcome = decide_outcome(track, results)
    if outcome.state != "not_found":
        return outcome

    text = _genius_lyrics(track, genius_client)
    if text:
        return SyncOutcome(state="plain", synced=False, lyrics_text=text)

    return SyncOutcome(state="not_found", synced=False)


from .library import tracks as library_tracks


@dataclass(frozen=True, slots=True)
class DownloadResult:
    status: str  # 'done' | 'error'
    message: str
    track_rowid: int | None = None


def run_download(
    conn,
    *,
    payload: dict,
    metadata_provider: MetadataProvider,
    audio_source: AudioSource,
    genius_client,
    music_root: Path,
    art_root: Path,
    art_provider,
    embed_backfill,
) -> DownloadResult:
    """One track, start to finish. See the design doc's "The pipeline"
    section -- this function is steps 1-6 plus indexing, in order.
    """
    track_rowid = payload.get("track_rowid")
    existing = library_tracks.find_done(
        conn, track_rowid=track_rowid,
        source_site_id=payload.get("source_site_id"),
        isrc=payload.get("isrc"),
        recording_mbid=payload.get("recording_mbid"),
        artist_name=payload.get("artist_name"),
        track_name=payload.get("track_name"),
        duration_ms=payload.get("duration_ms"),
    )
    if existing is not None:
        return DownloadResult(status="done", message="already in library",
                                track_rowid=existing["track_rowid"])

    # Step 1/2: resolve + metadata.
    metadata = metadata_provider.resolve(payload)
    if metadata is None:
        return DownloadResult(status="error", message="could not resolve metadata")

    resolved_rowid = track_rowid or library_tracks.next_local_rowid(conn)
    origin = "catalog" if track_rowid is not None else "local"

    # Plan Assumption 1: a done/missing_file holder of this Spotify id means
    # "already in library"; a local error holder is a failed attempt, so
    # claim_track_id NULLs its track_id and this attempt takes it. A manifest
    # error holder keeps its id (the importer rewrites it each startup), so
    # this attempt downloads without the stamp.
    if metadata.track_id is not None:
        holder = library_tracks.claim_track_id(conn, metadata.track_id)
        if holder is not None and holder["track_rowid"] != resolved_rowid:
            if holder["status"] != "error":
                return DownloadResult(status="done", message="already in library",
                                      track_rowid=holder["track_rowid"])
            metadata = replace(metadata, track_id=None)

    # A post-freeze album has no catalog album_rowid, and a null one is not
    # survivable: queries.albums() filters it out of the gallery and
    # queries.album() addresses an album by rowid. Allocated against the
    # release so every track of one record shares one album.
    album_rowid = metadata.album_rowid
    if album_rowid is None and metadata.release_mbid is not None:
        album_rowid = library_tracks.album_rowid_for_release(
            conn, metadata.release_mbid
        )

    # The tracks row is written now, before download -- a kill mid-download
    # leaves a diagnosable status='error' row instead of an orphaned file
    # and no row at all (design doc: "written at the start of the job").
    # `album_rowid` and `release_mbid` go in together, in this one write: the
    # allocator's reuse query is `release_mbid = ? AND album_rowid IS NOT
    # NULL`, so a row carrying the mbid with a null album would be invisible
    # to it and the next track of the release would allocate a second album.
    library_tracks.write_pending(
        conn, track_rowid=resolved_rowid, origin=origin,
        metadata_tier=metadata.metadata_tier,
        artist_name=metadata.artist_name, artist_names=metadata.artist_names,
        album_name=metadata.album_name, track_name=metadata.track_name,
        source_site_id=payload.get("source_site_id"), isrc=metadata.isrc,
        track_id=metadata.track_id, album_rowid=album_rowid,
        artist_rowid=metadata.artist_rowid, track_no=metadata.track_no,
        disc_no=metadata.disc_no, duration_ms=metadata.duration_ms,
        recording_mbid=metadata.recording_mbid,
        release_mbid=metadata.release_mbid,
    )

    # Step 3: download.
    artist_dir = music_root / safe_component(metadata.artist_name)
    album_dir = artist_dir / safe_component(metadata.album_name or "Singles")
    base_name = f"{(metadata.track_no or 0):02d} - {safe_component(metadata.track_name)}"
    query = payload.get("url") or f"ytsearch1:{metadata.artist_name} - {metadata.track_name}"
    try:
        audio = audio_source.fetch(query=query, out_dir=album_dir, base_name=base_name)
    except Exception as error:
        library_tracks.mark_error(conn, resolved_rowid, str(error))
        return DownloadResult(status="error", message=str(error), track_rowid=resolved_rowid)

    # Step 3b: is this the recording that was asked for?
    #
    # `ytsearch1:Artist - Title` answers with the whole record whenever a
    # title track shares the album's name, and nothing downstream noticed:
    # queueing "you seem pretty sad for a girl so in love" wrote 52 minutes of
    # audio into the library as a 20-second track -- tagged, indexed, and
    # embedded, so CLAP described the wrong 52 minutes to the recommender.
    #
    # The file goes before the row is marked, not after and not never: yt-dlp
    # runs with `overwrites: False`, so a retry left staring at the wrong 52
    # minutes would skip the download and accept them a second time.
    if _length_mismatch(metadata.duration_ms, audio.duration_s):
        try:
            audio.file_path.unlink()
        except OSError:
            pass
        message = (
            f"downloaded {audio.duration_s:.0f}s of audio for a"
            f" {metadata.duration_ms / 1000:.0f}s track -- the top result was"
            " probably not this recording; queue it again with an explicit"
            ' url ({"kind": "url", ...}) to say which one to take'
        )
        library_tracks.mark_error(conn, resolved_rowid, message)
        return DownloadResult(
            status="error", message=message, track_rowid=resolved_rowid
        )

    # Step 4: tag.
    try:
        tag_m4a(
            audio.file_path, artist_name=metadata.artist_name,
            album_name=metadata.album_name, track_title=metadata.track_name,
            track_id=metadata.track_id, track_no=metadata.track_no,
            total_tracks=metadata.total_tracks, disc_no=metadata.disc_no,
        )
    except Exception:
        pass  # Tagging failure doesn't invalidate audio that already downloaded.

    # Step 5: lyrics. A failure here (provider outage) must not fail the
    # whole job -- the track is playable and indexed either way.
    lyrics_path = None
    try:
        track_dict = {
            "track_rowid": resolved_rowid, "track_name": metadata.track_name,
            "artist_name": metadata.artist_name, "album_name": metadata.album_name,
            "duration_ms": metadata.duration_ms, "is_instrumental": False,
            "file_path": str(audio.file_path),
        }
        outcome = fetch_lyrics(track_dict, genius_client=genius_client)
        from .lyrics_lrclib import _apply
        _apply(conn, track_dict, outcome, checked_at=int(__import__("time").time()))
        if outcome.lyrics_text is not None:
            lyrics_path = str(audio.file_path).rsplit(".", 1)[0] + ".lyrics.txt"
    except Exception:
        pass

    # Step 6: art. Same failure isolation as lyrics. `art_provider` is the
    # already-composed chain (ChainedArtProvider: CoverArtArchive ->
    # YtdlpThumbnailArt -> NoArt in production, Task 12) -- run_download
    # makes exactly one `.cover()` call, same as art_backfill does for one
    # album, and never touches an individual tier itself.
    art_path = None
    try:
        from .art import AlbumRef, cache_cover
        album_ref = AlbumRef(
            album_rowid=album_rowid or resolved_rowid,
            album_name=metadata.album_name, artist_name=metadata.artist_name,
            isrcs=(metadata.isrc,) if metadata.isrc else (),
            release_mbid=metadata.release_mbid,
            tracks=((metadata.track_name, metadata.duration_ms),),
        )
        cover = art_provider.cover(album_ref)
        if cover is not None:
            path = cache_cover(cover, album_ref, art_root)
            art_path = str(path)
    except Exception:
        pass

    library_tracks.mark_done(
        conn, resolved_rowid, file_path=str(audio.file_path),
        lyrics_path=lyrics_path, art_path=art_path,
        source_site_id=audio.source_site_id,
    )
    embed_backfill()
    return DownloadResult(status="done", message="ok", track_rowid=resolved_rowid)
