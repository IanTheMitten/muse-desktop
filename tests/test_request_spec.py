"""muse/request_spec.py: the validated request spec and the seed builder."""

import ast
import math
from pathlib import Path

import pytest

from muse import arc, request_spec as R


def base(**over):
    spec = {"version": 1, "kind": "mood",
            "start": {"subject": {"theme_broad:love": 0.8}, "darkness": 0.75},
            "goal": {"subject": None, "darkness": 0.25}}
    spec.update(over)
    return spec


def test_module_is_pure():
    tree = ast.parse(Path(R.__file__).read_text())
    imported = {a.name.split(".")[0] for n in ast.walk(tree)
                if isinstance(n, ast.Import) for a in n.names}
    imported |= {(n.module or "").split(".")[0] for n in ast.walk(tree)
                 if isinstance(n, ast.ImportFrom)}
    assert not imported & {"sqlite3", "requests", "urllib", "http", "socket"}


def test_full_spec_round_trips():
    full = {"version": 1, "kind": "mixed", "seed": 4, "artist": "iu",
            "unresolved": ["x"],
            "start": {"subject": {"theme_broad:love": 0.8}, "darkness": 0.75},
            "goal": {"subject": {"theme_broad:faith": 1}, "darkness": 0.25},
            "shape": "late", "sound": "calm", "length": 12,
            "limits": "clean", "defaulted": ["sound"]}
    assert R.check(full) == full
    assert R.check(R.check(full)) == full


def test_defaults_fill():
    out = R.check(base())
    assert out["seed"] is None and out["artist"] is None
    assert out["unresolved"] == [] and out["defaulted"] == []
    assert (out["shape"], out["sound"], out["length"], out["limits"]) == (
        "gradual", "any", None, "anything")


def test_check_returns_a_copy():
    spec = base()
    out = R.check(spec)
    out["start"]["darkness"] = 0.1
    assert spec["start"]["darkness"] == 0.75


@pytest.mark.parametrize("spec, field", [
    (base(version=0), "version"),
    (base(kind="nope"), "kind"),
    (base(seed=True), "seed"),
    (base(start={"subject": None, "darkness": 1.5}), "start.darkness"),
    (base(start={"subject": {"a": -1}, "darkness": None}), "start.subject"),
    (base(goal={"subject": {"a": math.nan}, "darkness": None}), "goal.subject"),
    (base(length=0), "length"),
    (base(length=101), "length"),
    (base(shape="fast"), "shape"),
    (base(artist=""), "artist"),
    (base(artist="  "), "artist"),
    ("nope", "spec"),
])
def test_bad_field_names_its_path(spec, field):
    with pytest.raises(R.SpecError) as err:
        R.check(spec)
    assert err.value.field == field


def test_missing_required_key():
    spec = base()
    del spec["goal"]
    with pytest.raises(R.SpecError) as err:
        R.check(spec)
    assert err.value.field == "goal"


def test_artist_is_stripped_and_casefolded():
    assert R.check(base(artist=" IU "))["artist"] == "iu"


def test_seed_spec():
    out = R.seed_spec(5)
    assert out["seed"] == 5 and out["kind"] == "song"
    assert out["goal"]["darkness"] == arc.END_TARGET
    assert R.seed_spec(5, lift=False)["goal"]["darkness"] is None
