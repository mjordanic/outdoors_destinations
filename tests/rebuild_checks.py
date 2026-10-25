"""Comparison helpers for the clean-rebuild checks (README "Clean rebuild").

The slow ``real_data`` tests apply these to a rebuilt data directory and to the
reference (``DAYHIKE_REFERENCE_DIR``); tests/test_rebuild_checks.py runs them
on tiny fixtures in the normal suite. ``tests/rebuild/capture_golden.py``
writes the reference side once, before the rebuild.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("DAYHIKE_DATA_DIR", REPO / "data"))
REFERENCE_DIR = (
    Path(os.environ["DAYHIKE_REFERENCE_DIR"]) if os.environ.get("DAYHIKE_REFERENCE_DIR") else None
)
VALHALLA_URL = os.environ.get("DAYHIKE_VALHALLA_URL", "http://127.0.0.1:8002")
EUROPE_BOUNDS = (32.6, -31.3, 71.2, 46.4)  # south, west, north, east (README Europe walkthrough)
# FABDEM tiles that carry vertices but are 404 on the mirror (checked 2026-10-25).
KNOWN_DEM_GAPS = frozenset({"N41E043", "N41E044", "N41E046"})
# Geofabrik "europe" children that group other children.
COMPOSITE_REGIONS = frozenset({"alps", "dach", "britain-and-ireland", "great-britain"})
CHUNK = 2_000
# Tables and columns of a Catalog and its GeometryStore after ingest, the three
# sample steps and build-similarity (tests/test_pipeline_rebuild.py checks it).
CATALOG_SCHEMA = {
    "amenity_facts": ["dayhike_id", "kind", "count", "nearest_m"],
    "catalog_meta": ["key", "value"],
    "climate_months": ["dayhike_id", "month", "temperature_c", "precipitation_mm", "solar_radiation"],
    "dayhikes": ["id", "name", "name_origin", "length_m", "difficulty"],
    "feature_vectors": [
        "dayhike_id", "length_m", "elevation_min_m", "elevation_max_m", "elevation_gain_m",
        "difficulty_ordinal", "tree_fraction", "shrub_fraction", "grass_fraction",
        "bare_fraction", "snow_fraction", "temperature_c", "precipitation_mm", "solar_radiation",
    ],
    "labels": ["hiker_id", "dayhike_id", "score", "created_at"],
    "trailheads": ["dayhike_id", "latitude", "longitude"],
}  # fmt: skip
GEOMETRY_SCHEMA = {
    "amenities": ["osm_key", "kind", "latitude", "longitude", "tags"],
    "dayhikes": [
        "id", "name", "name_origin", "length_m", "unconnected_m", "difficulty", "members",
        "relation_tags",
    ],
    "geometry_meta": ["key", "value"],
    "vertex_climate": ["dayhike_id", "seq", "month", "temperature_c", "precipitation_mm", "solar_radiation"],
    "vertices": ["dayhike_id", "seq", "node_id", "latitude", "longitude", "ele", "landcover"],
    "ways": [
        "dayhike_id", "seq", "way_id", "node_ids", "tags", "in_chain", "leftover", "walked_m",
        "walk_order",
    ],
}  # fmt: skip

real_data = [
    pytest.mark.real_data,
    pytest.mark.skipif(
        os.environ.get("DAYHIKE_REAL_DATA") != "1", reason="real data: set DAYHIKE_REAL_DATA=1"
    ),
]
needs_reference = pytest.mark.skipif(
    REFERENCE_DIR is None, reason="no reference: set DAYHIKE_REFERENCE_DIR"
)


def connect_ro(path: Path, *, immutable: bool = False) -> sqlite3.Connection:
    """Open SQLite read-only; ``immutable`` also skips locking (for the frozen reference)."""
    if not Path(path).is_file():
        raise FileNotFoundError(path)
    flags = "mode=ro&immutable=1" if immutable else "mode=ro"
    return sqlite3.connect(f"file:{path}?{flags}", uri=True)


def schema(connection: sqlite3.Connection) -> dict[str, list[str]]:
    """Table name -> column names, for every table."""
    tables = [
        name
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
    ]
    return {
        table: [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
        for table in tables
    }


@dataclass
class TableDiff:
    """Rows of one table that differ between an old and a new SQLite file, by key."""

    left: list[tuple] = field(default_factory=list)
    joined: list[tuple] = field(default_factory=list)
    changed: list[tuple[tuple, tuple, tuple]] = field(default_factory=list)

    @property
    def equal(self) -> bool:
        return not (self.left or self.joined or self.changed)

    def report(self, limit: int = 20) -> str:
        lines = [f"left {len(self.left)}, joined {len(self.joined)}, changed {len(self.changed)}"]
        lines += [f"  left   {key}" for key in self.left[:limit]]
        lines += [f"  joined {key}" for key in self.joined[:limit]]
        lines += [f"  changed {key}: {old} -> {new}" for key, old, new in self.changed[:limit]]
        return "\n".join(lines)


def compare_tables(
    old: sqlite3.Connection,
    new: sqlite3.Connection,
    table: str,
    key: Sequence[str],
    columns: Sequence[str],
    *,
    abs_tol: float = 0.0,
    where: str = "",
) -> TableDiff:
    """Compare ``columns`` of ``table`` row by row on ``key``, in key order.

    Floats differ only beyond ``abs_tol``; NULL equals only NULL. Both sides
    stream in key order, ``CHUNK`` rows at a time.
    """
    select = (
        f"SELECT {', '.join([*key, *columns])} FROM {table} {where} "
        f"ORDER BY {', '.join(key)}"
    )
    width = len(key)
    diff = TableDiff()
    old_rows, new_rows = _stream(old, select), _stream(new, select)
    old_row, new_row = next(old_rows, None), next(new_rows, None)
    while old_row is not None or new_row is not None:
        if new_row is None or (old_row is not None and old_row[:width] < new_row[:width]):
            diff.left.append(old_row[:width])
            old_row = next(old_rows, None)
        elif old_row is None or new_row[:width] < old_row[:width]:
            diff.joined.append(new_row[:width])
            new_row = next(new_rows, None)
        else:
            if not _same(old_row[width:], new_row[width:], abs_tol):
                diff.changed.append((old_row[:width], old_row[width:], new_row[width:]))
            old_row, new_row = next(old_rows, None), next(new_rows, None)
    return diff


def _stream(connection: sqlite3.Connection, select: str) -> Iterable[tuple]:
    cursor = connection.execute(select)
    while rows := cursor.fetchmany(CHUNK):
        yield from rows


def _same(old: Sequence, new: Sequence, abs_tol: float) -> bool:
    for a, b in zip(old, new, strict=True):
        if a is None or b is None:
            if a is not b:
                return False
        elif isinstance(a, float) or isinstance(b, float):
            if not math.isclose(a, b, rel_tol=0.0, abs_tol=abs_tol):
                return False
        elif a != b:
            return False
    return True


def raw_manifest(root: Path, relpaths: Iterable[str], *, hashed: Iterable[str] = ()) -> dict:
    """relpath -> (size, mtime_ns, sha256 or ""), following symlinks."""
    hashed = set(hashed)
    manifest = {}
    for relpath in sorted(relpaths):
        path = root / relpath
        stat = path.stat()
        digest = _sha256(path) if relpath in hashed else ""
        manifest[relpath] = (stat.st_size, stat.st_mtime_ns, digest)
    return manifest


def write_manifest(path: Path, manifest: Mapping[str, tuple[int, int, str]]) -> None:
    lines = ["path\tsize\tmtime_ns\tsha256"]
    lines += [f"{rel}\t{size}\t{mtime}\t{digest}" for rel, (size, mtime, digest) in manifest.items()]
    path.write_text("\n".join(lines) + "\n")


def read_manifest(path: Path) -> dict[str, tuple[int, int, str]]:
    manifest = {}
    for line in path.read_text().splitlines()[1:]:
        rel, size, mtime, digest = line.split("\t")
        manifest[rel] = (int(size), int(mtime), digest)
    return manifest


def manifest_diff(
    expected: Mapping[str, tuple[int, int, str]], actual: Mapping[str, tuple[int, int, str]]
) -> list[str]:
    """One line per expected file that is missing or differs in size, mtime or hash."""
    problems = []
    for rel, (size, mtime, digest) in sorted(expected.items()):
        if rel not in actual:
            problems.append(f"missing {rel}")
            continue
        got_size, got_mtime, got_digest = actual[rel]
        if got_size != size:
            problems.append(f"changed {rel}: size {size} -> {got_size}")
        elif got_mtime != mtime:
            problems.append(f"changed {rel}: mtime_ns {mtime} -> {got_mtime}")
        elif digest and got_digest != digest:
            problems.append(f"changed {rel}: sha256 {digest} -> {got_digest}")
    return problems


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1 << 24):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class CountryCoverage:
    """Trailheads in one country and how many of those hikes have each sampled value."""

    hikes: int
    landcover: int
    climate: int
    elevation: int


def europe_countries(index_path: Path) -> list[tuple[str, object]]:
    """(id, polygon) of each Geofabrik ``europe`` child in index-v1.json, composites left out."""
    from shapely.geometry import shape

    features = json.loads(Path(index_path).read_text())["features"]
    return sorted(
        (feature["properties"]["id"], shape(feature["geometry"]))
        for feature in features
        if feature["properties"].get("parent") == "europe"
        and feature["properties"]["id"] not in COMPOSITE_REGIONS
    )


def trailhead_coverage(catalog: sqlite3.Connection) -> np.ndarray:
    """Rows of (latitude, longitude, has LandCover, has climate, has elevation), by id."""
    rows = catalog.execute(
        "SELECT t.latitude, t.longitude, f.tree_fraction IS NOT NULL, "
        "f.temperature_c IS NOT NULL, f.elevation_gain_m IS NOT NULL "
        "FROM trailheads t JOIN feature_vectors f USING (dayhike_id) ORDER BY t.dayhike_id"
    ).fetchall()
    return np.array(rows, dtype=float).reshape(-1, 5)


def coverage_by_country(
    countries: Sequence[tuple[str, object]], rows: np.ndarray
) -> tuple[dict[str, CountryCoverage], int]:
    """Coverage per country (a Trailhead on a shared border counts in both) and unassigned count."""
    import shapely

    points = shapely.points(rows[:, 1], rows[:, 0])
    assigned = np.zeros(len(rows), dtype=bool)
    out = {}
    for name, polygon in countries:
        shapely.prepare(polygon)
        inside = shapely.contains(polygon, points)
        assigned |= inside
        out[name] = CountryCoverage(int(inside.sum()), *(int(rows[inside, c].sum()) for c in (2, 3, 4)))
    return out, int((~assigned).sum())


# Golden rankings: Seeds by id (a name can be ambiguous Europe-wide) and weight
# sets whose Measures do not read LandCover or climate, so a rebuild with wider
# raster coverage must still reproduce them exactly.
COVERAGE_MEASURES = frozenset({"landcover", "temperature", "precipitation", "solar_radiation"})
GEOMETRY_WEIGHTS = {
    "length": 100, "elevation_gain": 100, "highest": 100, "lowest": 100, "profile": 100,
    "climb_grade": 100, "steepest_km": 100,
}  # fmt: skip
ALTITUDE_WEIGHTS = {"highest": 30, "lowest": 30, "length": 7, "elevation_gain": 7, "difficulty": 6}
GOLDEN_SEEDS = {
    4004229: "Ben Nevis Mountain Path",
    17085818: "Kungsleden Etapp 7",
    17085819: "Kungsleden Etapp 6",
    19446684: "Aljažev dom – Kredarica – Triglav",
    3360396: "Czarny Staw – Rysy",
    15661826: "Montanha do Pico",
    11340787: "Le Puy de Dôme",
    2235103: "PR 1.2 Vereda do Pico Ruivo",
}
GOLDEN_TOP = 50


def golden_weight_sets() -> dict[str, dict[str, float]]:
    from dayhike.similarity import MEASURE_NAMES

    return {
        "geometry": GEOMETRY_WEIGHTS,
        "altitude": ALTITUDE_WEIGHTS,
        "no_coverage": {name: 100 for name in MEASURE_NAMES if name not in COVERAGE_MEASURES},
    }


def top_matches(store, seed_id: int, weights: Mapping[str, float], limit: int = GOLDEN_TOP) -> list[tuple]:
    """(id, similarity, raw, score) of the best ``limit`` DayHikes for one Seed id.

    Orders and scores as ``rank_dayhikes`` does, on the store alone.
    """
    from dayhike.similarity import random_pair_percentile, score_rows

    (seed_row,) = np.searchsorted(store.ids, [seed_id])
    assert store.ids[seed_row] == seed_id, seed_id
    rows = np.arange(len(store.ids))
    rows = rows[rows != seed_row]
    similarity, raw = score_rows(store, [seed_row], rows, weights)
    scored = ~np.isnan(similarity)
    rows, similarity, raw = rows[scored], similarity[scored], raw[scored]
    order = np.lexsort((store.ids[rows], -raw, -similarity))[:limit]
    score = random_pair_percentile(store, similarity[order], weights)
    return [
        (int(store.ids[row]), float(s), float(r), float(p))
        for row, s, r, p in zip(rows[order], similarity[order], raw[order], score)
    ]


def similarity_between(store, seed_id: int, other_id: int, weights: Mapping[str, float]) -> tuple[float, float, int]:
    """(similarity, raw, rank among all candidates) of ``other_id`` for Seed ``seed_id``."""
    from dayhike.similarity import score_rows

    seed_row, other_row = np.searchsorted(store.ids, [seed_id, other_id])
    rows = np.arange(len(store.ids))
    rows = rows[rows != seed_row]
    similarity, raw = score_rows(store, [seed_row], rows, weights)
    at = int(np.searchsorted(rows, other_row))
    better = (similarity > similarity[at]) | ((similarity == similarity[at]) & (raw > raw[at]))
    return float(similarity[at]), float(raw[at]), int(better.sum()) + 1


# Valhalla: the cities of firstmate's od-valhalla probe and three long routes.
CITIES = {
    "Lisbon": (38.7223, -9.1393), "Warsaw": (52.2297, 21.0122), "Athens": (37.9838, 23.7275),
    "Oslo": (59.9139, 10.7522), "Zagreb": (45.815, 15.98), "Vienna": (48.2082, 16.3738),
    "Madrid": (40.4168, -3.7038), "Rome": (41.9028, 12.4964), "Paris": (48.8566, 2.3522),
}  # fmt: skip
CITY_ROUTES = (("Zagreb", "Vienna"), ("Lisbon", "Warsaw"), ("Athens", "Oslo"))
NEARBY_ORIGINS = {"munich": (48.137, 11.575), "oslo": CITIES["Oslo"], "lisbon": CITIES["Lisbon"]}
NEARBY_HOURS = (2, 4)


def route(url: str, start: tuple[float, float], end: tuple[float, float]) -> tuple[float, float]:
    """(hours, km) of a Valhalla auto route."""
    from dayhike.valhalla import post_json

    body = post_json(
        url,
        "/route",
        {"locations": [{"lat": start[0], "lon": start[1]}, {"lat": end[0], "lon": end[1]}], "costing": "auto"},
    )
    summary = body["trip"]["summary"]
    return summary["time"] / 3600, summary["length"]


def locates(url: str, place: tuple[float, float]) -> bool:
    """Whether Valhalla snaps the place onto an auto edge."""
    from dayhike.valhalla import post_json

    body = post_json(url, "/locate", {"locations": [{"lat": place[0], "lon": place[1]}], "costing": "auto"})
    return bool(body and body[0].get("edges"))


def nearby_hours(catalog, url: str, origin: tuple[float, float], hours: float, snaps_path: Path) -> dict[int, float]:
    """DayHike id -> estimated DriveTime hours within ``hours`` (``dayhike nearby --highways``)."""
    from dayhike import Origin, RankedDayHike, Ranking, filter_by_drive_time
    from dayhike.highways import HighwayDriveTimeClient, HighwaySnaps

    everything = Ranking(items=tuple(RankedDayHike(hike, 0.0) for hike in catalog.dayhikes))
    client = HighwayDriveTimeClient(hours, HighwaySnaps(url, snaps_path))
    reachable = filter_by_drive_time(everything, Origin(*origin), hours, client)
    return {item.dayhike.id: item.drive_time.hours for item in reachable.items}


def unreachable_ids(catalog, url: str, snaps_path: Path) -> list[int]:
    """Ids of DayHikes whose Trailhead has no highway within 100 km."""
    from dayhike.highways import HighwaySnaps

    places = [(hike.trailhead.latitude, hike.trailhead.longitude) for hike in catalog.dayhikes]
    snaps = HighwaySnaps(url, snaps_path).of(places)
    return sorted(hike.id for hike, snap in zip(catalog.dayhikes, snaps) if snap is None)


def tile_counts_in_tar(tar_path: Path) -> dict[str, int]:
    """Entries of a Valhalla tile tar: all of them and the .gph tiles per hierarchy level."""
    import tarfile

    counts = {"entries": 0, "level_0": 0, "level_1": 0, "level_2": 0}
    with tarfile.open(tar_path) as archive:
        for member in archive:
            counts["entries"] += 1
            level = member.name.split("/", 1)[0]
            if member.name.endswith(".gph") and f"level_{level}" in counts:
                counts[f"level_{level}"] += 1
    return counts


def write_tsv(path: Path, header: Sequence[str], rows: Iterable[Sequence]) -> None:
    lines = ["\t".join(header)] + ["\t".join(str(value) for value in row) for row in rows]
    path.write_text("\n".join(lines) + "\n")


def read_tsv(path: Path) -> list[dict[str, str]]:
    header, *lines = path.read_text().splitlines()
    names = header.split("\t")
    return [dict(zip(names, line.split("\t"), strict=True)) for line in lines]
