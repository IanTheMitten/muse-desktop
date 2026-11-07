"""Content embeddings: CLAP audio and the Spotify feature vector.

The only module that imports torch — and only inside the real encoder's
`__init__` (`ClapAudioEncoder`), never at import time, so importing this
module, the default test run, and the app itself never pay for it.
`AudioEncoder` is a Protocol for exactly that reason: `StubAudioEncoder` is
deterministic (hash of input → a fixed-dimension L2-normalized vector) and is
what every test but the slow ones runs against.

`embeddings.audio_vec IS NULL` is the resumable batch's work queue — see
`muse.library.embeddings`. This module is the batch logic itself: what gets
computed for a pending track and what "the lyrics changed" means.
"""

import array
import hashlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import audio_decode
from .library import embeddings as library_embeddings
from .library import features as library_features
from .lyrics import read_lyrics

log = logging.getLogger("muse.embed")

EMBED = "embed"

AUDIO_DIM = 512

STUB_AUDIO_MODEL = "stub-audio"
CLAP_MODEL = "laion/larger_clap_music_and_speech"

# feature_vec's column order lives on the table that computes the raw values;
# re-exported here so callers of this module never need to reach into
# `library.features` just to build a `bounds` dict.
FEATURE_VEC_COLUMNS = library_features.FEATURE_VEC_COLUMNS


# --- vector packing ---------------------------------------------------

def pack_vector(values) -> bytes:
    """Float32 bytes, native byte order — the same layout numpy's
    `.tobytes()` would produce, so a real encoder's output packs identically
    to a stub's without either one depending on numpy."""
    return array.array("f", values).tobytes()


def unpack_vector(blob: bytes | None) -> list[float] | None:
    if blob is None:
        return None
    values = array.array("f")
    values.frombytes(blob)
    return list(values)


def l2_normalize(values: list[float]) -> list[float]:
    norm = sum(v * v for v in values) ** 0.5
    if norm == 0:
        return list(values)
    return [v / norm for v in values]


# --- encoder protocols --------------------------------------------------


class AudioEncoder(Protocol):
    model_name: str

    def encode(self, audio_path: Path) -> list[float]:
        """An L2-normalized, AUDIO_DIM-wide vector for the track at this path."""
        ...


def _hash_vector(seed: str, dim: int) -> list[float]:
    """Deterministic: the same seed always produces the same vector, and a
    different seed (almost certainly) produces a different one. Good enough
    for exercising ranking and batch logic; carries no musical meaning."""
    values: list[float] = []
    counter = 0
    while len(values) < dim:
        digest = hashlib.sha256(f"{seed}:{counter}".encode()).digest()
        for offset in range(0, len(digest) - 3, 4):
            if len(values) >= dim:
                break
            n = int.from_bytes(digest[offset : offset + 4], "big")
            values.append((n / 0xFFFFFFFF) * 2 - 1)
        counter += 1
    return l2_normalize(values[:dim])


class StubAudioEncoder:
    """No torch, no model download, no audio decode. Default encoder for
    tests and for any batch run that has not opted into the real one."""

    model_name = STUB_AUDIO_MODEL

    def encode(self, audio_path: Path) -> list[float]:
        return _hash_vector(str(audio_path), AUDIO_DIM)

    def encode_text(self, text: str) -> list[float]:
        return _hash_vector("text:" + text, AUDIO_DIM)


# --- real encoders ------------------------------------------------------
#
# torch and transformers are imported here, inside __init__, and nowhere
# else in this module. The class is not constructed by the default test run or by the app unless `config.embed_batch` is on — see
# `muse.api.create_app`.

CLAP_SAMPLE_RATE = 48_000  # what laion/larger_clap_music_and_speech expects
CLAP_WINDOW_SECONDS = 10
CLAP_WINDOW_FRACTIONS = (0.25, 0.5, 0.75)


def _probe_duration_seconds(audio_path: Path) -> float:
    return audio_decode.duration_seconds(audio_path)


def _decode_window(
    audio_path: Path, start_seconds: float, duration_seconds: float,
    sample_rate: int = CLAP_SAMPLE_RATE,
):
    """One window as mono float32 PCM at `sample_rate`, via PyAV (no ffmpeg
    on PATH in the frozen worker)."""
    return audio_decode.decode_mono(
        audio_path, sample_rate, max(0.0, start_seconds), duration_seconds
    )


