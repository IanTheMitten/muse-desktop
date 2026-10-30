"""The download pipeline: resolve -> download -> tag -> lyrics -> art ->
index. See docs/superpowers/specs/2026-09-03-download-ingest-pipeline-design.md
for the design this implements.

Three Protocols, same seam pattern as art.py's ArtProvider: real
implementations shell out to the yt-dlp binary (`muse/ytdlp.py`) and use
mutagen; they are constructed lazily and tests use fakes that never touch
the network.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

from . import ytdlp
from .remux import NO_M4A, remux_m4a

CATALOG = "catalog"
MUSICBRAINZ = "musicbrainz"
YTDLP = "ytdlp"

ALREADY_IN_DOWNLOADS = "Already in downloads"

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


class YtDlpAudioSource:
    """The real thing: the yt-dlp binary, remuxed to a clean m4a."""

    UPDATE_RETRY_GAP_S = 3600

    def __init__(self, binary: Path):
        self._binary = binary
        self._updated_at = float("-inf")

    def _download(self, query: str, out_dir: Path, base_name: str) -> dict:
        try:
            return ytdlp.download(self._binary, query, out_dir, base_name)
        except RuntimeError as error:
            # A failure is often a stale binary (YouTube changed): update and
            # retry once, but not on every failed track of a batch.
            if (str(error) == ytdlp.MISSING
                    or time.monotonic() - self._updated_at < self.UPDATE_RETRY_GAP_S):
                raise
            self._updated_at = time.monotonic()
            if not ytdlp.update(self._binary):
                raise
            return ytdlp.download(self._binary, query, out_dir, base_name)

    def fetch(self, *, query: str, out_dir: Path, base_name: str) -> AudioResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        info = self._download(query, out_dir, base_name)
        path = out_dir / f"{base_name}.m4a"
        if not path.exists():
            raise RuntimeError(NO_M4A)
        remux_m4a(path)
        return AudioResult(
            path, str(info["id"]) if info.get("id") else None,
            float(d) if (d := info.get("duration")) else None,
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
    """Ported from music_downloader.py's embed_m4a_tags."""
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


from .lyrics_lrclib import SyncOutcome, _apply, decide_outcome, search_lrclib


def fetch_lyrics(track: dict) -> SyncOutcome:
    """The lyrics chain for one freshly-downloaded track: LRCLIB (synced
    preferred, PRD story 91), else not_found. Returns a SyncOutcome so the
    caller can apply it through lyrics_lrclib._apply.

    Never returns state='manual' -- that state is reserved for a
    listener's own hand-fix (muse/library/lyrics_state.py).
    """
    results = search_lrclib(track["track_name"], track["artist_name"],
                              track.get("album_name", ""))
    return decide_outcome(track, results)


