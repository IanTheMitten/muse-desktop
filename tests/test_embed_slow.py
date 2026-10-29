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


def test_the_real_mer_encoder_produces_a_va_point_in_range(tmp_path):
    audio_path = tmp_path / "fixture.wav"
    _sine_wav(audio_path)

    encoder = embed.MerAudioVAEncoder()
    valence, arousal = encoder.encode(audio_path)

    assert -1.0 <= valence <= 1.0
    assert -1.0 <= arousal <= 1.0


def test_the_real_text_va_encoder_produces_a_va_point_in_range():
    encoder = embed.TextVAEncoder()

    valence, arousal = encoder.encode("A joyful, upbeat song about summer")

    assert -1.0 <= valence <= 1.0
    assert -1.0 <= arousal <= 1.0


@pytest.mark.slow
def test_text_va_encoder_handles_lyrics_longer_than_the_token_limit():
    """`TextVAEncoder` truncated with `text[:512]`, counting characters as a
    stand-in for tokens. That holds for English -- 512 characters is roughly
    128 tokens -- and fails for scripts the tokenizer splits more finely.

    Both real tracks that hit this are K-pop. One of them is only 446
    characters, comfortably inside the old slice, and still overran RoBERTa's
    514 position embeddings because 262 of those characters are Korean. It
    raised "index 514 is out of bounds for dimension 1 with size 514" and
    failed the entire track, not just its emotion leg.

    Long ASCII is included as the case the old slice did handle, so a
    regression that drops truncation entirely still fails here."""
    from muse.embed import TextVAEncoder

    encoder = TextVAEncoder()
    for text in ("사랑인가 봐 " * 400, "love maybe " * 400):
        valence, arousal = encoder.encode(text)
        assert -1.0 <= valence <= 1.0
        assert -1.0 <= arousal <= 1.0
