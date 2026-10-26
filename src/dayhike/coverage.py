"""A rough SVG map of Europe with the parts the Catalog covers shaded.

Coverage comes from the Catalog, not from a list: every Trailhead is placed in
a country polygon (Natural Earth admin-0 countries, public domain), and a
polygon part (a mainland, an island, an exclave) is shaded when it holds at
least ``min_hikes`` Trailheads and ``min_density`` per 1,000 km², so a few
hikes near a border do not shade all of European Russia. Rerun it after the
Catalog changes.
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import numpy as np
import shapely

VIEW = (-32.0, 32.0, 47.0, 72.0)  # west, south, east, north (degrees): Azores and Madeira too
CENTRE = (10.0, 52.0)  # Lambert azimuthal equal-area centre (lon, lat)
WIDTH = 1000.0  # SVG user units across
SIMPLIFY = 1.2  # SVG units
MIN_HIKES = 10
MIN_DENSITY = 0.2  # Trailheads per 1,000 km²
EARTH_RADIUS_KM = 6371.0
COVERED = "#2f7d4f"
UNCOVERED = "#dfe4dc"
NATURAL_EARTH_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/"
    "geojson/ne_50m_admin_0_countries.geojson"
)


def coverage_map(
    catalog_path: Path | str,
    countries_path: Path | str,
    out_path: Path | str,
    *,
    min_hikes: int = MIN_HIKES,
    min_density: float = MIN_DENSITY,
) -> list[tuple[str, int]]:
    """Write the SVG; return (country, Trailheads) for every covered country, most first."""
    catalog = sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True)
    try:
        trailheads = np.array(
            catalog.execute("SELECT longitude, latitude FROM trailheads").fetchall(), dtype=float
        ).reshape(-1, 2)
    finally:
        catalog.close()
    names, parts = [], []
    for feature in json.loads(Path(countries_path).read_text())["features"]:
        properties = feature["properties"]
        name = properties.get("NAME") or properties.get("ADMIN") or properties.get("name") or "?"
        for part in shapely.get_parts(shapely.geometry.shape(feature["geometry"])):
            names.append(name)
            parts.append(part)
    parts = np.array(parts, dtype=object)
    _, part_at = shapely.STRtree(parts).query(shapely.points(trailheads), predicate="within")
    counts = np.bincount(part_at, minlength=len(parts))
    view = shapely.box(*VIEW)
    shapes = []
    covered: dict[str, int] = {}
    # The projection is equal-area on a unit sphere.
    areas_km2 = shapely.area(_project(parts)) * EARTH_RADIUS_KM**2
    for name, part, count, area_km2 in zip(names, parts, counts, areas_km2):
        is_covered = bool(count >= min_hikes and count / area_km2 * 1000 >= min_density)
        if is_covered:
            covered[name] = covered.get(name, 0) + int(count)
        clipped = shapely.intersection(part, view)
        if not clipped.is_empty:
            shapes.append((clipped, is_covered))
    frame = _project(shapely.segmentize(view, 0.5))
    x0, y0, x1, y1 = frame.bounds
    scale = WIDTH / (x1 - x0)
    height = (y1 - y0) * scale

    def to_svg(geometry):
        projected = _project(geometry)
        flipped = shapely.transform(projected, lambda xy: np.column_stack([(xy[:, 0] - x0) * scale, (y1 - xy[:, 1]) * scale]))
        return shapely.simplify(flipped, SIMPLIFY)

    # Covered and uncovered land are drawn as two shapes, without borders between countries.
    paths = []
    for is_covered, fill in ((False, UNCOVERED), (True, COVERED)):
        land = shapely.union_all([geometry for geometry, flag in shapes if flag == is_covered])
        d = _path(to_svg(land))
        if d:
            paths.append(f'<path fill="{fill}" d="{d}"/>')
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH:.0f} {height:.0f}" '
        f'role="img" aria-label="Map of Europe; the shaded land has DayHikes in the Catalog">'
        f"<title>DayHike coverage</title>"
        f'<g fill-rule="evenodd">' + "".join(paths) + "</g></svg>\n"
    )
    Path(out_path).write_text(svg)
    return sorted(covered.items(), key=lambda item: -item[1])


def _project(geometry):
    lon0, lat0 = map(math.radians, CENTRE)

    def laea(lonlat: np.ndarray) -> np.ndarray:
        lon, lat = np.radians(lonlat[:, 0]), np.radians(lonlat[:, 1])
        k = np.sqrt(2 / (1 + math.sin(lat0) * np.sin(lat) + math.cos(lat0) * np.cos(lat) * np.cos(lon - lon0)))
        return np.column_stack(
            [
                k * np.cos(lat) * np.sin(lon - lon0),
                k * (math.cos(lat0) * np.sin(lat) - math.sin(lat0) * np.cos(lat) * np.cos(lon - lon0)),
            ]
        )

    return shapely.transform(geometry, laea)


def _path(geometry) -> str:
    rings = []
    for polygon in shapely.get_parts(geometry):
        if polygon.geom_type != "Polygon" or polygon.is_empty:
            continue
        for ring in (polygon.exterior, *polygon.interiors):
            xy = np.asarray(ring.coords)[:-1]
            if len(xy) >= 3:
                rings.append("M" + "L".join(f"{x:.0f},{y:.0f}" for x, y in xy) + "Z")
    return "".join(rings)
