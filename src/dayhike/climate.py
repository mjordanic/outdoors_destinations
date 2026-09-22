from __future__ import annotations

import math
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np

from dayhike.catalog import (
    CLIMATE_FEATURE_NAMES,
    Catalog,
    Scaler,
    feature_matrix,
    fit_scaler_from_matrix,
    geometry_store_path_for,
)
from dayhike.dem import Bounds, DEFAULT_DEM_BOUNDS

Progress = Callable[[str], None]
Retrieve = Callable[[str, str], None]
DEFAULT_CLIMATE_DIR = Path("data/climate")
WORLDCLIM_BASE = "https://geodata.ucdavis.edu/climate/worldclim/2_1/base"
WORLDCLIM_VARIABLES = ("tavg", "prec", "srad")
WORLDCLIM_ATTRIBUTION = (
    "Fick, S.E. and R.J. Hijmans, 2017. WorldClim 2: new 1km spatial resolution "
    "climate surfaces for global land areas. International Journal of Climatology "
    "37 (12): 4302-4315."
)
_VERTEX_CHUNK = 50_000
_HIKE_IN_CHUNK = 500
_CLIMATE_FILE = re.compile(
    r"^wc2\.1_30s_(tavg|prec|srad)_(\d{1,2})\.tiff?$", re.IGNORECASE
)


def worldclim_zip_url(variable: str) -> str:
    """HTTPS URL of one WorldClim 2.1 30 arc-second monthly zip (tavg, prec, or srad)."""
    return f"{WORLDCLIM_BASE}/wc2.1_30s_{variable}.zip"


