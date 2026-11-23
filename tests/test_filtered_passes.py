"""The ways and nodes passes filter in C++ (osmium filters) and give the work
file the rows the per-object Python passes gave (the reference passes below)."""

import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import numpy as np
import osmium
import pytest

import dayhike.ingest as ingest
from dayhike.ingest import (
    PATH_HIGHWAYS,
    PATH_KEYS,
    PATH_LEVELS,
    POINT_TAGS,
    _Pending,
    _connect_work,
    _init_work,
    _is_extra_path,
    _iter_file,
    _pass_nodes,
    _pass_relations,
    _pass_ways,
    _point_kind,
    _resolve_pending_amenities,
    _sorted_has,
    _sorted_ids,
)
from dayhike.osm import _parse_ele_m
from test_pbf_ingest import _write_pbf
from test_streaming_ingest import _negative_ids_fixture

FIXTURES = Path(__file__).parent / "fixtures"
OSM_FILES = sorted(FIXTURES.glob("*.osm"))
WORK_TABLES = (
    "relations",
    "needed_ways",
    "amenity_ways",
    "ways",
    "path_ways",
    "needed_nodes",
    "path_needed_nodes",
    "snapshot_path_ways",
    "snapshot_path_nodes",
    "nodes",
    "path_nodes",
    "pending_amenity_ways",
    "pending_amenity_relations",
    "amenities",
)


def _reference_pass_ways(conn: sqlite3.Connection, paths: list[Path], levels: list[str]) -> None:
    needed_ways = _sorted_ids(conn, "needed_ways")
    amenity_ways = _sorted_ids(conn, "amenity_ways")
    pending = _Pending(conn)
    for path, path_level in zip(paths, levels):
        for obj in _iter_file(path, osmium.osm.WAY):
            osm_id = int(obj.id)
            node_ids = [int(node.ref) for node in obj.nodes]
            tags = dict(obj.tags)
            kind = _point_kind(tags)
            if _sorted_has(needed_ways, osm_id):
                pending.add(
                    "INSERT OR REPLACE INTO ways (id, node_ids, tags) VALUES (?, ?, ?)",
                    osm_id, json.dumps(node_ids), json.dumps(tags),
                )  # fmt: skip
                pending.add_ids("needed_nodes", node_ids)
            elif _is_extra_path(tags, path_level):
                pending.add(
                    "INSERT OR REPLACE INTO path_ways (id, node_ids, tags) VALUES (?, ?, ?)",
                    osm_id, json.dumps(node_ids), json.dumps(tags),
                )  # fmt: skip
                pending.add_ids("path_needed_nodes", node_ids)
                if path_level != "graded":
                    pending.add_ids("snapshot_path_ways", [osm_id])
                    pending.add_ids("snapshot_path_nodes", node_ids)
            if node_ids and (kind is not None or _sorted_has(amenity_ways, osm_id)):
                pending.add(
                    "INSERT OR REPLACE INTO pending_amenity_ways (id, kind, node_id, tags) VALUES (?, ?, ?, ?)",
                    osm_id, kind or "", node_ids[0], json.dumps(tags),
                )  # fmt: skip
                pending.add_ids("needed_nodes", node_ids[:1])
    pending.write()


def _reference_pass_nodes(conn: sqlite3.Connection, paths: list[Path]) -> None:
    needed_nodes = _sorted_ids(conn, "needed_nodes")
    path_nodes = _sorted_ids(conn, "path_needed_nodes")
    pending = _Pending(conn)
    for path in paths:
        for obj in _iter_file(path, osmium.osm.NODE):
            osm_id = int(obj.id)
            location = obj.location
            tags = dict(obj.tags)
            kind = _point_kind(tags)
            needed = _sorted_has(needed_nodes, osm_id)
            on_path = not needed and _sorted_has(path_nodes, osm_id)
            if location.valid() and (needed or on_path or kind is not None):
                point = (float(location.lat), float(location.lon))
                if needed:
                    pending.add(
                        "INSERT OR REPLACE INTO nodes (id, lat, lon, ele) VALUES (?, ?, ?, ?)",
                        osm_id, point[0], point[1], _parse_ele_m(tags.get("ele")),
                    )  # fmt: skip
                elif on_path:
                    pending.add(
                        "INSERT OR REPLACE INTO path_nodes (id, lat, lon, ele) VALUES (?, ?, ?, ?)",
                        osm_id, point[0], point[1], _parse_ele_m(tags.get("ele")),
                    )  # fmt: skip
                if kind is not None:
                    pending.add(
                        "INSERT OR REPLACE INTO amenities (osm_key, kind, lat, lon, tags) VALUES (?, ?, ?, ?, ?)",
                        f"n{osm_id}", kind, point[0], point[1], json.dumps(tags),
                    )  # fmt: skip
    pending.write()
    _resolve_pending_amenities(conn)


