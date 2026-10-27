"""The serving export: the small read-only dataset the web demo answers from.

``export_serving`` runs once where the Catalog, its GeometryStore and the
similarity directory live. It writes a directory a server memory-maps:

- ``similarity/``: a copy of the Catalog's similarity directory (ADR-0013).
- ``trailheads.npy``: Trailhead latitude and longitude, one row per DayHike,
  in the similarity ``ids`` order.
- ``hikes.sqlite``: per DayHike its name, the DayHikeGeometry simplified to
  about ``LINE_TOLERANCE_M`` (Google polyline), its ElevationProfile in true
  metres at ``ELEVATION_POINTS`` equal steps, the metres it climbs and
  descends in the walking direction, and the amenity points the
  Measures count (along the line, Peaks around it, at the Trailhead) as
  zlib-compressed JSON (``POINT_COLUMNS`` per point); and ``hike_boxes``, an
  R*Tree of each simplified line's bounding box, for the hikes in a map view.
- ``trailgraph.sqlite``: the Catalog's TrailGraph (ADR-0018), each Segment
  with its line simplified like the hikes' and the metres it climbs each way,
  when the Catalog has one: the page composes hikes from it, and its OSM node
  and way ids resolve a shared link.
- ``snaps.npz``: highway snaps of every Trailhead (``HighwaySnaps``), when a
  snap file or a Valhalla URL is given.
- ``valhalla/``: the highways-only tile extract and a ``valhalla.json`` that
  serves it from ``SERVING_ROOT`` inside the container, when given.
- ``meta.json``: written last, so a directory without it is incomplete.

``ServingData`` opens such a directory without ``Catalog.open``: name search,
the hikes in a map view, hike detail and Ranking all read the memory-mapped arrays and the SQLite file.
The Segments in a map view and a ComposedHike's detail read ``trailgraph.sqlite``; a Ranking
from a ComposedHike scores its facts against every DayHike like any Seed's. Detail, pair and
Ranking measure every hike with its way back to the start, or every hike one way (ADR-0019).
With a TrailGraph a Ranking also searches it for SuggestedLoops near its best DayHikes and
lists them among them (``dayhike.loops``, ADR-0020).
"""

from __future__ import annotations

import bisect
import itertools
import json
import math
import os
import re
import shutil
import sqlite3
import time
import unicodedata
import zlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import shapely
from sklearn.neighbors import BallTree

from dayhike.catalog import (
    EARTH_RADIUS_M,
    MAX_CAR_SPEED_MS,
    MatrixClient,
    Origin,
    Trailhead,
    geometry_store_path_for,
    similarity_dir_for,
    straight_line_distances_m,
    trailgraph_path_for,
)
from dayhike.loops import LOOP_SEEDS, LoopTarget, find_loops
from dayhike.similarity import (
    ALONG_KINDS,
    CORRIDOR_M,
    FACT_COLUMNS,
    MEASURES,
    PEAKS_AROUND_M,
    PROFILE_POINTS,
    SEARCH_STEP_M,
    TRAILHEAD_M,
    TWIN_M,
    TWIN_SHARE,
    TWIN_STEP_M,
    AmenityPoints,
    OutsideSeed,
    SimilarityStore,
    StaleSimilarity,
    _calibration_key,
    _check_climate,
    _densify,
    _even_points,
    _haversine_m,
    _measure_weights,
    _path_length_m,
    _resample,
    _step_m,
    are_twins,
    calibrate,
    climb_and_descent,
    measure_scores,
    nearest_on_line,
    rank_rows,
    score_rows,
)
from dayhike.trailgraph import (
    TRAILHEAD_KINDS,
    WAY_BACK_KINDS,
    ComposedWalk,
    Route,
    TrailGraph,
    WayBack,
    decode_coords,
    round_trip,
    way_back,
)

Progress = Callable[[str], None]
Reach = Callable[[list[tuple[float, float]]], list[float | None]]  # drive hours to places, None out of reach

SERVING_FORMAT = 4
SERVING_ROOT = "/data"  # where the container finds the export (web/Dockerfile)
LINE_TOLERANCE_M = 10.0
ELEVATION_POINTS = 128
LOOP_MEASURED = 40  # the SuggestedLoops guessed best that a Ranking measures exactly
LOOP_BUDGET_S = 5.0  # the most a Ranking spends on SuggestedLoops
LOOP_SEED_SHARE = 0.5  # a loop sharing more of its walk with the Seed, or of the Seed's, is no suggestion
GROUP_M = 10_000.0  # hikes starting closer than this to a better one are its versions, one row
VERSIONS = 3  # versions per row, the row's own included
VERSION_DIFFERS = 0.5  # a version walks more than this share of its length off every other one
LOOP_NAME_M = 2_000.0  # a loop is named after a named point this near its start
TRAILHEAD_NEAREST = 3  # parking and restaurant/cafe points kept per Trailhead
VALHALLA_CACHE_BYTES = 256 * 1024 * 1024  # mjolnir.max_cache_size; the build default is 1 GB
SEARCH_SCAN_CAP = 5_000  # occurrences of the scanned word a search looks at, so short queries stay fast
_HIKE_CHUNK = 2_000
_RASTER_FACTS = ("tree_share", "temperature_c", "southness", "waterside_share")  # read from rasters
_SIMILARITY_FILES = (
    "ids.npy", "facts.npy", "profile.npy", "twins.npy", "quantiles.npy",
    "facts_back.npy", "profile_back.npy", "quantiles_back.npy", "way_back.npy", "meta.json",
)  # fmt: skip

