import sqlite3
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from dayhike import Catalog, build_catalog, geometry_store_path_for, rank_dayhikes, sample_climate
from dayhike.climate import fetch_worldclim, worldclim_zip_url
from dayhike.dem import Bounds

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
CLIMATE_FEATURES = ("temperature_c", "precipitation_mm", "solar_radiation")
WORLDCLIM_ATTRIBUTION = (
    "Fick, S.E. and R.J. Hijmans, 2017. WorldClim 2: new 1km spatial resolution "
    "climate surfaces for global land areas. International Journal of Climatology "
    "37 (12): 4302-4315."
)


def _write_climate_tifs(
    dest: Path,
    *,
    west: float,
    south: float,
    east: float,
    north: float,
    tavg: float | tuple[float, ...] = 10.0,
    prec: float | tuple[float, ...] = 80.0,
    srad: float | tuple[float, ...] = 15000.0,
) -> None:
    import rasterio
    from rasterio.transform import from_bounds

    tavg_vals = (tavg,) * 12 if isinstance(tavg, (int, float)) else tuple(tavg)
    prec_vals = (prec,) * 12 if isinstance(prec, (int, float)) else tuple(prec)
    srad_vals = (srad,) * 12 if isinstance(srad, (int, float)) else tuple(srad)
    height = width = 8
    transform = from_bounds(west, south, east, north, width, height)
    for month in range(1, 13):
        for var, value in (
            ("tavg", tavg_vals[month - 1]),
            ("prec", prec_vals[month - 1]),
            ("srad", srad_vals[month - 1]),
        ):
            path = dest / f"wc2.1_30s_{var}_{month:02d}.tif"
            data = np.full((height, width), float(value), dtype="float32")
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


def _zip_worldclim_variable(zip_path: Path, src_dir: Path, variable: str) -> None:
    import zipfile

    with zipfile.ZipFile(zip_path, "w") as zf:
        for month in range(1, 13):
            tif = src_dir / f"wc2.1_30s_{variable}_{month:02d}.tif"
            zf.write(tif, tif.name)


def _retrieve_worldclim_zip(
    url: str,
    filename: str,
    *,
    west: float,
    south: float,
    east: float,
    north: float,
    tavg: float | tuple[float, ...] = 10.0,
    prec: float | tuple[float, ...] = 80.0,
    srad: float | tuple[float, ...] = 15000.0,
) -> None:
    variable = Path(url).name.removeprefix("wc2.1_30s_").removesuffix(".zip")
    staging = Path(filename).parent / variable
    staging.mkdir(exist_ok=True)
    _write_climate_tifs(
        staging,
        west=west,
        south=south,
        east=east,
        north=north,
        tavg=tavg,
        prec=prec,
        srad=srad,
    )
    _zip_worldclim_variable(Path(filename), staging, variable)


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
        assert "vertex_climate" in tables
        assert (
            store.execute("SELECT COUNT(*) FROM vertex_climate").fetchone()[0] == 0
        )

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
    for name in CLIMATE_FEATURES:
        assert name in catalog.scaler.feature_names


