"""Graded paths in the TrailGraph (ADR-0037).

tests/fixtures/graded_paths.osm has two marked trails and a graded
``highway=path`` (a ``sac_scale``, in no relation) that closes a loop with
one of them, beside paths that stay out: one with no ``sac_scale``, a track
with one, and one whose ``sac_scale`` is no grade.
"""

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from test_streaming_ingest import _sorted_pbf
from test_trailgraph import _route, _write_rasters

from dayhike import build_catalog, build_similarity, sample_climate, sample_dem, sample_landcover
from dayhike.catalog import trailgraph_path_for
from dayhike.loops import LoopTarget, find_loops
from dayhike.trailgraph import sample_trailgraph

FIXTURE = Path(__file__).parent / "fixtures" / "graded_paths.osm"
VALLEY_TRAIL, RIDGE_TRAIL = 3001, 3002
GRADED_PATH = 210
B, C = 2, 3  # node ids (fixture comments)


@pytest.fixture(scope="module")
def catalog_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("graded") / "catalog.sqlite"
    build_catalog(FIXTURE, path)
    return path


def _segments(path: Path) -> list[dict]:
    db = sqlite3.connect(trailgraph_path_for(path))
    db.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in db.execute("SELECT * FROM segments WHERE kind = 'trail' ORDER BY id")]
    finally:
        db.close()


def _on_way(segments: list[dict], way_id: int) -> dict:
    (found,) = [s for s in segments if way_id in json.loads(s["way_ids"])]
    return found


def test_a_graded_path_in_no_relation_is_a_segment_with_no_relation_id(catalog_path: Path) -> None:
    segments = _segments(catalog_path)

    graded = _on_way(segments, GRADED_PATH)
    assert json.loads(graded["relation_ids"]) == []
    assert graded["sac_scale"] == "mountain_hiking"
    assert {graded["a"], graded["b"]} == {B, C}
    assert graded["length_m"] == pytest.approx(3_000, abs=1)
    # It joins the Valley Trail at B and C: the trail is two Segments there,
    # and its own graded way B - C keeps the relation's id.
    assert json.loads(_on_way(segments, 201)["relation_ids"]) == [VALLEY_TRAIL]
    assert json.loads(_on_way(segments, 202)["relation_ids"]) == [VALLEY_TRAIL]
    db = sqlite3.connect(trailgraph_path_for(catalog_path))
    try:
        degrees = dict(db.execute("SELECT id, degree FROM junctions"))
    finally:
        db.close()
    assert degrees[B] == degrees[C] == 3
    # Paths with no grade, tracks and a sac_scale that is no grade stay out.
    assert {way for s in segments for way in json.loads(s["way_ids"])} == {201, 202, GRADED_PATH, 220}
    # A graded path is no DayHike.
    ids = {row[0] for row in sqlite3.connect(catalog_path).execute("SELECT id FROM dayhikes")}
    assert ids == {VALLEY_TRAIL, RIDGE_TRAIL}


@pytest.mark.skipif(shutil.which("osmium") is None, reason="osmium-tool")
def test_the_catalog_extract_keeps_graded_paths(tmp_path: Path) -> None:
    source = _sorted_pbf(FIXTURE, tmp_path / "graded.osm.pbf")
    path = tmp_path / "catalog.sqlite"

    build_catalog(source, path)

    graded = _on_way(_segments(path), GRADED_PATH)
    assert json.loads(graded["relation_ids"]) == [] and graded["length_m"] == pytest.approx(3_000, abs=1)


@pytest.fixture(scope="module")
def data(tmp_path_factory: pytest.TempPathFactory):
    from dayhike.serving import ServingData, export_serving

    root = tmp_path_factory.mktemp("graded-export")
    dem, landcover, climate = _write_rasters(root)
    path = root / "catalog.sqlite"
    build_catalog(FIXTURE, path)
    sample_dem(path, dem)
    sample_landcover(path, landcover)
    sample_climate(path, climate)
    sample_trailgraph(path, dem_dir=dem, landcover_dir=landcover, climate_dir=climate)
    build_similarity(path, dem, landcover_dir=landcover)
    return ServingData(export_serving(path, root / "web-serving"))


def test_a_loop_closes_over_a_graded_path(data) -> None:
    # The Valley Trail and the Ridge Trail are lines: only the graded path closes a loop.
    target = LoopTarget(4_000.0, float("nan"), float("nan"), float("nan"), None)

    found = find_loops(data.trailgraph, target, [(46.05, 14.0629579576)], limit=10, deadline=float("inf"))

    assert found
    walk = data.trailgraph.walk_segments(found[0].start, found[0].segments)
    assert (walk.lat[0], walk.lon[0]) == (walk.lat[-1], walk.lon[-1])
    assert sum(walk.relation_m.values()) == pytest.approx(1_000, abs=1)  # the rest is graded path


def test_a_walk_on_graded_paths_only_is_named_by_the_place_at_its_start(data, catalog_path: Path) -> None:
    graded = _on_way(_segments(catalog_path), GRADED_PATH)

    hike = data.compose(_route(catalog_path, B, [graded["id"]]))

    assert hike["name"] == "Your hike from Mossy Spring"
    assert hike["routes"] == [] and hike["walk_m"] == pytest.approx(3_000, abs=1)
