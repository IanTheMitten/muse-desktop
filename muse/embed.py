"""Content embeddings: CLAP audio, the Spotify feature vector, MiniLM lyrics,
and the emotion leg's audio-VA / lyrics-VA / fused emotion_vec (ADR 0003).

The only module that imports torch — and only inside the real encoders'
`__init__` (`ClapAudioEncoder`, `MiniLmLyricsEncoder`, `MerAudioVAEncoder`,
`TextVAEncoder`) or a helper one of them calls (`_build_audio_va_model`),
never at import time, so importing this module, the default test run, and
the app itself never pay for it. `AudioEncoder`,
`LyricsEncoder`, `AudioVAEncoder`, and `LyricsVAEncoder` are Protocols for
exactly that reason: the four Stub* encoders are deterministic (hash of
input → a fixed-dimension L2-normalized vector for content, an unnormalized
2D point for VA) and are what every test but the slow ones runs against.

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

from .library import embeddings as library_embeddings
from .library import features as library_features
from .lyrics import read_lyrics

log = logging.getLogger("muse.embed")

EMBED = "embed"

AUDIO_DIM = 512
LYRICS_DIM = 384

STUB_AUDIO_MODEL = "stub-audio"
STUB_LYRICS_MODEL = "stub-lyrics"
CLAP_MODEL = "laion/larger_clap_music_and_speech"
MINILM_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

STUB_AUDIO_VA_MODEL = "stub-audio-va"
STUB_LYRICS_VA_MODEL = "stub-lyrics-va"
AUDIO_VA_MODEL = "audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim"
LYRICS_VA_MODEL = "j-hartmann/emotion-english-distilroberta-base"

# `w` in ADR 0003's `emotion_vec = w * audio_va + (1 - w) * lyrics_va`. Equal
# weight: there is no tuning data yet to justify trusting one source over the
# other, and audio_va alone is already the fallback when lyrics_va is absent.
EMOTION_AUDIO_WEIGHT = 0.5

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


class LyricsEncoder(Protocol):
    model_name: str

    def encode(self, text: str) -> list[float]:
        """An L2-normalized, LYRICS_DIM-wide vector for this cleaned text."""
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


class StubLyricsEncoder:
    model_name = STUB_LYRICS_MODEL

    def encode(self, text: str) -> list[float]:
        return _hash_vector(text, LYRICS_DIM)


# --- emotion (valence-arousal) encoder protocols -------------------------
#
# A VA point is a position on a bounded plane, not a direction -- unlike
# audio_vec/lyrics_vec it is never L2-normalized, and comparing two of them
# is Euclidean distance, not cosine (see `muse.reco.emotion_sim`).


class AudioVAEncoder(Protocol):
    model_name: str

    def encode(self, audio_path: Path) -> tuple[float, float]:
        """(valence, arousal), each in [-1, 1], for the track at this path."""
        ...


class LyricsVAEncoder(Protocol):
    model_name: str

    def encode(self, text: str) -> tuple[float, float]:
        """(valence, arousal), each in [-1, 1], for this cleaned text."""
        ...


def _hash_va(seed: str) -> tuple[float, float]:
    """Deterministic stand-in VA point -- same idea as `_hash_vector`, just
    2D and left unnormalized, since VA is a bounded plane, not a direction."""
    digest = hashlib.sha256(seed.encode()).digest()
    valence = (int.from_bytes(digest[0:4], "big") / 0xFFFFFFFF) * 2 - 1
    arousal = (int.from_bytes(digest[4:8], "big") / 0xFFFFFFFF) * 2 - 1
    return valence, arousal


class NullAudioVAEncoder:
    """Yields no VA point at all, so the emotion leg is written NULL.

    Deliberately not `StubAudioVAEncoder`. The stub produces deterministic
    hash-derived coordinates, which is right for tests and catastrophic for a
    real library: a model that failed to load would silently fill `audio_va`
    and `emotion_vec` with meaningless-but-plausible numbers, and nothing
    downstream could tell them from measured ones. Show the loss, never hide
    it (principle 3) -- a null leg is visible in `reasons`, a fake one is
    not.
    """

    # Named, not None. `audio_va_model` is what tells `embeddings.pending`
    # the difference between "never asked" and "asked, and the model was
    # gone" -- and without that difference every track keeps a NULL audio_va
    # forever, re-queues on every restart, and re-embeds the whole library.
    model_name = "unavailable"

    def encode(self, audio_path: Path) -> None:
        return None


class StubAudioVAEncoder:
    model_name = STUB_AUDIO_VA_MODEL

    def encode(self, audio_path: Path) -> tuple[float, float]:
        return _hash_va(str(audio_path))


class StubLyricsVAEncoder:
    model_name = STUB_LYRICS_VA_MODEL

    def encode(self, text: str) -> tuple[float, float]:
        return _hash_va(text)


def fuse_emotion_vec(
    audio_va: tuple[float, float] | None,
    lyrics_va: tuple[float, float] | None,
) -> tuple[float, float] | None:
    """`emotion_vec = w * audio_va + (1 - w) * lyrics_va` (ADR 0003), falling
    back to whichever side exists -- an instrumental has no lyrics_va and
    still gets a usable point instead of dropping the emotion leg entirely.

    Both sides null gives None, which is the emotion leg genuinely absent.
    That happens when the audio-VA model cannot be loaded at all; writing a
    fused point from no evidence would be inventing one."""
    if audio_va is None:
        return lyrics_va
    if lyrics_va is None:
        return audio_va
    w = EMOTION_AUDIO_WEIGHT
    return (
        w * audio_va[0] + (1 - w) * lyrics_va[0],
        w * audio_va[1] + (1 - w) * lyrics_va[1],
    )


# --- real encoders ------------------------------------------------------
#
# torch, transformers, and sentence-transformers are imported here, inside
# __init__, and nowhere else in this module. Neither class is constructed by
# the default test run or by the app unless `config.embed_batch` is on — see
# `muse.api.create_app`.

CLAP_SAMPLE_RATE = 48_000  # what laion/larger_clap_music_and_speech expects
CLAP_WINDOW_SECONDS = 10
CLAP_WINDOW_FRACTIONS = (0.25, 0.5, 0.75)


def _probe_duration_seconds(audio_path: Path) -> float:
    import subprocess

    output = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(output.stdout.strip())


def _decode_window(
    audio_path: Path, start_seconds: float, duration_seconds: float,
    sample_rate: int = CLAP_SAMPLE_RATE,
):
    """One window as mono float32 PCM at `sample_rate`, via ffmpeg — the
    PRD's chosen decode path, and the only one that reads every codec
    already in this library without a second audio dependency."""
    import subprocess

    import numpy as np

    result = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", str(max(0.0, start_seconds)),
            "-t", str(duration_seconds), "-i", str(audio_path),
            "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "-",
        ],
        capture_output=True, check=True,
    )
    return np.frombuffer(result.stdout, dtype=np.float32)


class ClapAudioEncoder:
    """`laion/larger_clap_music_and_speech` via transformers, CPU.

    Three CLAP_WINDOW_SECONDS windows at 25%/50%/75% of the track, mean-
    pooled then L2-normalized — avoids intros and outros dominating a
    whole-track average and bounds the cost regardless of track length
    (PRD: "Audio embeddings").
    """

    model_name = CLAP_MODEL

    def __init__(self):
        import torch
        from transformers import ClapModel, ClapProcessor

        self._torch = torch
        self._model = ClapModel.from_pretrained(self.model_name)
        self._model.eval()
        self._processor = ClapProcessor.from_pretrained(self.model_name)

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


class MiniLmLyricsEncoder:
    """`sentence-transformers/all-MiniLM-L6-v2`, 384d, L2-normalized."""

    model_name = MINILM_MODEL

    def __init__(self):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(self.model_name)

    def encode(self, text: str) -> list[float]:
        vector = self._model.encode(text, normalize_embeddings=True)
        return vector.tolist()


AUDIO_VA_SAMPLE_RATE = 16_000  # what the wav2vec2 msp-dim checkpoint expects
AUDIO_VA_WINDOW_SECONDS = 10


def _build_audio_va_model(model_name: str):
    """Load the msp-dim checkpoint into a locally-defined regression head.

    audeering ships weights and a `config.json` naming
    `Wav2Vec2ForSpeechClassification`, but no module implementing it: the
    snapshot holds only the config, the safetensors, and the preprocessor
    config, and `auto_map` is null. `AutoModel` therefore resolves the
    config's `model_type` and builds a bare `Wav2Vec2Model`, silently
    dropping every `classifier.*` tensor as unexpected and returning a
    `Wav2Vec2BaseModelOutput` whose string keys unpacked as "logits" — the
    bug this replaces. `trust_remote_code` was never doing anything here
    because there is no remote code to trust, so it is gone too. The head
    below is the one the model card documents; defining it here is the only
    way the fine-tuned weights actually load.

    Kept in a function rather than at module scope so torch stays imported
    inside real encoders only — a top-level `class ...(nn.Module)` would
    make `import muse.embed` pull torch in for everyone.
    """
    import torch
    from huggingface_hub import hf_hub_download
    from safetensors import safe_open
    from torch import nn
    from transformers import AutoConfig
    from transformers.models.wav2vec2.modeling_wav2vec2 import (
        Wav2Vec2Model,
        Wav2Vec2PreTrainedModel,
    )

    # This repo's `config.json` leaves `num_labels` null, so the checkpoint
    # is the only honest source for the head's width. Measured here:
    # `classifier.out_proj.weight` is [3, 1024] — the three msp-dim outputs
    # (arousal, dominance, valence) over wav2vec2-large's 1024-wide hidden
    # state.
    with safe_open(
        hf_hub_download(model_name, "model.safetensors"), framework="pt"
    ) as checkpoint:
        num_labels, hidden_size = (
            checkpoint.get_slice("classifier.out_proj.weight").get_shape()
        )

    class RegressionHead(nn.Module):
        def __init__(self, config):
            super().__init__()
            self.dense = nn.Linear(config.hidden_size, config.hidden_size)
            self.dropout = nn.Dropout(config.final_dropout)
            self.out_proj = nn.Linear(config.hidden_size, config.num_labels)

        def forward(self, features):
            x = self.dropout(features)
            x = torch.tanh(self.dense(x))
            return self.out_proj(self.dropout(x))

    class EmotionModel(Wav2Vec2PreTrainedModel):
        def __init__(self, config):
            super().__init__(config)
            self.wav2vec2 = Wav2Vec2Model(config)
            self.classifier = RegressionHead(config)
            # `post_init`, not the model card's older `init_weights`: current
            # transformers builds the tied-weight bookkeeping there, and
            # `from_pretrained` reaches for it while finalising the load.
            self.post_init()

        def forward(self, input_values):
            # Mean-pooled over time before the head, as the model card
            # specifies — the checkpoint was fine-tuned against exactly this
            # pooling, and any other reduction reads its weights wrong.
            hidden_states = torch.mean(self.wav2vec2(input_values)[0], dim=1)
            return hidden_states, self.classifier(hidden_states)

    config = AutoConfig.from_pretrained(model_name, num_labels=num_labels)
    if config.hidden_size != hidden_size:
        raise RuntimeError(
            f"{model_name}: head expects {hidden_size}-wide hidden states, "
            f"config declares {config.hidden_size}"
        )
    model, info = EmotionModel.from_pretrained(
        model_name, config=config, output_loading_info=True
    )
    # A head left at its random initialisation still returns three numbers
    # in a plausible range, so a partial load here would poison every
    # emotion vec in the library without ever looking wrong. Refuse instead:
    # `_optional_encoder` in `muse.jobs` turns the failure into a missing
    # emotion leg, which is recoverable, and a silently random one is not.
    dropped = {
        name: sorted(keys)
        for name, keys in (
            ("missing", info.get("missing_keys") or []),
            ("unexpected", info.get("unexpected_keys") or []),
            ("mismatched", [k[0] for k in info.get("mismatched_keys") or []]),
        )
        if keys
    }
    if dropped:
        raise RuntimeError(f"{model_name} did not load cleanly: {dropped}")
    return model


def _to_va_axis(value: float) -> float:
    """One msp-dim output ([0, 1]) as one VA axis ([-1, 1]).

    Clamped, because the head's last layer is a plain `nn.Linear`: its
    targets were bounded but its outputs are not, and a real track measured
    at arousal 1.027 on the first run. `reco.emotion_sim` scales distance by
    the diagonal of the [-1, 1] plane, so a point outside it is a distance
    the similarity cannot express. Trimming the overshoot costs a hundredth
    of an axis on the loudest tracks; letting it through costs the leg its
    range.
    """
    return max(-1.0, min(1.0, value * 2 - 1))


class MerAudioVAEncoder:
    """`audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim`: a wav2vec2 head
    fine-tuned to regress arousal/dominance/valence in [0, 1]. Dominance is
    discarded — ADR 0003 only wants the VA plane — and the output is
    rescaled to the [-1, 1] convention the rest of this module's VA points
    use. The head is rebuilt locally by `_build_audio_va_model` rather than
    loaded by name: the repo publishes no model class of its own, only
    weights.

    One centre window rather than CLAP's three: emotion, unlike audio
    content, does not need intro/outro-avoidance to the same degree, and a
    speech-domain checkpoint gains nothing from redundant windows over a
    full song.
    """

    model_name = AUDIO_VA_MODEL

    def __init__(self):
        import torch
        from transformers import Wav2Vec2FeatureExtractor

        self._torch = torch
        self._model = _build_audio_va_model(self.model_name)
        self._model.eval()
        self._processor = Wav2Vec2FeatureExtractor.from_pretrained(self.model_name)

    def encode(self, audio_path: Path) -> tuple[float, float]:
        duration = _probe_duration_seconds(audio_path)
        window = _decode_window(
            audio_path,
            max(0.0, duration / 2 - AUDIO_VA_WINDOW_SECONDS / 2),
            AUDIO_VA_WINDOW_SECONDS,
            sample_rate=AUDIO_VA_SAMPLE_RATE,
        )
        inputs = self._processor(
            window, sampling_rate=AUDIO_VA_SAMPLE_RATE, return_tensors="pt"
        )
        with self._torch.no_grad():
            _, logits = self._model(inputs.input_values)
        arousal, dominance, valence = logits[0].tolist()
        # The head's last layer is a plain linear one; its targets were in
        # [0, 1] but nothing bounds it there, and loud tracks do overshoot
        # (an arousal of 1.03 on real library audio). Clamp, because
        # `reco.emotion_sim` divides by the diagonal of the [-1, 1] plane
        # and a point outside it is a distance the scale cannot express.
        return _to_va_axis(valence), _to_va_axis(arousal)


# Ekman-emotion label -> circumplex VA position (Russell 1980), used to turn
# a text classifier's per-label probabilities into one continuous point on
# the plane instead of jumping between corners whenever a lyric sits on the
# border between two emotions. Coordinates are illustrative placements
# within [-1, 1], not measured constants -- there is no ground-truth VA
# lexicon in this pipeline to calibrate them against yet.
_EMOTION_TO_VA = {
    "anger": (-0.6, 0.8),
    "disgust": (-0.6, 0.2),
    "fear": (-0.7, 0.7),
    "joy": (0.8, 0.6),
    "neutral": (0.0, -0.2),
    "sadness": (-0.7, -0.4),
    "surprise": (0.3, 0.8),
}


class TextVAEncoder:
    """`j-hartmann/emotion-english-distilroberta-base`'s 7-class Ekman
    probabilities, folded into a VA point via `_EMOTION_TO_VA` and weighted
    by each label's predicted probability -- a probability-weighted blend
    degrades gracefully across an ambiguous lyric instead of snapping to
    whichever single label edges out the others.
    """

    model_name = LYRICS_VA_MODEL

    def __init__(self):
        from transformers import pipeline

        self._classifier = pipeline(
            "text-classification", model=self.model_name, top_k=None
        )
        # Ask the model how much it can take rather than hard-coding 512:
        # this checkpoint allows 514 positions, two of which are the special
        # tokens, and a sibling checkpoint need not agree.
        self._max_length = self._classifier.tokenizer.model_max_length

    def encode(self, text: str) -> tuple[float, float]:
        # `truncation=True`, not `text[:512]`. The slice counted *characters*
        # as a proxy for tokens, which holds for English -- 512 characters is
        # roughly 128 tokens -- and breaks badly for scripts the tokenizer
        # splits more finely. A Korean character can cost two or three
        # tokens, so 512 characters of K-pop lyrics overran RoBERTa's 514
        # position embeddings and raised "index 514 is out of bounds",
        # failing the whole track rather than its emotion leg.
        #
        # The tokenizer is the only thing that knows where its own limit is,
        # so let it do the cutting.
        [scores] = self._classifier(
            text, truncation=True, max_length=self._max_length
        )
        valence = arousal = 0.0
        for entry in scores:
            v, a = _EMOTION_TO_VA[entry["label"]]
            valence += v * entry["score"]
            arousal += a * entry["score"]
        return valence, arousal


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


def _audio_va_by_track(conn, track_rowids: list[int]) -> dict[int, bytes | None]:
    """`audio_va` for a batch of already-embedded tracks in one query --
    same reasoning as `_lyrics_paths`: the lyrics-changed pass looks at
    every one of them on every run, so re-fusing `emotion_vec` must not cost
    a query per track."""
    if not track_rowids:
        return {}
    placeholders = ",".join("?" * len(track_rowids))
    rows = conn.execute(
        f"SELECT track_rowid, audio_va FROM embeddings"
        f" WHERE track_rowid IN ({placeholders})",
        track_rowids,
    ).fetchall()
    return {row["track_rowid"]: row["audio_va"] for row in rows}


def run_embed_batch(
    conn,
    audio_encoder: AudioEncoder,
    lyrics_encoder: LyricsEncoder,
    audio_va_encoder: AudioVAEncoder | None = None,
    lyrics_va_encoder: LyricsVAEncoder | None = None,
    *,
    on_progress: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> EmbedResult:
    """Embed every pending track, then re-embed lyrics for any already-
    embedded track whose cleaned lyrics text has changed since.

    Resumable by construction: the pending queue comes from
    `library.embeddings.pending`, which is exactly the set a killed run
    would leave behind. A track's failure is counted and skipped rather than
    ending the run, same as the art backfill.

    `audio_va_encoder`/`lyrics_va_encoder` default to the stubs -- unlike
    `audio_encoder`/`lyrics_encoder`, which every caller already has an
    opinion about, most callers of this function do not care about the
    emotion leg specifically and should not have to say so.
    """
    audio_va_encoder = audio_va_encoder or StubAudioVAEncoder()
    lyrics_va_encoder = lyrics_va_encoder or StubLyricsVAEncoder()

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
            _embed_one(
                conn, track, audio_encoder, lyrics_encoder,
                audio_va_encoder, lyrics_va_encoder, bounds,
            )
        except Exception:
            log.exception("Embedding failed for track %d", track["track_rowid"])
            result.failed += 1
        else:
            result.embedded += 1
        if on_progress is not None:
            on_progress(done, len(queue), f"track {track['track_rowid']}")

    lyrics_paths = _lyrics_paths(conn, list(stored_hashes))
    audio_vas = _audio_va_by_track(conn, list(stored_hashes))

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
            # An already-embedded track *usually* has an audio_va, set
            # alongside audio_vec in _embed_one -- but not always. When the
            # audio-VA model cannot be loaded at all the leg is written NULL
            # on purpose, so this path has to cope with having no audio side
            # to fall back to or fuse with. The comment that used to sit here
            # asserted the opposite as an invariant.
            audio_va = unpack_vector(audio_vas[track_rowid])
            audio_va = tuple(audio_va) if audio_va is not None else None
            if cleaned is None:
                library_embeddings.update_lyrics(
                    conn, track_rowid, lyrics_vec=None, lyrics_model=None,
                    lyrics_hash=None, lyrics_va=None,
                    emotion_vec=(pack_vector(audio_va)
                                 if audio_va is not None else None),
                    lyrics_va_model=None,
                )
            else:
                vector = pack_vector(lyrics_encoder.encode(cleaned))
                lyrics_va = lyrics_va_encoder.encode(cleaned)
                emotion_vec = fuse_emotion_vec(audio_va, lyrics_va)
                library_embeddings.update_lyrics(
                    conn, track_rowid, lyrics_vec=vector,
                    lyrics_model=lyrics_encoder.model_name,
                    lyrics_hash=fresh_hash, lyrics_va=pack_vector(lyrics_va),
                    emotion_vec=(pack_vector(emotion_vec)
                     if emotion_vec is not None else None),
                    lyrics_va_model=lyrics_va_encoder.model_name,
                )
        except Exception:
            log.exception("Lyrics re-embed failed for track %d", track_rowid)
            result.failed += 1
        else:
            result.lyrics_reembedded += 1

    return result


def _embed_one(
    conn, track, audio_encoder, lyrics_encoder, audio_va_encoder,
    lyrics_va_encoder, bounds,
) -> None:
    track_rowid = track["track_rowid"]
    audio_path = Path(track["file_path"])

    audio_vec = pack_vector(audio_encoder.encode(audio_path))
    audio_va = audio_va_encoder.encode(audio_path)

    cleaned = _cleaned_lyrics(track["lyrics_path"])
    if cleaned is None:
        lyrics_vec = lyrics_model = hash_ = lyrics_va = lyrics_va_model = None
    else:
        lyrics_vec = pack_vector(lyrics_encoder.encode(cleaned))
        lyrics_model = lyrics_encoder.model_name
        hash_ = lyrics_hash(cleaned)
        lyrics_va = lyrics_va_encoder.encode(cleaned)
        lyrics_va_model = lyrics_va_encoder.model_name

    emotion_vec = fuse_emotion_vec(audio_va, lyrics_va)

    raw = library_features.raw_feature_values(conn, track_rowid)
    normalized = normalize_feature_vector(raw, bounds)
    feature_vec = pack_vector(normalized) if normalized is not None else None

    library_embeddings.upsert(
        conn,
        track_rowid,
        audio_vec=audio_vec,
        feature_vec=feature_vec,
        lyrics_vec=lyrics_vec,
        audio_model=audio_encoder.model_name,
        lyrics_model=lyrics_model,
        lyrics_hash=hash_,
        audio_va=pack_vector(audio_va) if audio_va is not None else None,
        lyrics_va=pack_vector(lyrics_va) if lyrics_va is not None else None,
        emotion_vec=(pack_vector(emotion_vec)
                     if emotion_vec is not None else None),
        audio_va_model=audio_va_encoder.model_name,
        lyrics_va_model=lyrics_va_model,
    )
