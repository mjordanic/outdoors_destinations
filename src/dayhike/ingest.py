from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

import numpy as np
import osmium

from dayhike.built import BuildContext, BuiltHikeSource, BuiltWalk, built_dayhike
from dayhike.external import (
    EXTERNAL_ID_MAX,
    EXTERNAL_ID_MIN,
    ExternalHike,
    external_dayhike,
    external_label,
    long_enough,
)
from dayhike.catalog import (
    GAP_JOIN_M,
    KIND_RELATION,
    NAME_ORIGIN_FROM_TO,
    NAME_ORIGIN_OSM_NAME,
    NAME_ORIGIN_OSM_REF,
    NAME_ORIGIN_PLACE,
    NAME_ORIGIN_SYNTHETIC,
    PLACE_RADIUS_M,
    DayHike,
    Heights,
    IngestReport,
    LowerStart,
    MainWalk,
    PreviousStarts,
    SAC_SCALE_RANK,
    NamedPoints,
    _access_tree,
    _amenity_index,
    _dayhike_from_relation,
    _drop_reason,
    _haversine_m,
    assemble_main_walk,
    dashed_ends,
    declared_start,
    first_choice,
    named_points,
    tagged_ends,
)
from dayhike.dem import DEM_READ_KEY
from dayhike.destination_hikes import destination_source, search_destinations
from dayhike.named_trails import named_trails
from dayhike.names import unique_names
from dayhike.network_hikes import network_source
from dayhike.osm import (
    NodeStore,
    OsmRelation,
    OsmSnapshot,
    OsmWay,
    _MEMBER_TYPE,
    _parse_ele_m,
    amenity_kind,
)
from dayhike.parallel import parallel_iter, parallel_map
from dayhike.similarity import TWIN_SHARE, TWIN_STEP_M, _even_points, _step_m, twin_groups
from dayhike.stages import day_stages, external_stages

INGEST_WORK_SCHEMA = 12
INGEST_CHECKPOINT_OBJECTS = 50_000
INGEST_PROGRESS_EVERY_OBJECTS = 100_000
WORK_CACHE_KIB = 262_144  # the work file's page cache while ingest runs
HIKING_ROUTES = ("hiking", "foot")  # route=foot is read as hiking (ADR-0034)
CATALOG_TAG_FILTERS = (
    "r/route=hiking,foot",
    "w/sac_scale",
    "nwr/tourism=alpine_hut,wilderness_hut,viewpoint",
    "nwr/amenity=parking,restaurant,cafe,drinking_water,shelter,water_point",
    "nwr/natural=peak,spring",
    "wr/water=lake,reservoir,pond",
    "n/information=guidepost",
    "nwr/man_made=water_tap",
    "n/place=city,town,village,hamlet",
)
# What ingest reads of the paths no hiking relation lists, each level adding to the one
# before (ADR-0065, ADR-0066): graded paths (ADR-0037), then named paths, footways and
# bridleways (the ways of named trails), then named tracks too, then unnamed paths too.
# Beyond ``graded`` they also enter the snapshot, with highway=trailhead nodes.
PATH_LEVELS = ("graded", "trails", "named", "unnamed")
TRAIL_PATH_HIGHWAYS = ("path", "footway", "bridleway")
NAMED_PATH_HIGHWAYS = (*TRAIL_PATH_HIGHWAYS, "track")
NOT_A_PATH_FOOTWAYS = ("sidewalk", "crossing")
TRAILHEAD_TAG_FILTER = "n/highway=trailhead"
LAKE_WATER = ("lake", "reservoir", "pond")  # a named natural=water of these is a lake a NetworkHike may go to
DESTINATION_KINDS = ("peak", "hut", "viewpoint", "lake")  # named, a NetworkHike's destinations (ADR-0039)
# Point kinds read only for built hikes, NetworkHikes and named trails (trailhead nodes, ADR-0066):
# no amenity, no named point, not in the GeometryStore.
NETWORK_ONLY_KINDS = ("lake", "guidepost", "trailhead")
# Sources of DayHikes that are not one relation (ADR-0038), run after the relations are kept.
# named_trails builds only from the trail ways a ``paths`` level beyond ``graded`` reads (ADR-0066).
BUILT_HIKE_SOURCES: tuple[BuiltHikeSource, ...] = (day_stages, network_source, named_trails, destination_source)

Progress = Callable[[str], None]


