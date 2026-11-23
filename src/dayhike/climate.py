from __future__ import annotations

import re
import sqlite3
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np

from dayhike.catalog import CLIMATE_FEATURE_NAMES, ensure_column, geometry_store_path_for, remove_similarity
from dayhike.db import writer
from dayhike.dem import Bounds, DEFAULT_DEM_BOUNDS
from dayhike.download import download as _download

Progress = Callable[[str], None]
Retrieve = Callable[[str, str], None]
DEFAULT_CLIMATE_DIR = Path("data/climate")
CHELSA_BASE = "https://os.unil.cloud.switch.ch/chelsa02/chelsa/global/climatologies"
CHELSA_PERIOD = "1981-2010"
CHELSA_VARIABLES = ("tas", "pr", "rsds")
CHELSA_ATTRIBUTION = (
    "Karger, D.N., Conrad, O., Böhner, J., Kawohl, T., Kreft, H., Soria-Auza, R.W., "
    "Zimmermann, N.E., Linder, H.P., Kessler, M., 2017. Climatologies at high "
    "resolution for the earth's land surface areas. Scientific Data 4, 170122. "
    "CHELSA V2.1, EnviDat, https://doi.org/10.16904/envidat.228.v2.1 (CC0 1.0)."
)
# CHELSA stores uint16 with a per-file scale/offset to K, kg m-2 month-1 and W m-2.
# After that, value * factor + offset gives °C, mm/month and kJ m-2 day-1
# (W m-2 * 86 400 s / 1000), the units the climate Measures have always used.
_UNITS = {"tas": (1.0, -273.15), "pr": (1.0, 0.0), "rsds": (86.4, 0.0)}
_PLAUSIBLE = {"tas": (-70.0, 50.0), "pr": (0.0, 3000.0), "rsds": (0.0, 45000.0)}
_VERTEX_CHUNK = 1_000_000
# Clips of one source grid agree on origin only to float noise (~1e-14 degrees);
# CHELSA cells are ~8e-3. Origins are snapped to _GRID_SNAP when written and
# grids compare equal within _GRID_TOLERANCE when read, so clips fetched
# before the snap still count as one grid.
_GRID_SNAP = 9
_GRID_TOLERANCE = 1e-9
_CLIMATE_FILE = re.compile(r"^chelsa_(tas|pr|rsds)_(\d{2})\.tif$", re.IGNORECASE)


def chelsa_url(variable: str, month: int) -> str:
    """HTTPS URL of one CHELSA V2.1 1981–2010 monthly climatology (tas, pr, or rsds)."""
    name = f"CHELSA_{variable}_{month:02d}_{CHELSA_PERIOD}_V.2.1.tif"
    return f"{CHELSA_BASE}/{variable}/{CHELSA_PERIOD}/{name}"


