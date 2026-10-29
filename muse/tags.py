"""Read-only tag and cover-art extraction via mutagen. Never writes the file."""

from dataclasses import dataclass
from pathlib import Path

import mutagen
from mutagen.wave import WAVE

AUDIO_EXTENSIONS = (".m4a", ".mp3", ".flac", ".wav", ".ogg", ".opus")


@dataclass(frozen=True)
class FileTags:
    title: str | None
    artist: str | None
    album: str | None
    album_artist: str | None
    track_no: int | None
    disc_no: int | None
    isrc: str | None
    duration_ms: int | None
    year: int | None = None


def _year(value: str | None) -> int | None:
    if value is not None and value[:4].isdigit() and 1000 <= int(value[:4]) <= 9999:
        return int(value[:4])
    return None


def _num(value: str | None) -> int | None:
    head = (value or "").split("/")[0].strip()
    return int(head) if head.isdigit() else None


def read_tags(path: Path) -> FileTags | None:
    try:
        f = mutagen.File(path, easy=True)
        if f is None:
            return None
        tags = f.tags or {}
        if isinstance(f, WAVE):
            def get(key):
                frame = tags.get(key)
                return str(frame.text[0]) if frame is not None and frame.text else None
            keys = ("TIT2", "TPE1", "TALB", "TPE2", "TRCK", "TPOS", "TSRC", "TDRC")
        else:
            def get(key):
                v = tags.get(key)
                return str(v[0]) if v else None
            keys = ("title", "artist", "album", "albumartist", "tracknumber", "discnumber", "isrc", "date")
        title, artist, album, album_artist, track, disc, isrc, date = (get(k) for k in keys)
        length = getattr(f.info, "length", None)
        return FileTags(
            title=title, artist=artist, album=album, album_artist=album_artist,
            track_no=_num(track), disc_no=_num(disc), isrc=isrc,
            duration_ms=round(length * 1000) if length is not None else None,
            year=_year(date),
        )
    except Exception:
        return None


def embedded_picture(path: Path) -> tuple[bytes, str] | None:
    try:
        f = mutagen.File(path)
        if f is None or f.tags is None and not getattr(f, "pictures", None):
            return None
        if hasattr(f, "pictures"):  # FLAC
            pic = f.pictures[0]
            data, png = pic.data, pic.mime == "image/png"
        elif hasattr(f.tags, "getall"):  # ID3
            pic = f.tags.getall("APIC")[0]
            data, png = pic.data, pic.mime == "image/png"
        else:  # MP4
            from mutagen.mp4 import MP4Cover
            pic = f.tags["covr"][0]
            data, png = bytes(pic), pic.imageformat == MP4Cover.FORMAT_PNG
        return data, ".png" if png else ".jpg"
    except Exception:
        return None
