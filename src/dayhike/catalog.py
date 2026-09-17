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
AMENITY_RADIUS_M = 1_000
OSM_ATTRIBUTION = "© OpenStreetMap contributors"
HUT_TOURISM = frozenset({"alpine_hut", "wilderness_hut"})
RESTAURANT_CAFE_AMENITY = frozenset({"restaurant", "cafe"})
AMENITY_KINDS = ("hut", "parking", "restaurant_cafe", "drinking_water")
FEATURE_NAMES = (
    "length_m",
    "elevation_min_m",
    "elevation_max_m",
    "elevation_gain_m",
    "difficulty_ordinal",
    "hut_count",
    "hut_nearest_m",
    "parking_count",
    "parking_nearest_m",
    "restaurant_cafe_count",
    "restaurant_cafe_nearest_m",
    "drinking_water_count",
    "drinking_water_nearest_m",
)

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
class AmenityFact:
    count: int
    nearest_m: float | None


@dataclass(frozen=True)
class AmenityFacts:
    hut: AmenityFact
    parking: AmenityFact
    restaurant_cafe: AmenityFact
    drinking_water: AmenityFact


@dataclass(frozen=True)
class Trailhead:
    latitude: float
    longitude: float
    amenity_facts: AmenityFacts


@dataclass(frozen=True)
class Difficulty:
    sac_scale: str | None


@dataclass(frozen=True)
class FeatureVector:
    length_m: float
    elevation_min_m: float | None
    elevation_max_m: float | None
    elevation_gain_m: float | None
    difficulty_ordinal: int | None
    amenity_facts: AmenityFacts


@dataclass(frozen=True)
class DayHike:
    id: int
    name: str
    trailhead: Trailhead
    length_m: float
    difficulty: Difficulty
    feature_vector: FeatureVector


@dataclass(frozen=True)
class Scaler:
    feature_names: tuple[str, ...]
    mean: tuple[float, ...]
    scale: tuple[float, ...]


@dataclass(frozen=True)
class RankedDayHike:
    dayhike: DayHike
    score: float


