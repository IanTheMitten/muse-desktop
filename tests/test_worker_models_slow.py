import math
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

TONE = Path(__file__).parent / "fixtures" / "smoke" / "tone.wav"


def test_clap():
    from muse.embed import ClapAudioEncoder

    enc = ClapAudioEncoder()
    v = enc.encode(TONE)
    assert len(v) == 512
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, abs_tol=1e-3)
    assert len(enc.encode_text("calm piano")) == 512


def test_minilm():
    from muse.embed import MiniLmLyricsEncoder

    assert len(MiniLmLyricsEncoder().encode("hello world")) == 384


def test_audio_va():
    from muse.embed import MerAudioVAEncoder

    v = MerAudioVAEncoder().encode(TONE)
    assert len(v) == 2 and all(-1.0 <= x <= 1.0 for x in v)


def test_lyrics_va():
    from muse.embed import TextVAEncoder

    v = TextVAEncoder().encode("I am so happy")
    assert len(v) == 2 and all(-1.0 <= x <= 1.0 for x in v)


def test_mert():
    from muse.mert import MertEncoder

    assert len(MertEncoder().encode(TONE)) == 768
