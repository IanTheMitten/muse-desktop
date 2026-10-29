import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("av")

from muse.audio_decode import AudioDecodeError, decode_mono, duration_seconds  # noqa: E402

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "smoke").glob("tone.*"))


def test_six_fixtures_present():
    assert len(FIXTURES) == 6


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.suffix)
def test_decodes_float32_samples(path):
    samples = decode_mono(path, 16000)
    assert samples.dtype.name == "float32"
    assert len(samples) > 0


def test_window_length_on_long_enough_audio(tmp_path):
    import wave

    import numpy as np

    wav = tmp_path / "long.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.zeros(16000 * 3, dtype="<i2")).tobytes())
    assert abs(len(decode_mono(wav, 16000, 0.5, 1.0)) - 16000) <= 160


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.suffix)
def test_duration_matches_mutagen(path):
    mutagen = pytest.importorskip("mutagen")
    assert abs(duration_seconds(path) - mutagen.File(path).info.length) < 0.1


def test_garbage_raises(tmp_path):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(__import__("os").urandom(1024))
    with pytest.raises(AudioDecodeError):
        decode_mono(bad, 16000)


def test_imports_leave_torch_and_av_out():
    code = (
        "import sys, muse.audio_decode, muse.mert;"
        "assert 'torch' not in sys.modules and 'av' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
