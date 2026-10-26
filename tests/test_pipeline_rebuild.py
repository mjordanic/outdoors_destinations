"""The whole pipeline on the fixture, twice: a rebuild gives the same bytes.

Ingest tests/fixtures/catalog.osm, sample synthetic DEM, LandCover and climate
rasters, then build-similarity, in two separate directories. The slow
real-data checks (README "Clean rebuild") rely on this determinism: on the
same snapshot and the same uv.lock a rebuilt Catalog must equal the old one.
"""

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest
from rebuild_checks import CATALOG_SCHEMA, GEOMETRY_SCHEMA, schema
from test_climate import _write_climate_tifs

from dayhike import (
    build_catalog,
    build_similarity,
    geometry_store_path_for,
    sample_climate,
    sample_dem,
    sample_landcover,
    similarity_dir_for,
)

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
WORK_FILE_PATTERNS = ("*.work", "*.tmp", "*-wal", "*-shm", "*.writing.osm.pbf", "*.part", "*.clip")


def _write_tif(path: Path, data: np.ndarray, *, west: float, south: float, east: float, north: float) -> None:
    import rasterio
    from rasterio.transform import from_bounds

    height, width = data.shape
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype=data.dtype.name,
        crs="EPSG:4326",
        transform=from_bounds(west, south, east, north, width, height),
        nodata=0,
    ) as dataset:
        dataset.write(data, 1)


def _run_pipeline(root: Path) -> Path:
    root.mkdir()
    rows, cols = np.mgrid[0:64, 0:64]
    dem = root / "dem"
    dem.mkdir()
    _write_tif(
        dem / "N45E015_FABDEM_V1-2.tif",
        (300.0 + 7.0 * rows + 3.0 * cols).astype("float32"),
        west=15.0, south=45.0, east=16.0, north=46.0,
    )  # fmt: skip
    landcover = root / "landcover"
    landcover.mkdir()
    _write_tif(
        landcover / "ESA_WorldCover_10m_2021_v200_N45E015_Map.tif",
        np.choose((rows // 8 + cols // 8) % 4, [10, 20, 30, 60]).astype("uint8"),
        west=14.9, south=44.9, east=15.7, north=46.2,
    )  # fmt: skip
    climate = root / "climate"
    climate.mkdir()
    _write_climate_tifs(
        climate,
        west=14.9, south=44.9, east=15.7, north=46.2,
        tas=tuple(month - 2.5 for month in range(12)),
        pr=tuple(40.0 + 9.0 * month for month in range(12)),
        rsds=tuple(5_000.0 + 1_000.0 * month for month in range(12)),
    )  # fmt: skip
    catalog_path = root / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    assert sample_dem(catalog_path, dem) > 0
    assert sample_landcover(catalog_path, landcover) > 0
    assert sample_climate(catalog_path, climate) > 0
    build_similarity(catalog_path, dem)
    return catalog_path


@pytest.fixture(scope="module")
def two_runs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    base = tmp_path_factory.mktemp("rebuild")
    return _run_pipeline(base / "first"), _run_pipeline(base / "second")


def _rows(path: Path) -> dict[str, list[tuple]]:
    connection = sqlite3.connect(path)
    try:
        tables = schema(connection)
        return {
            table: connection.execute(
                f"SELECT * FROM {table} ORDER BY {', '.join(str(i + 1) for i in range(len(columns)))}"
            ).fetchall()
            for table, columns in tables.items()
        }
    finally:
        connection.close()


def _meta(directory: Path) -> dict:
    meta = json.loads((directory / "meta.json").read_text())
    return {key: value for key, value in meta.items() if not key.endswith("_seconds")}


def test_fixture_pipeline_twice_gives_identical_outputs(two_runs: tuple[Path, Path]) -> None:
    first, second = two_runs

    for name in ("catalog.sqlite", "catalog.geometry.sqlite"):
        old, new = _rows(first.parent / name), _rows(second.parent / name)
        assert old.keys() == new.keys()
        for table in old:
            assert old[table] == new[table], f"{name}:{table}"
    assert any(row[3] is not None for row in _rows(first)["climate_months"])
    first_store, second_store = similarity_dir_for(first), similarity_dir_for(second)
    arrays = sorted(path.name for path in first_store.glob("*.npy"))
    assert arrays == ["facts.npy", "ids.npy", "profile.npy", "quantiles.npy", "twins.npy"]
    for name in arrays:
        assert (first_store / name).read_bytes() == (second_store / name).read_bytes(), name
    assert _meta(first_store) == _meta(second_store)


def test_fixture_pipeline_leaves_no_work_files(two_runs: tuple[Path, Path]) -> None:
    for catalog_path in two_runs:
        left = [path for pattern in WORK_FILE_PATTERNS for path in catalog_path.parent.rglob(pattern)]
        assert left == []


def test_fixture_catalog_schema_is_the_documented_schema(two_runs: tuple[Path, Path]) -> None:
    catalog_path = two_runs[0]
    for path, expected in (
        (catalog_path, CATALOG_SCHEMA),
        (geometry_store_path_for(catalog_path), GEOMETRY_SCHEMA),
    ):
        connection = sqlite3.connect(path)
        try:
            assert schema(connection) == expected, path.name
        finally:
            connection.close()
