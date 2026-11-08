"""`muse/energy.py` -- pure energy-axis math and its purity seam."""

import inspect

import pytest

import muse.energy as energy


def test_raw_energy_closer_to_calm_is_negative():
    assert energy.raw_energy([1, 0], [1, 0], [0, 1]) == -1.0


def test_raw_energy_closer_to_energetic_is_positive():
    assert energy.raw_energy([0, 1], [1, 0], [0, 1]) == 1.0


def test_to_unit_empty():
    assert energy.to_unit({}) == {}


def test_to_unit_single_is_zero():
    assert energy.to_unit({7: 3.2}) == {7: 0.0}


def test_to_unit_spreads_over_minus_one_to_one():
    assert energy.to_unit({1: 0.1, 2: 0.5, 3: 0.9}) == {1: -1.0, 2: 0.0, 3: 1.0}


def test_to_unit_ties_share_average_rank():
    out = energy.to_unit({1: 0.2, 2: 0.2, 3: 0.9})
    assert out == {1: pytest.approx(-0.5), 2: pytest.approx(-0.5), 3: pytest.approx(1.0)}


def test_spearman_perfect_positive():
    assert energy.spearman([1, 2, 3], [10, 20, 30]) == pytest.approx(1.0)


def test_spearman_perfect_negative():
    assert energy.spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)


def test_spearman_single_point_is_none():
    assert energy.spearman([1], [1]) is None


def test_spearman_constant_input_is_none():
    assert energy.spearman([1, 1, 1], [1, 2, 3]) is None


def test_constants():
    assert energy.CALM_PROMPT == "a calm, relaxing song"
    assert energy.ENERGETIC_PROMPT == "an upbeat, high-energy song"
    assert energy.CALM == "calm"
    assert energy.ENERGETIC == "energetic"
    assert energy.GATE_RHO == 0.5


def test_energy_module_is_pure():
    source = inspect.getsource(energy)
    assert "sqlite3" not in source
    assert "Connection" not in source
    imports = [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert all(line == "import math" for line in imports)
