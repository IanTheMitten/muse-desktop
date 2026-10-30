import subprocess
import sys

import pytest

av = pytest.importorskip("av")

from muse.ingest import tag_m4a
from muse.remux import NO_M4A, remux_m4a


def _encode_sine(path, seconds=2, rate=44100):
    import numpy as np

    t = np.arange(seconds * rate) / rate
    pcm = (np.sin(2 * np.pi * 440 * t) * 0.3 * 32767).astype("int16")
    with av.open(str(path), "w", format="ipod") as out:
        stream = out.add_stream("aac", rate=rate)
        stream.layout = "mono"
        for i in range(0, len(pcm), 1024):
            frame = av.AudioFrame.from_ndarray(pcm[None, i:i + 1024], format="s16", layout="mono")
            frame.sample_rate = rate
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)


def test_remux_roundtrip_and_tag(tmp_path):
    from mutagen.mp4 import MP4

    path = tmp_path / "a.m4a"
    _encode_sine(path)
    remux_m4a(path)
    assert path.exists()
    assert not list(tmp_path.glob("*.remux.m4a"))
    assert abs(MP4(path).info.length - 2.0) < 0.1
    tag_m4a(path, artist_name="A", album_name="B", track_title="C",
            track_id=None, track_no=1, total_tracks=1, disc_no=None)
    assert MP4(path)["\xa9nam"] == ["C"]


def test_remux_garbage_raises(tmp_path):
    import os

    path = tmp_path / "bad.m4a"
    path.write_bytes(os.urandom(1024))
    with pytest.raises(RuntimeError) as exc:
        remux_m4a(path)
    assert str(exc.value) == NO_M4A
    assert not (tmp_path / "bad.remux.m4a").exists()


def test_import_does_not_load_av():
    subprocess.run(
        [sys.executable, "-c", "import sys, muse.remux; assert 'av' not in sys.modules"],
        check=True,
    )