# Groups are sections of the weights panel only; scoring knows no groups (ADR-0013).
MEASURE_INFO: dict[str, tuple[str, str, str]] = {
    "length": ("effort", "Length", "Walk length; the shorter over the longer (floor 1 km)."),
    "elevation_gain": ("effort", "Elevation gain", "Metres climbed, the larger of climb and descent (floor 100 m)."),
    "difficulty": ("effort", "Difficulty", "Strictest SAC grade, 1-6; alike within 3 grades."),
    "highest": ("altitude", "Highest point", "Highest elevation; alike within 400 m."),
    "lowest": ("altitude", "Lowest point", "Lowest elevation; alike within 400 m."),
    "profile": ("profile", "Profile shape", "Elevation profile scaled to 0-1 in length and height, walked either way."),
    "landcover": ("landscape", "Land cover", "Shared share of tree, shrub, grass, bare, snow and other cover."),
    "peak_summited": ("landscape", "Peak summited", "A Peak within 50 m of the line, or not."),
    "viewpoint_along": ("landscape", "Viewpoint along", "A Viewpoint within 100 m of the line, or not."),
    "peaks_around": ("landscape", "Peaks around", "Peaks within 2 km of the line (floor 1)."),
    "temperature": ("weather", "Temperature", "Mean temperature (June-August, or the chosen month); alike within 10 °C."),
    "precipitation": ("weather", "Precipitation", "Mean monthly precipitation (or the chosen month's) (floor 10 mm)."),
    "solar_radiation": ("weather", "Solar radiation", "Solar radiation (June-August, or the chosen month); alike within 5,000 kJ m⁻² day⁻¹."),
    "southness": ("weather", "Sun-facing slopes", "Which way the slopes face: 1 south, 0 north (from FABDEM)."),
    "water_count": ("water", "Water sources", "WaterSources within 100 m of the line, counted up to 3."),
    "dry_stretch": ("water", "Dry stretch", "Longest walk between WaterSources (floor 1 km)."),
    "waterside": ("water", "Waterside", "Share of the walk within 200 m of sea, lake or wide river (WorldCover); alike within 0.5."),
    "hut_along": ("facilities", "Hut along", "A Hut within 100 m of the line, or not."),
    "restaurant_cafe_along": ("facilities", "Restaurant or cafe along", "A restaurant or cafe within 100 m of the line, or not."),
    "shelter_along": ("facilities", "Shelter along", "A Shelter within 100 m of the line, or not."),
    "restaurant_cafe_trailhead": ("facilities", "Restaurant at trailhead", "A restaurant or cafe within 500 m of the Trailhead, or not."),
    "parking_trailhead": ("facilities", "Parking at trailhead", "Parking within 500 m of the Trailhead, or not."),
    "surface": ("trail", "Surface", "Shared share of paved, gravel, natural and rock surface."),
    "tarmac_approach": ("trail", "Tarmac approach", "Paved metres before the trail leaves the road; alike within 2 km."),
    "round_trip": ("trail", "Round trip", "Ends within 50 m of the Trailhead, or not (always, with the way back)."),
    "climb_grade": ("steepness", "Climb grade", "Gain over the length of the stretches that climb (floor 3 %)."),
    "steepest_km": ("steepness", "Steepest km", "Largest height change over 1 km (floor 50 m)."),
}
MEASURE_GROUPS = ("effort", "altitude", "profile", "landscape", "weather", "water", "facilities", "trail", "steepness")
POINT_COLUMNS = ("kind", "where", "lat", "lon", "along_m", "off_m", "name", "ele")


class ServingExportError(ValueError):
    """The inputs of a serving export do not fit together."""


