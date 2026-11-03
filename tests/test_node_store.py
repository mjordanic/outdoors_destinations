"""NodeStore: the work file's nodes as sorted arrays behind the dict-like reads ingest makes."""

import math

import numpy as np
import pytest

from dayhike.osm import NodeStore


def _store() -> NodeStore:
    # Out of id order, one without ele.
    return NodeStore.from_rows([(30, 45.5, 15.25, 812.0), (10, 46.0, 7.75, None), (20, -1.5, 0.125, 0.0)])


def test_a_node_reads_as_its_lat_lon_pair():
    nodes = _store().points

    assert nodes[10] == (46.0, 7.75)
    assert nodes[30] == (45.5, 15.25)
    assert type(nodes[20][0]) is float


def test_a_missing_node_is_not_in_the_store():
    nodes = _store().points

    assert 15 not in nodes and 99 not in nodes and 10 in nodes
    assert nodes.get(15) is None
    with pytest.raises(KeyError):
        nodes[15]


def test_the_store_iterates_its_ids_in_order_and_counts_them():
    nodes = _store().points

    assert list(nodes) == [10, 20, 30]
    assert len(nodes) == 3
    assert dict(nodes.items()) == {10: (46.0, 7.75), 20: (-1.5, 0.125), 30: (45.5, 15.25)}


def test_elevations_hold_only_the_nodes_with_an_ele():
    elevations = _store().elevations

    assert dict(elevations.items()) == {20: 0.0, 30: 812.0}
    assert 10 not in elevations
    assert elevations.get(10) is None
    assert math.isnan(elevations.get(10, np.nan))


def test_an_empty_store_holds_nothing():
    store = NodeStore.from_rows([])

    assert len(store.points) == 0 and 5 not in store.points and len(store.elevations) == 0
