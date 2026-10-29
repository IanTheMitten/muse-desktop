"""Reading lyrics files.

Absent lyrics are a loud failure — the panel says so — so what is tested here
is the quiet kind: a file that reads as text but displays as rubbish, and a
file that is technically there and holds nothing.

Nothing in this module writes. That is the ticket's whole constraint, and the
last test asserts it.
"""

from muse.lyrics import read_lyrics


def write(tmp_path, name, data: bytes):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_a_plain_file_reads_as_it_is(tmp_path):
    path = write(tmp_path, "a.txt", "One line\nAnother line\n".encode())
    assert read_lyrics(path) == "One line\nAnother line\n"


def test_a_byte_order_mark_is_not_a_lyric(tmp_path):
    """Left in, it renders as a stray glyph in front of the first word — and it
    looks like a typo in the file rather than a bug in the reader."""
    path = write(tmp_path, "a.txt", b"\xef\xbb\xbfFirst line\n")
    assert read_lyrics(path) == "First line\n"


def test_accented_text_survives(tmp_path):
    """Mojibake is the quietest possible failure: the page renders, nothing
    errors, and the words are wrong."""
    path = write(tmp_path, "a.txt", "Je t'aime — encore\n".encode())
    assert read_lyrics(path) == "Je t'aime — encore\n"


def test_one_bad_byte_does_not_lose_the_whole_file(tmp_path):
    """A file that is readable everywhere else is worth more than a refusal."""
    path = write(tmp_path, "a.txt", b"Good line\n\xff\nAnother good line\n")
    text = read_lyrics(path)
    assert text is not None
    assert "Good line" in text and "Another good line" in text


def test_an_empty_file_is_no_lyrics(tmp_path):
    """`lyrics_path` being set does not mean there are lyrics. Returning "" here
    would render an empty panel with no explanation, which reads as a bug."""
    assert read_lyrics(write(tmp_path, "a.txt", b"")) is None
    assert read_lyrics(write(tmp_path, "b.txt", b"\n\n   \n")) is None


def test_a_missing_file_is_no_lyrics(tmp_path):
    """The import records the path; the file can go away afterwards."""
    assert read_lyrics(tmp_path / "not-here.txt") is None


def test_no_path_is_no_lyrics():
    assert read_lyrics(None) is None
    assert read_lyrics("") is None


def test_reading_does_not_touch_the_file(tmp_path):
    """Slice 1 displays lyrics and nothing else. Any write here would be a
    change to files slice 4 is supposed to be the first to edit."""
    path = write(tmp_path, "a.txt", b"Words\n")
    before = path.stat()

    read_lyrics(path)

    after = path.stat()
    assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns, before.st_size)


def test_lyrics_files_names_txt_and_lrc(tmp_path):
    from muse.lyrics import lyrics_files

    assert lyrics_files(tmp_path, 7) == (tmp_path / "7.txt", tmp_path / "7.lrc")
