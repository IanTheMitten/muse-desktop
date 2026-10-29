"""`muse/lrc.py`: the shared LRC tag grammar, pure and I/O-free."""

from muse.lrc import LrcLine, parse_lrc, strip_timestamps

SYNCED_LRC = (
    "[ar:Some Artist]\n"
    "[ti:Some Song]\n"
    "[00:12.00] walking down this empty road again tonight\n"
    "[00:16.50] thinking of the words you never said to me\n"
    "[01:02.345] and the light is fading slow behind the hills\n"
)


def test_parse_lrc_reads_timestamps_in_order():
    lines = parse_lrc(SYNCED_LRC)
    assert lines == [
        LrcLine(ms=12_000, text="walking down this empty road again tonight"),
        LrcLine(ms=16_500, text="thinking of the words you never said to me"),
        LrcLine(ms=62_345, text="and the light is fading slow behind the hills"),
    ]


def test_parse_lrc_drops_metadata_lines():
    lines = parse_lrc(SYNCED_LRC)
    assert all(line.text not in ("Some Artist", "Some Song") for line in lines)


def test_parse_lrc_drops_untimed_stray_lines():
    """A line with no tag at all is dropped, not shown at position 0 -- an
    untimed line rendered as "playing since the first second" is a worse
    answer than not showing it."""
    text = "no tag on this one\n[00:05.00] this one has a tag"
    lines = parse_lrc(text)
    assert lines == [LrcLine(ms=5_000, text="this one has a tag")]


def test_parse_lrc_expands_a_shared_double_tag_into_two_lines():
    """A doubled timestamp on a repeated chorus line -- one text, two
    positions -- becomes two `LrcLine`s so the synced view's binary search
    over timestamps never has to special-case a repeated lyric."""
    text = "[00:10.00][00:40.00]same chorus line"
    lines = parse_lrc(text)
    assert lines == [
        LrcLine(ms=10_000, text="same chorus line"),
        LrcLine(ms=40_000, text="same chorus line"),
    ]


def test_parse_lrc_sorts_out_of_order_input():
    text = "[00:20.00] second\n[00:05.00] first"
    lines = parse_lrc(text)
    assert [line.text for line in lines] == ["first", "second"]


def test_parse_lrc_handles_mmss_without_a_fraction():
    assert parse_lrc("[01:02] no fraction") == [LrcLine(ms=62_000, text="no fraction")]


def test_parse_lrc_empty_text_yields_no_lines():
    assert parse_lrc("") == []


def test_strip_timestamps_matches_parse_lrc_text():
    """The writer's derivation and the reader's parse agree on what the
    words are, even though one keeps timestamps and the other discards
    them -- same tag grammar, read two different ways."""
    derived = strip_timestamps(SYNCED_LRC)
    parsed_text = "\n".join(line.text for line in parse_lrc(SYNCED_LRC))
    assert derived == parsed_text
