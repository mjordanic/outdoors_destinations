from pathlib import Path

import pytest

from dayhike import build_catalog

FIXTURES = Path(__file__).parent / "fixtures"


def test_reversed_member_ways_assemble_into_one_linestring(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURES / "reversed_ways.osm", tmp_path / "catalog.sqlite")
    hike = catalog.dayhikes[0]

    assert hike.name == "Zig Zag"
    assert hike.length_m == pytest.approx(5_000.0, abs=1.0)
    assert (hike.trailhead.latitude, hike.trailhead.longitude) == (45.0, 15.0)


def test_trailhead_is_the_endpoint_nearer_parking(tmp_path: Path) -> None:
    catalog = build_catalog(
        FIXTURES / "trailhead_at_parking.osm", tmp_path / "catalog.sqlite"
    )
    hike = catalog.dayhikes[0]

    assert hike.name == "Parking End Path"
    assert hike.trailhead.latitude == pytest.approx(45.0449660803)
    assert hike.trailhead.longitude == pytest.approx(15.0)
    assert hike.trailhead.amenity_facts.parking.count == 1


def test_trailhead_is_the_endpoint_nearer_driveable_highway(tmp_path: Path) -> None:
    catalog = build_catalog(
        FIXTURES / "trailhead_at_highway.osm", tmp_path / "catalog.sqlite"
    )
    hike = catalog.dayhikes[0]

    assert hike.name == "Highway End Path"
    assert hike.trailhead.latitude == pytest.approx(45.0449660803)
    assert hike.trailhead.longitude == pytest.approx(15.0)


def test_way_and_relation_amenities_count_once_each(tmp_path: Path) -> None:
    catalog = build_catalog(
        FIXTURES / "way_and_relation_amenities.osm", tmp_path / "catalog.sqlite"
    )
    facts = catalog.dayhikes[0].trailhead.amenity_facts

    assert facts.parking.count == 1
    assert facts.parking.nearest_m == pytest.approx(100.0, abs=50.0)
    assert facts.hut.count == 1
    assert facts.restaurant_cafe.count == 1
    assert facts.drinking_water.count == 0
    assert facts.drinking_water.nearest_m is None
