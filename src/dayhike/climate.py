from __future__ import annotations

import math
import re
import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np

from dayhike.catalog import (
    CLIMATE_FEATURE_NAMES,
    Catalog,
    Scaler,
    feature_matrix,
    fit_scaler_from_matrix,
    geometry_store_path_for,
)

Progress = Callable[[str], None]
DEFAULT_CLIMATE_DIR = Path("data/climate")
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
