import sqlite3
from pathlib import Path

import pytest

from dayhike.db import scoped


def test_a_scope_commits_what_it_wrote_and_closes_the_connection(tmp_path: Path) -> None:
    path = tmp_path / "a.sqlite"
    with scoped(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE t (x INTEGER)")
        db.execute("INSERT INTO t VALUES (1)")

    with pytest.raises(sqlite3.ProgrammingError):
        db.execute("SELECT 1")
    with scoped(sqlite3.connect(path)) as db:
        assert db.execute("SELECT x FROM t").fetchall() == [(1,)]


def test_a_scope_rolls_back_and_closes_when_the_block_fails(tmp_path: Path) -> None:
    path = tmp_path / "a.sqlite"
    with scoped(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE t (x INTEGER)")

    with pytest.raises(RuntimeError):
        with scoped(sqlite3.connect(path)) as db:
            db.execute("INSERT INTO t VALUES (1)")
            raise RuntimeError
    with pytest.raises(sqlite3.ProgrammingError):
        db.execute("SELECT 1")
    with scoped(sqlite3.connect(path)) as db:
        assert db.execute("SELECT COUNT(*) FROM t").fetchone() == (0,)


def test_a_writer_waits_for_another_writer_instead_of_failing(tmp_path: Path) -> None:
    import threading
    import time

    from dayhike.db import BUSY_TIMEOUT_S, writer

    path = tmp_path / "a.sqlite"
    with scoped(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE t (x INTEGER)")
    holder = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")

    def release() -> None:
        time.sleep(0.5)
        holder.execute("COMMIT")

    thread = threading.Thread(target=release)
    thread.start()
    with scoped(writer(path)) as db:
        assert db.execute("PRAGMA busy_timeout").fetchone() == (BUSY_TIMEOUT_S * 1000,)
        db.execute("INSERT INTO t VALUES (1)")
    thread.join()
    holder.close()

    with scoped(sqlite3.connect(path)) as db:
        assert db.execute("SELECT x FROM t").fetchall() == [(1,)]
