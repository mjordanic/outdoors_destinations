from __future__ import annotations

import json
import math
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

EARTH_RADIUS_M = 6_371_000
MIN_LENGTH_M = 2_000
MAX_LENGTH_M = 25_000
OSM_ATTRIBUTION = "© OpenStreetMap contributors"

SAC_SCALE_RANK = {
    "hiking": 1,
    "mountain_hiking": 2,
    "demanding_mountain_hiking": 3,
    "alpine_hiking": 4,
    "demanding_alpine_hiking": 5,
    "difficult_alpine_hiking": 6,
}


@dataclass(frozen=True)
class Hiker:
    id: str


@dataclass(frozen=True)
class Trailhead:
    latitude: float
    longitude: float


@dataclass(frozen=True)
class Difficulty:
    sac_scale: str | None


@dataclass(frozen=True)
class DayHike:
    id: int
    name: str
    trailhead: Trailhead
    length_m: float
    difficulty: Difficulty


@dataclass(frozen=True)
class Label:
    hiker_id: str
    dayhike_id: int
    score: float
    created_at: str


@dataclass(frozen=True)
class IngestReport:
    kept: int
    dropped_unnamed: int
    dropped_length: int
    dropped_network: int


@dataclass(frozen=True)
class Catalog:
    path: Path
    dayhikes: tuple[DayHike, ...]
    ingest_report: IngestReport
    attribution: str
    labels: tuple[Label, ...]

    @classmethod
    def open(cls, path: Path | str) -> Catalog:
        path = Path(path)
        connection = sqlite3.connect(path)
        try:
            meta = dict(connection.execute("SELECT key, value FROM catalog_meta"))
            report_data = json.loads(meta["ingest_report"])
            trailheads = {
                dayhike_id: Trailhead(latitude=latitude, longitude=longitude)
                for dayhike_id, latitude, longitude in connection.execute(
                    "SELECT dayhike_id, latitude, longitude FROM trailheads"
                )
            }
            dayhikes = tuple(
                DayHike(
                    id=dayhike_id,
                    name=name,
                    trailhead=trailheads[dayhike_id],
                    length_m=length_m,
                    difficulty=Difficulty(sac_scale=sac_scale),
                )
                for dayhike_id, name, length_m, sac_scale in connection.execute(
                    "SELECT id, name, length_m, difficulty FROM dayhikes ORDER BY id"
                )
            )
            labels = tuple(
                Label(
                    hiker_id=hiker_id,
                    dayhike_id=dayhike_id,
                    score=score,
                    created_at=created_at,
                )
                for hiker_id, dayhike_id, score, created_at in connection.execute(
                    "SELECT hiker_id, dayhike_id, score, created_at FROM labels"
                )
            )
            return cls(
                path=path,
                dayhikes=dayhikes,
                ingest_report=IngestReport(
                    kept=report_data["kept"],
                    dropped_unnamed=report_data["dropped_unnamed"],
                    dropped_length=report_data["dropped_length"],
                    dropped_network=report_data["dropped_network"],
                ),
                attribution=meta["attribution"],
                labels=labels,
            )
        finally:
            connection.close()


@dataclass(frozen=True)
class _OsmWay:
    node_ids: tuple[int, ...]
    tags: dict[str, str]


@dataclass(frozen=True)
class _OsmRelation:
    id: int
    members: tuple[tuple[str, int], ...]
    tags: dict[str, str]


def build_catalog(osm_path: Path | str, catalog_path: Path | str) -> Catalog:
    osm_path = Path(osm_path)
    catalog_path = Path(catalog_path)
    nodes, ways, relations = _parse_osm(osm_path)
    report_counts = {"unnamed": 0, "length": 0, "network": 0}
    kept: list[DayHike] = []
    for relation in relations:
        if relation.tags.get("route") != "hiking":
            continue
        drop_reason = _drop_reason(relation, nodes, ways)
        if drop_reason is not None:
            report_counts[drop_reason] += 1
            continue
        kept.append(_dayhike_from_relation(relation, nodes, ways))
    kept.sort(key=lambda hike: hike.id)
    report = IngestReport(
        kept=len(kept),
        dropped_unnamed=report_counts["unnamed"],
        dropped_length=report_counts["length"],
        dropped_network=report_counts["network"],
    )
    _write_sqlite(catalog_path, kept, report)
    return Catalog.open(catalog_path)


def _parse_osm(
    path: Path,
) -> tuple[dict[int, tuple[float, float]], dict[int, _OsmWay], list[_OsmRelation]]:
    root = ET.parse(path).getroot()
    nodes: dict[int, tuple[float, float]] = {}
    ways: dict[int, _OsmWay] = {}
    relations: list[_OsmRelation] = []
    for element in root:
        if element.tag == "node":
            nodes[int(element.attrib["id"])] = (
                float(element.attrib["lat"]),
                float(element.attrib["lon"]),
            )
        elif element.tag == "way":
            nds = tuple(int(nd.attrib["ref"]) for nd in element if nd.tag == "nd")
            ways[int(element.attrib["id"])] = _OsmWay(nds, _tags(element))
        elif element.tag == "relation":
            members = tuple(
                (member.attrib["type"], int(member.attrib["ref"]))
                for member in element
                if member.tag == "member"
            )
            relations.append(
                _OsmRelation(int(element.attrib["id"]), members, _tags(element))
            )
    return nodes, ways, relations


