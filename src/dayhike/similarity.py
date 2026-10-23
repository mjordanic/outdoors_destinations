"""Facet similarity for the notebook design phase (ADR-0011, proposed).

Pure measure functions take two facts and return a similarity in [0, 1], or
None when either fact is missing. Production Ranking does not use this module.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString
from sklearn.neighbors import BallTree

from dayhike.catalog import EARTH_RADIUS_M, GAP_JOIN_M, geometry_store_path_for
from dayhike.dem import sample_elevations

AMENITY_PAD_M = 3_000  # vertex box pad for region amenities; covers Peaks around
PROFILE_POINTS = 64
PROFILE_RANGE_FLOOR_M = 50.0
PROFILE_DTW_BAND = 0.1  # Sakoe-Chiba band as a share of the points
CORRIDOR_M = 100.0  # amenity along the DayHike (CONTEXT.md Corridor)
PEAK_SUMMITED_M = 50.0
PEAKS_AROUND_M = 2_000.0
TRAILHEAD_M = 500.0  # amenity at the Trailhead (CONTEXT.md Trailhead)
WATER_SOURCE_KINDS = ("drinking_water", "spring", "water_tap", "water_point")
FACILITIES_FLAG_COLUMNS = (
    "hut_along",
    "restaurant_cafe_along",
    "shelter_along",
    "restaurant_cafe_trailhead",
    "parking_trailhead",
)
LANDCOVER_SHARE_COLUMNS = (
    "tree_share",
    "shrub_share",
    "grass_share",
    "bare_share",
    "snow_share",
    "other_share",
)
CLIMATE_NAMES = ("temperature_c", "precipitation_mm", "solar_radiation")
CLIMATE_MONTH_COLUMNS = tuple(
    f"{name}_{month:02d}" for month in range(1, 13) for name in CLIMATE_NAMES
)
SOLAR_ASPECT_POINTS = 32  # about this many vertices sampled per DayHike
SOLAR_ASPECT_OFFSET_ARCSEC = 2  # east and north offsets: two FABDEM cells
SOLAR_ASPECT_MIN_SLOPE_DEG = 5.0  # flatter points face no way
SURFACE_SHARE_COLUMNS = ("paved_share", "gravel_share", "natural_share", "rock_share")
_SURFACE_TAG_CLASS = {
    **dict.fromkeys(
        ("asphalt", "concrete", "paving_stones", "sett", "cobblestone", "paved"),
        "paved",
    ),
    **dict.fromkeys(
        ("compacted", "gravel", "fine_gravel", "unpaved", "pebblestone"), "gravel"
    ),
    **dict.fromkeys(
        ("ground", "dirt", "earth", "grass", "mud", "sand", "woodchips"), "natural"
    ),
    **dict.fromkeys(("rock", "bare_rock", "stone"), "rock"),
}
_ROAD_HIGHWAYS = frozenset(
    (
        "motorway", "trunk", "primary", "secondary", "tertiary", "residential",
        "service", "living_street", "pedestrian", "cycleway", "unclassified",
    )
)  # fmt: skip
_TRACKTYPE_CLASS = {
    "grade1": "paved",
    "grade2": "gravel",
    "grade3": "gravel",
    "grade4": "natural",
    "grade5": "natural",
}


def similarity_facts_path_for(catalog_path: Path | str) -> Path:
    """Return the similarity facts table path written next to a Catalog."""
    path = Path(catalog_path)
    stem = path.stem if path.suffix == ".sqlite" else path.name
    return path.with_name(f"{stem}.similarity_facts.pkl")


def _missing(value: object) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def ratio(a: float | None, b: float | None, *, floor: float) -> float | None:
    """Size measure: smaller over larger, each raised to at least ``floor``."""
    if _missing(a) or _missing(b):
        return None
    a, b = max(a, floor), max(b, floor)
    return min(a, b) / max(a, b)


def tolerance(a: float | None, b: float | None, *, d: float) -> float | None:
    """Level measure: 1 at equal values, 0 once they differ by ``d`` or more."""
    if _missing(a) or _missing(b):
        return None
    return max(0.0, 1.0 - abs(a - b) / d)


def mix(p: Sequence[float] | None, q: Sequence[float] | None) -> float | None:
    """Mix measure: the shared share of two fraction lists that each sum to 1."""
    if p is None or q is None or any(_missing(x) for x in (*p, *q)):
        return None
    return min(1.0, sum(min(x, y) for x, y in zip(p, q, strict=True)))


def presence(
    a: Sequence[bool | None] | None, b: Sequence[bool | None] | None
) -> float | None:
    """Presence measure: share of yes/no flags that agree; both-absent agrees.

    A flag missing on either side drops out; no comparable flag gives None.
    """
    if a is None or b is None:
        return None
    pairs = [
        (x, y)
        for x, y in zip(a, b, strict=True)
        if not _missing(x) and not _missing(y)
    ]
    if not pairs:
        return None
    return sum(bool(x) == bool(y) for x, y in pairs) / len(pairs)


def facet_score(measures: Sequence[float | None]) -> float | None:
    """FacetScore: plain mean of the measures that are not missing."""
    present = [m for m in measures if not _missing(m)]
    if not present:
        return None
    return sum(present) / len(present)


def combined_score(
    facet_scores: Mapping[str, float | None], facet_weights: Mapping[str, float]
) -> tuple[float | None, float]:
    """Weighted mean of the scored Facets, and the share of weight scored.

    FacetWeights may be in any scale and are normalized to sum to 1 here;
    weights of 0 or less have no effect. A
    Facet with a missing FacetScore drops out and the rest renormalize.
    """
    positive = {facet: w for facet, w in facet_weights.items() if w > 0}
    scored = {
        facet: w
        for facet, w in positive.items()
        if not _missing(facet_scores.get(facet))
    }
    total = sum(positive.values())
    scored_weight = sum(scored.values())
    if not scored:
        return None, 0.0
    score = sum(w * facet_scores[facet] for facet, w in scored.items()) / scored_weight
    return score, scored_weight / total


def multi_seed_score(seed_scores: Sequence[float | None]) -> float | None:
    """Several Seeds: a candidate's score is its best combined score to any Seed."""
    present = [s for s in seed_scores if not _missing(s)]
    return max(present) if present else None


