from __future__ import annotations

import heapq
import json
import math
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable
from typing import Iterable, Mapping, Protocol, Sequence

import numpy as np
from sklearn.neighbors import BallTree
from sklearn.preprocessing import StandardScaler

from dayhike.osm import OsmRelation, OsmSnapshot, OsmWay

EARTH_RADIUS_M = 6_371_000
MIN_LENGTH_M = 1_000  # inclusive DayHike length band
MAX_LENGTH_M = 20_000
AMENITY_RADIUS_M = 20_000  # great-circle from Trailhead; zero counts are valid
TRAILHEAD_ACCESS_RADIUS_M = 5_000  # parking / driveable highway near an endpoint
GAP_JOIN_M = 50  # member-way ends this close join two pieces with a straight gap
LOOP_SHARE = 0.5  # a piece whose closed loop is at least this share of its length is a loop
STEM_ACCESS_RADIUS_M = 200  # a loop's dead end ending this close to access is its stem
STEM_MIN_LENGTH_M = 1_000  # else a loop's only dead end at least this long is its stem
OSM_ATTRIBUTION = "© OpenStreetMap contributors"
AMENITY_KINDS = ("hut", "parking", "restaurant_cafe", "drinking_water")
NAME_ORIGIN_OSM_NAME = "osm_name"
NAME_ORIGIN_OSM_REF = "osm_ref"
NAME_ORIGIN_SYNTHETIC = "synthetic"
# Intrinsic FeatureVector columns only. DriveTime is query-time, not a feature.
LANDCOVER_FEATURE_NAMES = (
    "tree_fraction",
    "shrub_fraction",
    "grass_fraction",
    "bare_fraction",
    "snow_fraction",
)
CLIMATE_FEATURE_NAMES = (
    "temperature_c",
    "precipitation_mm",
    "solar_radiation",
)
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
    *LANDCOVER_FEATURE_NAMES,
    *CLIMATE_FEATURE_NAMES,
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
    tree_fraction: float | None = None
    shrub_fraction: float | None = None
    grass_fraction: float | None = None
    bare_fraction: float | None = None
    snow_fraction: float | None = None
    temperature_c: float | None = None
    precipitation_mm: float | None = None
    solar_radiation: float | None = None


@dataclass(frozen=True)
class DayHike:
    id: int
    name: str
    trailhead: Trailhead
    length_m: float
    difficulty: Difficulty
    feature_vector: FeatureVector
    name_origin: str = NAME_ORIGIN_OSM_NAME
    climate_months: tuple[tuple[float, float, float] | None, ...] = (None,) * 12


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
    named_osm_name: int = 0
    named_osm_ref: int = 0
    named_synthetic: int = 0


