"""Connection handling for library.sqlite3.

`library` is the only module with write access to a database. Everything else
asks it.
"""

import sqlite3
import threading
from pathlib import Path

from .schema import migrate


class Rows:
    """The rows a statement produced, already read.

    `execute()` returns this instead of a live cursor because the connection is
    shared across FastAPI's thread pool. A cursor is lazy — the work happens
    while it is iterated — so handing one back would move the database access
    outside the lock that is supposed to be protecting it, which is not
    protection at all.

    Materialising is affordable here and only here: every query in this
    application is bounded by a 723-row library. A query that could return an
    unbounded result set would need a different answer.
    """

    __slots__ = ("_rows", "lastrowid", "rowcount")

    def __init__(self, rows: list, lastrowid: int | None, rowcount: int):
        self._rows = rows
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list:
        return self._rows

    def __iter__(self):
        return iter(self._rows)

    def __len__(self) -> int:
        return len(self._rows)


class SharedConnection(sqlite3.Connection):
    """One connection, used from many threads, one statement at a time.

    FastAPI runs sync endpoints in a thread pool, and this app holds a single
    connection for the process lifetime. Python's `sqlite3` will happily let
    two threads interleave on one connection and the result is
    `InterfaceError: bad parameter or other API misuse` — an error that only
    appears under real concurrency, which is to say on the day the screen
    finally asks for a dozen covers at once.

    A lock rather than a pool: at one-listener volumes the contention is
    nothing, and a pool would mean deciding which connection owns the write.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = threading.RLock()

    def execute(self, sql: str, parameters=()) -> Rows:  # type: ignore[override]
        with self._lock:
            cursor = super().execute(sql, parameters)
            try:
                # Empty for anything that is not a SELECT, which is what the
                # callers of INSERT already assume.
                rows = cursor.fetchall()
            finally:
                lastrowid, rowcount = cursor.lastrowid, cursor.rowcount
                cursor.close()
        return Rows(rows, lastrowid, rowcount)


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (creating if absent) the library database, migrated and ready.

    WAL mode with a single writer: at one-listener volumes there is no
    contention to manage, and WAL means a reader is never blocked by the
    import running at startup. Foreign keys on, because `plays` referencing a
    track that does not exist is the kind of corruption that only shows up
    when the data has become irreplaceable.
    """
    # A fresh data dir has nothing in it yet; the DB is the first thing opened.
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False because FastAPI runs sync endpoints in a thread
    # pool; `SharedConnection` is what makes that safe.
    conn = sqlite3.connect(
        str(path),
        isolation_level=None,
        check_same_thread=False,
        factory=SharedConnection,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    # A reader that arrives mid-write waits instead of raising. WAL makes this
    # rare; the import at startup is when it is not.
    conn.execute("PRAGMA busy_timeout=5000")
    migrate(conn)
    return conn
