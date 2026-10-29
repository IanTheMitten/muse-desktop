"""LRC format primitives, shared by the writer (`muse/lyrics_lrclib.py`) and
the reader (`muse/api.py`'s synced-lyrics endpoint).

One `.lrc` file, two consumers with opposite needs: the writer strips
timestamps to derive `.lyrics.txt`'s embedded text (O10 -- "the writer
derives `.lyrics.txt` from the `.lrc`... so they cannot drift"), and the
reader keeps them to drive the synced-lyrics view's active-line highlight
(stories 60-63). Both start from the same tag grammar, so it lives in one
place rather than two regexes drifting apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A single `[mm:ss.xx]` or `[mm:ss]` timing tag. Matched with `sub`+`count=1`
# in a loop rather than a single `^(...)*` pattern because a handful of real
# lines carry two tags for one lyric (a repeated chorus line LRCLIB times
# once and tags twice) -- one pass would leave the second tag in the text.
_TAG_RE = re.compile(r"^\[(\d{1,3}):(\d{2})(?:\.(\d{1,3}))?\]\s*")

# A metadata line (`[ar:...]`, `[ti:...]`, `[offset:...]`, ...) rather than a
# timed lyric. LRCLIB's own response never includes one, but a `.lrc` this
# app wrote itself does (`build_lrc`'s `[ar:]`/`[ti:]`/`[al:]` header), and a
# hand-pasted file (O11's future paste-in path) might carry more.
_METADATA_RE = re.compile(r"^\[[A-Za-z]+:[^\]]*\]\s*$")


def _tag_ms(minutes: str, seconds: str, fraction: str | None) -> int:
    frac = (fraction or "0").ljust(3, "0")[:3]
    return (int(minutes) * 60 + int(seconds)) * 1000 + int(frac)


@dataclass(frozen=True, slots=True)
class LrcLine:
    ms: int
    text: str


def parse_lrc(text: str) -> list[LrcLine]:
    """`.lrc` text -> timed lines, sorted by timestamp.

    A line can carry more than one tag (a shared chorus timestamp); each tag
    produces its own `LrcLine` with the same text, since that is what the
    synced view's binary search needs -- one timestamp, one text, no special
    case for "this text applies to two positions in the song".

    A line with no recognisable tag at all is dropped rather than kept at
    position 0: an untimed stray line rendered as "playing since the first
    second" is a worse answer than not showing it, and real `.lrc` files
    from LRCLIB do not have any.
    """
    lines: list[LrcLine] = []
    for raw_line in text.splitlines():
        if _METADATA_RE.match(raw_line):
            continue
        remainder = raw_line
        tags: list[int] = []
        match = _TAG_RE.match(remainder)
        while match:
            tags.append(_tag_ms(*match.groups()))
            remainder = _TAG_RE.sub("", remainder, count=1)
            match = _TAG_RE.match(remainder)
        body = remainder.strip()
        for ms in tags:
            lines.append(LrcLine(ms=ms, text=body))
    lines.sort(key=lambda line: line.ms)
    return lines


def strip_timestamps(text: str) -> str:
    """`.lrc` -> plain text: drop every tag and metadata line, join what's
    left. This is the writer's derivation (O10) -- kept here so the writer
    and the reader agree on what counts as a tag without importing from each
    other's module.
    """
    out = []
    for raw_line in text.splitlines():
        if _METADATA_RE.match(raw_line):
            continue
        line = raw_line
        while _TAG_RE.match(line):
            line = _TAG_RE.sub("", line, count=1)
        line = line.strip()
        if line:
            out.append(line)
    return "\n".join(out)