class ClapAudioEncoder:
    """`laion/larger_clap_music_and_speech` via transformers, CPU.

    Three CLAP_WINDOW_SECONDS windows at 25%/50%/75% of the track, mean-
    pooled then L2-normalized — avoids intros and outros dominating a
    whole-track average and bounds the cost regardless of track length
    (PRD: "Audio embeddings").
    """

    model_name = CLAP_MODEL

    def __init__(self, source: Path | None = None):
        import torch
        from transformers import ClapModel, ClapProcessor

        src = str(source) if source else self.model_name
        self._torch = torch
        self._model = ClapModel.from_pretrained(src)
        self._model.eval()
        self._processor = ClapProcessor.from_pretrained(src)

    def encode_text(self, text: str) -> list[float]:
        """A free-text query in the same space as the audio vectors.

        CLAP is a *joint* text-audio model, and this is the whole reason the
        PRD chose it over MERT: one model embeds both sides, so vibe search
        (story 45) needs no second model and no second index. MERT cannot
        serve this at all -- it is audio-only, with no text tower -- which is
        why ADR 0004 keeps CLAP rather than letting the content leg replace
        it.

        L2-normalized like `encode`, so a text vector and an audio vector are
        comparable by plain cosine.
        """
        inputs = self._processor(text=[text], return_tensors="pt", padding=True)
        with self._torch.no_grad():
            features = self._model.get_text_features(**inputs)
        # `pooler_output`, for the same reason as the audio path: indexing the
        # output object gives `last_hidden_state`, which here is (1, 5, 768)
        # -- per-token hidden states in the text tower's own width, not the
        # 512-d joint-space vector an audio_vec can be compared against.
        return l2_normalize(features.pooler_output[0].cpu().numpy().tolist())

    def encode(self, audio_path: Path) -> list[float]:
        duration = _probe_duration_seconds(audio_path)
        windows = [
            _decode_window(
                audio_path,
                max(0.0, fraction * duration - CLAP_WINDOW_SECONDS / 2),
                CLAP_WINDOW_SECONDS,
            )
            for fraction in CLAP_WINDOW_FRACTIONS
        ]
        # `audio=`, not `audios=`: transformers 5.x turned the old spelling
        # into a hard ValueError rather than a warning.
        inputs = self._processor(
            audio=windows, sampling_rate=CLAP_SAMPLE_RATE, return_tensors="pt"
        )
        with self._torch.no_grad():
            features = self._model.get_audio_features(**inputs)
        # And `get_audio_features` now returns a BaseModelOutputWithPooling
        # rather than the projected tensor it used to. `pooler_output` is the
        # 512-d vector in CLAP's shared text-audio space; `last_hidden_state`
        # is (1, 1024, 2, 32) and is not what any of this compares.
        pooled = features.pooler_output.mean(dim=0)
        return l2_normalize(pooled.cpu().numpy().tolist())


# --- lyrics text prep -----------------------------------------------------

# A line that is nothing but one bracketed tag: "[Chorus]", "[Verse 1]",
# "[Bridge]". Real lyric lines that happen to contain brackets (a bracketed
# aside mid-sentence) do not match because they carry other text too.
_SECTION_HEADER_RE = re.compile(r"^\s*\[[^\]\n]+\]\s*$")


def clean_lyrics_for_embedding(text: str) -> str:
    """The exact text `lyrics_hash` is computed over and the lyrics encoder
    sees. Section headers are stripped because they are structure, not
    lyric content, and would otherwise nudge every song with a "[Chorus]"
    tag toward each other regardless of what the chorus says.
    """
    lines = [line for line in text.splitlines() if not _SECTION_HEADER_RE.match(line)]
    return "\n".join(lines).strip()


def lyrics_hash(text: str) -> str:
    """sha256 of exactly the text that gets embedded — never the raw file
    contents — so a change that clean_lyrics_for_embedding absorbs (e.g. a
    section header added or removed) does not trigger a re-embed of text
    that reads identically to the model."""
    return hashlib.sha256(text.encode()).hexdigest()


# --- feature vector ---------------------------------------------------