def export_serving(
    catalog_path: Path | str,
    out_dir: Path | str,
    *,
    valhalla_dir: Path | str | None = None,
    snaps_path: Path | str | None = None,
    valhalla_url: str | None = None,
    progress: Progress | None = None,
) -> Path:
    """Write the serving export of a Catalog into ``out_dir`` (see the module doc).

    Reads the Catalog, its GeometryStore and its similarity directory, all
    read-only. ``valhalla_dir`` holds the highways-only ``valhalla_tiles.tar``
    and the ``valhalla.json`` that built it; the tar is hard-linked when it
    can be (same disk), else copied. ``snaps_path`` seeds ``snaps.npz`` from an
    existing snap cache; with ``valhalla_url`` every Trailhead not in it is
    snapped now, so a server never has to.
    """
    started = time.monotonic()
    catalog_path, out = Path(catalog_path), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "meta.json").unlink(missing_ok=True)
    catalog = sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True)
    try:
        hikes = catalog.execute(
            "SELECT d.id, d.name, d.name_origin, d.difficulty, t.latitude, t.longitude "
            "FROM dayhikes d JOIN trailheads t ON t.dayhike_id = d.id ORDER BY d.id"
        ).fetchall()
        attribution = dict(catalog.execute("SELECT key, value FROM catalog_meta"))["attribution"]
    finally:
        catalog.close()
    ids = np.array([row[0] for row in hikes], dtype=np.int64)
    try:
        store = SimilarityStore.open_for(catalog_path, ids)
    except StaleSimilarity as exc:
        raise ServingExportError(str(exc)) from exc
    _check_trailgraph_sampled(trailgraph_path_for(catalog_path), store)
    similarity = out / "similarity"
    similarity.mkdir(exist_ok=True)
    for name in _SIMILARITY_FILES:
        shutil.copyfile(similarity_dir_for(catalog_path) / name, similarity / name)
    trailheads = np.array([(row[4], row[5]) for row in hikes], dtype=np.float64).reshape(-1, 2)
    np.save(out / "trailheads.npy", trailheads)
    sources = _write_hikes(catalog_path, out / "hikes.sqlite", hikes, progress)
    compose = _write_trailgraph(trailgraph_path_for(catalog_path), out / "trailgraph.sqlite", progress)
    # A rerun without tiles or snaps drops the old ones: they may be for other tiles.
    if valhalla_dir is not None:
        _write_valhalla(Path(valhalla_dir), out / "valhalla")
    else:
        shutil.rmtree(out / "valhalla", ignore_errors=True)
    if snaps_path is not None or valhalla_url is not None:
        _write_snaps(out / "snaps.npz", trailheads, snaps_path, valhalla_url, progress)
    else:
        (out / "snaps.npz").unlink(missing_ok=True)
    meta = {
        "format": SERVING_FORMAT,
        "dayhikes": len(ids),
        "facts_version": json.loads((similarity / "meta.json").read_text())["facts_version"],
        "line_tolerance_m": LINE_TOLERANCE_M,
        "elevation_points": ELEVATION_POINTS,
        "attribution": {"osm": attribution, **sources},
        "drive_time": (out / "valhalla" / "valhalla.json").is_file() and (out / "snaps.npz").is_file(),
        "compose": compose,
        "export_seconds": round(time.monotonic() - started, 1),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    return out


def _write_hikes(
    catalog_path: Path,
    path: Path,
    hikes: list[tuple],
    progress: Progress | None,
) -> dict[str, str]:
    """Write ``hikes.sqlite``; return the GeometryStore's source attributions."""
    path.unlink(missing_ok=True)
    geo = sqlite3.connect(f"file:{geometry_store_path_for(catalog_path)}?mode=ro", uri=True)
    db = sqlite3.connect(path)
    try:
        sources = {
            key.removesuffix("_attribution"): value
            for key, value in geo.execute("SELECT key, value FROM geometry_meta")
            if key.endswith("_attribution")
        }
        line_kinds = (
            (_amenity_points(geo, tuple(k for k in ALONG_KINDS if k != "peak")), CORRIDOR_M),
            (_amenity_points(geo, ("peak",)), PEAKS_AROUND_M),
        )
        trailhead_kinds = {kind: _amenity_points(geo, (kind,)) for kind in ("parking", "restaurant_cafe")}
        db.executescript(
            "PRAGMA journal_mode = OFF;"
            "CREATE TABLE hikes ("
            " id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_origin TEXT NOT NULL,"
            " sac_scale TEXT, walk_m REAL, line TEXT, elevation BLOB, climb_m REAL, descent_m REAL,"
            " points BLOB NOT NULL);"
            "CREATE VIRTUAL TABLE hike_boxes USING rtree(id, south, north, west, east)"
        )
        for start in range(0, len(hikes), _HIKE_CHUNK):
            chunk = hikes[start : start + _HIKE_CHUNK]
            walks = _vertices(geo, chunk[0][0], chunk[-1][0])
            rows, boxes = [], []
            for dayhike_id, name, name_origin, sac_scale, th_lat, th_lon in chunk:
                points = _trailhead_points(trailhead_kinds, th_lat, th_lon)
                line = elevation = walk_m = climb_m = descent_m = None
                walk = walks.get(dayhike_id)
                if walk is not None and len(walk[0]) >= 2:
                    lat, lon, ele = walk
                    walk_m = float(_step_m(lat, lon).sum())
                    kept_lat, kept_lon = simplify_line(lat, lon, LINE_TOLERANCE_M)
                    line = encode_polyline(kept_lat, kept_lon)
                    boxes.append((dayhike_id, kept_lat.min(), kept_lat.max(), kept_lon.min(), kept_lon.max()))
                    profile = _resample(lat, lon, ele, points=ELEVATION_POINTS)
                    if profile is not None:
                        elevation = np.round(profile).astype("<i2").tobytes()
                        climb_m, descent_m = (round(v) for v in climb_and_descent(lat, lon, ele))
                    along = [p for kinds, radius in line_kinds for p in _line_points(kinds, lat, lon, radius)]
                    points = sorted(along, key=lambda point: point[4]) + points
                rows.append(
                    (dayhike_id, name, name_origin, sac_scale, walk_m, line, elevation, climb_m, descent_m, points)
                )
            _name_points(geo, rows)
            db.executemany(
                "INSERT INTO hikes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(*row[:9], zlib.compress(json.dumps(row[9], ensure_ascii=False).encode())) for row in rows],
            )
            db.executemany("INSERT INTO hike_boxes VALUES (?, ?, ?, ?, ?)", boxes)
            if progress is not None:
                progress(f"serving export: {min(start + _HIKE_CHUNK, len(hikes))}/{len(hikes)} DayHikes")
        db.commit()
    finally:
        geo.close()
        db.close()
    return sources


def _check_trailgraph_sampled(source: Path, store: SimilarityStore) -> None:
    """Refuse a TrailGraph ``sample-trailgraph`` never ran on when the similarity
    facts read the rasters: its ComposedHikes would lack those facts."""
    if not source.is_file() or all(np.isnan(store.column(name)).all() for name in _RASTER_FACTS):
        return
    graph = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        unsampled = graph.execute("SELECT 1 FROM segments WHERE landcover IS NULL LIMIT 1").fetchone()
    finally:
        graph.close()
    if unsampled is not None:
        raise ServingExportError(
            f"{source}: the TrailGraph has no raster samples but the similarity facts do; "
            "run `dayhike sample-trailgraph`"
        )


def _write_trailgraph(source: Path, path: Path, progress: Progress | None) -> bool:
    """Copy the TrailGraph, adding each Segment's display line and
    its climb each way; False (and no file) when the Catalog has no TrailGraph."""
    path.unlink(missing_ok=True)
    if not source.is_file():
        return False
    if progress is not None:
        progress("serving export: TrailGraph")
    graph = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        graph.execute("VACUUM INTO ?", (str(path),))
    finally:
        graph.close()
    db = sqlite3.connect(path)
    try:
        db.executescript(
            "ALTER TABLE segments ADD COLUMN line TEXT;"
            "ALTER TABLE segments ADD COLUMN climb_ab REAL;"
            "ALTER TABLE segments ADD COLUMN climb_ba REAL;"
        )
        updates = []
        for segment_id, coords, ele in db.execute("SELECT id, coords, ele FROM segments"):
            lat, lon = decode_coords(coords).T
            ele = np.frombuffer(ele, dtype="<f4").astype(float)
            climb_ab, climb_ba = climb_and_descent(lat, lon, ele)[0], climb_and_descent(lat[::-1], lon[::-1], ele[::-1])[0]
            updates.append(
                (
                    encode_polyline(*simplify_line(lat, lon, LINE_TOLERANCE_M)),
                    None if math.isnan(climb_ab) else round(climb_ab),
                    None if math.isnan(climb_ba) else round(climb_ba),
                    segment_id,
                )
            )
        db.executemany("UPDATE segments SET line = ?, climb_ab = ?, climb_ba = ? WHERE id = ?", updates)
        db.commit()
        db.execute("VACUUM")
    finally:
        db.close()
    return True


@dataclass
class _Kinds:
    """Amenity points of some kinds with their GeometryStore rowids."""

    points: AmenityPoints
    rowids: np.ndarray


def _amenity_points(geo: sqlite3.Connection, kinds: tuple[str, ...]) -> _Kinds:
    rows = []
    for kind in kinds:
        rows += [
            (rowid, lat, lon, kind)
            for rowid, lat, lon in geo.execute(
                "SELECT rowid, latitude, longitude FROM amenities WHERE kind = ?", (kind,)
            )
        ]
    return _Kinds(
        points=AmenityPoints([(lat, lon) for _, lat, lon, _ in rows], [kind for *_, kind in rows]),
        rowids=np.array([row[0] for row in rows], dtype=np.int64),
    )


def _vertices(
    geo: sqlite3.Connection, first_id: int, last_id: int
) -> dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """DayHikeGeometry (lat, lon, ele; NaN missing) of the DayHikes in an id range."""
    table = np.array(
        geo.execute(
            "SELECT dayhike_id, latitude, longitude, ifnull(ele, 'nan') FROM vertices "
            "WHERE dayhike_id BETWEEN ? AND ? ORDER BY dayhike_id, seq",
            (first_id, last_id),
        ).fetchall(),
        dtype=float,
    ).reshape(-1, 4)
    hike_ids, starts = np.unique(table[:, 0], return_index=True)
    ends = np.append(starts[1:], len(table))
    return {
        int(dayhike_id): (table[s:e, 1], table[s:e, 2], table[s:e, 3])
        for dayhike_id, s, e in zip(hike_ids, starts, ends)
    }


def _line_points(kinds: _Kinds, lat: np.ndarray, lon: np.ndarray, radius_m: float) -> list[list]:
    """The points of ``kinds`` within ``radius_m`` of the line.

    The export asks for what the Measures count: WaterSources, Huts,
    restaurants and cafes, Shelters and Viewpoints within ``CORRIDOR_M``, and
    Peaks within ``PEAKS_AROUND_M`` (the Measures summit those within 50 m).
    The name column holds a rowid until ``_name_points`` replaces it.
    """
    line = np.radians(_densify(lat, lon, SEARCH_STEP_M))
    reach = math.hypot(radius_m, SEARCH_STEP_M / 2) + 1.0
    hits = kinds.points.tree.query_radius(line, r=reach / EARTH_RADIUS_M)
    hits = np.unique(np.concatenate(hits)).astype(int)
    hits = hits[hits < len(kinds.rowids)]
    off, along = nearest_on_line(lat, lon, kinds.points.points[hits, 0], kinds.points.points[hits, 1])
    found = []
    for hit, off_m, along_m in zip(hits, off, along):
        if off_m <= radius_m:
            point_lat, point_lon = kinds.points.points[hit]
            found.append(
                [kinds.points.kinds[hit], "line", round(point_lat, 5), round(point_lon, 5),
                 round(float(along_m)), round(float(off_m)), int(kinds.rowids[hit]), None]
            )  # fmt: skip
    return found


def _trailhead_points(kinds: Mapping[str, _Kinds], lat: float, lon: float) -> list[list]:
    """The nearest few parking and restaurant/cafe points within ``TRAILHEAD_M``."""
    found = []
    for kind, of_kind in kinds.items():
        (hits,), (distance,) = of_kind.points.tree.query_radius(
            np.radians([[lat, lon]]),
            r=TRAILHEAD_M / EARTH_RADIUS_M,
            return_distance=True,
            sort_results=True,
        )
        for hit, angle in list(zip(hits, distance))[:TRAILHEAD_NEAREST]:
            if hit >= len(of_kind.rowids):
                continue
            point_lat, point_lon = of_kind.points.points[hit]
            found.append(
                [kind, "trailhead", round(point_lat, 5), round(point_lon, 5), None,
                 round(float(angle) * EARTH_RADIUS_M), int(of_kind.rowids[hit]), None]
            )  # fmt: skip
    return found


def _name_points(geo: sqlite3.Connection, rows: list[tuple]) -> None:
    """Replace each point's rowid with its OSM name and ``ele`` tag (or None)."""
    wanted = sorted({point[6] for row in rows for point in row[-1]})
    tags: dict[int, dict] = {}
    for start in range(0, len(wanted), 900):
        batch = wanted[start : start + 900]
        for rowid, text in geo.execute(
            f"SELECT rowid, tags FROM amenities WHERE rowid IN ({','.join('?' * len(batch))})",
            batch,
        ):
            tags[rowid] = json.loads(text)
    for row in rows:
        for point in row[-1]:
            found = tags.get(point[6], {})
            point[6] = found.get("name")
            point[7] = _number(found.get("ele"))


def _number(value: object) -> float | None:
    try:
        number = float(str(value).replace(",", ".").removesuffix("m").strip())
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def simplify_line(
    lat: np.ndarray, lon: np.ndarray, tolerance_m: float
) -> tuple[np.ndarray, np.ndarray]:
    """Douglas-Peucker on a flat plane around the line, keeping both ends."""
    lat0 = float(np.mean(lat))
    scale = math.cos(math.radians(lat0))
    xy = np.column_stack([np.radians(lon) * scale, np.radians(lat)]) * EARTH_RADIUS_M
    kept = shapely.get_coordinates(shapely.simplify(shapely.linestrings(xy), tolerance_m))
    return np.degrees(kept[:, 1] / EARTH_RADIUS_M), np.degrees(kept[:, 0] / EARTH_RADIUS_M / scale)


def encode_polyline(lat: np.ndarray, lon: np.ndarray) -> str:
    """Google encoded polyline, precision 5 (what Leaflet plugins and OSRM use)."""
    values = np.round(np.column_stack([lat, lon]) * 1e5).astype(np.int64)
    deltas = np.diff(values, axis=0, prepend=np.zeros((1, 2), dtype=np.int64)).ravel()
    out = []
    for delta in deltas.tolist():
        value = ~(delta << 1) if delta < 0 else delta << 1
        while value >= 0x20:
            out.append(chr((0x20 | (value & 0x1F)) + 63))
            value >>= 5
        out.append(chr(value + 63))
    return "".join(out)


def decode_polyline(text: str) -> list[tuple[float, float]]:
    """Inverse of ``encode_polyline``: (lat, lon) pairs."""
    numbers, value, shift = [], 0, 0
    for char in text:
        chunk = ord(char) - 63
        value |= (chunk & 0x1F) << shift
        shift += 5
        if chunk < 0x20:
            numbers.append(~(value >> 1) if value & 1 else value >> 1)
            value, shift = 0, 0
    coords = np.cumsum(np.array(numbers, dtype=np.int64).reshape(-1, 2), axis=0) / 1e5
    return [(float(a), float(b)) for a, b in coords]


def _write_valhalla(source: Path, target: Path) -> None:
    """Link the tile extract and write the config that serves it in the container."""
    target.mkdir(exist_ok=True)
    tar = target / "valhalla_tiles.tar"
    tar.unlink(missing_ok=True)
    try:
        os.link(source / "valhalla_tiles.tar", tar)
    except OSError:
        shutil.copyfile(source / "valhalla_tiles.tar", tar)
    config = json.loads((source / "valhalla.json").read_text())
    overrides = source / "mjolnir.drive-time.json"
    if overrides.is_file():
        config = _merged(config, json.loads(overrides.read_text()))
    config = _merged(
        config,
        {
            "mjolnir": {
                "tile_extract": f"{SERVING_ROOT}/valhalla/valhalla_tiles.tar",
                "tile_dir": f"{SERVING_ROOT}/valhalla/valhalla_tiles",
                "max_cache_size": VALHALLA_CACHE_BYTES,
            },
            "httpd": {"service": {"listen": "tcp://127.0.0.1:8002"}},
        },
    )
    (target / "valhalla.json").write_text(json.dumps(config, indent=1))


def _merged(base: dict, extra: Mapping) -> dict:
    """``jq -s '.[0] * .[1]'``: objects merge recursively, anything else replaces."""
    merged = dict(base)
    for key, value in extra.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _merged(merged[key], value)
        else:
            merged[key] = value
    return merged


def _write_snaps(
    path: Path,
    trailheads: np.ndarray,
    seed: Path | str | None,
    valhalla_url: str | None,
    progress: Progress | None,
) -> None:
    from dayhike.highways import HighwaySnaps
    from dayhike.valhalla import DEFAULT_VALHALLA_URL

    if seed is not None and Path(seed).resolve() != path.resolve():
        shutil.copyfile(seed, path)
    snaps = HighwaySnaps(valhalla_url or DEFAULT_VALHALLA_URL, path)
    places = [(float(lat), float(lon)) for lat, lon in trailheads]
    if valhalla_url is None:
        missing = len({p for p in places if p not in snaps._known})
        if missing:
            raise ServingExportError(f"{missing} Trailheads have no snap; pass --valhalla-url")
        return
    if progress is not None:
        progress("serving export: snapping Trailheads onto the highway tiles")
    snaps.of(places)


class ServingData:
    """A serving export, opened read-only and memory-mapped."""

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        meta_path = self.directory / "meta.json"
        if not meta_path.is_file():
            raise ServingExportError(f"{self.directory}: no complete serving export; run `dayhike export-serving`")
        self.meta = json.loads(meta_path.read_text())
        if self.meta["format"] != SERVING_FORMAT:
            raise ServingExportError(
                f"{self.directory}: export format {self.meta['format']}, expected {SERVING_FORMAT}; "
                "rerun `dayhike export-serving`"
            )
        self.store = SimilarityStore.open_dir(self.directory / "similarity")
        graph = self.directory / "trailgraph.sqlite"
        self.trailgraph = TrailGraph(graph) if self.meta.get("compose") and graph.is_file() else None
        self.trailheads = np.load(self.directory / "trailheads.npy", mmap_mode="r")
        self._db_uri = f"file:{self.directory / 'hikes.sqlite'}?mode=ro&immutable=1"
        with self._db() as db:
            self._folded = [_fold(name) for (name,) in db.execute("SELECT name FROM hikes ORDER BY id")]
        # One string of every folded name between newlines: str.find walks it in C.
        self._haystack = "\n" + "\n".join(self._folded) + "\n"
        self._starts = np.cumsum([1] + [len(name) + 1 for name in self._folded[:-1]])

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_uri, uri=True)

    def row_of(self, dayhike_id: int) -> int:
        row = int(np.searchsorted(self.store.ids, dayhike_id))
        if row >= len(self.store.ids) or self.store.ids[row] != dayhike_id:
            raise KeyError(dayhike_id)
        return row

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """DayHikes whose name holds every word of the query, in any order.

        Matching ignores case and accents and folds German ue/oe/ae to u/o/a.
        The best come first: the exact name, then names starting with the
        query, then names where every word starts a word, then the rest;
        shorter names first within each. A number also finds that DayHike id.
        Only the first SEARCH_SCAN_CAP occurrences, in id order, of the
        longest word (two letters or more) are considered.
        """
        needle = _fold(query.strip())
        words = needle.split()
        rows: list[int] = []
        if needle.isdigit():
            try:
                rows.append(self.row_of(int(needle)))
            except KeyError:
                pass
        scan = max(words, key=len, default="")
        if len(scan) >= 2:
            found = [
                row
                for row in self._rows_containing(scan)
                if row not in rows and all(word in self._folded[row] for word in words)
            ]
            found.sort(key=lambda row: (_relevance(self._folded[row], needle, words), len(self._folded[row]), row))
            rows += found
        return self._summaries(rows[:limit])

    def _rows_containing(self, word: str) -> list[int]:
        """Rows whose name holds ``word``, in id order, from its first SEARCH_SCAN_CAP occurrences."""
        hits = itertools.islice(re.finditer(re.escape(word), self._haystack), SEARCH_SCAN_CAP)
        offsets = np.fromiter((hit.start() for hit in hits), dtype=np.int64)
        return np.unique(np.searchsorted(self._starts, offsets, side="right") - 1).tolist()

    def _summaries(self, rows: list[int], return_to_start: bool = False) -> list[dict]:
        """Name and main figures per row, one way or with the way back."""
        store = self.store.mode(return_to_start)
        names = self._names([int(self.store.ids[row]) for row in rows])
        return [
            {
                "id": int(self.store.ids[row]),
                "name": names[int(self.store.ids[row])][0],
                "length_m": _value(store.column("length_m")[row]),
                "elevation_gain_m": _value(store.column("elevation_gain_m")[row]),
                **self._climb(row, names[int(self.store.ids[row])][1:], return_to_start),
                "highest_m": _value(store.column("highest_m")[row]),
                "difficulty_rank": _value(store.column("difficulty_rank")[row]),
                "trailhead": [float(v) for v in self.trailheads[row]],
            }
            for row in rows
        ]

    def _climb(self, row: int, one_way: tuple, return_to_start: bool) -> dict:
        """Climb and descent (metres, walking direction), one way or with the way back."""
        if not return_to_start:
            return {"climb_m": one_way[0], "descent_m": one_way[1]}
        _, climb_m, descent_m, _ = self.store.with_way_back.way_back[:, row]
        return {"climb_m": _round(climb_m), "descent_m": _round(descent_m)}

    def in_box(self, south: float, west: float, north: float, east: float, limit: int) -> tuple[list[dict], bool]:
        """The DayHikes whose line's bounding box meets the box, each with its id,
        name, walk length and line, and whether there are more than ``limit``;
        then none are listed.
        """
        with self._db() as db:
            found = db.execute(
                "SELECT h.id, h.name, h.walk_m, h.line FROM hike_boxes b JOIN hikes h ON h.id = b.id "
                "WHERE b.north >= ? AND b.south <= ? AND b.east >= ? AND b.west <= ? LIMIT ?",
                (south, north, west, east, limit + 1),
            ).fetchall()
        if len(found) > limit:
            return [], True
        return [{"id": i, "name": name, "walk_m": walk_m, "line": line} for i, name, walk_m, line in found], False

    def _names(self, ids: list[int]) -> dict[int, tuple[str, float | None, float | None]]:
        """Name, climb and descent (metres, walking direction) per DayHike id."""
        with self._db() as db:
            return {
                dayhike_id: (name, climb_m, descent_m)
                for dayhike_id, name, climb_m, descent_m in db.execute(
                    f"SELECT id, name, climb_m, descent_m FROM hikes WHERE id IN ({','.join('?' * len(ids))})", ids
                )
            }

    def detail(self, dayhike_id: int, return_to_start: bool = True) -> dict:
        """Everything the page plots for one DayHike, and the facts behind every
        Measure. With ``return_to_start`` the facts, climb and descent are of
        the walk with its way back, and ``way_back`` says how it gets back;
        the line and ElevationProfile stay the DayHike's own."""
        row = self.row_of(dayhike_id)
        store = self.store.mode(return_to_start)
        with self._db() as db:
            name, name_origin, sac_scale, walk_m, line, elevation, climb_m, descent_m, points = db.execute(
                "SELECT name, name_origin, sac_scale, walk_m, line, elevation, climb_m, descent_m, points "
                "FROM hikes WHERE id = ?",
                (dayhike_id,),
            ).fetchone()
        profile = np.frombuffer(elevation, dtype="<i2").tolist() if elevation else None
        return {
            "id": dayhike_id,
            "name": name,
            "name_origin": name_origin,
            "sac_scale": sac_scale,
            "trailhead": [float(v) for v in self.trailheads[row]],
            "walk_m": walk_m,
            **self._climb(row, (climb_m, descent_m), return_to_start),
            "line": line,
            "elevation": None
            if profile is None
            else {"step_m": walk_m / (len(profile) - 1), "metres": profile},
            "points": [dict(zip(POINT_COLUMNS, point)) for point in json.loads(zlib.decompress(points))],
            "facts": {column: _value(store.facts[i, row]) for i, column in enumerate(FACT_COLUMNS)},
            "profile_shape": [_value(v) for v in store.profile[row]],
            "way_back": _way_back(*store.way_back[[0, 3], row]) if return_to_start else None,
        }

    def segments_in_box(
        self, south: float, west: float, north: float, east: float, limit: int
    ) -> tuple[dict, bool]:
        """The TrailGraph Segments whose box meets the box, Links included, and
        their Junctions; whether there are more than ``limit``, and then none.

        A Segment is ``[id, kind, a, b, length_m, climb_ab, climb_ba, line,
        sac_scale, routes, step_ab, step_ba]``, routes the labels of its OSM
        relations and the steps how a ``Route`` names it walked from a and
        from b; a Junction is ``[id, lat, lon, degree, key]``.
        """
        empty = {"junctions": [], "segments": []}
        with sqlite3.connect(f"file:{self.trailgraph.path}?mode=ro", uri=True) as db:
            found = db.execute(
                "SELECT s.id, s.kind, s.a, s.b, s.length_m, s.climb_ab, s.climb_ba, s.line, s.sac_scale, "
                "s.relation_ids, s.step_ab, s.step_ba FROM segment_boxes b JOIN segments s ON s.id = b.id "
                "WHERE b.north >= ? AND b.south <= ? AND b.east >= ? AND b.west <= ? ORDER BY s.id LIMIT ?",
                (south, north, west, east, limit + 1),
            ).fetchall()
            if len(found) > limit:
                return empty, True
            relation_ids = sorted({rel for row in found for rel in json.loads(row[9])})
            labels = dict(
                _in_chunks(db, "SELECT id, coalesce(name, ref) FROM relations WHERE id IN ({})", relation_ids)
            )
            ends = sorted({end for row in found for end in row[2:4]})
            junctions = _in_chunks(
                db, "SELECT id, latitude, longitude, degree, key FROM junctions WHERE id IN ({})", ends
            )
        segments = [
            [*row[:9], [labels[rel] for rel in json.loads(row[9]) if labels.get(rel)][:3], *row[10:]]
            for row in found
        ]
        return {"junctions": [list(row) for row in junctions], "segments": segments}, False

    def compose(self, route: Route, name: str | None = None, return_to_start: bool = True) -> dict:
        """The detail of a ComposedHike, in the shape of ``detail``: ``id`` is None
        and ``route`` holds the Route as text and its start Junction, Segment
        ids and their steps in this export (None where the walk cuts a Segment,
        as a link from an older build may). ``name`` is the Hiker's; without one it is proposed
        from the trails walked most and a named place at the start. ``return_to_start``
        measures it with its way back, as ``detail``."""
        walk = self.trailgraph.walk(route)
        trip, back = self._trip(walk, return_to_start)
        facts, shape = self.trailgraph.facts(trip)
        lat, lon, ele = walk.lat, walk.lon, walk.ele
        walk_m = float(_step_m(lat, lon).sum())
        line = elevation = climb_m = descent_m = None
        if len(lat) >= 2:
            line = encode_polyline(*simplify_line(lat, lon, LINE_TOLERANCE_M))
            profile = _resample(lat, lon, ele, points=ELEVATION_POINTS)
            if profile is not None:
                elevation = {"step_m": walk_m / (ELEVATION_POINTS - 1), "metres": np.round(profile).astype(int).tolist()}
                climb_m, descent_m = (_round(v) for v in climb_and_descent(trip.lat, trip.lon, trip.ele))
        labels = self._labels(list(walk.relation_m))
        routes = list(dict.fromkeys(labels[rel] for rel in walk.relation_m if labels.get(rel)))
        return {
            "id": None,
            "route": {"text": str(route), "start": walk.start, "segments": walk.segments, "steps": walk.steps},
            "name": name or self._proposed_name(walk, labels),
            "name_origin": "hiker" if name else "proposed",
            "sac_scale": walk.sac_scale,
            "trailhead": [float(lat[0]), float(lon[0])],
            "walk_m": walk_m,
            "climb_m": climb_m,
            "descent_m": descent_m,
            "line": line,
            "elevation": elevation,
            "points": self._composed_points(lat, lon),
            "facts": {column: _value(value) for column, value in facts.items()},
            "profile_shape": None if shape is None else [_value(v) for v in shape],
            "routes": routes,
            "way_back": None if back is None else {"m": back.metres, "kind": back.kind},
        }

    def _labels(self, relation_ids: list[int]) -> dict[int, str]:
        """The name, else ref, of each OSM relation of the TrailGraph that has one."""
        with sqlite3.connect(f"file:{self.trailgraph.path}?mode=ro", uri=True) as db:
            return dict(_in_chunks(db, "SELECT id, coalesce(name, ref) FROM relations WHERE id IN ({})", relation_ids))

    def _trip(self, walk: ComposedWalk, return_to_start: bool) -> tuple[ComposedWalk, WayBack | None]:
        """The walk with its way back and the way back (ADR-0019), or the walk and None."""
        if not return_to_start:
            return walk, None
        back = way_back(walk.lat, walk.lon, self.trailgraph)
        return round_trip(walk, back), back

    def _proposed_name(self, walk: ComposedWalk, labels: dict[int, str]) -> str:
        """The two trails walked longest, and the named place nearest the start
        within ``TRAILHEAD_M``: "Summit Line and Lake Loop from Lakeside Inn"."""
        main: dict[str, float] = {}
        for relation, metres in walk.relation_m.items():
            if labels.get(relation):
                main[labels[relation]] = main.get(labels[relation], 0.0) + metres
        trails = " and ".join(sorted(main, key=lambda label: -main[label])[:2]) or "Your hike"
        lat, lon = float(walk.lat[0]), float(walk.lon[0])
        near = [
            (metres, row[4])
            for row in self.trailgraph.amenities_near(walk.lat[:1], walk.lon[:1], TRAILHEAD_M)
            if row[4] and (metres := _haversine_m(lat, lon, row[2], row[3])) <= TRAILHEAD_M
        ]
        return f"{trails} from {min(near)[1]}" if near else trails

    def _composed_points(self, lat: np.ndarray, lon: np.ndarray) -> list[dict]:
        """The amenity points a ComposedHike's detail shows, as ``_write_hikes`` picks them."""
        rows = self.trailgraph.amenities_near(lat, lon, PEAKS_AROUND_M)
        named = {row[0]: (row[4], _number(row[5])) for row in rows}

        def kinds(wanted: tuple[str, ...]) -> _Kinds:
            chosen = [row for row in rows if row[1] in wanted]
            return _Kinds(
                AmenityPoints([(row[2], row[3]) for row in chosen], [row[1] for row in chosen]),
                np.array([row[0] for row in chosen], dtype=np.int64),
            )

        points = []
        if len(lat) >= 2:
            along = tuple(kind for kind in ALONG_KINDS if kind != "peak")
            points = [p for wanted, radius in ((along, CORRIDOR_M), (("peak",), PEAKS_AROUND_M))
                      for p in _line_points(kinds(wanted), lat, lon, radius)]  # fmt: skip
            points.sort(key=lambda point: point[4])
        points += _trailhead_points({kind: kinds((kind,)) for kind in TRAILHEAD_KINDS}, float(lat[0]), float(lon[0]))
        for point in points:
            point[6], point[7] = named[point[6]]
        return [dict(zip(POINT_COLUMNS, point)) for point in points]

    def _seed(self, seed: int | Route, return_to_start: bool) -> tuple[int | OutsideSeed, ComposedWalk | None]:
        """A DayHike's store row, or a ComposedHike's facts beside the store
        (with its way back when the DayHikes are; the DayHikes it retraces as its Twins)
        and its walk."""
        if not isinstance(seed, Route):
            return self.row_of(seed), None
        walk = self.trailgraph.walk(seed)
        facts, shape = self.trailgraph.facts(self._trip(walk, return_to_start)[0])
        return OutsideSeed.of(facts, shape, self._retraced_rows(walk.lat, walk.lon)), walk

    def _retraced_rows(self, lat: np.ndarray, lon: np.ndarray) -> list[int]:
        """Rows of the DayHikes a walk retraces: its Twins, on their served lines."""
        if len(lat) < 2:
            return []
        walk = _even_points(lat, lon, TWIN_STEP_M)
        length = _path_length_m(walk[:, 0], walk[:, 1])
        with self._db() as db:
            found = db.execute(
                "SELECT h.id, h.line FROM hike_boxes b JOIN hikes h ON h.id = b.id "
                "WHERE b.north >= ? AND b.south <= ? AND b.east >= ? AND b.west <= ?",
                (lat.min(), lat.max(), lon.min(), lon.max()),
            ).fetchall()
        rows = []
        for dayhike_id, line in found:
            points = np.array(decode_polyline(line))
            other = _even_points(points[:, 0], points[:, 1], TWIN_STEP_M)
            other_length = _path_length_m(other[:, 0], other[:, 1])
            if min(length, other_length) >= TWIN_SHARE * max(length, other_length) and are_twins(walk, other):
                rows.append(self.row_of(dayhike_id))
        return rows

    def rank(
        self,
        seed: int | Route,
        weights: Mapping[str, float],
        *,
        month: int | None = None,
        allowed_rows: np.ndarray | None = None,
        limit: int = 10,
        return_to_start: bool = True,
        reach: Reach | None = None,
    ) -> list[dict]:
        """The ``limit`` hikes most similar to one Seed, with every Measure's score.

        The Seed is a DayHike id or a ComposedHike's Route (the DayHikes it
        retraces are left out, as a Seed's Twins). Each Measure's ``score`` is
        its calibrated score (what the weights average), ``raw`` the
        uncalibrated one; both are None where either hike lacks the fact.
        ``return_to_start`` measures the Seed and every hike alike with
        its way back (and the figures listed are of that walk), else one way.
        Each hike has its served ``line``.

        With a TrailGraph the hikes are DayHikes and SuggestedLoops (ADR-0020),
        ranked together: a loop has ``id`` None, its ``route``, a name from
        where it starts, and ``drive_hours`` to its start from ``reach`` (None
        without it), which leaves out a loop starting out of reach.

        One row per area: best first, a hike starting within ``GROUP_M`` of a
        better row's start is a version of that row, listed in its
        ``versions`` (at most ``VERSIONS`` - 1, each walking more than
        ``VERSION_DIFFERS`` of its length off every version kept), else left out.
        """
        store = self.store.mode(return_to_start)
        seed_row, seed_walk = self._seed(seed, return_to_start)
        allowed = None if allowed_rows is None else self.store.ids[allowed_rows]
        rows, scores = rank_rows(
            store, [seed_row], weights, month=month, allowed_ids=allowed, limit=max(limit, LOOP_SEEDS)
        )
        hikes = self._summaries(rows.tolist(), return_to_start)
        lines = self._served_lines([hike["id"] for hike in hikes])
        candidates = []
        for hike, score, measures in zip(hikes, scores, self._measures(store, seed_row, rows, month)):
            hike["line"] = lines[hike["id"]]
            line = np.array(decode_polyline(hike["line"]))
            hike["score"] = float(score)
            hike["measures"] = measures
            candidates.append((hike, _even_points(line[:, 0], line[:, 1], TWIN_STEP_M)))
        if self.trailgraph is not None and LOOP_SEEDS:
            seed_line = (
                np.array(decode_polyline(self._served_lines([seed])[seed]))
                if seed_walk is None
                else np.column_stack([seed_walk.lat, seed_walk.lon])
            )
            candidates += self._loops(
                store, seed_row, seed_line, weights, month, rows[:LOOP_SEEDS], return_to_start, reach
            )
        groups: list[list[tuple[dict, np.ndarray]]] = []  # the best first in each
        for hike, points in sorted(candidates, key=lambda candidate: -candidate[0]["score"]):
            start = hike["trailhead"]
            group = next((g for g in groups if _haversine_m(*start, *g[0][0]["trailhead"]) < GROUP_M), None)
            if group is None:
                if len(groups) < limit:
                    groups.append([(hike, points)])
            elif len(group) < VERSIONS and all(1.0 - _share_on(points, kept) > VERSION_DIFFERS for _, kept in group):
                group.append((hike, points))
        return [{**group[0][0], "versions": [hike for hike, _ in group[1:]]} for group in groups]

    def _loops(
        self,
        store: SimilarityStore,
        seed: int | OutsideSeed,
        seed_line: np.ndarray,
        weights: Mapping[str, float],
        month: int | None,
        rows: np.ndarray,
        return_to_start: bool,
        reach: Reach | None,
    ) -> list[tuple[dict, np.ndarray]]:
        """SuggestedLoops from Junctions near the Trailheads of ``rows``, as
        ``rank`` lists them, each with its walk every ``TWIN_STEP_M``: the
        ``LOOP_MEASURED`` best guesses of ``find_loops`` measured (with their
        way back when the Seed is) and scored like a DayHike. A loop is left out
        that shares more than ``LOOP_SEED_SHARE`` of its walk with the Seed's
        line (``seed_line``, (lat, lon) rows) or of the Seed's with it, or that
        is a Twin of a DayHike. All of it stops at ``LOOP_BUDGET_S``."""
        deadline = time.monotonic() + LOOP_BUDGET_S
        if not isinstance(seed, OutsideSeed):
            seed = OutsideSeed.of(dict(zip(FACT_COLUMNS, store.facts[:, seed].tolist())), store.profile[seed])
        fact = dict(zip(FACT_COLUMNS, seed.facts.tolist()))
        target = LoopTarget(
            fact["length_m"], fact["highest_m"], fact["lowest_m"], fact["elevation_gain_m"], seed.profile
        )
        trailheads = [(float(lat), float(lon)) for lat, lon in self.trailheads[rows]]
        found = find_loops(self.trailgraph, target, trailheads, limit=LOOP_MEASURED, deadline=deadline, reach=reach)
        measured = []
        for loop in found:
            if time.monotonic() >= deadline:
                break
            walk = self.trailgraph.walk_segments(loop.start, loop.segments)
            trip = self._trip(walk, return_to_start)[0]
            facts, shape = self.trailgraph.facts(trip)
            measured.append((loop, walk, trip, facts, shape))
        if not measured:
            return []
        loops = SimilarityStore(
            ids=np.zeros(len(measured), dtype=np.int64),
            facts=np.array([[facts[c] for c in FACT_COLUMNS] for *_, facts, _ in measured], dtype=np.float32).T,
            profile=np.array(
                [np.full(PROFILE_POINTS, np.nan) if shape is None else shape for *_, shape in measured],
                dtype=np.float32,
            ),
            twins=np.arange(len(measured)),
            quantiles=store.quantiles,
        )
        similarity, _ = score_rows(loops, [seed], np.arange(len(measured)), _measure_weights(weights), month=month)
        seed_points = _even_points(seed_line[:, 0], seed_line[:, 1], TWIN_STEP_M)
        kept: dict[int, np.ndarray] = {}
        for i in np.flatnonzero(~np.isnan(similarity)).tolist():
            walk = measured[i][1]
            points = _even_points(walk.lat, walk.lon, TWIN_STEP_M)
            if max(_share_on(points, seed_points), _share_on(seed_points, points)) > LOOP_SEED_SHARE:
                continue  # it mostly walks the Seed again
            if self._retraced_rows(walk.lat, walk.lon):
                continue  # a Catalog DayHike stands for it
            kept[i] = points
        chosen = list(kept)
        listed = []
        for i, measures in zip(chosen, self._measures(loops, seed, np.array(chosen, dtype=np.int64), month)):
            loop, walk, trip, facts, _ = measured[i]
            climb_m, descent_m = climb_and_descent(trip.lat, trip.lon, trip.ele)
            listed.append((
                {
                    "id": None,
                    "route": {"text": str(walk.route)},
                    "name": self._loop_name(walk),
                    "length_m": _value(facts["length_m"]),
                    "elevation_gain_m": _value(facts["elevation_gain_m"]),
                    "climb_m": _round(climb_m),
                    "descent_m": _round(descent_m),
                    "highest_m": _value(facts["highest_m"]),
                    "difficulty_rank": _value(facts["difficulty_rank"]),
                    "trailhead": [float(walk.lat[0]), float(walk.lon[0])],
                    "line": encode_polyline(*simplify_line(walk.lat, walk.lon, LINE_TOLERANCE_M)),
                    "drive_hours": loop.drive_hours,
                    "score": float(similarity[i]),
                    "measures": measures,
                },
                kept[i],
            ))
        return listed

    def _served_lines(self, ids: list[int]) -> dict[int, str]:
        """DayHikes' served lines, encoded, what their Twins are found on, by id."""
        with self._db() as db:
            return dict(_in_chunks(db, "SELECT id, line FROM hikes WHERE id IN ({})", ids))

    def _loop_name(self, walk: ComposedWalk) -> str:
        """A SuggestedLoop's name, from where it starts: the named amenity point
        nearest its start, "from" it within ``TRAILHEAD_M``, "near" it within
        ``LOOP_NAME_M``, else the start's coordinates."""
        lat, lon = float(walk.lat[0]), float(walk.lon[0])
        near = sorted(
            (metres, row[4])
            for row in self.trailgraph.amenities_near(walk.lat[:1], walk.lon[:1], LOOP_NAME_M)
            if row[4] and (metres := _haversine_m(lat, lon, row[2], row[3])) <= LOOP_NAME_M
        )
        if not near:
            return f"Loop from {lat:.4f}, {lon:.4f}"
        metres, name = near[0]
        return f"Loop {'from' if metres <= TRAILHEAD_M else 'near'} {name}"

    def pair(
        self,
        a: int | Route,
        b_id: int,
        weights: Mapping[str, float],
        *,
        month: int | None = None,
        return_to_start: bool = True,
    ) -> dict:
        """How alike two hikes are: the score a Ranking would show for DayHike b
        with a (a DayHike id or a ComposedHike's Route) as the Seed (None when no
        weighted Measure is scored), and every Measure's calibrated and raw score;
        both hikes with their way back, or both one way.
        """
        store = self.store.mode(return_to_start)
        a_row, _ = self._seed(a, return_to_start)
        b = self.row_of(b_id)
        chosen = _measure_weights(weights)
        _check_climate(store, month)
        rows = np.array([b])
        similarity, _ = score_rows(store, [a_row], rows, chosen, month=month)
        return {
            "a": None if isinstance(a, Route) else a,
            "b": b_id,
            "score": _value(similarity[0]),
            "measures": self._measures(store, a_row, rows, month)[0],
        }

    def _measures(
        self, store: SimilarityStore, seed: int | OutsideSeed, rows: np.ndarray, month: int | None
    ) -> list[dict]:
        """Per row, every Measure's calibrated ``score`` and ``raw`` score against the Seed."""
        measures = {}
        for measure in MEASURES:
            raw = measure_scores(store, measure, seed, rows, month=month)
            measures[measure.name] = (raw, calibrate(store, _calibration_key(measure, month), raw))
        return [
            {name: {"score": _value(calibrated[i]), "raw": _value(raw[i])} for name, (raw, calibrated) in measures.items()}
            for i in range(len(rows))
        ]

    def reachable(
        self, origin: Origin, max_hours: float, client: MatrixClient
    ) -> dict[int, float]:
        """Rows whose Trailhead is within ``max_hours`` DriveTime, with the hours."""
        hours = self.drive_hours(origin, max_hours, client, self.trailheads)
        return {row: value for row, value in enumerate(hours) if value is not None}

    def drive_hours(
        self, origin: Origin, max_hours: float, client: MatrixClient, places: Sequence[tuple[float, float]]
    ) -> list[float | None]:
        """DriveTime hours to each place, None beyond ``max_hours``.

        Only places within the 140 km/h straight-line reach are asked.
        """
        reach_m = max_hours * 3600 * MAX_CAR_SPEED_MS
        places = np.asarray(places, dtype=float).reshape(-1, 2)
        rows = np.flatnonzero(straight_line_distances_m(origin, places) <= reach_m)
        hours: list[float | None] = [None] * len(places)
        if rows.size == 0:
            return hours
        times = client.sources_to_targets(
            origin, [Trailhead(float(lat), float(lon), None) for lat, lon in places[rows]]
        )
        for row, seconds in zip(rows.tolist(), times):
            if seconds is not None and math.isfinite(seconds) and 0 <= seconds <= max_hours * 3600:
                hours[row] = seconds / 3600
        return hours