def fetch_chelsa(
    dest: Path | str,
    bounds: Bounds = DEFAULT_DEM_BOUNDS,
    *,
    retrieve: Retrieve | None = None,
    progress: Progress | None = None,
) -> list[Path]:
    """Download missing CHELSA V2.1 1981–2010 monthly GeoTIFFs into dest.

    Each global GeoTIFF is clipped to bounds (default: the DEM Alps+Dinarides
    bbox) and stored as float32 in °C, mm/month, or kJ m-2 day-1. A present
    GeoTIFF that does not cover bounds or cannot be read counts as missing and
    is replaced.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    retrieve = retrieve or _download
    paths: list[Path] = []
    for variable in CHELSA_VARIABLES:
        for month in range(1, 13):
            out = _month_tif(dest, variable, month)
            if _covers(out, bounds):
                paths.append(out)
                continue
            if progress is not None:
                progress(f"climate fetch: {variable} {month:02d}")
            with tempfile.TemporaryDirectory() as tmp:
                download = Path(tmp) / "chelsa.tif"
                try:
                    retrieve(chelsa_url(variable, month), str(download))
                except FileNotFoundError:
                    continue
                if not download.exists() or download.stat().st_size == 0:
                    continue
                # Unlink first: out may be a symlink to another data directory.
                out.unlink(missing_ok=True)
                _clip_and_convert(download, out, variable, bounds)
            if out.exists():
                paths.append(out)
    return sorted(paths)


def sample_climate(
    catalog_path: Path | str,
    raster_dir: Path | str,
    *,
    progress: Progress | None = None,
) -> int:
    """Write Catalog hike-level monthly climate means from local rasters.

    A vertex counts when all 36 rasters have a value there. A DayHike gets the
    mean over its counted vertices, or no climate when none counts. Returns the
    number of counted vertices.
    """
    catalog_path = Path(catalog_path)
    raster_dir = Path(raster_dir)
    store_path = geometry_store_path_for(catalog_path)
    if not store_path.is_file():
        raise FileNotFoundError(store_path)
    rasters = _climate_rasters(raster_dir)
    if not rasters:
        return 0
    missing = [
        f"{variable}_{month:02d}"
        for variable in CHELSA_VARIABLES
        for month in range(1, 13)
        if not any(var == variable and m == month for var, m, _path in rasters)
    ]
    if missing:
        raise ValueError(f"incomplete climate rasters in {raster_dir}, missing: {', '.join(missing)}")
    if progress is not None:
        progress("climate sample: vertices")
    grids, grid_of = _grids_of(rasters)
    with tempfile.TemporaryDirectory(prefix="dayhike-climate-") as spool:
        # Each vertex's hike and cell on each grid, spooled: the vertices are
        # never all in memory, and the distinct cells are known once all are read.
        vertices = Path(spool) / "vertices"
        hike_ids, grid_cells = _spool_vertex_cells(store_path, grids, vertices)
        if not len(hike_ids):
            return 0
        # Each raster read once, at the distinct cells the vertices fall in:
        # which vertices every raster covers, then per-hike sums over those
        # vertices only, added in the order the vertices are read.
        known = {grid: (cells, np.zeros(0, dtype=np.int64)) for grid, cells in zip(grids, grid_cells, strict=True)}
        empty = np.zeros(0)
        values = []
        for var, month, path in rasters:
            if progress is not None:
                progress(f"climate sample: {var} {month:02d}")
            values.append(_raster_cells(path, empty, empty, known)[0])
        covered_cells = [
            np.logical_and.reduce([~np.isnan(value) for value, grid in zip(values, grid_of, strict=True) if grid == index])
            for index in range(len(grids))
        ]
        counts = np.zeros(len(hike_ids), dtype=np.int64)
        sums = np.zeros((len(rasters), len(hike_ids)))
        with open(vertices, "rb") as spooled:
            while len(chunk := np.fromfile(spooled, _vertex_cells(len(grids)), count=_VERTEX_CHUNK)):
                at = [np.searchsorted(cells, chunk["cell"][:, index]) for index, cells in enumerate(grid_cells)]
                covered = np.logical_and.reduce([on[at[index]] for index, on in enumerate(covered_cells)])
                hike = np.searchsorted(hike_ids, chunk["hike"][covered])
                counts += np.bincount(hike, minlength=len(hike_ids))
                for row, value, grid in zip(sums, values, grid_of, strict=True):
                    np.add.at(row, hike, value[at[grid][covered]])
    sampled = int(counts.sum())
    if not sampled:
        return 0
    means = np.full((len(hike_ids), 12, 3), np.nan)
    for (var, month, _path), row in zip(rasters, sums, strict=True):
        with np.errstate(invalid="ignore", divide="ignore"):
            means[:, month - 1, CHELSA_VARIABLES.index(var)] = row / counts
    if progress is not None:
        progress(f"climate sample: {sampled} vertices")
    _drop_vertex_climate(store_path)
    if progress is not None:
        progress("climate sample: feature_vectors")
    _write_catalog_climate(catalog_path, hike_ids[counts > 0], means[counts > 0])
    remove_similarity(catalog_path, progress)
    return sampled


def _climate_rasters(raster_dir: Path) -> list[tuple[str, int, Path]]:
    found: list[tuple[str, int, Path]] = []
    for path in sorted(raster_dir.glob("*.tif")):
        match = _CLIMATE_FILE.match(path.name)
        if match is None:
            continue
        month = int(match.group(2))
        if 1 <= month <= 12:
            found.append((match.group(1).lower(), month, path))
    return found


def _grids_of(rasters: Sequence[tuple[str, int, Path]]) -> tuple[list[tuple], list[int]]:
    """The distinct grids (transform, height, width) of the rasters, each as
    its first raster has it, and the index of each raster's grid."""
    import rasterio

    grids: list[tuple] = []
    grid_of: list[int] = []
    for _var, _month, path in rasters:
        with rasterio.open(path) as dataset:
            grid = (tuple(dataset.transform)[:6], dataset.height, dataset.width)
        index = next((index for index, known in enumerate(grids) if _same_grid(known, grid)), len(grids))
        if index == len(grids):
            grids.append(grid)
        grid_of.append(index)
    return grids, grid_of


