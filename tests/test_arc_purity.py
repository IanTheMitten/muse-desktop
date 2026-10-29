"""`muse/arc.py` purity -- the same seam `tests/test_reco_rank.py` asserts
for `muse/reco.py` (ADR 0009, Binding)."""

import inspect
import sqlite3

import muse.arc as arc


def test_arc_module_source_has_no_sqlite3_import():
    source = inspect.getsource(arc)
    assert "import sqlite3" not in source
    assert "Connection" not in source


def test_arc_module_holds_no_connection_attribute():
    for name in dir(arc):
        assert not isinstance(getattr(arc, name), sqlite3.Connection)
