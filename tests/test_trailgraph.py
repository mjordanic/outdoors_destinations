"""The TrailGraph (ADR-0018): the marked network as Junctions and Segments, and ComposedHikes.

tests/fixtures/trailgraph.osm is a small network with one of each case the
graph handles: a Junction where a short relation leaves, a dead-end tip 12 m
from another way, a crossing with no node, a bridge, and two ways passing
15 m apart with no connection near.
"""

import json
import math
import re
import shutil
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest
from test_climate import _write_tif

from dayhike import (
    build_catalog,
    build_similarity,
    geometry_store_path_for,
    sample_climate,
    sample_dem,
    sample_landcover,
)
from dayhike.catalog import similarity_dir_for, trailgraph_path_for
from dayhike.similarity import FACT_COLUMNS, SimilarityStore
from dayhike.trailgraph import Route, RouteError, TrailGraph, sample_trailgraph

FIXTURE = Path(__file__).parent / "fixtures" / "trailgraph.osm"
LAKE_LOOP, SUMMIT_LINE, CONNECTOR, CROSS_TRAIL, BRIDGE_PATH, MEADOW_PATH = 2001, 2002, 2004, 2005, 2006, 2007
A, S1, G, D, P, F, Z2 = 1, 2, 4, 11, 13, 20, 51  # node ids (fixture comments)
BOX = {"west": 14.0, "south": 46.03, "east": 14.1, "north": 46.08}


@pytest.fixture(scope="module")
def catalog_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("trailgraph") / "catalog.sqlite"
    build_catalog(FIXTURE, path)
    return path


def _segments(path: Path) -> list[dict]:
    db = sqlite3.connect(trailgraph_path_for(path))
    db.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in db.execute("SELECT * FROM segments ORDER BY id")]
    finally:
        db.close()


def _segment(segments: list[dict], a: int, b: int, kind: str = "trail") -> dict:
    (found,) = [s for s in segments if {s["a"], s["b"]} == {a, b} and s["kind"] == kind]
    return found


def _route(path: Path, start: int, segment_ids: Sequence[int]) -> Route:
    """The Route of a walk given by Junction and Segment ids, as the map page writes it."""
    db = sqlite3.connect(trailgraph_path_for(path))
    try:
        keys = dict(db.execute("SELECT id, key FROM junctions"))
        rows = {row[0]: row[1:] for row in db.execute("SELECT id, a, b, step_ab, step_ba FROM segments")}
    finally:
        db.close()
    here, steps = start, []
    for segment in segment_ids:
        a, b, step_ab, step_ba = rows[segment]
        steps.extend((step_ab if here == a else step_ba).split(","))
        here = b if here == a else a
    return Route(keys[start], tuple(steps))


def test_ingest_writes_the_trailgraph_of_every_relation_it_read(catalog_path: Path) -> None:
    segments = _segments(catalog_path)
    relations = {rel for s in segments for rel in json.loads(s["relation_ids"])}

    # The Spur Zuweg (2003) is 500 m, too short for a DayHike, and is still on the network.
    assert relations == {2001, 2002, 2003, 2004, 2005, 2006, 2007}
    assert 2003 not in {row[0] for row in sqlite3.connect(catalog_path).execute("SELECT id FROM dayhikes")}
    # Ways merge through nodes where only two meet: the Summit Line is two
    # Segments because the Spur leaves at D.
    summit = [s for s in segments if json.loads(s["relation_ids"]) == [SUMMIT_LINE]]
    assert sorted((s["a"], s["b"]) for s in summit) == [(A, D), (D, P)]
    up = _segment(segments, D, P)
    assert up["sac_scale"] == "demanding_mountain_hiking"
    assert json.loads(up["way_ids"]) == [105]
    # Per run of steps on one way: the way (negative against its node order), metres, surface, steps, sac_scale.
    assert json.loads(up["parts"]) == [
        [105 if up["a"] == D else -105, pytest.approx(up["length_m"]), "natural", 2, "demanding_mountain_hiking"]
    ]
    assert np.frombuffer(up["node_ids"], dtype="<i8").tolist() == [D, 12, P]
    assert np.frombuffer(up["ele"], dtype="<f4").tolist() == [900, 1250, 1640]