def _vertex_cells(grid_count: int) -> np.dtype:
    return np.dtype([("hike", "<i8"), ("cell", "<i8", (grid_count,))])


def _spool_vertex_cells(store_path: Path, grids: list[tuple], path: Path) -> tuple[np.ndarray, list[np.ndarray]]:
    """Spool each vertex's hike and cell on each grid (row x width + col, -1
    off it) to ``path`` in the order the vertices are read; return the
    distinct hikes and the distinct cells on each grid, sorted."""
    from rasterio import Affine

    hikes = [np.zeros(0, dtype=np.int64)]
    cells = [[np.zeros(0, dtype=np.int64)] for _grid in grids]
    connection = sqlite3.connect(store_path)
    try:
        cursor = connection.execute("SELECT dayhike_id, latitude, longitude FROM vertices")
        with open(path, "wb") as out:
            while rows := cursor.fetchmany(_VERTEX_CHUNK):
                table = np.array(rows, dtype=np.float64)
                chunk = np.empty(len(table), _vertex_cells(len(grids)))
                chunk["hike"] = table[:, 0].astype(np.int64)
                hikes = _add_distinct(hikes, chunk["hike"])
                for index, (transform, height, width) in enumerate(grids):
                    x, y = ~Affine(*transform) @ (table[:, 2], table[:, 1])
                    row, col = np.floor(y).astype(np.int64), np.floor(x).astype(np.int64)
                    inside = (row >= 0) & (row < height) & (col >= 0) & (col < width)
                    chunk["cell"][:, index] = np.where(inside, row * width + col, -1)
                    cells[index] = _add_distinct(cells[index], chunk["cell"][:, index])
                chunk.tofile(out)
    finally:
        connection.close()
    return np.unique(np.concatenate(hikes)), [np.unique(np.concatenate(each)) for each in cells]


def _add_distinct(parts: list[np.ndarray], values: np.ndarray) -> list[np.ndarray]:
    """``parts`` (the distinct values so far first) with ``values``' distinct
    values added, merged into the first part once the others outgrow it."""
    parts = [*parts, np.unique(values)]
    if sum(len(part) for part in parts[1:]) > len(parts[0]):
        parts = [np.unique(np.concatenate(parts))]
    return parts


