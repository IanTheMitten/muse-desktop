#!/usr/bin/env python3
"""Shared cleanup + validation for Genius lyrics.

Genius search results are not always lyrics. The same endpoint happily returns
album tracklists, "artist's songs highest to lowest" stat pages, translation
pages, user playlist logs, and — because Genius also hosts literature — entire
novels. Anything written to a .lyrics.txt file goes through validate() first so
that junk is rejected at the source instead of being discovered later.

Used by music_downloader.py (before first write), clean_lyrics.py (to gate
candidates) and repair_lyrics.py (to audit files already on disk).
"""

from __future__ import annotations

import re

try:
    from langdetect import detect as _lang_detect, DetectorFactory
    from langdetect.lang_detect_exception import LangDetectException
    DetectorFactory.seed = 0  # deterministic results
except ImportError:  # language checks degrade to "unknown", never to a failure
    _lang_detect = None
    LangDetectException = Exception

# A song shorter than this is almost certainly a stub or an error page; longer
# than the max is not a song at all (the longest real lyrics here are ~6k).
MIN_CHARS = 60
MAX_CHARS = 8000

# Phrases that only ever appear on non-lyric Genius pages.
JUNK_PATTERNS = [
    (re.compile(r"Highest To Lowest", re.I),                    "genius stat list"),
    (re.compile(r"Repertorio|Traducidos al Español", re.I), "genius repertoire page"),
    (re.compile(r"All songs will be in order", re.I),           "user playlist log"),
    (re.compile(r"^CHAPTER\s+[IVXLC0-9]", re.M),                "book text"),
    (re.compile(r"\bPrinted and Sold in\b", re.I),              "book text"),
    (re.compile(r"\bThis (?:album|mixtape) (?:is|was) released\b", re.I), "album blurb"),
]

# Genius mirrors most popular songs as per-language translation pages. These
# headers say "<language> lyrics TO <song>", which no original lyric page does,
# so they identify a translation even when MusicBrainz has no language on file.
TRANSLATION_HEADERS = re.compile(
    r"Songtext\s+zu|Traducci[oó]n\s+(?:al|de)|Tradu[cç][aã]o\s+(?:em|de)|"
    r"Traduzione\s+(?:in|di)|Traduction\s+(?:en|de)|"
    r"[Пп]еревод\s+песни|T[uü]rk[cç]e\s+[CÇcç]eviri|แปลเพลง|"
    r"\b(?:English|Spanish|German|French|Turkish|Polish|Italian|Portuguese|"
    r"Russian|Thai|Romanian|Greek|Hungarian)\s+Translation\b",
    re.IGNORECASE,
)

SECTION_RE = re.compile(r"^\[[^\]]{2,60}\]", re.M)


