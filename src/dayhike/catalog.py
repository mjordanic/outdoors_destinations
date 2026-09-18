from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Iterable, Mapping, Protocol, Sequence

import numpy as np
from sklearn.neighbors import BallTree
from sklearn.preprocessing import StandardScaler

from dayhike.osm import OsmRelation, OsmSnapshot, OsmWay

EARTH_RADIUS_M = 6_371_000
MIN_LENGTH_M = 2_000  # inclusive DayHike length band
MAX_LENGTH_M = 25_000
AMENITY_RADIUS_M = 20_000  # great-circle from Trailhead; zero counts are valid
TRAILHEAD_ACCESS_RADIUS_M = 5_000  # parking / driveable highway near an endpoint
OSM_ATTRIBUTION = "© OpenStreetMap contributors"
AMENITY_KINDS = ("hut", "parking", "restaurant_cafe", "drinking_water")
# Intrinsic FeatureVector columns only. DriveTime is query-time, not a feature.
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
class Origin:
    latitude: float
    longitude: float


@dataclass(frozen=True)
class DriveTime:
    hours: float


@dataclass(frozen=True)
class RankedDayHike:
    dayhike: DayHike
    score: float
    drive_time: DriveTime | None = None


@dataclass(frozen=True)
class Ranking:
    items: tuple[RankedDayHike, ...]


class MatrixClient(Protocol):
    def sources_to_targets(
        self, origin: Origin, trailheads: Sequence[Trailhead]
    ) -> Sequence[float | None]:
        """Return DriveTime in seconds for each Trailhead, or None if unreachable."""


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
                scaler=scaler,
            )
        finally:
            connection.close()


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


_SECONDS_PER_HOUR = 3600.0


def filter_by_drive_time(
    ranking: Ranking,
    origin: Origin,
    max_hours: float,
    matrix_client: MatrixClient,
) -> Ranking:
    """Drop DayHikes whose DriveTime from Origin is missing or above max hours.

    Matrix times are seconds; None means unreachable and is dropped, not filled
    in. Remaining items keep Ranking order and report DriveTime in hours.
    Inclusive of max_hours.
    """
    if not ranking.items:
        return Ranking(items=())
    trailheads = [item.dayhike.trailhead for item in ranking.items]
    times_s = matrix_client.sources_to_targets(origin, trailheads)
    kept: list[RankedDayHike] = []
    for item, time_s in zip(ranking.items, times_s):
        if time_s is None or not math.isfinite(time_s) or time_s < 0:
            continue
        hours = time_s / _SECONDS_PER_HOUR
        if hours <= max_hours:
            kept.append(
                RankedDayHike(
                    dayhike=item.dayhike,
                    score=item.score,
                    drive_time=DriveTime(hours=hours),
                )
            )
    return Ranking(items=tuple(kept))


def build_catalog(
    osm_path: Path | str | Sequence[Path | str],
    catalog_path: Path | str,
    *,
    progress: Callable[[str], None] | None = None,
    fresh: bool = False,
) -> Catalog:
    """Ingest OSM XML or PBF snapshot(s) into a Catalog of named DayHikes.

    Streams the snapshot in passes and keeps only hiking-route members and
    amenities in a work file next to the Catalog, so the source does not need
    to fit in RAM. Re-running the same inputs resumes that work file.
    ``fresh=True`` discards it and starts again. ``progress`` receives one
    line per stage (filter, relations, ways, nodes, access, assemble, write).

    Unnamed relations, out-of-band lengths, and iwn/nwn superroutes are
    counted in the ingest report and are not DayHikes. Attribution is stored
    with the Catalog. DriveTime is not computed at ingest. Replaces the
    SQLite file if it exists, only after the new file is complete; overlapping
    snapshots keep one row per OSM id.
    """
    from dayhike.ingest import emit_progress, run_ingest

    catalog_path = Path(catalog_path)
    kept, report, work_path = run_ingest(
        _osm_paths(osm_path), catalog_path, progress=progress, fresh=fresh
    )
    scaler = _fit_scaler(kept)
    emit_progress(progress, "write", str(catalog_path))
    _write_sqlite(catalog_path, kept, report, scaler)
    if work_path.exists():
        work_path.unlink()
    return Catalog.open(catalog_path)


