from __future__ import annotations

import math
import shutil
import sqlite3
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dayhike.catalog import (
    Catalog,
    Scaler,
    feature_matrix,
    fit_scaler_from_matrix,
    geometry_store_path_for,
)

FABDEM_VERSION = "V1-2"
FABDEM_DATASET = "https://huggingface.co/datasets/pavelzaitsau/fabdem-v12/resolve/main/tiles"
Progress = Callable[[str], None]
Retrieve = Callable[[str, str], None]


@dataclass(frozen=True)
class Bounds:
    south: float
    west: float
    north: float
    east: float


DEFAULT_DEM_DIR = Path("data/dem")
DEFAULT_DEM_BOUNDS = Bounds(south=41.8, west=5.9, north=50.6, east=20.4)
# Same countries as fetch-extracts: Alps + Dinarides.
_VERTEX_CHUNK = 50_000
_HIKE_IN_CHUNK = 500


def fabdem_tile_id(lat: float, lon: float) -> str:
    """Return the FABDEM 1° tile id whose southwest corner contains this point."""
    return _format_corner(math.floor(lat), math.floor(lon))


def fabdem_archive_id(tile_id: str) -> str:
    """Return the 10° FABDEM archive folder that contains this 1° tile."""
    lat_sw, lon_sw = _parse_tile(tile_id)
    lat0 = int(math.floor(lat_sw / 10) * 10)
    lon0 = int(math.floor(lon_sw / 10) * 10)
    return (
        f"{_format_corner(lat0, lon0)}-{_format_corner(lat0 + 10, lon0 + 10)}"
        f"_FABDEM_{FABDEM_VERSION}"
    )


def fabdem_tile_url(tile_id: str) -> str:
    """HTTPS URL of one 1° FABDEM GeoTIFF (Hugging Face mirror of Bristol V1-2)."""
    filename = f"{tile_id}_FABDEM_{FABDEM_VERSION}.tif"
    return f"{FABDEM_DATASET}/{fabdem_archive_id(tile_id)}/{filename}"


def tiles_covering(bounds: Bounds) -> list[str]:
    """1° FABDEM tiles whose southwest-corner cells intersect the box."""
    lat_start = math.floor(bounds.south)
    lat_end = math.floor(bounds.north)
    lon_start = math.floor(bounds.west)
    lon_end = math.floor(bounds.east)
    tiles = [
        _format_corner(lat, lon)
        for lat in range(lat_start, lat_end + 1)
        for lon in range(lon_start, lon_end + 1)
    ]
    return tiles