@dataclass(frozen=True)
class Catalog:
    path: Path
    dayhikes: tuple[DayHike, ...]
    ingest_report: IngestReport
    attribution: str
    scaler: Scaler
    month_scalers: Mapping[int, Scaler] = field(default_factory=dict)

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
            feature_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(feature_vectors)")
            }
            has_landcover = set(LANDCOVER_FEATURE_NAMES) <= feature_columns
            has_climate = set(CLIMATE_FEATURE_NAMES) <= feature_columns
            vector_select = (
                "SELECT dayhike_id, length_m, elevation_min_m, elevation_max_m, "
                "elevation_gain_m, difficulty_ordinal"
            )
            if has_landcover:
                vector_select += (
                    ", tree_fraction, shrub_fraction, grass_fraction, "
                    "bare_fraction, snow_fraction"
                )
            if has_climate:
                vector_select += ", temperature_c, precipitation_mm, solar_radiation"
            vectors = {}
            for row in connection.execute(vector_select + " FROM feature_vectors"):
                (
                    dayhike_id,
                    length_m,
                    elevation_min_m,
                    elevation_max_m,
                    elevation_gain_m,
                    difficulty_ordinal,
                ) = row[:6]
                offset = 6
                if has_landcover:
                    (
                        tree_fraction,
                        shrub_fraction,
                        grass_fraction,
                        bare_fraction,
                        snow_fraction,
                    ) = row[offset : offset + 5]
                    offset += 5
                else:
                    tree_fraction = shrub_fraction = grass_fraction = None
                    bare_fraction = snow_fraction = None
                if has_climate:
                    temperature_c, precipitation_mm, solar_radiation = row[offset:]
                else:
                    temperature_c = precipitation_mm = solar_radiation = None
                vectors[dayhike_id] = FeatureVector(
                    length_m=length_m,
                    elevation_min_m=elevation_min_m,
                    elevation_max_m=elevation_max_m,
                    elevation_gain_m=elevation_gain_m,
                    difficulty_ordinal=difficulty_ordinal,
                    amenity_facts=trailheads[dayhike_id].amenity_facts,
                    tree_fraction=tree_fraction,
                    shrub_fraction=shrub_fraction,
                    grass_fraction=grass_fraction,
                    bare_fraction=bare_fraction,
                    snow_fraction=snow_fraction,
                    temperature_c=temperature_c,
                    precipitation_mm=precipitation_mm,
                    solar_radiation=solar_radiation,
                )
            climate_months_by_id: dict[int, list[tuple[float, float, float] | None]] = {}
            catalog_tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "climate_months" in catalog_tables:
                for dayhike_id, month, temperature_c, precipitation_mm, solar_radiation in (
                    connection.execute(
                        "SELECT dayhike_id, month, temperature_c, precipitation_mm, "
                        "solar_radiation FROM climate_months"
                    )
                ):
                    months = climate_months_by_id.setdefault(dayhike_id, [None] * 12)
                    if 1 <= int(month) <= 12:
                        months[int(month) - 1] = (
                            temperature_c,
                            precipitation_mm,
                            solar_radiation,
                        )
            dayhike_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(dayhikes)")
            }
            if "name_origin" in dayhike_columns:
                dayhike_rows = connection.execute(
                    "SELECT id, name, length_m, difficulty, name_origin "
                    "FROM dayhikes ORDER BY id"
                )
            else:
                dayhike_rows = (
                    (*row, NAME_ORIGIN_OSM_NAME)
                    for row in connection.execute(
                        "SELECT id, name, length_m, difficulty FROM dayhikes ORDER BY id"
                    )
                )
            dayhikes = tuple(
                DayHike(
                    id=dayhike_id,
                    name=name,
                    trailhead=trailheads[dayhike_id],
                    length_m=length_m,
                    difficulty=Difficulty(sac_scale=sac_scale),
                    feature_vector=vectors[dayhike_id],
                    name_origin=name_origin,
                    climate_months=tuple(
                        climate_months_by_id.get(dayhike_id, [None] * 12)
                    ),
                )
                for dayhike_id, name, length_m, sac_scale, name_origin in dayhike_rows
            )
            scaler_rows = list(
                connection.execute(
                    "SELECT feature_name, mean, scale FROM scaler ORDER BY position"
                )
            )
            scaler_names = [name for name, _, _ in scaler_rows]
            scaler_mean = [mean for _, mean, _ in scaler_rows]
            scaler_scale = [scale for _, _, scale in scaler_rows]
            for name in LANDCOVER_FEATURE_NAMES:
                if name not in scaler_names:
                    scaler_names.append(name)
                    scaler_mean.append(0.0)
                    scaler_scale.append(1.0)
            for name in CLIMATE_FEATURE_NAMES:
                if name not in scaler_names:
                    scaler_names.append(name)
                    scaler_mean.append(0.0)
                    scaler_scale.append(1.0)
            scaler = Scaler(
                feature_names=tuple(scaler_names),
                mean=tuple(scaler_mean),
                scale=tuple(scaler_scale),
            )
            month_scalers: dict[int, Scaler] = {}
            if "scaler_month" in catalog_tables:
                by_month: dict[int, list[tuple[str, float, float]]] = {}
                for month, feature_name, mean, scale in connection.execute(
                    "SELECT month, feature_name, mean, scale FROM scaler_month "
                    "ORDER BY month, position"
                ):
                    by_month.setdefault(int(month), []).append(
                        (feature_name, mean, scale)
                    )
                for month, rows in by_month.items():
                    names = [name for name, _, _ in rows]
                    means = [mean for _, mean, _ in rows]
                    scales = [scale for _, _, scale in rows]
                    month_scalers[month] = Scaler(
                        feature_names=tuple(names),
                        mean=tuple(means),
                        scale=tuple(scales),
                    )
            return cls(
                path=path,
                dayhikes=dayhikes,
                ingest_report=IngestReport(
                    kept=report_data["kept"],
                    dropped_unnamed=report_data["dropped_unnamed"],
                    dropped_length=report_data["dropped_length"],
                    dropped_network=report_data["dropped_network"],
                    named_osm_name=report_data.get("named_osm_name", 0),
                    named_osm_ref=report_data.get("named_osm_ref", 0),
                    named_synthetic=report_data.get("named_synthetic", 0),
                ),
                attribution=meta["attribution"],
                scaler=scaler,
                month_scalers=month_scalers,
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


def geometry_store_path_for(catalog_path: Path | str) -> Path:
    """Return the GeometryStore path written next to a Catalog SQLite file."""
    path = Path(catalog_path)
    if path.suffix == ".sqlite":
        return path.with_name(f"{path.stem}.geometry.sqlite")
    return path.with_name(f"{path.name}.geometry.sqlite")


def dayhike_label(relation_id: int, tags: Mapping[str, str]) -> tuple[str, str]:
    """Return the Seed label and NameOrigin for a hiking relation."""
    name = (tags.get("name") or "").strip()
    if name:
        return name, NAME_ORIGIN_OSM_NAME
    ref = (tags.get("ref") or "").strip()
    if ref:
        return ref, NAME_ORIGIN_OSM_REF
    return f"relation:{relation_id}", NAME_ORIGIN_SYNTHETIC


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


def rank_dayhikes(
    catalog: Catalog, seeds: Sequence[str], *, month: int | None = None
) -> Ranking:
    """Rank Catalog DayHikes by maximum cosine similarity to named Seeds.

    Similarity is cosine of FeatureVectors after the Catalog StandardScaler.
    Seeds are resolved with ``resolve_seed`` and omitted from the Ranking.
    Optional QueryMonth substitutes that month's climate into the three
    climate slots and scales those slots on that month's slice.
    """
    if month is not None:
        _require_query_month(catalog, month)
    seed_ids = {resolve_seed(catalog, name) for name in seeds}
    ids = [hike.id for hike in catalog.dayhikes]
    names = [hike.name for hike in catalog.dayhikes]
    matrix = feature_matrix(catalog.dayhikes)
    if month is None:
        scaled = scale_matrix(matrix, catalog.scaler)
    else:
        matrix = _climate_matrix_for_month(catalog.dayhikes, matrix, month)
        scaled = scale_matrix(matrix, catalog.scaler)
        scaled = _rescale_climate_slice(
            scaled, matrix, catalog.scaler, catalog.month_scalers[month]
        )
    by_id = {hike.id: hike for hike in catalog.dayhikes}
    return Ranking(
        items=tuple(
            RankedDayHike(dayhike=by_id[dayhike_id], score=score)
            for dayhike_id, _name, score in rank_scaled_rows(
                ids, names, scaled, seed_ids
            )
        )
    )


def _require_query_month(catalog: Catalog, month: int) -> None:
    if isinstance(month, bool) or not isinstance(month, int) or month < 1 or month > 12:
        raise ValueError(f"QueryMonth must be an integer from 1 to 12, got {month!r}")
    sampled = any(
        values is not None for hike in catalog.dayhikes for values in hike.climate_months
    )
    if not sampled or month not in catalog.month_scalers:
        raise ValueError("climate has not been sampled")


def _climate_matrix_for_month(
    dayhikes: Sequence[DayHike], matrix: np.ndarray, month: int
) -> np.ndarray:
    start = FEATURE_NAMES.index("temperature_c")
    out = np.array(matrix, dtype=float, copy=True)
    for index, hike in enumerate(dayhikes):
        values = hike.climate_months[month - 1]
        if values is None:
            out[index, start : start + 3] = np.nan
        else:
            out[index, start : start + 3] = values
    return out


def _rescale_climate_slice(
    scaled: np.ndarray,
    matrix: np.ndarray,
    scaler: Scaler,
    month_scaler: Scaler,
) -> np.ndarray:
    out = np.array(scaled, dtype=float, copy=True)
    for name, mean, scale in zip(
        month_scaler.feature_names, month_scaler.mean, month_scaler.scale, strict=True
    ):
        column = scaler.feature_names.index(name)
        values = matrix[:, column]
        missing = np.isnan(values)
        out[:, column] = np.where(missing, 0.0, (values - mean) / scale)
    return out


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
    """Ingest OSM XML or PBF snapshot(s) into a Catalog of DayHikes.

    Streams the snapshot in passes and keeps only hiking-route members and
    amenities in a work file next to the Catalog, so the source does not need
    to fit in RAM. Re-running the same inputs resumes that work file.
    ``fresh=True`` discards it and starts again. ``progress`` receives one
    line per stage (filter, relations, ways, nodes, access, assemble, write).

    Unnamed `highway=path` ways, out-of-band lengths, and iwn/nwn superroutes are
    counted in the ingest report and are not DayHikes. A missing OSM `name` is
    kept with a Seed label (`name`, else `ref`, else `relation:{id}`). Attribution is stored
    with the Catalog. DriveTime is not computed at ingest. Replaces the
    SQLite file if it exists, only after the new file is complete; overlapping
    snapshots keep one row per OSM id.
    """
    from dayhike.ingest import (
        cleanup_access_sidecars,
        emit_progress,
        run_ingest,
        write_geometry_store,
    )

    catalog_path = Path(catalog_path)
    kept, report, work_path, access_points = run_ingest(
        _osm_paths(osm_path), catalog_path, progress=progress, fresh=fresh
    )
    scaler = fit_scaler_from_matrix(feature_matrix(kept), FEATURE_NAMES)
    emit_progress(progress, "write", str(catalog_path))
    _write_sqlite(catalog_path, kept, report, scaler)
    if work_path.exists():
        work_conn = sqlite3.connect(work_path)
        try:
            write_geometry_store(
                geometry_store_path_for(catalog_path), kept, work_conn, access_points
            )
        finally:
            work_conn.close()
        work_path.unlink()
    cleanup_access_sidecars(catalog_path)
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
    amenity_index: _AmenityIndex | None,
) -> DayHike:
    # The main walk starts at the Trailhead.
    latitude, longitude = points[0]
    sac_scale = _max_sac_scale(relation, snapshot.ways)
    amenity_facts = _amenity_facts_at(latitude, longitude, amenity_index)
    elevation_min_m, elevation_max_m, elevation_gain_m = _elevation_stats(
        vertices, snapshot.node_elevations
    )
    name, name_origin = dayhike_label(relation.id, relation.tags)
    return DayHike(
        id=relation.id,
        name=name,
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
        name_origin=name_origin,
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
            if kind not in nearest:
                continue
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
        # OSM ele absent: missing, not zero. sample-dem fills this from FABDEM.
        return None, None, None
    gain_m = 0.0
    for start, end in zip(elevations, elevations[1:]):
        if end > start:
            gain_m += end - start
    return min(elevations), max(elevations), gain_m


LEFTOVER_ROLES = frozenset(
    {"alternative", "alternate", "variant", "variation", "detour", "excursion", "connection"}
)


@dataclass(frozen=True)
class MainWalk:
    """DayHikeGeometry of one relation: what ingest needs from one assembly.

    ``vertices`` are the ordered (node id, point) pairs from the Trailhead and
    ``length_m`` their line length, joined gaps included. The per-listing
    lists follow member-way listed order (ways missing from the snapshot
    skipped): ``in_chain`` whether any part of the way is walked,
    ``leftovers`` None when walked else the Leftover reason, ``walked_m`` the
    metres of the way on the walk and ``walk_order`` its position along the
    walk (None for Leftovers). ``unconnected_m`` is the distinct member-way
    length that is not walked; joined gaps are not counted.
    ``access_search_points`` are where ingest looks for access points: both
    ends of every dead end of the main piece, a line's two ends, and points
    along a loop no further apart than ``TRAILHEAD_ACCESS_RADIUS_M``. They do
    not depend on the access points.
    """

    vertices: list[tuple[int, tuple[float, float]]]
    length_m: float
    in_chain: list[bool]
    unconnected_m: float
    leftovers: list[str | None]
    walked_m: list[float]
    walk_order: list[int | None]
    access_search_points: list[tuple[float, float]]

    @property
    def trailhead(self) -> tuple[float, float]:
        return self.vertices[0][1]


@dataclass(frozen=True)
class _Edge:
    # A member way split at shared nodes, or a joined gap (way_id None).
    way_id: int | None
    rank: tuple[int, int]  # (listing, piece along the way): member order
    node_ids: tuple[int, ...]
    length_m: float


_Segment = tuple[int, tuple[int, ...]]  # (edge, its node ids in walking direction)


def assemble_main_walk(
    relation: OsmRelation,
    ways: Mapping[int, OsmWay],
    nodes: Mapping[int, tuple[float, float]],
    access_tree: BallTree | None,
) -> MainWalk:
    """The one place member ways become a DayHike's line (ADR-0012).

    Distinct member ways form a graph split at every shared node. Way ends of
    different pieces within ``GAP_JOIN_M`` are joined; the longest piece is
    walked. A piece whose closed loop, after setting aside dead ends and then
    shortcuts, is at least ``LOOP_SHARE`` of its length is a loop: it walks
    that loop plus at most one stem (see ``_stem``), from the stem's far end or
    else from its vertex nearest ``access_tree``, in member-order direction.
    Otherwise it is a line: it sheds its shortest dead ends until two remain,
    then its shortest odd-junction cycle paths until it can be walked once,
    from the end ``_pick_trailhead`` chooses with ``access_tree``.
    """
    listed: list[int] = []  # way id per member-way listing in the snapshot
    leftovers: list[str | None] = []
    first_listing: dict[int, int] = {}
    role_only: dict[int, OsmWay] = {}
    for index, (member_type, member_id) in enumerate(relation.members):
        if member_type != "way":
            continue
        way = ways.get(member_id)
        if way is None:
            continue
        role = relation.roles[index] if index < len(relation.roles) else ""
        if role in LEFTOVER_ROLES:
            leftovers.append("role")
            role_only[member_id] = way
        elif member_id in first_listing:
            leftovers.append("repeat")
        else:
            first_listing[member_id] = len(listed)
            leftovers.append(None)
        listed.append(member_id)
    for way_id in first_listing:
        role_only.pop(way_id, None)

    way_nodes = {
        way_id: [node_id for node_id in ways[way_id].node_ids if node_id in nodes]
        for way_id in first_listing
    }
    edges = _split_ways(first_listing, way_nodes, nodes)
    edges += _gap_edges(edges, way_nodes, nodes, len(listed))
    removed = _main_piece_removals(edges)
    alive = {index for index in range(len(edges)) if index not in removed}
    search_points = [
        nodes[node_id] for _, tip, far in _dead_ends(edges, alive) for node_id in (tip, far)
    ]
    loop = _loop(edges, alive)
    if loop is None:
        _shed_dead_ends(edges, alive, removed)
        _shed_shortcuts(edges, alive, removed, max_odd=2)
        segments = _segments(edges, _walk(edges, alive, nodes, access_tree))
        if segments:
            search_points += [nodes[segments[0][1][0]], nodes[segments[-1][1][-1]]]
    else:
        core, shortcuts = loop
        for index in shortcuts:
            alive.discard(index)
            removed[index] = "shortcut"
        circuit = _segments(edges, _walk(edges, core, nodes, None))
        search_points += _points_every(
            [nodes[node_id] for _, node_ids in circuit for node_id in node_ids],
            TRAILHEAD_ACCESS_RADIUS_M,
        )
        segments = _loop_walk(edges, alive, core, circuit, nodes, access_tree, removed)
    for index in alive - {index for index, _ in segments}:
        removed[index] = "shortcut"

    vertices: list[tuple[int, tuple[float, float]]] = []
    walked_m: dict[int, float] = {}
    for index, node_ids in segments:
        for node_id in node_ids[1:] if vertices else node_ids:
            point = nodes[node_id]
            if not vertices or vertices[-1][1] != point:
                vertices.append((node_id, point))
        way_id = edges[index].way_id
        if way_id is not None:
            walked_m[way_id] = walked_m.get(way_id, 0.0) + _linestring_length_m(
                [nodes[node_id] for node_id in node_ids]
            )
    order = {way_id: position for position, way_id in enumerate(walked_m)}
    reasons: dict[int, str] = {}
    for index, reason in removed.items():
        way_id = edges[index].way_id
        if way_id is not None and way_id not in walked_m:
            reasons.setdefault(way_id, reason)

    in_chain: list[bool] = []
    walked: list[float] = []
    walk_order: list[int | None] = []
    for listing, way_id in enumerate(listed):
        is_walked = leftovers[listing] is None and way_id in walked_m
        if leftovers[listing] is None and not is_walked:
            leftovers[listing] = reasons.get(way_id, "disconnected")
        in_chain.append(is_walked)
        walked.append(walked_m[way_id] if is_walked else 0.0)
        walk_order.append(order[way_id] if is_walked else None)
    unconnected_m = sum(
        _member_way_length_m(list(way.node_ids), nodes) for way in role_only.values()
    ) + sum(
        edges[index].length_m for index in removed if edges[index].way_id is not None
    )
    length_m = _linestring_length_m([point for _, point in vertices])
    return MainWalk(
        vertices,
        length_m,
        in_chain,
        unconnected_m,
        leftovers,
        walked,
        walk_order,
        search_points,
    )


def _split_ways(
    first_listing: Mapping[int, int],
    way_nodes: Mapping[int, list[int]],
    nodes: Mapping[int, tuple[float, float]],
) -> list[_Edge]:
    # Split at every node two member ways share or one way repeats. A closed
    # piece is split once more in its middle so no edge starts where it ends.
    uses = Counter(node_id for ids in way_nodes.values() for node_id in ids)
    edges: list[_Edge] = []
    for way_id, listing in first_listing.items():
        ids = way_nodes[way_id]
        pieces: list[list[int]] = []
        start = 0
        for position in range(1, len(ids)):
            if position == len(ids) - 1 or uses[ids[position]] > 1:
                piece = ids[start : position + 1]
                if piece[0] != piece[-1]:
                    pieces.append(piece)
                elif len(piece) > 2:
                    middle = len(piece) // 2
                    pieces += [piece[: middle + 1], piece[middle:]]
                start = position
        for number, piece in enumerate(pieces):
            points = [nodes[node_id] for node_id in piece]
            edges.append(
                _Edge(way_id, (listing, number), tuple(piece), _linestring_length_m(points))
            )
    return edges


def _gap_edges(
    edges: Sequence[_Edge],
    way_nodes: Mapping[int, list[int]],
    nodes: Mapping[int, tuple[float, float]],
    gap_rank: int,
) -> list[_Edge]:
    # Join pieces whose way ends are within GAP_JOIN_M, closest first, as long
    # as they are still different pieces.
    pieces = _UnionFind()
    for edge in edges:
        pieces.union(edge.node_ids[0], edge.node_ids[-1])
    ends = sorted({node_id for ids in way_nodes.values() if ids for node_id in (ids[0], ids[-1])})
    if len({pieces.find(node_id) for node_id in ends}) < 2:
        return []
    points = np.radians([nodes[node_id] for node_id in ends])
    tree = BallTree(points, metric="haversine")
    near, distances = tree.query_radius(
        points, r=GAP_JOIN_M / EARTH_RADIUS_M, return_distance=True
    )
    pairs = sorted(
        (float(distance * EARTH_RADIUS_M), ends[i], ends[int(j)])
        for i, (hits, dists) in enumerate(zip(near, distances))
        for j, distance in zip(hits, dists)
        if i < j and pieces.find(ends[i]) != pieces.find(ends[int(j)])
    )
    gaps: list[_Edge] = []
    for distance_m, first, second in pairs:
        if pieces.find(first) == pieces.find(second):
            continue
        pieces.union(first, second)
        gaps.append(_Edge(None, (gap_rank, len(gaps)), (first, second), distance_m))
    return gaps


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, item: int) -> int:
        root = item
        while (parent := self.parent.setdefault(root, root)) != root:
            root = parent
        while item != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, first: int, second: int) -> None:
        self.parent[self.find(first)] = self.find(second)


