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
    sample_landcover,
)
from dayhike.dem import Bounds, DEFAULT_DEM_BOUNDS
from dayhike.landcover import (
    fetch_worldcover,
    worldcover_tile_id,
    worldcover_tile_url,
    worldcover_tiles_covering,
)

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
LANDCOVER_FRACTIONS = (
    "tree_fraction",
    "shrub_fraction",
    "grass_fraction",
    "bare_fraction",
    "snow_fraction",
)


def test_ingest_creates_empty_landcover_and_does_not_need_a_raster(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    catalog = build_catalog(FIXTURE, catalog_path)

    store = sqlite3.connect(geometry_store_path_for(catalog_path))
    catalog_db = sqlite3.connect(catalog_path)
    try:
        vertex_columns = {
            row[1] for row in store.execute("PRAGMA table_info(vertices)")
        }
        assert "landcover" in vertex_columns
        unset = store.execute(
            "SELECT COUNT(*) FROM vertices WHERE landcover IS NOT NULL"
        ).fetchone()[0]
        assert unset == 0

        feature_columns = {
            row[1] for row in catalog_db.execute("PRAGMA table_info(feature_vectors)")
        }
        assert set(LANDCOVER_FRACTIONS) <= feature_columns
        filled = catalog_db.execute(
            "SELECT COUNT(*) FROM feature_vectors WHERE "
            "tree_fraction IS NOT NULL OR shrub_fraction IS NOT NULL OR "
            "grass_fraction IS NOT NULL OR bare_fraction IS NOT NULL OR "
            "snow_fraction IS NOT NULL"
        ).fetchone()[0]
        assert filled == 0
    finally:
        store.close()
        catalog_db.close()

    for hike in catalog.dayhikes:
        vector = hike.feature_vector
        assert vector.tree_fraction is None
        assert vector.shrub_fraction is None
        assert vector.grass_fraction is None
        assert vector.bare_fraction is None
        assert vector.snow_fraction is None


def _write_class_tif(
    path: Path,
    *,
    west: float,
    south: float,
    east: float,
    north: float,
    codes: np.ndarray,
) -> None:
    import rasterio
    from rasterio.transform import from_bounds

    height, width = codes.shape
    transform = from_bounds(west, south, east, north, width, height)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=transform,
        nodata=0,
    ) as dataset:
        dataset.write(codes.astype("uint8"), 1)