def profile_shape(
    latitude: Sequence[float],
    longitude: Sequence[float],
    elevation: Sequence[float],
) -> np.ndarray | None:
    """ProfileShape of one ElevationProfile given as vertices in walk order.

    Elevation is resampled at ``PROFILE_POINTS`` equal steps of the
    cumulative-distance fraction 0 to 1 (haversine along every vertex), then the
    minimum is subtracted and the result divided by max(range,
    ``PROFILE_RANGE_FLOOR_M``), so a nearly flat DayHike stays nearly flat.
    Vertices without elevation are skipped; fewer than two with elevation gives
    None.
    """
    lat = np.radians(np.asarray(latitude, dtype=float))
    lon = np.radians(np.asarray(longitude, dtype=float))
    ele = np.asarray(elevation, dtype=float)
    step = 2 * EARTH_RADIUS_M * np.arcsin(
        np.sqrt(
            np.sin(np.diff(lat) / 2) ** 2
            + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(np.diff(lon) / 2) ** 2
        )
    )
    distance = np.concatenate([[0.0], np.cumsum(step)])
    has_ele = np.isfinite(ele)
    if has_ele.sum() < 2 or distance[-1] <= 0:
        return None
    at, ele = distance[has_ele], ele[has_ele]
    keep = np.concatenate([[True], np.diff(at) > 0])
    resampled = np.interp(
        np.linspace(0.0, distance[-1], PROFILE_POINTS), at[keep], ele[keep]
    )
    low = resampled.min()
    return (resampled - low) / max(resampled.max() - low, PROFILE_RANGE_FLOOR_M)


def profile_lockstep(a: np.ndarray | None, b: np.ndarray | None) -> float | None:
    """Profile measure, point by point: 1 - mean abs difference of two
    ProfileShapes, the better of ``b`` walked forward or reversed.
    """
    if _missing(a) or _missing(b):
        return None
    a, b = np.asarray(a), np.asarray(b)
    return 1.0 - min(
        float(np.mean(np.abs(a - b))), float(np.mean(np.abs(a - b[::-1])))
    )


def profile_dtw(
    a: np.ndarray | None, b: np.ndarray | None, *, band: float = PROFILE_DTW_BAND
) -> float | None:
    """Profile measure with banded dynamic time warping: 1 - cost, the better
    of ``b`` walked forward or reversed.

    The warping path may stray at most ``band`` of the points off the diagonal
    (Sakoe-Chiba). Cost is the mean abs difference along the cheapest path, so
    it is never above the lock-step mean and 1 - cost stays in [0, 1].
    """
    if _missing(a) or _missing(b):
        return None
    a, b = np.asarray(a), np.asarray(b)
    radius = round(band * len(a))
    return 1.0 - min(_dtw_mean_cost(a, b, radius), _dtw_mean_cost(a, b[::-1], radius))


