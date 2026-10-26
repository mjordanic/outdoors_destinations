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
    sample_climate,
)
from dayhike.climate import CHELSA_ATTRIBUTION, chelsa_url, fetch_chelsa
from dayhike.dem import Bounds

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
CLIMATE_FEATURES = ("temperature_c", "precipitation_mm", "solar_radiation")
VARIABLES = ("tas", "pr", "rsds")


def _write_tif(
    path: Path,
    data: np.ndarray,
    *,
    west: float,
    south: float,
    east: float,
    north: float,
    nodata: float = float("nan"),
    scale: float = 1.0,
) -> None:
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
        dtype=data.dtype,
        crs="EPSG:4326",
        transform=from_bounds(west, south, east, north, width, height),
        nodata=nodata,
    ) as dataset:
        dataset.write(data, 1)
        dataset.scales = (scale,)


def _write_climate_tifs(
    dest: Path,
    *,
    west: float,
    south: float,
    east: float,
    north: float,
    tas: float | tuple[float, ...] = 10.0,
    pr: float | tuple[float, ...] = 80.0,
    rsds: float | tuple[float, ...] = 15000.0,
) -> None:
    """Sample-ready rasters: chelsa_<var>_<MM>.tif, float32 in Measure units."""
    by_variable = {
        name: (value,) * 12 if isinstance(value, (int, float)) else tuple(value)
        for name, value in (("tas", tas), ("pr", pr), ("rsds", rsds))
    }
    for month in range(1, 13):
        for var in VARIABLES:
            _write_tif(
                dest / f"chelsa_{var}_{month:02d}.tif",
                np.full((8, 8), by_variable[var][month - 1], dtype="float32"),
                west=west,
                south=south,
                east=east,
                north=north,
            )


def _retrieve_chelsa(
    url: str,
    filename: str,
    *,
    west: float,
    south: float,
    east: float,
    north: float,
    raw: dict[str, int] | None = None,
) -> None:
    """Stand-in for a CHELSA download: uint16 raw values with CHELSA's scales."""
    raw = raw or {"tas": 2831, "pr": 800, "rsds": 200}
    scales = {"tas": 0.1, "pr": 0.1, "rsds": 1.0}
    variable = Path(url).name.split("_")[1]
    _write_tif(
        Path(filename),
        np.full((8, 8), raw[variable], dtype="uint16"),
        west=west,
        south=south,
        east=east,
        north=north,
        nodata=65535,
        scale=scales[variable],
    )


