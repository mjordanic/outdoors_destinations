"""Paths in no relation read with the ``paths`` ingest option (ADR-0065).

tests/fixtures/named_paths.osm has one marked trail and, in no relation, a
named path that closes a loop with it, a named track, a named sidewalk and an
unnamed path. Each test ingests the fixture as a PBF, so it goes through the
osmium catalog extract as a real snapshot does.
"""

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from test_streaming_ingest import _sorted_pbf

from dayhike import build_catalog
from dayhike.catalog import trailgraph_path_for
from dayhike.ingest import run_ingest

FIXTURE = Path(__file__).parent / "fixtures" / "named_paths.osm"
NETWORK_FIXTURE = Path(__file__).parent / "fixtures" / "network_paths.osm"
LAKE_PATH, FOREST_ROAD, SIDEWALK, UNNAMED_PATH = 230, 231, 232, 233
N1, N2 = 4, 5  # nodes only the Lake Path uses

pytestmark = pytest.mark.skipif(shutil.which("osmium") is None, reason="osmium-tool")


@pytest.fixture(scope="module")
def pbf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _sorted_pbf(FIXTURE, tmp_path_factory.mktemp("named-pbf") / "named.osm.pbf")


def _trail_way_ids(catalog_path: Path) -> dict[int, dict]:
    db = sqlite3.connect(trailgraph_path_for(catalog_path))
    db.row_factory = sqlite3.Row
    try:
        segments = [dict(row) for row in db.execute("SELECT * FROM segments WHERE kind = 'trail'")]
    finally:
        db.close()
    return {way_id: segment for segment in segments for way_id in json.loads(segment["way_ids"])}


def _build(pbf: Path, root: Path, **options) -> Path:
    path = root / "catalog.sqlite"
    build_catalog(pbf, path, **options)
    return path


def test_by_default_a_named_path_in_no_relation_stays_out(pbf: Path, tmp_path: Path) -> None:
    on_way = _trail_way_ids(_build(pbf, tmp_path))

    assert LAKE_PATH not in on_way and FOREST_ROAD not in on_way
    assert set(on_way) == {201, 202}


def test_a_named_path_in_no_relation_joins_the_trailgraph(pbf: Path, tmp_path: Path) -> None:
    on_way = _trail_way_ids(_build(pbf, tmp_path, paths="named"))

    lake = on_way[LAKE_PATH]
    assert json.loads(lake["relation_ids"]) == []
    assert lake["length_m"] == pytest.approx(3_000, abs=1)
    assert json.loads(on_way[FOREST_ROAD]["relation_ids"]) == []
    # A sidewalk stays out though named, and an unnamed path needs the next level.
    assert SIDEWALK not in on_way and UNNAMED_PATH not in on_way


def test_unnamed_paths_join_at_the_unnamed_level(pbf: Path, tmp_path: Path) -> None:
    on_way = _trail_way_ids(_build(pbf, tmp_path, paths="unnamed"))

    assert {LAKE_PATH, FOREST_ROAD, UNNAMED_PATH} <= set(on_way)
    assert SIDEWALK not in on_way


def test_a_named_path_in_no_relation_enters_the_snapshot(pbf: Path, tmp_path: Path) -> None:
    _, _, _, default = run_ingest([pbf], tmp_path / "default" / "catalog.sqlite")
    _, _, _, named = run_ingest([pbf], tmp_path / "named" / "catalog.sqlite", paths="named")

    assert LAKE_PATH not in default.ways and N1 not in default.nodes
    assert named.ways[LAKE_PATH].tags["name"] == "Lake Path"
    assert named.ways[LAKE_PATH].node_ids == (2, N1, N2, 3)
    assert N1 in named.nodes and N2 in named.nodes
    assert SIDEWALK not in named.ways


def test_the_paths_option_leaves_the_catalog_dayhikes_unchanged(pbf: Path, tmp_path: Path) -> None:
    default = build_catalog(pbf, tmp_path / "default" / "catalog.sqlite")
    named = build_catalog(pbf, tmp_path / "named" / "catalog.sqlite", paths="named")

    assert [(h.id, h.name, h.length_m) for h in named.dayhikes] == [
        (h.id, h.name, h.length_m) for h in default.dayhikes
    ]
    assert len(default.dayhikes) == 1


def test_a_network_hike_does_not_walk_a_path_in_no_relation_to_its_parking(tmp_path: Path) -> None:
    # tests/fixtures/network_paths.osm: a node-network loop whose signpost
    # reaches its parking only over a named track in no relation (way 240).
    pbf = _sorted_pbf(NETWORK_FIXTURE, tmp_path / "network.osm.pbf")
    default = build_catalog(pbf, tmp_path / "default" / "catalog.sqlite")
    named_path = tmp_path / "named" / "catalog.sqlite"
    named = build_catalog(pbf, named_path, paths="named")

    def network(catalog):
        return [
            (h.id, h.name, h.parts, h.length_m, h.trailhead.latitude, h.trailhead.longitude)
            for h in catalog.dayhikes
            if h.kind == "network"
        ]

    assert len(network(default)) == 1
    assert network(named) == network(default)
    assert 240 in _trail_way_ids(named_path)


def test_a_work_file_of_another_paths_level_is_not_resumed(pbf: Path, tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    run_ingest([pbf], catalog_path)  # leaves its work file, as a run stopped before writing does

    _, _, _, snapshot = run_ingest([pbf], catalog_path, paths="named")

    assert LAKE_PATH in snapshot.ways


def test_an_unknown_paths_level_is_refused(pbf: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="paths"):
        build_catalog(pbf, tmp_path / "catalog.sqlite", paths="every")


def test_the_ingest_command_takes_the_paths_level(pbf: Path, tmp_path: Path) -> None:
    from dayhike.cli import main

    path = tmp_path / "catalog.sqlite"
    assert main(["ingest", str(pbf), "--catalog", str(path), "--paths", "named"]) == 0

    assert LAKE_PATH in _trail_way_ids(path)
