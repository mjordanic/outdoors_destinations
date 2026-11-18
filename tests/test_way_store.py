"""WayStore: the work file's ways as flat arrays behind the dict-like reads ingest makes."""

import json

import numpy as np
import pytest

from dayhike.osm import OsmWay, WayStore


def _rows():
    # Work-file rows (id, node ids JSON, tags JSON), not in id order, two with the same tags.
    return [
        (30, json.dumps([5, 6, 7]), json.dumps({"highway": "path", "sac_scale": "hiking"})),
        (10, json.dumps([1, 2]), json.dumps({"highway": "path", "sac_scale": "hiking"})),
        (12_000_000_000, json.dumps([7, 12_345_678_901, 5]), json.dumps({"highway": "track", "name": "Pot"})),
        (20, json.dumps([]), json.dumps({})),
    ]


def _dict(rows):
    # What ingest held before: one OsmWay per row, in row order.
    return {way_id: OsmWay(tuple(json.loads(node_ids)), json.loads(tags)) for way_id, node_ids, tags in rows}


def test_a_way_reads_as_the_osm_way_of_its_row():
    ways = WayStore.from_rows(_rows())

    assert ways[30] == OsmWay((5, 6, 7), {"highway": "path", "sac_scale": "hiking"})
    assert ways[12_000_000_000].node_ids == (7, 12_345_678_901, 5)
    assert ways[20] == OsmWay((), {})
    assert type(ways[30].node_ids) is tuple and type(ways[30].node_ids[0]) is int


def test_a_missing_way_is_not_in_the_store():
    ways = WayStore.from_rows(_rows())

    assert 11 not in ways and 10 in ways
    assert ways.get(11) is None
    with pytest.raises(KeyError):
        ways[11]


def test_the_store_iterates_in_row_order_like_the_dict_it_replaces():
    ways, before = WayStore.from_rows(_rows()), _dict(_rows())

    assert list(ways) == list(before) == [30, 10, 12_000_000_000, 20]
    assert len(ways) == 4
    assert list(ways.items()) == list(before.items())
    assert list(ways.values()) == list(before.values())
    assert dict(ways) == before


def test_ways_of_the_same_tags_share_one_dict():
    ways = WayStore.from_rows(_rows())

    assert ways[10].tags is ways[30].tags


def test_ways_added_later_read_after_the_loaded_ones():
    ways, before = WayStore.from_rows(_rows()), _dict(_rows())
    added = {99: OsmWay((1, 9), {"highway": "path"}), 5: OsmWay((2, 3), {"highway": "footway"})}

    ways.update(added)
    before.update(added)

    assert list(ways.items()) == list(before.items())
    assert 99 in ways and ways[5] == added[5] and len(ways) == 6


def test_an_empty_store_holds_nothing():
    ways = WayStore.from_rows([])

    assert len(ways) == 0 and 5 not in ways and list(ways) == []


def test_sparse_and_clustered_ids_read_like_a_dict():
    # Way ids clustered and far apart, in no order, as two files' are.
    rng = np.random.default_rng(5)
    ids = rng.permutation(np.unique(np.concatenate([rng.integers(1, 3_000, 400), rng.integers(1_200_000_000, 1_200_090_000, 400)])))
    rows = [
        (int(i), json.dumps(rng.integers(1, 10**10, int(i) % 9).tolist()), json.dumps({"highway": ["path", "track"][int(i) % 2]}))
        for i in ids
    ]
    ways, before = WayStore.from_rows(rows), _dict(rows)

    probes = sorted({p for i in before for p in (i - 1, i, i + 1)} | {0, -5})
    assert [ways.get(p) for p in probes] == [before.get(p) for p in probes]
    assert [p in ways for p in probes] == [p in before for p in probes]
    assert list(ways.items()) == list(before.items())
