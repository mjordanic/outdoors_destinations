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
# Trailheads per country in the Catalog of the 2026-10-31 snapshot (the shared
# rebuild: relations, NetworkHikes and DayStages), counted on 2026-11-03 with the
# index of 2026-11-01. guernsey-jersey has none.
RECORDED_HIKES = {
    "albania": 91, "andorra": 35, "austria": 12_635, "azores": 123, "belarus": 45,
    "belgium": 11_349, "bosnia-herzegovina": 177, "bulgaria": 627, "croatia": 1_242,
    "cyprus": 221, "czech-republic": 6_770, "denmark": 1_960, "estonia": 537,
    "faroe-islands": 1, "finland": 1_062, "france": 35_126, "georgia": 283,
    "germany": 51_077, "greece": 1_552, "hungary": 3_288, "iceland": 167,
    "ireland-and-northern-ireland": 982, "isle-of-man": 14, "italy": 32_202, "kosovo": 36,
    "latvia": 457, "liechtenstein": 167, "lithuania": 328, "luxembourg": 550, "macedonia": 60,
    "malta": 42, "moldova": 2, "monaco": 2, "montenegro": 261, "netherlands": 32_886,
    "norway": 3_306, "poland": 5_907, "portugal": 1_746, "romania": 1_827,
    "serbia": 828, "slovakia": 2_755, "slovenia": 2_119, "spain": 9_380,
    "sweden": 4_183, "switzerland": 17_292, "turkey": 220, "ukraine": 632,
    "united-kingdom": 6_811,
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