def _raster_values(
    path: Path, latitudes: np.ndarray, longitudes: np.ndarray
) -> np.ndarray:
    """Cell value under each point as float64; NaN off the raster or on nodata."""
    import rasterio
    from rasterio.windows import Window

    values = np.full(len(latitudes), np.nan)
    with rasterio.open(path) as dataset:
        cols, rows = ~dataset.transform @ (longitudes, latitudes)
        rows = np.floor(rows).astype(np.int64)
        cols = np.floor(cols).astype(np.int64)
        inside = (rows >= 0) & (rows < dataset.height) & (cols >= 0) & (cols < dataset.width)
        if not inside.any():
            return values
        rows, cols = rows[inside], cols[inside]
        top, left = int(rows.min()), int(cols.min())
        window = Window(left, top, int(cols.max()) - left + 1, int(rows.max()) - top + 1)
        data = dataset.read(1, window=window).astype(np.float64)
        nodata = dataset.nodata
    if nodata is not None and not np.isnan(nodata):
        data[data == nodata] = np.nan
    data[~np.isfinite(data)] = np.nan
    values[inside] = data[rows - top, cols - left]
    return values


def _same_grid(first: tuple, second: tuple) -> bool:
    """Whether two (transform, height, width) grids match, the transforms to float noise."""
    from rasterio import Affine

    return first[1:] == second[1:] and Affine(*first[0]).almost_equals(Affine(*second[0]), _GRID_TOLERANCE)


def _raster_cells(
    path: Path, latitudes: np.ndarray, longitudes: np.ndarray, grids: dict[tuple, tuple[np.ndarray, np.ndarray]]
) -> tuple[np.ndarray, np.ndarray]:
    """The values ``_raster_values`` gives, as each distinct cell's value and each point's cell.

    Returns the value per distinct cell (float64; NaN off the raster or on
    nodata) and, per point, the index of its cell in it. The raster is read
    once. Rasters on one grid (to float noise) share the cells: ``grids`` keeps them by grid.
    """
    import rasterio
    from rasterio.windows import Window

    with rasterio.open(path) as dataset:
        grid = (tuple(dataset.transform)[:6], dataset.height, dataset.width)
        known = next((key for key in grids if _same_grid(key, grid)), None)
        if known is not None:
            grid = known
        else:
            cols, rows = ~dataset.transform @ (longitudes, latitudes)
            rows = np.floor(rows).astype(np.int64)
            cols = np.floor(cols).astype(np.int64)
            inside = (rows >= 0) & (rows < dataset.height) & (cols >= 0) & (cols < dataset.width)
            grids[grid] = np.unique(np.where(inside, rows * dataset.width + cols, -1), return_inverse=True)
        cells, point_cell = grids[grid]
        values = np.full(len(cells), np.nan)
        on = cells >= 0
        if not on.any():
            return values, point_cell
        rows, cols = cells[on] // dataset.width, cells[on] % dataset.width
        top, left = int(rows.min()), int(cols.min())
        window = Window(left, top, int(cols.max()) - left + 1, int(rows.max()) - top + 1)
        data = dataset.read(1, window=window)[rows - top, cols - left].astype(np.float64)
        nodata = dataset.nodata
    if nodata is not None and not np.isnan(nodata):
        data[data == nodata] = np.nan
    data[~np.isfinite(data)] = np.nan
    values[on] = data
    return values, point_cell


def _drop_vertex_climate(store_path: Path) -> None:
    """Drop the per-vertex climate older versions stored; run VACUUM to free the disk."""
    store = writer(store_path)
    try:
        store.execute("DROP TABLE IF EXISTS vertex_climate")
        store.execute("DELETE FROM geometry_meta WHERE key = 'worldclim_attribution'")
        store.execute(
            "INSERT OR REPLACE INTO geometry_meta (key, value) VALUES (?, ?)",
            ("chelsa_attribution", CHELSA_ATTRIBUTION),
        )
        store.commit()
    finally:
        store.close()


