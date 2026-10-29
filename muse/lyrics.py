"""Reading the `.lyrics.txt` files that are already on disk.

Deliberately the whole of it. The provider chain, quality validation, the
`lyrics_state` table, and every write of a `.lyrics.txt` or `.lrc` file now
live in `muse/lyrics_lrclib.py` and `muse/lyrics_sync.py` (slice 4) — the
reader and the writer are deliberately separate modules, not merged, so this
one stays read-only plain text with no timings, no parsed lines, and no
offset, exactly as it was before slice 4 existed to have anything to undo.

Measured on 2026-08-10: 704 of the 723 playable tracks have a file, every one
of them present on disk and valid UTF-8. The largest is 478 KB, which is one of
the wrong-content files the PRD describes. It is served as it is; deciding that
a file is wrong is slice 4's job, and guessing at it here would hide the only
evidence of the problem.
"""

from pathlib import Path

# A byte-order mark at the start of a file is a byte, not a lyric. Left in, it
# renders as a stray glyph before the first word.
BOM = "﻿"


def read_lyrics(path: str | Path | None) -> str | None:
    """The text of a lyrics file, or None when there is nothing to show.

    None covers every way of having no lyrics — no path recorded, the file
    deleted since import, a file that holds only whitespace — because they are
    the same thing to a reader and splitting them would only give the UI more
    empty states to render identically.
    """
    if not path:
        return None

    file = Path(path)
    try:
        raw = file.read_bytes()
    except OSError:
        return None

    # `errors="replace"` rather than a strict decode: a file with one bad byte
    # is still a readable lyric everywhere else, and refusing the whole thing
    # over it would be a worse answer than a single replacement character.
    text = raw.decode("utf-8", errors="replace").lstrip(BOM)
    return text if text.strip() else None