def fetch_worldclim(
    dest: Path | str,
    bounds: Bounds = DEFAULT_DEM_BOUNDS,
    *,
    retrieve: Retrieve | None = None,
    progress: Progress | None = None,
) -> list[Path]:
    """Download missing WorldClim 2.1 30s monthly GeoTIFFs into dest.

    Extracts the three global monthly zips and clips each GeoTIFF to bounds
    (default: the DEM Alps+Dinarides bbox).
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    retrieve = retrieve or _download
    paths: list[Path] = []
    for variable in WORLDCLIM_VARIABLES:
        present = _present_month_tifs(dest, variable)
        paths.extend(present)
        if len(present) == 12:
            continue
        if progress is not None:
            progress(f"climate fetch: {variable}")
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / f"wc2.1_30s_{variable}.zip"
            try:
                retrieve(worldclim_zip_url(variable), str(zip_path))
            except FileNotFoundError:
                continue
            if not zip_path.exists() or zip_path.stat().st_size == 0:
                continue
            paths.extend(
                _extract_worldclim_month_tifs(zip_path, dest, variable, bounds)
            )
    return sorted(set(paths))


def sample_climate(
    catalog_path: Path | str,
    raster_dir: Path | str,
    *,
    progress: Progress | None = None,
) -> int:
    """Write monthly climate onto covered vertices and Catalog hike-level means."""
    catalog_path = Path(catalog_path)
    raster_dir = Path(raster_dir)
    store_path = geometry_store_path_for(catalog_path)
    if not store_path.is_file():
        raise FileNotFoundError(store_path)
    rasters = _climate_rasters(raster_dir)
    if not rasters:
        return 0
    if progress is not None:
        progress("climate sample: vertices")
    sampled = 0
    touched: set[int] = set()
    write = sqlite3.connect(store_path)
    try:
        write.execute("PRAGMA journal_mode=WAL")
        _ensure_vertex_climate(write)
        read = sqlite3.connect(store_path)
        try:
            cursor = read.execute(
                "SELECT dayhike_id, seq, latitude, longitude FROM vertices"
            )
            while True:
                rows = cursor.fetchmany(_VERTEX_CHUNK)
                if not rows:
                    break
                coverable = [
                    (int(dayhike_id), int(seq), float(lat), float(lon))
                    for dayhike_id, seq, lat, lon in rows
                ]
                climates = _sample_points(
                    rasters, [(lat, lon) for _id, _seq, lat, lon in coverable]
                )
                inserts: list[tuple[int, int, int, float, float, float]] = []
                for (dayhike_id, seq, _lat, _lon), months in zip(
                    coverable, climates, strict=True
                ):
                    if months is None:
                        continue
                    sampled += 1
                    touched.add(dayhike_id)
                    for month, (temperature_c, precipitation_mm, solar_radiation) in (
                        enumerate(months, start=1)
                    ):
                        inserts.append(
                            (
                                dayhike_id,
                                seq,
                                month,
                                temperature_c,
                                precipitation_mm,
                                solar_radiation,
                            )
                        )
                if not inserts:
                    continue
                write.executemany(
                    "INSERT OR REPLACE INTO vertex_climate ("
                    "dayhike_id, seq, month, temperature_c, precipitation_mm, "
                    "solar_radiation) VALUES (?, ?, ?, ?, ?, ?)",
                    inserts,
                )
                if progress is not None:
                    progress(f"climate sample: {sampled} vertices")
            if sampled:
                write.execute(
                    "INSERT OR REPLACE INTO geometry_meta (key, value) VALUES (?, ?)",
                    ("worldclim_attribution", WORLDCLIM_ATTRIBUTION),
                )
            write.commit()
        finally:
            read.close()
            if write.in_transaction:
                write.rollback()
            write.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            write.execute("PRAGMA journal_mode=DELETE")
    finally:
        write.close()
    if not touched:
        return 0
    if progress is not None:
        progress("climate sample: feature_vectors")
    _refresh_catalog_climate(catalog_path, store_path, touched)
    return sampled


def _climate_rasters(raster_dir: Path) -> list[tuple[str, int, Path]]:
    found: list[tuple[str, int, Path]] = []
    for path in sorted(raster_dir.glob("*.tif")) + sorted(raster_dir.glob("*.tiff")):
        match = _CLIMATE_FILE.match(path.name)
        if match is None:
            continue
        month = int(match.group(2))
        if 1 <= month <= 12:
            found.append((match.group(1).lower(), month, path))
    return found


def _sample_points(
    rasters: Sequence[tuple[str, int, Path]],
    points: Sequence[tuple[float, float]],
) -> list[tuple[tuple[float, float, float], ...] | None]:
    import rasterio

    n = len(points)
    tavg: list[list[float | None]] = [[None] * 12 for _ in range(n)]
    prec: list[list[float | None]] = [[None] * 12 for _ in range(n)]
    srad: list[list[float | None]] = [[None] * 12 for _ in range(n)]
    buckets = {"tavg": tavg, "prec": prec, "srad": srad}
    for var, month, path in rasters:
        target = buckets[var]
        with rasterio.open(path) as dataset:
            bounds = dataset.bounds
            nodata = dataset.nodata
            indices = [
                index
                for index in range(n)
                if bounds.left <= points[index][1] <= bounds.right
                and bounds.bottom <= points[index][0] <= bounds.top
            ]
            if not indices:
                continue
            coords = [(points[index][1], points[index][0]) for index in indices]
            for index, sample in zip(indices, dataset.sample(coords), strict=True):
                value = float(sample[0])
                if not math.isfinite(value):
                    continue
                if nodata is not None and value == nodata:
                    continue
                target[index][month - 1] = value
    out: list[tuple[tuple[float, float, float], ...] | None] = []
    for index in range(n):
        if any(
            value is None
            for row in (tavg[index], prec[index], srad[index])
            for value in row
        ):
            out.append(None)
            continue
        out.append(
            tuple(
                (tavg[index][month], prec[index][month], srad[index][month])
                for month in range(12)
            )
        )
    return out


def _refresh_catalog_climate(
    catalog_path: Path, store_path: Path, hike_ids: set[int]
) -> None:
    store = sqlite3.connect(store_path)
    catalog = sqlite3.connect(catalog_path)
    try:
        for name in CLIMATE_FEATURE_NAMES:
            _ensure_column(catalog, "feature_vectors", name, "REAL")
        _ensure_climate_months(catalog)
        by_hike: dict[int, dict[int, list[tuple[float, float, float]]]] = {
            dayhike_id: {month: [] for month in range(1, 13)} for dayhike_id in hike_ids
        }
        ids = list(hike_ids)
        for start in range(0, len(ids), _HIKE_IN_CHUNK):
            chunk = ids[start : start + _HIKE_IN_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            for dayhike_id, month, temperature_c, precipitation_mm, solar_radiation in (
                store.execute(
                    "SELECT dayhike_id, month, temperature_c, precipitation_mm, "
                    "solar_radiation FROM vertex_climate "
                    f"WHERE dayhike_id IN ({placeholders})",
                    chunk,
                )
            ):
                by_hike[int(dayhike_id)][int(month)].append(
                    (float(temperature_c), float(precipitation_mm), float(solar_radiation))
                )
        month_rows: list[tuple[int, int, float, float, float]] = []
        regime_rows: list[tuple[float, float, float, int]] = []
        for dayhike_id, months in by_hike.items():
            if not any(months[month] for month in range(1, 13)):
                continue
            hike_means: list[tuple[float, float, float]] = []
            complete = True
            for month in range(1, 13):
                samples = months[month]
                if not samples:
                    complete = False
                    break
                n = len(samples)
                hike_means.append(
                    (
                        sum(sample[0] for sample in samples) / n,
                        sum(sample[1] for sample in samples) / n,
                        sum(sample[2] for sample in samples) / n,
                    )
                )
            if not complete:
                continue
            for month, (temperature_c, precipitation_mm, solar_radiation) in enumerate(
                hike_means, start=1
            ):
                month_rows.append(
                    (
                        dayhike_id,
                        month,
                        temperature_c,
                        precipitation_mm,
                        solar_radiation,
                    )
                )
            regime_rows.append((*_regime(hike_means), dayhike_id))
        catalog.executemany(
            "DELETE FROM climate_months WHERE dayhike_id = ?",
            [(dayhike_id,) for dayhike_id in hike_ids],
        )
        catalog.executemany(
            "INSERT INTO climate_months (dayhike_id, month, temperature_c, "
            "precipitation_mm, solar_radiation) VALUES (?, ?, ?, ?, ?)",
            month_rows,
        )
        catalog.executemany(
            "UPDATE feature_vectors SET temperature_c = ?, precipitation_mm = ?, "
            "solar_radiation = ? WHERE dayhike_id = ?",
            regime_rows,
        )
        catalog.commit()
    finally:
        store.close()
        catalog.close()
    loaded = Catalog.open(catalog_path)
    _rewrite_scaler(catalog_path, fit_scaler_from_matrix(feature_matrix(loaded.dayhikes)))
    _rewrite_month_scalers(catalog_path, loaded)


def _regime(
    months: Sequence[tuple[float, float, float]],
) -> tuple[float, float, float]:
    return (
        (months[5][0] + months[6][0] + months[7][0]) / 3.0,
        sum(month[1] for month in months) / 12.0,
        (months[5][2] + months[6][2] + months[7][2]) / 3.0,
    )


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


def _rewrite_month_scalers(catalog_path: Path, catalog: Catalog) -> None:
    connection = sqlite3.connect(catalog_path)
    try:
        _ensure_scaler_month(connection)
        connection.execute("DELETE FROM scaler_month")
        rows: list[tuple[int, int, str, float, float]] = []
        for month in range(1, 13):
            observed = [
                hike.climate_months[month - 1]
                for hike in catalog.dayhikes
                if hike.climate_months[month - 1] is not None
            ]
            if observed:
                matrix = [
                    [temperature_c, precipitation_mm, solar_radiation]
                    for temperature_c, precipitation_mm, solar_radiation in observed
                ]
                scaler = fit_scaler_from_matrix(
                    np.array(matrix, dtype=float),
                    CLIMATE_FEATURE_NAMES,
                )
            else:
                scaler = Scaler(CLIMATE_FEATURE_NAMES, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
            for position, (name, mean, scale) in enumerate(
                zip(scaler.feature_names, scaler.mean, scaler.scale, strict=True)
            ):
                rows.append((month, position, name, mean, scale))
        connection.executemany(
            "INSERT INTO scaler_month (month, position, feature_name, mean, scale) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()


def _ensure_vertex_climate(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS vertex_climate (
            dayhike_id INTEGER NOT NULL,
            seq INTEGER NOT NULL,
            month INTEGER NOT NULL,
            temperature_c REAL NOT NULL,
            precipitation_mm REAL NOT NULL,
            solar_radiation REAL NOT NULL,
            PRIMARY KEY (dayhike_id, seq, month)
        )
        """
    )