def test_sample_writes_twelve_rows_on_covered_vertices_and_leaves_uncovered_unset(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    # Covers Ridge Walk (lon 15.0, lat 45.0–45.09), not Valley Path (lon 15.1).
    _write_climate_tifs(
        tmp_path,
        west=14.99,
        south=44.99,
        east=15.05,
        north=45.12,
        tavg=12.5,
        prec=90.0,
        srad=18000.0,
    )

    sampled = sample_climate(catalog_path, tmp_path)

    assert sampled > 0
    store = sqlite3.connect(geometry_store_path_for(catalog_path))
    try:
        ridge = list(
            store.execute(
                "SELECT seq, month, temperature_c, precipitation_mm, solar_radiation "
                "FROM vertex_climate WHERE dayhike_id = 1001 ORDER BY seq, month"
            )
        )
        valley_count = store.execute(
            "SELECT COUNT(*) FROM vertex_climate WHERE dayhike_id = 1002"
        ).fetchone()[0]
        ridge_vertices = store.execute(
            "SELECT COUNT(*) FROM vertices WHERE dayhike_id = 1001"
        ).fetchone()[0]
        assert ridge
        months_by_seq: dict[int, list[int]] = {}
        for seq, month, temperature_c, precipitation_mm, solar_radiation in ridge:
            months_by_seq.setdefault(seq, []).append(month)
            assert temperature_c == pytest.approx(12.5, abs=0.01)
            assert precipitation_mm == pytest.approx(90.0, abs=0.01)
            assert solar_radiation == pytest.approx(18000.0, abs=0.01)
        assert set(months_by_seq) == set(range(ridge_vertices))
        assert all(months == list(range(1, 13)) for months in months_by_seq.values())
        assert valley_count == 0
        assert store.execute(
            "SELECT value FROM geometry_meta WHERE key = 'attribution'"
        ).fetchone() == ("© OpenStreetMap contributors",)
        assert store.execute(
            "SELECT value FROM geometry_meta WHERE key = 'worldclim_attribution'"
        ).fetchone() == (WORLDCLIM_ATTRIBUTION,)
    finally:
        store.close()
    store_path = geometry_store_path_for(catalog_path)
    assert not Path(str(store_path) + "-wal").exists()
    assert not Path(str(store_path) + "-shm").exists()


def test_catalog_stores_monthly_means_and_regime_and_keeps_uncovered_hikes(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    catalog = build_catalog(FIXTURE, catalog_path)
    kept_ids = {hike.id for hike in catalog.dayhikes}
    tavg = (1.0, 2.0, 3.0, 4.0, 5.0, 10.0, 20.0, 30.0, 9.0, 8.0, 7.0, 6.0)
    prec = (10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0, 110.0, 120.0)
    srad = (
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
        tavg=tavg,
        prec=prec,
        srad=srad,
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


def test_sample_adds_missing_climate_tables_in_place_and_refits_scaler(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    catalog = build_catalog(FIXTURE, catalog_path)
    kept_ids = [hike.id for hike in catalog.dayhikes]
    store_path = geometry_store_path_for(catalog_path)
    store = sqlite3.connect(store_path)
    catalog_db = sqlite3.connect(catalog_path)
    try:
        store.execute("DROP TABLE vertex_climate")
        store.commit()
        catalog_db.execute("DROP TABLE climate_months")
        for name in CLIMATE_FEATURES:
            catalog_db.execute(f"ALTER TABLE feature_vectors DROP COLUMN {name}")
        catalog_db.execute(
            "DELETE FROM scaler WHERE feature_name IN ({})".format(
                ",".join("?" * len(CLIMATE_FEATURES))
            ),
            CLIMATE_FEATURES,
        )
        catalog_db.commit()
    finally:
        store.close()
        catalog_db.close()

    tavg = (0.0,) * 6 + (21.0,) + (0.0,) * 5
    _write_climate_tifs(
        tmp_path,
        west=14.99,
        south=44.99,
        east=15.05,
        north=45.12,
        tavg=tavg,
        prec=40.0,
        srad=12000.0,
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
        scaler_rows = {
            name: (mean, scale)
            for name, mean, scale in catalog_db.execute(
                "SELECT feature_name, mean, scale FROM scaler"
            )
        }
        assert set(CLIMATE_FEATURES) <= set(scaler_rows)
        assert scaler_rows["temperature_c"][0] == pytest.approx(7.0)
        july = {
            name: (mean, scale)
            for name, mean, scale in catalog_db.execute(
                "SELECT feature_name, mean, scale FROM scaler_month WHERE month = 7 "
                "ORDER BY position"
            )
        }
        assert tuple(july) == CLIMATE_FEATURES
        assert july["temperature_c"][0] == pytest.approx(21.0)
        assert july["temperature_c"][0] != scaler_rows["temperature_c"][0]
        months = {
            row[0]
            for row in catalog_db.execute("SELECT DISTINCT month FROM scaler_month")
        }
        assert months == set(range(1, 13))
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
        assert "vertex_climate" in store_tables
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
    tavg = (0.0,) * 6 + (21.0,) + (0.0,) * 5
    _write_climate_tifs(
        tmp_path,
        west=14.99,
        south=44.99,
        east=15.05,
        north=45.12,
        tavg=tavg,
        prec=40.0,
        srad=12000.0,
    )
    sample_climate(catalog_path, tmp_path)
    return catalog_path


def test_ranking_with_month_uses_july_not_june_august_regime(tmp_path: Path) -> None:
    catalog = Catalog.open(_sample_ridge_july_spike(tmp_path))
    seed = next(hike for hike in catalog.dayhikes if hike.name == "Ridge Walk")
    july_twin = replace(seed, id=9001, name="July Twin")
    july = seed.climate_months[6]
    assert july is not None
    months = list(seed.climate_months)
    months[6] = (
        seed.feature_vector.temperature_c,
        july[1],
        july[2],
    )
    regime_twin = replace(seed, id=9002, name="Regime Twin", climate_months=tuple(months))
    catalog = replace(catalog, dayhikes=(seed, july_twin, regime_twin))

    without_month = rank_dayhikes(catalog, ["Ridge Walk"])
    with_july = rank_dayhikes(catalog, ["Ridge Walk"], month=7)

    without_scores = {item.dayhike.name: item.score for item in without_month.items}
    july_scores = {item.dayhike.name: item.score for item in with_july.items}
    assert without_scores["July Twin"] == pytest.approx(without_scores["Regime Twin"])
    assert july_scores["July Twin"] > july_scores["Regime Twin"]
    assert [item.dayhike.name for item in with_july.items][0] == "July Twin"


def test_query_month_outside_1_to_12_fails(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")

    with pytest.raises(ValueError, match="1 to 12"):
        rank_dayhikes(catalog, ["Ridge Walk"], month=0)
    with pytest.raises(ValueError, match="1 to 12"):
        rank_dayhikes(catalog, ["Ridge Walk"], month=13)


def test_query_month_fails_when_climate_has_not_been_sampled(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")

    with pytest.raises(ValueError, match="climate has not been sampled"):
        rank_dayhikes(catalog, ["Ridge Walk"], month=7)


def test_omitting_query_month_ranks_unsampled_catalog(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")

    ranking = rank_dayhikes(catalog, ["Ridge Walk"])

    assert ranking.items
    assert all(item.drive_time is None for item in ranking.items)


def test_warm_sunny_july_seed_ranks_similar_july_ahead_of_different_july(
    tmp_path: Path,
) -> None:
    catalog = Catalog.open(_sample_ridge_july_spike(tmp_path))
    seed = next(hike for hike in catalog.dayhikes if hike.name == "Ridge Walk")
    similar = replace(seed, id=9001, name="Warm Twin")
    july = seed.climate_months[6]
    assert july is not None
    months = list(seed.climate_months)
    months[6] = (0.0, july[1], 0.0)
    different = replace(seed, id=9002, name="Cold Twin", climate_months=tuple(months))
    catalog = replace(catalog, dayhikes=(seed, similar, different))

    ranking = rank_dayhikes(catalog, ["Ridge Walk"], month=7)
    names = [item.dayhike.name for item in ranking.items]
    scores = {item.dayhike.name: item.score for item in ranking.items}

    assert names[0] == "Warm Twin"
    assert scores["Warm Twin"] > scores["Cold Twin"]


def test_forested_seed_in_july_ranks_forested_neighbour_ahead_of_barer_hotter(
    tmp_path: Path,
) -> None:
    catalog = Catalog.open(_sample_ridge_july_spike(tmp_path))
    seed = next(hike for hike in catalog.dayhikes if hike.name == "Ridge Walk")
    forested_vector = replace(
        seed.feature_vector,
        tree_fraction=1.0,
        shrub_fraction=0.0,
        grass_fraction=0.0,
        bare_fraction=0.0,
        snow_fraction=0.0,
    )
    seed = replace(seed, feature_vector=forested_vector)
    forested = replace(seed, id=9001, name="Forested Twin")
    july = seed.climate_months[6]
    assert july is not None
    months = list(seed.climate_months)
    months[6] = (july[0] + 15.0, july[1], july[2] + 4000.0)
    barer = replace(
        seed,
        id=9002,
        name="Bare Hot Twin",
        feature_vector=replace(
            forested_vector,
            tree_fraction=0.0,
            bare_fraction=1.0,
        ),
        climate_months=tuple(months),
    )
    catalog = replace(catalog, dayhikes=(seed, forested, barer))

    ranking = rank_dayhikes(catalog, ["Ridge Walk"], month=7)
    names = [item.dayhike.name for item in ranking.items]
    scores = {item.dayhike.name: item.score for item in ranking.items}

    assert names[0] == "Forested Twin"
    assert scores["Forested Twin"] > scores["Bare Hot Twin"]


def test_drive_time_filter_still_runs_after_ranking_with_month(tmp_path: Path) -> None:
    from dayhike import Origin, filter_by_drive_time

    catalog = Catalog.open(_sample_ridge_july_spike(tmp_path))
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
    assert "drive_time" not in catalog.scaler.feature_names


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
        tavg=12.5,
        prec=90.0,
        srad=18000.0,
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


def test_worldclim_zip_url_points_at_ucdavis_30s_base_zips() -> None:
    assert worldclim_zip_url("tavg") == (
        "https://geodata.ucdavis.edu/climate/worldclim/2_1/base/wc2.1_30s_tavg.zip"
    )
    assert worldclim_zip_url("prec") == (
        "https://geodata.ucdavis.edu/climate/worldclim/2_1/base/wc2.1_30s_prec.zip"
    )
    assert worldclim_zip_url("srad") == (
        "https://geodata.ucdavis.edu/climate/worldclim/2_1/base/wc2.1_30s_srad.zip"
    )


def test_fetch_worldclim_extracts_missing_months_and_skips_existing(
    tmp_path: Path,
) -> None:
    recorded: list[str] = []
    dest = tmp_path / "climate"
    dest.mkdir()
    for month in range(1, 13):
        (dest / f"wc2.1_30s_tavg_{month:02d}.tif").write_bytes(b"already-tavg")
    existing_prec = dest / "wc2.1_30s_prec_01.tif"
    existing_prec.write_bytes(b"already-prec")

    def fake_retrieve(url: str, filename: str) -> None:
        recorded.append(url)
        _retrieve_worldclim_zip(
            url, filename, west=14.99, south=44.99, east=15.05, north=45.12
        )

    paths = fetch_worldclim(dest, retrieve=fake_retrieve)

    assert recorded == [worldclim_zip_url("prec"), worldclim_zip_url("srad")]
    assert (dest / "wc2.1_30s_tavg_01.tif").read_bytes() == b"already-tavg"
    assert existing_prec.read_bytes() == b"already-prec"
    assert (dest / "wc2.1_30s_prec_02.tif").stat().st_size > 0
    assert (dest / "wc2.1_30s_srad_12.tif").stat().st_size > 0
    assert dest / "wc2.1_30s_tavg_01.tif" in paths
    assert existing_prec in paths
    assert dest / "wc2.1_30s_srad_12.tif" in paths


def test_fetch_worldclim_writes_month_tifs_sample_climate_reads(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    dest = tmp_path / "climate"

    def fake_retrieve(url: str, filename: str) -> None:
        _retrieve_worldclim_zip(
            url,
            filename,
            west=14.99,
            south=44.99,
            east=15.05,
            north=45.12,
            tavg=12.5,
            prec=90.0,
            srad=18000.0,
        )

    paths = fetch_worldclim(dest, retrieve=fake_retrieve)

    assert len(paths) == 36
    assert dest / "wc2.1_30s_tavg_07.tif" in paths
    assert dest / "wc2.1_30s_prec_01.tif" in paths
    assert dest / "wc2.1_30s_srad_12.tif" in paths
    assert sample_climate(catalog_path, dest) > 0


def test_fetch_worldclim_clips_to_requested_bounds(tmp_path: Path) -> None:
    import rasterio

    dest = tmp_path / "climate"
    requested = Bounds(south=45.0, west=15.0, north=45.2, east=15.2)

    def fake_retrieve(url: str, filename: str) -> None:
        _retrieve_worldclim_zip(
            url, filename, west=14.9, south=44.9, east=15.3, north=45.3
        )

    fetch_worldclim(dest, requested, retrieve=fake_retrieve)

    with rasterio.open(dest / "wc2.1_30s_tavg_01.tif") as dataset:
        assert dataset.bounds.left == pytest.approx(15.0, abs=0.06)
        assert dataset.bounds.bottom == pytest.approx(45.0, abs=0.06)
        assert dataset.bounds.right == pytest.approx(15.2, abs=0.06)
        assert dataset.bounds.top == pytest.approx(45.2, abs=0.06)


def test_fetch_worldclim_skips_missing_remote_zips(tmp_path: Path) -> None:
    def missing(_url: str, _filename: str) -> None:
        raise FileNotFoundError("404")

    paths = fetch_worldclim(tmp_path, retrieve=missing)

    assert paths == []
    assert not (tmp_path / "wc2.1_30s_tavg_01.tif").exists()


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
    (rasters / "wc2.1_30s_srad_12.tif").unlink()

    with pytest.raises(ValueError, match="srad_12"):
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
