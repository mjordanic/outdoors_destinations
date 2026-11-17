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
