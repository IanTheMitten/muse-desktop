import os
import wave

from mutagen.id3 import APIC, TALB, TIT2, TPE1, TPE2, TPOS, TRCK
from mutagen.wave import WAVE

from muse.tags import embedded_picture, read_tags


def _tagged_wav(tmp_path):
    p = tmp_path / "a.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 16000)
    f = WAVE(str(p))
    f.add_tags()
    f.tags.add(TIT2(encoding=3, text="Song"))
    f.tags.add(TPE1(encoding=3, text="Artist"))
    f.tags.add(TALB(encoding=3, text="Album"))
    f.tags.add(TPE2(encoding=3, text="AA"))
    f.tags.add(TRCK(encoding=3, text="3/10"))
    f.tags.add(TPOS(encoding=3, text="1/2"))
    f.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=b"\xff\xd8jpg"))
    f.save()
    return p


def test_read_tags(tmp_path):
    t = read_tags(_tagged_wav(tmp_path))
    assert (t.title, t.artist, t.album, t.album_artist) == ("Song", "Artist", "Album", "AA")
    assert (t.track_no, t.disc_no, t.duration_ms) == (3, 1, 2000)


def test_embedded_picture(tmp_path):
    assert embedded_picture(_tagged_wav(tmp_path)) == (b"\xff\xd8jpg", ".jpg")


def test_garbage_file_is_none(tmp_path):
    p = tmp_path / "x.mp3"
    p.write_bytes(os.urandom(2048))
    assert read_tags(p) is None
    assert embedded_picture(p) is None