def _tags(element: ET.Element) -> dict[str, str]:
    return {tag.attrib["k"]: tag.attrib["v"] for tag in element if tag.attrib.get("k")}


def _drop_reason(
    relation: _OsmRelation,
    nodes: Mapping[int, tuple[float, float]],
    ways: Mapping[int, _OsmWay],
) -> str | None:
    points = _relation_points(relation, nodes, ways)
    length_m = _linestring_length_m(points)
    if _is_network_superroute(relation, length_m):
        return "network"
    name = (relation.tags.get("name") or "").strip()
    if not name:
        return "unnamed"
    if length_m < MIN_LENGTH_M or length_m > MAX_LENGTH_M:
        return "length"
    return None


def _is_network_superroute(relation: _OsmRelation, length_m: float) -> bool:
    network = relation.tags.get("network")
    if network == "iwn":
        return True
    if network != "nwn":
        return False
    has_child_relations = any(member_type == "relation" for member_type, _ in relation.members)
    return has_child_relations or length_m > MAX_LENGTH_M


def _dayhike_from_relation(
    relation: _OsmRelation,
    nodes: Mapping[int, tuple[float, float]],
    ways: Mapping[int, _OsmWay],
) -> DayHike:
    points = _relation_points(relation, nodes, ways)
    latitude, longitude = points[0]
    return DayHike(
        id=relation.id,
        name=relation.tags["name"].strip(),
        trailhead=Trailhead(latitude=latitude, longitude=longitude),
        length_m=_linestring_length_m(points),
        difficulty=Difficulty(sac_scale=_max_sac_scale(relation, ways)),
    )


def _relation_points(
    relation: _OsmRelation,
    nodes: Mapping[int, tuple[float, float]],
    ways: Mapping[int, _OsmWay],
) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for member_type, member_id in relation.members:
        if member_type != "way":
            continue
        way = ways.get(member_id)
        if way is None:
            continue
        for node_id in way.node_ids:
            point = nodes.get(node_id)
            if point is None:
                continue
            if not points or points[-1] != point:
                points.append(point)
    return points


def _max_sac_scale(relation: _OsmRelation, ways: Mapping[int, _OsmWay]) -> str | None:
    best: tuple[int, str] | None = None
    for member_type, member_id in relation.members:
        if member_type != "way":
            continue
        way = ways.get(member_id)
        if way is None:
            continue
        scale = way.tags.get("sac_scale")
        rank = SAC_SCALE_RANK.get(scale) if scale else None
        if rank is None:
            continue
        if best is None or rank > best[0]:
            best = (rank, scale)
    return None if best is None else best[1]


def _linestring_length_m(points: Sequence[tuple[float, float]]) -> float:
    length = 0.0
    for start, end in zip(points, points[1:]):
        length += _haversine_m(start[0], start[1], end[0], end[1])
    return length


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _write_sqlite(path: Path, dayhikes: Iterable[DayHike], report: IngestReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE catalog_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE dayhikes (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                length_m REAL NOT NULL,
                difficulty TEXT
            );
            CREATE TABLE trailheads (
                dayhike_id INTEGER PRIMARY KEY REFERENCES dayhikes(id),
                latitude REAL NOT NULL,
                longitude REAL NOT NULL
            );
            CREATE TABLE labels (
                hiker_id TEXT NOT NULL,
                dayhike_id INTEGER NOT NULL,
                score REAL NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO catalog_meta (key, value) VALUES (?, ?)",
            ("attribution", OSM_ATTRIBUTION),
        )
        connection.execute(
            "INSERT INTO catalog_meta (key, value) VALUES (?, ?)",
            (
                "ingest_report",
                json.dumps(
                    {
                        "kept": report.kept,
                        "dropped_unnamed": report.dropped_unnamed,
                        "dropped_length": report.dropped_length,
                        "dropped_network": report.dropped_network,
                    }
                ),
            ),
        )
        connection.executemany(
            "INSERT INTO dayhikes (id, name, length_m, difficulty) VALUES (?, ?, ?, ?)",
            [
                (hike.id, hike.name, hike.length_m, hike.difficulty.sac_scale)
                for hike in dayhikes
            ],
        )
        connection.executemany(
            "INSERT INTO trailheads (dayhike_id, latitude, longitude) VALUES (?, ?, ?)",
            [
                (hike.id, hike.trailhead.latitude, hike.trailhead.longitude)
                for hike in dayhikes
            ],
        )
        connection.commit()
    finally:
        connection.close()
