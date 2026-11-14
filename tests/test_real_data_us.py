"""Checks on a one-state US build: known trails, a drive time from a US city, the pages' wording.

Slow, opt-in (DAYHIKE_REAL_DATA=1). DAYHIKE_US_DATA_DIR holds the build of the
``us-rhode-island`` region (docs/technical.md "A US build"): ``catalog.sqlite``
after ingest, the three sample commands, sample-trailgraph and build-similarity,
and ``web-serving/``, its export with ``--region us-rhode-island``. The drive-time
check needs that state's highway tiles served at DAYHIKE_VALHALLA_URL (default
http://127.0.0.1:8002). Skipped without DAYHIKE_US_DATA_DIR.

Recorded on the Geofabrik extract of 2026-11-14 (23 DayHikes, FABDEM, WorldCover
2021, CHELSA V2.1). Ids, not names: names repeat across a continent.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import rebuild_checks as rc

from dayhike import Catalog, rank_dayhikes

US_DATA_DIR = Path(os.environ["DAYHIKE_US_DATA_DIR"]) if os.environ.get("DAYHIKE_US_DATA_DIR") else None
pytestmark = [
    *rc.real_data,
    pytest.mark.skipif(US_DATA_DIR is None, reason="no US build: set DAYHIKE_US_DATA_DIR"),
]

NORTH_SOUTH_TRAIL, CLIFF_WALK, BENSON_MOUNTAIN_TRAIL = 11102215, 14738780, 20632264
# id -> (name, length km low, high)
KNOWN = {
    NORTH_SOUTH_TRAIL: ("North South Trail", 85, 100),  # 77 miles (124 km) published, the Catalog's line 91.7 km
    CLIFF_WALK: ("Cliff Walk", 5, 6.5),  # Newport, 3.5 miles
    BENSON_MOUNTAIN_TRAIL: ("Benson Mountain Trail", 4.5, 6),
}
JERIMOTH_HILL_M = 247  # Rhode Island's highest point
PROVIDENCE = (41.824, -71.4128)


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog.open(US_DATA_DIR / "catalog.sqlite")


def test_known_trails_are_in_the_catalog_with_their_length(catalog: Catalog) -> None:
    hikes = {hike.id: hike for hike in catalog.dayhikes}

    for dayhike_id, (name, low, high) in KNOWN.items():
        assert hikes[dayhike_id].name == name
        assert low <= hikes[dayhike_id].length_m / 1000 <= high, name
    # The long trail is also cut into day stages.
    stages = [hike for hike in hikes.values() if hike.name.startswith("North South Trail, stage ")]
    assert len(stages) >= 5 and all(8_000 <= hike.length_m <= 25_000 for hike in stages)


def test_every_hike_has_heights_land_cover_and_climate_within_the_state() -> None:
    connection = rc.connect_ro(US_DATA_DIR / "catalog.sqlite")
    try:
        rows = connection.execute(
            "SELECT dayhike_id, elevation_min_m, elevation_max_m, tree_fraction, temperature_c FROM feature_vectors"
        ).fetchall()
    finally:
        connection.close()
    facts = {row[0]: row[1:] for row in rows}

    assert len(facts) >= 20
    assert [i for i, row in facts.items() if None in row] == []
    # Coast to the highest hill, and a New England summer (June-August mean).
    assert all(-5 <= low <= high <= JERIMOTH_HILL_M + 10 for low, high, _, _ in facts.values())
    assert all(17 <= temperature <= 24 for *_, temperature in facts.values())
    assert facts[CLIFF_WALK][1] <= 40  # along the cliffs above the sea
    assert facts[BENSON_MOUNTAIN_TRAIL][2] >= 0.9  # in the forest


def test_a_known_trail_ranks_the_others(catalog: Catalog) -> None:
    ranking = rank_dayhikes(catalog, [KNOWN[CLIFF_WALK][0]])

    assert len(ranking.items) == len(catalog.dayhikes) - 1
    assert all(0 <= item.score <= 1 for item in ranking.items)
    assert CLIFF_WALK not in [item.dayhike.id for item in ranking.items]


def test_drive_time_from_providence_reaches_the_state(catalog: Catalog, tmp_path: Path) -> None:
    hours = rc.route(rc.VALHALLA_URL, PROVIDENCE, (41.4901, -71.3128))[0]  # to Newport
    assert 0.4 <= hours <= 1.0

    within_two = rc.nearby_hours(catalog, rc.VALHALLA_URL, PROVIDENCE, 2, tmp_path / "snaps.npz")
    within_one = rc.nearby_hours(catalog, rc.VALHALLA_URL, PROVIDENCE, 1, tmp_path / "snaps.npz")

    assert set(within_two) == {hike.id for hike in catalog.dayhikes}  # the state is about an hour across
    assert CLIFF_WALK in within_one and all(hours <= 1 for hours in within_one.values())
    assert set(within_one) <= set(within_two)


def test_the_pages_name_the_state_and_not_europe() -> None:
    from fastapi.testclient import TestClient

    from dayhike.serving import ServingData
    from dayhike.web import create_app

    client = TestClient(create_app(ServingData(US_DATA_DIR / "web-serving")))
    index, about, terms = (client.get(path).text for path in ("/", "/about.html", "/terms.html"))

    assert "hikes across Rhode Island.</p>" in index and "hikes across Rhode Island.</p>" in about
    assert 'alt="Map of Rhode Island with the covered countries shaded"' in index
    assert "It covers Rhode Island where OpenStreetMap maps hiking routes" in about
    assert "Europe" not in index + about and "European Union" not in terms
