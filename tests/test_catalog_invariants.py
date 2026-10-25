"""Structural invariants of a full Catalog: one row per DayHike, values in range.

Slow, opt-in (DAYHIKE_REAL_DATA=1); reads DAYHIKE_DATA_DIR (default data/)
after ingest, the three sample steps and build-similarity. Bands come from the
2026-09-18 Europe Catalog plus a margin; the ingest report must equal the
recorded one when the data holds that snapshot.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
import rebuild_checks as rc

from dayhike import geometry_store_path_for

pytestmark = rc.real_data
SAC_SCALE = {
    "hiking": 1, "mountain_hiking": 2, "demanding_mountain_hiking": 3, "alpine_hiking": 4,
    "demanding_alpine_hiking": 5, "difficult_alpine_hiking": 6,
}  # fmt: skip
AMENITY_FACT_KINDS = ("drinking_water", "hut", "parking", "restaurant_cafe")
LANDCOVER = ("tree_fraction", "shrub_fraction", "grass_fraction", "bare_fraction", "snow_fraction")
CLIMATE = ("temperature_c", "precipitation_mm", "solar_radiation")
# Allowed share of DayHikes without the value, after a Europe-coverage rebuild.
NULL_BUDGETS = {"elevation_gain_m": 0.0005, "tree_fraction": 0.01, "temperature_c": 0.01}


@pytest.fixture(scope="module")
def catalog() -> sqlite3.Connection:
    connection = rc.connect_ro(rc.catalog_path())
    yield connection
    connection.close()


def _one(connection: sqlite3.Connection, sql: str) -> int | float:
    return connection.execute(sql).fetchone()[0]


def test_schema_is_exactly_the_current_schema(catalog: sqlite3.Connection) -> None:
    geometry = rc.connect_ro(geometry_store_path_for(rc.catalog_path()))
    try:
        assert rc.schema(geometry) == rc.GEOMETRY_SCHEMA
    finally:
        geometry.close()
    assert rc.schema(catalog) == rc.CATALOG_SCHEMA
    work_files = [
        path.name
        for pattern in ("*.work", "*.tmp", "*-wal", "*-shm", "*.writing.osm.pbf", "*.part")
        for path in rc.DATA_DIR.rglob(pattern)
    ]
    assert work_files == []


def test_ingest_report_is_consistent_and_matches_the_snapshot(catalog: sqlite3.Connection) -> None:
    report = json.loads(_one(catalog, "SELECT value FROM catalog_meta WHERE key = 'ingest_report'"))

    assert report["kept"] == report["named_osm_name"] + report["named_osm_ref"] + report["named_synthetic"]
    assert report["kept"] == _one(catalog, "SELECT COUNT(*) FROM dayhikes")
    if rc.same_snapshot():
        assert report == rc.RECORDED_INGEST_REPORT


def test_every_table_has_one_row_per_dayhike(catalog: sqlite3.Connection) -> None:
    n = _one(catalog, "SELECT COUNT(*) FROM dayhikes")

    assert _one(catalog, "SELECT COUNT(*) FROM trailheads") == n
    assert _one(catalog, "SELECT COUNT(*) FROM feature_vectors") == n
    assert _one(catalog, "SELECT COUNT(*) FROM amenity_facts") == len(AMENITY_FACT_KINDS) * n
    kinds = [kind for (kind,) in catalog.execute("SELECT DISTINCT kind FROM amenity_facts ORDER BY kind")]
    assert kinds == list(AMENITY_FACT_KINDS)
    with_climate = _one(catalog, "SELECT COUNT(*) FROM feature_vectors WHERE temperature_c IS NOT NULL")
    assert _one(catalog, "SELECT COUNT(*) FROM climate_months") == 12 * with_climate
    assert _one(catalog, "SELECT COUNT(DISTINCT dayhike_id) FROM climate_months") == with_climate
    assert _one(catalog, "SELECT COUNT(*) FROM labels") == 0


def test_ids_are_unique_and_shared_by_both_sqlite_files(catalog: sqlite3.Connection) -> None:
    ids = [i for (i,) in catalog.execute("SELECT id FROM dayhikes ORDER BY id")]
    geometry = rc.connect_ro(geometry_store_path_for(rc.catalog_path()))
    try:
        geometry_ids = [i for (i,) in geometry.execute("SELECT id FROM dayhikes ORDER BY id")]
    finally:
        geometry.close()

    assert len(ids) == len(set(ids))
    assert geometry_ids == ids
    for table in ("trailheads", "feature_vectors"):
        assert [i for (i,) in catalog.execute(f"SELECT dayhike_id FROM {table} ORDER BY 1")] == ids


def _outside(catalog: sqlite3.Connection, table: str, condition: str) -> list[tuple]:
    return catalog.execute(f"SELECT * FROM {table} WHERE {condition} LIMIT 20").fetchall()


def test_values_are_in_range(catalog: sqlite3.Connection) -> None:
    south, west, north, east = rc.EUROPE_BOUNDS
    fractions = " + ".join(LANDCOVER)
    checks = {
        "dayhikes": ["length_m NOT BETWEEN 1000 AND 20000"],
        "trailheads": [f"latitude NOT BETWEEN {south} AND {north}", f"longitude NOT BETWEEN {west} AND {east}"],
        "feature_vectors": [
            "length_m NOT BETWEEN 1000 AND 20000",
            "elevation_min_m < -50 OR elevation_max_m > 5100",
            "elevation_min_m > elevation_max_m",
            "elevation_gain_m NOT BETWEEN 0 AND 5200",
            *(f"{name} NOT BETWEEN 0 AND 1" for name in LANDCOVER),
            f"{fractions} > 1 + 1e-9",
            "temperature_c NOT BETWEEN -15 AND 30",
            "precipitation_mm NOT BETWEEN 0 AND 400",
            "solar_radiation NOT BETWEEN 0 AND 30000",
        ],
        "climate_months": [
            "temperature_c NOT BETWEEN -30 AND 40",
            "precipitation_mm NOT BETWEEN 0 AND 1500",
            "solar_radiation NOT BETWEEN 0 AND 35000",
            "month NOT BETWEEN 1 AND 12",
        ],
        "amenity_facts": [
            "(count = 0) != (nearest_m IS NULL)",
            "nearest_m > 20000",
            "count < 0",
        ],
    }
    outside = {
        f"{table}: {condition}": rows
        for table, conditions in checks.items()
        for condition in conditions
        if (rows := _outside(catalog, table, condition))
    }
    assert outside == {}
    difficulties = catalog.execute(
        "SELECT d.difficulty, f.difficulty_ordinal, COUNT(*) FROM dayhikes d "
        "JOIN feature_vectors f ON f.dayhike_id = d.id GROUP BY 1, 2"
    ).fetchall()
    for difficulty, ordinal, _count in difficulties:
        assert SAC_SCALE.get(difficulty) == ordinal, (difficulty, ordinal)


def test_landcover_and_climate_are_all_null_or_all_set_within_a_row(catalog: sqlite3.Connection) -> None:
    for names in (LANDCOVER, CLIMATE):
        all_null = " AND ".join(f"{name} IS NULL" for name in names)
        all_set = " AND ".join(f"{name} IS NOT NULL" for name in names)
        assert _outside(catalog, "feature_vectors", f"NOT ({all_null}) AND NOT ({all_set})") == []


def test_null_budgets(catalog: sqlite3.Connection) -> None:
    n = _one(catalog, "SELECT COUNT(*) FROM feature_vectors")
    shares = {
        name: _one(catalog, f"SELECT COUNT(*) FROM feature_vectors WHERE {name} IS NULL") / n
        for name in NULL_BUDGETS
    }
    print("null shares", shares)

    assert {name: share for name, share in shares.items() if share > NULL_BUDGETS[name]} == {}


def test_trailhead_is_the_first_vertex_of_every_walk() -> None:
    geometry = rc.connect_ro(geometry_store_path_for(rc.catalog_path()))
    try:
        geometry.execute("ATTACH DATABASE ? AS catalog", (f"file:{rc.catalog_path()}?mode=ro",))
        moved = geometry.execute(
            "SELECT t.dayhike_id, t.latitude, t.longitude, v.latitude, v.longitude "
            "FROM catalog.trailheads t LEFT JOIN vertices v ON v.dayhike_id = t.dayhike_id AND v.seq = 0 "
            "WHERE v.latitude IS NULL OR v.latitude != t.latitude OR v.longitude != t.longitude LIMIT 20"
        ).fetchall()
    finally:
        geometry.close()

    assert moved == []