def _main_piece_removals(edges: Sequence[_Edge]) -> dict[int, str]:
    # The longest piece by way length is walked; the rest is disconnected.
    pieces = _UnionFind()
    for edge in edges:
        pieces.union(edge.node_ids[0], edge.node_ids[-1])
    length_m: dict[int, float] = {}
    earliest: dict[int, tuple[int, int]] = {}
    for edge in edges:
        root = pieces.find(edge.node_ids[0])
        length_m[root] = length_m.get(root, 0.0) + (edge.way_id is not None) * edge.length_m
        earliest[root] = min(earliest.get(root, edge.rank), edge.rank)
    if not length_m:
        return {}
    main = min(length_m, key=lambda root: (-length_m[root], earliest[root]))
    return {
        index: "disconnected"
        for index, edge in enumerate(edges)
        if pieces.find(edge.node_ids[0]) != main
    }


def _incidence(edges: Sequence[_Edge], alive: set[int]) -> dict[int, list[int]]:
    incident: dict[int, list[int]] = {}
    for index in alive:
        edge = edges[index]
        incident.setdefault(edge.node_ids[0], []).append(index)
        incident.setdefault(edge.node_ids[-1], []).append(index)
    return incident


def _other_end(edge: _Edge, node_id: int) -> int:
    return edge.node_ids[-1] if edge.node_ids[0] == node_id else edge.node_ids[0]


