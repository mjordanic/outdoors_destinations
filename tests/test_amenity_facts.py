from pathlib import Path

import pytest

from dayhike import Catalog, build_catalog

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
RIDGE_WALK_ID = 1001
VALLEY_PATH_ID = 1002
KEPT_IDS = {RIDGE_WALK_ID, VALLEY_PATH_ID}


def _by_id(catalog: Catalog, hike_id: int):
    return next(hike for hike in catalog.dayhikes if hike.id == hike_id)


def test_ridge_walk_trailhead_records_nearby_hut_parking_cafe_and_water(
    tmp_path: Path,
) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    facts = _by_id(catalog, RIDGE_WALK_ID).trailhead.amenity_facts

    assert facts.hut.count == 1
    assert facts.hut.nearest_m == pytest.approx(250.0, abs=0.01)
    assert facts.parking.count == 1
    assert facts.parking.nearest_m == pytest.approx(400.0, abs=0.01)
    assert facts.restaurant_cafe.count == 1
    assert facts.restaurant_cafe.nearest_m == pytest.approx(100.0, abs=0.01)
    assert facts.drinking_water.count == 1
    assert facts.drinking_water.nearest_m == pytest.approx(550.0, abs=0.01)


def test_zero_amenity_counts_are_stored_and_that_dayhike_stays_in_catalog(
    tmp_path: Path,
) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    valley = _by_id(catalog, VALLEY_PATH_ID)
    facts = valley.trailhead.amenity_facts

    assert {hike.id for hike in catalog.dayhikes} == KEPT_IDS
    for fact in (facts.hut, facts.parking, facts.restaurant_cafe, facts.drinking_water):
        assert fact.count == 0
        assert fact.nearest_m is None


def test_restaurant_cafe_does_not_create_or_delete_dayhikes(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")

    assert {hike.id for hike in catalog.dayhikes} == KEPT_IDS
    assert _by_id(catalog, RIDGE_WALK_ID).trailhead.amenity_facts.restaurant_cafe.count == 1
    assert _by_id(catalog, VALLEY_PATH_ID).trailhead.amenity_facts.restaurant_cafe.count == 0