def _dtw_mean_cost(a: np.ndarray, b: np.ndarray, radius: int) -> float:
    """Mean abs difference along the cheapest banded warping path.

    Each cell keeps (summed cost, path length); ties on cost take the shorter
    path, so swapping ``a`` and ``b`` gives the same result.
    """
    a, b = a.tolist(), b.tolist()
    n = len(a)
    previous = [(0.0, 0)] + [(math.inf, 0)] * n
    for i in range(1, n + 1):
        current = [(math.inf, 0)] * (n + 1)
        for j in range(max(1, i - radius), min(n, i + radius) + 1):
            cost, length = min(previous[j - 1], previous[j], current[j - 1])
            current[j] = (cost + abs(a[i - 1] - b[j - 1]), length + 1)
        previous = current
    cost, length = previous[n]
    return cost / length


def load_region(
    catalog_path: Path | str,
    *,
    lat_range: tuple[float, float],
    lon_range: tuple[float, float],
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """GeometryStore frames for DayHikes whose Trailhead lies in a lat/lon box.

    Returns ``vertices, amenities, dayhikes, ways`` as GeoDataFrames in
    EPSG:4326, shaped as in notebook 04a. ``dayhikes`` carries the Catalog
    ``feature_vectors`` (with ``difficulty_ordinal``), the twelve
    ``climate_months`` as ``CLIMATE_MONTH_COLUMNS`` (``temperature_c_01`` ...
    ``solar_radiation_12``) and the Trailhead as ``trailhead_latitude`` /
    ``trailhead_longitude``. Rows are filtered in SQL. Amenities are those
    inside the region's vertex box padded by ``AMENITY_PAD_M``.
    """
    in_region = "IN (SELECT value FROM json_each(?))"
    catalog = sqlite3.connect(catalog_path)
    try:
        summaries = pd.read_sql_query(
            "SELECT t.latitude AS trailhead_latitude, "
            "t.longitude AS trailhead_longitude, f.* "
            "FROM trailheads t JOIN feature_vectors f "
            "ON f.dayhike_id = t.dayhike_id "
            "WHERE t.latitude BETWEEN ? AND ? AND t.longitude BETWEEN ? AND ?",
            catalog,
            params=(*lat_range, *lon_range),
        ).drop(columns="length_m")
        ids = (json.dumps(summaries["dayhike_id"].tolist()),)
        month_columns = ", ".join(
            f"MAX(CASE WHEN month = {int(column[-2:])} THEN {column[:-3]} END) AS {column}"
            for column in CLIMATE_MONTH_COLUMNS
        )
        months = pd.read_sql_query(
            f"SELECT dayhike_id, {month_columns} FROM climate_months "
            f"WHERE dayhike_id {in_region} GROUP BY dayhike_id",
            catalog,
            params=ids,
        )
    finally:
        catalog.close()
    summaries = summaries.merge(months, on="dayhike_id", how="left")

    geo = sqlite3.connect(geometry_store_path_for(catalog_path))
    try:
        dayhikes_tbl = pd.read_sql_query(
            f"SELECT * FROM dayhikes WHERE id {in_region} ORDER BY id", geo, params=ids
        )
        vertices_tbl = pd.read_sql_query(
            f"SELECT * FROM vertices WHERE dayhike_id {in_region} "
            "ORDER BY dayhike_id, seq",
            geo,
            params=ids,
        )
        ways_tbl = pd.read_sql_query(
            f"SELECT * FROM ways WHERE dayhike_id {in_region} ORDER BY dayhike_id, seq",
            geo,
            params=ids,
        )
        pad_lat = math.degrees(AMENITY_PAD_M / EARTH_RADIUS_M)
        far_lat = vertices_tbl["latitude"].abs().max()
        pad_lon = pad_lat / math.cos(math.radians(far_lat))
        amenities_tbl = pd.read_sql_query(
            "SELECT * FROM amenities WHERE latitude BETWEEN ? AND ? "
            "AND longitude BETWEEN ? AND ?",
            geo,
            params=(
                vertices_tbl["latitude"].min() - pad_lat,
                vertices_tbl["latitude"].max() + pad_lat,
                vertices_tbl["longitude"].min() - pad_lon,
                vertices_tbl["longitude"].max() + pad_lon,
            ),
        )
    finally:
        geo.close()

    vertices = _points(vertices_tbl)
    amenities = _points(amenities_tbl)
    lines = pd.DataFrame(
        {
            "id": dayhike_id,
            "geometry": _line(list(zip(grp["longitude"], grp["latitude"]))),
        }
        for dayhike_id, grp in vertices_tbl.groupby("dayhike_id", sort=True)
    )
    dayhikes = gpd.GeoDataFrame(
        dayhikes_tbl.merge(lines, on="id", how="left")
        .merge(summaries, left_on="id", right_on="dayhike_id", how="left")
        .drop(columns="dayhike_id"),
        geometry="geometry",
        crs="EPSG:4326",
    )
    node_xy = dict(
        zip(
            vertices_tbl["node_id"],
            zip(vertices_tbl["longitude"], vertices_tbl["latitude"]),
        )
    )
    ways = gpd.GeoDataFrame(
        ways_tbl,
        geometry=[
            _line([node_xy[n] for n in json.loads(node_ids) if n in node_xy])
            for node_ids in ways_tbl["node_ids"]
        ],
        crs="EPSG:4326",
    )
    return vertices, amenities, dayhikes, ways


def effort_facts(dayhikes: pd.DataFrame, vertices: pd.DataFrame) -> pd.DataFrame:
    """Effort facts per DayHike id: length, elevation gain, Difficulty rank.

    Elevation gain is the larger of the climb and the descent along the walk,
    so it does not depend on OSM's arbitrary way order. Vertices without
    ``ele`` are skipped; a DayHike with none has a missing gain, not 0.
    """
    ele = vertices.dropna(subset=["ele"]).sort_values(["dayhike_id", "seq"])
    step = ele.groupby("dayhike_id")["ele"].diff()
    climb = step.clip(lower=0).groupby(ele["dayhike_id"]).sum()
    descent = (-step).clip(lower=0).groupby(ele["dayhike_id"]).sum()
    hikes = dayhikes.set_index("id")
    return pd.DataFrame(
        {
            "length_m": hikes["length_m"],
            "elevation_gain_m": climb.combine(descent, max).reindex(hikes.index),
            "difficulty_rank": hikes["difficulty_ordinal"],
        }
    )


def altitude_facts(dayhikes: pd.DataFrame) -> pd.DataFrame:
    """Altitude facts per DayHike id: highest and lowest point (Catalog)."""
    hikes = dayhikes.set_index("id")
    return pd.DataFrame(
        {"highest_m": hikes["elevation_max_m"], "lowest_m": hikes["elevation_min_m"]}
    )


def profile_facts(dayhikes: pd.DataFrame, vertices: pd.DataFrame) -> pd.DataFrame:
    """Profile fact per DayHike id: the ProfileShape, in stored vertex order.

    A DayHike with fewer than two vertices with ``ele`` has a missing
    (None) ProfileShape.
    """
    shapes = {
        dayhike_id: profile_shape(grp["latitude"], grp["longitude"], grp["ele"])
        for dayhike_id, grp in vertices.sort_values(["dayhike_id", "seq"]).groupby(
            "dayhike_id"
        )
    }
    ids = dayhikes["id"]
    shape_per_id = pd.Series([shapes.get(i) for i in ids], index=ids, dtype=object)
    return pd.DataFrame({"profile_shape": shape_per_id})


def corridor_amenities(
    vertices: pd.DataFrame,
    amenities: pd.DataFrame,
    *,
    kind: str,
    radius_m: float,
) -> pd.DataFrame:
    """Amenity points of one ``kind`` within ``radius_m`` of each DayHikeGeometry.

    Distance is measured to the line, not only to its vertices: each segment of
    the walk is densified to at most ``radius_m / 10`` between points
    before a haversine BallTree query, so a point beside the middle of a long
    segment is found and the distance is over-stated by under 0.2% of
    ``radius_m``. Returns one row per (DayHike, amenity point) with columns
    ``dayhike_id``, ``osm_key`` and ``distance_m`` (to the line); a DayHike with
    no point in range has no row. The result does not depend on the walking
    direction.
    """
    points = amenities[amenities["kind"] == kind].reset_index(drop=True)
    none_found = pd.DataFrame({"dayhike_id": [], "osm_key": [], "distance_m": []})
    if points.empty:
        return none_found
    tree = BallTree(
        np.radians(points[["latitude", "longitude"]].to_numpy(float)),
        metric="haversine",
    )
    found = []
    for dayhike_id, chain in vertices.sort_values(["dayhike_id", "seq"]).groupby(
        "dayhike_id"
    ):
        line = _densify(chain["latitude"], chain["longitude"], radius_m / 10)
        hits, distance = tree.query_radius(
            line, r=radius_m / EARTH_RADIUS_M, return_distance=True
        )
        hit, distance = np.concatenate(hits), np.concatenate(distance)
        if hit.size:
            nearest = pd.Series(distance * EARTH_RADIUS_M).groupby(hit).min()
            found.append(
                pd.DataFrame(
                    {
                        "dayhike_id": dayhike_id,
                        "osm_key": points["osm_key"].to_numpy()[nearest.index],
                        "distance_m": nearest.to_numpy(),
                    }
                )
            )
    return pd.concat(found, ignore_index=True) if found else none_found


def _densify(latitude: Sequence[float], longitude: Sequence[float], step_m: float):
    """Vertices as radians (lat, lon) rows, with points inserted so that
    consecutive points are at most ``step_m`` apart.
    """
    lat = np.radians(np.asarray(latitude, dtype=float))
    lon = np.radians(np.asarray(longitude, dtype=float))
    segment = 2 * EARTH_RADIUS_M * np.arcsin(
        np.sqrt(
            np.sin(np.diff(lat) / 2) ** 2
            + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(np.diff(lon) / 2) ** 2
        )
    )
    pieces = np.maximum(1, np.ceil(segment / step_m)).astype(int)
    start = np.repeat(np.arange(len(segment)), pieces)
    along = (np.arange(pieces.sum()) - np.repeat(np.cumsum(pieces) - pieces, pieces)) / (
        pieces[start]
    )
    dense_lat = lat[start] + along * (lat[start + 1] - lat[start])
    dense_lon = lon[start] + along * (lon[start + 1] - lon[start])
    return np.column_stack(
        [np.append(dense_lat, lat[-1]), np.append(dense_lon, lon[-1])]
    )


def landscape_facts(
    dayhikes: pd.DataFrame, vertices: pd.DataFrame, amenities: pd.DataFrame
) -> pd.DataFrame:
    """Landscape facts per DayHike id, in stored order.

    Six LandCover shares: the Catalog's five fractions plus ``other_share`` =
    1 - their sum, clipped at 0; all six are missing when any Catalog fraction
    is. ``peak_summited``: a Peak within ``PEAK_SUMMITED_M`` of the line.
    ``viewpoint_along``: a Viewpoint in the ``CORRIDOR_M`` Corridor.
    ``peaks_around``: Peaks within ``PEAKS_AROUND_M`` of the line.
    """
    ids = dayhikes["id"]
    fractions = (
        dayhikes[
            [
                "tree_fraction",
                "shrub_fraction",
                "grass_fraction",
                "bare_fraction",
                "snow_fraction",
            ]
        ]
        .astype(float)
        .set_axis(LANDCOVER_SHARE_COLUMNS[:5], axis=1)
    )
    fractions["other_share"] = (1.0 - fractions.sum(axis=1, skipna=False)).clip(lower=0)
    fractions = fractions.mask(fractions.isna().any(axis=1))
    fractions.index = ids

    def hikes_with(kind: str, radius_m: float) -> pd.Series:
        return corridor_amenities(
            vertices, amenities, kind=kind, radius_m=radius_m
        )["dayhike_id"].value_counts().reindex(ids, fill_value=0)

    return fractions.assign(
        peak_summited=hikes_with("peak", PEAK_SUMMITED_M) > 0,
        viewpoint_along=hikes_with("viewpoint", CORRIDOR_M) > 0,
        peaks_around=hikes_with("peak", PEAKS_AROUND_M),
    )


def water_facts(
    dayhikes: pd.DataFrame, vertices: pd.DataFrame, amenities: pd.DataFrame
) -> pd.DataFrame:
    """Water facts per DayHike id, in stored order.

    ``water_count``: WaterSources (amenities of a ``WATER_SOURCE_KINDS`` kind) in
    the ``CORRIDOR_M`` Corridor, uncapped. ``dry_stretch_m``: DryStretch, the
    longest distance walked without passing one. Each Corridor water point is
    placed at its position along the line (where the line passes it closest); the
    stretches run from the first vertex to the first point, between points and
    from the last point to the last vertex, and the longest wins. Two points at
    one position split the walk once. With no water the DryStretch is the
    length of the walk (haversine over every vertex). Both facts are
    the same whichever way the DayHike is walked. A DayHike without vertices
    has both missing.
    """
    ids = dayhikes["id"]
    sources = amenities[amenities["kind"].isin(WATER_SOURCE_KINDS)].assign(kind="water")
    found = corridor_amenities(vertices, sources, kind="water", radius_m=CORRIDOR_M)
    spots = amenities.drop_duplicates("osm_key").set_index("osm_key")[["latitude", "longitude"]]
    found = found.join(spots, on="osm_key")
    counts = found["dayhike_id"].value_counts()
    facts = {}
    for dayhike_id, chain in vertices.sort_values(["dayhike_id", "seq"]).groupby("dayhike_id"):
        water = found[found["dayhike_id"] == dayhike_id]
        positions, length = _positions_along(
            chain["latitude"], chain["longitude"], water["latitude"], water["longitude"]
        )
        edges = np.concatenate([[0.0], np.sort(positions), [length]])
        facts[dayhike_id] = (counts.get(dayhike_id, 0), float(np.diff(edges).max()))
    return pd.DataFrame.from_dict(
        facts, orient="index", columns=["water_count", "dry_stretch_m"]
    ).reindex(ids.rename("id"))


def facilities_facts(
    dayhikes: pd.DataFrame, vertices: pd.DataFrame, amenities: pd.DataFrame
) -> pd.DataFrame:
    """Facilities facts per DayHike id, in stored order: five yes/no flags
    (``FACILITIES_FLAG_COLUMNS``).

    Along the DayHike, in the ``CORRIDOR_M`` Corridor (distance to the line):
    ``hut_along``, ``restaurant_cafe_along``, ``shelter_along``. A Shelter is
    never a Hut. At the Trailhead, within ``TRAILHEAD_M`` of the Catalog
    Trailhead (``trailhead_latitude`` / ``trailhead_longitude`` on
    ``dayhikes``, as ``load_region`` supplies; not the 20 km AmenityFact disk):
    ``restaurant_cafe_trailhead``, ``parking_trailhead``. A flag is False when
    the amenity is absent. The along flags do not depend on the walking
    direction.
    """
    ids = dayhikes["id"]

    def along(kind: str) -> pd.Series:
        found = corridor_amenities(vertices, amenities, kind=kind, radius_m=CORRIDOR_M)
        return ids.isin(found["dayhike_id"]).set_axis(ids)

    def at_trailhead(kind: str) -> pd.Series:
        points = amenities[amenities["kind"] == kind]
        if points.empty:
            return pd.Series(False, index=ids)
        tree = BallTree(
            np.radians(points[["latitude", "longitude"]].to_numpy(float)),
            metric="haversine",
        )
        trailheads = np.radians(
            dayhikes[["trailhead_latitude", "trailhead_longitude"]].to_numpy(float)
        )
        near = tree.query_radius(trailheads, r=TRAILHEAD_M / EARTH_RADIUS_M, count_only=True)
        return pd.Series(near > 0, index=ids)

    return pd.DataFrame(
        {
            "hut_along": along("hut"),
            "restaurant_cafe_along": along("restaurant_cafe"),
            "shelter_along": along("shelter"),
            "restaurant_cafe_trailhead": at_trailhead("restaurant_cafe"),
            "parking_trailhead": at_trailhead("parking"),
        }
    ).rename_axis("id")


def _positions_along(
    chain_lat: Sequence[float],
    chain_lon: Sequence[float],
    point_lat: Sequence[float],
    point_lon: Sequence[float],
) -> tuple[np.ndarray, float]:
    """Position (metres from the first vertex) of the closest place on the
    vertex chain to each point, and the chain length.

    Closeness is measured on a flat plane around the chain; distances along
    the chain are haversine, so positions add up to the chain length.
    """
    lat = np.asarray(chain_lat, dtype=float)
    lon = np.asarray(chain_lon, dtype=float)
    segment = np.array(
        [_path_length_m(lat[i : i + 2], lon[i : i + 2]) for i in range(len(lat) - 1)]
    )
    start = np.concatenate([[0.0], np.cumsum(segment)])
    lat0, lon0 = lat.mean(), lon.mean()

    def plane(latitude, longitude):
        y = np.radians(np.asarray(latitude, dtype=float) - lat0) * EARTH_RADIUS_M
        x = np.radians(np.asarray(longitude, dtype=float) - lon0) * EARTH_RADIUS_M * math.cos(
            math.radians(lat0)
        )
        return x, y

    x, y = plane(lat, lon)
    px, py = plane(point_lat, point_lon)
    dx, dy = np.diff(x), np.diff(y)
    flat = dx**2 + dy**2
    # Share of each segment, per (point, segment), clipped to the segment.
    along = np.clip(
        ((px[:, None] - x[:-1]) * dx + (py[:, None] - y[:-1]) * dy)
        / np.where(flat > 0, flat, 1.0),
        0.0,
        1.0,
    )
    miss = np.hypot(
        x[:-1] + along * dx - px[:, None], y[:-1] + along * dy - py[:, None]
    )
    nearest = miss.argmin(axis=1)
    rows = np.arange(len(px))
    return start[nearest] + along[rows, nearest] * segment[nearest], float(start[-1])


def surface_class_from_tag(tags: Mapping[str, str]) -> str | None:
    """Surface class (paved, gravel, natural, rock) of a way's ``surface`` tag.

    The first ``;`` token is used, lowercased; ``concrete:*`` counts as
    concrete. A missing or unlisted surface gives None.
    """
    token = tags.get("surface", "").split(";")[0].strip().lower()
    if token.startswith("concrete:"):
        token = "concrete"
    return _SURFACE_TAG_CLASS.get(token)


def surface_class(tags: Mapping[str, str]) -> str | None:
    """Surface class of a way: its surface tag, else its highway kind.

    Roads are paved; a track follows its tracktype (grade1 paved, grade2-3
    gravel, grade4-5 natural, none or other gravel); path, footway, steps and
    bridleway are natural; via_ferrata is rock. Anything else is None (left
    out of the SurfaceMix denominator).
    """
    from_tag = surface_class_from_tag(tags)
    if from_tag is not None:
        return from_tag
    highway = tags.get("highway", "")
    if highway.removesuffix("_link") in _ROAD_HIGHWAYS:
        return "paved"
    if highway == "track":
        return _TRACKTYPE_CLASS.get(tags.get("tracktype", ""), "gravel")
    if highway in ("path", "footway", "steps", "bridleway"):
        return "natural"
    if highway == "via_ferrata":
        return "rock"
    return None


def _path_length_m(latitude: Sequence[float], longitude: Sequence[float]) -> float:
    lat = np.radians(np.asarray(latitude, dtype=float))
    lon = np.radians(np.asarray(longitude, dtype=float))
    return float(
        np.sum(
            2
            * EARTH_RADIUS_M
            * np.arcsin(
                np.sqrt(
                    np.sin(np.diff(lat) / 2) ** 2
                    + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(np.diff(lon) / 2) ** 2
                )
            )
        )
    )


def trail_facts(
    dayhikes: pd.DataFrame, vertices: pd.DataFrame, ways: pd.DataFrame
) -> pd.DataFrame:
    """Trail facts per DayHike id, in stored order.

    Both read the main walk stored in ``ways``: ``walk_order`` (the way's
    position along the walk, which starts at the Trailhead; missing when the
    way is a Leftover) and ``walked_m`` (the metres of the way on the walk).
    SurfaceMix: four length fractions (``SURFACE_SHARE_COLUMNS``) over the
    walked ways that ``surface_class`` can classify, each weighted by its
    walked metres; unclassified ways are left out of the denominator.
    ``tarmac_approach_m``: the walked metres of consecutive paved ways from
    the start of the walk until the first way that is not paved (an
    unclassified way ends it); an all-paved DayHike gives its walked length.
    For a loop (the walk ends within ``GAP_JOIN_M`` of where it starts) the
    shorter of the two directions counts, so the walking direction never
    matters. Both are missing when no walked way is classified.
    """
    ids = dayhikes["id"]
    chain = vertices.sort_values(["dayhike_id", "seq"]).groupby("dayhike_id")
    ends = pd.concat(
        [chain[["latitude", "longitude"]].first(), chain[["latitude", "longitude"]].last()],
        axis=1,
        keys=["first", "last"],
    )
    facts = {}
    for dayhike_id, member_ways in (
        ways[ways["walk_order"].notna()].sort_values(["dayhike_id", "walk_order"]).groupby("dayhike_id")
    ):
        order = [
            (float(length), surface_class(json.loads(tags)))
            for length, tags in zip(member_ways["walked_m"], member_ways["tags"])
        ]
        classified = [(length, kind) for length, kind in order if kind is not None]
        total = sum(length for length, _ in classified)
        if total <= 0:
            continue
        approaches = [_paved_run(order)]
        if dayhike_id in ends.index:
            first, last = ends.loc[dayhike_id, "first"], ends.loc[dayhike_id, "last"]
            if (
                _path_length_m(
                    [first["latitude"], last["latitude"]],
                    [first["longitude"], last["longitude"]],
                )
                <= GAP_JOIN_M
            ):
                approaches.append(_paved_run(order[::-1]))
        facts[dayhike_id] = [
            sum(length for length, k in classified if k == kind) / total
            for kind in ("paved", "gravel", "natural", "rock")
        ] + [min(approaches)]
    return pd.DataFrame.from_dict(
        facts, orient="index", columns=[*SURFACE_SHARE_COLUMNS, "tarmac_approach_m"]
    ).reindex(ids.rename("id"))


def _paved_run(walked: Sequence[tuple[float, str | None]]) -> float:
    """Length of the paved ways at the start of ``walked``, up to the first
    way that is not paved (an unclassified way ends it).
    """
    run = 0.0
    for length, kind in walked:
        if kind != "paved":
            break
        run += length
    return run


def round_trip_facts(dayhikes: pd.DataFrame, vertices: pd.DataFrame) -> pd.DataFrame:
    """RoundTrip fact per DayHike id, in stored order.

    ``round_trip`` is True when the last vertex of the main walk lies within
    ``GAP_JOIN_M`` of the first (the Trailhead), so its length is the whole
    walk back to the car. A lollipop (stem plus loop) ends where the stem
    meets the loop and is not a RoundTrip. The fact is never missing: a
    DayHike without vertices is False.
    """
    chain = vertices.sort_values(["dayhike_id", "seq"]).groupby("dayhike_id")
    first = chain[["latitude", "longitude"]].first()
    last = chain[["latitude", "longitude"]].last()
    round_trip = pd.Series(
        [
            _path_length_m(
                [first.at[i, "latitude"], last.at[i, "latitude"]],
                [first.at[i, "longitude"], last.at[i, "longitude"]],
            )
            <= GAP_JOIN_M
            for i in first.index
        ],
        index=first.index,
        dtype=bool,
    )
    ids = dayhikes["id"]
    return pd.DataFrame(
        {"round_trip": round_trip.reindex(ids, fill_value=False).to_numpy()},
        index=ids.rename("id"),
    )


def weather_facts(
    dayhikes: pd.DataFrame, vertices: pd.DataFrame, dem_dir: Path | str
) -> pd.DataFrame:
    """Weather facts per DayHike id, in stored order.

    The ClimateNormal regime (``temperature_c``, ``precipitation_mm``,
    ``solar_radiation``: June-August mean temperature, mean monthly
    precipitation, June-August solar radiation) from the Catalog
    ``feature_vectors``; the twelve months (``CLIMATE_MONTH_COLUMNS``) from
    ``climate_months``; and SolarAspect ``southness`` from the FABDEM tiles
    in ``dem_dir`` (``solar_aspect_southness``). A DayHike outside the
    sampled rasters has the values missing, not 0.
    """
    hikes = dayhikes.set_index("id")
    facts = hikes[[*CLIMATE_NAMES, *CLIMATE_MONTH_COLUMNS]].astype(float)
    facts["southness"] = solar_aspect_southness(vertices, dem_dir).reindex(facts.index)
    return facts


def solar_aspect_southness(vertices: pd.DataFrame, dem_dir: Path | str) -> pd.Series:
    """SolarAspect southness per DayHike id: 1 due south, 0 due north, 0.5
    east or west, the mean over sloped sample points.

    About ``SOLAR_ASPECT_POINTS`` vertices per DayHike are sampled: every k-th
    counted from the nearer end of the walk, so the set is the same in either
    walking direction. At each, FABDEM in ``dem_dir`` is read at the vertex and
    ``SOLAR_ASPECT_OFFSET_ARCSEC`` east and north of it (whole 1 arc-second
    cells, so the offset is the distance between the cells read). The
    downslope direction gives southness (1 - its north component) / 2. Points
    with slope under ``SOLAR_ASPECT_MIN_SLOPE_DEG`` or without a height (no
    covering tile, nodata) are left out; a DayHike with none left is missing.
    """
    chain = vertices.sort_values(["dayhike_id", "seq"])
    position = chain.groupby("dayhike_id").cumcount().to_numpy()
    count = chain.groupby("dayhike_id")["seq"].transform("size").to_numpy()
    step = np.maximum(1, np.ceil(count / SOLAR_ASPECT_POINTS))
    chosen = chain[np.minimum(position, count - 1 - position) % step == 0]
    lat = chosen["latitude"].to_numpy(float)
    lon = chosen["longitude"].to_numpy(float)
    offset = SOLAR_ASPECT_OFFSET_ARCSEC / 3600
    probes = [
        point
        for y, x in zip(lat, lon)
        for point in ((y, x), (y, x + offset), (y + offset, x))
    ]
    sampled = sample_elevations(dem_dir, probes)
    # One row per chosen vertex: height at the vertex, east of it, north of it.
    heights = np.array([math.nan if h is None else h for h in sampled]).reshape(-1, 3)
    north_m = math.radians(offset) * EARTH_RADIUS_M
    east_m = north_m * np.cos(np.radians(lat))
    rise_east = (heights[:, 1] - heights[:, 0]) / east_m
    rise_north = (heights[:, 2] - heights[:, 0]) / north_m
    grade = np.hypot(rise_east, rise_north)
    sloped = np.degrees(np.arctan(grade)) >= SOLAR_ASPECT_MIN_SLOPE_DEG
    # Downslope points against the gradient; its north component is
    # -rise_north / grade, so a slope rising northward faces south.
    southness = np.where(
        sloped, (1 + rise_north / np.where(sloped, grade, 1.0)) / 2, math.nan
    )
    ids = pd.Index(vertices["dayhike_id"].unique(), name="dayhike_id")
    return (
        pd.Series(southness, index=chosen["dayhike_id"].to_numpy())
        .groupby(level=0)
        .mean()
        .reindex(ids)
    )


def _points(table: pd.DataFrame) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        table,
        geometry=gpd.points_from_xy(table["longitude"], table["latitude"]),
        crs="EPSG:4326",
    )


def _line(coords: list[tuple[float, float]]) -> LineString | None:
    return LineString(coords) if len(coords) >= 2 else None
