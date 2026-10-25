"""The serving export: the small read-only dataset the web demo answers from.

``export_serving`` runs once where the Catalog, its GeometryStore and the
similarity directory live. It writes a directory a server memory-maps:

- ``similarity/``: a copy of the Catalog's similarity directory (ADR-0013).
- ``trailheads.npy``: Trailhead latitude and longitude, one row per DayHike,
  in the similarity ``ids`` order.
- ``hikes.sqlite``: per DayHike its name, the DayHikeGeometry simplified to
  about ``LINE_TOLERANCE_M`` (Google polyline), its ElevationProfile in true
  metres at ``ELEVATION_POINTS`` equal steps, and the amenity points the
  Measures count (along the line, Peaks around it, at the Trailhead) as
  zlib-compressed JSON (``POINT_COLUMNS`` per point).
- ``snaps.npz``: highway snaps of every Trailhead (``HighwaySnaps``), when a
  snap file or a Valhalla URL is given.
- ``valhalla/``: the highways-only tile extract and a ``valhalla.json`` that
  serves it from ``SERVING_ROOT`` inside the container, when given.
- ``meta.json``: written last, so a directory without it is incomplete.

``ServingData`` opens such a directory without ``Catalog.open``: name search,
hike detail and Ranking all read the memory-mapped arrays and the SQLite file.
"""

from __future__ import annotations

import bisect
import json
import math
import os
import shutil
import sqlite3
import time
import unicodedata
import zlib
from collections.abc import Callable, Mapping
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
)
from dayhike.similarity import (
    ALONG_KINDS,
    CORRIDOR_M,
    FACT_COLUMNS,
    MEASURES,
    PEAKS_AROUND_M,
    SEARCH_STEP_M,
    TRAILHEAD_M,
    AmenityPoints,
    SimilarityStore,
    _calibration_key,
    _densify,
    _resample,
    _step_m,
    calibrate,
    measure_scores,
    nearest_on_line,
    rank_rows,
)

Progress = Callable[[str], None]

SERVING_FORMAT = 1
SERVING_ROOT = "/data"  # where the container finds the export (web/Dockerfile)
LINE_TOLERANCE_M = 10.0
ELEVATION_POINTS = 128
TRAILHEAD_NEAREST = 3  # parking and restaurant/cafe points kept per Trailhead
VALHALLA_CACHE_BYTES = 256 * 1024 * 1024  # mjolnir.max_cache_size; the build default is 1 GB
_HIKE_CHUNK = 2_000
_SIMILARITY_FILES = ("ids.npy", "facts.npy", "profile.npy", "quantiles.npy", "pairs.npy", "meta.json")