# Letters with no Unicode decomposition to a base letter, spelt as a plain keyboard would.
_PLAIN_LETTERS = str.maketrans(
    {"ł": "l", "ø": "o", "đ": "d", "ð": "d", "æ": "ae", "œ": "oe", "ı": "i", "þ": "th"}
)


def _fold(text: str) -> str:
    """Casefolded, accents removed, ue/oe/ae as u/o/a (Mueller is Müller): what search compares."""
    plain = text.replace("\n", " ").casefold().translate(_PLAIN_LETTERS)
    decomposed = unicodedata.normalize("NFKD", plain)
    bare = "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()
    return bare.replace("ue", "u").replace("oe", "o").replace("ae", "a")


def _relevance(name: str, needle: str, words: list[str]) -> int:
    """0 the exact name, 1 starts with the query, 2 every word starts a word, else 3."""
    if name == needle:
        return 0
    if name.startswith(needle):
        return 1
    name_words = re.split(r"\W+", name)
    if all(any(part.startswith(word) for part in name_words) for word in words):
        return 2
    return 3


def _in_chunks(db: sqlite3.Connection, query: str, values: list) -> list[tuple]:
    """``query`` with its ``IN ({})`` filled by ``values``, a few hundred at a time."""
    rows = []
    for start in range(0, len(values), 900):
        batch = values[start : start + 900]
        rows += db.execute(query.format(",".join("?" * len(batch))), batch).fetchall()
    return rows


def _value(value: float) -> float | None:
    value = float(value)
    return None if math.isnan(value) else value


def _round(value: float) -> int | None:
    return None if math.isnan(value) else round(float(value))


def _share_on(a: np.ndarray, b: np.ndarray) -> float:
    """The share of walk a within ``TWIN_M`` of walk b's line, both (lat, lon) rows every ``TWIN_STEP_M``."""
    distance, _ = nearest_on_line(b[:, 0], b[:, 1], a[:, 0], a[:, 1])
    return float((distance <= TWIN_M).mean())


def _way_back(metres: float, kind: float) -> dict:
    """How a DayHike gets back to its start, as ``way_back`` stores it."""
    return {"m": float(metres), "kind": WAY_BACK_KINDS[int(kind)]}
