"""A way back is never harder than the hike (ADR-0076).

tests/fixtures/graded_way_back.osm holds three Summit Trails, each with a
shorter graded path between its ends (see its comment).
"""

import math
from pathlib import Path

import numpy as np
import pytest

from dayhike import build_catalog
from dayhike.catalog import SAC_SCALE_RANK, trailgraph_path_for
from dayhike.similarity import FACT_COLUMNS, SimilarityStore, build_similarity
from dayhike.trailgraph import WAY_BACK_KINDS, TrailGraph, way_back

FIXTURE = Path(__file__).parent / "fixtures" / "graded_way_back.osm"
HIKING = float(SAC_SCALE_RANK["hiking"])


@pytest.fixture(scope="module")
def catalog(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("graded_way_back") / "catalog.sqlite"
    build_catalog(FIXTURE, path)
    return path


@pytest.fixture(scope="module")
def graph(catalog: Path) -> TrailGraph:
    return TrailGraph(trailgraph_path_for(catalog))


def _summit_trail(lon: float) -> tuple[np.ndarray, np.ndarray]:
    """The Summit Trail whose portal is at this longitude: P - W - S."""
    return np.array([36.0, 36.0, 36.05]), np.array([lon, lon + 0.1, lon])


def test_a_hike_whose_shorter_way_back_is_a_scramble_is_retraced(graph: TrailGraph) -> None:
    # Mount Whitney Trail: up the trail, and the only shorter way down is the
    # Mountaineers Route. A hiking-graded hike is walked back down its own trail.
    lat, lon = _summit_trail(-118.0)

    back = way_back(lat, lon, graph, HIKING)

    assert back.kind == "retrace"
    assert back.metres == pytest.approx(way_back(lat, lon, None, HIKING).metres)
    # A hike as hard as the scramble still takes it.
    scramble = way_back(lat, lon, graph, float(SAC_SCALE_RANK["demanding_alpine_hiking"]))
    assert scramble.kind == "trail" and scramble.metres == pytest.approx(5_560, abs=20)


def test_an_easy_way_back_beside_a_harder_shorter_one_is_taken(graph: TrailGraph) -> None:
    # The scramble and the easy descent join the same two Junctions: the
    # search keeps the easy one, though the scramble is shorter.
    lat, lon = _summit_trail(-116.0)

    back = way_back(lat, lon, graph, HIKING)

    assert back.kind == "trail"
    assert back.walk.sac_scale == "hiking"
    assert back.metres == pytest.approx(7_740, abs=40)


def test_a_hike_with_no_grade_takes_no_graded_way_back(graph: TrailGraph) -> None:
    # A way back with a grade would give the round trip a Difficulty the hike has not.
    lat, lon = _summit_trail(-114.0)

    assert way_back(lat, lon, graph, math.nan).kind == "retrace"
    assert way_back(lat, lon, graph, float(SAC_SCALE_RANK["demanding_mountain_hiking"])).kind == "trail"


def test_a_network_saved_without_grades_is_built_again(catalog: Path, tmp_path: Path) -> None:
    graph = TrailGraph(trailgraph_path_for(catalog))
    graph.write_network()
    (graph.network_dir / "sac_rank.npy").unlink()  # as an export before ADR-0076 saved it

    lat, lon = _summit_trail(-118.0)

    assert way_back(lat, lon, TrailGraph(graph.path), HIKING).kind == "retrace"


@pytest.fixture(scope="module")
def store(catalog: Path, tmp_path_factory: pytest.TempPathFactory) -> SimilarityStore:
    tmp_path = tmp_path_factory.mktemp("graded_way_back_similarity")
    (tmp_path / "dem").mkdir()
    (tmp_path / "landcover").mkdir()
    build_similarity(catalog, tmp_path / "dem", landcover_dir=tmp_path / "landcover")
    return SimilarityStore.open(catalog)


def test_the_similarity_facts_measure_no_way_back_harder_than_the_hike(store: SimilarityStore) -> None:
    rank = FACT_COLUMNS.index("difficulty_rank")

    np.testing.assert_array_equal(
        np.nan_to_num(store.with_way_back.facts[rank]), np.nan_to_num(store.facts[rank])
    )
    kinds = {WAY_BACK_KINDS[int(kind)] for kind in store.with_way_back.way_back[3]}
    assert kinds == {"trail", "retrace"}


def test_a_hiking_trail_beside_a_shorter_scramble_keeps_its_grade_with_the_way_back(
    store: SimilarityStore,
) -> None:
    # Mount Whitney Trail (ADR-0078): the shortest way back is the Mountaineers
    # Route, but the trail is retraced and the round trip stays T1.
    back = store.with_way_back
    index = list(back.ids).index(4001)

    assert WAY_BACK_KINDS[int(back.way_back[3][index])] == "retrace"
    assert back.facts[FACT_COLUMNS.index("difficulty_rank")][index] == HIKING