def test_a_dead_end_tip_joins_the_nearest_node_of_another_way_with_a_gap(catalog_path: Path) -> None:
    segments = _segments(catalog_path)

    gap = _segment(segments, G, F, kind="gap")
    assert gap["length_m"] == pytest.approx(12, abs=0.1)
    assert json.loads(gap["way_ids"]) == [] and json.loads(gap["parts"])[0][0] is None
    # G was a plain vertex of the Lake Loop; the join makes it a Junction.
    assert {(s["a"], s["b"]) for s in segments if G in (s["a"], s["b"]) and s["kind"] == "trail"} == {
        (S1, G),
        (A, G),
    }


def test_a_crossing_with_no_node_gets_one_and_a_bridge_is_never_joined(catalog_path: Path) -> None:
    graph = sqlite3.connect(trailgraph_path_for(catalog_path))
    junctions = dict(graph.execute("SELECT id, degree FROM junctions").fetchall())

    # The Cross Trail and the Spur meet at a new node with a negative id.
    assert junctions[-1] == 4
    # The Bridge Path passes over the Summit Line: its ends stay dead ends.
    assert junctions[40] == junctions[41] == 1
    assert _segment(_segments(catalog_path), 40, 41)["length_m"] == pytest.approx(1100, abs=1)


def test_ways_passing_with_no_connection_near_get_a_link(catalog_path: Path) -> None:
    segments = _segments(catalog_path)

    link = _segment(segments, S1, Z2, kind="link")
    assert link["length_m"] == pytest.approx(15, abs=0.1)
    # A Link is not part of the network until a Hiker picks it: it adds no degree.
    degree = dict(sqlite3.connect(trailgraph_path_for(catalog_path)).execute("SELECT id, degree FROM junctions"))
    assert degree[Z2] == 2 and degree[S1] == 2


def test_a_smaller_snap_tolerance_joins_no_tip_and_the_tolerance_is_capped(tmp_path: Path) -> None:
    from dayhike.cli import main

    path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, path, snap_tolerance_m=10)

    kinds = [s["kind"] for s in _segments(path)]
    assert "gap" not in kinds and "link" not in kinds
    # Refused before ingest starts: nothing is written.
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="at most 50"):
        build_catalog(FIXTURE, other / "catalog.sqlite", snap_tolerance_m=60)
    assert list(other.iterdir()) == []
    with pytest.raises(SystemExit) as exit_info:
        main(["ingest", str(FIXTURE), "--catalog", str(other / "catalog.sqlite"), "--snap-tolerance", "0"])
    assert exit_info.value.code == 2 and list(other.iterdir()) == []


def test_a_walk_follows_segments_end_to_end_and_may_reuse_one(catalog_path: Path) -> None:
    graph = TrailGraph(trailgraph_path_for(catalog_path))
    segments = _segments(catalog_path)
    up, top = _segment(segments, A, D)["id"], _segment(segments, D, P)["id"]

    there = graph.walk(_route(catalog_path, A, (up, top)))
    back = graph.walk(_route(catalog_path, A, (up, top, top, up)))

    assert (there.lat[0], there.lon[0]) == pytest.approx((46.05, 14.05))
    assert len(back.lat) == 2 * len(there.lat) - 1
    facts, _ = graph.facts(back)
    assert facts["length_m"] == pytest.approx(2 * graph.facts(there)[0]["length_m"])
    assert facts["round_trip"] == 1.0
    assert back.walked == [(pytest.approx(2 * there.walked[0][0]), "natural"), (pytest.approx(2 * there.walked[1][0]), "natural")]


@pytest.mark.parametrize(
    ("route", "message"),
    [
        ("1:", "at least one Segment"),
        ("999:104.11", "step 1 of this hike: OSM node 999 is no longer on OSM way 104"),
        ("1:999.11", "step 1 of this hike: OSM way 999 is no longer on the marked network"),
        ("1:104.13", "step 1 of this hike: OSM node 13 is no longer on OSM way 104"),
        # Way 104 runs from A to D: walked against its node order it does not leave A.
        ("1:-104.11", "step 1 of this hike: OSM way 104 no longer leads from OSM node 1 to OSM node 11"),
        ("1:104.11,104.11", "step 2 of this hike: OSM way 104 no longer leads from OSM node 11 to OSM node 11"),
        ("1:g.4", "step 1 of this hike: the gap from OSM node 1 to OSM node 4 is no longer"),
        ("c460000000_140000000:l.1", "the connect-here link from the trail crossing at 46.0000000, 14.0000000"),
    ],
)
def test_a_walk_refuses_what_is_not_one_walk(catalog_path: Path, route: str, message: str) -> None:
    with pytest.raises(RouteError, match=re.escape(message)):
        TrailGraph(trailgraph_path_for(catalog_path)).walk(Route.parse(route))


