"""`library` — owner of library.sqlite3.

Every read and write of library state goes through this module. It is the one
module in the system with write access to a database.
"""

from .db import connect
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
    "accept_state_write",
    "classify_play",
    "close_span",
    "connect",
    "current_version",
    "get_rating",
    "heartbeat",
    "migrate",
    "open_span",
    "read_state",
    "set_rating",
    "sweep_stale",
    "write_state",
]
