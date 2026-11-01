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


def test_a_point_the_tiles_grid_misses_reads_its_edge_cell_not_sea_level(tmp_path: Path) -> None:
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
        heights = np.full((3600, 3600), 1500.0, dtype="float32")
        heights[:, -1] = 1600.0
        heights[-1, :] = 1700.0
        dataset.write(heights, 1)

    samples = sample_elevations(tmp_path, [(45.5, 15.5), (45.5, 15.99995), (45.00003, 15.5)])

    assert samples == [pytest.approx(1500.0), pytest.approx(1600.0), pytest.approx(1700.0)]


def test_a_fabdem_point_outside_the_tiles_grid_stays_uncovered(tmp_path: Path) -> None:
    import rasterio
    from rasterio.transform import from_origin

    cell = 1 / 3600
    with rasterio.open(
        tmp_path / "N45E015_FABDEM_V1-2.tif",
        "w",
        driver="GTiff",
        height=3600,
        width=3600,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(15 - cell / 2, 46 + cell / 2, cell, cell),
        nodata=-9999.0,
    ) as dataset:
        dataset.write(np.full((3600, 3600), 1500.0, dtype="float32"), 1)

    samples = sample_elevations(tmp_path, [(45.5, 15.5), (45.5, 15.99995), (45.00003, 15.5)])

    assert samples == [pytest.approx(1500.0), None, None]


CELL = 1 / 3600  # a GLO-30 cell, in degrees
CANOPY_LAT = 45.5 - CELL / 2  # the centres of one row of cells


def _east_m(lon: np.ndarray) -> np.ndarray:
    """Metres east of 15.5, where forest gives way to open land."""
    return (lon - 15.5) * 111_195 * math.cos(math.radians(45.5))


def _ground(lon: np.ndarray) -> np.ndarray:
    """A 40 m hill and a 30 m dip in the forest west of 15.5; flat land east of it."""
    x = _east_m(lon)
    return 500 + 40 * np.exp(-(((x + 700) / 200) ** 2)) - 30 * np.exp(-(((x + 300) / 200) ** 2))


