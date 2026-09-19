from __future__ import annotations

import json
import math
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np
import osmium
from sklearn.neighbors import BallTree

from dayhike.catalog import (
    EARTH_RADIUS_M,
    NAME_ORIGIN_OSM_NAME,
    NAME_ORIGIN_OSM_REF,
    NAME_ORIGIN_SYNTHETIC,
    TRAILHEAD_ACCESS_RADIUS_M,
    DayHike,
    IngestReport,
    _access_tree,
    _amenity_index,
    _dayhike_from_relation,
    _drop_reason,
    _linestring_length_m,
    _relation_vertices,
)
from dayhike.osm import (
    DRIVEABLE_HIGHWAY,
    OsmRelation,
    OsmSnapshot,
    OsmWay,
    _MEMBER_TYPE,
    _parse_ele_m,
    amenity_kind,
)

INGEST_WORK_SCHEMA = 2
INGEST_CHECKPOINT_OBJECTS = 50_000
INGEST_PROGRESS_EVERY_OBJECTS = 100_000
CATALOG_TAG_FILTERS = (
    "r/route=hiking",
    "nwr/tourism=alpine_hut,wilderness_hut,viewpoint",
    "nwr/amenity=parking,restaurant,cafe,drinking_water,shelter",
    "nwr/natural=peak",
)

Progress = Callable[[str], None]


def work_path_for(catalog_path: Path) -> Path:
    return catalog_path.with_name(catalog_path.name + ".work")


def cleanup_access_sidecars(catalog_path: Path) -> None:
    for path in catalog_path.parent.glob(catalog_path.name + ".access*"):
        path.unlink()


def emit_progress(progress: Progress | None, stage: str, detail: str = "") -> None:
    if progress is None:
        return
    progress(f"{stage}: {detail}".rstrip() if detail else f"{stage}:")


def run_ingest(
    source_paths: Sequence[Path],
    catalog_path: Path,
    progress: Progress | None = None,
    fresh: bool = False,
) -> tuple[list[DayHike], IngestReport, Path]:
    catalog_path = Path(catalog_path)
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    work_path = work_path_for(catalog_path)
    fingerprint = _fingerprint(source_paths)
    if fresh and work_path.exists():
        work_path.unlink()
    resume = _work_is_resumable(work_path, fingerprint)
    if work_path.exists() and not resume:
        work_path.unlink()

    emit_progress(progress, "filter", "starting")
    scan_paths = _scan_paths(source_paths, progress) if not resume else _stored_scan_paths(
        work_path, source_paths
    )

    conn = sqlite3.connect(work_path)
    try:
        if not resume:
            _init_work(conn, fingerprint, source_paths, scan_paths)
        _pass_relations(conn, scan_paths, progress)
        _pass_ways(conn, scan_paths, progress)
        _pass_nodes(conn, scan_paths, progress)
        emit_progress(progress, "access", "loading work file")
        snapshot = _load_hiking_snapshot(conn, progress)
        emit_progress(
            progress,
            "access",
            "loaded "
            f"{len(snapshot.relations)} relations, "
            f"{len(snapshot.ways)} ways, "
            f"{len(snapshot.nodes)} nodes",
        )
        emit_progress(progress, "access", "choosing endpoints")
        endpoints = _kept_endpoints(
            snapshot,
            _relation_vertices,
            _linestring_length_m,
            _drop_reason,
            progress,
        )
        emit_progress(progress, "access", f"{len(endpoints)} endpoints")
        _pass_access(conn, source_paths, endpoints, catalog_path, progress)
        snapshot.access_points = _load_access_points(conn)
        snapshot.access_points.extend(_parking_near_endpoints(snapshot, endpoints))
        emit_progress(progress, "assemble", "DayHikes")
        access_tree = _access_tree(snapshot.access_points)
        amenity_index = _amenity_index(snapshot.amenities)
        kept, report_counts = _keep_dayhikes(
            snapshot,
            access_tree,
            amenity_index,
            _relation_vertices,
            _linestring_length_m,
            _drop_reason,
            _dayhike_from_relation,
        )
        origin_counts = Counter(hike.name_origin for hike in kept)
        report = IngestReport(
            kept=len(kept),
            dropped_unnamed=0,
            dropped_length=report_counts["length"],
            dropped_network=report_counts["network"],
            named_osm_name=origin_counts[NAME_ORIGIN_OSM_NAME],
            named_osm_ref=origin_counts[NAME_ORIGIN_OSM_REF],
            named_synthetic=origin_counts[NAME_ORIGIN_SYNTHETIC],
        )
        return kept, report, work_path
    finally:
        conn.close()


