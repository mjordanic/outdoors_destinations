"""sample-dem and sample-landcover with worker processes (ADR-0044): the same
GeometryStore and Catalog as with one, the rasters read in the workers."""

import os
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from dayhike import build_catalog
from dayhike import dem, landcover
from dayhike.catalog import geometry_store_path_for
from dayhike.dem import sample_dem
from dayhike.landcover import sample_landcover
from dayhike.parallel import WORKERS_ENV
from test_dem import _write_constant_tif
from test_landcover import _write_class_tif

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"


def _rasters(root: Path) -> tuple[Path, Path]:
    dem_dir, landcover_dir = root / "dem", root / "landcover"
    dem_dir.mkdir()
    landcover_dir.mkdir()
    _write_constant_tif(dem_dir / "N45E015_FABDEM_V1-2.tif", west=14.99, south=44.99, east=16.01, north=46.01, z=1400.0)
    for name, south, north, code in (("a_south.tif", 44.99, 45.05, 10), ("b_north.tif", 45.05, 45.12, 30)):
        _write_class_tif(
            landcover_dir / name, west=14.99, south=south, east=15.05, north=north, codes=np.full((8, 8), code, dtype="uint8")
        )
    return dem_dir, landcover_dir


def _sampled(root: Path, workers: int, monkeypatch) -> list[list[str]]:
    monkeypatch.setenv(WORKERS_ENV, str(workers))
    root.mkdir()
    catalog_path = root / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    dem_dir, landcover_dir = _rasters(root)
    assert sample_dem(catalog_path, dem_dir) > 0
    assert sample_landcover(catalog_path, landcover_dir) > 0
    dumps = []
    for path in (catalog_path, geometry_store_path_for(catalog_path)):
        conn = sqlite3.connect(path)
        dumps.append(list(conn.iterdump()))
        conn.close()
    return dumps


def test_sampling_writes_the_same_files_with_any_worker_count(tmp_path, monkeypatch):
    monkeypatch.setattr(dem, "_VERTEX_CHUNK", 3)  # many chunks, so every worker reads some
    monkeypatch.setattr(landcover, "_VERTEX_CHUNK", 3)
    serial = _sampled(tmp_path / "serial", 1, monkeypatch)

    assert _sampled(tmp_path / "workers", 3, monkeypatch) == serial


@pytest.mark.parametrize(
    "module, reader, sample",
    [
        (dem, "sample_elevations", lambda catalog, dem_dir, landcover_dir: sample_dem(catalog, dem_dir)),
        (landcover, "_sample_open_datasets", lambda catalog, dem_dir, landcover_dir: sample_landcover(catalog, landcover_dir)),
    ],
)
def test_the_rasters_are_read_in_the_workers(tmp_path, monkeypatch, module, reader, sample):
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    dem_dir, landcover_dir = _rasters(tmp_path)
    read = tmp_path / "read"
    real = getattr(module, reader)

    def recorded(*args):
        with read.open("a") as out:
            out.write(f"{os.getpid()}\n")
        return real(*args)

    monkeypatch.setattr(module, reader, recorded)
    monkeypatch.setattr(module, "_VERTEX_CHUNK", 3)
    monkeypatch.setenv(WORKERS_ENV, "2")

    assert sample(catalog_path, dem_dir, landcover_dir) > 0

    pids = read.read_text().split()
    assert len(pids) > 1
    assert str(os.getpid()) not in pids


@pytest.mark.parametrize("command", ["sample-dem", "sample-landcover", "sample-trailgraph", "build-similarity"])
def test_the_cli_takes_the_worker_count(tmp_path, monkeypatch, command):
    from dayhike.cli import main
    from dayhike.parallel import workers

    monkeypatch.setenv(WORKERS_ENV, "1")  # recorded, so the value main() sets is undone
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    dem_dir, landcover_dir = _rasters(tmp_path)
    rasters = {
        "sample-dem": ["--dem", str(dem_dir)],
        "sample-landcover": ["--landcover", str(landcover_dir)],
        "sample-trailgraph": ["--dem", str(dem_dir), "--landcover", str(landcover_dir), "--climate", str(tmp_path / "climate")],
    }

    main([command, "--catalog", str(catalog_path), *rasters.get(command, ["--dem", str(dem_dir), "--landcover", str(landcover_dir)]), "--workers", "3"])

    assert workers() == 3


def _similarity_files(root: Path, name: str, workers: int, monkeypatch) -> dict[str, np.ndarray]:
    from dayhike import build_similarity

    monkeypatch.setenv(WORKERS_ENV, str(workers))
    root.mkdir()
    catalog_path = root / "catalog.sqlite"
    build_catalog(Path(__file__).parent / "fixtures" / name, catalog_path)
    dem_dir, landcover_dir = _rasters(root)
    sample_dem(catalog_path, dem_dir)
    sample_landcover(catalog_path, landcover_dir)
    directory = build_similarity(catalog_path, dem_dir, landcover_dir=landcover_dir)
    return {path.name: np.load(path) for path in sorted(directory.glob("*.npy"))}


@pytest.mark.parametrize("name", ["catalog.osm", "long_way_back.osm", "node_network.osm", "long_tour.osm"])
def test_build_similarity_writes_the_same_facts_with_any_worker_count(tmp_path, monkeypatch, name):
    from dayhike import similarity

    monkeypatch.setattr(similarity, "_HIKE_CHUNK", 2)  # many chunks, so every worker measures some
    serial = _similarity_files(tmp_path / "serial", name, 1, monkeypatch)

    parallel = _similarity_files(tmp_path / "workers", name, 3, monkeypatch)

    assert parallel.keys() == serial.keys()
    for file, values in serial.items():
        assert np.array_equal(parallel[file], values, equal_nan=values.dtype.kind == "f"), file


def test_build_similarity_measures_the_hikes_in_the_workers(tmp_path, monkeypatch):
    from dayhike import build_similarity, similarity

    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    read = tmp_path / "read"
    real = similarity._walks

    def recorded(*args):
        with read.open("a") as out:
            out.write(f"{os.getpid()}\n")
        return real(*args)

    monkeypatch.setattr(similarity, "_walks", recorded)
    monkeypatch.setattr(similarity, "_HIKE_CHUNK", 1)
    monkeypatch.setenv(WORKERS_ENV, "2")

    build_similarity(catalog_path, tmp_path / "dem", landcover_dir=tmp_path / "landcover")

    pids = read.read_text().split()
    assert len(pids) > 1
    assert str(os.getpid()) not in pids


@pytest.mark.parametrize("sample, raster", [(sample_dem, 0), (sample_landcover, 1)])
def test_a_bad_worker_count_is_refused_before_the_store_is_touched(tmp_path, monkeypatch, sample, raster):
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    raster_dir = _rasters(tmp_path)[raster]
    monkeypatch.setenv(WORKERS_ENV, "0")

    with pytest.raises(ValueError, match=WORKERS_ENV):
        sample(catalog_path, raster_dir)