def _work(tmp_path: Path, name: str, paths: list[Path]) -> sqlite3.Connection:
    conn = _connect_work(tmp_path / f"{name}.work")
    _init_work(conn, "[]", paths, paths)
    return conn


def _tables(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    # rowid order: the amenities table is read in the order its rows were written.
    return {table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in WORK_TABLES}


def _pbfs(tmp_path: Path, osm_files: list[Path]) -> list[Path]:
    return [_write_pbf(osm, tmp_path / f"{osm.stem}.osm.pbf") for osm in osm_files]


def _assert_same_work_file(tmp_path: Path, pbfs: list[Path], levels: list[str]) -> dict[str, list[tuple]]:
    with closing(_work(tmp_path, "reference", pbfs)) as reference, closing(_work(tmp_path, "filtered", pbfs)) as filtered:
        for conn in (reference, filtered):
            _pass_relations(conn, pbfs, None)
        _reference_pass_ways(reference, pbfs, levels)
        _reference_pass_nodes(reference, pbfs)
        _pass_ways(filtered, pbfs, None, levels)
        _pass_nodes(filtered, pbfs, None)
        expected, got = _tables(reference), _tables(filtered)
    assert got == expected
    return got


@pytest.mark.parametrize("level", PATH_LEVELS)
@pytest.mark.parametrize("osm", OSM_FILES, ids=lambda path: path.stem)
def test_filtered_passes_write_the_rows_of_the_reference_passes(tmp_path: Path, osm: Path, level: str) -> None:
    tables = _assert_same_work_file(tmp_path, _pbfs(tmp_path, [osm]), [level])

    assert tables["nodes"] or tables["amenities"] or osm.stem == "gap_steps", "a fixture the passes read nothing of"


def test_filtered_passes_of_two_files_with_their_own_levels(tmp_path: Path) -> None:
    pbfs = _pbfs(tmp_path, [FIXTURES / "named_paths.osm", FIXTURES / "way_and_relation_amenities.osm"])

    tables = _assert_same_work_file(tmp_path, pbfs, ["unnamed", "graded"])

    assert tables["path_ways"] and tables["pending_amenity_ways"]


def test_point_tags_cover_every_node_that_is_a_point() -> None:
    # The nodes TagFilter keeps only nodes with one of POINT_TAGS: a point without one would be lost.
    probes = {
        "tourism": ("alpine_hut", "wilderness_hut", "viewpoint"),
        "amenity": ("parking", "restaurant", "cafe", "drinking_water", "shelter", "water_point"),
        "natural": ("peak", "spring", "water"),
        "water": ("lake", "reservoir", "pond"),
        "man_made": ("water_tap",),
        "place": ("city", "town", "village", "hamlet"),
        "information": ("guidepost",),
        "highway": ("trailhead",),
        "drinking_water": ("no", "yes"),
        "name": ("Somewhere",),
    }
    pairs = [(key, value) for key, values in probes.items() for value in values]
    for first in pairs:
        for second in pairs:
            tags = dict([first, second, ("name", "Somewhere")])
            if _point_kind(tags) is not None:
                assert set(tags.items()) & set(POINT_TAGS), tags


def test_way_filters_cover_every_way_that_is_a_point_or_an_extra_path() -> None:
    # The ways scans keep a way with one of POINT_TAGS (a point), or a PATH_KEYS key and a PATH_HIGHWAYS highway (a path).
    values = {
        "highway": ("path", "footway", "bridleway", "track", "trailhead", "service", "residential"),
        "sac_scale": ("hiking", "alpine_hiking", "none"),
        "footway": ("sidewalk", "crossing", "none"),
        "name": ("Somewhere",),
        "tourism": ("viewpoint",),
        "natural": ("peak", "water", "wood"),
        "water": ("lake",),
        "amenity": ("parking", "school"),
        "man_made": ("water_tap",),
        "place": ("hamlet",),
        "information": ("guidepost",),
    }
    pairs = [(key, value) for key, options in values.items() for value in options]
    for first in pairs:
        for second in pairs:
            for third in pairs:
                tags = dict([first, second, third])
                if _point_kind(tags) is not None:
                    assert set(tags.items()) & set(POINT_TAGS), tags
                for level in PATH_LEVELS:
                    if _is_extra_path(tags, level):
                        assert set(tags) & set(PATH_KEYS[level]), (level, tags)
                        assert tags.get("highway") in PATH_HIGHWAYS[level], (level, tags)


def test_id_filter_takes_every_id_of_arrays_longer_than_a_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ingest, "ID_FILTER_CHUNK", 3)
    first = np.array([1, 5, 9, 12, 20, 21, 22], dtype=np.int64)
    second = np.array([2, 3, 30], dtype=np.int64)

    ids = list(ingest._chunked_ids(first, second))

    assert sorted(ids) == sorted([1, 5, 9, 12, 20, 21, 22, 2, 3, 30])
    assert all(type(osm_id) is int for osm_id in ids)


