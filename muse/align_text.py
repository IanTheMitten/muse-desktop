"""Pure alignment helpers: no torch, shared by the worker and the sidecar.

Line timing only; words are what the aligners are fed, not what is stored.
"""

import math
import re

SCHUFO = "schufo"
MMS = "mms"

_ISO3 = {
    "en": "eng", "ko": "kor", "ja": "jpn", "zh": "zho", "es": "spa",
    "fr": "fra", "de": "deu", "pt": "por", "it": "ita", "ru": "rus",
    "th": "tha", "vi": "vie", "id": "ind", "tl": "tgl",
}


def lyric_lines(text: str) -> list[str]:
    return [s for s in (l.strip() for l in text.splitlines()) if s]


def words(line: str) -> list[str]:
    line = re.sub(r"\[[^\]]*\]", "", line).lower().replace("’", "'")
    line = "".join(c for c in line if c.isalnum() or c in "' ")
    return [t for t in line.split() if any(c.isalpha() for c in t)]


def route(text: str, lang: str) -> str:
    letters = [c for c in text if c.isalpha()]
    if lang == "en" and letters and sum(c.isascii() for c in letters) / len(letters) >= 0.9:
        return SCHUFO
    return MMS


def iso3(lang: str) -> str | None:
    return _ISO3.get(lang)


def fill_starts(raw: list[float | None]) -> list[int] | None:
    n = len(raw)
    if not n or sum(s is not None for s in raw) < math.ceil(n / 2):
        return None
    ms = [None if s is None else round(s * 1000) for s in raw]
    nxt = None
    for i in range(n - 1, -1, -1):
        if ms[i] is None:
            ms[i] = nxt
        else:
            nxt = ms[i]
    prev = None
    for i in range(n):
        if ms[i] is None:
            ms[i] = prev
        else:
            prev = ms[i]
    top = 0
    for i, v in enumerate(ms):
        top = ms[i] = max(top, v)
    if len(set(ms)) < max(min(3, n), math.ceil(n / 2)):
        return None
    return ms


def lrc_stamp(ms: int) -> str:
    m, rem = divmod(ms, 60000)
    s, rem = divmod(rem, 1000)
    return f"[{m:02d}:{s:02d}.{rem // 10:02d}]"


def build_lrc(lines: list[str], starts_ms: list[int], track: dict) -> str:
    header = []
    if track.get("artist_name"):
        header.append(f"[ar:{track['artist_name']}]")
    if track.get("track_name"):
        header.append(f"[ti:{track['track_name']}]")
    if track.get("album_name"):
        header.append(f"[al:{track['album_name']}]")
    body = [lrc_stamp(ms) + line for ms, line in zip(starts_ms, lines)]
    return "\n".join([*header, *body]) + "\n"
