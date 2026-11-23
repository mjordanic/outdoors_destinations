"""SQLite connections that close when their block does."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


# How long a writer waits for another process's write lock before failing with "database is
# locked": steps that write the Catalog or the GeometryStore may run at the same time
# (docs/technical.md, "Running steps at the same time"), each holding the lock only for a
# transaction, the longest a few minutes.
BUSY_TIMEOUT_S = 1800


def writer(path: Path | str) -> sqlite3.Connection:
    """A connection to a database other processes may write too: it waits ``BUSY_TIMEOUT_S`` for a lock."""
    return sqlite3.connect(path, timeout=BUSY_TIMEOUT_S)


@contextmanager
def scoped(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """``with connection:`` (commit, or roll back on an error) and then close it.

    ``with sqlite3.connect(...) as db:`` leaves the connection, and its file
    descriptor, open until the garbage collector finds it.
    """
    try:
        with connection:
            yield connection
    finally:
        connection.close()