def test_a_pass_resumed_inside_its_second_scan_keeps_the_first_scans_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Killed inside the node pass, whichever of its two scans the kill falls in, a rerun gives the reference rows.
    pbfs = _pbfs(tmp_path, [FIXTURES / "catalog.osm"])
    monkeypatch.setattr(ingest, "INGEST_CHECKPOINT_OBJECTS", 1)
    monkeypatch.setattr(ingest, "INGEST_PROGRESS_EVERY_OBJECTS", 2)
    with closing(_work(tmp_path, "reference", pbfs)) as reference:
        _pass_relations(reference, pbfs, None)
        _reference_pass_ways(reference, pbfs, ["graded"])
        _reference_pass_nodes(reference, pbfs)
        expected = _tables(reference)
    killed_in: list[str] = []

    for kill_at in range(1, 8):
        work = tmp_path / f"kill{kill_at}.work"
        conn = _connect_work(work)
        _init_work(conn, "[]", pbfs, pbfs)
        _pass_relations(conn, pbfs, None)
        _pass_ways(conn, pbfs, None, ["graded"])
        lines = 0

        def kill(message: str) -> None:
            nonlocal lines
            if message.startswith("nodes:") and "scanned" in message:
                lines += 1
                if lines == kill_at:
                    killed_in.append(message)
                    raise RuntimeError("killed")

        try:
            _pass_nodes(conn, pbfs, kill)
        except RuntimeError:
            pass
        _pass_nodes(conn, pbfs, None)
        assert _tables(conn) == expected, kill_at
        conn.close()
    assert len(killed_in) >= 2


def test_the_passes_handle_only_the_objects_their_filters_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A file of many objects of which few are wanted: the C++ filters skip the rest before Python sees them.
    osm = tmp_path / "many.osm"
    lines = ['<?xml version="1.0"?>', '<osm version="0.6">']
    for node_id in range(1, 60_001):
        lines.append(f'<node id="{node_id}" lat="46.{node_id % 1000:03d}" lon="14.{node_id % 997:03d}" version="1"/>')
    lines.append('<node id="60001" lat="46.1" lon="14.1" version="1"><tag k="natural" v="peak"/></node>')
    lines.append('<node id="60002" lat="46.2" lon="14.2" version="1"><tag k="highway" v="crossing"/></node>')
    lines.append('<way id="1" version="1"><nd ref="5"/><nd ref="6"/><tag k="highway" v="path"/><tag k="sac_scale" v="hiking"/></way>')
    lines.append('<way id="2" version="1"><nd ref="7"/><nd ref="8"/><tag k="natural" v="wood"/></way>')
    lines.append('<way id="3" version="1"><nd ref="9"/><nd ref="10"/><tag k="highway" v="residential"/><tag k="name" v="Main"/></way>')
    lines.append("</osm>")
    osm.write_text("\n".join(lines))
    pbf = _write_pbf(osm, tmp_path / "many.osm.pbf")
    handled: dict[str, list[int]] = {}
    scan = ingest._scan

    def counting_scan(conn, pass_name, paths, entities, filters_for, handle, *rest):  # type: ignore[no-untyped-def]
        def counting_handle(obj, osm_id, pending, index):  # type: ignore[no-untyped-def]
            handled.setdefault(pass_name, []).append(osm_id)
            handle(obj, osm_id, pending, index)

        scan(conn, pass_name, paths, entities, filters_for, counting_handle, *rest)

    with closing(_work(tmp_path, "reference", [pbf])) as reference, closing(_work(tmp_path, "filtered", [pbf])) as filtered:
        for conn in (reference, filtered):
            _pass_relations(conn, [pbf], None)
        monkeypatch.setattr(ingest, "_scan", counting_scan)
        _reference_pass_ways(reference, [pbf], ["trails"])
        _pass_ways(filtered, [pbf], None, ["trails"])
        _reference_pass_nodes(reference, [pbf])
        _pass_nodes(filtered, [pbf], None)
        assert _tables(filtered) == _tables(reference)
        assert filtered.execute("SELECT count(*) FROM path_nodes").fetchone() == (2,)
        assert filtered.execute("SELECT count(*) FROM amenities").fetchone() == (1,)
    assert handled == {"ways_paths": [1], "nodes_points": [60001], "nodes": [5, 6]}


@pytest.mark.skipif(shutil.which("osmium") is None, reason="needs osmium-tool")
def test_filtered_passes_of_a_file_of_negative_ids(tmp_path: Path) -> None:
    # IdFilter takes positive ids only: a JOSM file of new objects is read unfiltered by id, to the same rows.
    josm = _negative_ids_fixture(tmp_path)

    tables = _assert_same_work_file(tmp_path, [josm], ["graded"])

    assert tables["nodes"] and tables["ways"] and tables["relations"]
