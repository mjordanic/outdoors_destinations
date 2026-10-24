import sqlite3
from pathlib import Path

import numpy as np
import pytest

from dayhike import Catalog, build_catalog, geometry_store_path_for
from dayhike.dem import (
    Bounds,
    DEFAULT_DEM_BOUNDS,
    fabdem_archive_id,
    fabdem_tile_id,
    fabdem_tile_url,
    fetch_fabdem,
    sample_elevations,
    sample_dem,
    tiles_covering,
)

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"


def test_fabdem_tile_id_uses_southwest_corner() -> None:
    assert fabdem_tile_id(45.0, 15.0) == "N45E015"
    assert fabdem_tile_id(45.9, 15.9) == "N45E015"
    assert fabdem_tile_id(46.0, 16.0) == "N46E016"
    assert fabdem_tile_id(9.1, 5.2) == "N09E005"


def test_fabdem_archive_groups_ten_degree_blocks() -> None:
    assert fabdem_archive_id("N45E015") == "N40E010-N50E020_FABDEM_V1-2"
    assert fabdem_archive_id("N46E009") == "N40E000-N50E010_FABDEM_V1-2"
    assert fabdem_archive_id("N50E015") == "N50E010-N60E020_FABDEM_V1-2"


def test_fabdem_tile_url_points_at_huggingface_one_degree_geotiff() -> None:
    url = fabdem_tile_url("N45E015")
    assert url.startswith("https://huggingface.co/datasets/pavelzaitsau/fabdem-v12/resolve/main/tiles/")
    assert url.endswith("N40E010-N50E020_FABDEM_V1-2/N45E015_FABDEM_V1-2.tif")


def test_tiles_covering_default_prd_bounds_include_croatia_and_switzerland() -> None:
    tiles = tiles_covering(DEFAULT_DEM_BOUNDS)
    assert "N45E015" in tiles
    assert "N46E007" in tiles
    assert "N47E011" in tiles
    assert tiles == sorted(set(tiles))


def test_fetch_fabdem_downloads_missing_tiles_and_skips_existing(tmp_path: Path) -> None:
    recorded: list[str] = []
    dest = tmp_path / "dem"
    dest.mkdir()
    existing = dest / "N45E015_FABDEM_V1-2.tif"
    existing.write_bytes(b"already-there")

    def fake_retrieve(url: str, filename: str) -> None:
        recorded.append(url)
        Path(filename).write_bytes(b"tif-bytes")

    paths = fetch_fabdem(
        dest,
        Bounds(south=45.1, west=15.5, north=45.2, east=16.1),
        retrieve=fake_retrieve,
    )

    assert existing.read_bytes() == b"already-there"
    assert existing in paths
    fetched = [path for path in paths if path != existing]
    assert fetched == [dest / "N45E016_FABDEM_V1-2.tif"]
    assert recorded == [fabdem_tile_url("N45E016")]
    assert fetched[0].read_bytes() == b"tif-bytes"


def test_fetch_fabdem_skips_missing_remote_tiles(tmp_path: Path) -> None:
    def missing(_url: str, _filename: str) -> None:
        raise FileNotFoundError("404")

    paths = fetch_fabdem(
        tmp_path,
        Bounds(south=45.0, west=15.0, north=45.1, east=15.1),
        retrieve=missing,
    )

    assert paths == []
    assert not (tmp_path / "N45E015_FABDEM_V1-2.tif").exists()


def _write_constant_tif(
    path: Path, *, west: float, south: float, east: float, north: float, z: float
) -> None:
    import rasterio
    from rasterio.transform import from_bounds

    height = width = 8
    transform = from_bounds(west, south, east, north, width, height)
    data = np.full((height, width), z, dtype="float32")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
        nodata=-9999.0,
    ) as dataset:
        dataset.write(data, 1)


def test_sample_elevations_reads_covering_raster_and_skips_uncovered(
    tmp_path: Path,
) -> None:
    raster = tmp_path / "N45E015_FABDEM_V1-2.tif"
    _write_constant_tif(raster, west=14.99, south=44.99, east=16.01, north=46.01, z=1234.5)

    samples = sample_elevations(
        tmp_path,
        [(45.2, 15.1), (47.0, 11.0)],
    )

    assert samples[0] == pytest.approx(1234.5, abs=0.01)
    assert samples[1] is None


def test_sample_geometry_store_writes_dem_ele_and_catalog_feature_vectors(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    catalog = build_catalog(FIXTURE, catalog_path)
    valley = next(hike for hike in catalog.dayhikes if hike.name == "Valley Path")
    assert valley.feature_vector.elevation_min_m is None

    raster = tmp_path / "N45E015_FABDEM_V1-2.tif"
    _write_constant_tif(raster, west=14.99, south=44.99, east=16.01, north=46.01, z=1400.0)

    sampled = sample_dem(catalog_path, tmp_path)

    assert sampled > 0
    catalog = Catalog.open(catalog_path)
    valley = next(hike for hike in catalog.dayhikes if hike.name == "Valley Path")
    ridge = next(hike for hike in catalog.dayhikes if hike.name == "Ridge Walk")
    assert valley.feature_vector.elevation_min_m == pytest.approx(1400.0)
    assert valley.feature_vector.elevation_max_m == pytest.approx(1400.0)
    assert valley.feature_vector.elevation_gain_m == pytest.approx(0.0)
    assert ridge.feature_vector.elevation_min_m == pytest.approx(1400.0)

    store_path = geometry_store_path_for(catalog_path)
    assert not Path(str(store_path) + "-wal").exists()
    assert not Path(str(store_path) + "-shm").exists()
    connection = sqlite3.connect(store_path)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    finally:
        connection.close()


def test_sample_dem_without_rasters_keeps_osm_ele(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)

    assert sample_dem(catalog_path, tmp_path) == 0

    catalog = Catalog.open(catalog_path)
    ridge = next(hike for hike in catalog.dayhikes if hike.name == "Ridge Walk")
    valley = next(hike for hike in catalog.dayhikes if hike.name == "Valley Path")
    assert ridge.feature_vector.elevation_min_m == 200.0
    assert ridge.feature_vector.elevation_gain_m == 450.0
    assert valley.feature_vector.elevation_min_m is None
