from dataclasses import fields
from pathlib import Path

import pytest

from dayhike import Catalog, FeatureVector, build_catalog

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
RIDGE_WALK_ID = 1001
VALLEY_PATH_ID = 1002


def _by_id(catalog: Catalog, hike_id: int):
    return next(hike for hike in catalog.dayhikes if hike.id == hike_id)


def test_feature_vector_length_and_difficulty_ordinal_from_fixture(
    tmp_path: Path,
) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    ridge = _by_id(catalog, RIDGE_WALK_ID).feature_vector
    valley = _by_id(catalog, VALLEY_PATH_ID).feature_vector

    assert ridge.length_m == pytest.approx(10_000.0, abs=0.01)
    assert valley.length_m == pytest.approx(5_000.0, abs=0.01)
    assert ridge.difficulty_ordinal == 2
    assert valley.difficulty_ordinal is None


def test_feature_vector_includes_amenity_counts_and_nearest_distances(
    tmp_path: Path,
) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    ridge = _by_id(catalog, RIDGE_WALK_ID).feature_vector.amenity_facts
    valley = _by_id(catalog, VALLEY_PATH_ID).feature_vector.amenity_facts

    assert ridge.hut.count == 1
    assert ridge.hut.nearest_m == pytest.approx(250.0, abs=0.01)
    assert ridge.parking.count == 1
    assert ridge.parking.nearest_m == pytest.approx(400.0, abs=0.01)
    assert ridge.restaurant_cafe.count == 1
    assert ridge.restaurant_cafe.nearest_m == pytest.approx(100.0, abs=0.01)
    assert ridge.drinking_water.count == 1
    assert ridge.drinking_water.nearest_m == pytest.approx(550.0, abs=0.01)

    for fact in (
        valley.hut,
        valley.parking,
        valley.restaurant_cafe,
        valley.drinking_water,
    ):
        assert fact.count == 0
        assert fact.nearest_m is None


def test_missing_elevation_is_distinct_from_zero(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    valley = _by_id(catalog, VALLEY_PATH_ID).feature_vector

    assert valley.elevation_min_m is None
    assert valley.elevation_max_m is None
    assert valley.elevation_gain_m is None


def test_elevation_min_max_gain_when_ele_on_geometry(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    ridge = _by_id(catalog, RIDGE_WALK_ID).feature_vector

    assert ridge.elevation_min_m == 200.0
    assert ridge.elevation_max_m == 500.0
    assert ridge.elevation_gain_m == 450.0


def test_feature_vector_has_no_drivetime_origin_or_isochrone(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    vector = _by_id(catalog, RIDGE_WALK_ID).feature_vector
    names = {field.name.casefold() for field in fields(type(vector))}

    assert isinstance(vector, FeatureVector)
    for forbidden in ("drivetime", "drive_time", "origin", "isochrone"):
        assert not any(forbidden in name for name in names)


def test_ingest_reads_ele_with_units_and_skips_unparseable_values(
    tmp_path: Path,
) -> None:
    catalog = build_catalog(
        Path(__file__).parent / "fixtures" / "messy_ele.osm",
        tmp_path / "catalog.sqlite",
    )
    vector = catalog.dayhikes[0].feature_vector

    assert catalog.dayhikes[0].name == "Messy Ele Path"
    assert vector.elevation_min_m == pytest.approx(200.0)
    assert vector.elevation_max_m == pytest.approx(500.0)
    assert vector.elevation_gain_m == pytest.approx(300.0)
