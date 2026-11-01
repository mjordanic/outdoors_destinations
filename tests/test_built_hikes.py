"""Built DayHikes: Catalog hikes that are not one OSM relation (ADR-0038).

A built-hike source gets what ingest read and returns walks; the Catalog stores
each as a DayHike with a stable id from its OSM way/node steps.
"""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dayhike import build_catalog, build_similarity
from dayhike.built import BUILT_ID_MIN, BuiltWalk
from dayhike.catalog import geometry_store_path_for
from dayhike.serving import ServingData, export_serving
from dayhike.web import create_app

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
RIDGE_WALK_ID = 1001
# Ridge Walk's second half, nodes 2 → 4 → 3 along way 102: 5 km at 0.0449660803° of latitude.
STAGE_NODES = (2, 4, 3)
STAGE_M = 5_000


def ridge_stage(context) -> list[BuiltWalk]:
    """A source that cuts Ridge Walk's second half out as a stage."""
    nodes = context.snapshot.nodes
    return [
        BuiltWalk(
            kind="stage",
            name="Ridge Walk, stage 2: Col – Summit",
            vertices=tuple((node, nodes[node]) for node in STAGE_NODES),
            way_ids=(102,),
            parts=(RIDGE_WALK_ID,),
        )
    ]


def test_a_source_adds_a_built_dayhike_with_its_kind_parts_and_geometry(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite", sources=[ridge_stage])

    (built,) = [hike for hike in catalog.dayhikes if hike.kind != "relation"]
    assert built.id >= BUILT_ID_MIN
    assert built.id < 2**53  # a JavaScript-safe integer
    assert (built.kind, built.parts) == ("stage", (RIDGE_WALK_ID,))
    assert (built.name, built.name_origin) == ("Ridge Walk, stage 2: Col – Summit", "stage")
    assert abs(built.length_m - STAGE_M) < 1
    assert (built.trailhead.latitude, built.trailhead.longitude) == (45.0449661, 15.0)  # node 2
    assert built.difficulty.sac_scale == "mountain_hiking"
    assert built.route == "2:102.3"
    with sqlite3.connect(geometry_store_path_for(tmp_path / "catalog.sqlite")) as geo:
        nodes = [row[0] for row in geo.execute("SELECT node_id FROM vertices WHERE dayhike_id = ? ORDER BY seq", (built.id,))]
        ways = geo.execute("SELECT way_id, walk_order, walked_m FROM ways WHERE dayhike_id = ?", (built.id,)).fetchall()
    assert nodes == list(STAGE_NODES)
    assert [(way, order) for way, order, _ in ways] == [(102, 0)]
    assert abs(ways[0][2] - STAGE_M) < 1


def test_a_built_dayhike_keeps_its_id_across_rebuilds_and_leaves_the_relation_dayhikes_alone(tmp_path: Path) -> None:
    plain = build_catalog(FIXTURE, tmp_path / "plain.sqlite")
    first = build_catalog(FIXTURE, tmp_path / "first.sqlite", sources=[ridge_stage])
    # Two sources building the same walk give one DayHike.
    again = build_catalog(FIXTURE, tmp_path / "again.sqlite", sources=[ridge_stage, ridge_stage])

    def built(catalog) -> list[int]:
        return [hike.id for hike in catalog.dayhikes if hike.kind != "relation"]

    assert len(built(first)) == 1
    assert built(again) == built(first)
    relations = [hike for hike in first.dayhikes if hike.kind == "relation"]
    assert relations == list(plain.dayhikes)
    assert all(hike.parts == () and hike.route is None for hike in plain.dayhikes)
    assert first.ingest_report.kept == plain.ingest_report.kept + 1


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    directory = tmp_path_factory.mktemp("built")
    path = directory / "catalog.sqlite"
    build_catalog(FIXTURE, path, sources=[ridge_stage])
    build_similarity(path, directory / "dem")
    return TestClient(create_app(ServingData(export_serving(path, directory / "web-serving")), None))


def built_id(client: TestClient) -> int:
    (found,) = client.get("/api/search", params={"q": "stage 2"}).json()["results"]
    return found["id"]


def test_the_export_serves_a_built_dayhike_with_what_it_is_built_from(client: TestClient) -> None:
    hike = client.get(f"/api/hikes/{built_id(client)}").json()

    assert hike["name"] == "Ridge Walk, stage 2: Col – Summit"
    assert hike["built"] == {"kind": "stage", "parts": [RIDGE_WALK_ID], "route": "2:102.3"}
    assert abs(hike["walk_m"] - STAGE_M) < 1
    assert client.get(f"/api/hikes/{RIDGE_WALK_ID}").json()["built"] is None


def test_a_ranking_lists_a_built_dayhike_and_takes_it_as_a_seed(client: TestClient) -> None:
    stage = built_id(client)

    def listed(body: dict) -> set[int]:
        rows = client.post("/api/rank", json=body).json()["results"]
        return {hike["id"] for row in rows for hike in [row, *row.get("versions", [])]}

    assert stage in listed({"seed": RIDGE_WALK_ID})
    assert RIDGE_WALK_ID in listed({"seed": stage})