def check_paths(paths: str) -> None:
    """Refuse a paths level that is not one of ``PATH_LEVELS``."""
    if paths not in PATH_LEVELS:
        raise ValueError(f"paths must be one of {', '.join(PATH_LEVELS)}, got {paths!r}")


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
    sources: Sequence[BuiltHikeSource] = BUILT_HIKE_SOURCES,
    heights: Heights | None = None,
    external: Sequence[tuple[str, Sequence[ExternalHike]]] = (),
    previous: PreviousStarts | None = None,
    paths: str | Sequence[str] = "graded",
) -> tuple[list[DayHike], IngestReport, Path, OsmSnapshot]:
    # One paths level for every source, or one per source (a Europe and US build, ADR-0069).
    levels = [paths] * len(source_paths) if isinstance(paths, str) else list(paths)
    if len(levels) != len(source_paths):
        raise ValueError(f"{len(levels)} paths levels for {len(source_paths)} sources")
    for level in levels:
        check_paths(level)
    catalog_path = Path(catalog_path)
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    work_path = work_path_for(catalog_path)
    fingerprint = _fingerprint(source_paths, levels)
    resume = not fresh and _work_is_resumable(work_path, fingerprint)
    if not resume:
        remove_work(work_path)

    emit_progress(progress, "filter", "starting")
    scan_paths = _scan_paths(source_paths, progress, levels) if not resume else _stored_scan_paths(
        work_path, source_paths
    )

    conn = _connect_work(work_path)
    try:
        if not resume:
            _init_work(conn, fingerprint, source_paths, scan_paths)
        _pass_relations(conn, scan_paths, progress)
        _pass_ways(conn, scan_paths, progress, levels)
        _pass_nodes(conn, scan_paths, progress)
        if destination_source in sources:
            # Before the snapshot loads: the tile search forks its workers from a small process (ADR-0067).
            search_destinations(work_path, source_paths, getattr(heights, "raster_dir", None), progress)
        emit_progress(progress, "assemble", "loading work file")
        snapshot = _load_hiking_snapshot(conn, progress, with_paths=any(level != "graded" for level in levels))
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
        named = named_points(snapshot.named_points)
        lower = _lower_start(snapshot, heights, previous)
        walks: dict[int, _StoredWalk] = {}
        kept, report_counts = _keep_dayhikes(
            snapshot,
            access_tree,
            amenity_index,
            _drop_reason,
            _dayhike_from_relation,
            named,
            named_points(snapshot.places),
            walks if sources or external else None,
            lower,
        )
        relation_hikes = tuple(kept)
        if sources:
            emit_progress(progress, "assemble", "built DayHikes")
            context = BuildContext(
                snapshot,
                tuple(kept),
                lambda relation: _stored_walk(walks[relation.id], snapshot.nodes)
                if relation.id in walks
                else _main_walk(relation, snapshot, access_tree, named, lower),
                progress,
                lower,
                tuple(Path(path) for path in source_paths),
                getattr(heights, "raster_dir", None),
                work_path=work_path,
            )
            kept += _build_hikes(conn, sources, context, amenity_index)
        external_counts = {"added": 0, "stages": 0, "twin": 0, "duplicate": 0, "length": 0}
        if external:
            emit_progress(progress, "assemble", "external hikes")
            added, external_counts = _add_external(
                conn, external, relation_hikes, kept[len(relation_hikes) :], walks, snapshot, amenity_index
            )
            emit_progress(
                progress,
                "assemble",
                f"external hikes: {external_counts['added']} added ({external_counts['stages']} DayStages), "
                f"{external_counts['twin']} Twins of OSM hikes, "
                f"{external_counts['duplicate']} Twins of external hikes "
                f"and {external_counts['length']} under 1 km dropped",
            )
            kept = sorted([*kept, *added], key=lambda hike: hike.id)
        # Every hike name in the build is unique, and no placeholder ("Road", "Trail") names one (ADR-0068).
        kept = unique_names(kept, snapshot.places, snapshot.destinations)
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
            external_added=external_counts["added"],
            external_stages=external_counts["stages"],
            dropped_external_twin=external_counts["twin"],
            dropped_external_duplicate=external_counts["duplicate"],
            dropped_external_length=external_counts["length"],
        )
        return kept, report, work_path, snapshot
    finally:
        conn.close()