def test_sample_writes_class_on_covered_vertices_and_leaves_uncovered_unset(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    raster = tmp_path / "ridge_cover.tif"
    # Covers Ridge Walk (lon 15.0, lat 45.0–45.09), not Valley Path (lon 15.1).
    _write_class_tif(
        raster,
        west=14.99,
        south=44.99,
        east=15.05,
        north=45.12,
        codes=np.full((8, 8), 10, dtype="uint8"),
    )

    sampled = sample_landcover(catalog_path, tmp_path)

    assert sampled > 0
    store = sqlite3.connect(geometry_store_path_for(catalog_path))
    try:
        ridge = [
            row[0]
            for row in store.execute(
                "SELECT landcover FROM vertices WHERE dayhike_id = 1001 ORDER BY seq"
            )
        ]
        valley = [
            row[0]
            for row in store.execute(
                "SELECT landcover FROM vertices WHERE dayhike_id = 1002 ORDER BY seq"
            )
        ]
        assert ridge
        assert all(code == 10 for code in ridge)
        assert valley
        assert all(code is None for code in valley)
        assert store.execute(
            "SELECT value FROM geometry_meta WHERE key = 'attribution'"
        ).fetchone() == ("© OpenStreetMap contributors",)
        assert store.execute(
            "SELECT value FROM geometry_meta WHERE key = 'worldcover_attribution'"
        ).fetchone() == (
            "© ESA WorldCover project 2021 / contains modified Copernicus Sentinel data "
            "(2021) processed by ESA WorldCover consortium",
        )
    finally:
        store.close()
    store_path = geometry_store_path_for(catalog_path)
    assert not Path(str(store_path) + "-wal").exists()
    assert not Path(str(store_path) + "-shm").exists()


def test_catalog_fractions_use_sampled_vertices_and_keep_uncovered_hikes(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    catalog = build_catalog(FIXTURE, catalog_path)
    kept_ids = {hike.id for hike in catalog.dayhikes}
    _write_class_tif(
        tmp_path / "ridge_cover.tif",
        west=14.99,
        south=44.99,
        east=15.05,
        north=45.12,
        codes=np.full((8, 8), 10, dtype="uint8"),
    )

    sample_landcover(catalog_path, tmp_path)

    catalog = Catalog.open(catalog_path)
    assert {hike.id for hike in catalog.dayhikes} == kept_ids
    ridge = next(hike for hike in catalog.dayhikes if hike.id == 1001)
    valley = next(hike for hike in catalog.dayhikes if hike.id == 1002)
    assert ridge.feature_vector.tree_fraction == 1.0
    assert ridge.feature_vector.shrub_fraction == 0.0
    assert ridge.feature_vector.grass_fraction == 0.0
    assert ridge.feature_vector.bare_fraction == 0.0
    assert ridge.feature_vector.snow_fraction == 0.0
    assert valley.feature_vector.tree_fraction is None
    assert valley.feature_vector.shrub_fraction is None
    assert valley.feature_vector.grass_fraction is None
    assert valley.feature_vector.bare_fraction is None
    assert valley.feature_vector.snow_fraction is None


def test_other_class_codes_count_in_denominator_only(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    # Valley Path vertices: (45.2, 15.1) and (45.245, 15.1). North pixel is
    # tree (10); south pixel is cropland (40), which is leftover-only.
    _write_class_tif(
        tmp_path / "valley_mix.tif",
        west=15.09,
        south=45.19,
        east=15.11,
        north=45.26,
        codes=np.array([[10], [40]], dtype="uint8"),
    )

    sample_landcover(catalog_path, tmp_path)

    catalog = Catalog.open(catalog_path)
    valley = next(hike for hike in catalog.dayhikes if hike.id == 1002)
    assert valley.feature_vector.tree_fraction == 0.5
    assert valley.feature_vector.shrub_fraction == 0.0
    assert valley.feature_vector.grass_fraction == 0.0
    assert valley.feature_vector.bare_fraction == 0.0
    assert valley.feature_vector.snow_fraction == 0.0
    ridge = next(hike for hike in catalog.dayhikes if hike.id == 1001)
    assert ridge.feature_vector.tree_fraction is None


def test_permanent_snow_code_fills_snow_fraction_not_tree(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    _write_class_tif(
        tmp_path / "ridge_snow.tif",
        west=14.99,
        south=44.99,
        east=15.05,
        north=45.12,
        codes=np.full((8, 8), 70, dtype="uint8"),
    )

    sample_landcover(catalog_path, tmp_path)

    ridge = next(
        hike for hike in Catalog.open(catalog_path).dayhikes if hike.id == 1001
    )
    assert ridge.feature_vector.snow_fraction == 1.0
    assert ridge.feature_vector.tree_fraction == 0.0
    assert ridge.feature_vector.shrub_fraction == 0.0
    assert ridge.feature_vector.grass_fraction == 0.0
    assert ridge.feature_vector.bare_fraction == 0.0


def test_sample_adds_missing_landcover_columns_in_place(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    catalog = build_catalog(FIXTURE, catalog_path)
    kept_ids = [hike.id for hike in catalog.dayhikes]
    store_path = geometry_store_path_for(catalog_path)
    store = sqlite3.connect(store_path)
    catalog_db = sqlite3.connect(catalog_path)
    try:
        store.execute("ALTER TABLE vertices DROP COLUMN landcover")
        store.commit()
        for name in LANDCOVER_FRACTIONS:
            catalog_db.execute(f"ALTER TABLE feature_vectors DROP COLUMN {name}")
        catalog_db.commit()
    finally:
        store.close()
        catalog_db.close()

    _write_class_tif(
        tmp_path / "ridge_cover.tif",
        west=14.99,
        south=44.99,
        east=15.05,
        north=45.12,
        codes=np.full((8, 8), 10, dtype="uint8"),
    )
    sample_landcover(catalog_path, tmp_path)

    catalog = Catalog.open(catalog_path)
    assert [hike.id for hike in catalog.dayhikes] == kept_ids
    ridge = next(hike for hike in catalog.dayhikes if hike.id == 1001)
    assert ridge.feature_vector.tree_fraction == 1.0
    catalog_db = sqlite3.connect(catalog_path)
    store = sqlite3.connect(store_path)
    try:
        vertex_columns = {row[1] for row in store.execute("PRAGMA table_info(vertices)")}
        feature_columns = {
            row[1] for row in catalog_db.execute("PRAGMA table_info(feature_vectors)")
        }
        assert "landcover" in vertex_columns
        assert set(LANDCOVER_FRACTIONS) <= feature_columns
        assert "month" not in feature_columns
    finally:
        store.close()
        catalog_db.close()


def _sample_ridge_as_forest(tmp_path: Path) -> Path:
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    _write_class_tif(
        tmp_path / "ridge_cover.tif",
        west=14.99,
        south=44.99,
        east=15.05,
        north=45.12,
        codes=np.full((8, 8), 10, dtype="uint8"),
    )
    sample_landcover(catalog_path, tmp_path)
    return catalog_path


def test_forested_seed_ranks_forested_neighbour_ahead_of_barer_match(
    tmp_path: Path,
) -> None:
    catalog_path = _sample_ridge_as_forest(tmp_path)
    catalog_db = sqlite3.connect(catalog_path)
    catalog_db.executemany(
        "UPDATE feature_vectors SET tree_fraction = ?, shrub_fraction = 0, "
        "grass_fraction = 0, bare_fraction = ?, snow_fraction = 0 WHERE dayhike_id = ?",
        [(1.0, 0.0, 1007), (0.0, 1.0, 1002)],
    )
    catalog_db.commit()
    catalog_db.close()
    build_similarity(catalog_path, tmp_path / "dem")

    ranking = rank_dayhikes(Catalog.open(catalog_path), ["Ridge Walk"], {"landcover": 100})
    scores = {item.dayhike.id: item.score for item in ranking.items}

    # PS-1 (1007) is forested like the Seed, Valley Path (1002) bare; the
    # unsampled relation:1003 has no LandCover and is not ranked on it.
    assert [item.dayhike.id for item in ranking.items] == [1007, 1002]
    assert scores[1007] > scores[1002]


def test_landcover_fractions_stay_put_after_ranking(tmp_path: Path) -> None:
    catalog_path = _sample_ridge_as_forest(tmp_path)
    before = Catalog.open(catalog_path)
    fractions = {
        hike.id: (
            hike.feature_vector.tree_fraction,
            hike.feature_vector.shrub_fraction,
            hike.feature_vector.grass_fraction,
            hike.feature_vector.bare_fraction,
            hike.feature_vector.snow_fraction,
        )
        for hike in before.dayhikes
    }
    catalog_db = sqlite3.connect(catalog_path)
    try:
        feature_columns = {
            row[1] for row in catalog_db.execute("PRAGMA table_info(feature_vectors)")
        }
        assert set(LANDCOVER_FRACTIONS) <= feature_columns
        assert "month" not in feature_columns
        assert (
            catalog_db.execute("SELECT COUNT(*) FROM climate_months").fetchone()[0] == 0
        )
    finally:
        catalog_db.close()

    build_similarity(catalog_path, tmp_path / "dem")
    rank_dayhikes(before, ["Ridge Walk"])

    after = Catalog.open(catalog_path)
    assert {
        hike.id: (
            hike.feature_vector.tree_fraction,
            hike.feature_vector.shrub_fraction,
            hike.feature_vector.grass_fraction,
            hike.feature_vector.bare_fraction,
            hike.feature_vector.snow_fraction,
        )
        for hike in after.dayhikes
    } == fractions


def test_cli_sample_landcover_prints_sampled_count(tmp_path: Path) -> None:
    import subprocess
    import sys

    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    _write_class_tif(
        tmp_path / "ridge_cover.tif",
        west=14.99,
        south=44.99,
        east=15.05,
        north=45.12,
        codes=np.full((8, 8), 10, dtype="uint8"),
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "dayhike",
            "sample-landcover",
            "--catalog",
            str(catalog_path),
            "--landcover",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert int(result.stdout.strip().splitlines()[-1]) > 0


def test_worldcover_tile_url_points_at_public_v200_map_cog() -> None:
    url = worldcover_tile_url("N45E015")
    assert url.startswith(
        "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
    )
    assert url.endswith("ESA_WorldCover_10m_2021_v200_N45E015_Map.tif")


def test_worldcover_tile_id_uses_three_degree_southwest_corner() -> None:
    assert worldcover_tile_id(45.0, 15.0) == "N45E015"
    assert worldcover_tile_id(47.9, 17.9) == "N45E015"
    assert worldcover_tile_id(48.0, 12.0) == "N48E012"
    assert worldcover_tile_id(41.8, 5.9) == "N39E003"


def test_worldcover_tiles_covering_default_prd_bounds_include_alps_and_dinarides() -> None:
    tiles = worldcover_tiles_covering(DEFAULT_DEM_BOUNDS)
    assert "N45E015" in tiles
    assert "N48E012" in tiles
    assert "N39E003" in tiles
    assert tiles == sorted(set(tiles))


def test_fetch_worldcover_downloads_missing_tiles_and_skips_existing(
    tmp_path: Path,
) -> None:
    recorded: list[str] = []
    dest = tmp_path / "landcover"
    dest.mkdir()
    existing = dest / "ESA_WorldCover_10m_2021_v200_N45E012_Map.tif"
    existing.write_bytes(b"already-there")

    def fake_retrieve(url: str, filename: str) -> None:
        recorded.append(url)
        Path(filename).write_bytes(b"tif-bytes")

    paths = fetch_worldcover(
        dest,
        Bounds(south=45.1, west=14.0, north=45.2, east=16.0),
        retrieve=fake_retrieve,
    )

    assert existing.read_bytes() == b"already-there"
    assert existing in paths
    fetched = [path for path in paths if path != existing]
    assert fetched == [dest / "ESA_WorldCover_10m_2021_v200_N45E015_Map.tif"]
    assert recorded == [worldcover_tile_url("N45E015")]
    assert fetched[0].read_bytes() == b"tif-bytes"


def test_fetch_worldcover_writes_map_tifs_sample_landcover_reads(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    dest = tmp_path / "landcover"

    def fake_retrieve(_url: str, filename: str) -> None:
        _write_class_tif(
            Path(filename),
            west=14.99,
            south=44.99,
            east=15.05,
            north=45.12,
            codes=np.full((8, 8), 10, dtype="uint8"),
        )

    paths = fetch_worldcover(
        dest,
        Bounds(south=45.0, west=15.0, north=45.1, east=15.1),
        retrieve=fake_retrieve,
    )

    assert paths == [dest / "ESA_WorldCover_10m_2021_v200_N45E015_Map.tif"]
    assert sample_landcover(catalog_path, dest) > 0


def test_fetch_worldcover_skips_missing_remote_tiles(tmp_path: Path) -> None:
    def missing(_url: str, _filename: str) -> None:
        raise FileNotFoundError("404")

    paths = fetch_worldcover(
        tmp_path,
        Bounds(south=45.0, west=15.0, north=45.1, east=15.1),
        retrieve=missing,
    )

    assert paths == []
    assert not (tmp_path / "ESA_WorldCover_10m_2021_v200_N45E015_Map.tif").exists()


def test_cli_fetch_landcover_does_not_need_a_catalog() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "dayhike", "fetch-landcover", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--dest" in result.stdout
    assert "--bounds" in result.stdout
    assert "--catalog" not in result.stdout


def test_fetch_worldcover_fails_after_a_tile_download_error(tmp_path: Path) -> None:
    def broken(_url: str, _filename: str) -> None:
        raise OSError("connection reset")

    with pytest.raises(RuntimeError, match="N45E015"):
        fetch_worldcover(
            tmp_path,
            Bounds(south=45.0, west=15.0, north=45.1, east=15.1),
            retrieve=broken,
        )


def _write_overlapping_tiles(raster_dir: Path) -> None:
    raster_dir.mkdir()
    for name, code in (("a_tree.tif", 10), ("b_grass.tif", 30)):
        _write_class_tif(
            raster_dir / name,
            west=14.99,
            south=44.99,
            east=15.05,
            north=45.12,
            codes=np.full((8, 8), code, dtype="uint8"),
        )


def _sample_snapshot(catalog_path: Path) -> tuple[list, list]:
    store = sqlite3.connect(geometry_store_path_for(catalog_path))
    try:
        codes = list(
            store.execute(
                "SELECT dayhike_id, seq, landcover FROM vertices ORDER BY dayhike_id, seq"
            )
        )
    finally:
        store.close()
    catalog = Catalog.open(catalog_path)
    fractions = [
        (hike.id, hike.feature_vector.tree_fraction, hike.feature_vector.grass_fraction)
        for hike in catalog.dayhikes
    ]
    return codes, fractions


def test_sample_opens_each_tile_once_across_chunks_and_matches_single_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import rasterio

    from dayhike import landcover

    single = tmp_path / "single.sqlite"
    build_catalog(FIXTURE, single)
    rasters = tmp_path / "rasters"
    _write_overlapping_tiles(rasters)
    sampled_single = sample_landcover(single, rasters)

    chunked = tmp_path / "chunked.sqlite"
    build_catalog(FIXTURE, chunked)
    monkeypatch.setattr(landcover, "_VERTEX_CHUNK", 2)
    opened: list[str] = []
    real_open = rasterio.open

    def counting_open(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(rasterio, "open", counting_open)
    sampled_chunked = sample_landcover(chunked, rasters)

    assert sampled_chunked == sampled_single > 2
    assert len(opened) == len(set(opened)) == 2
    codes, fractions = _sample_snapshot(chunked)
    assert (codes, fractions) == _sample_snapshot(single)
    assert {code for _id, _seq, code in codes} == {10, None}


def test_sample_keeps_at_most_the_open_tile_limit_open_and_matches_unlimited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import rasterio

    from dayhike import landcover

    unlimited = tmp_path / "unlimited.sqlite"
    build_catalog(FIXTURE, unlimited)
    rasters = tmp_path / "rasters"
    rasters.mkdir()
    for name, south, north, code in (("a_south.tif", 44.99, 45.05, 10), ("b_north.tif", 45.05, 45.12, 30)):
        _write_class_tif(
            rasters / name,
            west=14.99,
            south=south,
            east=15.05,
            north=north,
            codes=np.full((8, 8), code, dtype="uint8"),
        )
    sample_landcover(unlimited, rasters)

    limited = tmp_path / "limited.sqlite"
    build_catalog(FIXTURE, limited)
    monkeypatch.setattr(landcover, "_VERTEX_CHUNK", 2)
    monkeypatch.setattr(landcover, "_MAX_OPEN_TILES", 1)
    opened = []
    most_open = 0
    real_open = rasterio.open

    def counting_open(path, *args, **kwargs):
        nonlocal most_open
        dataset = real_open(path, *args, **kwargs)
        opened.append(dataset)
        most_open = max(most_open, sum(not each.closed for each in opened))
        return dataset

    monkeypatch.setattr(rasterio, "open", counting_open)
    sample_landcover(limited, rasters)

    assert most_open == 1
    assert all(dataset.closed for dataset in opened)
    codes, fractions = _sample_snapshot(limited)
    assert (codes, fractions) == _sample_snapshot(unlimited)
    assert {code for _id, _seq, code in codes} >= {10, 30}


def test_failed_tile_open_still_closes_datasets_opened_so_far(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import rasterio

    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    rasters = tmp_path / "rasters"
    _write_overlapping_tiles(rasters)
    opened = []
    real_open = rasterio.open

    def failing_open(path, *args, **kwargs):
        if opened:
            raise OSError("unreadable tile")
        dataset = real_open(path, *args, **kwargs)
        opened.append(dataset)
        return dataset

    monkeypatch.setattr(rasterio, "open", failing_open)

    with pytest.raises(OSError, match="unreadable tile"):
        sample_landcover(catalog_path, rasters)

    assert len(opened) == 1
    assert all(dataset.closed for dataset in opened)
    store_path = geometry_store_path_for(catalog_path)
    assert not Path(str(store_path) + "-wal").exists()


def test_sample_landcover_removes_similarity_facts(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    directory = build_similarity(catalog_path, tmp_path / "dem")
    raster_dir = tmp_path / "sample_landcover"
    raster_dir.mkdir()
    _write_class_tif(
        raster_dir / "cover.tif",
        west=14.99, south=44.99, east=15.2, north=45.12,
        codes=np.full((8, 8), 10, dtype="uint8"),
    )
    messages: list[str] = []

    assert sample_landcover(catalog_path, raster_dir, progress=messages.append) > 0

    assert not directory.exists()
    assert messages[-1] == (
        f"removed stale similarity facts {directory}; run `dayhike build-similarity`"
    )
    with pytest.raises(StaleSimilarity, match="build-similarity"):
        rank_dayhikes(Catalog.open(catalog_path), ["Ridge Walk"])