def _osm_paths(osm_path: Path | str | Sequence[Path | str]) -> list[Path]:
    if isinstance(osm_path, (str, Path)):
        path = Path(osm_path)
        if path.is_dir():
            files = sorted(
                candidate
                for candidate in path.iterdir()
                if candidate.suffix in {".osm", ".xml", ".pbf"}
                or candidate.name.endswith(".osm.pbf")
            )
            if not files:
                raise FileNotFoundError(f"No OSM files in {path}")
            return files
        return [path]
    return [Path(path) for path in osm_path]


def _drop_reason(relation: OsmRelation, length_m: float) -> str | None:
    if _is_network_superroute(relation, length_m):
        return "network"
    name = (relation.tags.get("name") or "").strip()
    if not name:
        return "unnamed"
    if length_m < MIN_LENGTH_M or length_m > MAX_LENGTH_M:
        return "length"
    return None


def _is_network_superroute(relation: OsmRelation, length_m: float) -> bool:
    # iwn/long nwn relations are corridors, not a day outing a Hiker can name as a Seed.
    network = relation.tags.get("network")
    if network == "iwn":
        return True
    if network != "nwn":
        return False
    has_child_relations = any(member_type == "relation" for member_type, _ in relation.members)
    return has_child_relations or length_m > MAX_LENGTH_M