def _connect_work(work_path: Path) -> sqlite3.Connection:
    """The work file, in WAL mode without a sync at each commit: a crash of
    the machine can lose the last checkpoints, but a checkpoint and the rows
    written before it are kept or lost together, so a resumed pass never skips
    an object whose rows were not written."""
    conn = sqlite3.connect(work_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute(f"PRAGMA cache_size = -{WORK_CACHE_KIB}")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


def remove_work(work_path: Path) -> None:
    """The work file and its WAL side files, so a stale WAL never meets a new work file."""
    for path in (work_path, Path(f"{work_path}-wal"), Path(f"{work_path}-shm")):
        path.unlink(missing_ok=True)


class _Pending:
    """Rows a pass has read but not yet written, written together at each
    checkpoint (``write``) before its progress is marked, so a resumed pass
    never skips an object whose rows were not written. Rows of one statement
    are written in the order added; ids for an id-only table once each."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.rows: dict[str, list[tuple]] = {}
        self.ids: dict[str, list[int]] = {}

    def add(self, statement: str, *row) -> None:
        self.rows.setdefault(statement, []).append(row)

    def add_ids(self, table: str, ids: Sequence[int]) -> None:
        self.ids.setdefault(table, []).extend(ids)

    def write(self) -> None:
        for statement, rows in self.rows.items():
            self.conn.executemany(statement, rows)
        for table, ids in self.ids.items():
            self.conn.executemany(
                f"INSERT OR IGNORE INTO {table} (id) VALUES (?)",
                ((int(osm_id),) for osm_id in np.unique(np.array(ids, dtype=np.int64))),
            )
        self.rows.clear()
        self.ids.clear()


def _sorted_ids(conn: sqlite3.Connection, table: str) -> np.ndarray:
    # A pass's lookup ids as a sorted array (``_sorted_has``), not a set of Python ints.
    return np.fromiter((row[0] for row in conn.execute(f"SELECT id FROM {table} ORDER BY id")), dtype=np.int64)


def _fingerprint(paths: Sequence[Path], levels: Sequence[str]) -> str:
    rows: list[dict] = []
    for path, level in zip(paths, levels):
        stat = path.stat()
        rows.append(
            {"path": str(path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "paths": level}
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
        CREATE TABLE IF NOT EXISTS path_ways (
            id INTEGER PRIMARY KEY,
            node_ids TEXT NOT NULL,
            tags TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS needed_nodes (
            id INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS path_needed_nodes (
            id INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS snapshot_path_ways (
            id INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS snapshot_path_nodes (
            id INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS nodes (
            id INTEGER PRIMARY KEY,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            ele REAL
        );
        CREATE TABLE IF NOT EXISTS path_nodes (
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


def _scan_paths(source_paths: Sequence[Path], progress: Progress | None, levels: Sequence[str]) -> list[Path]:
    osmium_bin = shutil.which("osmium")
    scan: list[Path] = []
    for path, paths in zip(source_paths, levels):
        if osmium_bin is None or not _needs_catalog_filter(path):
            scan.append(path)
            continue
        cache = _catalog_extract_path(path)
        if (
            cache.is_file()
            and cache.stat().st_mtime_ns >= path.stat().st_mtime_ns
            and _catalog_filters_path(cache).is_file()
            and _catalog_filters_path(cache).read_text() == _catalog_filters_text(paths)
        ):
            emit_progress(progress, "filter", f"cached {cache}")
            scan.append(cache)
            continue
        emit_progress(progress, "filter", f"{path.name} -> {cache.name}")
        _run_osmium_tags_filter(osmium_bin, path, cache, paths)
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


def _catalog_filters(paths: str) -> tuple[str, ...]:
    if paths == "graded":
        return CATALOG_TAG_FILTERS
    # osmium cannot ask for a name too: the extract keeps every such way, the ways pass picks.
    highways = TRAIL_PATH_HIGHWAYS if paths == "trails" else NAMED_PATH_HIGHWAYS
    return (*CATALOG_TAG_FILTERS, "w/highway=" + ",".join(highways), TRAILHEAD_TAG_FILTER)


def _catalog_filters_text(paths: str) -> str:
    return "\n".join(_catalog_filters(paths)) + "\n"


def _run_osmium_tags_filter(osmium_bin: str, source: Path, dest: Path, paths: str) -> None:
    if dest.name.endswith(".osm.pbf"):
        tmp = dest.with_name(dest.name[: -len(".osm.pbf")] + ".writing.osm.pbf")
    else:
        tmp = dest.with_name(dest.stem + ".writing.osm.pbf")
    cmd = [
        osmium_bin,
        "tags-filter",
        str(source.resolve()),
        *_catalog_filters(paths),
        "-o",
        str(tmp),
        "--overwrite",
    ]
    cmd.append("--progress" if _stderr_is_tty() else "--no-progress")
    try:
        subprocess.run(cmd, check=True)
        tmp.replace(dest)
        _catalog_filters_path(dest).write_text(_catalog_filters_text(paths))
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


def _file_state(conn: sqlite3.Connection, pass_name: str, path: Path) -> tuple[int | None, bool]:
    # The last id read at the file's checkpoint (None: none yet), and whether the file is done.
    row = conn.execute(
        "SELECT last_id, done FROM file_progress WHERE pass = ? AND path = ?",
        (pass_name, str(path)),
    ).fetchone()
    if row is None:
        return None, False
    return int(row[0]), bool(row[1])


def _after(osm_id: int, last_id: int | None) -> bool:
    """Whether ``osm_id`` comes after the checkpoint ``last_id`` (None: no
    checkpoint) in a sorted OSM file: osmium sorts negative ids (new objects
    in a JOSM file) first, by absolute value, then positive ones."""
    return last_id is None or (osm_id > 0, abs(osm_id)) > (last_id > 0, abs(last_id))


def _mark_progress(
    conn: sqlite3.Connection, pass_name: str, path: Path, last_id: int | None, done: bool = False
) -> None:
    # Only a file with no object of the pass is done with no last id; a done file's is never read.
    conn.execute(
        "INSERT INTO file_progress (pass, path, last_id, done) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(pass, path) DO UPDATE SET last_id = excluded.last_id, "
        "done = excluded.done",
        (pass_name, str(path), 0 if last_id is None else last_id, int(done)),
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
    pending = _Pending(conn)
    for path in paths:
        last_id, done = _file_state(conn, "relations", path)
        if done:
            continue
        file_last = last_id
        for obj in _iter_file(path, osmium.osm.RELATION):
            if not obj.is_relation() or not _after(int(obj.id), last_id):
                continue
            tags = dict(obj.tags)
            members = [
                [_MEMBER_TYPE.get(str(member.type), str(member.type)), int(member.ref)]
                for member in obj.members
            ]
            roles = [str(member.role) for member in obj.members]
            osm_id = int(obj.id)
            if tags.get("route") in HIKING_ROUTES:
                pending.add(
                    "INSERT OR REPLACE INTO relations (id, members, roles, tags) VALUES (?, ?, ?, ?)",
                    osm_id, json.dumps(members), json.dumps(roles), json.dumps(tags),
                )  # fmt: skip
                pending.add_ids("needed_ways", [member_id for member_type, member_id in members if member_type == "way"])
                hiking += 1
            kind = _point_kind(tags)
            if kind is not None:
                pending.add(
                    "INSERT OR REPLACE INTO pending_amenity_relations (id, kind, members, tags) VALUES (?, ?, ?, ?)",
                    osm_id, kind, json.dumps(members), json.dumps(tags),
                )  # fmt: skip
                pending.add_ids("amenity_ways", [member_id for member_type, member_id in members if member_type == "way"])
                pending.add_ids("needed_nodes", [member_id for member_type, member_id in members if member_type == "node"])
            seen += 1
            file_last = osm_id
            if seen % INGEST_CHECKPOINT_OBJECTS == 0:
                pending.write()
                _mark_progress(conn, "relations", path, file_last)
            if seen % INGEST_PROGRESS_EVERY_OBJECTS == 0:
                emit_progress(
                    progress, "relations", f"{seen} scanned, {hiking} hiking, last_id={osm_id}"
                )
        pending.write()
        _mark_progress(conn, "relations", path, file_last, done=True)
    emit_progress(progress, "relations", f"{hiking} hiking relations")


def _pass_ways(
    conn: sqlite3.Connection, paths: Sequence[Path], progress: Progress | None, levels: Sequence[str]
) -> None:
    """The member ways of hiking relations, amenity ways and, per file at its
    ``levels`` entry, the paths in no relation. Those of a file read beyond
    ``graded`` are also marked to enter the snapshot (``snapshot_path_ways``),
    so a ``graded`` file of a mixed build (Europe beside the US) gives the
    snapshot what a build of it alone would."""
    emit_progress(progress, "ways", "starting")
    if _pass_done(conn, "ways", paths):
        emit_progress(progress, "ways", "resumed, already complete")
        return
    needed_ways = _sorted_ids(conn, "needed_ways")
    amenity_ways = _sorted_ids(conn, "amenity_ways")
    seen = 0
    kept = 0
    extra = 0
    pending = _Pending(conn)
    for path, path_level in zip(paths, levels):
        last_id, done = _file_state(conn, "ways", path)
        if done:
            continue
        file_last = last_id
        for obj in _iter_file(path, osmium.osm.WAY):
            if not obj.is_way() or not _after(int(obj.id), last_id):
                continue
            osm_id = int(obj.id)
            node_ids = [int(node.ref) for node in obj.nodes]
            tags = dict(obj.tags)
            kind = _point_kind(tags)
            if _sorted_has(needed_ways, osm_id):
                pending.add(
                    "INSERT OR REPLACE INTO ways (id, node_ids, tags) VALUES (?, ?, ?)",
                    osm_id, json.dumps(node_ids), json.dumps(tags),
                )
                pending.add_ids("needed_nodes", node_ids)
                kept += 1
            elif _is_extra_path(tags, path_level):
                pending.add(
                    "INSERT OR REPLACE INTO path_ways (id, node_ids, tags) VALUES (?, ?, ?)",
                    osm_id, json.dumps(node_ids), json.dumps(tags),
                )
                pending.add_ids("path_needed_nodes", node_ids)
                if path_level != "graded":
                    pending.add_ids("snapshot_path_ways", [osm_id])
                    pending.add_ids("snapshot_path_nodes", node_ids)
                extra += 1
            if node_ids and (kind is not None or _sorted_has(amenity_ways, osm_id)):
                pending.add(
                    "INSERT OR REPLACE INTO pending_amenity_ways (id, kind, node_id, tags) VALUES (?, ?, ?, ?)",
                    osm_id, kind or "", node_ids[0], json.dumps(tags),
                )
                pending.add_ids("needed_nodes", node_ids[:1])
            seen += 1
            file_last = osm_id
            if seen % INGEST_CHECKPOINT_OBJECTS == 0:
                pending.write()
                _mark_progress(conn, "ways", path, file_last)
            if seen % INGEST_PROGRESS_EVERY_OBJECTS == 0:
                emit_progress(
                    progress, "ways", f"{seen} scanned, {kept} hiking ways, last_id={osm_id}"
                )
        pending.write()
        _mark_progress(conn, "ways", path, file_last, done=True)
    read = ", ".join(dict.fromkeys(levels))
    emit_progress(progress, "ways", f"{kept} hiking ways, {extra} paths in no relation ({read})")


def _point_kind(tags: dict[str, str]) -> str | None:
    """The amenity kind of an OSM object, else ``lake`` for a named lake
    (``LAKE_WATER``), ``guidepost`` for a named guidepost or ``trailhead``
    for a ``highway=trailhead``."""
    kind = amenity_kind(tags)
    if kind is not None:
        return kind
    if tags.get("name"):
        if tags.get("natural") == "water" and tags.get("water") in LAKE_WATER:
            return "lake"
        if tags.get("information") == "guidepost":
            return "guidepost"
    if tags.get("highway") == "trailhead":
        return "trailhead"
    return None


def _is_graded_path(tags: dict[str, str]) -> bool:
    """A ``highway=path`` with a ``sac_scale`` grade: in the TrailGraph though no relation lists it (ADR-0037)."""
    return tags.get("highway") == "path" and tags.get("sac_scale") in SAC_SCALE_RANK


def _is_extra_path(tags: dict[str, str], level: str) -> bool:
    """A way no hiking relation lists that joins the TrailGraph at this ``PATH_LEVELS`` level (ADR-0065)."""
    if _is_graded_path(tags):
        return True
    if level == "graded" or tags.get("footway") in NOT_A_PATH_FOOTWAYS:
        return False
    highway = tags.get("highway")
    named = TRAIL_PATH_HIGHWAYS if level == "trails" else NAMED_PATH_HIGHWAYS
    return (bool(tags.get("name")) and highway in named) or (level == "unnamed" and highway == "path")


def _pass_nodes(
    conn: sqlite3.Connection, paths: Sequence[Path], progress: Progress | None
) -> None:
    emit_progress(progress, "nodes", "starting")
    if _pass_done(conn, "nodes", paths):
        emit_progress(progress, "nodes", "resumed, already complete")
        # A run stopped while resolving left no amenity of a way or relation:
        # resolve again, as replacing rows it does no harm when it had finished.
        _resolve_pending_amenities(conn)
        return
    needed_nodes = _sorted_ids(conn, "needed_nodes")
    path_nodes = _sorted_ids(conn, "path_needed_nodes")
    seen = 0
    kept = 0
    pending = _Pending(conn)
    for path in paths:
        last_id, done = _file_state(conn, "nodes", path)
        if done:
            continue
        file_last = last_id
        for obj in _iter_file(path, osmium.osm.NODE):
            if not obj.is_node() or not _after(int(obj.id), last_id):
                continue
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
                    )
                    kept += 1
                elif on_path:
                    pending.add(
                        "INSERT OR REPLACE INTO path_nodes (id, lat, lon, ele) VALUES (?, ?, ?, ?)",
                        osm_id, point[0], point[1], _parse_ele_m(tags.get("ele")),
                    )
                if kind is not None:
                    pending.add(
                        "INSERT OR REPLACE INTO amenities (osm_key, kind, lat, lon, tags) VALUES (?, ?, ?, ?, ?)",
                        f"n{osm_id}", kind, point[0], point[1], json.dumps(tags),
                    )
            seen += 1
            file_last = osm_id
            if seen % INGEST_CHECKPOINT_OBJECTS == 0:
                pending.write()
                _mark_progress(conn, "nodes", path, file_last)
            if seen % INGEST_PROGRESS_EVERY_OBJECTS == 0:
                emit_progress(
                    progress, "nodes", f"{seen} scanned, {kept} kept, last_id={osm_id}"
                )
        pending.write()
        _mark_progress(conn, "nodes", path, file_last, done=True)
    _resolve_pending_amenities(conn)
    emit_progress(progress, "nodes", f"{kept} geometry nodes")


def _sorted_has(ids: np.ndarray, osm_id: int) -> bool:
    at = int(ids.searchsorted(osm_id))
    return at < len(ids) and int(ids[at]) == osm_id


def _resolve_pending_amenities(conn: sqlite3.Connection) -> None:
    """The amenities mapped as ways or relations, each at its first node:
    looked up in the work file, not loaded whole (it holds every geometry node)."""

    def node_coords(node_id: int) -> tuple[float, float] | None:
        return conn.execute("SELECT lat, lon FROM nodes WHERE id = ?", (node_id,)).fetchone()

    conn.execute(
        "INSERT OR REPLACE INTO amenities (osm_key, kind, lat, lon, tags) "
        "SELECT 'w' || way.id, way.kind, node.lat, node.lon, way.tags "
        "FROM pending_amenity_ways AS way JOIN nodes AS node ON node.id = way.node_id "
        "WHERE way.kind != '' ORDER BY way.id"
    )
    for rel_id, kind, members_json, tags_json in conn.execute(
        "SELECT id, kind, members, tags FROM pending_amenity_relations"
    ):
        point = None
        for member_type, member_id in json.loads(members_json):
            if member_type == "node":
                point = node_coords(member_id)
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
                        point = node_coords(pending[0])
                else:
                    node_ids = json.loads(row[0])
                    if node_ids:
                        point = node_coords(node_ids[0])
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
    conn: sqlite3.Connection, progress: Progress | None = None, with_paths: bool = False
) -> OsmSnapshot:
    """The snapshot of the hiking tables; ``with_paths``, also of the paths in no
    hiking relation read from a file beyond the ``graded`` level
    (``snapshot_path_ways``) and the nodes only they use."""
    snapshot = OsmSnapshot()
    emit_progress(progress, "assemble", "loading nodes")
    nodes_sql = "SELECT id, lat, lon, ele FROM nodes"
    if with_paths:
        nodes_sql += (
            " UNION ALL SELECT id, lat, lon, ele FROM path_nodes WHERE id IN (SELECT id FROM snapshot_path_nodes)"
        )
    store = NodeStore.from_rows(conn.execute(nodes_sql + " ORDER BY id"))
    snapshot.nodes, snapshot.node_elevations = store.points, store.elevations
    loaded = 0
    ways_sql = ["SELECT id, node_ids, tags FROM ways"]
    if with_paths:
        ways_sql.append("SELECT id, node_ids, tags FROM path_ways WHERE id IN (SELECT id FROM snapshot_path_ways)")
    for sql in ways_sql:
        for way_id, node_ids_json, tags_json in conn.execute(sql):
            snapshot.ways[way_id] = OsmWay(tuple(json.loads(node_ids_json)), json.loads(tags_json))
            loaded += 1
            if loaded % INGEST_PROGRESS_EVERY_OBJECTS == 0:
                emit_progress(progress, "assemble", f"loading ways {loaded}")
    if with_paths:
        snapshot.path_ways = frozenset(way_id for (way_id,) in conn.execute("SELECT id FROM snapshot_path_ways"))
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
        if name and kind in DESTINATION_KINDS:
            snapshot.destinations.append((lat, lon, name))
        if name and kind == "guidepost":
            snapshot.guideposts.append((lat, lon, name))
        if kind == "trailhead":
            snapshot.trailheads.append((lat, lon))
        if kind in NETWORK_ONLY_KINDS:
            continue
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
    walks: dict[int, _StoredWalk] | None = None,
    lower: LowerStart | None = None,
):
    """The relation DayHikes, by id, and per drop reason how many relations
    it left out. ``walks``, when given, gets each kept DayHike's main walk
    (see ``_store_walk``)."""
    report_counts = {"length": 0, "container": 0, "node_network": 0}
    seen: set[int] = set()
    hiking = []
    for relation in snapshot.relations:
        if relation.id in seen:
            continue
        seen.add(relation.id)
        if relation.tags.get("route") in HIKING_ROUTES:
            hiking.append(relation)

    def keep(relation: OsmRelation) -> tuple[DayHike, _StoredWalk | None] | str:
        # The relation's DayHike and its stored main walk, or why it is dropped.
        walk = _main_walk(relation, snapshot, access_tree, named, lower)
        vertices = walk.vertices
        points = [point for _, point in vertices]
        length_m = walk.length_m
        round_trip = bool(points) and _haversine_m(*points[0], *points[-1]) <= GAP_JOIN_M
        reason = drop_reason(relation, length_m, round_trip)
        if reason is not None:
            return reason
        hike = dayhike_from_relation(relation, snapshot, vertices, points, length_m, amenity_index, places)
        return hike, _store_walk(walk) if walks is not None else None

    kept = []
    # Each relation is walked in a worker; only its DayHike and stored walk come back.
    for result in parallel_map(keep, hiking):
        if isinstance(result, str):
            report_counts[result] += 1
            continue
        hike, stored = result
        kept.append(hike)
        if walks is not None:
            walks[hike.id] = stored
    kept.sort(key=lambda hike: hike.id)
    return kept, report_counts


def _build_hikes(
    conn: sqlite3.Connection,
    sources: Sequence[BuiltHikeSource],
    context: BuildContext,
    amenity_index,
) -> list[DayHike]:
    """The built DayHikes of every source, a walk built twice kept once. Their
    vertices and ways wait in the work file for the GeometryStore."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS built_walks (id INTEGER PRIMARY KEY, vertices TEXT NOT NULL, ways TEXT NOT NULL)"
    )
    conn.execute("DELETE FROM built_walks")
    built: dict[int, DayHike] = {}
    for source in sources:
        walks = list(source(context))
        _add_read_ways(conn, context.snapshot, walks)
        context = replace(context, built=(*context.built, *walks))
        made = parallel_map(lambda walk: built_dayhike(walk, context.snapshot, amenity_index), walks)
        for walk, (hike, walked) in zip(walks, made):
            if hike.id in built:
                continue
            built[hike.id] = hike
            conn.execute(
                "INSERT INTO built_walks (id, vertices, ways) VALUES (?, ?, ?)",
                (
                    hike.id,
                    json.dumps([[node_id, lat, lon] for node_id, (lat, lon) in walk.vertices]),
                    json.dumps(walked),
                ),
            )
    conn.commit()
    return sorted(built.values(), key=lambda hike: hike.id)


