from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np
import osmium

from dayhike.catalog import (
    GAP_JOIN_M,
    NAME_ORIGIN_FROM_TO,
    NAME_ORIGIN_OSM_NAME,
    NAME_ORIGIN_OSM_REF,
    NAME_ORIGIN_PLACE,
    NAME_ORIGIN_SYNTHETIC,
    DayHike,
    IngestReport,
    MainWalk,
    SAC_SCALE_RANK,
    NamedPoints,
    _access_tree,
    _amenity_index,
    _dayhike_from_relation,
    _drop_reason,
    _haversine_m,
    assemble_main_walk,
    declared_start,
    named_points,
)
from dayhike.osm import (
    OsmRelation,
    OsmSnapshot,
    OsmWay,
    _MEMBER_TYPE,
    _parse_ele_m,
    amenity_kind,
)

INGEST_WORK_SCHEMA = 8
INGEST_CHECKPOINT_OBJECTS = 50_000
INGEST_PROGRESS_EVERY_OBJECTS = 100_000
HIKING_ROUTES = ("hiking", "foot")  # route=foot is read as hiking (ADR-0034)
CATALOG_TAG_FILTERS = (
    "r/route=hiking,foot",
    "w/sac_scale",
    "nwr/tourism=alpine_hut,wilderness_hut,viewpoint",
    "nwr/amenity=parking,restaurant,cafe,drinking_water,shelter,water_point",
    "nwr/natural=peak,spring",
    "nwr/man_made=water_tap",
    "n/place=city,town,village,hamlet",
)

Progress = Callable[[str], None]