def _dead_ends(edges: Sequence[_Edge], alive: set[int]) -> list[tuple[list[int], int, int]]:
    # (path, tip, far node) of every dead end: from a degree-1 node to the
    # first node whose degree is not 2.
    incident = _incidence(edges, alive)
    dead_ends = []
    for tip, here in incident.items():
        if len(here) != 1:
            continue
        path, node_id = [], tip
        while True:
            index = next(index for index in incident[node_id] if index not in path)
            path.append(index)
            node_id = _other_end(edges[index], node_id)
            if len(incident[node_id]) != 2:
                break
        dead_ends.append((path, tip, node_id))
    return dead_ends


def _shed_dead_ends(edges: Sequence[_Edge], alive: set[int], removed: dict[int, str]) -> None:
    # A dead end runs from a degree-1 node to the first junction of degree 3+.
    # While more than two remain, the shortest is a Leftover.
    while True:
        dead_ends = _dead_ends(edges, alive)
        if len(dead_ends) <= 2:
            return
        shortest = min(
            (path for path, _, _ in dead_ends),
            key=lambda path: (
                sum(edges[index].length_m for index in path),
                min(edges[index].rank for index in path),
            ),
        )
        for index in shortest:
            alive.discard(index)
            removed[index] = "dead_end"


def _loop(edges: Sequence[_Edge], alive: set[int]) -> tuple[set[int], set[int]] | None:
    """(closed loop, shortcuts) when the piece is a loop, else None.

    Dead ends are set aside down to the piece's cycles, then shortcuts until
    every node is even. The piece is a loop when what remains is at least
    ``LOOP_SHARE`` of its length. A figure-eight (all junctions even) stays
    whole.
    """
    incident = _incidence(edges, alive)
    degree = {node_id: len(here) for node_id, here in incident.items()}
    core = set(alive)
    tips = [node_id for node_id, count in degree.items() if count == 1]
    while tips:
        index = next((index for index in incident[tips.pop()] if index in core), None)
        if index is None:
            continue
        core.discard(index)
        for node_id in (edges[index].node_ids[0], edges[index].node_ids[-1]):
            degree[node_id] -= 1
            if degree[node_id] == 1:
                tips.append(node_id)
    shortcuts: dict[int, str] = {}
    _shed_shortcuts(edges, core, shortcuts, max_odd=0)
    if not core or any(len(here) % 2 for here in _incidence(edges, core).values()):
        return None
    loop_m = sum(edges[index].length_m for index in core)
    if loop_m < LOOP_SHARE * sum(edges[index].length_m for index in alive):
        return None
    return core, set(shortcuts)


