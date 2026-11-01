"""NetworkHikes in the Catalog (ADR-0039): ingest chains node-network legs into day hikes.

tests/fixtures/node_network.osm has two small node networks, each with a
parking by one signpost: one names its signposts in its legs and leads to a
named lake, the other leaves its start "?" beside a named guidepost and walks
the loop of an ordinary route relation.
"""

from pathlib import Path

import pytest

from dayhike import Catalog, build_catalog

FIXTURE = Path(__file__).parent / "fixtures" / "node_network.osm"
LE_PINET = (44.9, 6.6)  # signpost S, by the parking


@pytest.fixture(scope="module")
def catalog(tmp_path_factory: pytest.TempPathFactory) -> Catalog:
    return build_catalog(FIXTURE, tmp_path_factory.mktemp("network") / "catalog.sqlite")


def _network(catalog: Catalog) -> dict[str, object]:
    return {hike.name: hike for hike in catalog.dayhikes if hike.kind == "network"}


def test_ingest_chains_node_network_legs_into_a_network_hike(catalog: Catalog) -> None:
    loop = _network(catalog)["Loop from Le Pinet via Serre Lan"]

    assert (loop.kind, loop.name_origin, loop.parts) == ("network", "network", (11, 12, 13))
    assert (loop.trailhead.latitude, loop.trailhead.longitude) == LE_PINET
    assert abs(loop.length_m - (2_000 + 2_000 + 2_000 * 2**0.5)) < 5


def test_a_chain_that_walks_a_relation_dayhike_is_left_to_the_relation(catalog: Catalog) -> None:
    by_id = {hike.id: hike for hike in catalog.dayhikes}
    assert by_id[2001].name == "Tour des Combes"
    walked = [set(hike.parts) for hike in _network(catalog).values()]
    assert {21, 22, 23} not in walked
    assert {24, 25, 26} in walked


def test_a_line_to_a_named_lake_goes_there(catalog: Catalog) -> None:
    line = _network(catalog)["Le Pinet – Lac de l'Eychauda"]

    assert line.parts == (14, 15)  # leg 15 is a route=foot


def test_a_named_guidepost_names_a_start_its_legs_leave_unnamed(catalog: Catalog) -> None:
    loop = _network(catalog)["Loop from Col de Granon via Les Granges"]

    assert loop.parts in ((24, 25, 26), (26, 25, 24))
