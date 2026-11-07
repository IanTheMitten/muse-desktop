"""scripts/energy_check.py: CLAP energy vs stored audio_va arousal."""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from muse.embed import pack_vector
from muse import energy
from muse.library.schema import MIGRATIONS
from tests.conftest import insert_track

_spec = importlib.util.spec_from_file_location(
    "energy_check", Path(__file__).resolve().parent.parent / "scripts" / "energy_check.py"
)
energy_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(energy_check)


def _encode(text):
    return [1.0, 0.0] if text == energy.CALM_PROMPT else [0.0, 1.0]


def _add(conn, rowid, audio_vec, va):
    insert_track(conn, rowid)
    conn.execute(
        "INSERT INTO embeddings (track_rowid, audio_vec, audio_va, indexed_at)"
        " VALUES (?,?,?,?)",
        (rowid, pack_vector(audio_vec), pack_vector(va) if va else None, "2026-01-01"),
    )
    conn.commit()


# the script is a pre-migration-23 gate, so the fixture stops at v22
@pytest.fixture
def conn(tmp_path):
    raw = sqlite3.connect(tmp_path / "lib.sqlite3", isolation_level=None)
    raw.row_factory = sqlite3.Row
    raw.execute("CREATE TABLE schema_version (version INTEGER NOT NULL, applied_at INTEGER NOT NULL)")
    for i, fn in enumerate(MIGRATIONS[:22]):
        fn(raw)
        raw.execute("INSERT INTO schema_version VALUES (?, 0)", (i + 1,))
    yield raw
    raw.close()


def _fill(conn, reverse):
    # energy = vec[1] - vec[0]; rising with rowid
    for i, rowid in enumerate((1, 2, 3, 4)):
        vec = [1.0 - i * 0.2, i * 0.2]
        arousal = (3 - i if reverse else i) * 0.1
        _add(conn, rowid, vec, [0.0, arousal])


def test_aligned_passes(conn):
    _fill(conn, reverse=False)
    out = energy_check.check(conn, _encode)
    assert out["n"] == 4
    assert out["rho"] == pytest.approx(1.0)
    assert out["passed"]


def test_reversed_fails(conn):
    _fill(conn, reverse=True)
    out = energy_check.check(conn, _encode)
    assert out["rho"] == pytest.approx(-1.0)
    assert not out["passed"]


def test_row_without_va_skipped(conn):
    _fill(conn, reverse=False)
    _add(conn, 5, [0.5, 0.5], None)
    assert energy_check.check(conn, _encode)["n"] == 4