def work_path_for(catalog_path: Path) -> Path:
    return catalog_path.with_name(catalog_path.name + ".work")


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
        emit_progress(progress, "assemble", "loading work file")
        snapshot = _load_hiking_snapshot(conn, progress)
        emit_progress(
            progress,
            "assemble",
            "loaded "
            f"{len(snapshot.relations)} relations, "
            f"{len(snapshot.ways)} ways, "
            f"{len(snapshot.nodes)} nodes",
        )
        emit_progress(progress, "assemble", "DayHikes")
        access_tree = _access_tree(snapshot.access_points)
        amenity_index = _amenity_index(snapshot.amenities)
        kept, report_counts = _keep_dayhikes(
            snapshot,
            access_tree,
            amenity_index,
            _drop_reason,
            _dayhike_from_relation,
            named_points(snapshot.named_points),
            named_points(snapshot.places),
        )
        origin_counts = Counter(hike.name_origin for hike in kept)
        report = IngestReport(
            kept=len(kept),
            dropped_unnamed=0,
            dropped_length=report_counts["length"],
            dropped_container=report_counts["container"],
            named_osm_name=origin_counts[NAME_ORIGIN_OSM_NAME],
            named_osm_ref=origin_counts[NAME_ORIGIN_OSM_REF],
            named_synthetic=origin_counts[NAME_ORIGIN_SYNTHETIC],
            dropped_node_network=report_counts["node_network"],
            named_from_to=origin_counts[NAME_ORIGIN_FROM_TO],
            named_place=origin_counts[NAME_ORIGIN_PLACE],
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
            roles TEXT NOT NULL,
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
        CREATE TABLE IF NOT EXISTS graded_ways (
            id INTEGER PRIMARY KEY,
            node_ids TEXT NOT NULL,
            tags TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS needed_nodes (
            id INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS graded_needed_nodes (
            id INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS nodes (
            id INTEGER PRIMARY KEY,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            ele REAL
        );
        CREATE TABLE IF NOT EXISTS graded_nodes (
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
        if (
            cache.is_file()
            and cache.stat().st_mtime_ns >= path.stat().st_mtime_ns
            and _catalog_filters_path(cache).is_file()
            and _catalog_filters_path(cache).read_text() == _catalog_filters_text()
        ):
            emit_progress(progress, "filter", f"cached {cache}")
            scan.append(cache)
            continue
        emit_progress(progress, "filter", f"{path.name} -> {cache.name}")
        _run_osmium_tags_filter(osmium_bin, path, cache)
        scan.append(cache)
    if osmium_bin is None:
        emit_progress(progress, "filter", "osmium-tool not on PATH, reading source files")
    elif not any(_needs_catalog_filter(path) for path in source_paths):
        if any(_is_pbf(path) for path in source_paths):
            emit_progress(progress, "filter", "skipped (catalog extract, read as is)")
        else:
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


def _catalog_filters_path(cache: Path) -> Path:
    return cache.with_name(cache.name + ".filters")


def _catalog_filters_text() -> str:
    return "\n".join(CATALOG_TAG_FILTERS) + "\n"


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
        _catalog_filters_path(dest).write_text(_catalog_filters_text())
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
            roles = [str(member.role) for member in obj.members]
            osm_id = int(obj.id)
            if tags.get("route") in HIKING_ROUTES:
                conn.execute(
                    "INSERT OR REPLACE INTO relations (id, members, roles, tags) "
                    "VALUES (?, ?, ?, ?)",
                    (osm_id, json.dumps(members), json.dumps(roles), json.dumps(tags)),
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
    graded = 0
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
            elif _is_graded_path(tags):
                conn.execute(
                    "INSERT OR REPLACE INTO graded_ways (id, node_ids, tags) VALUES (?, ?, ?)",
                    (osm_id, json.dumps(node_ids), json.dumps(tags)),
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO graded_needed_nodes (id) VALUES (?)",
                    [(node_id,) for node_id in node_ids],
                )
                graded += 1
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
    emit_progress(progress, "ways", f"{kept} hiking ways, {graded} graded paths")


def _is_graded_path(tags: dict[str, str]) -> bool:
    """A ``highway=path`` with a ``sac_scale`` grade: in the TrailGraph though no relation lists it (ADR-0037)."""
    return tags.get("highway") == "path" and tags.get("sac_scale") in SAC_SCALE_RANK


def _pass_nodes(
    conn: sqlite3.Connection, paths: Sequence[Path], progress: Progress | None
) -> None:
    emit_progress(progress, "nodes", "starting")
    if _pass_done(conn, "nodes", paths):
        emit_progress(progress, "nodes", "resumed, already complete")
        return
    needed_nodes = {row[0] for row in conn.execute("SELECT id FROM needed_nodes")}
    # Sorted ids, not a set: graded paths may hold as many nodes as the hiking ways.
    graded_nodes = np.fromiter(
        (row[0] for row in conn.execute("SELECT id FROM graded_needed_nodes ORDER BY id")), dtype=np.int64
    )
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
            needed = osm_id in needed_nodes
            graded = not needed and _sorted_has(graded_nodes, osm_id)
            if location.valid() and (needed or graded or kind is not None):
                point = (float(location.lat), float(location.lon))
                if needed:
                    conn.execute(
                        "INSERT OR REPLACE INTO nodes (id, lat, lon, ele) VALUES (?, ?, ?, ?)",
                        (osm_id, point[0], point[1], _parse_ele_m(tags.get("ele"))),
                    )
                    kept += 1
                elif graded:
                    conn.execute(
                        "INSERT OR REPLACE INTO graded_nodes (id, lat, lon, ele) VALUES (?, ?, ?, ?)",
                        (osm_id, point[0], point[1], _parse_ele_m(tags.get("ele"))),
                    )
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


def _sorted_has(ids: np.ndarray, osm_id: int) -> bool:
    at = int(ids.searchsorted(osm_id))
    return at < len(ids) and int(ids[at]) == osm_id


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
            emit_progress(progress, "assemble", f"loading nodes {loaded}")
    loaded = 0
    for way_id, node_ids_json, tags_json in conn.execute(
        "SELECT id, node_ids, tags FROM ways"
    ):
        snapshot.ways[way_id] = OsmWay(tuple(json.loads(node_ids_json)), json.loads(tags_json))
        loaded += 1
        if loaded % INGEST_PROGRESS_EVERY_OBJECTS == 0:
            emit_progress(progress, "assemble", f"loading ways {loaded}")
    loaded = 0
    for rel_id, members_json, roles_json, tags_json in conn.execute(
        "SELECT id, members, roles, tags FROM relations"
    ):
        members = tuple(
            (member_type, member_id) for member_type, member_id in json.loads(members_json)
        )
        snapshot.relations.append(
            OsmRelation(rel_id, members, json.loads(tags_json), tuple(json.loads(roles_json)))
        )
        loaded += 1
        if loaded % INGEST_PROGRESS_EVERY_OBJECTS == 0:
            emit_progress(progress, "assemble", f"loading relations {loaded}")
    for kind, lat, lon, name in conn.execute(
        "SELECT kind, lat, lon, json_extract(tags, '$.name') FROM amenities"
    ):
        if kind == "place":
            snapshot.places.append((lat, lon, name))
        else:
            snapshot.amenities.append((kind, ((lat, lon),)))
        if name:
            snapshot.named_points.append((lat, lon, name))
        if kind == "parking":
            snapshot.access_points.append((lat, lon))
    return snapshot


def _keep_dayhikes(
    snapshot: OsmSnapshot,
    access_tree,
    amenity_index,
    drop_reason,
    dayhike_from_relation,
    named: NamedPoints | None = None,
    places: NamedPoints | None = None,
):
    report_counts = {"unnamed": 0, "length": 0, "container": 0, "node_network": 0}
    kept = []
    seen: set[int] = set()
    for relation in snapshot.relations:
        if relation.id in seen:
            continue
        seen.add(relation.id)
        if relation.tags.get("route") not in HIKING_ROUTES:
            continue
        walk = _main_walk(relation, snapshot, access_tree, named)
        vertices = walk.vertices
        points = [point for _, point in vertices]
        length_m = walk.length_m
        round_trip = bool(points) and _haversine_m(*points[0], *points[-1]) <= GAP_JOIN_M
        reason = drop_reason(relation, length_m, round_trip)
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
                amenity_index,
                places,
            )
        )
    kept.sort(key=lambda hike: hike.id)
    return kept, report_counts


def _main_walk(
    relation: OsmRelation, snapshot: OsmSnapshot, access_tree, named: NamedPoints | None
) -> MainWalk:
    """The relation's main walk, its line started where its from/to names say if they can."""
    return assemble_main_walk(
        relation, snapshot.ways, snapshot.nodes, access_tree, declared_start(relation.tags, named)
    )


def write_geometry_store(
    path: Path,
    dayhikes: Sequence[DayHike],
    conn: sqlite3.Connection,
) -> None:
    """Write DayHikeGeometry, member ways, and raw amenity points beside the Catalog.

    Access points and named points are the work file's, as in ``run_ingest``,
    so each main walk starts at the same Trailhead as in the Catalog.
    """
    from dayhike.catalog import OSM_ATTRIBUTION

    snapshot = _load_hiking_snapshot(conn)
    relations = {relation.id: relation for relation in snapshot.relations}
    access_tree = _access_tree(snapshot.access_points)
    named = named_points(snapshot.named_points)
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
                unconnected_m REAL NOT NULL,
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
                landcover INTEGER,
                PRIMARY KEY (dayhike_id, seq)
            );
            CREATE TABLE ways (
                dayhike_id INTEGER NOT NULL,
                seq INTEGER NOT NULL,
                way_id INTEGER NOT NULL,
                node_ids TEXT NOT NULL,
                tags TEXT NOT NULL,
                in_chain INTEGER NOT NULL,
                leftover TEXT,
                walked_m REAL NOT NULL,
                walk_order INTEGER,
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
            walk = _main_walk(relation, snapshot, access_tree, named)
            out.execute(
                "INSERT INTO dayhikes (id, name, name_origin, length_m, unconnected_m, "
                "difficulty, members, relation_tags) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    hike.id,
                    hike.name,
                    hike.name_origin,
                    hike.length_m,
                    walk.unconnected_m,
                    hike.difficulty.sac_scale,
                    json.dumps([list(member) for member in relation.members]),
                    json.dumps(relation.tags),
                ),
            )
            vertices = walk.vertices
            out.executemany(
                "INSERT INTO vertices (dayhike_id, seq, node_id, latitude, longitude, "
                "ele, landcover) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        hike.id,
                        seq,
                        node_id,
                        lat,
                        lon,
                        snapshot.node_elevations.get(node_id),
                        None,
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
                        int(walk.in_chain[way_seq]),
                        walk.leftovers[way_seq],
                        walk.walked_m[way_seq],
                        walk.walk_order[way_seq],
                    )
                )
                way_seq += 1
            out.executemany(
                "INSERT INTO ways (dayhike_id, seq, way_id, node_ids, tags, in_chain, "
                "leftover, walked_m, walk_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
