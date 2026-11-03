"""The opt-in real-data checks agree with ingest, shown on tiny fixtures.

tests/test_real_data.py is skipped without DAYHIKE_REAL_DATA=1. Here its step
measure and its "why did this DayHike leave" helper run on Catalogs built from
small OSM files, so a check that disagrees with ingest fails in the normal suite.
"""

import shutil
import sqlite3
import time
from pathlib import Path

import pytest
from test_main_walk import RELATION_ID, _osm
from test_real_data import _main_walk_reasons, _measure_walks, _start_distances
from test_streaming_ingest import _sorted_pbf

from dayhike import build_catalog, geometry_store_path_for

needs_osmium = pytest.mark.skipif(shutil.which("osmium") is None, reason="osmium-tool")

# b and c are two nodes at one point: way 1 ends at b and way 2 starts at c.
SAME_POINT = {"a": (0, 0), "b": (0, 1), "c": (0, 1), "d": (0, 2)}
SAME_POINT_WAYS = {1: "ab", 2: "cd"}


def _same_point_geometry(tmp_path: Path) -> Path:
    osm = tmp_path / "same_point.osm"
    osm.write_text(_osm(SAME_POINT, SAME_POINT_WAYS, [1, 2]))
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(osm, catalog_path)
    return geometry_store_path_for(catalog_path)


def _vertex_node_ids(geometry: Path) -> list[int]:
    connection = sqlite3.connect(geometry)
    try:
        return [
            node_id
            for (node_id,) in connection.execute(
                "SELECT node_id FROM vertices WHERE dayhike_id = ? ORDER BY seq", (RELATION_ID,)
            )
        ]
    finally:
        connection.close()


def test_a_step_between_two_nodes_at_one_point_is_not_a_long_step(tmp_path: Path) -> None:
    geometry = _same_point_geometry(tmp_path)

    # The vertex builder dropped c, so b (100 + 1) is followed by d (100 + 3).
    assert _vertex_node_ids(geometry) == [100, 101, 103]
    assert _measure_walks(geometry)["long_steps"] == {}


def test_a_step_no_walked_way_joins_is_still_a_long_step(tmp_path: Path) -> None:
    geometry = _same_point_geometry(tmp_path)
    connection = sqlite3.connect(geometry)
    try:
        # Make the last vertex a node no way reaches: b to it is a 1 km jump.
        connection.execute(
            "UPDATE vertices SET node_id = 999 WHERE dayhike_id = ? AND seq = 2", (RELATION_ID,)
        )
        connection.commit()
    finally:
        connection.close()

    steps = _measure_walks(geometry)["long_steps"]

    assert steps == {RELATION_ID: pytest.approx(1_000.0, abs=1.0)}


# A 0.8 km loop with a 0.3 km dead end at a. With parking by t the dead end is
# the stem, so the main walk is 1.1 km; without it the dead end is set aside
# and the 0.8 km left is under the 1 km minimum.
LOLLIPOP = {
    "a": (0, 0),
    "b": (0, 0.2),
    "m": (0.1, 0.2),
    "c": (0.2, 0.2),
    "d": (0.2, 0),
    "t": (0, -0.3),
}
LOLLIPOP_WAYS = {1: "ab", 2: "bmc", 3: "cd", 4: "da", 5: "at"}
PARKING_BY_T = [(0, -0.35)]


def _lollipop(tmp_path: Path, parking: list[tuple[float, float]]):
    osm = tmp_path / "lollipop.osm"
    osm.write_text(_osm(LOLLIPOP, LOLLIPOP_WAYS, [1, 2, 3, 4, 5], parking))
    catalog_path = tmp_path / "catalog.sqlite"
    catalog = build_catalog(osm, catalog_path)
    kept = {hike.id for hike in catalog.dayhikes}
    return _sorted_pbf(osm, tmp_path / "lollipop.osm.pbf"), geometry_store_path_for(catalog_path), kept


@needs_osmium
def test_a_dayhike_ingest_dropped_without_its_stem_is_under_1_km_to_the_helper(
    tmp_path: Path,
) -> None:
    extract, geometry, kept = _lollipop(tmp_path, [])

    reasons = _main_walk_reasons({RELATION_ID}, extract=extract, geometry=geometry)

    assert RELATION_ID not in kept
    assert reasons == {RELATION_ID: "under 1 km"}


@needs_osmium
def test_a_dayhike_ingest_kept_with_its_stem_is_kept_to_the_helper(tmp_path: Path) -> None:
    extract, geometry, kept = _lollipop(tmp_path, PARKING_BY_T)

    reasons = _main_walk_reasons({RELATION_ID}, extract=extract, geometry=geometry)

    assert RELATION_ID in kept
    assert reasons == {RELATION_ID: "kept by the main-walk rules"}


@needs_osmium
def test_a_node_network_leg_left_by_an_osm_edit_is_a_leg_to_the_helper(tmp_path: Path) -> None:
    osm = tmp_path / "leg.osm"
    osm.write_text(
        _osm(
            {"a": (0, 0), "b": (0, 0.8), "c": (0.6, 0.8)}, {1: "ab", 2: "bc"}, [1, 2],
            tags={"name": "Signpost A - Signpost B", "network:type": "node_network"},
        )
    )  # fmt: skip
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(osm, catalog_path)

    reasons = _main_walk_reasons(
        {RELATION_ID},
        extract=_sorted_pbf(osm, tmp_path / "leg.osm.pbf"),
        geometry=geometry_store_path_for(catalog_path),
    )

    assert reasons == {RELATION_ID: "node-network leg"}


