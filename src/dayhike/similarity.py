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
import pandas as pd
from shapely.geometry import LineString

from dayhike.catalog import EARTH_RADIUS_M, geometry_store_path_for

AMENITY_PAD_M = 3_000  # vertex box pad for region amenities; covers Peaks around


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


def load_region(
    catalog_path: Path | str,
    *,
    lat_range: tuple[float, float],
    lon_range: tuple[float, float],
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """GeometryStore frames for DayHikes whose Trailhead lies in a lat/lon box.

    Returns ``vertices, amenities, dayhikes, ways`` as GeoDataFrames in
    EPSG:4326, shaped as in notebook 04a. ``dayhikes`` carries the Catalog
    ``feature_vectors`` (with ``difficulty_ordinal``) and the Trailhead as
    ``trailhead_latitude`` / ``trailhead_longitude``. Rows are filtered in SQL.
    Amenities are those inside the region's vertex box padded by
    ``AMENITY_PAD_M``.
    """
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
    finally:
        catalog.close()

    ids = (json.dumps(summaries["dayhike_id"].tolist()),)
    in_region = "IN (SELECT value FROM json_each(?))"
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

    Elevation gain is the larger of the climb and the descent along the vertex
    chain, so it does not depend on OSM's arbitrary way order. Vertices without
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


def _points(table: pd.DataFrame) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        table,
        geometry=gpd.points_from_xy(table["longitude"], table["latitude"]),
        crs="EPSG:4326",
    )


def _line(coords: list[tuple[float, float]]) -> LineString | None:
    return LineString(coords) if len(coords) >= 2 else None
