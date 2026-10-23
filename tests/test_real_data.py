"""Checks on the real local data under data/, not on the OSM fixture.

Run after an ingest, the three sample commands and notebook 05:

    DAYHIKE_REAL_DATA=1 uv run pytest tests/test_real_data.py

Skipped without DAYHIKE_REAL_DATA=1. The baseline checks compare with
data/baseline/ (catalog.sqlite and catalog.similarity_facts.pkl copied before
the main-walk re-ingest, ADR-0012; not the 19 GB GeometryStore) and are
skipped without it. The old WaterSource "unchanged against the baseline"
checks were removed: the main walk changes lengths, geometry and facts.

The main-walk checks (steps, ways walked once, length band, Leftover reasons,
loop starts) need the new GeometryStore columns ``leftover``, ``walked_m`` and
``walk_order``, so they fail on a store from before the main walk. The
baseline comparison prints the DayHike ids that left and joined the Catalog,
with the main-walk reason for each that left, even without ``-s``; ticket 08
copies that into the report. Reasons for ids that left come from re-assembling
their relations from the Europe extract with ``osmium getid``, so that check
also needs the extract.

DAYHIKE_REAL_CATALOG points the checks at another Catalog; the notebook check
always reads data/catalog.sqlite, as the notebook does. The main-walk checks
pass on a Catalog built from tests/fixtures/catalog.osm; the water, extract
and notebook checks are about the real data and are not expected to pass there.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import osmium
import pandas as pd
import pytest
from sklearn.neighbors import BallTree

from dayhike import geometry_store_path_for
from dayhike.catalog import MAX_LENGTH_M, _drop_reason, assemble_main_walk
from dayhike.osm import amenity_kind
from dayhike.similarity import similarity_facts_path_for

REPO = Path(__file__).resolve().parents[1]
CATALOG = Path(os.environ.get("DAYHIKE_REAL_CATALOG", REPO / "data" / "catalog.sqlite"))
GEOMETRY = geometry_store_path_for(CATALOG)
FACTS = similarity_facts_path_for(CATALOG)
BASELINE = REPO / "data" / "baseline" / "catalog.sqlite"
EXTRACT = REPO / "data" / "osm" / "europe-latest.catalog.osm.pbf"
REGION_LAT = (42.3, 46.9)  # notebook 05 region
REGION_LON = (13.3, 19.5)
OLD_KINDS = ("hut", "parking", "restaurant_cafe", "drinking_water", "viewpoint", "peak", "shelter")
NEW_WATER_KINDS = ("spring", "water_tap", "water_point")
WATER_COLUMNS = ("water_count", "dry_stretch_m")

pytestmark = pytest.mark.skipif(
    os.environ.get("DAYHIKE_REAL_DATA") != "1", reason="real data: set DAYHIKE_REAL_DATA=1"
)
needs_baseline = pytest.mark.skipif(not BASELINE.is_file(), reason="no data/baseline/")


def _read(path: Path, sql: str, params: tuple = ()) -> pd.DataFrame:
    connection = sqlite3.connect(path)
    try:
        return pd.read_sql_query(sql, connection, params=params)
    finally:
        connection.close()


def _count(path: Path, sql: str) -> int:
    return int(_read(path, sql).iloc[0, 0])


# The data as it is now.


def test_every_catalog_dayhike_has_a_geometry_of_at_least_two_vertices() -> None:
    catalog_ids = _read(CATALOG, "SELECT id FROM dayhikes ORDER BY id")["id"].tolist()
    geometry_ids = _read(GEOMETRY, "SELECT id FROM dayhikes ORDER BY id")["id"].tolist()
    vertices = _read(
        GEOMETRY,
        "SELECT dayhike_id, COUNT(*) AS n FROM vertices GROUP BY dayhike_id ORDER BY dayhike_id",
    )

    assert geometry_ids == catalog_ids
    assert vertices["dayhike_id"].tolist() == catalog_ids
    assert vertices["n"].min() >= 2


def test_geometry_store_holds_every_amenity_kind_with_the_water_sources() -> None:
    kinds = set(_read(GEOMETRY, "SELECT DISTINCT kind FROM amenities")["kind"])

    assert kinds == set(OLD_KINDS + NEW_WATER_KINDS)


def test_no_stored_water_source_is_tagged_not_drinkable() -> None:
    assert (
        _count(
            GEOMETRY,
            "SELECT COUNT(*) FROM amenities WHERE kind IN ('spring', 'water_tap', 'water_point') "
            "AND json_extract(tags, '$.drinking_water') = 'no'",
        )
        == 0
    )


def test_springs_and_taps_tagged_as_drinking_water_stay_drinking_water() -> None:
    as_new_kind = _count(
        GEOMETRY,
        "SELECT COUNT(*) FROM amenities WHERE kind IN ('spring', 'water_tap', 'water_point') "
        "AND json_extract(tags, '$.amenity') = 'drinking_water'",
    )
    as_drinking_water = _count(
        GEOMETRY,
        "SELECT COUNT(*) FROM amenities WHERE kind = 'drinking_water' "
        "AND (json_extract(tags, '$.natural') = 'spring' "
        "OR json_extract(tags, '$.man_made') = 'water_tap')",
    )

    assert as_new_kind == 0
    assert as_drinking_water > 0


@pytest.mark.skipif(not EXTRACT.is_file(), reason="no filtered Europe extract")
def test_every_water_source_node_in_the_extract_is_in_the_geometry_store(
    tmp_path: Path,
) -> None:
    # osmium picks the candidate nodes on its own; only the kind rule is ours.
    candidates = tmp_path / "water.osm.pbf"
    subprocess.run(
        [
            "osmium",
            "tags-filter",
            str(EXTRACT),
            "n/natural=spring",
            "n/man_made=water_tap",
            "n/amenity=water_point",
            "--omit-referenced",
            "-o",
            str(candidates),
            "--no-progress",
        ],
        check=True,
    )
    expected = {}
    for node in osmium.FileProcessor(str(candidates), osmium.osm.NODE):
        kind = amenity_kind(dict(node.tags))
        if kind in NEW_WATER_KINDS and node.location.valid():
            expected[f"n{node.id}"] = kind
    stored = _read(
        GEOMETRY,
        "SELECT osm_key, kind FROM amenities "
        "WHERE kind IN ('spring', 'water_tap', 'water_point') AND osm_key LIKE 'n%'",
    )

    assert expected
    assert dict(zip(stored["osm_key"], stored["kind"])) == expected


def test_facts_table_has_water_facts_for_each_region_dayhike() -> None:
    facts = pd.read_pickle(FACTS)
    region = _read(
        CATALOG,
        "SELECT dayhike_id FROM trailheads WHERE latitude BETWEEN ? AND ? "
        "AND longitude BETWEEN ? AND ? ORDER BY dayhike_id",
        (*REGION_LAT, *REGION_LON),
    )

    assert sorted(facts.index) == region["dayhike_id"].tolist()
    assert facts[list(WATER_COLUMNS)].notna().all().all()
    # DryStretch is part of the walk; with no WaterSource it is the whole walk.
    assert (facts["dry_stretch_m"] <= facts["length_m"] * (1 + 1e-6)).all()
    dry = facts[facts["water_count"] == 0]
    assert np.allclose(dry["dry_stretch_m"], dry["length_m"], rtol=1e-3)


def test_notebook_05a_passes_every_verification_on_the_real_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_demonstration_notebooks import NOTEBOOKS, _notebook_text

    # The notebook falls back to the fixture under pytest; let it read data/.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("MPLBACKEND", "Agg")
    output, _, _ = _notebook_text(NOTEBOOKS / "05a_similarity.ipynb")

    assert "facts read from" in output
    verify = [line.split("\t") for line in output.splitlines() if line.startswith("verify\t")]
    assert verify
    assert [fields for fields in verify if fields[-1] != "PASS"] == []
    assert f"verify_summary\tpass={len(verify)}\tfail=0" in output




# The main walk (ADR-0012): rules a full ingest must have followed.

STEP_MAX_M = 50.0
BAND_M = (1_000.0, 20_000.0)
LOOP_CLOSES_M = 50.0  # first and last vertex this close: a loop
LOOP_NEAR_PARKING_M = 100.0
LOOP_START_M = 300.0
LOOP_START_FAIL_MAX = 0.02
EARTH_RADIUS_M = 6_371_000.0
CHUNK = 2_000


def _ids(ids) -> str:
    ordered = sorted(ids)
    more = f" and {len(ordered) - 5} more" if len(ordered) > 5 else ""
    return f"{len(ordered)} DayHikes, e.g. {ordered[:5]}{more}"


def _haversine_m(lat1, lon1, lat2, lon2):
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    h = np.sin((phi2 - phi1) / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(
        np.radians(lon2 - lon1) / 2
    ) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(h))


@pytest.fixture(scope="module")
def walk_measures() -> dict:
    """One pass over the GeometryStore: long steps and loop starts."""
    parking = _read(GEOMETRY, "SELECT latitude, longitude FROM amenities WHERE kind = 'parking'")
    tree = (
        BallTree(np.radians(parking[["latitude", "longitude"]].to_numpy()), metric="haversine")
        if len(parking)
        else None
    )
    ids = _read(GEOMETRY, "SELECT id FROM dayhikes ORDER BY id")["id"].tolist()
    long_steps: dict[int, float] = {}
    loops_near_parking: list[int] = []
    loops_failing: list[int] = []
    loops = 0
    for start in range(0, len(ids), CHUNK):
        low, high = ids[start], ids[min(start + CHUNK, len(ids)) - 1]
        vertices = _read(
            GEOMETRY,
            "SELECT dayhike_id, node_id, latitude, longitude FROM vertices "
            "WHERE dayhike_id BETWEEN ? AND ? ORDER BY dayhike_id, seq",
            (low, high),
        )
        ways = _read(
            GEOMETRY,
            "SELECT dayhike_id, node_ids FROM ways "
            "WHERE dayhike_id BETWEEN ? AND ? AND walk_order IS NOT NULL",
            (low, high),
        )
        edges: dict[int, set[tuple[int, int]]] = {}
        for dayhike_id, node_ids in zip(ways["dayhike_id"], ways["node_ids"]):
            nodes = json.loads(node_ids)
            pairs = edges.setdefault(dayhike_id, set())
            for a, b in zip(nodes, nodes[1:]):
                pairs.add((a, b))
                pairs.add((b, a))
        distance_to_parking = np.full(len(vertices), np.inf)
        if tree is not None:
            radians = np.radians(vertices[["latitude", "longitude"]].to_numpy())
            distance_to_parking = tree.query(radians)[0][:, 0] * EARTH_RADIUS_M
        for dayhike_id, rows in vertices.groupby("dayhike_id").indices.items():
            lat = vertices["latitude"].to_numpy()[rows]
            lon = vertices["longitude"].to_numpy()[rows]
            node = vertices["node_id"].to_numpy()[rows]
            steps = _haversine_m(lat[:-1], lon[:-1], lat[1:], lon[1:])
            pairs = edges.get(dayhike_id, set())
            for index in np.flatnonzero(steps > STEP_MAX_M):
                if (int(node[index]), int(node[index + 1])) not in pairs:
                    long_steps[dayhike_id] = max(long_steps.get(dayhike_id, 0.0), steps[index])
            if _haversine_m(lat[0], lon[0], lat[-1], lon[-1]) <= LOOP_CLOSES_M:
                loops += 1
                near = distance_to_parking[rows]
                if near.min() <= LOOP_NEAR_PARKING_M:
                    loops_near_parking.append(dayhike_id)
                    if near[0] > LOOP_START_M:
                        loops_failing.append(dayhike_id)
    return {
        "long_steps": long_steps,
        "loops": loops,
        "loops_near_parking": loops_near_parking,
        "loops_failing": loops_failing,
    }


def test_no_step_between_consecutive_ways_is_over_50_m(walk_measures: dict) -> None:
    # A step is the last vertex of one walked way to the first of the next. The
    # GeometryStore keeps one vertex sequence, so a step is two consecutive
    # vertices that no walked way joins; vertices inside a way are not steps.
    steps = walk_measures["long_steps"]

    assert not steps, f"steps over {STEP_MAX_M:.0f} m in {_ids(steps)}"


def test_no_way_is_walked_twice_in_one_dayhike() -> None:
    twice = _read(
        GEOMETRY,
        "SELECT dayhike_id FROM ways WHERE walk_order IS NOT NULL "
        "GROUP BY dayhike_id HAVING COUNT(*) > COUNT(DISTINCT way_id) "
        "OR COUNT(*) > COUNT(DISTINCT walk_order)",
    )["dayhike_id"]

    assert twice.empty, f"a way walked twice in {_ids(twice)}"


def test_every_catalog_length_is_between_1_and_20_km() -> None:
    outside = _read(
        CATALOG,
        "SELECT id FROM dayhikes WHERE length_m < ? OR length_m > ?",
        BAND_M,
    )["id"]

    assert outside.empty, f"length_m outside 1-20 km in {_ids(outside)}"


def test_every_leftover_has_a_reason_and_every_walked_way_a_walk_order() -> None:
    no_reason = _read(
        GEOMETRY,
        "SELECT DISTINCT dayhike_id FROM ways WHERE walk_order IS NULL "
        "AND (leftover IS NULL OR leftover = '')",
    )["dayhike_id"]
    no_order = _read(
        GEOMETRY,
        "SELECT DISTINCT dayhike_id FROM ways WHERE walk_order IS NOT NULL "
        "AND leftover IS NOT NULL",
    )["dayhike_id"]
    in_chain_without_order = _read(
        GEOMETRY,
        "SELECT DISTINCT dayhike_id FROM ways WHERE in_chain = 1 AND walk_order IS NULL",
    )["dayhike_id"]

    assert no_reason.empty, f"a Leftover without a reason in {_ids(no_reason)}"
    assert no_order.empty, f"a way both walked and a Leftover in {_ids(no_order)}"
    assert in_chain_without_order.empty, (
        f"a way in_chain without a walk order in {_ids(in_chain_without_order)}"
    )


def test_a_loop_by_a_parking_point_starts_within_300_m_of_one(
    walk_measures: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    # The PRD's 22.5% "before" measure. Parking only: the GeometryStore holds no
    # drivable-road access nodes, so road access points nearer than the parking
    # explain a few failures (hence the 2% allowance).
    near, failing = walk_measures["loops_near_parking"], walk_measures["loops_failing"]
    share = len(failing) / len(near) if near else 0.0
    with capsys.disabled():
        print(
            f"\nloops {walk_measures['loops']}; passing within {LOOP_NEAR_PARKING_M:.0f} m of "
            f"parking {len(near)}; starting over {LOOP_START_M:.0f} m from it "
            f"{len(failing)} ({share:.2%}; was 22.5% before the main walk)"
        )

    assert share <= LOOP_START_FAIL_MAX, (
        f"{share:.2%} of loops by parking start over {LOOP_START_M:.0f} m from it, "
        f"{_ids(failing)}"
    )


# Against data/baseline/: the same OSM snapshot, so only the main walk may move
# DayHikes in or out.

LEFT_REASONS = ("over 20 km", "under 1 km", "superroute")


def _ids_of(path: Path) -> set[int]:
    return set(_read(path, "SELECT id FROM dayhikes")["id"])


def _main_walk_reasons(ids: set[int]) -> dict[int, str]:
    """Why each relation in ``ids`` is not a DayHike: its main-walk drop reason."""
    from dayhike.ingest import _load_hiking_snapshot, run_ingest

    reasons: dict[int, str] = {}
    with tempfile.TemporaryDirectory() as folder:
        folder_path = Path(folder)
        wanted = folder_path / "wanted.osm.pbf"
        subprocess.run(
            ["osmium", "getid", "-r", "-t", str(EXTRACT), *(f"r{i}" for i in sorted(ids)),
             "-o", str(wanted), "--no-progress", "--overwrite"],
            check=True,
        )
        _, _, work_path = run_ingest([wanted], folder_path / "catalog.sqlite")
        connection = sqlite3.connect(work_path)
        try:
            snapshot = _load_hiking_snapshot(connection)
        finally:
            connection.close()
        for relation in snapshot.relations:
            if relation.id not in ids:
                continue
            walk = assemble_main_walk(relation, snapshot.ways, snapshot.nodes, None)
            drop = _drop_reason(relation, walk.length_m)
            if drop == "network":
                reasons[relation.id] = "superroute"
            elif drop == "length":
                reasons[relation.id] = "over 20 km" if walk.length_m > MAX_LENGTH_M else "under 1 km"
            else:
                reasons[relation.id] = "kept by the main-walk rules"
    return {i: reasons.get(i, "not in the extract") for i in ids}


@pytest.fixture(scope="module")
def baseline_comparison() -> dict:
    left = _ids_of(BASELINE) - _ids_of(CATALOG)
    joined = _ids_of(CATALOG) - _ids_of(BASELINE)
    reasons = _main_walk_reasons(left) if left else {}
    return {"left": left, "joined": joined, "reasons": reasons}


@needs_baseline
@pytest.mark.skipif(not EXTRACT.is_file(), reason="no filtered Europe extract")
def test_dayhikes_that_left_and_joined_the_catalog_are_listed(
    baseline_comparison: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    left, joined, reasons = (
        baseline_comparison["left"],
        baseline_comparison["joined"],
        baseline_comparison["reasons"],
    )
    with capsys.disabled():
        print(f"\nleft the Catalog: {len(left)}; joined: {len(joined)}")
        for reason in sorted(set(reasons.values())):
            grouped = sorted(i for i in left if reasons[i] == reason)
            print(f"left, {reason}: {len(grouped)}: {grouped}")
        print(f"joined: {sorted(joined)}")


@needs_baseline
@pytest.mark.skipif(not EXTRACT.is_file(), reason="no filtered Europe extract")
def test_no_dayhike_left_the_catalog_for_any_reason_but_the_main_walk(
    baseline_comparison: dict,
) -> None:
    # In particular none left for lack of an access point.
    other = {
        i: reason
        for i, reason in baseline_comparison["reasons"].items()
        if reason not in LEFT_REASONS
    }

    assert not other, f"left for another reason: {_ids(other)}: {sorted(set(other.values()))}"