def _shed_shortcuts(
    edges: Sequence[_Edge], alive: set[int], removed: dict[int, str], max_odd: int
) -> None:
    # While more than ``max_odd`` nodes are odd, the shortest path between two
    # odd junctions whose removal keeps the piece connected is a Leftover.
    while True:
        incident = _incidence(edges, alive)
        odd = [node_id for node_id, here in incident.items() if len(here) % 2 == 1]
        if len(odd) <= max_odd:
            return
        path = _shortest_removable_path(
            edges, alive, incident, [node_id for node_id in odd if len(incident[node_id]) >= 3]
        )
        if path is None:
            return
        for index in path:
            alive.discard(index)
            removed[index] = "shortcut"


def _shortest_removable_path(
    edges: Sequence[_Edge],
    alive: set[int],
    incident: Mapping[int, list[int]],
    sources: Sequence[int],
) -> list[int] | None:
    # Grow shortest-path regions from every source at once; an edge between
    # two regions closes a path between their sources. The global shortest such
    # path is the first candidate.
    distance: dict[int, float] = {}
    owner: dict[int, int] = {}
    via: dict[int, int] = {}
    queue: list[tuple[float, int, int, int]] = [(0.0, node_id, node_id, -1) for node_id in sources]
    heapq.heapify(queue)
    while queue:
        dist, node_id, source, index = heapq.heappop(queue)
        if node_id in distance:
            continue
        distance[node_id], owner[node_id], via[node_id] = dist, source, index
        for index in incident[node_id]:
            other = _other_end(edges[index], node_id)
            if other not in distance:
                heapq.heappush(queue, (dist + edges[index].length_m, other, source, index))
    candidates = []
    for index in alive:
        first, second = edges[index].node_ids[0], edges[index].node_ids[-1]
        if owner[first] == owner[second]:
            continue
        length_m = distance[first] + edges[index].length_m + distance[second]
        path = [index] + _path_back(edges, via, first) + _path_back(edges, via, second)
        candidates.append((length_m, min(edges[i].rank for i in path), path))
    for _, _, path in sorted(candidates, key=lambda candidate: candidate[:2]):
        if _connected(edges, alive - set(path)):
            return path
    return None