def _add_read_ways(conn: sqlite3.Connection, snapshot: OsmSnapshot, walks: Sequence[BuiltWalk]) -> None:
    """The ways built walks read beyond the snapshot (DestinationHikes, ADR-0067):
    into the snapshot, so they are built and stored like any other, and into the
    work file's path tables, so the TrailGraph holds them for "Edit on the map"."""
    ways = {way_id: way for walk in walks for way_id, way in walk.ways.items() if way_id not in snapshot.ways}
    if not ways:
        return
    nodes = {node: point for walk in walks for node, point in walk.nodes.items() if node not in snapshot.nodes}
    snapshot.ways.update(ways)
    conn.executemany(
        "INSERT OR IGNORE INTO path_ways (id, node_ids, tags) VALUES (?, ?, ?)",
        [(way_id, json.dumps(list(way.node_ids)), json.dumps(way.tags)) for way_id, way in ways.items()],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO path_nodes (id, lat, lon, ele) VALUES (?, ?, ?, NULL)",
        [(node, lat, lon) for node, (lat, lon) in nodes.items()],
    )


def _add_external(
    conn: sqlite3.Connection,
    external: Sequence[tuple[str, Sequence[ExternalHike]]],
    relation_hikes: Sequence[DayHike],
    built_hikes: Sequence[DayHike],
    walks: dict[int, _StoredWalk],
    snapshot: OsmSnapshot,
    amenity_index,
) -> tuple[list[DayHike], dict[str, int]]:
    """The DayHikes of the external hikes (ADR-0062) and how many were added
    and dropped. One under 1 km is dropped, and so is a Twin of a relation
    DayHike (the Twin rule: the OSM hike is listed) or of an external hike kept
    before it (counted apart, as a duplicate). A record with no name of its own
    is named after the nearest place (ADR-0017). A kept hike longer than
    ``STAGE_SPLIT_M`` is also cut into DayStages (ADR-0040) as a relation DayHike
    is; each stage is a hike of its own, dropped as a Twin of a relation DayHike,
    of a built DayStage in ``built_hikes`` or of an external hike kept before it.
    Their lines wait in the work file for the GeometryStore."""
    counts = {"added": 0, "stages": 0, "twin": 0, "duplicate": 0, "length": 0}
    places = named_points(snapshot.places)
    candidates: list[tuple[str, ExternalHike]] = []
    for source_id, hikes in external:
        for hike in hikes:
            if long_enough(hike):
                place = None if places is None else places.nearest(*hike.points[0], PLACE_RADIUS_M)
                name, name_origin = external_label(hike, place, source_id)
                candidates.append((source_id, replace(hike, name=name, name_origin=name_origin)))
            else:
                counts["length"] += 1
    conn.execute(
        "CREATE TABLE IF NOT EXISTS built_walks (id INTEGER PRIMARY KEY, vertices TEXT NOT NULL, ways TEXT NOT NULL)"
    )
    conn.execute("DELETE FROM built_walks WHERE id >= ? AND id < ?", (EXTERNAL_ID_MIN, EXTERNAL_ID_MAX))
    if not candidates:
        conn.commit()
        return [], counts
    routes = [_twin_route(hike.points) for _, hike in candidates]
    given = _given_routes(conn, routes, relation_hikes, (), walks, snapshot)
    # A group's root is its first row, so a group holding a relation has a root below len(given).
    groups = twin_groups([*given, *routes])[len(given) :]
    kept: list[tuple[str, ExternalHike]] = []
    kept_routes: list[np.ndarray] = []
    kept_groups: set[int] = set()
    for (source_id, hike), route, group in zip(candidates, routes, groups):
        if group < len(given):
            counts["twin"] += 1
            continue
        if group in kept_groups:
            counts["duplicate"] += 1
            continue
        kept_groups.add(int(group))
        kept.append((source_id, hike))
        kept_routes.append(route)
    # DayStages of the kept hikes. Each is compared with the relation DayHikes and built DayStages near
    # it and with the hikes kept above, in front of every stage, so a stage is dropped as a Twin of one of them
    # and of a stage kept before it.
    stages = external_stages(kept, snapshot, places)
    added = list(kept)
    if stages:
        stage_routes = [_twin_route(stage.points) for _, stage in stages]
        given = _given_routes(conn, stage_routes, relation_hikes, built_hikes, walks, snapshot)
        groups = twin_groups([*given, *kept_routes, *stage_routes])[len(given) + len(kept_routes) :]
        kept_groups = set()
        for (source_id, stage), group in zip(stages, groups):
            if group < len(given):
                counts["twin"] += 1
            elif group < len(given) + len(kept_routes) or group in kept_groups:
                counts["duplicate"] += 1
            else:
                kept_groups.add(int(group))
                added.append((source_id, stage))
    dayhikes: dict[int, DayHike] = {}
    for source_id, hike in added:
        dayhike = external_dayhike(source_id, hike, snapshot, amenity_index)
        if dayhike.id in dayhikes:
            continue
        dayhikes[dayhike.id] = dayhike
        counts["stages"] += hike.stage is not None
        conn.execute(
            "INSERT INTO built_walks (id, vertices, ways) VALUES (?, ?, ?)",
            (dayhike.id, json.dumps([[0, lat, lon] for lat, lon in hike.points]), json.dumps([])),
        )
    conn.commit()
    counts["added"] = len(dayhikes)
    return sorted(dayhikes.values(), key=lambda hike: hike.id), counts