def _straight_walk_store(path: Path, vertices: int) -> Path:
    """A GeometryStore of one DayHike: ``vertices`` points 100 m apart, each its own walked way."""
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE amenities (latitude REAL, longitude REAL, kind TEXT);"
        "CREATE TABLE dayhikes (id INTEGER PRIMARY KEY);"
        "CREATE TABLE vertices (dayhike_id INTEGER, seq INTEGER, node_id INTEGER, latitude REAL, longitude REAL);"
        "CREATE TABLE ways (dayhike_id INTEGER, node_ids TEXT, walk_order INTEGER);"
        "INSERT INTO dayhikes VALUES (1);"
    )
    connection.executemany(
        "INSERT INTO vertices VALUES (1, ?, ?, ?, 0.0)", [(i, 10 + i, i * 0.0009) for i in range(vertices)]
    )
    connection.executemany("INSERT INTO ways VALUES (1, ?, ?)", [(f"[{10 + i}]", i) for i in range(vertices)])
    connection.commit()
    connection.close()
    return path


def test_measuring_the_steps_of_a_long_walk_grows_about_linearly(tmp_path: Path) -> None:
    # Hikes have no upper length cap (ADR-0033): a 1,700 km line has tens of thousands of steps.
    timings = []
    for vertices in (2_000, 8_000):
        store = _straight_walk_store(tmp_path / f"walk{vertices}.sqlite", vertices)
        started = time.perf_counter()
        steps = _measure_walks(store)["long_steps"]
        timings.append(time.perf_counter() - started)
        assert steps == {1: pytest.approx(100.0, abs=1.0)}

    assert timings[1] < 8 * timings[0] + 0.5


@needs_osmium
def test_a_dayhike_whose_relation_left_osm_is_not_in_the_extract_to_the_helper(tmp_path: Path) -> None:
    # On a fresh snapshot a relation deleted in OSM is a departure, not an error.
    extract, geometry, _ = _lollipop(tmp_path, [])

    reasons = _main_walk_reasons({RELATION_ID, 999_999}, extract=extract, geometry=geometry)

    assert reasons == {RELATION_ID: "under 1 km", 999_999: "not in the extract"}


# A 400 m square walked from (0, 0) back to it, a vertex every 100 m: a loop.
SQUARE = [(0, 0), (0, 1), (0, 2), (0, 3), (0, 4), (1, 4), (2, 4), (3, 4), (4, 4),
          (4, 3), (4, 2), (4, 1), (4, 0), (3, 0), (2, 0), (1, 0), (0, 0)]  # fmt: skip
STEP_DEG = 0.0009  # about 100 m


def _loop_store(path: Path, parking: list[tuple[float, float]]) -> Path:
    """A GeometryStore of DayHike 1, the SQUARE loop, and DayHike 2, a line by every parking point."""
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE amenities (latitude REAL, longitude REAL, kind TEXT);"
        "CREATE TABLE dayhikes (id INTEGER PRIMARY KEY);"
        "CREATE TABLE vertices (dayhike_id INTEGER, seq INTEGER, node_id INTEGER, latitude REAL, longitude REAL);"
        "CREATE TABLE ways (dayhike_id INTEGER, node_ids TEXT, walk_order INTEGER);"
        "INSERT INTO dayhikes VALUES (1), (2);"
    )
    connection.executemany("INSERT INTO amenities VALUES (?, ?, 'parking')", parking)
    connection.executemany(
        "INSERT INTO vertices VALUES (1, ?, ?, ?, ?)",
        [(i, 10 + i, row * STEP_DEG, column * STEP_DEG) for i, (row, column) in enumerate(SQUARE)],
    )
    line = [(5.0, 0.0), (5.0, 4 * STEP_DEG)]  # far from the square, by no parking point either
    connection.executemany(
        "INSERT INTO vertices VALUES (2, ?, ?, ?, ?)", [(i, 100 + i, lat, lon) for i, (lat, lon) in enumerate(line)]
    )
    connection.commit()
    connection.close()
    return path


@pytest.mark.parametrize(
    ("parking", "near", "failing"),
    [
        ([(4 * STEP_DEG + 0.0002, 4 * STEP_DEG)], [1], [1]),  # by the far corner only: starts 566 m off
        ([(0.0002, 0.0)], [1], []),  # by the start
        ([(2 * STEP_DEG, 2 * STEP_DEG)], [], []),  # in the middle, 200 m from every vertex
        ([], [], []),
    ],
)
def test_a_loop_by_a_parking_point_and_where_it_starts(tmp_path, parking, near, failing) -> None:
    measures = _measure_walks(_loop_store(tmp_path / "loop.sqlite", parking))

    assert (measures["loops"], measures["loops_near_parking"], measures["loops_failing"]) == (1, near, failing)


def test_a_hike_start_is_measured_to_the_nearest_parking_and_place(tmp_path: Path) -> None:
    store = _loop_store(tmp_path / "loop.sqlite", [(0.0, 4 * STEP_DEG)])  # 400 m east of the square's start
    connection = sqlite3.connect(store)
    connection.execute("INSERT INTO amenities VALUES (?, 0.0, 'place')", (-STEP_DEG,))  # 100 m south of it
    connection.commit()
    connection.close()

    to_parking, to_place = _start_distances(store, [1])

    assert to_parking == {1: pytest.approx(400.0, rel=0.01)}
    assert to_place == {1: pytest.approx(100.0, rel=0.01)}
