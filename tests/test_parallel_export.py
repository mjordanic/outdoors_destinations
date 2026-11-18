"""export-serving and count-commons with worker processes (ADR-0044): the same
files as with one, the per-hike chunks computed in the workers and written in order."""

import os
import sqlite3
from pathlib import Path

import pytest

from dayhike import build_catalog, build_similarity, commons, serving
from dayhike.catalog import geometry_store_path_for
from dayhike.commons import count_commons
from dayhike.parallel import WORKERS_ENV
from dayhike.serving import export_serving
from test_commons import file_page, geotag, image, write_dumps

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"


@pytest.fixture(scope="module")
def catalog_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("parallel-export")
    path = directory / "catalog.sqlite"
    build_catalog(FIXTURE, path)
    build_similarity(path, directory / "dem")
    return path


def _dump(path: Path) -> list[str]:
    db = sqlite3.connect(path)
    try:
        return list(db.iterdump())
    finally:
        db.close()


def _exported(catalog_path: Path, out: Path, workers: int, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    monkeypatch.setenv(WORKERS_ENV, str(workers))
    export_serving(catalog_path, out)
    return {
        "hikes.sqlite": _dump(out / "hikes.sqlite"),
        "trailgraph.sqlite": _dump(out / "trailgraph.sqlite"),
        "landmarks.sqlite": _dump(out / "landmarks.sqlite"),
        "trailheads.npy": (out / "trailheads.npy").read_bytes(),
    }


def test_export_serving_writes_the_same_files_with_any_worker_count(
    catalog_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(serving, "_HIKE_CHUNK", 2)  # many chunks, so every worker has some
    serial = _exported(catalog_path, tmp_path / "serial", 1, monkeypatch)

    assert len(serial["hikes.sqlite"]) > 10
    assert _exported(catalog_path, tmp_path / "workers", 3, monkeypatch) == serial


def test_export_serving_builds_its_hike_chunks_in_the_workers(
    catalog_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    read = tmp_path / "read"
    real = serving._vertices

    def recorded(*args):
        with read.open("a") as out:
            out.write(f"{os.getpid()}\n")
        return real(*args)

    monkeypatch.setattr(serving, "_vertices", recorded)
    monkeypatch.setattr(serving, "_HIKE_CHUNK", 2)
    monkeypatch.setenv(WORKERS_ENV, "2")

    export_serving(catalog_path, tmp_path / "out")

    pids = read.read_text().split()
    assert len(set(pids)) > 1
    assert str(os.getpid()) not in pids


def _counted(catalog_path: Path, dumps: Path, directory: Path, workers: int, monkeypatch: pytest.MonkeyPatch):
    directory.mkdir()
    copy = directory / "catalog.sqlite"
    for source, target in ((catalog_path, copy), (geometry_store_path_for(catalog_path), geometry_store_path_for(copy))):
        source_db, target_db = sqlite3.connect(source), sqlite3.connect(target)
        source_db.backup(target_db)
        source_db.close()
        target_db.close()
    monkeypatch.setenv(WORKERS_ENV, str(workers))
    counted = count_commons(copy, dumps)
    return counted, _dump(copy)


def test_count_commons_writes_the_same_counts_with_any_worker_count(
    catalog_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    geo = sqlite3.connect(geometry_store_path_for(catalog_path))
    try:
        spots = [
            (lat + 0.0003, lon + 0.00011)
            for lat, lon in geo.execute("SELECT latitude, longitude FROM vertices WHERE seq = 0 ORDER BY dayhike_id")
        ]
    finally:
        geo.close()
    dumps = write_dumps(
        tmp_path / "dumps",
        geo_tags=[geotag(i, 100 + i, *spot) for i, spot in enumerate(spots)],
        pages=[file_page(100 + i, f"Photo_{i}.jpg") for i in range(len(spots))],
        images=[image(f"Photo_{i}.jpg", 900 + i % 2) for i in range(len(spots))],
    )
    monkeypatch.setattr(commons, "_HIKE_CHUNK", 1)  # a chunk per hike
    serial_count, serial = _counted(catalog_path, dumps, tmp_path / "serial", 1, monkeypatch)

    workers_count, workers = _counted(catalog_path, dumps, tmp_path / "workers", 3, monkeypatch)

    assert serial_count > 1
    assert any(isinstance(line, str) and "commons_contributors" in line for line in serial)
    assert (workers_count, workers) == (serial_count, serial)


@pytest.mark.parametrize("command", ["export-serving", "count-commons"])
def test_the_cli_takes_the_worker_count(
    command: str, catalog_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dayhike.cli import main

    monkeypatch.setenv(WORKERS_ENV, "1")  # recorded, so the value main() sets is undone
    seen = []

    def run(*args, **kwargs):
        seen.append(os.environ[WORKERS_ENV])
        return tmp_path

    # export-serving imports its function when it runs; count-commons is imported at the top.
    monkeypatch.setattr("dayhike.serving.export_serving" if command == "export-serving" else "dayhike.cli.count_commons", run)
    extra = ["--out", str(tmp_path / "o")] if command == "export-serving" else []

    main([command, "--catalog", str(catalog_path), "--workers", "3", *extra])

    assert seen == ["3"]
