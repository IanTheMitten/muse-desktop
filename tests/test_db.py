"""The shared connection.

One connection serves FastAPI's whole thread pool. Two threads interleaving on
it raises `InterfaceError: bad parameter or other API misuse` — an error that
appears only under real concurrency, which in practice means the first screen
that asks for a dozen covers at once.
"""

from concurrent.futures import ThreadPoolExecutor

from tests.conftest import insert_track


def test_many_threads_can_read_at_once(conn):
    for rowid in range(1, 40):
        insert_track(conn, rowid)

    def read(rowid: int) -> int:
        row = conn.execute(
            "SELECT track_rowid FROM tracks WHERE track_rowid = ?", (rowid,)
        ).fetchone()
        return row["track_rowid"]

    with ThreadPoolExecutor(max_workers=16) as pool:
        found = list(pool.map(read, [r for r in range(1, 40)] * 8))

    assert len(found) == 39 * 8


def test_reads_and_writes_interleave_without_corrupting_each_other(conn):
    insert_track(conn, 1)

    def write(index: int) -> None:
        conn.execute(
            "INSERT INTO plays (track_rowid, started_at, reached_ms,"
            " duration_ms, heartbeat_at) VALUES (1, ?, 0, 1000, ?)",
            (index, index),
        )

    def read(_: int) -> None:
        conn.execute("SELECT COUNT(*) FROM plays").fetchone()

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(write, range(60)))
        list(pool.map(read, range(60)))

    assert conn.execute("SELECT COUNT(*) FROM plays").fetchone()[0] == 60


def test_execute_still_reports_what_a_cursor_would(conn):
    """`lastrowid` and `rowcount` are what `plays` and `jobs` are built on."""
    insert_track(conn, 1)

    written = conn.execute(
        "INSERT INTO plays (track_rowid, started_at, reached_ms, duration_ms,"
        " heartbeat_at) VALUES (1, 0, 0, 1000, 0)"
    )
    assert written.lastrowid == 1

    updated = conn.execute("UPDATE plays SET reached_ms = 5 WHERE track_rowid = 1")
    assert updated.rowcount == 1