def _path_back(edges: Sequence[_Edge], via: Mapping[int, int], node_id: int) -> list[int]:
    path = []
    while via[node_id] != -1:
        path.append(via[node_id])
        node_id = _other_end(edges[via[node_id]], node_id)
    return path


def _connected(edges: Sequence[_Edge], alive: set[int]) -> bool:
    incident = _incidence(edges, alive)
    if not incident:
        return True
    start = next(iter(incident))
    seen, stack = {start}, [start]
    while stack:
        node_id = stack.pop()
        for index in incident[node_id]:
            other = _other_end(edges[index], node_id)
            if other not in seen:
                seen.add(other)
                stack.append(other)
    return len(seen) == len(incident)


def _walk(
    edges: Sequence[_Edge],
    alive: set[int],
    nodes: Mapping[int, tuple[float, float]],
    access_tree: BallTree | None,
) -> list[tuple[int, int]]:
    """(edge, node it is entered from) along the walk, from the Trailhead.

    Each edge once (Hierholzer), taking the edge earliest in member order
    wherever there is a choice. A line starts at the end ``_pick_trailhead``
    chooses; a piece with no odd node starts where member order starts it.
    """
    incident = _incidence(edges, alive)
    if not incident:
        return []
    for here in incident.values():
        here.sort(key=lambda index: edges[index].rank)
    odd = [node_id for node_id, here in incident.items() if len(here) % 2 == 1]

    def member_order(node_id: int) -> tuple:
        return min(
            (edges[index].rank, edges[index].node_ids[0] != node_id)
            for index in incident[node_id]
        )

    if odd:
        first, last = sorted(odd, key=member_order)[:2]
        if len(odd) == 2 and _pick_trailhead(
            [nodes[first], nodes[last]], access_tree
        ) != nodes[first]:
            first = last
    else:
        first = min(incident, key=member_order)
    if len(odd) > 2:
        # Could not be made walkable once: keep the trail member order gives.
        greedy: list[tuple[int, int]] = []
        node_id, taken = first, set()
        while (index := next((i for i in incident[node_id] if i not in taken), None)) is not None:
            taken.add(index)
            greedy.append((index, node_id))
            node_id = _other_end(edges[index], node_id)
        return greedy
    position = {node_id: 0 for node_id in incident}
    used: set[int] = set()
    stack: list[tuple[int, tuple[int, int] | None]] = [(first, None)]
    trail: list[tuple[int, int]] = []
    while stack:
        node_id, arrived = stack[-1]
        here = incident[node_id]
        while position[node_id] < len(here) and here[position[node_id]] in used:
            position[node_id] += 1
        if position[node_id] < len(here):
            index = here[position[node_id]]
            used.add(index)
            stack.append((_other_end(edges[index], node_id), (index, node_id)))
        else:
            stack.pop()
            if arrived is not None:
                trail.append(arrived)
    trail.reverse()
    return trail