@pytest.mark.parametrize("text", ["", "1:x.2", "1:104", "1:104.11,", "a:104.11"])
def test_a_route_that_is_not_one_is_refused_as_text(text: str) -> None:
    with pytest.raises(RouteError, match="not a composed hike"):
        Route.parse(text)


def test_a_route_names_segments_and_crossings_by_osm_ids(catalog_path: Path) -> None:
    segments = _segments(catalog_path)
    route = _route(catalog_path, A, (_segment(segments, A, D)["id"], _segment(segments, D, P)["id"]))
    crossing = sqlite3.connect(trailgraph_path_for(catalog_path)).execute(
        "SELECT key, latitude, longitude FROM junctions WHERE id = -1"
    ).fetchone()

    assert str(route) == "1:104.11,105.13"
    assert Route.parse(str(route)) == route
    # Walked down, each Segment leaves on its last way, against the way's node order.
    assert str(_route(catalog_path, P, (_segment(segments, D, P)["id"], _segment(segments, A, D)["id"]))) == (
        "13:-105.11,-104.1"
    )
    assert crossing[0] == f"c{round(crossing[1] * 1e7)}_{round(crossing[2] * 1e7)}"


# ---------- shared links across a rebuild ----------


def _variant(root: Path, *, attach_at: int | None = None, drop_ways: Sequence[int] = ()) -> Path:
    """The fixture with a short trail (way 100, the lowest way id, so every
    Segment is renumbered) leaving node ``attach_at``, or with ways deleted."""
    text = FIXTURE.read_text()
    if attach_at is not None:
        # 566 m south-west of A, or 500 m south-west of Q2: near no other trail.
        lat, lon = (46.0464027, 14.0448168) if attach_at == A else (46.0580939, 14.02538)
        text = text.replace("  <way ", f'  <node id="60" lat="{lat}" lon="{lon}" version="1"/>\n  <way ', 1)
        text = text.replace(
            "  <relation ",
            f'  <way id="100" version="1"><nd ref="{attach_at}"/><nd ref="60"/><tag k="highway" v="path"/></way>\n'
            "  <relation ",
            1,
        )
        text = text.replace(
            "</osm>",
            '  <relation id="2000" version="1"><member type="way" ref="100" role=""/><tag k="type" v="route"/>'
            '<tag k="route" v="hiking"/><tag k="name" v="Short Way"/></relation>\n</osm>',
        )
    for way in drop_ways:
        text = re.sub(rf'  <way id="{way}" .*?</way>\n', "", text, flags=re.S)
        text = text.replace(f'<member type="way" ref="{way}" role=""/>', "")
    path = root / "variant.osm"
    path.write_text(text)
    return path


SUMMIT_LINK = "1:104.11,105.13"  # A to D on way 104, D to P on way 105


def _same_walk(after, before) -> None:
    assert after.lat.tolist() == before.lat.tolist() and after.lon.tolist() == before.lon.tolist()
    assert after.ele.tolist() == before.ele.tolist()
    assert [surface for _, surface in after.walked] == [surface for _, surface in before.walked]
    assert [metres for metres, _ in after.walked] == pytest.approx([metres for metres, _ in before.walked])
    assert after.relation_m == pytest.approx(before.relation_m) and after.sac_scale == before.sac_scale


@pytest.mark.parametrize(
    ("change", "link", "segments_after"),
    [
        # A trail added at A: every Segment is renumbered.
        ({"attach_at": A}, SUMMIT_LINK, 2),
        # A trail added at Q2, inside the Segment D-P: the walk crosses the new Junction.
        ({"attach_at": 12}, SUMMIT_LINK, 3),
        # The Spur gone: D is no longer a Junction, and A-D and D-P are one Segment.
        ({"drop_ways": [106]}, SUMMIT_LINK, 1),
        # ... and a walk that starts at D, now inside that Segment, still starts there.
        ({"drop_ways": [106]}, "11:105.13", None),
    ],
)
def test_a_shared_link_opens_the_same_walk_after_a_rebuild(
    catalog_path: Path, tmp_path: Path, change: dict, link: str, segments_after: int | None
) -> None:
    rebuilt = tmp_path / "catalog.sqlite"
    build_catalog(_variant(tmp_path, **change), rebuilt)

    before = TrailGraph(trailgraph_path_for(catalog_path)).walk(Route.parse(link))
    after = TrailGraph(trailgraph_path_for(rebuilt)).walk(Route.parse(link))

    _same_walk(after, before)
    if segments_after is None:  # it cuts a Segment: the page cannot go on drawing from it
        assert after.segments is None and after.start is None
    else:
        assert len(after.segments) == segments_after and after.segments != before.segments