@dataclass(frozen=True)
class Ranking:
    items: tuple[RankedDayHike, ...]


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
    scaler: Scaler

    @classmethod
    def open(cls, path: Path | str) -> Catalog:
        path = Path(path)
        connection = sqlite3.connect(path)
        try:
            meta = dict(connection.execute("SELECT key, value FROM catalog_meta"))
            report_data = json.loads(meta["ingest_report"])
            amenity_rows: dict[int, dict[str, AmenityFact]] = {}
            for dayhike_id, kind, count, nearest_m in connection.execute(
                "SELECT dayhike_id, kind, count, nearest_m FROM amenity_facts"
            ):
                amenity_rows.setdefault(dayhike_id, {})[kind] = AmenityFact(
                    count=count, nearest_m=nearest_m
                )
            trailheads = {
                dayhike_id: Trailhead(
                    latitude=latitude,
                    longitude=longitude,
                    amenity_facts=_amenity_facts_from_map(amenity_rows[dayhike_id]),
                )
                for dayhike_id, latitude, longitude in connection.execute(
                    "SELECT dayhike_id, latitude, longitude FROM trailheads"
                )
            }
            vectors = {
                dayhike_id: FeatureVector(
                    length_m=length_m,
                    elevation_min_m=elevation_min_m,
                    elevation_max_m=elevation_max_m,
                    elevation_gain_m=elevation_gain_m,
                    difficulty_ordinal=difficulty_ordinal,
                    amenity_facts=trailheads[dayhike_id].amenity_facts,
                )
                for (
                    dayhike_id,
                    length_m,
                    elevation_min_m,
                    elevation_max_m,
                    elevation_gain_m,
                    difficulty_ordinal,
                ) in connection.execute(
                    "SELECT dayhike_id, length_m, elevation_min_m, elevation_max_m, "
                    "elevation_gain_m, difficulty_ordinal FROM feature_vectors"
                )
            }
            dayhikes = tuple(
                DayHike(
                    id=dayhike_id,
                    name=name,
                    trailhead=trailheads[dayhike_id],
                    length_m=length_m,
                    difficulty=Difficulty(sac_scale=sac_scale),
                    feature_vector=vectors[dayhike_id],
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
            scaler_rows = list(
                connection.execute(
                    "SELECT feature_name, mean, scale FROM scaler ORDER BY position"
                )
            )
            scaler = Scaler(
                feature_names=tuple(name for name, _, _ in scaler_rows),
                mean=tuple(mean for _, mean, _ in scaler_rows),
                scale=tuple(scale for _, _, scale in scaler_rows),
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
                scaler=scaler,
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


class SeedNameError(ValueError):
    """A Seed name did not resolve to exactly one Catalog DayHike."""


class UnknownSeedName(SeedNameError):
    """No Catalog DayHike matched the Seed name."""


class AmbiguousSeedName(SeedNameError):
    """More than one Catalog DayHike matched the Seed name."""

    def __init__(self, message: str, candidates: tuple[str, ...]) -> None:
        super().__init__(message)
        self.candidates = candidates


def resolve_seed(catalog: Catalog, name: str) -> int:
    """Return the DayHike id for a Seed name in an already-built Catalog.

    Matching is a case-insensitive exact match after strip: the query and each
    DayHike name are compared with ``str.casefold`` once leading and trailing
    whitespace is removed. This does not rank and does not read OSM.
    """
    query = name.strip().casefold()
    matches = [
        hike for hike in catalog.dayhikes if hike.name.strip().casefold() == query
    ]
    if len(matches) == 1:
        return matches[0].id
    if not matches:
        raise UnknownSeedName(f"Unknown Seed name {name!r}")
    candidates = tuple(hike.name for hike in matches)
    raise AmbiguousSeedName(
        f"Seed name {name!r} matches more than one DayHike: {', '.join(candidates)}",
        candidates,
    )


def rank_dayhikes(catalog: Catalog, seeds: Sequence[str]) -> Ranking:
    """Rank Catalog DayHikes by maximum cosine similarity to named Seeds.

    Similarity is cosine of FeatureVectors after the Catalog StandardScaler.
    Seeds are resolved with ``resolve_seed`` and omitted from the Ranking.
    """
    seed_ids = {resolve_seed(catalog, name) for name in seeds}
    seed_scaled = [
        _scale_vector(hike.feature_vector, catalog.scaler)
        for hike in catalog.dayhikes
        if hike.id in seed_ids
    ]
    ranked: list[RankedDayHike] = []
    for hike in catalog.dayhikes:
        if hike.id in seed_ids:
            continue
        scaled = _scale_vector(hike.feature_vector, catalog.scaler)
        score = max(_cosine(scaled, seed_vector) for seed_vector in seed_scaled)
        ranked.append(RankedDayHike(dayhike=hike, score=score))
    ranked.sort(key=lambda item: (-item.score, item.dayhike.id))
    return Ranking(items=tuple(ranked))


def build_catalog(osm_path: Path | str, catalog_path: Path | str) -> Catalog:
    osm_path = Path(osm_path)
    catalog_path = Path(catalog_path)
    nodes, ways, relations, amenities, node_elevations = _parse_osm(osm_path)
    report_counts = {"unnamed": 0, "length": 0, "network": 0}
    kept: list[DayHike] = []
    for relation in relations:
        if relation.tags.get("route") != "hiking":
            continue
        drop_reason = _drop_reason(relation, nodes, ways)
        if drop_reason is not None:
            report_counts[drop_reason] += 1
            continue
        kept.append(
            _dayhike_from_relation(relation, nodes, ways, amenities, node_elevations)
        )
    kept.sort(key=lambda hike: hike.id)
    report = IngestReport(
        kept=len(kept),
        dropped_unnamed=report_counts["unnamed"],
        dropped_length=report_counts["length"],
        dropped_network=report_counts["network"],
    )
    scaler = _fit_scaler(kept)
    _write_sqlite(catalog_path, kept, report, scaler)
    return Catalog.open(catalog_path)


def _parse_osm(
    path: Path,
) -> tuple[
    dict[int, tuple[float, float]],
    dict[int, _OsmWay],
    list[_OsmRelation],
    list[tuple[str, float, float]],
    dict[int, float],
]:
    root = ET.parse(path).getroot()
    nodes: dict[int, tuple[float, float]] = {}
    ways: dict[int, _OsmWay] = {}
    relations: list[_OsmRelation] = []
    amenities: list[tuple[str, float, float]] = []
    node_elevations: dict[int, float] = {}
    for element in root:
        if element.tag == "node":
            latitude = float(element.attrib["lat"])
            longitude = float(element.attrib["lon"])
            node_id = int(element.attrib["id"])
            nodes[node_id] = (latitude, longitude)
            tags = _tags(element)
            kind = _amenity_kind(tags)
            if kind is not None:
                amenities.append((kind, latitude, longitude))
            if "ele" in tags:
                node_elevations[node_id] = float(tags["ele"])
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
    return nodes, ways, relations, amenities, node_elevations


def _amenity_kind(tags: Mapping[str, str]) -> str | None:
    if tags.get("tourism") in HUT_TOURISM:
        return "hut"
    amenity = tags.get("amenity")
    if amenity == "parking":
        return "parking"
    if amenity in RESTAURANT_CAFE_AMENITY:
        return "restaurant_cafe"
    if amenity == "drinking_water":
        return "drinking_water"
    return None


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
    amenities: Sequence[tuple[str, float, float]],
    node_elevations: Mapping[int, float],
) -> DayHike:
    vertices = _relation_vertices(relation, nodes, ways)
    points = [point for _, point in vertices]
    latitude, longitude = points[0]
    length_m = _linestring_length_m(points)
    sac_scale = _max_sac_scale(relation, ways)
    amenity_facts = _amenity_facts_at(latitude, longitude, amenities)
    elevation_min_m, elevation_max_m, elevation_gain_m = _elevation_stats(
        vertices, node_elevations
    )
    return DayHike(
        id=relation.id,
        name=relation.tags["name"].strip(),
        trailhead=Trailhead(
            latitude=latitude,
            longitude=longitude,
            amenity_facts=amenity_facts,
        ),
        length_m=length_m,
        difficulty=Difficulty(sac_scale=sac_scale),
        feature_vector=FeatureVector(
            length_m=length_m,
            elevation_min_m=elevation_min_m,
            elevation_max_m=elevation_max_m,
            elevation_gain_m=elevation_gain_m,
            difficulty_ordinal=None if sac_scale is None else SAC_SCALE_RANK.get(sac_scale),
            amenity_facts=amenity_facts,
        ),
    )


def _amenity_facts_at(
    latitude: float,
    longitude: float,
    amenities: Sequence[tuple[str, float, float]],
) -> AmenityFacts:
    distances: dict[str, list[float]] = {kind: [] for kind in AMENITY_KINDS}
    for kind, amenity_lat, amenity_lon in amenities:
        distance_m = _haversine_m(latitude, longitude, amenity_lat, amenity_lon)
        if distance_m <= AMENITY_RADIUS_M:
            distances[kind].append(distance_m)
    return AmenityFacts(
        hut=_amenity_fact(distances["hut"]),
        parking=_amenity_fact(distances["parking"]),
        restaurant_cafe=_amenity_fact(distances["restaurant_cafe"]),
        drinking_water=_amenity_fact(distances["drinking_water"]),
    )


def _amenity_fact(distances_m: Sequence[float]) -> AmenityFact:
    if not distances_m:
        return AmenityFact(count=0, nearest_m=None)
    return AmenityFact(count=len(distances_m), nearest_m=min(distances_m))


def _amenity_facts_from_map(facts: Mapping[str, AmenityFact]) -> AmenityFacts:
    return AmenityFacts(
        hut=facts["hut"],
        parking=facts["parking"],
        restaurant_cafe=facts["restaurant_cafe"],
        drinking_water=facts["drinking_water"],
    )


def _elevation_stats(
    vertices: Sequence[tuple[int, tuple[float, float]]],
    node_elevations: Mapping[int, float],
) -> tuple[float | None, float | None, float | None]:
    elevations = [
        node_elevations[node_id]
        for node_id, _ in vertices
        if node_id in node_elevations
    ]
    if not elevations:
        return None, None, None
    gain_m = 0.0
    for start, end in zip(elevations, elevations[1:]):
        if end > start:
            gain_m += end - start
    return min(elevations), max(elevations), gain_m


def _relation_vertices(
    relation: _OsmRelation,
    nodes: Mapping[int, tuple[float, float]],
    ways: Mapping[int, _OsmWay],
) -> list[tuple[int, tuple[float, float]]]:
    vertices: list[tuple[int, tuple[float, float]]] = []
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
            if vertices and vertices[-1][1] == point:
                continue
            vertices.append((node_id, point))
    return vertices


def _relation_points(
    relation: _OsmRelation,
    nodes: Mapping[int, tuple[float, float]],
    ways: Mapping[int, _OsmWay],
) -> list[tuple[float, float]]:
    return [point for _, point in _relation_vertices(relation, nodes, ways)]


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


def _optional(value: float | None) -> float:
    return math.nan if value is None else value


def _feature_row(vector: FeatureVector) -> tuple[float, ...]:
    facts = vector.amenity_facts
    return (
        vector.length_m,
        _optional(vector.elevation_min_m),
        _optional(vector.elevation_max_m),
        _optional(vector.elevation_gain_m),
        math.nan if vector.difficulty_ordinal is None else float(vector.difficulty_ordinal),
        float(facts.hut.count),
        _optional(facts.hut.nearest_m),
        float(facts.parking.count),
        _optional(facts.parking.nearest_m),
        float(facts.restaurant_cafe.count),
        _optional(facts.restaurant_cafe.nearest_m),
        float(facts.drinking_water.count),
        _optional(facts.drinking_water.nearest_m),
    )


def _fit_scaler(dayhikes: Sequence[DayHike]) -> Scaler:
    rows = [_feature_row(hike.feature_vector) for hike in dayhikes]
    n_features = len(FEATURE_NAMES)
    if not rows:
        zeros = (0.0,) * n_features
        ones = (1.0,) * n_features
        return Scaler(FEATURE_NAMES, zeros, ones)
    n = len(rows)
    fill: list[float] = []
    for j in range(n_features):
        present = [row[j] for row in rows if not math.isnan(row[j])]
        fill.append((sum(present) / len(present)) if present else 0.0)
    filled = [
        tuple(fill[j] if math.isnan(row[j]) else row[j] for j in range(n_features))
        for row in rows
    ]
    mean: list[float] = []
    scale: list[float] = []
    for j in range(n_features):
        column = [row[j] for row in filled]
        column_mean = sum(column) / n
        variance = sum((value - column_mean) ** 2 for value in column) / n
        column_scale = math.sqrt(variance)
        mean.append(column_mean)
        scale.append(1.0 if column_scale == 0.0 else column_scale)
    return Scaler(FEATURE_NAMES, tuple(mean), tuple(scale))


def _scale_vector(vector: FeatureVector, scaler: Scaler) -> tuple[float, ...]:
    return tuple(
        0.0 if math.isnan(value) else (value - mean) / scale
        for value, mean, scale in zip(
            _feature_row(vector), scaler.mean, scaler.scale, strict=True
        )
    )


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _write_sqlite(
    path: Path, dayhikes: Iterable[DayHike], report: IngestReport, scaler: Scaler
) -> None:
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
            CREATE TABLE amenity_facts (
                dayhike_id INTEGER NOT NULL REFERENCES dayhikes(id),
                kind TEXT NOT NULL,
                count INTEGER NOT NULL,
                nearest_m REAL,
                PRIMARY KEY (dayhike_id, kind)
            );
            CREATE TABLE feature_vectors (
                dayhike_id INTEGER PRIMARY KEY REFERENCES dayhikes(id),
                length_m REAL NOT NULL,
                elevation_min_m REAL,
                elevation_max_m REAL,
                elevation_gain_m REAL,
                difficulty_ordinal INTEGER
            );
            CREATE TABLE labels (
                hiker_id TEXT NOT NULL,
                dayhike_id INTEGER NOT NULL,
                score REAL NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE scaler (
                position INTEGER PRIMARY KEY,
                feature_name TEXT NOT NULL,
                mean REAL NOT NULL,
                scale REAL NOT NULL
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
        connection.executemany(
            "INSERT INTO amenity_facts (dayhike_id, kind, count, nearest_m) "
            "VALUES (?, ?, ?, ?)",
            [
                (hike.id, kind, fact.count, fact.nearest_m)
                for hike in dayhikes
                for kind, fact in (
                    ("hut", hike.trailhead.amenity_facts.hut),
                    ("parking", hike.trailhead.amenity_facts.parking),
                    ("restaurant_cafe", hike.trailhead.amenity_facts.restaurant_cafe),
                    ("drinking_water", hike.trailhead.amenity_facts.drinking_water),
                )
            ],
        )
        connection.executemany(
            "INSERT INTO feature_vectors (dayhike_id, length_m, elevation_min_m, "
            "elevation_max_m, elevation_gain_m, difficulty_ordinal) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    hike.id,
                    hike.feature_vector.length_m,
                    hike.feature_vector.elevation_min_m,
                    hike.feature_vector.elevation_max_m,
                    hike.feature_vector.elevation_gain_m,
                    hike.feature_vector.difficulty_ordinal,
                )
                for hike in dayhikes
            ],
        )
        connection.executemany(
            "INSERT INTO scaler (position, feature_name, mean, scale) VALUES (?, ?, ?, ?)",
            [
                (position, name, mean, scale)
                for position, (name, mean, scale) in enumerate(
                    zip(scaler.feature_names, scaler.mean, scaler.scale, strict=True)
                )
            ],
        )
        connection.commit()
    finally:
        connection.close()