def _segments(
    edges: Sequence[_Edge], trail: Sequence[tuple[int, int]]
) -> list[_Segment]:
    # A segment for each (edge, node it is entered from).
    return [
        (
            index,
            edges[index].node_ids
            if edges[index].node_ids[0] == from_node
            else edges[index].node_ids[::-1],
        )
        for index, from_node in trail
    ]


def _loop_walk(
    edges: Sequence[_Edge],
    alive: set[int],
    core: set[int],
    circuit: list[_Segment],
    nodes: Mapping[int, tuple[float, float]],
    access_tree: BallTree | None,
    removed: dict[int, str],
) -> list[_Segment]:
    # The stem from its far end, then ``circuit`` (member-order direction)
    # from where the stem meets it; without a stem, from the loop's vertex
    # nearest an access point. Every other dead end is a Leftover.
    stem = _stem(edges, alive, core, nodes, access_tree)
    for index in alive - core - {index for index, _ in stem}:
        alive.discard(index)
        removed[index] = "dead_end"
    if stem:
        start = stem[-1][1][-1]
    else:
        start = _loop_start(circuit, nodes, access_tree)
    for position, (index, node_ids) in enumerate(circuit):
        if start in node_ids[:-1]:
            cut = node_ids.index(start)
            rest = circuit[position + 1 :] + circuit[:position]
            head = [(index, node_ids[: cut + 1])] if cut else []
            return stem + [(index, node_ids[cut:])] + rest + head
    return stem + circuit


def _stem(
    edges: Sequence[_Edge],
    alive: set[int],
    core: set[int],
    nodes: Mapping[int, tuple[float, float]],
    access_tree: BallTree | None,
) -> list[_Segment]:
    """The one dead end a loop walks, from its far end to the loop, or [].

    A dead end whose far end is within ``STEM_ACCESS_RADIUS_M`` of an access
    point, nearest first; else the only dead end when it is at least
    ``STEM_MIN_LENGTH_M`` long.
    """
    incident = _incidence(edges, alive)
    toward_loop: dict[int, int] = {}
    stack = [node_id for node_id, here in incident.items() if any(i in core for i in here)]
    seen = set(stack)
    while stack:
        node_id = stack.pop()
        for index in incident[node_id]:
            other = _other_end(edges[index], node_id)
            if index not in core and other not in seen:
                seen.add(other)
                toward_loop[other] = index
                stack.append(other)
    tips = [node_id for node_id, here in incident.items() if len(here) == 1]
    stems: dict[int, list[_Segment]] = {}
    for tip in tips:
        trail, node_id = [], tip
        while node_id in toward_loop:
            trail.append((toward_loop[node_id], node_id))
            node_id = _other_end(edges[toward_loop[node_id]], node_id)
        if trail:
            stems[tip] = _segments(edges, trail)
    if access_tree is not None and stems:
        dist, _ = access_tree.query(np.radians([nodes[tip] for tip in stems]), k=1)
        near = [
            (float(distance) * EARTH_RADIUS_M, min(edges[i].rank for i, _ in stems[tip]), tip)
            for distance, tip in zip(dist[:, 0], stems)
        ]
        near = [candidate for candidate in near if candidate[0] <= STEM_ACCESS_RADIUS_M]
        if near:
            return stems[min(near)[2]]
    if len(tips) == 1 and tips[0] in stems:
        stem = stems[tips[0]]
        if sum(edges[index].length_m for index, _ in stem) >= STEM_MIN_LENGTH_M:
            return stem
    return []


def _loop_start(
    circuit: Sequence[_Segment],
    nodes: Mapping[int, tuple[float, float]],
    access_tree: BallTree | None,
) -> int:
    # The loop vertex nearest an access point within the Trailhead radius,
    # else where member order starts the loop.
    node_ids = [node_id for _, ids in circuit for node_id in ids[:-1]]
    if access_tree is None or not node_ids:
        return circuit[0][1][0]
    dist, _ = access_tree.query(np.radians([nodes[node_id] for node_id in node_ids]), k=1)
    nearest = int(np.argmin(dist[:, 0]))
    if float(dist[nearest, 0]) * EARTH_RADIUS_M > TRAILHEAD_ACCESS_RADIUS_M:
        return circuit[0][1][0]
    return node_ids[nearest]


def _points_every(
    points: Sequence[tuple[float, float]], spacing_m: float
) -> list[tuple[float, float]]:
    # Points along the line, the first one included, no more than spacing_m
    # apart along it.
    if not points:
        return []
    out = [points[0]]
    since_m = 0.0
    for start, end in zip(points, points[1:]):
        step_m = _haversine_m(start[0], start[1], end[0], end[1])
        done_m = 0.0
        while since_m + step_m - done_m > spacing_m:
            done_m += spacing_m - since_m
            share = done_m / step_m
            out.append(
                (start[0] + share * (end[0] - start[0]), start[1] + share * (end[1] - start[1]))
            )
            since_m = 0.0
        since_m += step_m - done_m
    return out


