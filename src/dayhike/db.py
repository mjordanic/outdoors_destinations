"""SQLite connections that close when their block does."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager


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
