"""Every Europe country keeps its DayHikes, and LandCover, climate and elevation cover them.

Slow, opt-in (DAYHIKE_REAL_DATA=1); reads DAYHIKE_DATA_DIR (default data/) and
the Geofabrik country polygons in data/osm/geofabrik-index-v1.json
(``curl -L -o data/osm/geofabrik-index-v1.json https://download.geofabrik.de/index-v1.json``).
A Trailhead counts in every ``europe`` child polygon that holds it; the
composites alps, dach, britain-and-ireland and great-britain are left out.
"""

from __future__ import annotations

import pytest
import rebuild_checks as rc

pytestmark = rc.real_data
INDEX = rc.DATA_DIR / "osm" / "geofabrik-index-v1.json"
# Trailheads per country in the Catalog of the 2026-09-18 snapshot, as counted
# on 2026-10-25 with the index of that day. guernsey-jersey and isle-of-man have none.
RECORDED_HIKES = {
    "albania": 63, "andorra": 9, "austria": 9_669, "azores": 108, "belarus": 25,
    "belgium": 3_153, "bosnia-herzegovina": 90, "bulgaria": 399, "croatia": 816,
    "cyprus": 168, "czech-republic": 3_388, "denmark": 1_155, "estonia": 215,
    "faroe-islands": 1, "finland": 599, "france": 24_414, "georgia": 167,
    "germany": 35_644, "greece": 1_219, "hungary": 2_054, "iceland": 126,
    "ireland-and-northern-ireland": 353, "italy": 28_389, "kosovo": 21, "latvia": 190,
    "liechtenstein": 160, "lithuania": 162, "luxembourg": 365, "macedonia": 21,
    "malta": 35, "moldova": 1, "monaco": 2, "montenegro": 152, "netherlands": 12_525,
    "norway": 2_484, "poland": 2_254, "portugal": 1_127, "romania": 1_272,
    "serbia": 431, "slovakia": 1_952, "slovenia": 1_506, "spain": 5_326,
    "sweden": 2_419, "switzerland": 16_523, "turkey": 92, "ukraine": 424,
    "united-kingdom": 1_676,
}  # fmt: skip
COVERED_SHARE = 0.99  # of each value, in every country with at least COVERED_MIN_HIKES
COVERED_MIN_HIKES = 50
# Three FABDEM tiles under Georgian walks are 404 upstream (rc.KNOWN_DEM_GAPS).
KNOWN_GAPS = {("georgia", "elevation"): 0.79}


@pytest.fixture(scope="module")
def coverage() -> dict[str, rc.CountryCoverage]:
    if not INDEX.is_file():
        pytest.skip(f"no {INDEX}")
    catalog = rc.connect_ro(rc.catalog_path())
    try:
        rows = rc.trailhead_coverage(catalog)
    finally:
        catalog.close()
    by_country, unassigned = rc.coverage_by_country(rc.europe_countries(INDEX), rows)
    print("unassigned Trailheads", unassigned)
    return by_country


def _band(recorded: int) -> float:
    if not rc.same_snapshot():
        return 0.15 * recorded
    return 2 if recorded < 400 else 0.005 * recorded


def test_every_country_of_the_extract_has_dayhikes(coverage: dict[str, rc.CountryCoverage]) -> None:
    assert [name for name in RECORDED_HIKES if coverage.get(name, rc.CountryCoverage(0, 0, 0, 0)).hikes == 0] == []


def test_hike_counts_per_country_within_bands(coverage: dict[str, rc.CountryCoverage]) -> None:
    outside = {
        name: (recorded, coverage[name].hikes)
        for name, recorded in RECORDED_HIKES.items()
        if abs(coverage[name].hikes - recorded) > _band(recorded)
    }

    assert outside == {}


def test_landcover_climate_and_elevation_cover_every_country(coverage: dict[str, rc.CountryCoverage]) -> None:
    short = {}
    for name, counts in coverage.items():
        if counts.hikes < COVERED_MIN_HIKES:
            continue
        for value in ("landcover", "climate", "elevation"):
            share = getattr(counts, value) / counts.hikes
            if share < KNOWN_GAPS.get((name, value), COVERED_SHARE):
                short[f"{name} {value}"] = round(share, 4)

    assert short == {}
