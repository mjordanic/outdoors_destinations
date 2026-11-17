"""DestinationHikes through ingest (#124, ADR-0067): the source reads its own
walkable graph from the OSM file, so a walk may take a track no hiking relation
lists, and the Catalog, the GeometryStore and the TrailGraph all hold it.

The fixture: Koča na Planini, a hut 4 km up a marked path from the car park in
Dolina and 5 km up an unmarked track from a car park area with no name beside
Dom v Severu, a hut in the next valley north.
"""

import math
import shutil
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from dayhike import build_catalog
from dayhike.built import BUILT_ID_MIN
from dayhike.catalog import geometry_store_path_for, trailgraph_path_for
from dayhike.destination_hikes import destination_source
from dayhike.similarity import _step_m
from dayhike.trailgraph import Route, TrailGraph
from dem_tiles import write_dem_tile

LAT, LON = 46.3, 13.8
KM_LAT = 1 / 111.195
KM_LON = KM_LAT / math.cos(math.radians(LAT))
HUT_KM = 4.0
TRACK = 1001  # the unmarked track from Sever


def _at(north_km: float, east_km: float) -> tuple[float, float]:
    return (round(LAT + north_km * KM_LAT, 7), round(LON + east_km * KM_LON, 7))


def _fixture(path: Path, lake: bool = False) -> Path:
    nodes: dict[int, tuple[tuple[float, float], dict[str, str]]] = {}
    ways: list[tuple[int, list[int], dict[str, str]]] = []

    def line(first: int, a: tuple[float, float], b: tuple[float, float], last: int | None = None) -> list[int]:
        steps = round(math.dist(a, b) * 4)
        ids = []
        for i in range(steps + 1):
            node = last if i == steps and last is not None else first + i
            nodes.setdefault(node, (_at(a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps), {}))
            ids.append(node)
        return ids

    path_ids = line(200, (0, 0), (HUT_KM, 0))
    ways.append((1000, path_ids, {"highway": "path", "name": "Pot na Planino"}))
    ways.append((TRACK, line(300, (9, 0), (HUT_KM, 0), last=path_ids[-1]), {"highway": "track"}))
    ways.append((1002, line(500, (HUT_KM, 0), (HUT_KM, 13))[::-1][:-1] + [path_ids[-1]], {"highway": "path"}))
    nodes[1] = (_at(HUT_KM, 0.02), {"tourism": "alpine_hut", "name": "Koča na Planini", "ele": "1800"})
    nodes[2] = (_at(-0.03, 0), {"amenity": "parking", "name": "Dolina"})
    nodes[3] = (_at(9.5, 0.03), {"tourism": "alpine_hut", "name": "Dom v Severu"})  # names the car park beside it
    corners = [400, 401, 402, 403]
    for node, (north, east) in zip(corners, [(9.0, 0.03), (9.05, 0.03), (9.05, 0.08), (9.0, 0.08)]):
        nodes[node] = (_at(north, east), {})
    ways.append((2001, [*corners, corners[0]], {"amenity": "parking"}))
    relations = [  # the path is a piece of a marked tour, 17 km on past the hut: no copy of the walk
        (5000, [1000, 1002], {"type": "route", "route": "hiking", "name": "Dolina – Planina – Vzhod"}),
    ]
    if lake:  # Jezero, 70 m off the path 5 km east of the hut, a multipolygon whose outline has no tags
        outline = [600, 601, 602, 603]
        for node, (north, east) in zip(outline, [(4.05, 4.95), (4.2, 4.95), (4.2, 5.05), (4.05, 5.05)]):
            nodes[node] = (_at(HUT_KM + north - 4, east), {})
        ways.append((3001, [*outline, outline[0]], {}))
        relations.append((6000, [3001], {"type": "multipolygon", "natural": "water", "water": "lake", "name": "Jezero"}))
    return _write_osm(path, nodes, ways, relations)


def _write_osm(path: Path, nodes, ways, relations=()) -> Path:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<osm version="0.6" generator="test">']
    for node, ((lat, lon), tags) in sorted(nodes.items()):
        tag_xml = "".join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items())
        lines.append(f'<node id="{node}" version="1" lat="{lat}" lon="{lon}">{tag_xml}</node>')
    for way, refs, tags in ways:
        body = "".join(f'<nd ref="{ref}"/>' for ref in refs) + "".join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items())
        lines.append(f'<way id="{way}" version="1">{body}</way>')
    for relation, members, tags in relations:
        body = "".join(f'<member type="way" ref="{ref}" role="outer"/>' for ref in members)
        body += "".join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items())
        lines.append(f'<relation id="{relation}" version="1">{body}</relation>')
    lines.append("</osm>")
    path.write_text("\n".join(lines))
    return path


def _dem(directory: Path) -> Path:
    hut = _at(HUT_KM, 0)[0]
    write_dem_tile(directory, LAT, LON, lambda lat: 1_800 - np.abs(lat - hut) / KM_LAT * 120)  # 120 m a km below the hut
    return directory


def _pbf(xml: Path) -> Path:
    import osmium

    pbf = xml.with_suffix(".osm.pbf")
    with osmium.SimpleWriter(str(pbf)) as writer:
        for obj in osmium.FileProcessor(str(xml)):
            writer.add(obj)
    return pbf


def _destination_hikes(catalog):
    return sorted((hike for hike in catalog.dayhikes if hike.kind == "destination"), key=lambda hike: hike.name)