def _given_routes(
    conn: sqlite3.Connection,
    routes: Sequence[np.ndarray],
    relation_hikes: Sequence[DayHike],
    built_hikes: Sequence[DayHike],
    walks: dict[int, _StoredWalk],
    snapshot: OsmSnapshot,
) -> list[np.ndarray]:
    """The Twin-comparison routes of the relation DayHikes, and of the built
    DayStages in ``built_hikes``, that start near ``routes`` and have a length
    that allows a Twin of one of them: the only ones an external hike is compared with."""
    lengths = [float(_step_m(route[:, 0], route[:, 1]).sum()) for route in routes]
    reach_m = max(lengths) / TWIN_SHARE
    points = np.concatenate(routes)
    south, west = points.min(axis=0)
    north, east = points.max(axis=0)
    pad_lat = reach_m / 111_320
    pad_lon = reach_m / (111_320 * max(0.1, np.cos(np.radians(max(abs(south), abs(north))))))

    def near(hike: DayHike) -> bool:
        return (
            TWIN_SHARE * min(lengths) <= hike.length_m <= reach_m
            and south - pad_lat <= hike.trailhead.latitude <= north + pad_lat
            and west - pad_lon <= hike.trailhead.longitude <= east + pad_lon
        )

    given = [
        _twin_route([point for _, point in _stored_walk(walks[hike.id], snapshot.nodes).vertices])
        for hike in relation_hikes
        if hike.id in walks and near(hike)
    ]
    for hike in built_hikes:
        if hike.kind == "stage" and near(hike):
            (vertices,) = conn.execute("SELECT vertices FROM built_walks WHERE id = ?", (hike.id,)).fetchone()
            given.append(_twin_route([(lat, lon) for _, lat, lon in json.loads(vertices)]))
    return given