def _write_catalog_climate(
    catalog_path: Path, hike_ids: np.ndarray, means: np.ndarray
) -> None:
    """Replace every hike's climate: covered hikes get means, the rest NULL."""
    catalog = writer(catalog_path)
    try:
        for name in CLIMATE_FEATURE_NAMES:
            ensure_column(catalog, "feature_vectors", name, "REAL")
        _ensure_climate_months(catalog)
        month_rows: list[tuple[int, int, float, float, float]] = []
        regime_rows: list[tuple[float, float, float, int]] = []
        for dayhike_id, hike_means in zip(hike_ids.tolist(), means.tolist(), strict=True):
            for month, (temperature_c, precipitation_mm, solar_radiation) in enumerate(
                hike_means, start=1
            ):
                month_rows.append(
                    (dayhike_id, month, temperature_c, precipitation_mm, solar_radiation)
                )
            regime_rows.append((*regime(hike_means), dayhike_id))
        catalog.execute("DELETE FROM climate_months")
        catalog.execute(
            "UPDATE feature_vectors SET temperature_c = NULL, precipitation_mm = NULL, "
            "solar_radiation = NULL"
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
        catalog.close()


def regime(
    months: Sequence[Sequence[float]],
) -> tuple[float, float, float]:
    """The default climate regime of 12 months of (temperature, precipitation,
    radiation): June–August temperature and radiation, all-year precipitation."""
    return (
        (months[5][0] + months[6][0] + months[7][0]) / 3.0,
        sum(month[1] for month in months) / 12.0,
        (months[5][2] + months[6][2] + months[7][2]) / 3.0,
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


def _month_tif(dest: Path, variable: str, month: int) -> Path:
    return dest / f"chelsa_{variable}_{month:02d}.tif"


def _covers(path: Path, bounds: Bounds) -> bool:
    """Whether path is a readable raster whose extent holds bounds, to one cell."""
    import rasterio
    from rasterio.errors import RasterioIOError

    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        with rasterio.open(path) as dataset:
            extent = dataset.bounds
            cell_x, cell_y = dataset.res
    except RasterioIOError:
        return False
    return (
        extent.left <= bounds.west + cell_x
        and extent.bottom <= bounds.south + cell_y
        and extent.right >= bounds.east - cell_x
        and extent.top >= bounds.north - cell_y
    )


def _snapped(transform):
    """transform with its origin rounded, so every clip of one source grid gets the same one."""
    from rasterio import Affine

    return Affine(transform.a, transform.b, round(transform.c, _GRID_SNAP), transform.d, transform.e, round(transform.f, _GRID_SNAP))


def _clip_and_convert(source: Path, out: Path, variable: str, bounds: Bounds) -> None:
    """Write source clipped to bounds as float32 in Measure units, NaN for nodata."""
    import rasterio
    from rasterio.windows import from_bounds

    with rasterio.open(source) as dataset:
        west = max(bounds.west, float(dataset.bounds.left))
        south = max(bounds.south, float(dataset.bounds.bottom))
        east = min(bounds.east, float(dataset.bounds.right))
        north = min(bounds.north, float(dataset.bounds.top))
        if west >= east or south >= north:
            return
        window = from_bounds(west, south, east, north, dataset.transform)
        window = window.round_offsets().round_lengths()
        if window.width <= 0 or window.height <= 0:
            return
        raw = dataset.read(1, window=window, masked=True)
        factor, offset = _UNITS[variable]
        physical = raw.astype(np.float64) * dataset.scales[0] + dataset.offsets[0]
        data = (physical * factor + offset).astype(np.float32).filled(np.nan)
        low, high = _PLAUSIBLE[variable]
        if np.any((data < low) | (data > high)):
            raise ValueError(
                f"{out.name}: converted {variable} outside plausible range "
                f"{low:g}..{high:g} (min {np.nanmin(data):g}, max {np.nanmax(data):g}); "
                "check the file's scale and offset"
            )
        profile = {
            "driver": "GTiff",
            "height": data.shape[0],
            "width": data.shape[1],
            "count": 1,
            "dtype": "float32",
            "crs": dataset.crs,
            "transform": _snapped(dataset.window_transform(window)),
            "nodata": np.nan,
            "compress": "deflate",
            "predictor": 3,
            "tiled": True,
        }
    tmp = out.with_name(out.name + ".part")
    with rasterio.open(tmp, "w", **profile) as target:
        target.write(data, 1)
    tmp.replace(out)