def test_ingest_creates_empty_climate_and_does_not_need_a_raster(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    catalog = build_catalog(FIXTURE, catalog_path)

    store = sqlite3.connect(geometry_store_path_for(catalog_path))
    catalog_db = sqlite3.connect(catalog_path)
    try:
        tables = {
            row[0]
            for row in store.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "vertex_climate" not in tables

        catalog_tables = {
            row[0]
            for row in catalog_db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "climate_months" in catalog_tables
        assert (
            catalog_db.execute("SELECT COUNT(*) FROM climate_months").fetchone()[0]
            == 0
        )

        feature_columns = {
            row[1] for row in catalog_db.execute("PRAGMA table_info(feature_vectors)")
        }
        assert set(CLIMATE_FEATURES) <= feature_columns
        filled = catalog_db.execute(
            "SELECT COUNT(*) FROM feature_vectors WHERE "
            "temperature_c IS NOT NULL OR precipitation_mm IS NOT NULL OR "
            "solar_radiation IS NOT NULL"
        ).fetchone()[0]
        assert filled == 0
    finally:
        store.close()
        catalog_db.close()

    for hike in catalog.dayhikes:
        vector = hike.feature_vector
        assert vector.temperature_c is None
        assert vector.precipitation_mm is None
        assert vector.solar_radiation is None
        assert hike.climate_months == (None,) * 12


def test_sample_stores_no_per_vertex_climate_and_drops_an_old_table(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    store_path = geometry_store_path_for(catalog_path)
    store = sqlite3.connect(store_path)
    store.execute(
        "CREATE TABLE vertex_climate (dayhike_id INTEGER, seq INTEGER, month INTEGER, "
        "temperature_c REAL, precipitation_mm REAL, solar_radiation REAL)"
    )
    store.execute(
        "INSERT INTO geometry_meta (key, value) VALUES ('worldclim_attribution', 'old')"
    )
    store.commit()
    store.close()
    _write_climate_tifs(tmp_path, west=14.99, south=44.99, east=15.05, north=45.12)

    sampled = sample_climate(catalog_path, tmp_path)

    store = sqlite3.connect(store_path)
    try:
        ridge_vertices = store.execute(
            "SELECT COUNT(*) FROM vertices WHERE dayhike_id = 1001"
        ).fetchone()[0]
        tables = {
            row[0]
            for row in store.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        meta = dict(store.execute("SELECT key, value FROM geometry_meta"))
    finally:
        store.close()
    assert sampled == ridge_vertices
    assert "vertex_climate" not in tables
    assert meta["attribution"] == "© OpenStreetMap contributors"
    assert meta["chelsa_attribution"] == CHELSA_ATTRIBUTION
    assert "worldclim_attribution" not in meta
    assert not Path(str(store_path) + "-wal").exists()
    assert not Path(str(store_path) + "-shm").exists()


def test_catalog_stores_monthly_means_and_regime_and_keeps_uncovered_hikes(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    catalog = build_catalog(FIXTURE, catalog_path)
    kept_ids = {hike.id for hike in catalog.dayhikes}
    tas = (1.0, 2.0, 3.0, 4.0, 5.0, 10.0, 20.0, 30.0, 9.0, 8.0, 7.0, 6.0)
    pr = (10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0, 110.0, 120.0)
    rsds = (
        1000.0,
        2000.0,
        3000.0,
        4000.0,
        5000.0,
        6000.0,
        7000.0,
        8000.0,
        9000.0,
        10000.0,
        11000.0,
        12000.0,
    )
    _write_climate_tifs(
        tmp_path,
        west=14.99,
        south=44.99,
        east=15.05,
        north=45.12,
        tas=tas,
        pr=pr,
        rsds=rsds,
    )

    sample_climate(catalog_path, tmp_path)

    catalog = Catalog.open(catalog_path)
    assert {hike.id for hike in catalog.dayhikes} == kept_ids
    ridge = next(hike for hike in catalog.dayhikes if hike.id == 1001)
    valley = next(hike for hike in catalog.dayhikes if hike.id == 1002)
    assert ridge.climate_months[6] == pytest.approx((20.0, 70.0, 7000.0))
    assert ridge.climate_months[5] == pytest.approx((10.0, 60.0, 6000.0))
    assert ridge.climate_months[7] == pytest.approx((30.0, 80.0, 8000.0))
    assert ridge.feature_vector.temperature_c == pytest.approx(20.0)
    assert ridge.feature_vector.precipitation_mm == pytest.approx(65.0)
    assert ridge.feature_vector.solar_radiation == pytest.approx(7000.0)
    assert valley.feature_vector.temperature_c is None
    assert valley.feature_vector.precipitation_mm is None
    assert valley.feature_vector.solar_radiation is None
    assert valley.climate_months == (None,) * 12
    catalog_db = sqlite3.connect(catalog_path)
    try:
        null_hikes = catalog_db.execute(
            "SELECT COUNT(*) FROM feature_vectors WHERE temperature_c IS NULL"
        ).fetchone()[0]
        assert null_hikes >= 1
        ridge_months = catalog_db.execute(
            "SELECT COUNT(*) FROM climate_months WHERE dayhike_id = 1001"
        ).fetchone()[0]
        valley_months = catalog_db.execute(
            "SELECT COUNT(*) FROM climate_months WHERE dayhike_id = 1002"
        ).fetchone()[0]
        assert ridge_months == 12
        assert valley_months == 0
    finally:
        catalog_db.close()


def test_sample_adds_missing_climate_tables_in_place(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    catalog = build_catalog(FIXTURE, catalog_path)
    kept_ids = [hike.id for hike in catalog.dayhikes]
    store_path = geometry_store_path_for(catalog_path)
    store = sqlite3.connect(store_path)
    catalog_db = sqlite3.connect(catalog_path)
    try:
        catalog_db.execute("DROP TABLE climate_months")
        for name in CLIMATE_FEATURES:
            catalog_db.execute(f"ALTER TABLE feature_vectors DROP COLUMN {name}")
        catalog_db.commit()
    finally:
        store.close()
        catalog_db.close()

    tas = (0.0,) * 6 + (21.0,) + (0.0,) * 5
    _write_climate_tifs(
        tmp_path,
        west=14.99,
        south=44.99,
        east=15.05,
        north=45.12,
        tas=tas,
        pr=40.0,
        rsds=12000.0,
    )
    sample_climate(catalog_path, tmp_path)

    catalog = Catalog.open(catalog_path)
    assert [hike.id for hike in catalog.dayhikes] == kept_ids
    ridge = next(hike for hike in catalog.dayhikes if hike.id == 1001)
    assert ridge.feature_vector.temperature_c == pytest.approx(7.0)
    assert ridge.climate_months[6][0] == pytest.approx(21.0)
    catalog_db = sqlite3.connect(catalog_path)
    store = sqlite3.connect(store_path)
    try:
        store_tables = {
            row[0]
            for row in store.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        catalog_tables = {
            row[0]
            for row in catalog_db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "vertex_climate" not in store_tables
        assert "climate_months" in catalog_tables
        feature_columns = {
            row[1] for row in catalog_db.execute("PRAGMA table_info(feature_vectors)")
        }
        assert set(CLIMATE_FEATURES) <= feature_columns
    finally:
        store.close()
        catalog_db.close()


def _sample_ridge_july_spike(tmp_path: Path) -> Path:
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    tas = (0.0,) * 6 + (21.0,) + (0.0,) * 5
    _write_climate_tifs(
        tmp_path,
        west=14.99,
        south=44.99,
        east=15.05,
        north=45.12,
        tas=tas,
        pr=40.0,
        rsds=12000.0,
    )
    sample_climate(catalog_path, tmp_path)
    return catalog_path


def _copy_ridge_climate(catalog_path: Path, dayhike_id: int, **july) -> None:
    """Give another fixture DayHike Ridge Walk's climate, July changed by ``july``."""
    catalog_db = sqlite3.connect(catalog_path)
    try:
        catalog_db.execute(
            "INSERT INTO climate_months SELECT ?, month, temperature_c, "
            "precipitation_mm, solar_radiation FROM climate_months WHERE dayhike_id = 1001",
            (dayhike_id,),
        )
        for column, value in july.items():
            catalog_db.execute(
                f"UPDATE climate_months SET {column} = ? WHERE dayhike_id = ? AND month = 7",
                (value, dayhike_id),
            )
        catalog_db.execute(
            "UPDATE feature_vectors SET (temperature_c, precipitation_mm, solar_radiation) = "
            "(SELECT temperature_c, precipitation_mm, solar_radiation FROM feature_vectors "
            "WHERE dayhike_id = 1001) WHERE dayhike_id = ?",
            (dayhike_id,),
        )
        catalog_db.commit()
    finally:
        catalog_db.close()


def test_ranking_with_month_uses_july_not_june_august_regime(tmp_path: Path) -> None:
    catalog_path = _sample_ridge_july_spike(tmp_path)
    # Valley Path (1002) has Ridge Walk's July; PS-1 (1007) its regime in July.
    _copy_ridge_climate(catalog_path, 1002)
    _copy_ridge_climate(catalog_path, 1007, temperature_c=7.0)
    build_similarity(catalog_path, tmp_path / "dem")
    catalog = Catalog.open(catalog_path)
    weights = {"temperature": 100}

    without_month = rank_dayhikes(catalog, ["Ridge Walk"], weights)
    with_july = rank_dayhikes(catalog, ["Ridge Walk"], weights, month=7)

    without_scores = {item.dayhike.id: item.score for item in without_month.items}
    july_scores = {item.dayhike.id: item.score for item in with_july.items}
    assert without_scores[1002] == pytest.approx(without_scores[1007])
    assert july_scores[1002] > july_scores[1007]
    assert [item.dayhike.id for item in with_july.items] == [1002, 1007]


def test_query_month_outside_1_to_12_fails(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")

    with pytest.raises(ValueError, match="1 to 12"):
        rank_dayhikes(catalog, ["Ridge Walk"], month=0)
    with pytest.raises(ValueError, match="1 to 12"):
        rank_dayhikes(catalog, ["Ridge Walk"], month=13)


def test_query_month_fails_when_climate_has_not_been_sampled(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    build_similarity(catalog.path, tmp_path / "dem")

    with pytest.raises(ValueError, match="climate has not been sampled"):
        rank_dayhikes(catalog, ["Ridge Walk"], month=7)


def test_omitting_query_month_ranks_unsampled_catalog(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    build_similarity(catalog.path, tmp_path / "dem")

    ranking = rank_dayhikes(catalog, ["Ridge Walk"])

    assert ranking.items
    assert all(item.drive_time is None for item in ranking.items)


def test_forested_seed_in_july_ranks_forested_neighbour_ahead_of_barer_hotter(
    tmp_path: Path,
) -> None:
    catalog_path = _sample_ridge_july_spike(tmp_path)
    _copy_ridge_climate(catalog_path, 1002)
    _copy_ridge_climate(catalog_path, 1007, temperature_c=36.0, solar_radiation=16_000.0)
    catalog_db = sqlite3.connect(catalog_path)
    catalog_db.executemany(
        "UPDATE feature_vectors SET tree_fraction = ?, shrub_fraction = 0, "
        "grass_fraction = 0, bare_fraction = ?, snow_fraction = 0 WHERE dayhike_id = ?",
        [(1.0, 0.0, 1001), (1.0, 0.0, 1002), (0.0, 1.0, 1007)],
    )
    catalog_db.commit()
    catalog_db.close()
    build_similarity(catalog_path, tmp_path / "dem")
    weights = {"landcover": 100, "temperature": 100, "solar_radiation": 100}

    ranking = rank_dayhikes(Catalog.open(catalog_path), ["Ridge Walk"], weights, month=7)
    scores = {item.dayhike.id: item.score for item in ranking.items}

    assert [item.dayhike.id for item in ranking.items][0] == 1002
    assert scores[1002] > scores[1007]


def test_drive_time_filter_still_runs_after_ranking_with_month(tmp_path: Path) -> None:
    from dayhike import Origin, filter_by_drive_time

    catalog_path = _sample_ridge_july_spike(tmp_path)
    build_similarity(catalog_path, tmp_path / "dem")
    catalog = Catalog.open(catalog_path)
    ranking = rank_dayhikes(catalog, ["Ridge Walk"], month=7)
    times = tuple(1_800.0 for _ in ranking.items)

    class StubMatrix:
        def sources_to_targets(self, origin, trailheads):
            assert origin.latitude == 45.8
            assert len(trailheads) == len(times)
            return times

    filtered = filter_by_drive_time(
        ranking,
        Origin(latitude=45.8, longitude=15.97),
        max_hours=2,
        matrix_client=StubMatrix(),
    )

    assert [item.dayhike.id for item in filtered.items] == [
        item.dayhike.id for item in ranking.items
    ]
    assert all(item.drive_time is not None for item in filtered.items)


def test_cli_sample_climate_prints_sampled_count(tmp_path: Path) -> None:
    import subprocess
    import sys

    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    _write_climate_tifs(
        tmp_path,
        west=14.99,
        south=44.99,
        east=15.05,
        north=45.12,
        tas=12.5,
        pr=90.0,
        rsds=18000.0,
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "dayhike",
            "sample-climate",
            "--catalog",
            str(catalog_path),
            "--climate",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert int(result.stdout.strip().splitlines()[-1]) > 0


def test_cli_rank_month_uses_july_not_regime(tmp_path: Path) -> None:
    import subprocess
    import sys

    path = _sample_ridge_july_spike(tmp_path)
    build_similarity(path, tmp_path / "dem")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "dayhike",
            "rank",
            "--catalog",
            str(path),
            "--month",
            "7",
            "Ridge Walk",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines
    assert all(len(line.split("\t")) == 3 for line in lines)
    assert "Valley Path" in {line.split("\t")[1] for line in lines}


def test_cli_rank_month_fails_before_climate_is_sampled(tmp_path: Path) -> None:
    import subprocess
    import sys

    path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, path)
    build_similarity(path, tmp_path / "dem")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "dayhike",
            "rank",
            "--catalog",
            str(path),
            "--month",
            "7",
            "Ridge Walk",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "climate has not been sampled" in result.stderr


def test_chelsa_url_points_at_the_v2_1_1981_2010_climatologies() -> None:
    base = "https://os.unil.cloud.switch.ch/chelsa02/chelsa/global/climatologies"
    assert chelsa_url("tas", 7) == (
        f"{base}/tas/1981-2010/CHELSA_tas_07_1981-2010_V.2.1.tif"
    )
    assert chelsa_url("pr", 1) == f"{base}/pr/1981-2010/CHELSA_pr_01_1981-2010_V.2.1.tif"
    assert chelsa_url("rsds", 12) == (
        f"{base}/rsds/1981-2010/CHELSA_rsds_12_1981-2010_V.2.1.tif"
    )


def test_fetch_chelsa_downloads_missing_months_and_skips_existing(
    tmp_path: Path,
) -> None:
    recorded: list[str] = []
    requested = Bounds(south=45.0, west=15.0, north=45.2, east=15.2)
    staging = tmp_path / "staging"
    staging.mkdir()
    _write_climate_tifs(staging, west=14.9, south=44.9, east=15.3, north=45.3)
    dest = tmp_path / "climate"
    dest.mkdir()
    for month in range(1, 13):
        name = f"chelsa_tas_{month:02d}.tif"
        (dest / name).write_bytes((staging / name).read_bytes())
    existing_pr = dest / "chelsa_pr_01.tif"
    existing_pr.write_bytes((staging / existing_pr.name).read_bytes())
    already_tas = (dest / "chelsa_tas_01.tif").read_bytes()
    already_pr = existing_pr.read_bytes()

    def fake_retrieve(url: str, filename: str) -> None:
        recorded.append(url)
        _retrieve_chelsa(
            url,
            filename,
            west=14.9,
            south=44.9,
            east=15.3,
            north=45.3,
            raw={"tas": 3000, "pr": 900, "rsds": 300},
        )

    paths = fetch_chelsa(dest, requested, retrieve=fake_retrieve)

    assert recorded == [chelsa_url("pr", month) for month in range(2, 13)] + [
        chelsa_url("rsds", month) for month in range(1, 13)
    ]
    assert (dest / "chelsa_tas_01.tif").read_bytes() == already_tas
    assert existing_pr.read_bytes() == already_pr
    assert len(paths) == 36
    assert existing_pr in paths
    assert dest / "chelsa_rsds_12.tif" in paths


def test_fetch_chelsa_refetches_an_unreadable_present_raster(tmp_path: Path) -> None:
    import rasterio

    dest = tmp_path / "climate"
    dest.mkdir()
    broken = dest / "chelsa_tas_01.tif"
    broken.write_bytes(b"not-a-geotiff")
    recorded: list[str] = []

    def fake_retrieve(url: str, filename: str) -> None:
        recorded.append(url)
        _retrieve_chelsa(url, filename, west=14.99, south=44.99, east=15.05, north=45.12)

    fetch_chelsa(
        dest, Bounds(south=45.0, west=15.0, north=45.1, east=15.04), retrieve=fake_retrieve
    )

    assert chelsa_url("tas", 1) in recorded
    with rasterio.open(broken) as dataset:
        assert dataset.dtypes[0] == "float32"


def test_fetch_chelsa_converts_scaled_kelvin_mm_and_watts_to_measure_units(
    tmp_path: Path,
) -> None:
    import rasterio

    dest = tmp_path / "climate"

    def fake_retrieve(url: str, filename: str) -> None:
        _retrieve_chelsa(
            url,
            filename,
            west=14.99,
            south=44.99,
            east=15.05,
            north=45.12,
            raw={"tas": 2831, "pr": 1234, "rsds": 200},
        )

    fetch_chelsa(dest, retrieve=fake_retrieve)

    expected = {"tas": 283.1 - 273.15, "pr": 123.4, "rsds": 200 * 86.4}
    for variable, value in expected.items():
        with rasterio.open(dest / f"chelsa_{variable}_07.tif") as dataset:
            assert dataset.dtypes[0] == "float32"
            assert dataset.read(1) == pytest.approx(np.full((8, 8), value), abs=1e-3)


def test_fetch_chelsa_rejects_a_file_without_a_scale_tag(tmp_path: Path) -> None:
    dest = tmp_path / "climate"

    def fake_retrieve(url: str, filename: str) -> None:
        _write_tif(
            Path(filename),
            np.full((8, 8), 2831, dtype="uint16"),
            west=14.99,
            south=44.99,
            east=15.05,
            north=45.12,
            nodata=65535,
        )

    with pytest.raises(ValueError, match="chelsa_tas_01.tif: converted tas outside plausible range"):
        fetch_chelsa(dest, retrieve=fake_retrieve)

    assert not (dest / "chelsa_tas_01.tif").exists()


def test_fetch_chelsa_turns_nodata_into_nan_that_sampling_skips(tmp_path: Path) -> None:
    import rasterio

    dest = tmp_path / "climate"

    def fake_retrieve(url: str, filename: str) -> None:
        _retrieve_chelsa(
            url,
            filename,
            west=14.99,
            south=44.99,
            east=15.05,
            north=45.12,
            raw={"tas": 65535, "pr": 65535, "rsds": 65535},
        )

    fetch_chelsa(dest, retrieve=fake_retrieve)

    with rasterio.open(dest / "chelsa_tas_01.tif") as dataset:
        assert np.isnan(dataset.read(1)).all()
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    assert sample_climate(catalog_path, dest) == 0


def test_fetch_chelsa_writes_month_tifs_sample_climate_reads(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    dest = tmp_path / "climate"

    def fake_retrieve(url: str, filename: str) -> None:
        _retrieve_chelsa(url, filename, west=14.99, south=44.99, east=15.05, north=45.12)

    paths = fetch_chelsa(dest, retrieve=fake_retrieve)

    assert len(paths) == 36
    assert sample_climate(catalog_path, dest) > 0
    ridge = next(hike for hike in Catalog.open(catalog_path).dayhikes if hike.id == 1001)
    assert ridge.climate_months[6] == pytest.approx((9.95, 80.0, 17280.0), abs=1e-3)


def test_fetch_chelsa_clips_to_requested_bounds(tmp_path: Path) -> None:
    import rasterio

    dest = tmp_path / "climate"
    requested = Bounds(south=45.0, west=15.0, north=45.2, east=15.2)

    def fake_retrieve(url: str, filename: str) -> None:
        _retrieve_chelsa(url, filename, west=14.9, south=44.9, east=15.3, north=45.3)

    fetch_chelsa(dest, requested, retrieve=fake_retrieve)

    with rasterio.open(dest / "chelsa_tas_01.tif") as dataset:
        assert dataset.bounds.left == pytest.approx(15.0, abs=0.06)
        assert dataset.bounds.bottom == pytest.approx(45.0, abs=0.06)
        assert dataset.bounds.right == pytest.approx(15.2, abs=0.06)
        assert dataset.bounds.top == pytest.approx(45.2, abs=0.06)


def test_fetch_chelsa_refetches_when_present_rasters_do_not_cover_bounds(
    tmp_path: Path,
) -> None:
    import rasterio

    dest = tmp_path / "climate"
    dest.mkdir()
    _write_climate_tifs(dest, west=15.0, south=45.0, east=15.1, north=45.1)
    wider = Bounds(south=44.5, west=14.5, north=45.5, east=15.5)
    recorded: list[str] = []

    def fake_retrieve(url: str, filename: str) -> None:
        recorded.append(url)
        _retrieve_chelsa(url, filename, west=14.0, south=44.0, east=16.0, north=46.0)

    paths = fetch_chelsa(dest, wider, retrieve=fake_retrieve)

    assert recorded == [
        chelsa_url(variable, month) for variable in VARIABLES for month in range(1, 13)
    ]
    assert len(paths) == 36
    for path in paths:
        with rasterio.open(path) as dataset:
            assert dataset.bounds.left == pytest.approx(14.5, abs=0.13)
            assert dataset.bounds.bottom == pytest.approx(44.5, abs=0.13)
            assert dataset.bounds.right == pytest.approx(15.5, abs=0.13)
            assert dataset.bounds.top == pytest.approx(45.5, abs=0.13)


def test_fetch_chelsa_replaces_a_symlinked_raster_without_writing_through_it(
    tmp_path: Path,
) -> None:
    elsewhere = tmp_path / "reference"
    elsewhere.mkdir()
    _write_climate_tifs(elsewhere, west=15.0, south=45.0, east=15.1, north=45.1)
    dest = tmp_path / "climate"
    dest.mkdir()
    linked = dest / "chelsa_tas_01.tif"
    linked.symlink_to(elsewhere / linked.name)
    before = (elsewhere / linked.name).read_bytes()

    def fake_retrieve(url: str, filename: str) -> None:
        _retrieve_chelsa(url, filename, west=14.0, south=44.0, east=16.0, north=46.0)

    fetch_chelsa(
        dest, Bounds(south=44.5, west=14.5, north=45.5, east=15.5), retrieve=fake_retrieve
    )

    assert not linked.is_symlink()
    assert (elsewhere / linked.name).read_bytes() == before


def test_fetch_chelsa_skips_missing_remote_files(tmp_path: Path) -> None:
    def missing(_url: str, _filename: str) -> None:
        raise FileNotFoundError("404")

    paths = fetch_chelsa(tmp_path, retrieve=missing)

    assert paths == []
    assert not (tmp_path / "chelsa_tas_01.tif").exists()


def test_cli_fetch_climate_does_not_need_a_catalog() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "dayhike", "fetch-climate", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--dest" in result.stdout
    assert "--bounds" in result.stdout
    assert "--catalog" not in result.stdout


def test_sample_climate_rejects_incomplete_raster_set(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    rasters = tmp_path / "rasters"
    rasters.mkdir()
    _write_climate_tifs(
        rasters, west=14.9, south=44.9, east=15.2, north=45.2
    )
    (rasters / "chelsa_rsds_12.tif").unlink()

    with pytest.raises(ValueError, match="rsds_12"):
        sample_climate(catalog_path, rasters)


def test_cli_rank_month_out_of_range_exits_with_message_not_traceback(
    tmp_path: Path,
) -> None:
    import subprocess
    import sys

    path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, path)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "dayhike",
            "rank",
            "--catalog",
            str(path),
            "--month",
            "13",
            "Ridge Walk",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "1 to 12" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_sample_climate_warns_when_nothing_is_sampled(tmp_path: Path) -> None:
    import subprocess
    import sys

    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "dayhike",
            "sample-climate",
            "--catalog",
            str(catalog_path),
            "--climate",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0"
    assert "no vertices sampled" in result.stderr


def _store_vertices(catalog_path: Path) -> list[tuple[int, float, float]]:
    store = sqlite3.connect(geometry_store_path_for(catalog_path))
    try:
        return list(store.execute("SELECT dayhike_id, latitude, longitude FROM vertices"))
    finally:
        store.close()


def test_vectorised_sampling_matches_per_point_raster_sample(tmp_path: Path) -> None:
    import rasterio

    from dayhike.climate import _raster_values

    raster = tmp_path / "known.tif"
    data = np.arange(64, dtype="float32").reshape(8, 8)
    data[2, 3] = np.nan
    _write_tif(raster, data, west=14.99, south=44.99, east=15.05, north=45.12)
    points = [(45.0, 15.0), (45.03, 15.01), (45.1, 15.04), (45.11, 15.049)]
    points += [(44.0, 15.0), (45.0, 16.0)]
    points += [(lat, lon) for _id, lat, lon in _store_vertices(_catalog(tmp_path))]
    with rasterio.open(raster) as dataset:
        expected = [
            float(sample[0])
            if dataset.bounds.left <= lon < dataset.bounds.right
            and dataset.bounds.bottom < lat <= dataset.bounds.top
            else float("nan")
            for (lat, lon), sample in zip(
                points, dataset.sample([(lon, lat) for lat, lon in points]), strict=True
            )
        ]

    values = _raster_values(
        raster,
        np.array([lat for lat, _lon in points]),
        np.array([lon for _lat, lon in points]),
    )

    np.testing.assert_array_equal(values, np.array(expected))
    assert np.isnan(values[4]) and np.isnan(values[5])


def _catalog(tmp_path: Path) -> Path:
    catalog_path = tmp_path / "catalog.sqlite"
    if not catalog_path.exists():
        build_catalog(FIXTURE, catalog_path)
    return catalog_path


def test_hike_mean_counts_only_vertices_every_raster_covers(tmp_path: Path) -> None:
    import rasterio

    catalog_path = _catalog(tmp_path)
    rasters = tmp_path / "rasters"
    rasters.mkdir()
    # Covers Ridge Walk's lower vertices only; values differ per cell.
    bounds = {"west": 14.99, "south": 44.99, "east": 15.05, "north": 45.07}
    for month in range(1, 13):
        for var in VARIABLES:
            data = np.arange(64, dtype="float32").reshape(8, 8) + month
            _write_tif(rasters / f"chelsa_{var}_{month:02d}.tif", data, **bounds)
    ridge = [(lat, lon) for hike, lat, lon in _store_vertices(catalog_path) if hike == 1001]
    with rasterio.open(rasters / "chelsa_tas_07.tif") as dataset:
        inside = [(lat, lon) for lat, lon in ridge if lat < 45.07]
        samples = [float(v[0]) for v in dataset.sample([(lon, lat) for lat, lon in inside])]
    # One covered vertex has nodata in a single raster: it drops out of every month.
    with rasterio.open(rasters / "chelsa_pr_03.tif", "r+") as dataset:
        row, col = dataset.index(inside[0][1], inside[0][0])
        data = dataset.read(1)
        data[row, col] = np.nan
        dataset.write(data, 1)
    assert 2 <= len(inside) < len(ridge)

    sampled = sample_climate(catalog_path, rasters)

    assert sampled == len(inside) - 1
    hikes = {hike.id: hike for hike in Catalog.open(catalog_path).dayhikes}
    assert hikes[1001].climate_months[6][0] == pytest.approx(np.mean(samples[1:]))
    assert hikes[1002].climate_months == (None,) * 12
    assert hikes[1002].feature_vector.temperature_c is None


def test_resampling_clears_climate_of_hikes_no_longer_covered(tmp_path: Path) -> None:
    catalog_path = _catalog(tmp_path)
    wide = tmp_path / "wide"
    wide.mkdir()
    _write_climate_tifs(wide, west=14.9, south=44.9, east=15.6, north=45.7)
    sample_climate(catalog_path, wide)
    narrow = tmp_path / "narrow"
    narrow.mkdir()
    _write_climate_tifs(narrow, west=14.99, south=44.99, east=15.05, north=45.12)

    sample_climate(catalog_path, narrow)

    hikes = {hike.id: hike for hike in Catalog.open(catalog_path).dayhikes}
    assert hikes[1001].feature_vector.temperature_c == pytest.approx(10.0)
    assert hikes[1007].feature_vector.temperature_c is None
    assert hikes[1007].climate_months == (None,) * 12


def test_failed_raster_open_still_closes_datasets_opened_so_far(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import rasterio

    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    rasters = tmp_path / "rasters"
    rasters.mkdir()
    _write_climate_tifs(rasters, west=14.99, south=44.99, east=15.05, north=45.12)
    opened = []
    real_open = rasterio.open

    def failing_open(path, *args, **kwargs):
        if len(opened) == 5:
            raise OSError("unreadable raster")
        dataset = real_open(path, *args, **kwargs)
        opened.append(dataset)
        return dataset

    monkeypatch.setattr(rasterio, "open", failing_open)

    with pytest.raises(OSError, match="unreadable raster"):
        sample_climate(catalog_path, rasters)

    assert len(opened) == 5
    assert all(dataset.closed for dataset in opened)
    store_path = geometry_store_path_for(catalog_path)
    assert not Path(str(store_path) + "-wal").exists()


def test_sample_climate_removes_similarity_facts(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    directory = build_similarity(catalog_path, tmp_path / "dem")
    raster_dir = tmp_path / "sample_climate"
    raster_dir.mkdir()
    _write_climate_tifs(raster_dir, west=14.99, south=44.99, east=15.2, north=45.12)
    messages: list[str] = []

    assert sample_climate(catalog_path, raster_dir, progress=messages.append) > 0

    assert not directory.exists()
    assert messages[-1] == (
        f"removed stale similarity facts {directory}; run `dayhike build-similarity`"
    )
    with pytest.raises(StaleSimilarity, match="build-similarity"):
        rank_dayhikes(Catalog.open(catalog_path), ["Ridge Walk"])
