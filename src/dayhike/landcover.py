from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path

from dayhike.catalog import (
    Catalog,
    LANDCOVER_FEATURE_NAMES,
    Scaler,
    feature_matrix,
    fit_scaler_from_matrix,
    geometry_store_path_for,
)

Progress = Callable[[str], None]
DEFAULT_LANDCOVER_DIR = Path("data/landcover")
WORLDCOVER_ATTRIBUTION = (
    "© ESA WorldCover project 2021 / contains modified Copernicus Sentinel data "
    "(2021) processed by ESA WorldCover consortium"
)
_VERTEX_CHUNK = 50_000
_HIKE_IN_CHUNK = 500
_WORLDCOVER_NODATA = 0
_TREE = 10
_SHRUB = 20
_GRASS = 30
_BARE = 60
_SNOW = 70


def sample_landcover_classes(
    raster_dir: Path | str,
    points: Sequence[tuple[float, float]],
) -> list[int | None]:
    """Sample land-cover GeoTIFFs at (lat, lon). Uncovered is None."""
    import rasterio

    raster_dir = Path(raster_dir)
    out: list[int | None] = [None] * len(points)
    rasters = sorted(raster_dir.glob("*.tif")) + sorted(raster_dir.glob("*.tiff"))
    if not rasters:
        return out
    remaining = set(range(len(points)))
    for path in rasters:
        if not remaining:
            break
        with rasterio.open(path) as dataset:
            bounds = dataset.bounds
            nodata = dataset.nodata
            indices = [
                index
                for index in remaining
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
                if value == _WORLDCOVER_NODATA:
                    continue
                out[index] = int(value)
                remaining.discard(index)
    return out


def sample_landcover(
    catalog_path: Path | str,
    raster_dir: Path | str,
    *,
    progress: Progress | None = None,
) -> int:
    """Write land-cover classes onto covered vertices and Catalog fractions."""
    catalog_path = Path(catalog_path)
    raster_dir = Path(raster_dir)
    store_path = geometry_store_path_for(catalog_path)
    if not store_path.is_file():
        raise FileNotFoundError(store_path)
    rasters = list(raster_dir.glob("*.tif")) + list(raster_dir.glob("*.tiff"))
    if not rasters:
        return 0
    if progress is not None:
        progress("landcover sample: vertices")
    sampled = 0
    touched: set[int] = set()
    write = sqlite3.connect(store_path)
    try:
        write.execute("PRAGMA journal_mode=WAL")
        _ensure_column(write, "vertices", "landcover", "INTEGER")
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
                classes = sample_landcover_classes(
                    raster_dir,
                    [(lat, lon) for _id, _seq, lat, lon in coverable],
                )
                updates = [
                    (code, dayhike_id, seq)
                    for (dayhike_id, seq, _lat, _lon), code in zip(
                        coverable, classes, strict=True
                    )
                    if code is not None
                ]
                if not updates:
                    continue
                write.executemany(
                    "UPDATE vertices SET landcover = ? WHERE dayhike_id = ? AND seq = ?",
                    updates,
                )
                sampled += len(updates)
                touched.update(dayhike_id for _code, dayhike_id, _seq in updates)
                if progress is not None:
                    progress(f"landcover sample: {sampled} vertices")
            if sampled:
                write.execute(
                    "INSERT OR REPLACE INTO geometry_meta (key, value) VALUES (?, ?)",
                    ("worldcover_attribution", WORLDCOVER_ATTRIBUTION),
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
        progress("landcover sample: feature_vectors")
    _refresh_catalog_fractions(catalog_path, store_path, touched)
    return sampled


def _refresh_catalog_fractions(
    catalog_path: Path, store_path: Path, hike_ids: set[int]
) -> None:
    store = sqlite3.connect(store_path)
    catalog = sqlite3.connect(catalog_path)
    try:
        for name in LANDCOVER_FEATURE_NAMES:
            _ensure_column(catalog, "feature_vectors", name, "REAL")
        by_hike: dict[int, list[int]] = {dayhike_id: [] for dayhike_id in hike_ids}
        ids = list(hike_ids)
        for start in range(0, len(ids), _HIKE_IN_CHUNK):
            chunk = ids[start : start + _HIKE_IN_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            for dayhike_id, code in store.execute(
                "SELECT dayhike_id, landcover FROM vertices "
                f"WHERE dayhike_id IN ({placeholders}) ORDER BY dayhike_id, seq",
                chunk,
            ):
                if code is None:
                    continue
                by_hike[int(dayhike_id)].append(int(code))
        updates = [
            (*_landcover_fractions(codes), dayhike_id)
            for dayhike_id, codes in by_hike.items()
            if codes
        ]
        catalog.executemany(
            "UPDATE feature_vectors SET tree_fraction = ?, shrub_fraction = ?, "
            "grass_fraction = ?, bare_fraction = ?, snow_fraction = ? "
            "WHERE dayhike_id = ?",
            updates,
        )
        catalog.commit()
    finally:
        store.close()
        catalog.close()
    loaded = Catalog.open(catalog_path)
    _rewrite_scaler(catalog_path, fit_scaler_from_matrix(feature_matrix(loaded.dayhikes)))


def _landcover_fractions(
    codes: Sequence[int],
) -> tuple[float, float, float, float, float]:
    n = len(codes)
    return (
        codes.count(_TREE) / n,
        codes.count(_SHRUB) / n,
        codes.count(_GRASS) / n,
        codes.count(_BARE) / n,
        codes.count(_SNOW) / n,
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


def _ensure_column(
    connection: sqlite3.Connection, table: str, column: str, decl: str
) -> None:
    existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
