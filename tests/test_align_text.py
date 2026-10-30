"""`muse/align_text.py`: pure alignment helpers shared by worker, job and UI."""

from muse.align_text import MMS, SCHUFO, build_lrc, fill_starts, iso3, lrc_stamp, lyric_lines, route, words
from muse.lrc import parse_lrc


def test_lyric_lines():
    assert lyric_lines("  a \n\n b\r\n[Chorus]\n") == ["a", "b", "[Chorus]"]
    assert lyric_lines("") == []


def test_words():
    assert words("Hello, World!") == ["hello", "world"]
    assert words("[Chorus]") == []
    assert words("Don’t stop (oh)") == ["don't", "stop", "oh"]
    assert words("24/7 love") == ["love"]


def test_route():
    assert SCHUFO == "schufo"
    assert MMS == "mms"
    assert route("hello world", "en") == SCHUFO
    assert route("사랑해 baby", "ko") == MMS
    assert route("hello 사랑해요 사랑해요", "en") == MMS
    assert route("hola mi amor", "es") == MMS
    assert route("", "en") == MMS


def test_iso3():
    assert iso3("en") == "eng"
    assert iso3("ko") == "kor"
    assert iso3("ja") == "jpn"
    assert iso3("xx") is None


def test_fill_starts():
    assert fill_starts([1.0, 2.0, 3.0]) == [1000, 2000, 3000]
    assert fill_starts([None, 2.0, 3.0, 4.0]) == [2000, 2000, 3000, 4000]
    assert fill_starts([1.0, 2.0, 3.0, None]) == [1000, 2000, 3000, 3000]
    assert fill_starts([1.0, 3.0, 2.0, 4.0]) == [1000, 3000, 3000, 4000]
    assert fill_starts([None] * 4) is None
    assert fill_starts([1.0, None, None, None]) is None
    assert fill_starts([5.0, 5.0, 5.0, 5.0, 6.0, 7.0]) == [5000, 5000, 5000, 5000, 6000, 7000]
    assert fill_starts([5.0] * 5 + [6.0]) is None
    assert fill_starts([]) is None
    assert fill_starts([1.5]) == [1500]
    assert fill_starts([0.0004]) == [0]


def test_lrc_stamp():
    assert lrc_stamp(61234) == "[01:01.23]"
    assert lrc_stamp(0) == "[00:00.00]"


def test_build_lrc():
    meta = {"artist_name": "A", "track_name": "T", "album_name": None}
    out = build_lrc(["one", "two"], [1000, 61234], meta)
    assert out == "[ar:A]\n[ti:T]\n[00:01.00]one\n[01:01.23]two\n"
    parsed = parse_lrc(out)
    assert [p.ms for p in parsed] == [1000, 61230]
    assert [p.text for p in parsed] == ["one", "two"]
