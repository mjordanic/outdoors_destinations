"""Rebuilt highway tiles drive like the reference ones.

Slow, opt-in (DAYHIKE_REAL_DATA=1); needs the highway tiles under
DAYHIKE_DATA_DIR/valhalla/highways-eu served at DAYHIKE_VALHALLA_URL (default
http://127.0.0.1:8002; README "Highway tiles"), and compares with
DAYHIKE_REFERENCE_DIR/golden/ captured from the reference server.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import rebuild_checks as rc

from dayhike import Catalog

pytestmark = [*rc.real_data, rc.needs_reference]
TILES = rc.DATA_DIR / "valhalla" / "highways-eu"
# Relative tolerance on time (and length) against the reference route.
ROUTE_TOLERANCE = {("Zagreb", "Vienna"): 0.05, ("Lisbon", "Warsaw"): 0.02, ("Athens", "Oslo"): 0.02}


def _golden(name: str) -> list[dict[str, str]]:
    return rc.read_tsv(rc.REFERENCE_DIR / "golden" / name)


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog.open(rc.catalog_path())


def test_rebuilt_tiles_match_reference_tile_counts() -> None:
    golden = {row["key"]: int(row["count"]) for row in _golden("tiles.tsv")}

    assert rc.tile_counts_in_tar(TILES / "valhalla_tiles.tar") == golden
    assert (TILES / "file_hashes.txt").read_text().strip()
    assert rc.valhalla_version(rc.VALHALLA_URL) == "3.5.1"


def test_city_pairs_drive_within_tolerance() -> None:
    golden = {(row["from"], row["to"]): (float(row["hours"]), float(row["km"])) for row in _golden("routes.tsv")}
    assert set(golden) == set(ROUTE_TOLERANCE)

    for (start, end), tolerance in ROUTE_TOLERANCE.items():
        hours, km = rc.route(rc.VALHALLA_URL, rc.CITIES[start], rc.CITIES[end])
        old_hours, old_km = golden[(start, end)]
        assert hours == pytest.approx(old_hours, rel=tolerance), (start, end)
        assert hours <= old_hours + 1 / 60, (start, end)
        if (start, end) != ("Zagreb", "Vienna"):
            assert km == pytest.approx(old_km, rel=tolerance), (start, end)
        else:
            assert 3.3 <= hours <= 4.3
    assert [city for city, place in rc.CITIES.items() if not rc.locates(rc.VALHALLA_URL, place)] == []


@pytest.mark.parametrize("hours", rc.NEARBY_HOURS)
@pytest.mark.parametrize("origin", sorted(rc.NEARBY_ORIGINS))
def test_nearby_matches_reference(catalog: Catalog, tmp_path: Path, origin: str, hours: int) -> None:
    golden = {int(row["dayhike_id"]): float(row["hours"]) for row in _golden(f"nearby_{origin}_{hours}h.tsv")}

    found = rc.nearby_hours(catalog, rc.VALHALLA_URL, rc.NEARBY_ORIGINS[origin], hours, tmp_path / "snaps.npz")

    both = golden.keys() & found.keys()
    assert len(both) / len(golden.keys() | found.keys()) >= 0.999
    assert [i for i in both if abs(found[i] - golden[i]) > 1 / 60][:20] == []


def test_only_the_known_trailheads_have_no_highway(catalog: Catalog, tmp_path: Path) -> None:
    golden = [int(row["dayhike_id"]) for row in _golden("unreachable.tsv")]

    assert rc.unreachable_ids(catalog, rc.VALHALLA_URL, tmp_path / "snaps.npz") == golden
    assert len(golden) == 6
