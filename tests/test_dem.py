import math
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from dayhike import (
    Catalog,
    StaleSimilarity,
    build_catalog,
    build_similarity,
    geometry_store_path_for,
    rank_dayhikes,
)
from dayhike.dem import (
    COPERNICUS,
    FABDEM,
    Bounds,
    DEFAULT_DEM_BOUNDS,
    _elevation_stats,
    fabdem_archive_id,
    fabdem_tile_id,
    fabdem_tile_url,
    fetch_dem,
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


def test_copernicus_tile_url_points_at_the_aws_open_data_cog() -> None:
    assert COPERNICUS.tile_url("N45E015") == (
        "https://copernicus-dem-30m.s3.amazonaws.com/"
        "Copernicus_DSM_COG_10_N45_00_E015_00_DEM/Copernicus_DSM_COG_10_N45_00_E015_00_DEM.tif"
    )
    assert COPERNICUS.tile_url("S09W072").endswith(
        "Copernicus_DSM_COG_10_S09_00_W072_00_DEM/Copernicus_DSM_COG_10_S09_00_W072_00_DEM.tif"
    )
    assert FABDEM.tile_url("N45E015") == fabdem_tile_url("N45E015")


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


def test_fetch_fabdem_raises_on_a_non_404_download_error(tmp_path: Path) -> None:
    from urllib.error import URLError

    def broken_for_one(url: str, filename: str) -> None:
        if url == fabdem_tile_url("N45E016"):
            raise URLError("connection reset")
        Path(filename).write_bytes(b"tif-bytes")

    with pytest.raises(RuntimeError, match="N45E016"):
        fetch_fabdem(
            tmp_path,
            Bounds(south=45.1, west=15.5, north=45.2, east=16.1),
            retrieve=broken_for_one,
        )

    assert (tmp_path / "N45E015_FABDEM_V1-2.tif").read_bytes() == b"tif-bytes"


def test_fetch_dem_copernicus_skips_tiles_glo30_public_leaves_out(tmp_path: Path) -> None:
    # N40E043 (Armenia) is one of the GLO-30 tiles not released to the public.
    recorded: list[str] = []

    def fake_retrieve(url: str, filename: str) -> None:
        recorded.append(url)
        Path(filename).write_bytes(b"tif-bytes")

    paths = fetch_dem(
        tmp_path,
        Bounds(south=40.5, west=42.5, north=40.6, east=43.5),
        source=COPERNICUS,
        retrieve=fake_retrieve,
    )

    assert recorded == [COPERNICUS.tile_url("N40E042")]
    assert paths == [tmp_path / "Copernicus_DSM_COG_10_N40_00_E042_00_DEM.tif"]


def test_fetch_dem_copernicus_skips_sea_tiles_the_bucket_lacks(tmp_path: Path) -> None:
    def missing(_url: str, _filename: str) -> None:
        raise FileNotFoundError("404")

    paths = fetch_dem(
        tmp_path, Bounds(south=43.0, west=14.0, north=43.1, east=14.1), source=COPERNICUS, retrieve=missing
    )

    assert paths == []
    assert list(tmp_path.iterdir()) == []


def test_fetch_dem_command_takes_the_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dayhike import dem
    from dayhike.cli import main

    recorded: list[str] = []

    def fake_download(url: str, filename: str) -> None:
        recorded.append(url)
        Path(filename).write_bytes(b"tif-bytes")

    monkeypatch.setattr(dem, "_download", fake_download)
    bounds = "45.1,15.1,45.2,15.2"

    assert main(["fetch-dem", "--source", "copernicus", "--dest", str(tmp_path / "c"), "--bounds", bounds]) == 0
    assert main(["fetch-dem", "--dest", str(tmp_path / "f"), "--bounds", bounds]) == 0

    assert recorded == [COPERNICUS.tile_url("N45E015"), FABDEM.tile_url("N45E015")]


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


def test_sample_elevations_reads_copernicus_tiles(tmp_path: Path) -> None:
    raster = tmp_path / "Copernicus_DSM_COG_10_N45_00_E015_00_DEM.tif"
    _write_constant_tif(raster, west=14.99, south=44.99, east=16.01, north=46.01, z=987.0)

    assert sample_elevations(tmp_path, [(45.2, 15.1), (47.0, 11.0)]) == [pytest.approx(987.0), None]


def test_a_point_the_tiles_grid_misses_is_uncovered_not_sea_level(tmp_path: Path) -> None:
    import rasterio
    from rasterio.transform import from_origin

    # GLO-30 cells are centred on whole arc-seconds and a tile drops its east column and
    # south row, so its grid stops half a cell short of the tile's east and south edges.
    # It has no nodata value: outside the grid a read gives 0.
    cell = 1 / 3600
    with rasterio.open(
        tmp_path / "Copernicus_DSM_COG_10_N45_00_E015_00_DEM.tif",
        "w",
        driver="GTiff",
        height=3600,
        width=3600,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(15 - cell / 2, 46 + cell / 2, cell, cell),
    ) as dataset:
        dataset.write(np.full((3600, 3600), 1500.0, dtype="float32"), 1)

    samples = sample_elevations(tmp_path, [(45.5, 15.5), (45.5, 15.99995), (45.00003, 15.5)])

    assert samples == [pytest.approx(1500.0), None, None]


def test_a_dem_directory_holds_one_source(tmp_path: Path) -> None:
    for name in ("N45E015_FABDEM_V1-2.tif", "Copernicus_DSM_COG_10_N45_00_E015_00_DEM.tif"):
        _write_constant_tif(tmp_path / name, west=14.99, south=44.99, east=16.01, north=46.01, z=1.0)

    with pytest.raises(ValueError, match="FABDEM and Copernicus GLO-30"):
        sample_elevations(tmp_path, [(45.2, 15.1)])


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


def _geometry_meta(catalog_path: Path) -> dict[str, str]:
    connection = sqlite3.connect(geometry_store_path_for(catalog_path))
    try:
        return dict(connection.execute("SELECT key, value FROM geometry_meta"))
    finally:
        connection.close()


def test_sample_dem_from_copernicus_records_its_attribution(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    copernicus, fabdem = tmp_path / "copernicus", tmp_path / "fabdem"
    copernicus.mkdir()
    fabdem.mkdir()
    box = {"west": 14.99, "south": 44.99, "east": 16.01, "north": 46.01}
    _write_constant_tif(copernicus / "Copernicus_DSM_COG_10_N45_00_E015_00_DEM.tif", **box, z=1410.0)
    _write_constant_tif(fabdem / "N45E015_FABDEM_V1-2.tif", **box, z=1400.0)

    assert sample_dem(catalog_path, copernicus) > 0

    valley = next(hike for hike in Catalog.open(catalog_path).dayhikes if hike.name == "Valley Path")
    assert valley.feature_vector.elevation_min_m == pytest.approx(1410.0)
    # The notices Art. 6 of the Copernicus DEM licence asks for.
    attribution = _geometry_meta(catalog_path)["copernicus_attribution"]
    assert "Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018" in attribution
    assert "do not incur any liability" in attribution

    sample_dem(catalog_path, fabdem)

    assert "copernicus_attribution" not in _geometry_meta(catalog_path)


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


def test_sample_dem_removes_similarity_facts(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    directory = build_similarity(catalog_path, tmp_path / "dem")
    raster_dir = tmp_path / "sample_dem"
    raster_dir.mkdir()
    _write_constant_tif(
        raster_dir / "N45E015_FABDEM_V1-2.tif",
        west=14.99, south=44.99, east=16.01, north=46.01, z=1400.0,
    )
    messages: list[str] = []

    assert sample_dem(catalog_path, raster_dir, progress=messages.append) > 0

    assert not directory.exists()
    assert messages[-1] == (
        f"removed stale similarity facts {directory}; run `dayhike build-similarity`"
    )
    with pytest.raises(StaleSimilarity, match="build-similarity"):
        rank_dayhikes(Catalog.open(catalog_path), ["Ridge Walk"])


def test_catalog_gain_uses_the_similarity_gain_rule() -> None:
    # Vertices 30 m apart due north: jitter under 10 m and a one-vertex DEM spike add nothing.
    step = 30 / 111_195
    elevations = [400.0, 405.0, 398.0, 460.0, 402.0, 404.0, 430.0]
    vertices = [(45 + i * step, 15.0, ele) for i, ele in enumerate(elevations)]

    assert _elevation_stats(vertices) == (398.0, 460.0, pytest.approx(30.0))
    assert _elevation_stats([(45.0, 15.0, math.nan), *vertices[:2]]) == (400.0, 405.0, 0.0)
