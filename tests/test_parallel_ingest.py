"""Ingest with worker processes (ADR-0044): the same Catalog, GeometryStore and
TrailGraph as with one, built partly in the workers."""

import os
import sqlite3
from pathlib import Path

import pytest

import dayhike.ingest as ingest_module
import dayhike.network_hikes as network_hikes_module
import dayhike.similarity as similarity_module
from dayhike import build_catalog
from dayhike.catalog import geometry_store_path_for, trailgraph_path_for
from dayhike.cli import main
from dayhike.parallel import WORKERS_ENV, workers

FIXTURES = Path(__file__).parent / "fixtures"


def _dump(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def _ingest(tmp_path: Path, name: str, count: int, monkeypatch) -> list[list[str]]:
    monkeypatch.setenv(WORKERS_ENV, str(count))
    catalog_path = tmp_path / f"{name}-{count}" / "catalog.sqlite"
    build_catalog(FIXTURES / name, catalog_path)
    return [
        _dump(path)
        for path in (catalog_path, geometry_store_path_for(catalog_path), trailgraph_path_for(catalog_path))
    ]


@pytest.mark.parametrize("name", ["catalog.osm", "node_network.osm", "long_tour.osm", "graded_paths.osm"])
def test_ingest_builds_the_same_files_with_any_worker_count(tmp_path, monkeypatch, name):
    serial = _ingest(tmp_path, name, 1, monkeypatch)

    assert _ingest(tmp_path, name, 3, monkeypatch) == serial


def _record_pids(monkeypatch, module, name: str, log: Path) -> None:
    original = getattr(module, name)

    def recorded(*args, **kwargs):
        with log.open("a") as out:
            out.write(f"{os.getpid()}\n")
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, recorded)


@pytest.mark.parametrize(
    "fixture, module, name",
    [
        # relation DayHikes, and their GeometryStore rows
        ("catalog.osm", ingest_module, "_main_walk"),
        # built DayHikes from their walks
        ("long_tour.osm", ingest_module, "built_dayhike"),
        # NetworkHikes: each start's chains
        ("node_network.osm", network_hikes_module, "candidates"),
        # Twin checks of DayStages and NetworkHikes
        ("long_tour.osm", similarity_module, "are_twins"),
    ],
)
def test_ingest_work_runs_in_the_workers(tmp_path, monkeypatch, fixture, module, name):
    log = tmp_path / "pids"
    _record_pids(monkeypatch, module, name, log)
    monkeypatch.setenv(WORKERS_ENV, "2")

    build_catalog(FIXTURES / fixture, tmp_path / "catalog.sqlite")

    pids = log.read_text().split()
    assert pids
    assert str(os.getpid()) not in pids


def test_the_cli_runs_ingest_with_the_workers_it_is_given(tmp_path, monkeypatch):
    monkeypatch.setenv(WORKERS_ENV, "1")  # recorded, so the value main() sets is undone

    assert main(["ingest", str(FIXTURES / "catalog.osm"), "--catalog", str(tmp_path / "c.sqlite"), "--workers", "2"]) == 0

    assert workers() == 2
    assert (tmp_path / "c.sqlite").is_file()


def test_the_cli_refuses_a_worker_count_below_one_for_ingest(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(WORKERS_ENV, "1")  # recorded, so the value main() sets is undone

    with pytest.raises(SystemExit):
        main(["ingest", str(FIXTURES / "catalog.osm"), "--catalog", str(tmp_path / "c.sqlite"), "--workers", "0"])

    assert "--workers" in capsys.readouterr().err
    assert not (tmp_path / "c.sqlite").exists()