def _twin_route(points: Sequence[tuple[float, float]]) -> np.ndarray:
    # The line as (lat, lon) rows every TWIN_STEP_M, as Twins are compared.
    array = np.asarray(points, dtype=float)
    return _even_points(array[:, 0], array[:, 1], TWIN_STEP_M)


# A MainWalk held compactly: its vertices as node ids (their points are the
# snapshot's), its per-listing lists as arrays, walk_order None as -1. Europe's
# relation DayHikes walk 71 M vertices, too many to hold as Python tuples.
_StoredWalk = tuple[np.ndarray, float, np.ndarray, float, tuple[str | None, ...], np.ndarray, np.ndarray]


def _store_walk(walk: MainWalk) -> _StoredWalk:
    return (
        np.array([node_id for node_id, _ in walk.vertices], dtype=np.int64),
        walk.length_m,
        np.array(walk.in_chain, dtype=bool),
        walk.unconnected_m,
        tuple(walk.leftovers),
        np.array(walk.walked_m, dtype=np.float64),
        np.array([-1 if order is None else order for order in walk.walk_order], dtype=np.int64),
    )


def _stored_walk(stored: _StoredWalk, nodes) -> MainWalk:
    """The MainWalk ``_store_walk`` held, equal to the one it was given."""
    node_ids, length_m, in_chain, unconnected_m, leftovers, walked_m, walk_order = stored
    return MainWalk(
        [(node_id, nodes[node_id]) for node_id in node_ids.tolist()],
        length_m,
        in_chain.tolist(),
        unconnected_m,
        list(leftovers),
        walked_m.tolist(),
        [None if order < 0 else order for order in walk_order.tolist()],
    )


