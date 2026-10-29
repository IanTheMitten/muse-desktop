"""`library` — owner of library.sqlite3.

Every read and write of library state goes through this module. It is the one
module in the system with write access to a database.
"""

from .db import connect
from .importer import ImportResult, import_manifest, load_manifest
from .plays import (
    FULL_LISTEN,
    PARTIAL,
    SKIP,
    UNKNOWN,
    classify_play,
    close_span,
    heartbeat,
    open_span,
    sweep_stale,
)
from .ratings import get_rating, set_rating
from .schema import LATEST_VERSION, current_version, migrate
from .state import accept_state_write, read_state, write_state

__all__ = [
    "FULL_LISTEN",
    "LATEST_VERSION",
    "PARTIAL",
    "SKIP",
    "UNKNOWN",
    "ImportResult",
    "accept_state_write",
    "classify_play",
    "close_span",
    "connect",
    "current_version",
    "get_rating",
    "heartbeat",
    "import_manifest",
    "load_manifest",
    "migrate",
    "open_span",
    "read_state",
    "set_rating",
    "sweep_stale",
    "write_state",
]