def _fingerprint(paths: Sequence[Path]) -> str:
    rows = []
    for path in paths:
        stat = path.stat()
        rows.append(
            {"path": str(path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        )
    return json.dumps(rows)


def _work_is_resumable(work_path: Path, fingerprint: str) -> bool:
    if not work_path.exists():
        return False
    try:
        conn = sqlite3.connect(work_path)
        try:
            meta = dict(conn.execute("SELECT key, value FROM meta"))
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    return (
        meta.get("schema") == str(INGEST_WORK_SCHEMA)
        and meta.get("fingerprint") == fingerprint
    )


def _stored_scan_paths(work_path: Path, fallback: Sequence[Path]) -> list[Path]:
    conn = sqlite3.connect(work_path)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'scan_paths'").fetchone()
    finally:
        conn.close()
    if row is None:
        return list(fallback)
    return [Path(item) for item in json.loads(row[0])]


def _init_work(
    conn: sqlite3.Connection,
    fingerprint: str,
    source_paths: Sequence[Path],
    scan_paths: Sequence[Path],
) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS file_progress (
            pass TEXT NOT NULL,
            path TEXT NOT NULL,
            last_id INTEGER NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (pass, path)
        );
        CREATE TABLE IF NOT EXISTS relations (
            id INTEGER PRIMARY KEY,
            members TEXT NOT NULL,
            tags TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS needed_ways (
            id INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS amenity_ways (
            id INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS ways (
            id INTEGER PRIMARY KEY,
            node_ids TEXT NOT NULL,
            tags TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS needed_nodes (
            id INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS nodes (
            id INTEGER PRIMARY KEY,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            ele REAL
        );
        CREATE TABLE IF NOT EXISTS pending_amenity_ways (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL,
            node_id INTEGER NOT NULL,
            tags TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS pending_amenity_relations (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL,
            members TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS amenities (
            osm_key TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            tags TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS nearby_nodes (
            id INTEGER PRIMARY KEY,
            lat REAL NOT NULL,
            lon REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS access_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lat REAL NOT NULL,
            lon REAL NOT NULL
        );
        """
    )
    conn.executemany(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        [
            ("schema", str(INGEST_WORK_SCHEMA)),
            ("fingerprint", fingerprint),
            ("source_paths", json.dumps([str(path) for path in source_paths])),
            ("scan_paths", json.dumps([str(path) for path in scan_paths])),
        ],
    )
    conn.commit()


def _scan_paths(source_paths: Sequence[Path], progress: Progress | None) -> list[Path]:
    osmium_bin = shutil.which("osmium")
    scan: list[Path] = []
    for path in source_paths:
        if osmium_bin is None or not _needs_catalog_filter(path):
            scan.append(path)
            continue
        cache = _catalog_extract_path(path)
        if cache.is_file() and cache.stat().st_mtime_ns >= path.stat().st_mtime_ns:
            emit_progress(progress, "filter", f"cached {cache}")
            scan.append(cache)
            continue
        emit_progress(progress, "filter", f"{path.name} -> {cache.name}")
        _run_osmium_tags_filter(osmium_bin, path, cache)
        scan.append(cache)
    if osmium_bin is None:
        emit_progress(progress, "filter", "osmium-tool not on PATH, reading source files")
    elif not any(_needs_catalog_filter(path) for path in source_paths):
        emit_progress(progress, "filter", "skipped (not PBF)")
    return scan


def _needs_catalog_filter(path: Path) -> bool:
    return _is_pbf(path) and not path.name.endswith(".catalog.osm.pbf")


def _is_pbf(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(".pbf")


def _catalog_extract_path(source: Path) -> Path:
    name = source.name
    if name.endswith(".osm.pbf"):
        return source.with_name(name[: -len(".osm.pbf")] + ".catalog.osm.pbf")
    if name.endswith(".pbf"):
        return source.with_name(source.stem + ".catalog.osm.pbf")
    return source.with_name(source.name + ".catalog.osm.pbf")


def _run_osmium_tags_filter(osmium_bin: str, source: Path, dest: Path) -> None:
    if dest.name.endswith(".osm.pbf"):
        tmp = dest.with_name(dest.name[: -len(".osm.pbf")] + ".writing.osm.pbf")
    else:
        tmp = dest.with_name(dest.stem + ".writing.osm.pbf")
    cmd = [
        osmium_bin,
        "tags-filter",
        str(source.resolve()),
        *CATALOG_TAG_FILTERS,
        "-o",
        str(tmp),
        "--overwrite",
    ]
    cmd.append("--progress" if _stderr_is_tty() else "--no-progress")
    try:
        subprocess.run(cmd, check=True)
        tmp.replace(dest)
    finally:
        if tmp.exists():
            tmp.unlink()


def _stderr_is_tty() -> bool:
    return sys.stderr.isatty()


def _pass_done(conn: sqlite3.Connection, pass_name: str, paths: Sequence[Path]) -> bool:
    for path in paths:
        row = conn.execute(
            "SELECT done FROM file_progress WHERE pass = ? AND path = ?",
            (pass_name, str(path)),
        ).fetchone()
        if row is None or not row[0]:
            return False
    return bool(paths)


def _file_state(conn: sqlite3.Connection, pass_name: str, path: Path) -> tuple[int, bool]:
    row = conn.execute(
        "SELECT last_id, done FROM file_progress WHERE pass = ? AND path = ?",
        (pass_name, str(path)),
    ).fetchone()
    if row is None:
        return 0, False
    return int(row[0]), bool(row[1])


def _mark_progress(
    conn: sqlite3.Connection, pass_name: str, path: Path, last_id: int, done: bool = False
) -> None:
    conn.execute(
        "INSERT INTO file_progress (pass, path, last_id, done) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(pass, path) DO UPDATE SET last_id = excluded.last_id, "
        "done = excluded.done",
        (pass_name, str(path), last_id, int(done)),
    )
    conn.commit()


def _iter_file(path: Path, entities: osmium.osm.osm_entity_bits):
    yield from osmium.FileProcessor(str(path), entities)


def _pass_relations(
    conn: sqlite3.Connection, paths: Sequence[Path], progress: Progress | None
) -> None:
    emit_progress(progress, "relations", "starting")
    if _pass_done(conn, "relations", paths):
        emit_progress(progress, "relations", "resumed, already complete")
        return
    seen = 0
    hiking = 0
    for path in paths:
        last_id, done = _file_state(conn, "relations", path)
        if done:
            continue
        file_last = last_id
        for obj in _iter_file(path, osmium.osm.RELATION):
            if not obj.is_relation() or int(obj.id) <= last_id:
                continue
            tags = dict(obj.tags)
            members = [
                [_MEMBER_TYPE.get(str(member.type), str(member.type)), int(member.ref)]
                for member in obj.members
            ]
            osm_id = int(obj.id)
            if tags.get("route") == "hiking":
                conn.execute(
                    "INSERT OR REPLACE INTO relations (id, members, tags) VALUES (?, ?, ?)",
                    (osm_id, json.dumps(members), json.dumps(tags)),
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO needed_ways (id) VALUES (?)",
                    [(member_id,) for member_type, member_id in members if member_type == "way"],
                )
                hiking += 1
            kind = amenity_kind(tags)
            if kind is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO pending_amenity_relations "
                    "(id, kind, members, tags) VALUES (?, ?, ?, ?)",
                    (osm_id, kind, json.dumps(members), json.dumps(tags)),
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO amenity_ways (id) VALUES (?)",
                    [(member_id,) for member_type, member_id in members if member_type == "way"],
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO needed_nodes (id) VALUES (?)",
                    [(member_id,) for member_type, member_id in members if member_type == "node"],
                )
            seen += 1
            file_last = osm_id
            if seen % INGEST_CHECKPOINT_OBJECTS == 0:
                _mark_progress(conn, "relations", path, file_last)
            if seen % INGEST_PROGRESS_EVERY_OBJECTS == 0:
                emit_progress(
                    progress, "relations", f"{seen} scanned, {hiking} hiking, last_id={osm_id}"
                )
        _mark_progress(conn, "relations", path, file_last, done=True)
    emit_progress(progress, "relations", f"{hiking} hiking relations")


def _pass_ways(
    conn: sqlite3.Connection, paths: Sequence[Path], progress: Progress | None
) -> None:
    emit_progress(progress, "ways", "starting")
    if _pass_done(conn, "ways", paths):
        emit_progress(progress, "ways", "resumed, already complete")
        return
    needed_ways = {row[0] for row in conn.execute("SELECT id FROM needed_ways")}
    amenity_ways = {row[0] for row in conn.execute("SELECT id FROM amenity_ways")}
    seen = 0
    kept = 0
    for path in paths:
        last_id, done = _file_state(conn, "ways", path)
        if done:
            continue
        file_last = last_id
        for obj in _iter_file(path, osmium.osm.WAY):
            if not obj.is_way() or int(obj.id) <= last_id:
                continue
            osm_id = int(obj.id)
            node_ids = [int(node.ref) for node in obj.nodes]
            tags = dict(obj.tags)
            kind = amenity_kind(tags)
            if osm_id in needed_ways:
                conn.execute(
                    "INSERT OR REPLACE INTO ways (id, node_ids, tags) VALUES (?, ?, ?)",
                    (osm_id, json.dumps(node_ids), json.dumps(tags)),
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO needed_nodes (id) VALUES (?)",
                    [(node_id,) for node_id in node_ids],
                )
                kept += 1
            if node_ids and (kind is not None or osm_id in amenity_ways):
                conn.execute(
                    "INSERT OR REPLACE INTO pending_amenity_ways "
                    "(id, kind, node_id, tags) VALUES (?, ?, ?, ?)",
                    (osm_id, kind or "", node_ids[0], json.dumps(tags)),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO needed_nodes (id) VALUES (?)",
                    (node_ids[0],),
                )
            seen += 1
            file_last = osm_id
            if seen % INGEST_CHECKPOINT_OBJECTS == 0:
                _mark_progress(conn, "ways", path, file_last)
            if seen % INGEST_PROGRESS_EVERY_OBJECTS == 0:
                emit_progress(
                    progress, "ways", f"{seen} scanned, {kept} hiking ways, last_id={osm_id}"
                )
        _mark_progress(conn, "ways", path, file_last, done=True)
    emit_progress(progress, "ways", f"{kept} hiking ways")


def _pass_nodes(
    conn: sqlite3.Connection, paths: Sequence[Path], progress: Progress | None
) -> None:
    emit_progress(progress, "nodes", "starting")
    if _pass_done(conn, "nodes", paths):
        emit_progress(progress, "nodes", "resumed, already complete")
        return
    needed_nodes = {row[0] for row in conn.execute("SELECT id FROM needed_nodes")}
    seen = 0
    kept = 0
    for path in paths:
        last_id, done = _file_state(conn, "nodes", path)
        if done:
            continue
        file_last = last_id
        for obj in _iter_file(path, osmium.osm.NODE):
            if not obj.is_node() or int(obj.id) <= last_id:
                continue
            osm_id = int(obj.id)
            location = obj.location
            tags = dict(obj.tags)
            kind = amenity_kind(tags)
            if location.valid() and (osm_id in needed_nodes or kind is not None):
                point = (float(location.lat), float(location.lon))
                if osm_id in needed_nodes:
                    conn.execute(
                        "INSERT OR REPLACE INTO nodes (id, lat, lon, ele) VALUES (?, ?, ?, ?)",
                        (osm_id, point[0], point[1], _parse_ele_m(tags.get("ele"))),
                    )
                    kept += 1
                if kind is not None:
                    conn.execute(
                        "INSERT OR REPLACE INTO amenities "
                        "(osm_key, kind, lat, lon, tags) VALUES (?, ?, ?, ?, ?)",
                        (f"n{osm_id}", kind, point[0], point[1], json.dumps(tags)),
                    )
            seen += 1
            file_last = osm_id
            if seen % INGEST_CHECKPOINT_OBJECTS == 0:
                _mark_progress(conn, "nodes", path, file_last)
            if seen % INGEST_PROGRESS_EVERY_OBJECTS == 0:
                emit_progress(
                    progress, "nodes", f"{seen} scanned, {kept} kept, last_id={osm_id}"
                )
        _mark_progress(conn, "nodes", path, file_last, done=True)
    _resolve_pending_amenities(conn)
    emit_progress(progress, "nodes", f"{kept} geometry nodes")


def _resolve_pending_amenities(conn: sqlite3.Connection) -> None:
    node_coords = {
        node_id: (lat, lon)
        for node_id, lat, lon in conn.execute("SELECT id, lat, lon FROM nodes")
    }
    for way_id, kind, node_id, tags_json in conn.execute(
        "SELECT id, kind, node_id, tags FROM pending_amenity_ways"
    ):
        if not kind:
            continue
        point = node_coords.get(node_id)
        if point is None:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO amenities (osm_key, kind, lat, lon, tags) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"w{way_id}", kind, point[0], point[1], tags_json),
        )
    for rel_id, kind, members_json, tags_json in conn.execute(
        "SELECT id, kind, members, tags FROM pending_amenity_relations"
    ):
        point = None
        for member_type, member_id in json.loads(members_json):
            if member_type == "node":
                point = node_coords.get(member_id)
            elif member_type == "way":
                row = conn.execute(
                    "SELECT node_ids FROM ways WHERE id = ?", (member_id,)
                ).fetchone()
                if row is None:
                    pending = conn.execute(
                        "SELECT node_id FROM pending_amenity_ways WHERE id = ?",
                        (member_id,),
                    ).fetchone()
                    if pending is not None:
                        point = node_coords.get(pending[0])
                else:
                    node_ids = json.loads(row[0])
                    if node_ids:
                        point = node_coords.get(node_ids[0])
            if point is not None:
                break
        if point is None:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO amenities (osm_key, kind, lat, lon, tags) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"r{rel_id}", kind, point[0], point[1], tags_json),
        )
    conn.commit()


def _load_hiking_snapshot(
    conn: sqlite3.Connection, progress: Progress | None = None
) -> OsmSnapshot:
    snapshot = OsmSnapshot()
    loaded = 0
    for node_id, lat, lon, ele in conn.execute("SELECT id, lat, lon, ele FROM nodes"):
        snapshot.nodes[node_id] = (lat, lon)
        if ele is not None:
            snapshot.node_elevations[node_id] = ele
        loaded += 1
        if loaded % INGEST_PROGRESS_EVERY_OBJECTS == 0:
            emit_progress(progress, "access", f"loading nodes {loaded}")
    loaded = 0
    for way_id, node_ids_json, tags_json in conn.execute(
        "SELECT id, node_ids, tags FROM ways"
    ):
        snapshot.ways[way_id] = OsmWay(tuple(json.loads(node_ids_json)), json.loads(tags_json))
        loaded += 1
        if loaded % INGEST_PROGRESS_EVERY_OBJECTS == 0:
            emit_progress(progress, "access", f"loading ways {loaded}")
    loaded = 0
    for rel_id, members_json, tags_json in conn.execute(
        "SELECT id, members, tags FROM relations"
    ):
        members = tuple(
            (member_type, member_id) for member_type, member_id in json.loads(members_json)
        )
        snapshot.relations.append(OsmRelation(rel_id, members, json.loads(tags_json)))
        loaded += 1
        if loaded % INGEST_PROGRESS_EVERY_OBJECTS == 0:
            emit_progress(progress, "access", f"loading relations {loaded}")
    for _key, kind, lat, lon in conn.execute("SELECT osm_key, kind, lat, lon FROM amenities"):
        snapshot.amenities.append((kind, ((lat, lon),)))
    return snapshot


def _kept_endpoints(
    snapshot: OsmSnapshot,
    relation_vertices,
    linestring_length_m,
    drop_reason,
    progress: Progress | None = None,
) -> list[tuple[float, float]]:
    endpoints: list[tuple[float, float]] = []
    seen: set[int] = set()
    total = len(snapshot.relations)
    for index, relation in enumerate(snapshot.relations, start=1):
        if relation.id in seen:
            continue
        seen.add(relation.id)
        vertices = relation_vertices(relation, snapshot.nodes, snapshot.ways)
        points = [point for _, point in vertices]
        length_m = linestring_length_m(points)
        if drop_reason(relation, length_m) is not None or not points:
            continue
        endpoints.append(points[0])
        endpoints.append(points[-1])
        if index % INGEST_CHECKPOINT_OBJECTS == 0:
            emit_progress(progress, "access", f"choosing endpoints {index}/{total}")
    return endpoints


def _endpoint_tree(
    endpoints: Sequence[tuple[float, float]],
) -> BallTree | None:
    if not endpoints:
        return None
    return BallTree(np.radians(np.asarray(endpoints, dtype=float)), metric="haversine")


def _near_tree(
    tree: BallTree | None,
    points: Sequence[tuple[float, float]],
    radius_radians: float,
) -> np.ndarray:
    if tree is None or not points:
        return np.zeros(len(points), dtype=bool)
    indices = tree.query_radius(np.radians(np.asarray(points, dtype=float)), r=radius_radians)
    return np.array([len(hit) > 0 for hit in indices], dtype=bool)


def _pass_access(
    conn: sqlite3.Connection,
    source_paths: Sequence[Path],
    endpoints: Sequence[tuple[float, float]],
    catalog_path: Path,
    progress: Progress | None,
) -> None:
    emit_progress(progress, "access", "starting")
    if not endpoints:
        for path in source_paths:
            _mark_progress(conn, "access_nodes", path, 0, done=True)
            _mark_progress(conn, "access_ways", path, 0, done=True)
        emit_progress(progress, "access", "no kept DayHike endpoints")
        return
    scan_paths = _access_scan_paths(
        conn, source_paths, endpoints, catalog_path, progress
    )
    if _pass_done(conn, "access_ways", scan_paths):
        emit_progress(progress, "access", "resumed, already complete")
        return
    if not _has_progress(conn, "access_nodes", scan_paths) and not _has_progress(
        conn, "access_ways", scan_paths
    ):
        _clear_access(conn)
    tree = _endpoint_tree(endpoints)
    radius_radians = TRAILHEAD_ACCESS_RADIUS_M / EARTH_RADIUS_M
    _pass_access_nodes(conn, scan_paths, tree, radius_radians, progress)
    seen = 0
    kept = 0
    for path in scan_paths:
        last_id, done = _file_state(conn, "access_ways", path)
        if done:
            continue
        file_last = last_id
        for obj in _iter_file(path, osmium.osm.WAY):
            if not obj.is_way() or int(obj.id) <= last_id:
                continue
            osm_id = int(obj.id)
            tags = dict(obj.tags)
            if tags.get("highway") in DRIVEABLE_HIGHWAY:
                node_ids = [int(node.ref) for node in obj.nodes]
                for point in _nearby_points(conn, node_ids):
                    conn.execute(
                        "INSERT INTO access_points (lat, lon) VALUES (?, ?)",
                        point,
                    )
                    kept += 1
            seen += 1
            file_last = osm_id
            if seen % INGEST_CHECKPOINT_OBJECTS == 0:
                _mark_progress(conn, "access_ways", path, file_last)
            if seen % INGEST_PROGRESS_EVERY_OBJECTS == 0:
                emit_progress(
                    progress, "access", f"ways {seen} scanned, last_id={osm_id}"
                )
        _mark_progress(conn, "access_ways", path, file_last, done=True)
    emit_progress(progress, "access", f"{kept} clipped highway vertices")


def _has_progress(
    conn: sqlite3.Connection, pass_name: str, paths: Sequence[Path]
) -> bool:
    for path in paths:
        row = conn.execute(
            "SELECT 1 FROM file_progress WHERE pass = ? AND path = ?",
            (pass_name, str(path)),
        ).fetchone()
        if row is not None:
            return True
    return False


def _clear_access(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM nearby_nodes")
    conn.execute("DELETE FROM access_points")
    conn.execute(
        "DELETE FROM file_progress WHERE pass IN ('access_nodes', 'access_ways')"
    )
    conn.commit()


def _access_scan_paths(
    conn: sqlite3.Connection,
    source_paths: Sequence[Path],
    endpoints: Sequence[tuple[float, float]],
    catalog_path: Path,
    progress: Progress | None,
) -> list[Path]:
    osmium_bin = shutil.which("osmium")
    if osmium_bin is None:
        emit_progress(progress, "access", "osmium-tool not on PATH, scanning source")
        return list(source_paths)
    if not any(_is_pbf(path) for path in source_paths):
        emit_progress(progress, "access", "skipped (not PBF)")
        return list(source_paths)
    geojson = catalog_path.with_name(catalog_path.name + ".access.geojson")
    emit_progress(
        progress, "access", f"writing extract polygon ({len(endpoints)} endpoints)"
    )
    _write_access_geojson(geojson, endpoints)
    extracts: list[Path] = []
    for index, source in enumerate(source_paths):
        if not _is_pbf(source):
            emit_progress(progress, "access", f"skipped ({source.name} is not PBF)")
            extracts.append(source)
            continue
        dest = catalog_path.with_name(f"{catalog_path.name}.access.{index}.osm.pbf")
        if dest.exists() and (
            _has_progress(conn, "access_nodes", [dest])
            or _has_progress(conn, "access_ways", [dest])
        ):
            emit_progress(progress, "access", f"extract reused {dest.name}")
            extracts.append(dest)
            continue
        emit_progress(progress, "access", f"extract {source.name} -> {dest.name}")
        try:
            _run_osmium_extract(osmium_bin, source, geojson, dest)
        except subprocess.CalledProcessError:
            emit_progress(
                progress, "access", f"extract failed, scanning {source.name}"
            )
            extracts.append(source)
            continue
        extracts.append(dest)
    return extracts


def _write_access_geojson(
    path: Path, endpoints: Sequence[tuple[float, float]]
) -> None:
    # Osmium may omit nodes that sit on the polygon edge. Double the access
    # radius so each haversine disk is strictly inside its lon/lat box.
    extract_radius_m = TRAILHEAD_ACCESS_RADIUS_M + TRAILHEAD_ACCESS_RADIUS_M
    polygons: list[list[list[list[float]]]] = []
    seen: set[tuple[float, float]] = set()
    for lat, lon in endpoints:
        key = (round(lat, 7), round(lon, 7))
        if key in seen:
            continue
        seen.add(key)
        minlon, minlat, maxlon, maxlat = _containing_bbox(lat, lon, extract_radius_m)
        polygons.append(
            [
                [
                    [minlon, minlat],
                    [maxlon, minlat],
                    [maxlon, maxlat],
                    [minlon, maxlat],
                    [minlon, minlat],
                ]
            ]
        )
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {},
                        "geometry": {"type": "Polygon", "coordinates": ring},
                    }
                    for ring in polygons
                ],
            }
        )
    )


def _containing_bbox(
    lat: float, lon: float, radius_m: float
) -> tuple[float, float, float, float]:
    dlat = math.degrees(radius_m / EARTH_RADIUS_M)
    abs_cos = abs(math.cos(math.radians(lat)))
    dlon = 180.0 if abs_cos == 0.0 else min(180.0, dlat / abs_cos)
    minlat = max(-90.0, lat - dlat)
    maxlat = min(90.0, lat + dlat)
    minlon = lon - dlon
    maxlon = lon + dlon
    if minlon < -180.0 or maxlon > 180.0:
        minlon, maxlon = -180.0, 180.0
    return minlon, minlat, maxlon, maxlat


def _run_osmium_extract(
    osmium_bin: str, source: Path, polygon: Path, dest: Path
) -> None:
    if dest.name.endswith(".osm.pbf"):
        tmp = dest.with_name(dest.name[: -len(".osm.pbf")] + ".writing.osm.pbf")
    else:
        tmp = dest.with_name(dest.stem + ".writing.osm.pbf")
    cmd = [
        osmium_bin,
        "extract",
        "--polygon",
        str(polygon),
        "--strategy",
        "complete_ways",
        "-o",
        str(tmp),
        "--overwrite",
        str(source.resolve()),
    ]
    cmd.append("--progress")
    try:
        subprocess.run(cmd, check=True)
        tmp.replace(dest)
    finally:
        if tmp.exists():
            tmp.unlink()


def _nearby_points(
    conn: sqlite3.Connection, node_ids: Sequence[int]
) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    chunk_size = INGEST_CHECKPOINT_OBJECTS
    for start in range(0, len(node_ids), chunk_size):
        chunk = list(node_ids[start : start + chunk_size])
        if not chunk:
            continue
        placeholders = ",".join("?" * len(chunk))
        points.extend(
            conn.execute(
                f"SELECT lat, lon FROM nearby_nodes WHERE id IN ({placeholders})",
                chunk,
            )
        )
    return points


def _pass_access_nodes(
    conn: sqlite3.Connection,
    paths: Sequence[Path],
    tree: BallTree | None,
    radius_radians: float,
    progress: Progress | None,
) -> None:
    if _pass_done(conn, "access_nodes", paths):
        return
    batch_ids: list[int] = []
    batch_points: list[tuple[float, float]] = []
    batch_highways: list[bool] = []
    seen = 0

    def flush() -> None:
        if not batch_points:
            return
        keep = _near_tree(tree, batch_points, radius_radians)
        rows = [
            (osm_id, point[0], point[1])
            for osm_id, point, flag in zip(batch_ids, batch_points, keep, strict=True)
            if flag
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO nearby_nodes (id, lat, lon) VALUES (?, ?, ?)",
            rows,
        )
        highway_rows = [
            (point[0], point[1])
            for point, near, is_highway in zip(
                batch_points, keep, batch_highways, strict=True
            )
            if near and is_highway
        ]
        conn.executemany(
            "INSERT INTO access_points (lat, lon) VALUES (?, ?)",
            highway_rows,
        )
        batch_ids.clear()
        batch_points.clear()
        batch_highways.clear()

    for path in paths:
        last_id, done = _file_state(conn, "access_nodes", path)
        if done:
            continue
        file_last = last_id
        for obj in _iter_file(path, osmium.osm.NODE):
            if not obj.is_node() or int(obj.id) <= last_id:
                continue
            osm_id = int(obj.id)
            location = obj.location
            if location.valid():
                tags = dict(obj.tags)
                batch_ids.append(osm_id)
                batch_points.append((float(location.lat), float(location.lon)))
                batch_highways.append(tags.get("highway") in DRIVEABLE_HIGHWAY)
            seen += 1
            file_last = osm_id
            if len(batch_ids) >= INGEST_CHECKPOINT_OBJECTS:
                flush()
                _mark_progress(conn, "access_nodes", path, file_last)
            if seen % INGEST_PROGRESS_EVERY_OBJECTS == 0:
                emit_progress(progress, "access", f"nodes {seen} scanned, last_id={osm_id}")
        flush()
        _mark_progress(conn, "access_nodes", path, file_last, done=True)


def _load_access_points(conn: sqlite3.Connection) -> list[tuple[float, float]]:
    return [
        (lat, lon) for lat, lon in conn.execute("SELECT lat, lon FROM access_points")
    ]


def _parking_near_endpoints(
    snapshot: OsmSnapshot,
    endpoints: Sequence[tuple[float, float]],
) -> list[tuple[float, float]]:
    parking = [
        points[0]
        for kind, points in snapshot.amenities
        if kind == "parking" and points
    ]
    tree = _endpoint_tree(endpoints)
    keep = _near_tree(tree, parking, TRAILHEAD_ACCESS_RADIUS_M / EARTH_RADIUS_M)
    return [point for point, flag in zip(parking, keep, strict=True) if flag]


def _keep_dayhikes(
    snapshot: OsmSnapshot,
    access_tree,
    amenity_index,
    relation_vertices,
    linestring_length_m,
    drop_reason,
    dayhike_from_relation,
):
    report_counts = {"unnamed": 0, "length": 0, "network": 0}
    kept = []
    seen: set[int] = set()
    for relation in snapshot.relations:
        if relation.id in seen:
            continue
        seen.add(relation.id)
        if relation.tags.get("route") != "hiking":
            continue
        vertices = relation_vertices(relation, snapshot.nodes, snapshot.ways)
        points = [point for _, point in vertices]
        length_m = linestring_length_m(points)
        reason = drop_reason(relation, length_m)
        if reason is not None:
            report_counts[reason] += 1
            continue
        kept.append(
            dayhike_from_relation(
                relation,
                snapshot,
                vertices,
                points,
                length_m,
                access_tree,
                amenity_index,
            )
        )
    kept.sort(key=lambda hike: hike.id)
    return kept, report_counts


def write_geometry_store(
    path: Path, dayhikes: Sequence[DayHike], conn: sqlite3.Connection
) -> None:
    """Write DayHikeGeometry, member ways, and raw amenity points beside the Catalog."""
    from dayhike.catalog import OSM_ATTRIBUTION

    snapshot = _load_hiking_snapshot(conn)
    relations = {relation.id: relation for relation in snapshot.relations}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    out = sqlite3.connect(tmp)
    try:
        out.executescript(
            """
            CREATE TABLE geometry_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE dayhikes (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                name_origin TEXT NOT NULL,
                length_m REAL NOT NULL,
                difficulty TEXT,
                members TEXT NOT NULL,
                relation_tags TEXT NOT NULL
            );
            CREATE TABLE vertices (
                dayhike_id INTEGER NOT NULL,
                seq INTEGER NOT NULL,
                node_id INTEGER NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                ele REAL,
                PRIMARY KEY (dayhike_id, seq)
            );
            CREATE TABLE ways (
                dayhike_id INTEGER NOT NULL,
                seq INTEGER NOT NULL,
                way_id INTEGER NOT NULL,
                node_ids TEXT NOT NULL,
                tags TEXT NOT NULL,
                PRIMARY KEY (dayhike_id, seq)
            );
            CREATE TABLE amenities (
                osm_key TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                tags TEXT NOT NULL
            );
            """
        )
        out.execute(
            "INSERT INTO geometry_meta (key, value) VALUES (?, ?)",
            ("attribution", OSM_ATTRIBUTION),
        )
        for hike in dayhikes:
            relation = relations[hike.id]
            out.execute(
                "INSERT INTO dayhikes (id, name, name_origin, length_m, difficulty, "
                "members, relation_tags) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    hike.id,
                    hike.name,
                    hike.name_origin,
                    hike.length_m,
                    hike.difficulty.sac_scale,
                    json.dumps([list(member) for member in relation.members]),
                    json.dumps(relation.tags),
                ),
            )
            vertices = _relation_vertices(relation, snapshot.nodes, snapshot.ways)
            out.executemany(
                "INSERT INTO vertices (dayhike_id, seq, node_id, latitude, longitude, ele) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        hike.id,
                        seq,
                        node_id,
                        lat,
                        lon,
                        snapshot.node_elevations.get(node_id),
                    )
                    for seq, (node_id, (lat, lon)) in enumerate(vertices)
                ],
            )
            way_rows = []
            way_seq = 0
            for member_type, member_id in relation.members:
                if member_type != "way":
                    continue
                way = snapshot.ways.get(member_id)
                if way is None:
                    continue
                way_rows.append(
                    (
                        hike.id,
                        way_seq,
                        member_id,
                        json.dumps(list(way.node_ids)),
                        json.dumps(way.tags),
                    )
                )
                way_seq += 1
            out.executemany(
                "INSERT INTO ways (dayhike_id, seq, way_id, node_ids, tags) "
                "VALUES (?, ?, ?, ?, ?)",
                way_rows,
            )
        amenity_rows = list(
            conn.execute("SELECT osm_key, kind, lat, lon, tags FROM amenities")
        )
        out.executemany(
            "INSERT INTO amenities (osm_key, kind, latitude, longitude, tags) "
            "VALUES (?, ?, ?, ?, ?)",
            amenity_rows,
        )
        out.commit()
    except Exception:
        out.close()
        if tmp.exists():
            tmp.unlink()
        raise
    out.close()
    tmp.replace(path)
