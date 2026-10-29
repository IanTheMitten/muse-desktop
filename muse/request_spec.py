"""Request spec v1: the one shape the arc planner reads (docs/plans/2026-10-18-prompt-arc.md).
Pure validation and the seed-entry builder; no I/O."""

import math

from . import arc

VERSION = 1
KINDS = ("mood", "song", "artist", "mixed", "off_topic")
SHAPES = ("steady", "gradual", "late")
SOUNDS = ("calm", "mid", "energetic", "any")
LIMITS = ("anything", "no_explicit", "clean")


class SpecError(ValueError):
    """A spec field that fails validation; `field` is its dotted path."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


def _number(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _subject(value: object, field: str) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise SpecError(field, "must be null or an object of weights")
    out = {}
    for key, weight in value.items():
        if not isinstance(key, str):
            raise SpecError(field, "keys must be strings")
        if not _number(weight) or weight < 0:
            raise SpecError(field, f"weight for {key!r} must be a finite number >= 0")
        out[key] = weight
    return out


def _darkness(value: object, field: str) -> float | None:
    if value is None:
        return None
    if not _number(value) or not 0 <= value <= 1:
        raise SpecError(field, "must be null or a number in [0, 1]")
    return value


def _end(spec: dict, name: str) -> dict:
    block = spec[name]
    if not isinstance(block, dict):
        raise SpecError(name, "must be an object")
    return {
        "subject": _subject(block.get("subject"), f"{name}.subject"),
        "darkness": _darkness(block.get("darkness"), f"{name}.darkness"),
    }


def _choice(spec: dict, key: str, options: tuple, default: str) -> str:
    value = spec.get(key, default)
    if value not in options:
        raise SpecError(key, f"must be one of {', '.join(options)}")
    return value


def check(spec: object) -> dict:
    """A validated copy of `spec` with every key present."""
    if not isinstance(spec, dict):
        raise SpecError("spec", "must be an object")
    for key in ("version", "kind", "start", "goal"):
        if key not in spec:
            raise SpecError(key, "is required")
    version = spec["version"]
    if isinstance(version, bool) or version != VERSION:
        raise SpecError("version", f"must be {VERSION}")
    if spec["kind"] not in KINDS:
        raise SpecError("kind", f"must be one of {', '.join(KINDS)}")
    seed = spec.get("seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise SpecError("seed", "must be null or an int")
    artist = spec.get("artist")
    if artist is not None:
        if not isinstance(artist, str) or not artist.strip():
            raise SpecError("artist", "must be null or a non-empty string")
        artist = artist.strip().casefold()
    unresolved = spec.get("unresolved", [])
    if not isinstance(unresolved, list):
        raise SpecError("unresolved", "must be a list")
    defaulted = spec.get("defaulted", [])
    if not isinstance(defaulted, list):
        raise SpecError("defaulted", "must be a list")
    length = spec.get("length")
    if length is not None and (isinstance(length, bool) or not isinstance(length, int)
                               or not 1 <= length <= 100):
        raise SpecError("length", "must be null or an int in [1, 100]")
    return {
        "version": VERSION,
        "kind": spec["kind"],
        "seed": seed,
        "artist": artist,
        "unresolved": list(unresolved),
        "start": _end(spec, "start"),
        "goal": _end(spec, "goal"),
        "shape": _choice(spec, "shape", SHAPES, "gradual"),
        "sound": _choice(spec, "sound", SOUNDS, "any"),
        "length": length,
        "limits": _choice(spec, "limits", LIMITS, "anything"),
        "defaulted": list(defaulted),
    }


def seed_spec(seed: int, *, lift: bool = True) -> dict:
    """The spec a plain seed-track session stands for."""
    return check({
        "version": 1, "kind": "song", "seed": seed,
        "start": {"subject": None, "darkness": None},
        "goal": {"subject": None, "darkness": arc.END_TARGET if lift else None},
    })