def fetch_fabdem(
    dest: Path | str,
    bounds: Bounds = DEFAULT_DEM_BOUNDS,
    *,
    retrieve: Retrieve | None = None,
    progress: Progress | None = None,
    max_workers: int = 4,
) -> list[Path]:
    """Download missing FABDEM 1° GeoTIFFs covering bounds into dest."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    retrieve = retrieve or _download

    def fetch_one(tile_id: str) -> Path | None:
        path = dest / f"{tile_id}_FABDEM_{FABDEM_VERSION}.tif"
        if path.exists() and path.stat().st_size > 0:
            return path
        if progress is not None:
            progress(f"dem fetch: {tile_id}")
        try:
            retrieve(fabdem_tile_url(tile_id), str(path))
        except FileNotFoundError:
            if path.exists():
                path.unlink()
            return None
        if path.exists() and path.stat().st_size > 0:
            return path
        return None

    tiles = tiles_covering(bounds)
    if max_workers <= 1:
        return [path for tile_id in tiles if (path := fetch_one(tile_id)) is not None]
    paths: list[Path] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(fetch_one, tile_id) for tile_id in tiles]
        for future in as_completed(futures):
            try:
                path = future.result()
            except Exception as exc:
                if progress is not None:
                    progress(f"dem fetch error: {exc}")
                continue
            if path is not None:
                paths.append(path)
    paths.sort()
    return paths


def sample_elevations(
    raster_dir: Path | str,
    points: Sequence[tuple[float, float]],
) -> list[float | None]:
    """Sample FABDEM GeoTIFFs in raster_dir at (lat, lon) points. Uncovered is None."""
    import rasterio

    raster_dir = Path(raster_dir)
    out: list[float | None] = [None] * len(points)
    buckets: dict[str, list[int]] = {}
    for index, (lat, lon) in enumerate(points):
        buckets.setdefault(fabdem_tile_id(lat, lon), []).append(index)
    for tile_id, indices in buckets.items():
        path = raster_dir / f"{tile_id}_FABDEM_{FABDEM_VERSION}.tif"
        if not path.is_file():
            continue
        coords = [(points[index][1], points[index][0]) for index in indices]
        with rasterio.open(path) as dataset:
            nodata = dataset.nodata
            for index, sample in zip(indices, dataset.sample(coords), strict=True):
                value = float(sample[0])
                if not math.isfinite(value):
                    continue
                if nodata is not None and value == nodata:
                    continue
                out[index] = value
    return out


def sample_dem(
    catalog_path: Path | str,
    raster_dir: Path | str,
    *,
    progress: Progress | None = None,
) -> int:
    """Write DEM elevations onto covered GeometryStore vertices and Catalog stats."""
    catalog_path = Path(catalog_path)
    raster_dir = Path(raster_dir)
    store_path = geometry_store_path_for(catalog_path)
    if not store_path.is_file():
        raise FileNotFoundError(store_path)
    available = {
        path.name.split("_FABDEM", 1)[0]
        for path in raster_dir.glob(f"*_FABDEM_{FABDEM_VERSION}.tif")
    }
    if not available:
        return 0
    south = min(_parse_tile(tile_id)[0] for tile_id in available)
    west = min(_parse_tile(tile_id)[1] for tile_id in available)
    north = max(_parse_tile(tile_id)[0] + 1 for tile_id in available)
    east = max(_parse_tile(tile_id)[1] + 1 for tile_id in available)
    sampled = 0
    touched: set[int] = set()
    if progress is not None:
        progress("dem sample: vertices")
    write = sqlite3.connect(store_path)
    try:
        write.execute("PRAGMA journal_mode=WAL")
        read = sqlite3.connect(store_path)
        try:
            cursor = read.execute(
                "SELECT dayhike_id, seq, latitude, longitude FROM vertices "
                "WHERE latitude >= ? AND latitude < ? AND longitude >= ? AND longitude < ?",
                (south, north, west, east),
            )
            while True:
                rows = cursor.fetchmany(_VERTEX_CHUNK)
                if not rows:
                    break
                coverable = [
                    (int(dayhike_id), int(seq), float(lat), float(lon))
                    for dayhike_id, seq, lat, lon in rows
                    if fabdem_tile_id(float(lat), float(lon)) in available
                ]
                if not coverable:
                    continue
                elevations = sample_elevations(
                    raster_dir,
                    [(lat, lon) for _id, _seq, lat, lon in coverable],
                )
                updates = [
                    (elevation, dayhike_id, seq)
                    for (dayhike_id, seq, _lat, _lon), elevation in zip(
                        coverable, elevations, strict=True
                    )
                    if elevation is not None
                ]
                if not updates:
                    continue
                write.executemany(
                    "UPDATE vertices SET ele = ? WHERE dayhike_id = ? AND seq = ?",
                    updates,
                )
                sampled += len(updates)
                touched.update(dayhike_id for _elevation, dayhike_id, _seq in updates)
                if progress is not None:
                    progress(f"dem sample: {sampled} vertices")
            write.commit()
        finally:
            read.close()
    finally:
        write.close()
    if not touched:
        return 0
    if progress is not None:
        progress("dem sample: feature_vectors")
    _refresh_catalog_elevations(catalog_path, store_path, touched)
    return sampled


def _refresh_catalog_elevations(
    catalog_path: Path, store_path: Path, hike_ids: set[int]
) -> None:
    store = sqlite3.connect(store_path)
    catalog = sqlite3.connect(catalog_path)
    try:
        by_hike: dict[int, list[float]] = {dayhike_id: [] for dayhike_id in hike_ids}
        ids = list(hike_ids)
        for start in range(0, len(ids), _HIKE_IN_CHUNK):
            chunk = ids[start : start + _HIKE_IN_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            for dayhike_id, elevation in store.execute(
                "SELECT dayhike_id, ele FROM vertices "
                f"WHERE dayhike_id IN ({placeholders}) ORDER BY dayhike_id, seq",
                chunk,
            ):
                if elevation is None:
                    continue
                by_hike[int(dayhike_id)].append(float(elevation))
        updates = [
            (*_elevation_stats(values), dayhike_id)
            for dayhike_id, values in by_hike.items()
            if values
        ]
        catalog.executemany(
            "UPDATE feature_vectors SET elevation_min_m = ?, elevation_max_m = ?, "
            "elevation_gain_m = ? WHERE dayhike_id = ?",
            updates,
        )
        catalog.commit()
    finally:
        store.close()
        catalog.close()
    loaded = Catalog.open(catalog_path)
    _rewrite_scaler(catalog_path, fit_scaler_from_matrix(feature_matrix(loaded.dayhikes)))


def _rewrite_scaler(catalog_path: Path, scaler: Scaler) -> None:
    connection = sqlite3.connect(catalog_path)
    try:
        connection.execute("DELETE FROM scaler")
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


def _elevation_stats(elevations: Sequence[float]) -> tuple[float, float, float]:
    gain_m = 0.0
    for start, end in zip(elevations, elevations[1:]):
        if end > start:
            gain_m += end - start
    return min(elevations), max(elevations), gain_m


def _format_corner(lat_sw: int, lon_sw: int) -> str:
    ns = "N" if lat_sw >= 0 else "S"
    ew = "E" if lon_sw >= 0 else "W"
    return f"{ns}{abs(lat_sw):02d}{ew}{abs(lon_sw):03d}"


def _parse_tile(tile_id: str) -> tuple[int, int]:
    ns = tile_id[0]
    ew_at = 3 if tile_id[3] in "EW" else 4
    lat = int(tile_id[1:ew_at])
    ew = tile_id[ew_at]
    lon = int(tile_id[ew_at + 1 :])
    if ns == "S":
        lat = -lat
    if ew == "W":
        lon = -lon
    return lat, lon


def _download(url: str, filename: str) -> None:
    dest = Path(filename)
    tmp = dest.with_name(dest.name + ".part")
    request = Request(url, headers={"User-Agent": "dayhike"})
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            with urlopen(request, timeout=120) as source, tmp.open("wb") as out:
                shutil.copyfileobj(source, out)
            tmp.replace(dest)
            return
        except HTTPError as exc:
            tmp.unlink(missing_ok=True)
            if exc.code == 404:
                raise FileNotFoundError(url) from exc
            last_error = exc
        except (URLError, TimeoutError, OSError) as exc:
            tmp.unlink(missing_ok=True)
            last_error = exc
        time.sleep(min(2**attempt, 16))
    assert last_error is not None
    raise last_error