def _dayhike_from_relation(
    relation: OsmRelation,
    snapshot: OsmSnapshot,
    vertices: Sequence[tuple[int, tuple[float, float]]],
    points: Sequence[tuple[float, float]],
    length_m: float,
    access_tree: BallTree | None,
    amenity_index: _AmenityIndex | None,
) -> DayHike:
    latitude, longitude = _pick_trailhead(points, access_tree)
    sac_scale = _max_sac_scale(relation, snapshot.ways)
    amenity_facts = _amenity_facts_at(latitude, longitude, amenity_index)
    elevation_min_m, elevation_max_m, elevation_gain_m = _elevation_stats(
        vertices, snapshot.node_elevations
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


def _access_tree(
    access_points: Sequence[tuple[float, float]],
) -> BallTree | None:
    if not access_points:
        return None
    return BallTree(np.radians(np.asarray(access_points, dtype=float)), metric="haversine")


@dataclass(frozen=True)
class _AmenityIndex:
    tree: BallTree
    ids: np.ndarray
    kinds: np.ndarray


def _amenity_index(
    amenities: Sequence[tuple[str, tuple[tuple[float, float], ...]]],
) -> _AmenityIndex | None:
    lats: list[float] = []
    lons: list[float] = []
    ids: list[int] = []
    kinds: list[str] = []
    for amenity_id, (kind, points) in enumerate(amenities):
        for lat, lon in points:
            lats.append(lat)
            lons.append(lon)
            ids.append(amenity_id)
            kinds.append(kind)
    if not lats:
        return None
    tree = BallTree(
        np.radians(np.column_stack([lats, lons])), metric="haversine"
    )
    return _AmenityIndex(tree, np.asarray(ids), np.asarray(kinds))


def _pick_trailhead(
    points: Sequence[tuple[float, float]],
    access_tree: BallTree | None,
) -> tuple[float, float]:
    first = points[0]
    last = points[-1]
    if first == last or access_tree is None:
        return first
    query = np.radians([[first[0], first[1]], [last[0], last[1]]])
    dist, _idx = access_tree.query(query, k=1)
    first_m = float(dist[0, 0] * EARTH_RADIUS_M)
    last_m = float(dist[1, 0] * EARTH_RADIUS_M)
    first_ok = first_m <= TRAILHEAD_ACCESS_RADIUS_M
    last_ok = last_m <= TRAILHEAD_ACCESS_RADIUS_M
    if first_ok and last_ok:
        return first if first_m <= last_m else last
    if last_ok:
        return last
    return first


def _amenity_facts_at(
    latitude: float,
    longitude: float,
    amenity_index: _AmenityIndex | None,
) -> AmenityFacts:
    distances: dict[str, list[float]] = {kind: [] for kind in AMENITY_KINDS}
    if amenity_index is not None:
        indices, radii = amenity_index.tree.query_radius(
            np.radians([[latitude, longitude]]),
            r=AMENITY_RADIUS_M / EARTH_RADIUS_M,
            return_distance=True,
        )
        nearest: dict[str, dict[int, float]] = {kind: {} for kind in AMENITY_KINDS}
        for idx, radius in zip(indices[0], radii[0]):
            kind = str(amenity_index.kinds[idx])
            amenity_id = int(amenity_index.ids[idx])
            distance_m = float(radius * EARTH_RADIUS_M)
            prev = nearest[kind].get(amenity_id)
            if prev is None or distance_m < prev:
                nearest[kind][amenity_id] = distance_m
        for kind in AMENITY_KINDS:
            distances[kind] = list(nearest[kind].values())
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
        # OSM ele absent: missing, not zero. A DEM is out of this milestone.
        return None, None, None
    gain_m = 0.0
    for start, end in zip(elevations, elevations[1:]):
        if end > start:
            gain_m += end - start
    return min(elevations), max(elevations), gain_m


def _assemble_node_ids(
    relation: OsmRelation, ways: Mapping[int, OsmWay]
) -> list[int]:
    chain: list[int] = []
    for member_type, member_id in relation.members:
        if member_type != "way":
            continue
        way = ways.get(member_id)
        if way is None or not way.node_ids:
            continue
        chain = _extend_node_chain(chain, list(way.node_ids))
    return chain


def _extend_node_chain(chain: list[int], ids: list[int]) -> list[int]:
    if not chain:
        return ids
    start, end = chain[0], chain[-1]
    if ids[0] == end:
        return chain + ids[1:]
    if ids[-1] == end:
        return chain + list(reversed(ids[:-1]))
    if ids[-1] == start:
        return ids + chain[1:]
    if ids[0] == start:
        return list(reversed(ids)) + chain[1:]
    return chain + ids


def _relation_vertices(
    relation: OsmRelation,
    nodes: Mapping[int, tuple[float, float]],
    ways: Mapping[int, OsmWay],
) -> list[tuple[int, tuple[float, float]]]:
    vertices: list[tuple[int, tuple[float, float]]] = []
    for node_id in _assemble_node_ids(relation, ways):
        point = nodes.get(node_id)
        if point is None:
            continue
        if vertices and vertices[-1][1] == point:
            continue
        vertices.append((node_id, point))
    return vertices


def _max_sac_scale(relation: OsmRelation, ways: Mapping[int, OsmWay]) -> str | None:
    # Strictest member-way sac_scale. Missing stays None, not silently "hiking".
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


def _missing_as_nan(value: float | None) -> float:
    return math.nan if value is None else value


def _feature_row(vector: FeatureVector) -> tuple[float, ...]:
    facts = vector.amenity_facts
    return (
        vector.length_m,
        _missing_as_nan(vector.elevation_min_m),
        _missing_as_nan(vector.elevation_max_m),
        _missing_as_nan(vector.elevation_gain_m),
        math.nan if vector.difficulty_ordinal is None else float(vector.difficulty_ordinal),
        float(facts.hut.count),
        _missing_as_nan(facts.hut.nearest_m),
        float(facts.parking.count),
        _missing_as_nan(facts.parking.nearest_m),
        float(facts.restaurant_cafe.count),
        _missing_as_nan(facts.restaurant_cafe.nearest_m),
        float(facts.drinking_water.count),
        _missing_as_nan(facts.drinking_water.nearest_m),
    )


def _fit_scaler(dayhikes: Sequence[DayHike]) -> Scaler:
    n_features = len(FEATURE_NAMES)
    if not dayhikes:
        return Scaler(FEATURE_NAMES, (0.0,) * n_features, (1.0,) * n_features)
    matrix = np.array(
        [_feature_row(hike.feature_vector) for hike in dayhikes], dtype=float
    )
    mean: list[float] = []
    scale: list[float] = []
    for column in range(n_features):
        observed = matrix[:, column]
        observed = observed[~np.isnan(observed)].reshape(-1, 1)
        if observed.size == 0:
            mean.append(0.0)
            scale.append(1.0)
            continue
        fitted = StandardScaler().fit(observed)
        column_scale = float(fitted.scale_[0])
        mean.append(float(fitted.mean_[0]))
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
    dayhikes = tuple(dayhikes)
    if len({hike.id for hike in dayhikes}) != len(dayhikes):
        raise ValueError("duplicate DayHike ids in catalog ingest")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    connection = sqlite3.connect(tmp)
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
    except Exception:
        connection.close()
        if tmp.exists():
            tmp.unlink()
        raise
    connection.close()
    tmp.replace(path)