def test_a_hut_reachable_from_two_valleys_gets_a_walk_from_each(tmp_path: Path) -> None:
    osm = _fixture(tmp_path / "planina.osm")
    path = tmp_path / "catalog.sqlite"

    catalog = build_catalog(osm, path, sources=[destination_source], dem_dir=_dem(tmp_path / "dem"))

    dolina, sever = _destination_hikes(catalog)
    assert (dolina.name, sever.name) == ("Koča na Planini from Dolina", "Koča na Planini from Dom v Severu")
    assert {dolina.name_origin, sever.name_origin} == {"destination"}
    assert dolina.parts == () and sever.parts == ()
    assert BUILT_ID_MIN <= min(dolina.id, sever.id) and max(dolina.id, sever.id) < 2**53
    assert abs(dolina.length_m - 4_000) < 50 and abs(sever.length_m - 5_000) < 50
    with sqlite3.connect(geometry_store_path_for(path)) as geo:
        ways = {row[0] for row in geo.execute("SELECT way_id FROM ways WHERE dayhike_id = ?", (sever.id,))}
    assert ways == {TRACK}
    graph = TrailGraph(trailgraph_path_for(path))
    for hike in (dolina, sever):  # "Edit on the map" walks its Route on the TrailGraph
        walked = graph.walk(Route.parse(hike.route))
        assert abs(_step_m(walked.lat, walked.lon).sum() - hike.length_m) < 50


def test_a_destination_hike_keeps_its_id_across_rebuilds_from_xml_or_pbf(tmp_path: Path) -> None:
    osm = _fixture(tmp_path / "planina.osm")
    if shutil.which("osmium") is None:
        pytest.skip("osmium-tool is not on PATH")

    first = build_catalog(osm, tmp_path / "first.sqlite", sources=[destination_source])
    again = build_catalog(_pbf(osm), tmp_path / "again.sqlite", sources=[destination_source])

    assert [hike.id for hike in _destination_hikes(first)] == [hike.id for hike in _destination_hikes(again)]
    assert len(_destination_hikes(first)) == 2


def test_the_export_serves_a_destination_hike_as_built_from_no_relation(tmp_path: Path) -> None:
    from dayhike import build_similarity
    from dayhike.serving import ServingData, export_serving

    path = tmp_path / "catalog.sqlite"
    catalog = build_catalog(_fixture(tmp_path / "planina.osm"), path, sources=[destination_source])
    build_similarity(path, tmp_path / "dem")
    data = ServingData(export_serving(path, tmp_path / "web-serving"))

    for hike in _destination_hikes(catalog):
        assert data.detail(hike.id)["built"] == {"kind": "destination", "parts": [], "route": hike.route}


def test_a_lake_mapped_as_a_multipolygon_of_untagged_ways_is_a_destination(tmp_path: Path) -> None:
    osm = _fixture(tmp_path / "planina.osm", lake=True)

    catalog = build_catalog(osm, tmp_path / "catalog.sqlite", sources=[destination_source])

    assert "Jezero from Dom v Severu" in {hike.name for hike in _destination_hikes(catalog)}


@pytest.mark.parametrize("as_way", [False, True])
def test_a_closed_hut_far_from_what_the_catalog_reads_gets_a_walk(tmp_path: Path, as_way: bool) -> None:
    # A disused hut the catalog does not read, as a node or drawn as a way after the path,
    # and a car park with no name beside it: for a walk to the hut the car park is named
    # after the guidepost by it, not the hut. Nothing else here is a destination or a hut.
    hut = {"disused:tourism": "alpine_hut", "name": "Stara koča"}
    nodes = {
        2: (_at(0, 0), {"amenity": "parking"}),
        3: (_at(-0.2, 0), {"information": "guidepost", "name": "Razpotje"}),
    }
    corners = [_at(0, 0), _at(0, 0.6), _at(0.6, 0.6), _at(0.6, 0)]
    ids = []
    for leg, (a, b) in enumerate(zip(corners, corners[1:])):
        for i in range(0 if leg == 0 else 1, 7):
            ids.append(10 + len(ids))
            nodes[ids[-1]] = ((a[0] + (b[0] - a[0]) * i / 6, a[1] + (b[1] - a[1]) * i / 6), {})
    ways = [(1000, ids, {"highway": "path"})]
    if as_way:
        outline = [50, 51, 52, 53]
        for node, (north, east) in zip(outline, [(0.6, -0.03), (0.62, -0.03), (0.62, -0.01), (0.6, -0.01)]):
            nodes[node] = (_at(north, east), {})
        ways.append((2000, [*outline, outline[0]], {"building": "hut", **hut}))
    else:
        nodes[1] = (_at(0.6, 0), hut)
    osm = _write_osm(tmp_path / "stara.osm", nodes, ways)

    catalog = build_catalog(osm, tmp_path / "catalog.sqlite", sources=[destination_source])

    assert [hike.name for hike in _destination_hikes(catalog)] == ["Stara koča from Razpotje"]


def test_destinations_either_side_of_a_tile_edge_are_merged(tmp_path: Path) -> None:
    # A hut at the end of a path 50 m south of 46 N and a viewpoint with a Wikidata tag
    # 110 m north of it, past the path's end, in the next 1-degree tile: one destination,
    # the viewpoint, reached at the hut.
    nodes = {
        1: ((45.99955, LON), {"tourism": "alpine_hut", "name": "Koča"}),
        2: ((45.985, LON), {"amenity": "parking", "name": "Dolina"}),
        3: ((46.00055, LON), {"tourism": "viewpoint", "name": "Razgled", "wikidata": "Q1"}),
    }
    ids = []
    for i in range(9):
        ids.append(10 + i)
        nodes[ids[-1]] = ((round(45.985 + (45.99955 - 45.985) * i / 8, 7), LON), {})
    osm = _write_osm(tmp_path / "edge.osm", nodes, [(1000, ids, {"highway": "path"})])

    catalog = build_catalog(osm, tmp_path / "catalog.sqlite", sources=[destination_source])

    assert [hike.name for hike in _destination_hikes(catalog)] == ["Razgled from Dolina"]