def normalize_feature_vector(
    raw: tuple[float, ...] | None,
    bounds: dict[str, tuple[float, float]],
) -> list[float] | None:
    """Min-max normalize a raw 13-tuple against frozen per-column bounds.

    None when there is nothing to normalize (`raw` is None — the track has
    not been measured) or nothing to normalize against yet (`bounds` does not
    cover every column — the library has never had a fully-measured track).
    Out-of-range values are clipped to [0, 1] rather than extrapolated, which
    is what keeps a stored feature_vec meaningful after the bounds are frozen
    (ADR 0003 / PRD O3): a later outlier moves to the nearest edge instead of
    silently redefining the scale for every vector already stored.
    """
    if raw is None:
        return None
    if any(column not in bounds for column in FEATURE_VEC_COLUMNS):
        return None

    normalized = []
    for column, value in zip(FEATURE_VEC_COLUMNS, raw):
        low, high = bounds[column]
        if high == low:
            normalized.append(0.0)
            continue
        scaled = (value - low) / (high - low)
        normalized.append(min(1.0, max(0.0, scaled)))
    return normalized


# --- the batch ----------------------------------------------------------


@dataclass(slots=True)
class EmbedResult:
    considered: int = 0
    embedded: int = 0
    lyrics_reembedded: int = 0
    failed: int = 0


def _lyrics_paths(conn, track_rowids: list[int]) -> dict[int, str | None]:
    """`lyrics_path` for a batch of tracks in one query, rather than one
    round trip per already-embedded track -- the lyrics-changed pass looks
    at every one of them on every run, so a per-track query would turn a
    normal, nothing-changed startup into a query storm over the library."""
    if not track_rowids:
        return {}
    placeholders = ",".join("?" * len(track_rowids))
    rows = conn.execute(
        f"SELECT track_rowid, lyrics_path FROM tracks"
        f" WHERE track_rowid IN ({placeholders})",
        track_rowids,
    ).fetchall()
    return {row["track_rowid"]: row["lyrics_path"] for row in rows}


def _cleaned_lyrics(lyrics_path: str | None) -> str | None:
    text = read_lyrics(lyrics_path)
    if text is None:
        return None
    cleaned = clean_lyrics_for_embedding(text)
    return cleaned or None


def run_embed_batch(
    conn,
    audio_encoder: AudioEncoder,
    *,
    on_progress: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> EmbedResult:
    """Embed every pending track, then refresh `lyrics_hash` for any already-
    embedded track whose cleaned lyrics text has changed since.

    Resumable by construction: the pending queue comes from
    `library.embeddings.pending`, which is exactly the set a killed run
    would leave behind. A track's failure is counted and skipped rather than
    ending the run, same as the art backfill.
    """
    result = EmbedResult()
    queue = library_embeddings.pending(conn)
    # Captured before any writes below, so a track embedded in this same run
    # is not immediately re-examined by the lyrics-changed pass.
    stored_hashes = library_embeddings.lyrics_hash_by_track(conn)
    bounds = library_features.frozen_bounds(conn)
    result.considered = len(queue)

    for done, track in enumerate(queue, start=1):
        if should_stop is not None and should_stop():
            break
        try:
            _embed_one(conn, track, audio_encoder, bounds)
        except Exception:
            log.exception("Embedding failed for track %d", track["track_rowid"])
            result.failed += 1
        else:
            result.embedded += 1
        if on_progress is not None:
            on_progress(done, len(queue), f"track {track['track_rowid']}")

    lyrics_paths = _lyrics_paths(conn, list(stored_hashes))

    for track_rowid, stored_hash in stored_hashes.items():
        if should_stop is not None and should_stop():
            break
        if track_rowid not in lyrics_paths:
            continue
        cleaned = _cleaned_lyrics(lyrics_paths[track_rowid])
        fresh_hash = lyrics_hash(cleaned) if cleaned is not None else None
        if fresh_hash == stored_hash:
            continue
        try:
            library_embeddings.update_lyrics_hash(
                conn, track_rowid, lyrics_hash=fresh_hash
            )
        except Exception:
            log.exception("Lyrics hash refresh failed for track %d", track_rowid)
            result.failed += 1
        else:
            result.lyrics_reembedded += 1

    return result


def _embed_one(conn, track, audio_encoder, bounds) -> None:
    track_rowid = track["track_rowid"]
    audio_path = Path(track["file_path"])

    audio_vec = pack_vector(audio_encoder.encode(audio_path))

    cleaned = _cleaned_lyrics(track["lyrics_path"])
    hash_ = lyrics_hash(cleaned) if cleaned is not None else None

    raw = library_features.raw_feature_values(conn, track_rowid)
    normalized = normalize_feature_vector(raw, bounds)
    feature_vec = pack_vector(normalized) if normalized is not None else None

    library_embeddings.upsert(
        conn,
        track_rowid,
        audio_vec=audio_vec,
        feature_vec=feature_vec,
        audio_model=audio_encoder.model_name,
        lyrics_hash=hash_,
    )