def _ensure_climate_months(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS climate_months (
            dayhike_id INTEGER NOT NULL,
            month INTEGER NOT NULL,
            temperature_c REAL,
            precipitation_mm REAL,
            solar_radiation REAL,
            PRIMARY KEY (dayhike_id, month)
        )
        """
    )


def _ensure_scaler_month(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS scaler_month (
            month INTEGER NOT NULL,
            position INTEGER NOT NULL,
            feature_name TEXT NOT NULL,
            mean REAL NOT NULL,
            scale REAL NOT NULL,
            PRIMARY KEY (month, position)
        )
        """
    )


def _ensure_column(
    connection: sqlite3.Connection, table: str, column: str, decl: str
) -> None:
    existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _month_tif(dest: Path, variable: str, month: int) -> Path:
    return dest / f"wc2.1_30s_{variable}_{month:02d}.tif"


def _present_month_tifs(dest: Path, variable: str) -> list[Path]:
    paths: list[Path] = []
    for month in range(1, 13):
        path = _month_tif(dest, variable, month)
        if path.exists() and path.stat().st_size > 0:
            paths.append(path)
    return paths


def _extract_worldclim_month_tifs(
    zip_path: Path, dest: Path, variable: str, bounds: Bounds
) -> list[Path]:
    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            name = Path(info.filename).name
            match = _CLIMATE_FILE.match(name)
            if match is None or match.group(1).lower() != variable:
                continue
            month = int(match.group(2))
            if not 1 <= month <= 12:
                continue
            out = _month_tif(dest, variable, month)
            if out.exists() and out.stat().st_size > 0:
                continue
            with archive.open(info) as source, out.open("wb") as target:
                shutil.copyfileobj(source, target)
            _clip_to_bounds(out, bounds)
            if out.exists() and out.stat().st_size > 0:
                extracted.append(out)
    return extracted


def _clip_to_bounds(path: Path, bounds: Bounds) -> None:
    import rasterio
    from rasterio.windows import from_bounds

    with rasterio.open(path) as dataset:
        west = max(bounds.west, float(dataset.bounds.left))
        south = max(bounds.south, float(dataset.bounds.bottom))
        east = min(bounds.east, float(dataset.bounds.right))
        north = min(bounds.north, float(dataset.bounds.top))
        if west >= east or south >= north:
            clipped = None
        else:
            window = from_bounds(west, south, east, north, dataset.transform)
            window = window.round_offsets().round_lengths()
            if window.width <= 0 or window.height <= 0:
                clipped = None
            else:
                data = dataset.read(1, window=window)
                profile = dataset.profile.copy()
                profile.update(
                    height=data.shape[0],
                    width=data.shape[1],
                    transform=dataset.window_transform(window),
                )
                clipped = (data, profile)
    if clipped is None:
        path.unlink()
        return
    data, profile = clipped
    tmp = path.with_name(path.name + ".clip")
    with rasterio.open(tmp, "w", **profile) as out:
        out.write(data, 1)
    tmp.replace(path)


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