def _write_canopy_tiles(root: Path, dem_file: str) -> tuple[Path, Path]:
    """GLO-30-like cells of the ground with 18 m tree tops on half of them, in
    2 x 2 cell clumps. WorldCover maps forest west of 15.5 and grassland east
    of it, where the same clumps stand for rocks and roofs."""
    import rasterio
    from rasterio.transform import from_origin

    from test_landcover import _write_class_tif

    dem, landcover = root / "dem", root / "landcover"
    dem.mkdir()
    landcover.mkdir()
    rows, cols = np.mgrid[0:72, 0:216]
    heights = _ground(15.47 + (cols + 0.5) * CELL) + np.where((rows // 2 + cols // 2) % 2 == 0, 18.0, 0.0)
    with rasterio.open(
        dem / dem_file, "w", driver="GTiff", height=72, width=216, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(15.47, 45.51, CELL, CELL),
    ) as dataset:
        dataset.write(heights.astype("float32"), 1)
    codes = np.where(np.arange(720) < 360, 10, 30)[None, :].repeat(240, axis=0)
    _write_class_tif(
        landcover / "ESA_WorldCover_10m_2021_v200_N45E015_Map.tif",
        west=15.47, south=45.49, east=15.53, north=45.51, codes=codes,
    )
    return dem, landcover


def _canopy_walk() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """One point per cell along the row, from 1.2 km into the forest to 1.2 km into the open."""
    lon = 15.47 + (np.arange(216) + 0.5) * CELL
    lon = lon[np.abs(_east_m(lon)) <= 1_200]
    return np.full(len(lon), CANOPY_LAT), lon, _east_m(lon)


def test_copernicus_under_trees_reads_the_ground_below_the_canopy(tmp_path: Path) -> None:
    from dayhike.similarity import elevation_gain

    dem, landcover = _write_canopy_tiles(tmp_path, "Copernicus_DSM_COG_10_N45_00_E015_00_DEM.tif")
    lat, lon, x = _canopy_walk()
    points = list(zip(lat.tolist(), lon.tolist()))

    raw = np.array(sample_elevations(dem, points))
    floor = np.array(sample_elevations(dem, points, landcover_dir=landcover))

    forest, open_land = x < -150, x > 150
    ground = _ground(lon)
    # The hill and the dip stay; the tree tops go.
    assert np.abs(raw[forest] - ground[forest]).max() == pytest.approx(18.0, abs=0.01)
    assert np.abs(floor[forest] - ground[forest]).max() < 4.0
    assert elevation_gain(lat[forest], lon[forest], raw[forest]) > elevation_gain(
        lat[forest], lon[forest], ground[forest]
    ) + 100
    assert elevation_gain(lat[forest], lon[forest], floor[forest]) == pytest.approx(
        elevation_gain(lat[forest], lon[forest], ground[forest]), abs=5.0
    )
    # Open land keeps every reading as it was, rocks and roofs included.
    assert floor[open_land].tolist() == raw[open_land].tolist()
    assert (floor <= raw + 4.0).all()


def test_fabdem_under_trees_is_read_as_it_is(tmp_path: Path) -> None:
    dem, landcover = _write_canopy_tiles(tmp_path, "N45E015_FABDEM_V1-2.tif")
    lat, lon, _ = _canopy_walk()
    points = list(zip(lat.tolist(), lon.tolist()))

    assert sample_elevations(dem, points, landcover_dir=landcover) == sample_elevations(dem, points)


def test_copernicus_forest_at_the_grid_edge_keeps_the_slope(tmp_path: Path) -> None:
    import rasterio
    from rasterio.transform import from_origin

    from test_landcover import _write_class_tif

    dem, landcover = tmp_path / "dem", tmp_path / "landcover"
    dem.mkdir()
    landcover.mkdir()
    # A 50 % slope rising to the north-east, under forest with no tree tops.
    rows, cols = np.mgrid[0:40, 0:40]
    heights = 800 + 0.5 * 30.9 * (cols - rows)
    with rasterio.open(
        dem / "Copernicus_DSM_COG_10_N45_00_E015_00_DEM.tif", "w", driver="GTiff", height=40, width=40,
        count=1, dtype="float32", crs="EPSG:4326", transform=from_origin(15.0, 46.0, CELL, CELL),
    ) as dataset:
        dataset.write(heights.astype("float32"), 1)
    _write_class_tif(
        landcover / "ESA_WorldCover_10m_2021_v200_N45E015_Map.tif",
        west=15.0, south=45.98, east=15.02, north=46.0, codes=np.full((240, 240), 10),
    )
    centre = (np.arange(40) + 0.5) * CELL
    points = [(46.0 - centre[r], 15.0 + centre[c]) for r, c in [(0, 0), (0, 20), (20, 0), (39, 39), (39, 20), (20, 39)]]

    raw = sample_elevations(dem, points)
    floor = sample_elevations(dem, points, landcover_dir=landcover)

    assert floor == pytest.approx(raw, abs=0.01)


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


def _write_grassland(root: Path) -> Path:
    """A WorldCover tile of grassland over the fixture's hikes."""
    from test_landcover import _write_class_tif

    landcover = root / "landcover"
    landcover.mkdir()
    _write_class_tif(
        landcover / "ESA_WorldCover_10m_2021_v200_N45E015_Map.tif",
        west=14.99, south=44.99, east=16.01, north=46.01, codes=np.full((8, 8), 30),
    )
    return landcover


def test_sample_dem_from_copernicus_records_its_attribution(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    copernicus, fabdem = tmp_path / "copernicus", tmp_path / "fabdem"
    copernicus.mkdir()
    fabdem.mkdir()
    box = {"west": 14.99, "south": 44.99, "east": 16.01, "north": 46.01}
    _write_constant_tif(copernicus / "Copernicus_DSM_COG_10_N45_00_E015_00_DEM.tif", **box, z=1410.0)
    _write_constant_tif(fabdem / "N45E015_FABDEM_V1-2.tif", **box, z=1400.0)

    assert sample_dem(catalog_path, copernicus, landcover_dir=_write_grassland(tmp_path)) > 0

    valley = next(hike for hike in Catalog.open(catalog_path).dayhikes if hike.name == "Valley Path")
    assert valley.feature_vector.elevation_min_m == pytest.approx(1410.0)
    # The notices Art. 6 of the Copernicus DEM licence asks for.
    attribution = _geometry_meta(catalog_path)["copernicus_attribution"]
    assert "Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018" in attribution
    assert "do not incur any liability" in attribution

    sample_dem(catalog_path, fabdem)

    assert "copernicus_attribution" not in _geometry_meta(catalog_path)


def test_sample_dem_from_copernicus_needs_worldcover_for_the_canopy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from dayhike.cli import main

    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    copernicus = tmp_path / "copernicus"
    copernicus.mkdir()
    _write_constant_tif(
        copernicus / "Copernicus_DSM_COG_10_N45_00_E015_00_DEM.tif",
        west=14.99, south=44.99, east=16.01, north=46.01, z=1410.0,
    )

    for landcover_dir in (None, tmp_path / "no-landcover"):
        with pytest.raises(ValueError, match="WorldCover"):
            sample_dem(catalog_path, copernicus, landcover_dir=landcover_dir)
    with pytest.raises(SystemExit):
        main(["sample-dem", "--catalog", str(catalog_path), "--dem", str(copernicus), "--landcover", str(tmp_path)])
    assert "needs WorldCover tiles" in capsys.readouterr().err


def test_sample_dem_writes_the_copernicus_canopy_floor(tmp_path: Path) -> None:
    import rasterio
    from rasterio.transform import from_origin

    from test_landcover import _write_class_tif

    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    copernicus, landcover = tmp_path / "copernicus", tmp_path / "landcover"
    copernicus.mkdir()
    landcover.mkdir()
    # The Ridge Walk runs north along 15.0 from 45.0; a forest of 2 x 2 cell clumps 18 m tall.
    rows, cols = np.mgrid[0:396, 0:72]
    heights = 1400.0 + np.where((rows // 2 + cols // 2) % 2 == 0, 18.0, 0.0)
    with rasterio.open(
        copernicus / "Copernicus_DSM_COG_10_N45_00_E015_00_DEM.tif", "w", driver="GTiff", height=396,
        width=72, count=1, dtype="float32", crs="EPSG:4326", transform=from_origin(14.99, 45.1, CELL, CELL),
    ) as dataset:
        dataset.write(heights.astype("float32"), 1)
    _write_class_tif(
        landcover / "ESA_WorldCover_10m_2021_v200_N45E015_Map.tif",
        west=14.99, south=44.99, east=15.02, north=45.11, codes=np.full((1440, 360), 10),
    )

    assert sample_dem(catalog_path, copernicus, landcover_dir=landcover) > 0

    connection = sqlite3.connect(geometry_store_path_for(catalog_path))
    try:
        rows = connection.execute(
            "SELECT v.latitude, v.longitude, v.ele FROM vertices v JOIN dayhikes d ON d.id = v.dayhike_id "
            "WHERE d.name = 'Ridge Walk' ORDER BY v.seq"
        ).fetchall()
    finally:
        connection.close()
    points = [(lat, lon) for lat, lon, _ in rows]
    assert [ele for _, _, ele in rows] == sample_elevations(copernicus, points, landcover_dir=landcover)
    assert all(ele == pytest.approx(1400.0, abs=3.0) for _, _, ele in rows)  # the ground, not the tree tops


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
