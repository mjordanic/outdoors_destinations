"""The GeometryStore is written from the main walks ingest already stored
(``run_ingest`` returns them), not from walks assembled a second time."""

import sqlite3
from pathlib import Path

import pytest

import dayhike.ingest as ingest_module
from dayhike.ingest import _connect_work, run_ingest, write_geometry_store
from dayhike.parallel import WORKERS_ENV

FIXTURES = Path(__file__).parent / "fixtures"


def _dump(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


@pytest.mark.parametrize("name", ["catalog.osm", "node_network.osm", "long_tour.osm", "graded_paths.osm"])
@pytest.mark.parametrize("workers", [1, 3])
def test_the_store_from_the_stored_walks_is_the_store_from_assembled_walks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, workers: int
) -> None:
    monkeypatch.setenv(WORKERS_ENV, str(workers))
    kept, _, work_path, snapshot, walks = run_ingest([FIXTURES / name], tmp_path / "catalog.sqlite")
    conn = _connect_work(work_path)
    try:
        write_geometry_store(tmp_path / "assembled.sqlite", kept, conn, snapshot)
        write_geometry_store(tmp_path / "stored.sqlite", kept, conn, snapshot, walks=walks)
    finally:
        conn.close()

    assert _dump(tmp_path / "stored.sqlite") == _dump(tmp_path / "assembled.sqlite")


def test_the_store_assembles_no_main_walk_that_ingest_stored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WORKERS_ENV, "1")
    kept, _, work_path, snapshot, walks = run_ingest([FIXTURES / "catalog.osm"], tmp_path / "catalog.sqlite")
    assert walks
    assembled: list[int] = []
    main_walk = ingest_module._main_walk
    monkeypatch.setattr(
        ingest_module, "_main_walk", lambda relation, *args: assembled.append(relation.id) or main_walk(relation, *args)
    )
    conn = _connect_work(work_path)
    try:
        write_geometry_store(tmp_path / "stored.sqlite", kept, conn, snapshot, walks=walks)
        assert assembled == []
        write_geometry_store(tmp_path / "assembled.sqlite", kept, conn, snapshot)
    finally:
        conn.close()

    assert sorted(assembled) == sorted(hike.id for hike in kept if hike.id in walks)
