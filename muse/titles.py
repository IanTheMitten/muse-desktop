"""Title normalization, shared by search indexing now and by lyrics provider
lookups in slices 3 and 4.

`repair_lyrics.py:norm_title()` is the prior art. It squashes a filename down to
an identity key with no separators, which is right for matching two filenames and
wrong for a search index, so this function keeps word boundaries. Both are
deliberately conservative in the same way: only suffixes and parentheticals from
a known noise vocabulary are removed, because the alternative — stripping every
dash and every parenthetical — eats titles like `Sgt. Pepper's Lonely Hearts Club
Band (Reprise)` where the parenthetical is the song.

The result of this function is an index entry. It is never displayed. Display
titles are always exactly what the catalog says.
"""

import re
import unicodedata

# Suffixes after a spaced dash that describe a *version* of the same song.
# `Talking to the Moon - Acoustic Piano` and `Stoned - Bonus Track` are the
# measured examples from the library.
_VERSION_SUFFIX = re.compile(
    r"""\s+-\s+(
        remaster(ed)?          |
        mono|stereo            |
        acoustic               |
        live                   |
        bonus\s+track          |
        deluxe                 |
        radio\s+edit           |
        single\s+version       |
        album\s+version        |
        extended(\s+\w+)?      |
        demo                   |
        from\s+["“]       |   # From "High School Musical: The Musical..."
        original\s+(motion\s+picture|soundtrack)
    ).*$""",
    re.IGNORECASE | re.VERBOSE,
)

# Parentheticals that name a collaborator rather than the song.
# `Uptown Funk (feat. Bruno Mars)`, `&burn (with Vince Staples)`.
_CREDIT_PAREN = re.compile(
    r"\s*[\(\[]\s*(feat\.?|featuring|ft\.?|with)\s+[^\)\]]*[\)\]]",
    re.IGNORECASE,
)

_PUNCT = re.compile(r"[‘’ʼ]")  # curly apostrophes -> plain
_SPACE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """Fold a display title down to a searchable form.

    Lowercases, removes credit parentheticals and version suffixes, and collapses
    whitespace. Leaves anything it does not recognise alone.
    """
    if not title:
        return ""

    text = unicodedata.normalize("NFKC", title)
    text = _PUNCT.sub("'", text)
    text = _CREDIT_PAREN.sub("", text)
    text = _VERSION_SUFFIX.sub("", text)
    text = _SPACE.sub(" ", text).strip()
    return text.lower()


_QUALIFIER = re.compile(r"\b(?:(live|remix|instrumental|acoustic)\b|(inst)\.)", re.IGNORECASE)


def qualifiers(title: str) -> frozenset[str]:
    """The version words a title carries, `inst.` folded to `instrumental` (Decision 6)."""
    return frozenset((word or "instrumental").lower() for word, _ in _QUALIFIER.findall(title or ""))


def same_title(a: str, b: str) -> bool:
    """Same song and same version: equal normalized titles and equal qualifiers (Decision 2)."""
    key = normalize_title(a)
    return bool(key) and key == normalize_title(b) and qualifiers(a) == qualifiers(b)
