"""Range parsing.

The spec puts streaming in the "loud failure" column and it is right about the
part that matters: whether mobile Safari is happy is settled on the phone, not
here. But the *parsing* is a different animal — an off-by-one in `Content-Range`
does not announce itself, it just makes seeking subtly wrong or makes the last
frame of a track vanish. It is pure, it takes no fixtures, and it costs nothing.
"""

import pytest

from muse.streaming import ByteRange, Unsatisfiable, parse_range, read_range

SIZE = 10_000


def test_no_header_means_send_the_whole_file():
    assert parse_range(None, SIZE) is None
    assert parse_range("", SIZE) is None


def test_an_ordinary_range():
    assert parse_range("bytes=0-1023", SIZE) == ByteRange(0, 1023)


def test_an_open_ended_range_runs_to_the_end():
    """`bytes=0-` is the first thing a media element sends."""
    assert parse_range("bytes=0-", SIZE) == ByteRange(0, SIZE - 1)


def test_a_range_from_the_middle():
    assert parse_range("bytes=5000-", SIZE) == ByteRange(5000, 9999)


def test_a_suffix_range_reads_from_the_end():
    """Safari asks for the last bytes of an .m4a to find the moov atom before
    it will play anything. Get this wrong and playback never starts."""
    assert parse_range("bytes=-500", SIZE) == ByteRange(9500, 9999)


def test_a_suffix_larger_than_the_file_is_the_whole_file():
    assert parse_range("bytes=-99999", SIZE) == ByteRange(0, SIZE - 1)


def test_an_end_past_the_file_is_clamped_not_rejected():
    """Asking for more than exists is normal client behaviour near the end of
    a track. The answer is the rest of the file."""
    assert parse_range("bytes=9990-999999", SIZE) == ByteRange(9990, 9999)


def test_the_last_byte_is_reachable():
    assert parse_range(f"bytes={SIZE - 1}-", SIZE) == ByteRange(9999, 9999)
    assert parse_range(f"bytes={SIZE - 1}-", SIZE).length == 1


def test_a_start_past_the_end_is_unsatisfiable():
    with pytest.raises(Unsatisfiable):
        parse_range(f"bytes={SIZE}-", SIZE)


def test_a_zero_length_suffix_is_unsatisfiable():
    with pytest.raises(Unsatisfiable):
        parse_range("bytes=-0", SIZE)


def test_a_backwards_range_is_unsatisfiable():
    with pytest.raises(Unsatisfiable):
        parse_range("bytes=500-100", SIZE)


@pytest.mark.parametrize(
    "header",
    [
        "bytes=0-100, 200-300",  # multi-range: no media element asks for this
        "items=0-100",
        "bytes=abc-def",
        "bytes=",
        "garbage",
    ],
)
def test_a_header_we_do_not_serve_falls_back_to_the_whole_file(header):
    """RFC 7233 says ignore what you cannot honour and answer 200, which is
    also the only safe thing for a player."""
    assert parse_range(header, SIZE) is None


def test_content_range_counts_the_way_http_counts():
    """Inclusive end, total size after the slash. An off-by-one here is exactly
    the silent kind."""
    assert ByteRange(0, 1023).content_range(SIZE) == "bytes 0-1023/10000"
    assert ByteRange(0, 1023).length == 1024
    assert ByteRange(9999, 9999).content_range(SIZE) == "bytes 9999-9999/10000"


def test_read_range_returns_exactly_the_requested_bytes(tmp_path):
    audio = tmp_path / "track.m4a"
    audio.write_bytes(bytes(range(256)) * 40)  # 10240 bytes

    got = b"".join(read_range(audio, ByteRange(1000, 1999), chunk_size=128))

    assert len(got) == 1000
    assert got == audio.read_bytes()[1000:2000]


def test_read_range_can_read_the_final_byte(tmp_path):
    audio = tmp_path / "track.m4a"
    audio.write_bytes(b"0123456789")

    assert b"".join(read_range(audio, ByteRange(9, 9))) == b"9"
