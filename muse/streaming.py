"""Range requests, for seeking without re-downloading.

A media element does not fetch a file and play it. It asks for a byte range,
starts playing, and asks for more — and when the listener drags the scrubber it
asks for a range from the middle. Answer that with the whole file every time and
every seek re-downloads the track.

Mobile Safari is the specific thing being satisfied here. It is stricter than
most clients about `Content-Range` and about getting a `206` when it asked for
one, and a response that satisfies a test client can still fail in the browser.
So the parsing is pure and tested, and the behaviour it feeds is verified by
hand on the phone.

Audio is never transcoded: bytes go out exactly as stored.
"""

import re
from dataclasses import dataclass
from pathlib import Path

CHUNK_SIZE = 64 * 1024

MEDIA_TYPES = {
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
}


def media_type_for(path: Path) -> str:
    return MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")

# `bytes=0-1023`, `bytes=1024-`, `bytes=-500`. One range only: multi-range
# responses need multipart/byteranges, no media element asks for them, and
# guessing wrong is worse than ignoring the header.
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class Unsatisfiable(Exception):
    """The range asked for lies outside the file. Answer 416."""


@dataclass(frozen=True, slots=True)
class ByteRange:
    start: int
    end: int  # inclusive, as HTTP counts it

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    def content_range(self, size: int) -> str:
        return f"bytes {self.start}-{self.end}/{size}"


def parse_range(header: str | None, size: int) -> ByteRange | None:
    """The range a client asked for, or None to send the whole file.

    Raises `Unsatisfiable` when the request names bytes the file does not have.
    """
    if not header:
        return None

    match = _RANGE_RE.match(header.strip())
    if not match:
        # Malformed or multi-range. RFC 7233 says ignore it and send 200,
        # which is also the safest thing for a player.
        return None

    raw_start, raw_end = match.groups()

    if not raw_start:
        # `bytes=-500` means the *last* 500 bytes. Safari uses this to read the
        # moov atom at the end of an .m4a before it will play anything.
        if not raw_end:
            return None
        suffix = int(raw_end)
        if suffix == 0:
            raise Unsatisfiable
        start = max(0, size - suffix)
        return ByteRange(start, size - 1)

    start = int(raw_start)
    if start >= size:
        raise Unsatisfiable

    end = int(raw_end) if raw_end else size - 1
    # A client may ask past the end; the answer is the rest of the file, not an
    # error. Only a start past the end is unsatisfiable.
    end = min(end, size - 1)
    if end < start:
        raise Unsatisfiable

    return ByteRange(start, end)


def read_range(path: Path, byte_range: ByteRange, chunk_size: int = CHUNK_SIZE):
    """Yield exactly the requested bytes, a chunk at a time.

    Streamed rather than read whole: a request for the last 500 bytes of an
    11 MB file should not read 11 MB, and the phone should start hearing audio
    before the server has finished reading.
    """
    remaining = byte_range.length
    with open(path, "rb") as handle:
        handle.seek(byte_range.start)
        while remaining > 0:
            chunk = handle.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
