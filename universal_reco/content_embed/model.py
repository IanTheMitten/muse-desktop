"""Content-embedding leg: raw audio -> pretrained MERT-v1-95M -> vector.

Fails loudly (raises) on unreadable audio rather than returning a zero
vector -- this runs as offline batch processing, and a silent zero vector
would quietly corrupt every downstream cosine-similarity comparison instead
of surfacing the bad input file.

"Unreadable" is narrower than "corrupt". A file with a damaged bitstream but
recoverable audio is decoded by ffmpeg and embedded from what survives; only
a file nothing can get samples out of raises.
"""

import shutil
import subprocess

import numpy as np
import torch
import torchaudio
from transformers import AutoModel, Wav2Vec2FeatureExtractor

_MODEL_NAME = "m-a-p/MERT-v1-95M"
_TARGET_SAMPLE_RATE = 24000
_CHUNK_SECONDS = 5

_model = None
_processor = None


def _load_model():
    global _model, _processor
    if _model is None:
        _model = AutoModel.from_pretrained(_MODEL_NAME, trust_remote_code=True)
        _model.eval()
        _processor = Wav2Vec2FeatureExtractor.from_pretrained(
            _MODEL_NAME, trust_remote_code=True
        )
    return _model, _processor


def _ffmpeg_mono_resampled(path: str) -> torch.Tensor:
    """Decode with ffmpeg, straight to mono at the target rate.

    The fallback for files whose bitstream is damaged. torchcodec treats a
    bad packet as fatal and aborts the whole file; ffmpeg conceals it and
    keeps going, which for real damage of this kind recovers essentially
    everything -- measured on the six files in this library that failed,
    209.6 s recovered from a 209.59 s container, 208.5 from 208.557.

    Decoding directly to f32 mono at `_TARGET_SAMPLE_RATE` means ffmpeg does
    the downmix and resample too, so this path needs no torchaudio at all.
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not on PATH, and torchaudio could not "
                           f"decode {path}")
    process = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path,
         "-f", "f32le", "-ac", "1", "-ar", str(_TARGET_SAMPLE_RATE), "-"],
        capture_output=True,
    )
    samples = np.frombuffer(process.stdout, dtype=np.float32)
    if samples.size == 0:
        # `returncode` is deliberately not the test. ffmpeg exits non-zero
        # for the concealed-error case that this function exists to rescue,
        # so the only question that matters is whether audio came out.
        detail = process.stderr.decode("utf-8", "replace").strip()[:200]
        raise RuntimeError(f"ffmpeg decoded no audio from {path}: {detail}")
    return torch.from_numpy(samples.copy())


def _load_mono_resampled(path: str) -> torch.Tensor:
    """Mono audio at the target rate, by whichever decoder can read the file.

    torchaudio first, because it is the faster path and the one this project
    is built on. It raises on a damaged bitstream rather than skipping the
    damaged part, so ffmpeg is tried second -- a file with a few corrupt
    frames is worth embedding from the rest of the audio, and the
    alternative is a track that can never enter the leg at all.
    """
    try:
        waveform, sample_rate = torchaudio.load(path)
    except Exception:
        return _ffmpeg_mono_resampled(path)

    waveform = waveform.mean(dim=0)
    if sample_rate != _TARGET_SAMPLE_RATE:
        waveform = torchaudio.functional.resample(
            waveform, sample_rate, _TARGET_SAMPLE_RATE
        )
    return waveform


def _chunk(waveform: torch.Tensor) -> list[torch.Tensor]:
    chunk_len = _CHUNK_SECONDS * _TARGET_SAMPLE_RATE
    chunks = []
    for start in range(0, waveform.shape[0], chunk_len):
        chunk = waveform[start : start + chunk_len]
        if chunk.shape[0] < chunk_len:
            padded = torch.zeros(chunk_len)
            padded[: chunk.shape[0]] = chunk
            chunk = padded
        chunks.append(chunk)
    return chunks


def embed(path: str) -> np.ndarray:
    model, processor = _load_model()
    waveform = _load_mono_resampled(path)

    chunk_vectors = []
    for chunk in _chunk(waveform):
        inputs = processor(
            chunk.numpy(), sampling_rate=_TARGET_SAMPLE_RATE, return_tensors="pt"
        )
        with torch.no_grad():
            outputs = model(**inputs, output_hidden_states=True)
        # Pool across all 13 hidden-state layers (embedding + 12 transformer
        # layers), not just the final one -- different layers capture
        # different musical properties (MERT's model card recommends against
        # relying on last_hidden_state alone). No downstream task exists here
        # to learn per-layer weights, so this mirrors MERT's own reference
        # usage: mean over time per layer, then uniform mean across layers.
        layer_states = torch.stack(outputs.hidden_states).squeeze(1)  # (layers, time, hidden)
        time_pooled = layer_states.mean(dim=1)  # (layers, hidden)
        chunk_vectors.append(time_pooled.mean(dim=0))  # (hidden,)

    if not chunk_vectors:
        raise ValueError(f"No audio samples decoded from {path}")

    track_vector = torch.stack(chunk_vectors).mean(dim=0)
    return track_vector.numpy().astype(np.float32)
