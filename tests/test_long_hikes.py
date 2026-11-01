"""Long hikes downstream of ingest (ADR-0035): the WayBack bound, the loop
bound and the Wish inputs for hikes of 20 to 2,000 km.

tests/fixtures/long_way_back.osm holds two long walks, each with a shorter
trail back between its ends (see its comment).
"""

import time
from pathlib import Path

import numpy as np
import pytest

from dayhike import build_catalog
from dayhike.catalog import trailgraph_path_for
from dayhike.loops import LoopTarget, find_loops
from dayhike.trailgraph import TrailGraph, way_back

FIXTURE = Path(__file__).parent / "fixtures" / "long_way_back.osm"
N1, M1, N2 = (45.0, 6.0), (45.54, 7.0), (46.08, 6.0)  # Zigzag Line
N3, M2, N4 = (45.0, 10.0), (45.5, 11.0), (45.0, 10.0635)  # Long Near Loop
N5, M3, N6 = (45.0, 14.0), (45.18, 15.0), (45.36, 14.0)  # Wide Line


@pytest.fixture(scope="module")
def graph(tmp_path_factory: pytest.TempPathFactory) -> TrailGraph:
    path = tmp_path_factory.mktemp("long_hikes") / "catalog.sqlite"
    build_catalog(FIXTURE, path)
    return TrailGraph(trailgraph_path_for(path))


def _walk(*points: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    return np.array([p[0] for p in points]), np.array([p[1] for p in points])


def test_a_line_whose_trail_back_is_far_retraces_instead_of_failing(graph: TrailGraph) -> None:
    # The 120 km Straight Back is shorter than retracing the zigzag, but a trail
    # back that long is past the bound: the walk is retraced, and nothing fails
    # (build-similarity used to stop here with "at most 100 km long").
    lat, lon = _walk(N1, M1, N2)

    back = way_back(lat, lon, graph)

    assert back.kind == "retrace"
    assert back.metres == pytest.approx(way_back(lat, lon, None).metres)
    assert back.metres > 190_000


def test_a_long_walk_ending_near_its_start_still_takes_the_trail_back(graph: TrailGraph) -> None:
    # The bound is on the way back, not on the walk: a 184 km tour ending 5 km
    # from its start walks those 5 km back, as a day hike would.
    lat, lon = _walk(N3, M2, N4)

    back = way_back(lat, lon, graph)

    assert way_back(lat, lon, None).metres > 180_000
    assert back.kind == "trail"
    assert back.metres == pytest.approx(4_990, abs=20)


def test_the_trail_search_stops_at_the_bound(graph: TrailGraph) -> None:
    # Its ends are 40 km apart, within the bound, but the only trail between
    # them is the 74 km Detour Back: past the bound, so the line is retraced.
    lat, lon = _walk(N5, M3, N6)

    back = way_back(lat, lon, graph)

    assert back.kind == "retrace"
    assert back.metres == pytest.approx(way_back(lat, lon, None).metres)


def test_a_multi_day_seed_gets_no_suggested_loops(graph: TrailGraph) -> None:
    # Loops are searched up to 40 km only, a bound on one vCPU (ADR-0035): a
    # 100 km Seed asks for loops of 75 km and more, so none are searched.
    target = LoopTarget(length_m=100_000, highest_m=2_000, lowest_m=1_000, gain_m=3_000, shape=None)
    started = time.monotonic()

    loops = find_loops(graph, target, [N3, N5], limit=5, deadline=started + 5)

    assert loops == []
    assert time.monotonic() - started < 0.5