def _member_way_length_m(
    node_ids: Sequence[int], nodes: Mapping[int, tuple[float, float]]
) -> float:
    points = [nodes[node_id] for node_id in node_ids if node_id in nodes]
    if len(points) < 2:
        return 0.0
    return _linestring_length_m(points)


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
        _missing_as_nan(vector.tree_fraction),
        _missing_as_nan(vector.shrub_fraction),
        _missing_as_nan(vector.grass_fraction),
        _missing_as_nan(vector.bare_fraction),
        _missing_as_nan(vector.snow_fraction),
        _missing_as_nan(vector.temperature_c),
        _missing_as_nan(vector.precipitation_mm),
        _missing_as_nan(vector.solar_radiation),
    )


def feature_matrix(dayhikes: Sequence[DayHike]) -> np.ndarray:
    """Stack Catalog FeatureVector rows, with missing values as NaN."""
    if not dayhikes:
        return np.empty((0, len(FEATURE_NAMES)), dtype=float)
    return np.array(
        [_feature_row(hike.feature_vector) for hike in dayhikes], dtype=float
    )


def fit_scaler_from_matrix(
    matrix: np.ndarray, feature_names: Sequence[str] = FEATURE_NAMES
) -> Scaler:
    """Fit a per-column StandardScaler, ignoring NaN. All-NaN columns stay mean 0 scale 1."""
    names = tuple(feature_names)
    n_features = len(names)
    if matrix.size == 0:
        return Scaler(names, (0.0,) * n_features, (1.0,) * n_features)
    if matrix.ndim != 2 or matrix.shape[1] != n_features:
        raise ValueError(
            f"matrix has shape {matrix.shape}, expected (_, {n_features})"
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
    return Scaler(names, tuple(mean), tuple(scale))


def scale_matrix(matrix: np.ndarray, scaler: Scaler) -> np.ndarray:
    """Scale rows with Catalog scaler params. NaN becomes 0 after scaling."""
    if matrix.size == 0:
        return np.empty((0, len(scaler.feature_names)), dtype=float)
    scaled = np.empty(matrix.shape, dtype=float)
    for column, (mean, scale) in enumerate(zip(scaler.mean, scaler.scale, strict=True)):
        values = matrix[:, column]
        missing = np.isnan(values)
        scaled[:, column] = np.where(missing, 0.0, (values - mean) / scale)
    return scaled


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity of two scaled FeatureVector rows."""
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def rank_scaled_rows(
    ids: Sequence[int],
    names: Sequence[str],
    scaled: np.ndarray,
    seed_ids: Sequence[int] | set[int],
) -> tuple[tuple[int, str, float], ...]:
    """Max cosine to any Seed row. Seeds are omitted. Sort by (-score, id)."""
    seed_set = set(seed_ids)
    seed_positions = [index for index, dayhike_id in enumerate(ids) if dayhike_id in seed_set]
    if not seed_positions:
        return ()
    seed_vectors = scaled[seed_positions]
    ranked: list[tuple[int, str, float]] = []
    for index, (dayhike_id, name) in enumerate(zip(ids, names, strict=True)):
        if dayhike_id in seed_set:
            continue
        row = scaled[index]
        score = max(cosine_similarity(row, seed_vector) for seed_vector in seed_vectors)
        ranked.append((dayhike_id, name, score))
    ranked.sort(key=lambda item: (-item[2], item[0]))
    return tuple(ranked)


def _scale_vector(vector: FeatureVector, scaler: Scaler) -> tuple[float, ...]:
    return tuple(scale_matrix(np.array([_feature_row(vector)], dtype=float), scaler)[0])


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
                name_origin TEXT NOT NULL,
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
                difficulty_ordinal INTEGER,
                tree_fraction REAL,
                shrub_fraction REAL,
                grass_fraction REAL,
                bare_fraction REAL,
                snow_fraction REAL,
                temperature_c REAL,
                precipitation_mm REAL,
                solar_radiation REAL
            );
            CREATE TABLE climate_months (
                dayhike_id INTEGER NOT NULL REFERENCES dayhikes(id),
                month INTEGER NOT NULL,
                temperature_c REAL,
                precipitation_mm REAL,
                solar_radiation REAL,
                PRIMARY KEY (dayhike_id, month)
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
                        "named_osm_name": report.named_osm_name,
                        "named_osm_ref": report.named_osm_ref,
                        "named_synthetic": report.named_synthetic,
                    }
                ),
            ),
        )
        connection.executemany(
            "INSERT INTO dayhikes (id, name, name_origin, length_m, difficulty) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (hike.id, hike.name, hike.name_origin, hike.length_m, hike.difficulty.sac_scale)
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
            "elevation_max_m, elevation_gain_m, difficulty_ordinal, tree_fraction, "
            "shrub_fraction, grass_fraction, bare_fraction, snow_fraction, "
            "temperature_c, precipitation_mm, solar_radiation) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    hike.id,
                    hike.feature_vector.length_m,
                    hike.feature_vector.elevation_min_m,
                    hike.feature_vector.elevation_max_m,
                    hike.feature_vector.elevation_gain_m,
                    hike.feature_vector.difficulty_ordinal,
                    hike.feature_vector.tree_fraction,
                    hike.feature_vector.shrub_fraction,
                    hike.feature_vector.grass_fraction,
                    hike.feature_vector.bare_fraction,
                    hike.feature_vector.snow_fraction,
                    hike.feature_vector.temperature_c,
                    hike.feature_vector.precipitation_mm,
                    hike.feature_vector.solar_radiation,
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