from .library import art as library_art
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
    music_root: Path,
    lyrics_root: Path,
    art_root: Path,
    art_provider,
    embed_backfill,
    under_lock=None,
) -> DownloadResult:
    """One track, start to finish. Audio is staged in `music_root/.incoming`
    and moved to its final path only after the row is written, all under the
    scan lock, so the scanner never sees a file without its row. Fetching,
    lyrics lookup and art lookup run outside the lock.
    """
    lock = under_lock or (lambda fn: fn())
    track_rowid = payload.get("track_rowid")

    def _existing():
        return library_tracks.find_done(
            conn, track_rowid=track_rowid,
            source_site_id=payload.get("source_site_id"),
            isrc=payload.get("isrc"),
            recording_mbid=payload.get("recording_mbid"),
            artist_name=payload.get("artist_name"),
            track_name=payload.get("track_name"),
            duration_ms=payload.get("duration_ms"),
        )

    existing = lock(_existing)
    if existing is not None:
        return DownloadResult(status="done", message="already in library",
                                track_rowid=existing["track_rowid"])

    # Step 1/2: resolve (may probe the network, so outside the lock) + metadata.
    metadata = metadata_provider.resolve(payload)
    if metadata is None:
        return DownloadResult(status="error", message="could not resolve metadata")

    origin = "catalog" if track_rowid is not None else "local"

    def _claim_and_write():
        # Allocate and write in one lock call so a waiting scan cannot take the rowid.
        rowid = track_rowid or library_tracks.next_local_rowid(conn)
        meta = metadata
        # Plan Assumption 1: a done/missing_file holder of this Spotify id means
        # "already in library"; a local error holder is a failed attempt, so
        # claim_track_id NULLs its track_id and this attempt takes it. A manifest
        # error holder keeps its id (the importer rewrites it each startup), so
        # this attempt downloads without the stamp.
        if meta.track_id is not None:
            holder = library_tracks.claim_track_id(conn, meta.track_id)
            if holder is not None and holder["track_rowid"] != rowid:
                if holder["status"] != "error":
                    return DownloadResult(status="done", message="already in library",
                                          track_rowid=holder["track_rowid"]), meta, None, rowid
                meta = replace(meta, track_id=None)

        # A post-freeze album has no catalog album_rowid, and a null one is not
        # survivable: queries.albums() filters it out of the gallery and
        # queries.album() addresses an album by rowid. Allocated against the
        # release so every track of one record shares one album.
        album = meta.album_rowid
        if album is None and meta.release_mbid is not None:
            album = library_tracks.album_rowid_for_release(conn, meta.release_mbid)

        # The tracks row is written now, before download -- a kill mid-download
        # leaves a diagnosable status='error' row instead of an orphaned file
        # and no row at all. `album_rowid` and `release_mbid` go in together:
        # the allocator's reuse query is `release_mbid = ? AND album_rowid IS
        # NOT NULL`, so a row carrying the mbid with a null album would be
        # invisible to it and the next track would allocate a second album.
        library_tracks.write_pending(
            conn, track_rowid=rowid, origin=origin,
            metadata_tier=meta.metadata_tier,
            artist_name=meta.artist_name, artist_names=meta.artist_names,
            album_name=meta.album_name, track_name=meta.track_name,
            source_site_id=payload.get("source_site_id"), isrc=meta.isrc,
            track_id=meta.track_id, album_rowid=album,
            artist_rowid=meta.artist_rowid, track_no=meta.track_no,
            disc_no=meta.disc_no, duration_ms=meta.duration_ms,
            recording_mbid=meta.recording_mbid,
            release_mbid=meta.release_mbid,
        )
        return None, meta, album, rowid

    early, metadata, album_rowid, resolved_rowid = lock(_claim_and_write)
    if early is not None:
        return early

    def _fail(message: str) -> DownloadResult:
        lock(lambda: library_tracks.mark_error(conn, resolved_rowid, message))
        return DownloadResult(status="error", message=message, track_rowid=resolved_rowid)

    # Step 3: download into staging.
    incoming = music_root / ".incoming"
    base_name = str(resolved_rowid)

    def _unstage():
        for leftover in incoming.glob(f"{base_name}.*"):
            try:
                leftover.unlink()
            except OSError:
                pass

    query = payload.get("url") or f"ytsearch1:{metadata.artist_name} - {metadata.track_name}"
    try:
        audio = audio_source.fetch(query=query, out_dir=incoming, base_name=base_name)
    except Exception as error:
        _unstage()
        return _fail(str(error))

    # Step 3b: is this the recording that was asked for?
    #
    # `ytsearch1:Artist - Title` answers with the whole record whenever a
    # title track shares the album's name, and nothing downstream noticed:
    # 52 minutes of audio landed in the library as a 20-second track.
    # The staged file goes before the row is marked: a retry left staring at
    # the wrong audio would otherwise accept it a second time.
    if _length_mismatch(metadata.duration_ms, audio.duration_s):
        _unstage()
        return _fail(
            f"downloaded {audio.duration_s:.0f}s of audio for a"
            f" {metadata.duration_ms / 1000:.0f}s track -- the top result was"
            " probably not this recording; queue it again with an explicit"
            ' url ({"kind": "url", ...}) to say which one to take'
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

    # Step 5: lyrics lookup. A failure here (provider outage) must not fail
    # the job -- the track is playable and indexed either way.
    final = (
        music_root.resolve() / safe_component(metadata.artist_name)
        / safe_component(metadata.album_name or "Singles")
        / f"{(metadata.track_no or 0):02d} - {safe_component(metadata.track_name)}"
          f"{audio.file_path.suffix}"
    )
    track_dict = {
        "track_rowid": resolved_rowid, "track_name": metadata.track_name,
        "artist_name": metadata.artist_name, "album_name": metadata.album_name,
        "duration_ms": metadata.duration_ms, "is_instrumental": False,
    }
    try:
        outcome = fetch_lyrics(track_dict)
    except Exception:
        outcome = None

    # Step 6: art lookup. Same failure isolation. `art_provider` is the
    # already-composed chain -- exactly one `.cover()` call.
    art_path = None
    cover = None
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
            art_path = str(cache_cover(cover, album_ref, art_root))
    except Exception:
        cover = None

    def _land() -> DownloadResult | None:
        if final.exists():
            audio.file_path.unlink(missing_ok=True)
            library_tracks.mark_error(conn, resolved_rowid, ALREADY_IN_DOWNLOADS)
            return DownloadResult(status="error", message=ALREADY_IN_DOWNLOADS,
                                  track_rowid=resolved_rowid)
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(audio.file_path, final)
        st = final.stat()
        library_tracks.mark_done(
            conn, resolved_rowid, file_path=str(final), lyrics_path=None,
            art_path=art_path if album_rowid is None else None,
            source_site_id=audio.source_site_id,
            file_size=st.st_size, file_mtime=int(st.st_mtime),
        )
        if outcome is not None:
            try:
                _apply(conn, {**track_dict, "file_path": str(final)}, outcome,
                       checked_at=int(time.time()), lyrics_root=lyrics_root)
            except Exception:
                pass
        if cover is not None and art_path is not None and album_rowid is not None:
            try:
                library_art.record_cover(
                    conn, album_rowid, art_path, source=cover.source, mbid=cover.mbid
                )
            except Exception:
                pass
        return None

    landed = lock(_land)
    if landed is not None:
        return landed
    embed_backfill()
    return DownloadResult(status="done", message="ok", track_rowid=resolved_rowid)