def test_a_shared_link_whose_way_was_deleted_says_so(catalog_path: Path, tmp_path: Path) -> None:
    rebuilt = tmp_path / "catalog.sqlite"
    build_catalog(_variant(tmp_path, drop_ways=[105]), rebuilt)

    with pytest.raises(RouteError, match=re.escape("step 2 of this hike: OSM way 105 is no longer on the marked network")):
        TrailGraph(trailgraph_path_for(rebuilt)).walk(Route.parse(SUMMIT_LINK))


# ---------- composing a DayHike's own Segments gives its facts ----------


def _write_rasters(root: Path) -> tuple[Path, Path, Path]:
    rows, cols = np.mgrid[0:100, 0:200]
    dem = root / "dem"
    dem.mkdir()
    # Rising northward and eastward, with a bump: every Measure on elevation has work to do.
    height = 500.0 + 6.0 * (99 - rows) + 1.5 * cols + 80.0 * np.sin(cols / 9.0)
    _write_tif(dem / "N46E014_FABDEM_V1-2.tif", height.astype("float32"), nodata=-9999.0, **BOX)
    landcover = root / "landcover"
    landcover.mkdir()
    rows, cols = np.mgrid[0:600, 0:1200]
    lon = BOX["west"] + (cols + 0.5) * 0.1 / 1200
    lat = BOX["north"] - (rows + 0.5) * 0.05 / 600
    classes = np.where(lon > 14.0645, 80, np.where(lat < 46.055, 10, np.where(lon < 14.03, 60, 30)))
    _write_tif(
        landcover / "ESA_WorldCover_10m_2021_v200_N45E012_Map.tif", classes.astype("uint8"), nodata=0, **BOX
    )
    climate = root / "climate"
    climate.mkdir()
    rows, cols = np.mgrid[0:8, 0:8]
    for month in range(1, 13):
        for variable, base in (("tas", month - 3.0), ("pr", 40.0 + 9 * month), ("rsds", 5_000.0 + 900 * month)):
            values = base + 0.37 * rows + 0.11 * cols
            _write_tif(climate / f"chelsa_{variable}_{month:02d}.tif", values.astype("float32"), **BOX)
    return dem, landcover, climate


