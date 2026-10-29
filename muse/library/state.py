"""The server-side player state, and the guard that keeps two devices from
corrupting it.

The queue lives on the server so it follows the listener between the laptop and
the phone. Both devices heartbeat every 10 seconds, which means an idle
background tab would otherwise keep overwriting the position of the device that
is actually playing — the failure is silent, and the only symptom is a position
that drifts backwards for no reason.

Two rules, both in `accept_state_write()`.
"""

import json
import sqlite3
import time

DEFAULT_STATE = {
    "queue": [],
    "queue_index": 0,
    "position_ms": 0,
    "shuffle": 0,
    "repeat_mode": "off",
    "radio_seed": None,
    "radio_added": [],
    "updated_at": 0,
    "updated_by": None,
}


def accept_state_write(
    incoming_updated_at: int,
    stored_updated_at: int,
    client_state: str,
) -> bool:
    """Whether a player-state write should be applied.

    - Only a client that is actually `playing` writes. Paused and idle clients
      read.
    - `updated_at` must strictly increase. Equal is rejected too: two writes
      carrying the same timestamp carry no evidence of which came second.

    The timestamp is the server's, not the client's. A client-supplied one
    would mean two devices with disagreeing clocks fight forever, with the
    faster clock winning every conflict and no way for a listener to see why.
    Ordering writes by the single clock that observes all of them removes the
    question.
    """
    if client_state != "playing":
        return False
    return incoming_updated_at > stored_updated_at


def read_state(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT * FROM player_state WHERE id = 1").fetchone()
    if row is None:
        return dict(DEFAULT_STATE)
    state = dict(row)
    state.pop("id", None)
    state["queue"] = json.loads(state["queue"])
    state["radio_added"] = json.loads(state["radio_added"])
    return state


def write_state(
    conn: sqlite3.Connection,
    queue: list[int],
    queue_index: int,
    position_ms: int,
    client_state: str,
    shuffle: int = 0,
    repeat_mode: str = "off",
    radio_seed: int | None = None,
    updated_by: str | None = None,
    radio_added: list[int] | None = None,
    updated_at: int | None = None,
) -> bool:
    """Apply a guarded write. Returns whether it was accepted."""
    now = updated_at if updated_at is not None else int(time.time() * 1000)
    stored = read_state(conn)["updated_at"]
    if not accept_state_write(now, stored, client_state):
        return False

    conn.execute(
        "INSERT INTO player_state"
        "  (id, queue, queue_index, position_ms, shuffle, repeat_mode,"
        "   radio_seed, updated_at, updated_by, radio_added)"
        " VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(id) DO UPDATE SET"
        "   queue=excluded.queue, queue_index=excluded.queue_index,"
        "   position_ms=excluded.position_ms, shuffle=excluded.shuffle,"
        "   repeat_mode=excluded.repeat_mode, radio_seed=excluded.radio_seed,"
        "   updated_at=excluded.updated_at, updated_by=excluded.updated_by,"
        "   radio_added=excluded.radio_added",
        (
            json.dumps(queue),
            queue_index,
            position_ms,
            shuffle,
            repeat_mode,
            radio_seed,
            now,
            updated_by,
            json.dumps(radio_added or []),
        ),
    )
    return True