def clean_genius(text: str, title: str) -> str:
    """Strip the boilerplate lyricsgenius leaves around the actual lyrics."""
    # lyricsgenius prepends "{Title} Lyrics\n"
    text = re.sub(rf"^{re.escape(title)}\s+Lyrics\n", "", text, flags=re.IGNORECASE)
    # Strip contributor line: "123 ContributorsTitle..."
    text = re.sub(r"^\d+\s+Contributors?\n", "", text, flags=re.MULTILINE)
    # Strip "You might also like" injected mid-lyrics
    text = re.sub(r"You might also like\n?", "", text)
    # Strip concert/ticket promos Genius injects around the lyrics
    text = re.sub(r"^.*Get tickets as low as.*$\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^See .+ Live.*$\n?", "", text, flags=re.MULTILINE)
    # Strip trailing embed marker
    text = re.sub(r"\d*Embed\s*$", "", text.rstrip())
    # Normalize line endings and collapse 3+ blank lines
    text = text.replace("\r\n", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def looks_like_tracklist(text: str) -> bool:
    """Detect album/tracklist/booklet pages returned instead of lyrics."""
    head = text[:200].lower()
    if re.search(r"\btrack\s*list\b|\btracklist\b", head):
        return True
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 4:
        return False
    # Lines like "1. Song Name" / "01 Title" / "1 - Title" dominate a tracklist.
    numbered = sum(1 for ln in lines if re.match(r"^\(?\d{1,2}[\).\-:\s]", ln))
    if numbered >= 4 and numbered / len(lines) >= 0.6:
        return True
    # "- Song Name (1,234,567)" stat rows.
    stats = sum(1 for ln in lines if re.match(r"^[-*•]\s*.+\(\s*[\d,]{4,}\s*\)\s*$", ln))
    return stats >= 4 and stats / len(lines) >= 0.5


def looks_like_prose(text: str) -> bool:
    """Novels/scripts: mostly long paragraph lines, no [Verse]/[Chorus] markers."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 3 or len(text) < 3000:
        return False
    if SECTION_RE.search(text):
        return False
    long_lines = sum(1 for ln in lines if len(ln) > 120)
    return long_lines / len(lines) > 0.25


def detect_lang(text: str) -> str | None:
    """ISO 639-1 language of the lyric body, or None if undeterminable."""
    if _lang_detect is None:
        return None
    sample = re.sub(r"\[.*?\]", " ", text).strip()  # drop [Verse]/[Chorus] markers
    if len(sample) < 40:
        return None
    try:
        return _lang_detect(sample).split("-")[0]
    except LangDetectException:
        return None


_SCRIPT_RANGES = {
    "hangul":     (0xAC00, 0xD7AF),
    "thai":       (0x0E00, 0x0E7F),
    "cyrillic":   (0x0400, 0x04FF),
    "arabic":     (0x0600, 0x06FF),
    "greek":      (0x0370, 0x03FF),
    "hebrew":     (0x0590, 0x05FF),
    "devanagari": (0x0900, 0x097F),
}

# Which script a language is normally written in, for the check below.
_LANG_SCRIPT = {
    "ko": "hangul", "th": "thai", "ru": "cyrillic", "uk": "cyrillic",
    "bg": "cyrillic", "sr": "cyrillic", "ar": "arabic", "fa": "arabic",
    "el": "greek", "he": "hebrew", "hi": "devanagari",
}


def dominant_script(text: str, min_letters: int = 20) -> str | None:
    """The non-Latin script this text is mostly written in, or None."""
    counts = dict.fromkeys(_SCRIPT_RANGES, 0)
    letters = 0
    for ch in text:
        if not ch.isalpha():
            continue
        letters += 1
        cp = ord(ch)
        for name, (lo, hi) in _SCRIPT_RANGES.items():
            if lo <= cp <= hi:
                counts[name] += 1
                break
    if letters < min_letters:
        return None
    name, n = max(counts.items(), key=lambda kv: kv[1])
    return name if n / letters > 0.30 else None


def script_mismatch(title: str, text: str, expected_lang: str | None = None) -> bool:
    """True when lyrics are in a script the song title gives no reason to expect.

    A Latin-titled song whose lyrics are mostly Hangul/Thai/Cyrillic is nearly
    always Genius's localized translation page rather than the original. If the
    title itself carries that script (e.g. "사랑인가 봐 Love, Maybe") or
    MusicBrainz confirms the language, the lyrics are taken as genuine.

    This catches translations that the language check cannot, because
    MusicBrainz has no language on file for roughly a third of these tracks.
    """
    body_script = dominant_script(text)
    if body_script is None:
        return False
    # Titles are short, so allow a much lower letter count before deciding.
    if dominant_script(title, min_letters=2) == body_script:
        return False
    return _LANG_SCRIPT.get(expected_lang or "") != body_script


def validate(text: str, title: str = "", expected_lang: str | None = None) -> tuple[bool, str]:
    """Check that ``text`` is plausibly the lyrics of ``title``.

    ``expected_lang`` is an ISO 639-1 code from MusicBrainz; when supplied, a
    confident mismatch means Genius served a translation page rather than the
    original lyrics. 'mul' (multilingual) and None disable the check.

    Returns (ok, reason); reason is "" when ok.
    """
    body = (text or "").strip()
    if not body:
        return False, "empty"
    if len(body) < MIN_CHARS:
        return False, f"too short ({len(body)} chars)"
    if len(body) > MAX_CHARS:
        return False, f"too long for a song ({len(body)} chars)"
    if looks_like_tracklist(body):
        return False, "tracklist/stat page"
    if looks_like_prose(body):
        return False, "prose, not lyrics"
    for rx, why in JUNK_PATTERNS:
        if rx.search(body):
            return False, why
    # Only the first lines carry the page header; the word "translation" further
    # down is usually just a lyric.
    if TRANSLATION_HEADERS.search("\n".join(body.splitlines()[:3])):
        return False, "translation page, not the original lyrics"
    if expected_lang and expected_lang not in ("mul", "und"):
        got = detect_lang(body)
        if got and got != expected_lang:
            return False, f"wrong language (got {got}, expected {expected_lang})"
    return True, ""
