"""A rebuilt data directory equals the reference it replaces.

Slow, opt-in (DAYHIKE_REAL_DATA=1); compares DAYHIKE_DATA_DIR (default data/)
with DAYHIKE_REFERENCE_DIR, frozen copies of the old outputs plus
raw-manifest.tsv and golden/ (README "Clean rebuild"). Skipped without a
reference.

The pipeline is deterministic (same snapshot, same uv.lock, a fixed
Calibration seed), so equal means exact. The one allowed change is wider
raster coverage: the reference sampled LandCover and climate over part of
Europe only. Values that read those rasters must be equal for DayHikes whose
every vertex lies strictly inside the old coverage, may appear where the
reference had none, and may never disappear. (For climate, a DayHike with a
vertex exactly on a raster cell edge is left out of the exact comparison.)
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

import numpy as np
import pytest
import rebuild_checks as rc

from dayhike import SimilarityStore, geometry_store_path_for, similarity_dir_for
from dayhike.similarity import CALIBRATION_KEYS, FACT_COLUMNS, LANDCOVER_SHARE_COLUMNS

pytestmark = [*rc.real_data, rc.needs_reference]
# Old raster coverage (south, west, north, east): 24 WorldCover tiles and the
# fetch-climate default clip of the reference.
OLD_LANDCOVER = (39.0, 3.0, 51.0, 21.0)
OLD_CLIMATE = (41.8, 5.9, 50.6, 20.4)
LANDCOVER = ("tree_fraction", "shrub_fraction", "grass_fraction", "bare_fraction", "snow_fraction")
CLIMATE = ("temperature_c", "precipitation_mm", "solar_radiation")
CLIMATE_FACTS = tuple(name for name in FACT_COLUMNS if name.split("_")[0] in ("temperature", "precipitation", "solar"))


def _reference(name: str) -> sqlite3.Connection:
    return rc.connect_ro(rc.REFERENCE_DIR / name, immutable=True)


@pytest.fixture(scope="module")
def catalogs() -> tuple[sqlite3.Connection, sqlite3.Connection]:
    old, new = _reference("catalog.sqlite"), rc.connect_ro(rc.catalog_path())
    yield old, new
    old.close()
    new.close()


@pytest.fixture(scope="module")
def geometries() -> tuple[sqlite3.Connection, sqlite3.Connection]:
    old = _reference("catalog.geometry.sqlite")
    new = rc.connect_ro(geometry_store_path_for(rc.catalog_path()))
    yield old, new
    old.close()
    new.close()


@pytest.fixture(scope="module")
def inside_old(geometries) -> dict[str, set[int]]:
    """DayHike ids whose every vertex lies strictly inside the old LandCover / climate coverage.

    For climate, also off the edges of the 30" cells: a vertex exactly on an
    edge (lon 6.65) falls in either cell, depending on the float origin of
    the clipped raster, and the old and new clips start at different places.
    """
    _, new = geometries
    inside: dict[str, set[int]] = {"landcover": set(), "climate": set()}
    for dayhike_id, south, north, west, east, on_edge in new.execute(
        "SELECT dayhike_id, MIN(latitude), MAX(latitude), MIN(longitude), MAX(longitude), "
        "MAX(ABS(latitude * 120 - ROUND(latitude * 120)) < 1e-6 "
        "OR ABS(longitude * 120 - ROUND(longitude * 120)) < 1e-6) "
        "FROM vertices GROUP BY dayhike_id"
    ):
        for name, (s, w, n, e) in (("landcover", OLD_LANDCOVER), ("climate", OLD_CLIMATE)):
            if s < south and north < n and w < west and east < e and not (name == "climate" and on_edge):
                inside[name].add(dayhike_id)
    return inside


def _assert_equal(diff: rc.TableDiff) -> None:
    assert diff.equal, diff.report()


def test_dayhike_ids_names_lengths_and_difficulty_equal_reference(catalogs) -> None:
    _assert_equal(rc.compare_tables(*catalogs, "dayhikes", ["id"], ["name", "name_origin", "length_m", "difficulty"]))


def test_ingest_report_equals_reference(catalogs) -> None:
    old, new = (
        json.loads(c.execute("SELECT value FROM catalog_meta WHERE key = 'ingest_report'").fetchone()[0])
        for c in catalogs
    )

    assert new == old


def test_trailheads_and_amenity_facts_equal_reference(catalogs) -> None:
    _assert_equal(rc.compare_tables(*catalogs, "trailheads", ["dayhike_id"], ["latitude", "longitude"]))
    _assert_equal(rc.compare_tables(*catalogs, "amenity_facts", ["dayhike_id", "kind"], ["count", "nearest_m"]))


def test_elevation_columns_equal_reference(catalogs) -> None:
    columns = ["length_m", "difficulty_ordinal", "elevation_min_m", "elevation_max_m", "elevation_gain_m"]
    _assert_equal(rc.compare_tables(*catalogs, "feature_vectors", ["dayhike_id"], columns, abs_tol=1e-9))


def _changes_inside(diff: rc.TableDiff, inside: set[int]) -> list:
    return [(key, old, new) for key, old, new in diff.changed if key[0] in inside]


def test_landcover_and_climate_equal_reference_where_reference_had_them(catalogs, inside_old) -> None:
    old, new = catalogs
    for names, coverage in ((LANDCOVER, "landcover"), (CLIMATE, "climate")):
        diff = rc.compare_tables(old, new, "feature_vectors", ["dayhike_id"], list(names))
        assert diff.left == [] and diff.joined == []
        lost = [(key, before, after) for key, before, after in diff.changed if None not in before and None in after]
        assert lost[:20] == [], f"{coverage} lost"
        assert _changes_inside(diff, inside_old[coverage])[:20] == [], f"{coverage} changed inside old coverage"
    months = rc.compare_tables(old, new, "climate_months", ["dayhike_id", "month"], list(CLIMATE))
    assert months.left[:20] == []
    assert _changes_inside(months, inside_old["climate"])[:20] == []


def _walk_hashes(geometry: sqlite3.Connection) -> dict[int, tuple[str, str]]:
    """Per DayHike: sha256 of (seq, node_id, lat, lon, ele) rows and of the landcover column."""
    hashes = {}
    current, core, cover = None, hashlib.sha256(), hashlib.sha256()
    cursor = geometry.execute(
        "SELECT dayhike_id, seq, node_id, latitude, longitude, ele, landcover FROM vertices ORDER BY dayhike_id, seq"
    )
    while rows := cursor.fetchmany(50_000):
        for dayhike_id, *vertex, landcover in rows:
            if dayhike_id != current:
                if current is not None:
                    hashes[current] = (core.hexdigest(), cover.hexdigest())
                current, core, cover = dayhike_id, hashlib.sha256(), hashlib.sha256()
            core.update(repr(vertex).encode())
            cover.update(repr(landcover).encode())
    if current is not None:
        hashes[current] = (core.hexdigest(), cover.hexdigest())
    return hashes


def test_geometry_equals_reference(geometries, inside_old) -> None:
    old, new = geometries
    _assert_equal(
        rc.compare_tables(
            old, new, "dayhikes", ["id"],
            ["name", "name_origin", "length_m", "unconnected_m", "difficulty", "members", "relation_tags"],
        )
    )  # fmt: skip
    _assert_equal(
        rc.compare_tables(
            old, new, "ways", ["dayhike_id", "seq"],
            ["way_id", "node_ids", "tags", "in_chain", "leftover", "walked_m", "walk_order"],
        )
    )  # fmt: skip
    _assert_equal(rc.compare_tables(old, new, "amenities", ["osm_key"], ["kind", "latitude", "longitude", "tags"]))
    before, after = _walk_hashes(old), _walk_hashes(new)
    assert sorted(before.keys() ^ after.keys()) == []
    assert [i for i in before if before[i][0] != after[i][0]][:20] == []
    inside = inside_old["landcover"]
    assert [i for i in before if i in inside and before[i][1] != after[i][1]][:20] == []


def _stores() -> tuple[SimilarityStore, SimilarityStore]:
    return SimilarityStore.open(rc.REFERENCE_DIR / "catalog.sqlite"), SimilarityStore.open(rc.catalog_path())


def _differing(old: np.ndarray, new: np.ndarray, ids: np.ndarray) -> list[int]:
    same = (old == new) | (np.isnan(old) & np.isnan(new))
    return ids[~same].tolist()


def test_similarity_facts_equal_reference(inside_old) -> None:
    old, new = _stores()
    assert np.array_equal(old.ids, new.ids)
    ids = np.asarray(new.ids)
    by_coverage = {
        **{name: "landcover" for name in LANDCOVER_SHARE_COLUMNS},
        **{name: "climate" for name in CLIMATE_FACTS},
    }
    differing = {}
    for name in FACT_COLUMNS:
        before, after = np.asarray(old.column(name)), np.asarray(new.column(name))
        if name not in by_coverage:
            changed = _differing(before, after, ids)
        else:
            inside = np.isin(ids, list(inside_old[by_coverage[name]]))
            changed = _differing(before[inside], after[inside], ids[inside])
            changed += ids[~np.isnan(before) & np.isnan(after)].tolist()
        if changed:
            differing[name] = changed[:20]
    assert differing == {}
    before, after = np.asarray(old.profile), np.asarray(new.profile)
    same = ((before == after) | (np.isnan(before) & np.isnan(after))).all(axis=1)
    assert ids[~same].tolist()[:20] == []


def test_calibration_equals_reference_for_measures_not_touched_by_coverage() -> None:
    old, new = _stores()
    keys = [
        index for index, key in enumerate(CALIBRATION_KEYS) if key.split("@")[0] not in rc.COVERAGE_MEASURES
    ]
    assert len(keys) == 22
    differing = [
        CALIBRATION_KEYS[index]
        for index in keys
        if not np.array_equal(old.quantiles[index], new.quantiles[index], equal_nan=True)
    ]

    assert differing == []


def test_golden_rankings_reproduce() -> None:
    golden: dict[tuple[str, int], list[dict]] = {}
    for row in rc.read_tsv(rc.REFERENCE_DIR / "golden" / "rankings.tsv"):
        golden.setdefault((row["set"], int(row["seed"])), []).append(row)
    store = SimilarityStore.open(rc.catalog_path())
    sets = rc.golden_weight_sets()
    assert len(golden) == len(sets) * len(rc.GOLDEN_SEEDS)

    for (name, seed), rows in golden.items():
        got = rc.top_matches(store, seed, sets[name])
        assert [g[0] for g in got] == [int(row["dayhike_id"]) for row in rows], (name, seed)
        for (_, similarity, raw), row in zip(got, rows):
            assert similarity == pytest.approx(float(row["similarity"]), abs=1e-6)
            assert raw == pytest.approx(float(row["raw"]), abs=1e-6)


def test_raw_inputs_are_unchanged() -> None:
    expected = {
        rel: entry
        for rel, entry in rc.read_manifest(rc.REFERENCE_DIR / "raw-manifest.tsv").items()
        if rel == "osm/europe-latest.osm.pbf" or rel.startswith(("dem/", "landcover/"))
    }
    actual = rc.raw_manifest(
        rc.DATA_DIR, [rel for rel in expected if (rc.DATA_DIR / rel).exists()], hashed=["osm/europe-latest.osm.pbf"]
    )

    assert rc.manifest_diff(expected, actual) == []
    assert len(expected) > 1_800
