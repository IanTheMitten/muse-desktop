# universal_reco/tests/test_model.py
from pathlib import Path

import numpy as np

from content_embed import embed

FIXTURE = Path(__file__).parent / "fixtures" / "sample.wav"


def test_embed_returns_fixed_size_vector():
    vector = embed(str(FIXTURE))

    assert isinstance(vector, np.ndarray)
    assert vector.ndim == 1
    assert vector.shape[0] == 768
    assert vector.dtype == np.float32


def test_embed_raises_on_missing_file():
    import pytest

    with pytest.raises(Exception):
        embed(str(FIXTURE.parent / "does_not_exist.wav"))


def test_falls_back_to_ffmpeg_when_torchaudio_cannot_decode(monkeypatch):
    """torchcodec treats a damaged packet as fatal and aborts the file.
    ffmpeg conceals the damage and keeps going, which on the six real files
    that failed in this library recovered essentially all of the audio --
    209.6 s out of a 209.59 s container.

    A track with a few corrupt frames is worth embedding from the rest; the
    alternative is one that can never enter the leg at all."""
    from content_embed import model

    def cannot_decode(path):
        raise RuntimeError("Could not push packet to decoder")

    monkeypatch.setattr(model.torchaudio, "load", cannot_decode)
    waveform = model._load_mono_resampled(str(FIXTURE))

    assert waveform.ndim == 1
    assert waveform.shape[0] > 0


def test_the_fallback_matches_the_normal_path(monkeypatch):
    """The two decoders must agree, or a file's vector would depend on which
    one happened to read it. Same duration and same rough loudness is enough
    -- sample-exact agreement across two resamplers is not a thing to
    demand."""
    from content_embed import model

    direct = model._load_mono_resampled(str(FIXTURE))

    def cannot_decode(path):
        raise RuntimeError("forced fallback")

    monkeypatch.setattr(model.torchaudio, "load", cannot_decode)
    fallback = model._load_mono_resampled(str(FIXTURE))

    assert abs(fallback.shape[0] - direct.shape[0]) <= model._TARGET_SAMPLE_RATE // 10
    assert abs(float(fallback.abs().mean()) - float(direct.abs().mean())) < 0.05


def test_a_file_nothing_can_decode_still_raises(monkeypatch, tmp_path):
    """The fallback widens what counts as readable; it does not make failure
    silent. A zero vector would corrupt every downstream cosine rather than
    surfacing the bad file."""
    import pytest

    from content_embed import model

    def cannot_decode(path):
        raise RuntimeError("forced fallback")

    monkeypatch.setattr(model.torchaudio, "load", cannot_decode)
    not_audio = tmp_path / "not-audio.m4a"
    not_audio.write_bytes(b"this is not an audio file")

    with pytest.raises(RuntimeError):
        model._load_mono_resampled(str(not_audio))