def _lower_start(
    snapshot: OsmSnapshot, heights: Heights | None, previous: PreviousStarts | None = None
) -> LowerStart | None:
    """The height rule of ingest's lines (ADR-0048): parking and places reach an end."""
    if heights is None:
        return None
    access = [*snapshot.access_points, *((lat, lon) for lat, lon, _ in snapshot.places)]
    return LowerStart(heights, access, previous)


def _main_walk(
    relation: OsmRelation,
    snapshot: OsmSnapshot,
    access_tree,
    named: NamedPoints | None,
    lower: LowerStart | None = None,
) -> MainWalk:
    """The relation's main walk, its line started where its from/to tags say if
    they can, else at its lower end if ``lower`` can tell (ADR-0048), else,
    when it lacks a from or to tag, where its dashed name says if it can."""
    tags = relation.tags
    tagged = tagged_ends(tags)
    ways = [snapshot.ways[way_id] for kind, way_id in relation.members if kind == "way" and way_id in snapshot.ways]
    declared = first_choice(
        declared_start(tagged, named),
        None if lower is None else lower.choice(ways, snapshot.nodes),
        None if tagged else declared_start(dashed_ends(tags), named),
    )
    return assemble_main_walk(relation, snapshot.ways, snapshot.nodes, access_tree, declared)