@pytest.fixture(scope="module")
def sampled(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("sampled")
    dem, landcover, climate = _write_rasters(root)
    path = root / "catalog.sqlite"
    build_catalog(FIXTURE, path)
    assert sample_dem(path, dem) > 0
    assert sample_landcover(path, landcover) > 0
    assert sample_climate(path, climate) > 0
    build_similarity(path, dem, landcover_dir=landcover)
    assert sample_trailgraph(path, dem_dir=dem, landcover_dir=landcover, climate_dir=climate) > 0
    return path


def _route_of(path: Path, dayhike_id: int) -> Route | None:
    """The Segments a DayHike's walk follows, or None where it leaves the TrailGraph's vertices."""
    walk = [
        node
        for (node,) in sqlite3.connect(geometry_store_path_for(path)).execute(
            "SELECT node_id FROM vertices WHERE dayhike_id = ? ORDER BY seq", (dayhike_id,)
        )
    ]
    starting: dict[tuple[int, int], tuple[int, list[int]]] = {}
    for segment in _segments(path):
        if segment["kind"] == "link":
            continue
        nodes = np.frombuffer(segment["node_ids"], dtype="<i8").tolist()
        starting[(nodes[0], nodes[1])] = (segment["id"], nodes)
        starting[(nodes[-1], nodes[-2])] = (segment["id"], nodes[::-1])
    route, at = [], 0
    while at < len(walk) - 1:
        found = starting.get((walk[at], walk[at + 1]))
        if found is None or walk[at : at + len(found[1])] != found[1]:
            return None
        route.append(found[0])
        at += len(found[1]) - 1
    return _route(path, walk[0], route)


def test_composing_a_dayhikes_own_segments_reproduces_its_facts(sampled: Path) -> None:
    store = SimilarityStore.open(sampled)
    graph = TrailGraph(trailgraph_path_for(sampled))
    routes = {int(i): _route_of(sampled, int(i)) for i in store.ids}

    # The Cross Trail's walk has no node where the TrailGraph joins the crossing.
    assert {i for i, route in routes.items() if route is None} == {CROSS_TRAIL}
    checked = set()
    for row, dayhike_id in enumerate(store.ids.tolist()):
        if routes[dayhike_id] is None:
            continue
        facts, shape = graph.facts(graph.walk(routes[dayhike_id]))
        for index, column in enumerate(FACT_COLUMNS):
            expected = float(store.facts[index, row])
            if math.isnan(expected):
                assert math.isnan(facts[column]), (dayhike_id, column)
            else:
                assert facts[column] == pytest.approx(expected, rel=1e-5, abs=1e-5), (dayhike_id, column)
        assert shape == pytest.approx(store.profile[row], abs=1e-5)
        checked.add(dayhike_id)
    assert checked == {LAKE_LOOP, SUMMIT_LINE, CONNECTOR, BRIDGE_PATH, MEADOW_PATH}
    # The rasters reached every kind of fact.
    loop = list(store.ids).index(LAKE_LOOP)
    for column in ("landcover", "temperature_c_07", "southness", "waterside_share", "highest_m"):
        name = "tree_share" if column == "landcover" else column
        assert not math.isnan(store.facts[FACT_COLUMNS.index(name), loop]), column
    assert 0 < store.facts[FACT_COLUMNS.index("waterside_share"), loop] < 1


def test_sampling_again_changes_nothing(sampled: Path, tmp_path: Path) -> None:
    before = _segments(sampled)
    root = tmp_path / "again"
    root.mkdir()
    dem, landcover, climate = _write_rasters(root)

    sample_trailgraph(sampled, dem_dir=dem, landcover_dir=landcover, climate_dir=climate)

    assert _segments(sampled) == before


def test_sampling_without_rasters_keeps_the_osm_heights(catalog_path: Path, tmp_path: Path) -> None:
    sample_trailgraph(catalog_path, dem_dir=tmp_path, landcover_dir=tmp_path, climate_dir=tmp_path)

    up = _segment(_segments(catalog_path), D, P)
    assert np.frombuffer(up["ele"], dtype="<f4").tolist() == [900, 1250, 1640]
    assert up["landcover"] is None or set(np.frombuffer(up["landcover"], dtype="u1")) == {0}


# ---------- serving, the API and the CLI ----------


@pytest.fixture(scope="module")
def data(sampled: Path):
    from dayhike.serving import ServingData, export_serving

    return ServingData(export_serving(sampled, sampled.parent / "web-serving"))


@pytest.fixture(scope="module")
def summit(sampled: Path) -> Route:
    return _route_of(sampled, SUMMIT_LINE)


def test_the_export_keeps_the_trailgraph_with_display_lines(data, sampled: Path) -> None:
    from dayhike.serving import decode_polyline

    db = sqlite3.connect(data.trailgraph.path)
    columns = {row[1] for row in db.execute("PRAGMA table_info(segments)")}
    assert {"node_ids", "line", "climb_ab", "climb_ba"} <= columns
    found, too_many = data.segments_in_box(46.04, 14.0, 46.07, 14.1, 100)
    assert too_many is False
    assert len(found["segments"]) == len(_segments(sampled))
    up = next(s for s in found["segments"] if {s[2], s[3]} == {D, P})
    assert up[1] == "trail" and up[8] == "demanding_mountain_hiking" and up[9] == ["Summit Line"]
    line = decode_polyline(up[7])
    assert len(line) == 2  # D, P: the vertex between is on the straight line
    to_peak, back = (up[5], up[6]) if up[3] == P else (up[6], up[5])
    # The climb each way, in the sampled FABDEM heights.
    up_route, down_route = _route(sampled, D, (up[0],)), _route(sampled, P, (up[0],))
    assert (to_peak, back) == (data.compose(up_route)["climb_m"], data.compose(down_route)["climb_m"])
    assert (to_peak, back) != (0, 0)
    # How the page names it in a shared link, walked from a and from b.
    assert {up[10], up[11]} == {up_route.steps[0], down_route.steps[0]} == {"105.13", "-105.11"}
    assert {j[0] for j in found["junctions"]} >= {A, D, P, F, Z2, -1}
    assert [j[4] for j in found["junctions"] if j[0] == D] == ["11"]
    assert data.segments_in_box(46.04, 14.0, 46.07, 14.1, 3) == ({"junctions": [], "segments": []}, True)


def test_a_composed_hike_has_the_detail_shape_of_a_dayhike(data, summit: Route) -> None:
    composed, catalog = data.compose(summit), data.detail(SUMMIT_LINE)

    assert set(catalog) <= set(composed)
    assert composed["id"] is None
    assert composed["route"] == {
        "text": "1:104.11,105.13",
        "start": A,
        "segments": composed["route"]["segments"],
        "steps": ["104.11", "105.13"],
    }
    assert len(composed["route"]["segments"]) == 2
    assert composed["routes"] == ["Summit Line"]
    for key in ("walk_m", "climb_m", "descent_m", "line", "elevation", "trailhead", "sac_scale"):
        assert composed[key] == pytest.approx(catalog[key]), key
    assert composed["points"] == catalog["points"]
    assert composed["facts"] == pytest.approx(catalog["facts"], rel=1e-5, abs=1e-5)


def test_a_ranking_from_a_composed_hike_hides_the_dayhikes_it_retraces(data, sampled: Path, summit: Route) -> None:
    from dayhike.similarity import DEFAULT_WEIGHTS

    composed = data.rank(summit, DEFAULT_WEIGHTS)
    catalog = data.rank(SUMMIT_LINE, DEFAULT_WEIGHTS)

    assert SUMMIT_LINE not in [r["id"] for r in composed]
    assert [r["id"] for r in composed] == [r["id"] for r in catalog]
    assert [r["score"] for r in composed] == pytest.approx([r["score"] for r in catalog])
    # A longer walk that holds the Summit Line is no Twin of it, so it may list it.
    ids = data.compose(summit)["route"]["segments"]
    longer = _route(sampled, A, (*ids, *ids[::-1]))
    assert SUMMIT_LINE in [r["id"] for r in data.rank(longer, DEFAULT_WEIGHTS)]
    pair = data.pair(summit, LAKE_LOOP, DEFAULT_WEIGHTS)
    assert pair["a"] is None
    assert pair["score"] == pytest.approx(data.pair(SUMMIT_LINE, LAKE_LOOP, DEFAULT_WEIGHTS)["score"])


@pytest.fixture(scope="module")
def client(data):
    from fastapi.testclient import TestClient

    from dayhike.web import create_app

    return TestClient(create_app(data))


def test_a_composed_hike_is_named_by_its_main_trails_and_start_or_by_the_hiker(data, summit: Route) -> None:
    proposed = data.compose(summit)
    named = data.compose(summit, "Grey Peak the steep way")

    # The Lakeside Inn is 45 m from the start, A.
    assert (proposed["name"], proposed["name_origin"]) == ("Summit Line from Lakeside Inn", "proposed")
    assert (named["name"], named["name_origin"]) == ("Grey Peak the steep way", "hiker")
    assert named["facts"] == proposed["facts"]


def test_api_segments_compose_rank_and_pair(client, summit: Route) -> None:
    composed = {"route": str(summit)}

    assert client.get("/api/info").json()["compose"] is True
    box = client.get("/api/segments", params={"south": 46.04, "west": 14.0, "north": 46.07, "east": 14.1}).json()
    assert box["too_many"] is False and box["segments"] and box["junctions"]
    hike = client.post("/api/compose", json=composed).json()
    assert hike["route"]["text"] == str(summit) and hike["facts"]["round_trip"] == 0.0
    assert hike["name"] == "Summit Line from Lakeside Inn"
    named = client.post("/api/compose", json={**composed, "name": "  Up to Grey Peak "}).json()
    assert named["name"] == "Up to Grey Peak"
    ranked = client.post("/api/rank", json={"composed": composed}).json()
    assert ranked["seed"] is None and ranked["composed"] == {"route": str(summit), "name": None}
    assert SUMMIT_LINE not in [r["id"] for r in ranked["results"]]
    pair = client.post("/api/pair", json={"composed": composed, "b": LAKE_LOOP}).json()
    assert pair["a"] is None and pair["b"] == LAKE_LOOP and pair["score"] is not None


@pytest.mark.parametrize(
    ("path", "body", "status"),
    [
        ("/api/compose", {"route": "1:999.11"}, 400),
        ("/api/compose", {"route": "1:"}, 422),
        ("/api/compose", {"route": "1;drop"}, 422),
        ("/api/compose", {"route": "1:" + ",".join(["104.11"] * 2001)}, 422),
        ("/api/compose", {"route": "1:104.11", "name": "x" * 121}, 422),
        ("/api/rank", {"seed": SUMMIT_LINE, "composed": {"route": "1:104.11"}}, 400),
        ("/api/rank", {}, 400),
        ("/api/rank", {"composed": {"route": "999:104.11"}}, 400),
        ("/api/pair", {"composed": {"route": "999:104.11"}, "b": LAKE_LOOP}, 400),
    ],
)
def test_api_refuses_a_bad_composed_hike(client, path: str, body: dict, status: int) -> None:
    assert client.post(path, json=body).status_code == status


def test_an_over_long_back_and_forth_link_is_refused_while_it_is_stitched(client, catalog_path: Path, monkeypatch) -> None:
    import dayhike.trailgraph

    # 45 times up and down way 104, A to D: about 105 km.
    link = "1:" + ",".join(["104.11", "-104.1"] * 45)

    for path, body in (("/api/compose", {"route": link}), ("/api/rank", {"composed": {"route": link}})):
        response = client.post(path, json=body)
        assert response.status_code == 400 and "at most 100 km" in response.json()["detail"]
    monkeypatch.setattr(dayhike.trailgraph, "MAX_WALK_STEPS", 3)
    with pytest.raises(RouteError, match="step 2 of this hike: a composed hike may walk at most 3 vertex steps"):
        TrailGraph(trailgraph_path_for(catalog_path)).walk(Route.parse("1:104.11,-104.1"))


def test_an_export_without_a_trailgraph_does_not_compose(sampled: Path, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from dayhike.serving import ServingData, export_serving
    from dayhike.web import create_app

    for name in ("catalog.sqlite", "catalog.geometry.sqlite"):
        (tmp_path / name).write_bytes((sampled.parent / name).read_bytes())
    build_similarity(tmp_path / "catalog.sqlite", tmp_path / "dem")
    client = TestClient(create_app(ServingData(export_serving(tmp_path / "catalog.sqlite", tmp_path / "out"))))

    assert client.get("/api/info").json()["compose"] is False
    assert client.get("/api/segments", params={"south": 46, "west": 14, "north": 46.1, "east": 14.1}).status_code == 404
    assert client.post("/api/rank", json={"composed": {"route": "1:104.11"}}).status_code == 404
    assert client.post("/api/rank", json={"seed": SUMMIT_LINE}).status_code == 200


def test_the_export_refuses_a_trailgraph_never_sampled_when_the_facts_are(sampled: Path, tmp_path: Path) -> None:
    from dayhike.serving import ServingExportError, export_serving

    catalog = tmp_path / "catalog.sqlite"
    for name in ("catalog.sqlite", "catalog.geometry.sqlite", "catalog.trailgraph.sqlite"):
        shutil.copyfile(sampled.parent / name, tmp_path / name)
    shutil.copytree(similarity_dir_for(sampled), similarity_dir_for(catalog))
    with sqlite3.connect(trailgraph_path_for(catalog)) as db:  # as ingest writes it
        db.execute("UPDATE segments SET landcover = NULL, climate_cell = NULL, southness = NULL")
    db.close()

    with pytest.raises(ServingExportError, match="run `dayhike sample-trailgraph`"):
        export_serving(catalog, tmp_path / "out")
    assert not (tmp_path / "out" / "meta.json").exists()


def test_cli_samples_the_trailgraph_and_composes(sampled: Path, data, summit: Route, tmp_path: Path, capsys) -> None:
    from dayhike.cli import main

    assert main(["sample-trailgraph", "--catalog", str(sampled), "--dem", str(tmp_path), "--landcover",
                 str(tmp_path), "--climate", str(tmp_path)]) == 0  # fmt: skip
    assert capsys.readouterr().out.strip() == str(sum(len(s["coords"]) // 8 for s in _segments(sampled)))
    assert main(["compose", "--data", str(data.directory), "--limit", "2", str(summit)]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0].startswith("Summit Line from Lakeside Inn\t2728 m\t")
    assert len(lines) == 3 and all(str(SUMMIT_LINE) not in line.split("\t")[0] for line in lines[1:])