# Groups are sections of the weights panel only; scoring knows no groups (ADR-0013).
MEASURE_INFO: dict[str, tuple[str, str, str]] = {
    "length": ("effort", "Length", "Walk length; the shorter over the longer (floor 1 km)."),
    "elevation_gain": ("effort", "Elevation gain", "Metres climbed, the larger of climb and descent (floor 100 m)."),
    "difficulty": ("effort", "Difficulty", "Strictest SAC grade, 1-6; alike within 3 grades."),
    "highest": ("altitude", "Highest point", "Highest elevation; alike within 1,000 m."),
    "lowest": ("altitude", "Lowest point", "Lowest elevation; alike within 1,000 m."),
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
    "hut_along": ("facilities", "Hut along", "A Hut within 100 m of the line, or not."),
    "restaurant_cafe_along": ("facilities", "Restaurant or cafe along", "A restaurant or cafe within 100 m of the line, or not."),
    "shelter_along": ("facilities", "Shelter along", "A Shelter within 100 m of the line, or not."),
    "restaurant_cafe_trailhead": ("facilities", "Restaurant at trailhead", "A restaurant or cafe within 500 m of the Trailhead, or not."),
    "parking_trailhead": ("facilities", "Parking at trailhead", "Parking within 500 m of the Trailhead, or not."),
    "surface": ("trail", "Surface", "Shared share of paved, gravel, natural and rock surface."),
    "tarmac_approach": ("trail", "Tarmac approach", "Paved metres before the trail leaves the road; alike within 2 km."),
    "round_trip": ("trail", "Round trip", "Ends within 50 m of the Trailhead, or not."),
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
    store = SimilarityStore.open(catalog_path)
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
    if not np.array_equal(ids, store.ids):
        raise ServingExportError(
            f"{similarity_dir_for(catalog_path)} is for other DayHikes; run `dayhike build-similarity`"
        )
    similarity = out / "similarity"
    similarity.mkdir(exist_ok=True)
    for name in _SIMILARITY_FILES:
        shutil.copyfile(similarity_dir_for(catalog_path) / name, similarity / name)
    trailheads = np.array([(row[4], row[5]) for row in hikes], dtype=np.float64).reshape(-1, 2)
    np.save(out / "trailheads.npy", trailheads)
    sources = _write_hikes(catalog_path, out / "hikes.sqlite", hikes, progress)
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
            " sac_scale TEXT, walk_m REAL, line TEXT, elevation BLOB, points BLOB NOT NULL)"
        )
        for start in range(0, len(hikes), _HIKE_CHUNK):
            chunk = hikes[start : start + _HIKE_CHUNK]
            walks = _vertices(geo, chunk[0][0], chunk[-1][0])
            rows = []
            for dayhike_id, name, name_origin, sac_scale, th_lat, th_lon in chunk:
                points = _trailhead_points(trailhead_kinds, th_lat, th_lon)
                line = elevation = walk_m = None
                walk = walks.get(dayhike_id)
                if walk is not None and len(walk[0]) >= 2:
                    lat, lon, ele = walk
                    walk_m = float(_step_m(lat, lon).sum())
                    line = encode_polyline(*simplify_line(lat, lon, LINE_TOLERANCE_M))
                    profile = _resample(lat, lon, ele, points=ELEVATION_POINTS)
                    if profile is not None:
                        elevation = np.round(profile).astype("<i2").tobytes()
                    along = [p for kinds, radius in line_kinds for p in _line_points(kinds, lat, lon, radius)]
                    points = sorted(along, key=lambda point: point[4]) + points
                rows.append((dayhike_id, name, name_origin, sac_scale, walk_m, line, elevation, points))
            _name_points(geo, rows)
            db.executemany(
                "INSERT INTO hikes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(*row[:7], zlib.compress(json.dumps(row[7], ensure_ascii=False).encode())) for row in rows],
            )
            if progress is not None:
                progress(f"serving export: {min(start + _HIKE_CHUNK, len(hikes))}/{len(hikes)} DayHikes")
        db.commit()
    finally:
        geo.close()
        db.close()
    return sources


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
    wanted = sorted({point[6] for row in rows for point in row[7]})
    tags: dict[int, dict] = {}
    for start in range(0, len(wanted), 900):
        batch = wanted[start : start + 900]
        for rowid, text in geo.execute(
            f"SELECT rowid, tags FROM amenities WHERE rowid IN ({','.join('?' * len(batch))})",
            batch,
        ):
            tags[rowid] = json.loads(text)
    for row in rows:
        for point in row[7]:
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
            raise ServingExportError(f"{self.directory}: export format {self.meta['format']}, expected {SERVING_FORMAT}")
        self.store = SimilarityStore.open_dir(self.directory / "similarity")
        self.trailheads = np.load(self.directory / "trailheads.npy", mmap_mode="r")
        self._db_uri = f"file:{self.directory / 'hikes.sqlite'}?mode=ro&immutable=1"
        with self._db() as db:
            folded = [_fold(name) for (name,) in db.execute("SELECT name FROM hikes ORDER BY id")]
        # One string of every folded name between newlines: str.find walks it in C.
        self._haystack = "\n" + "\n".join(folded) + "\n"
        self._starts = np.cumsum([1] + [len(name) + 1 for name in folded[:-1]])

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_uri, uri=True)

    def row_of(self, dayhike_id: int) -> int:
        row = int(np.searchsorted(self.store.ids, dayhike_id))
        if row >= len(self.store.ids) or self.store.ids[row] != dayhike_id:
            raise KeyError(dayhike_id)
        return row

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """DayHikes whose name starts with the query, then those containing it.

        Matching ignores case and accents. A number also finds that DayHike id.
        """
        needle = _fold(query.strip())
        rows: list[int] = []
        if needle.isdigit():
            try:
                rows.append(self.row_of(int(needle)))
            except KeyError:
                pass
        if len(needle) >= 2:
            for pattern in ("\n" + needle, needle):
                at = self._haystack.find(pattern)
                while at != -1 and len(rows) < limit:
                    row = int(np.searchsorted(self._starts, at + (pattern[0] == "\n"), side="right")) - 1
                    if row not in rows:
                        rows.append(row)
                    if row + 1 >= len(self._starts):
                        break
                    at = self._haystack.find(pattern, int(self._starts[row + 1]) - 1)
        return self._summaries(rows[:limit])

    def _summaries(self, rows: list[int]) -> list[dict]:
        names = self._names([int(self.store.ids[row]) for row in rows])
        return [
            {
                "id": int(self.store.ids[row]),
                "name": names[int(self.store.ids[row])],
                "length_m": _value(self.store.column("length_m")[row]),
                "elevation_gain_m": _value(self.store.column("elevation_gain_m")[row]),
                "highest_m": _value(self.store.column("highest_m")[row]),
                "difficulty_rank": _value(self.store.column("difficulty_rank")[row]),
                "trailhead": [float(v) for v in self.trailheads[row]],
            }
            for row in rows
        ]

    def _names(self, ids: list[int]) -> dict[int, str]:
        with self._db() as db:
            return dict(
                db.execute(
                    f"SELECT id, name FROM hikes WHERE id IN ({','.join('?' * len(ids))})", ids
                )
            )

    def detail(self, dayhike_id: int) -> dict:
        """Everything the page plots for one DayHike, and the facts behind every Measure."""
        row = self.row_of(dayhike_id)
        with self._db() as db:
            name, name_origin, sac_scale, walk_m, line, elevation, points = db.execute(
                "SELECT name, name_origin, sac_scale, walk_m, line, elevation, points "
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
            "line": line,
            "elevation": None
            if profile is None
            else {"step_m": walk_m / (len(profile) - 1), "metres": profile},
            "points": [dict(zip(POINT_COLUMNS, point)) for point in json.loads(zlib.decompress(points))],
            "facts": {column: _value(self.store.facts[i, row]) for i, column in enumerate(FACT_COLUMNS)},
            "profile_shape": [_value(v) for v in self.store.profile[row]],
        }

    def rank(
        self,
        seed_id: int,
        weights: Mapping[str, float],
        *,
        month: int | None = None,
        allowed_rows: np.ndarray | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """The ``limit`` DayHikes most similar to one Seed, with every Measure's score.

        Each Measure's ``score`` is its calibrated score (what the weights
        average), ``raw`` the uncalibrated one; both are None where either
        DayHike lacks the fact.
        """
        seed = self.row_of(seed_id)
        allowed = None if allowed_rows is None else self.store.ids[allowed_rows]
        rows, scores = rank_rows(self.store, [seed], weights, month=month, allowed_ids=allowed, limit=limit)
        measures = {}
        for measure in MEASURES:
            raw = measure_scores(self.store, measure, seed, rows, month=month)
            measures[measure.name] = (raw, calibrate(self.store, _calibration_key(measure, month), raw))
        results = self._summaries(rows.tolist())
        for i, (result, score) in enumerate(zip(results, scores)):
            result["score"] = float(score)
            result["measures"] = {
                name: {"score": _value(calibrated[i]), "raw": _value(raw[i])}
                for name, (raw, calibrated) in measures.items()
            }
        return results

    def reachable(
        self, origin: Origin, max_hours: float, client: MatrixClient
    ) -> dict[int, float]:
        """Rows whose Trailhead is within ``max_hours`` DriveTime, with the hours.

        Only Trailheads within the 140 km/h straight-line reach are asked.
        """
        reach_m = max_hours * 3600 * MAX_CAR_SPEED_MS
        rows = np.flatnonzero(straight_line_distances_m(origin, self.trailheads) <= reach_m)
        if rows.size == 0:
            return {}
        times = client.sources_to_targets(
            origin, [Trailhead(float(lat), float(lon), None) for lat, lon in self.trailheads[rows]]
        )
        hours = {}
        for row, seconds in zip(rows.tolist(), times):
            if seconds is not None and math.isfinite(seconds) and 0 <= seconds <= max_hours * 3600:
                hours[row] = seconds / 3600
        return hours


def _fold(text: str) -> str:
    """Casefolded, accents removed: what search compares."""
    decomposed = unicodedata.normalize("NFKD", text.replace("\n", " "))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _value(value: float) -> float | None:
    value = float(value)
    return None if math.isnan(value) else value