def write_geometry_store(
    path: Path,
    dayhikes: Sequence[DayHike],
    conn: sqlite3.Connection,
    snapshot: OsmSnapshot | None = None,
    heights: Heights | None = None,
    dem_read: str = "",
    previous: PreviousStarts | None = None,
) -> None:
    """Write DayHikeGeometry, member ways, and raw amenity points beside the Catalog.

    Access points, named points and ``heights`` are those of ``run_ingest``,
    so each main walk starts at the same Trailhead as in the Catalog;
    ``snapshot`` is the one ``run_ingest`` loaded from it, else it is loaded again.
    ``dem_read`` names the DEM sources ``heights`` read (``dem_read_by``); it is recorded
    in ``geometry_meta`` unless empty.
    """
    from dayhike.catalog import OSM_ATTRIBUTION

    if snapshot is None:
        snapshot = _load_hiking_snapshot(conn)
    relations = {relation.id: relation for relation in snapshot.relations}
    access_tree = _access_tree(snapshot.access_points)
    named = named_points(snapshot.named_points)
    lower = _lower_start(snapshot, heights, previous)
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
        if dem_read:
            out.execute("INSERT INTO geometry_meta (key, value) VALUES (?, ?)", (DEM_READ_KEY["ingest"], dem_read))
        relation_hikes = [hike for hike in dayhikes if hike.kind == KIND_RELATION]
        # The main walks are assembled again in the workers; the rows are written here, in hike order.
        relation_rows = parallel_iter(
            lambda hike: _relation_geometry_rows(hike, relations[hike.id], snapshot, access_tree, named, lower),
            relation_hikes,
        )
        for hike in dayhikes:
            if hike.kind != KIND_RELATION:
                _write_built_geometry(out, hike, snapshot, conn)
                continue
            dayhike_row, vertex_rows, way_rows = next(relation_rows)
            out.execute(
                "INSERT INTO dayhikes (id, name, name_origin, length_m, unconnected_m, "
                "difficulty, members, relation_tags) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                dayhike_row,
            )
            out.executemany(
                "INSERT INTO vertices (dayhike_id, seq, node_id, latitude, longitude, "
                "ele, landcover) VALUES (?, ?, ?, ?, ?, ?, ?)",
                vertex_rows,
            )
            out.executemany(
                "INSERT INTO ways (dayhike_id, seq, way_id, node_ids, tags, in_chain, "
                "leftover, walked_m, walk_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                way_rows,
            )
        relation_rows.close()  # every row is taken: stop the workers now
        amenity_rows = list(
            conn.execute(
                f"SELECT osm_key, kind, lat, lon, tags FROM amenities WHERE kind NOT IN ({','.join('?' * len(NETWORK_ONLY_KINDS))})",
                NETWORK_ONLY_KINDS,
            )
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


def _relation_geometry_rows(
    hike: DayHike,
    relation: OsmRelation,
    snapshot: OsmSnapshot,
    access_tree,
    named: NamedPoints | None,
    lower: LowerStart | None = None,
) -> tuple[tuple, list[tuple], list[tuple]]:
    """A relation DayHike's rows of the GeometryStore: its dayhikes row, its
    main walk's vertices and its member ways."""
    walk = _main_walk(relation, snapshot, access_tree, named, lower)
    dayhike_row = (
        hike.id,
        hike.name,
        hike.name_origin,
        hike.length_m,
        walk.unconnected_m,
        hike.difficulty.sac_scale,
        json.dumps([list(member) for member in relation.members]),
        json.dumps(relation.tags),
    )
    vertex_rows = [
        (hike.id, seq, node_id, lat, lon, snapshot.node_elevations.get(node_id), None)
        for seq, (node_id, (lat, lon)) in enumerate(walk.vertices)
    ]
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
    return dayhike_row, vertex_rows, way_rows


def _write_built_geometry(
    out: sqlite3.Connection, hike: DayHike, snapshot: OsmSnapshot, conn: sqlite3.Connection
) -> None:
    """A built DayHike's rows of the GeometryStore: its walk as ``_build_hikes``
    kept it, every way it walks once, in walk order, and no Leftover."""
    vertices_json, ways_json = conn.execute(
        "SELECT vertices, ways FROM built_walks WHERE id = ?", (hike.id,)
    ).fetchone()
    walked = json.loads(ways_json)
    out.execute(
        "INSERT INTO dayhikes (id, name, name_origin, length_m, unconnected_m, "
        "difficulty, members, relation_tags) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            hike.id,
            hike.name,
            hike.name_origin,
            hike.length_m,
            0.0,
            hike.difficulty.sac_scale,
            json.dumps([["way", way_id] for way_id, _ in walked]),
            json.dumps({}),
        ),
    )
    out.executemany(
        "INSERT INTO vertices (dayhike_id, seq, node_id, latitude, longitude, "
        "ele, landcover) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (hike.id, seq, node_id, lat, lon, snapshot.node_elevations.get(node_id), None)
            for seq, (node_id, lat, lon) in enumerate(json.loads(vertices_json))
        ],
    )
    out.executemany(
        "INSERT INTO ways (dayhike_id, seq, way_id, node_ids, tags, in_chain, "
        "leftover, walked_m, walk_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                hike.id,
                seq,
                way_id,
                json.dumps(list(snapshot.ways[way_id].node_ids)),
                json.dumps(snapshot.ways[way_id].tags),
                1,
                None,
                walked_m,
                seq,
            )
            for seq, (way_id, walked_m) in enumerate(walked)
        ],
    )
