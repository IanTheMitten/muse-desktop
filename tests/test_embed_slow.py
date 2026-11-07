"""The real CLAP encoder, end to end.

Skipped unless torch and transformers are installed (the `embed` extra) and
run explicitly with `pytest -m slow` — never part of the default run, and
never a dependency of it. This is PRD seam 7's "one separately-marked slow
test": everything else in the embed batch runs against the stub encoders.
"""

import math
import struct
import wave
from pathlib import Path

import pytest

pytest.importorskip("torch")
pytest.importorskip("transformers")

from muse import embed  # noqa: E402  (after the importorskip guards)

pytestmark = pytest.mark.slow


def _sine_wav(path: Path, seconds: int = 12, sample_rate: int = 44_100,
              freq: float = 440.0) -> None:
    """A fixture audio file long enough for all three CLAP windows to land
    fully inside it — a real music file is not needed to prove the encoder
    produces a correctly-shaped, normalized vector."""
    with wave.open(str(path), "w") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = bytearray()
        for i in range(seconds * sample_rate):
            value = int(32_767 * 0.2 * math.sin(2 * math.pi * freq * i / sample_rate))
            frames += struct.pack("<h", value)
        wav.writeframes(bytes(frames))


def test_the_real_clap_encoder_produces_a_512d_l2_normalized_vector(tmp_path):
    audio_path = tmp_path / "fixture.wav"
    _sine_wav(audio_path)

    encoder = embed.ClapAudioEncoder()
    vector = encoder.encode(audio_path)

    assert len(vector) == embed.AUDIO_DIM
    norm = sum(v * v for v in vector) ** 0.5
    assert abs(norm - 1.0) < 1e-3
