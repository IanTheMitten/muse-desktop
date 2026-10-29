"""Pure functions in `muse.embed`: vector packing, the stub encoders, lyrics
cleaning, and feature-vector normalization.

No torch anywhere in this file. The stub encoders are deterministic --
hash of input to a fixed-dimension, L2-normalized vector -- which is what
lets ranking behaviour be tested without a 200 MB model (PRD, seam 7).
"""

import array
import hashlib
from pathlib import Path

from muse import embed


def test_pack_and_unpack_round_trip_float32_vectors():
    values = [0.1, -0.5, 2.0, 0.0]

    blob = embed.pack_vector(values)
    back = embed.unpack_vector(blob)

    assert len(blob) == 4 * 4  # float32, 4 bytes each
    assert back == list(array.array("f", values))


def test_unpack_vector_of_none_is_none():
    assert embed.unpack_vector(None) is None


def test_stub_audio_encoder_is_deterministic():
    encoder = embed.StubAudioEncoder()

    first = encoder.encode(Path("MUSIC/1.m4a"))
    second = encoder.encode(Path("MUSIC/1.m4a"))

    assert first == second


def test_stub_audio_encoder_differs_by_input():
    encoder = embed.StubAudioEncoder()

    a = encoder.encode(Path("MUSIC/1.m4a"))
    b = encoder.encode(Path("MUSIC/2.m4a"))

    assert a != b


def test_stub_audio_encoder_is_the_right_shape_and_l2_normalized():
    encoder = embed.StubAudioEncoder()

    vector = encoder.encode(Path("MUSIC/1.m4a"))

    assert len(vector) == embed.AUDIO_DIM
    norm = sum(v * v for v in vector) ** 0.5
    assert abs(norm - 1.0) < 1e-6


def test_stub_lyrics_encoder_is_the_right_shape_and_l2_normalized():
    encoder = embed.StubLyricsEncoder()

    vector = encoder.encode("some cleaned lyrics")

    assert len(vector) == embed.LYRICS_DIM
    norm = sum(v * v for v in vector) ** 0.5
    assert abs(norm - 1.0) < 1e-6


def test_clean_lyrics_strips_bracketed_section_headers():
    text = "[Chorus]\nHello there\n\n[Verse 1]\nWorld"

    assert embed.clean_lyrics_for_embedding(text) == "Hello there\n\nWorld"


def test_clean_lyrics_of_only_headers_is_empty():
    assert embed.clean_lyrics_for_embedding("[Instrumental]") == ""


def test_clean_lyrics_leaves_ordinary_text_alone():
    text = "Just a line\nAnother line"

    assert embed.clean_lyrics_for_embedding(text) == text


def test_lyrics_hash_is_sha256_of_the_exact_text():
    text = "Hello there\n\nWorld"

    assert embed.lyrics_hash(text) == hashlib.sha256(text.encode()).hexdigest()


def test_normalize_feature_vector_scales_into_zero_one():
    from muse.library.features import FEATURE_VEC_COLUMNS

    raw = tuple(10.0 * i for i in range(len(FEATURE_VEC_COLUMNS)))
    bounds = {column: (0.0, 10.0 * (len(FEATURE_VEC_COLUMNS) - 1))
              for column in FEATURE_VEC_COLUMNS}

    normalized = embed.normalize_feature_vector(raw, bounds)

    assert normalized[0] == 0.0
    assert abs(normalized[-1] - 1.0) < 1e-9


def test_normalize_feature_vector_clips_outliers():
    from muse.library.features import FEATURE_VEC_COLUMNS

    bounds = {column: (0.0, 10.0) for column in FEATURE_VEC_COLUMNS}
    raw = tuple(20.0 for _ in FEATURE_VEC_COLUMNS)  # every value above max

    normalized = embed.normalize_feature_vector(raw, bounds)

    assert all(v == 1.0 for v in normalized)

    raw_low = tuple(-20.0 for _ in FEATURE_VEC_COLUMNS)
    normalized_low = embed.normalize_feature_vector(raw_low, bounds)
    assert all(v == 0.0 for v in normalized_low)


def test_normalize_feature_vector_is_none_without_full_bounds():
    from muse.library.features import FEATURE_VEC_COLUMNS

    raw = tuple(1.0 for _ in FEATURE_VEC_COLUMNS)

    assert embed.normalize_feature_vector(raw, {}) is None


def test_normalize_feature_vector_is_none_without_raw_values():
    from muse.library.features import FEATURE_VEC_COLUMNS

    bounds = {column: (0.0, 10.0) for column in FEATURE_VEC_COLUMNS}

    assert embed.normalize_feature_vector(None, bounds) is None


def test_stub_audio_va_encoder_is_deterministic_and_in_range():
    encoder = embed.StubAudioVAEncoder()

    first = encoder.encode(Path("MUSIC/1.m4a"))
    second = encoder.encode(Path("MUSIC/1.m4a"))

    assert first == second
    assert len(first) == 2
    assert all(-1.0 <= v <= 1.0 for v in first)


def test_stub_audio_va_encoder_differs_by_input():
    encoder = embed.StubAudioVAEncoder()

    a = encoder.encode(Path("MUSIC/1.m4a"))
    b = encoder.encode(Path("MUSIC/2.m4a"))

    assert a != b


def test_stub_lyrics_va_encoder_is_deterministic_and_in_range():
    encoder = embed.StubLyricsVAEncoder()

    first = encoder.encode("some cleaned lyrics")
    second = encoder.encode("some cleaned lyrics")

    assert first == second
    assert len(first) == 2
    assert all(-1.0 <= v <= 1.0 for v in first)


def test_fuse_emotion_vec_falls_back_to_audio_va_alone_without_lyrics_va():
    audio_va = (0.3, -0.4)

    assert embed.fuse_emotion_vec(audio_va, None) == audio_va


def test_fuse_emotion_vec_blends_audio_and_lyrics_by_the_fixed_weight():
    audio_va = (1.0, 1.0)
    lyrics_va = (0.0, 0.0)
    w = embed.EMOTION_AUDIO_WEIGHT

    fused = embed.fuse_emotion_vec(audio_va, lyrics_va)

    assert fused == (w * 1.0 + (1 - w) * 0.0, w * 1.0 + (1 - w) * 0.0)
