from __future__ import annotations

import math
import os
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from dayhike.catalog import EARTH_RADIUS_M, geometry_store_path_for, remove_similarity
from dayhike.db import writer
from dayhike.download import download as _download
from dayhike.parallel import parallel_iter, workers

FABDEM_VERSION = "V1-2"
FABDEM_DATASET = "https://huggingface.co/datasets/pavelzaitsau/fabdem-v12/resolve/main/tiles"
FABDEM_ATTRIBUTION = (
    "FABDEM V1-2: Hawker, L., Uhe, P., Paulo, L., Sosa, J., Savage, J., Sampson, C., Neal, J., 2022. "
    "A 30 m global map of elevation with forests and buildings removed. Environmental Research Letters 17, "
    "024016. University of Bristol, https://doi.org/10.5523/bris.s5hqmjcdj8yo2ibzi9b4ew3sn (CC BY-NC-SA 4.0). "
    "Produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 "
    "provided under COPERNICUS by the European Union and ESA; all rights reserved."
)
COPERNICUS_DATASET = "https://copernicus-dem-30m.s3.amazonaws.com"
USGS_3DEP_DATASET = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1/TIFF/current"
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
# Copernicus GLO-30 maps tree tops (ADR-0030). Its cells this close to a point, and
# the WorldCover cells, give the canopy floor and how much of it the point reads ...
CANOPY_RADIUS_M = 75.0
CANOPY_PERCENTILE = 35.0  # ... this percentile of the cells' heights above their plane
_CANOPY_BLOCK = 512  # GLO-30 cells per side of the blocks ``_canopy_floor`` reads at a time
_METRES_PER_DEGREE = EARTH_RADIUS_M * math.pi / 180
_BLOCKS_PER_DEGREE = 7  # about the size of the blocks ``_canopy_floor`` and ``tree_share`` read at a time


def raster_order_sql(lat: str, lon: str) -> str:
    """SQLite ``ORDER BY`` terms that put points in raster order: by 1° cell, then
    by block of a 1/7° cell, then by position. A chunk of points in this order
    falls on few rasters and few blocks of them, so each is read once, not once
    per chunk. Only the order of the reads depends on it, never a height."""

    def cell(column: str, origin: int) -> str:
        return f"CAST({column} + {origin} AS INTEGER)"

    def block(column: str, origin: int) -> str:
        return f"CAST(({column} + {origin} - {cell(column, origin)}) * {_BLOCKS_PER_DEGREE} AS INTEGER)"

    return ", ".join(
        (cell(lat, 90), cell(lon, 180), block(lat, 90), block(lon, 180), lat, lon)
    )


def fabdem_tile_id(lat: float, lon: float) -> str:
    """Return the FABDEM 1° tile id whose southwest corner contains this point."""
    return format_corner(math.floor(lat), math.floor(lon))


def fabdem_archive_id(tile_id: str) -> str:
    """Return the 10° FABDEM archive folder that contains this 1° tile."""
    lat_sw, lon_sw = _parse_tile(tile_id)
    lat0 = int(math.floor(lat_sw / 10) * 10)
    lon0 = int(math.floor(lon_sw / 10) * 10)
    return (
        f"{format_corner(lat0, lon0)}-{format_corner(lat0 + 10, lon0 + 10)}"
        f"_FABDEM_{FABDEM_VERSION}"
    )


@dataclass(frozen=True)
class DemSource:
    """An elevation map in 1° GeoTIFF tiles, each named by a corner.

    FABDEM is the default: bare earth, forests and buildings removed (ADR-0008).
    Copernicus GLO-30 is the alternative: a surface model, tree tops and roofs
    included, free for commercial use (ADR-0030). Both cover the world. USGS
    3DEP 1″ is bare earth for the United States only: a ``regional`` source,
    read where it has a tile, with a world source elsewhere (ADR-0063).
    """

    name: str
    label: str
    file_format: str  # one tile's file name, from {ns}{lat}{ew}{lon} of its corner ({n}, {e} in lower case)
    url_format: str  # its HTTPS URL, from {stem} (file name without .tif), {archive} and {corner} (n40w106)
    excluded: frozenset[str] = frozenset()  # land tiles the source does not publish
    register_id: str = ""  # its row in docs/sources.csv
    regional: bool = False  # covers part of the world: read before the world source where it has a tile
    north_named: bool = False  # tiles named by their northwest corner, not their southwest one

    def tile_file(self, tile_id: str) -> str:
        return self.file_format.format(**self._corner(tile_id))

    def tile_url(self, tile_id: str) -> str:
        corner = self._corner(tile_id)
        file = self.file_format.format(**corner)
        return self.url_format.format(
            stem=file.removesuffix(".tif"),
            archive=fabdem_archive_id(tile_id),
            file=file,
            corner=f"{corner['n']}{corner['lat']:02d}{corner['e']}{corner['lon']:03d}",
        )

    def tile_of_file(self, name: str) -> str:
        """The 1° tile id (its southwest corner) of one of this source's file names."""
        match = re.search(r"([NSns])(\d{2})(?:_00_)?([EWew])(\d{3})", name)
        assert match is not None, name
        lat = int(match[2]) * (1 if match[1] in "Nn" else -1) - self.north_named
        lon = int(match[4]) * (1 if match[3] in "Ee" else -1)
        return format_corner(lat, lon)

    def tiles_in(self, raster_dir: Path) -> dict[str, Path]:
        """This source's tiles in raster_dir, by tile id."""
        return {self.tile_of_file(path.name): path for path in raster_dir.glob(self._pattern())}

    def _corner(self, tile_id: str) -> dict[str, object]:
        lat, lon = _parse_tile(tile_id)
        lat += self.north_named
        ns, ew = "N" if lat >= 0 else "S", "E" if lon >= 0 else "W"
        return {"ns": ns, "n": ns.lower(), "lat": abs(lat), "ew": ew, "e": ew.lower(), "lon": abs(lon)}

    def has_tiles_in(self, raster_dir: Path) -> bool:
        return next(raster_dir.glob(self._pattern()), None) is not None

    def _pattern(self) -> str:
        return re.sub(r"\{[^}]*\}", "*", self.file_format)


FABDEM = DemSource(
    "fabdem",
    "FABDEM",
    "{ns}{lat:02d}{ew}{lon:03d}_FABDEM_" + FABDEM_VERSION + ".tif",
    FABDEM_DATASET + "/{archive}/{file}",
    register_id="fabdem",
)
# GLO-30 Public leaves out these tiles (Armenia, Azerbaijan and their borders): the
# GLO-90 bucket's tileList.txt holds them, the GLO-30 bucket's does not (2026-10-30).
COPERNICUS_EXCLUDED_TILES = frozenset(
    """
    N38E045 N38E046 N38E048 N38E049 N39E044 N39E045 N39E046 N39E047 N39E048
    N39E049 N40E043 N40E044 N40E045 N40E046 N40E047 N40E048 N40E049 N40E050
    N41E043 N41E044 N41E045 N41E046 N41E047 N41E048 N41E049
    """.split()
)
# Art. 6 of the Copernicus DEM licence: the notice and the no-liability sentence.
COPERNICUS_ATTRIBUTION = (
    "Copernicus DEM GLO-30, produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and "
    "© Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; "
    "all rights reserved. The organisations in charge of the Copernicus programme by law or by delegation "
    "do not incur any liability for any use of the Copernicus WorldDEM-30."
)
COPERNICUS = DemSource(
    "copernicus",
    "Copernicus GLO-30",
    "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif",
    COPERNICUS_DATASET + "/{stem}/{file}",
    COPERNICUS_EXCLUDED_TILES,
    register_id="copernicus-glo30",
)
# USGS public domain; the credit USGS asks for, and what the map is.
USGS_3DEP_ATTRIBUTION = (
    "USGS 3D Elevation Program (3DEP) 1 arc-second, a bare-earth elevation map of the United States, "
    "public domain. Credit: U.S. Geological Survey."
)
# 3DEP 1″: one folder per tile, each named by its northwest corner (n40w106 holds 39-40 °N);
# NAD83, about 1 m off WGS 84, read as it is; each tile runs 6 cells past its 1° edges.
USGS_3DEP = DemSource(
    "3dep",
    "USGS 3DEP 1″",
    "USGS_1_{n}{lat:02d}{e}{lon:03d}.tif",
    USGS_3DEP_DATASET + "/{corner}/{file}",
    register_id="usgs-3dep",
    regional=True,
    north_named=True,
)


DEM_SOURCES = {source.name: source for source in (FABDEM, COPERNICUS, USGS_3DEP)}


def dem_sources_of(raster_dir: Path | str) -> tuple[DemSource, ...]:
    """The sources whose tiles raster_dir holds, in the order they are read:
    the regional ones (USGS 3DEP), then the one world source.

    A directory holds at most one world source, so FABDEM and Copernicus
    GLO-30 never mix; a regional source is read where it has a tile (ADR-0063).
    """
    raster_dir = Path(raster_dir)
    found = [source for source in DEM_SOURCES.values() if source.has_tiles_in(raster_dir)]
    world = [source for source in found if not source.regional]
    if len(world) > 1:
        labels = " and ".join(source.label for source in world)
        raise ValueError(f"{raster_dir} holds tiles of {labels}; keep one world elevation source per directory")
    return tuple(source for source in found if source.regional) + tuple(world)


def dem_tiles(raster_dir: Path | str) -> dict[str, tuple[tuple[DemSource, Path], ...]]:
    """Each 1° tile id the DEM tiles in raster_dir cover, with the source and
    file of every tile for it, in the order they are read: a regional
    source's first, then the world source's (``read_in_order``)."""
    tiles: dict[str, tuple[tuple[DemSource, Path], ...]] = {}
    for source in dem_sources_of(raster_dir):
        for tile_id, path in source.tiles_in(Path(raster_dir)).items():
            tiles[tile_id] = (*tiles.get(tile_id, ()), (source, path))
    return tiles


def read_in_order(
    files: Sequence[tuple[DemSource, Path]],
    count: int,
    read: Callable[[DemSource, Path, list[int]], Sequence[float | None]],
) -> tuple[list[float | None], list[DemSource | None]]:
    """The heights of ``count`` points of one 1° cell, and the source of each.

    Each point is read from the first of the cell's ``files`` with data under
    it: ``read(source, path, at)`` gives the heights of the points at
    indices ``at`` on that file, None at nodata. So a point on a nodata cell
    of a regional tile reads the world source's tile (ADR-0063).
    """
    heights: list[float | None] = [None] * count
    read_from: list[DemSource | None] = [None] * count
    missing = list(range(count))
    for source, path in files:
        if not missing:
            break
        for index, value in zip(missing, read(source, path, missing), strict=True):
            if value is not None:
                heights[index], read_from[index] = value, source
        missing = [index for index in missing if heights[index] is None]
    return heights, read_from


def tiles_covering(bounds: Bounds) -> list[str]:
    """1° FABDEM tiles whose southwest-corner cells intersect the box."""
    lat_start = math.floor(bounds.south)
    lat_end = math.floor(bounds.north)
    lon_start = math.floor(bounds.west)
    lon_end = math.floor(bounds.east)
    tiles = [
        format_corner(lat, lon)
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
    return fetch_dem(
        dest, bounds, source=FABDEM, retrieve=retrieve, progress=progress, max_workers=max_workers
    )


def fetch_dem(
    dest: Path | str,
    bounds: Bounds = DEFAULT_DEM_BOUNDS,
    *,
    source: DemSource = FABDEM,
    retrieve: Retrieve | None = None,
    progress: Progress | None = None,
    max_workers: int = 4,
) -> list[Path]:
    """Download the missing 1° GeoTIFFs of ``source`` covering bounds into dest.

    Tiles the source leaves out are not asked for; a tile the server does not
    have (sea) is skipped.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    retrieve = retrieve or _download

    def fetch_one(tile_id: str) -> Path | None:
        path = dest / source.tile_file(tile_id)
        if path.exists() and path.stat().st_size > 0:
            return path
        if progress is not None:
            progress(f"dem fetch: {tile_id}")
        try:
            retrieve(source.tile_url(tile_id), str(path))
        except FileNotFoundError:
            if path.exists():
                path.unlink()
            return None
        if path.exists() and path.stat().st_size > 0:
            return path
        return None

    tiles = [tile_id for tile_id in tiles_covering(bounds) if tile_id not in source.excluded]
    if max_workers <= 1:
        return [path for tile_id in tiles if (path := fetch_one(tile_id)) is not None]
    paths: list[Path] = []
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_one, tile_id): tile_id for tile_id in tiles}
        for future in as_completed(futures):
            try:
                path = future.result()
            except Exception as exc:
                if progress is not None:
                    progress(f"dem fetch error: {exc}")
                failed.append(futures[future])
                continue
            if path is not None:
                paths.append(path)
    if failed:
        raise RuntimeError(f"dem fetch failed for tile(s): {', '.join(sorted(failed))}")
    paths.sort()
    return paths


def read_cells(dataset, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """The value of the cell under each point of an open raster, in its dtype.

    The same as ``dataset.sample`` point by point: the cell is found with the
    same floor, and off the grid a point reads the nodata value (0 without
    one). Each block of the raster holding a point is read once.
    """
    from rasterio.transform import rowcol

    out = np.full(len(lon), dataset.nodata or 0, dtype=dataset.dtypes[0])
    if not len(lon):
        return out
    rows, cols = (np.asarray(v, dtype=np.int64) for v in rowcol(dataset.transform, lon, lat))
    inside = np.flatnonzero((rows >= 0) & (rows < dataset.height) & (cols >= 0) & (cols < dataset.width))
    if len(inside):
        out[inside] = read_grid_cells(dataset, rows[inside], cols[inside])
    return out


def read_grid_cells(dataset, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    """Band 1 at each (row, col) of an open raster, all on its grid, in its dtype.
    Each block of the raster holding a cell is read once."""
    from rasterio.windows import Window

    rows, cols = np.asarray(rows, dtype=np.int64), np.asarray(cols, dtype=np.int64)
    out = np.empty(len(rows), dtype=dataset.dtypes[0])
    if not len(rows):
        return out
    block_height, block_width = dataset.block_shapes[0]
    blocks = rows // block_height * (dataset.width // block_width + 1) + cols // block_width
    order = np.argsort(blocks, kind="stable")
    blocks = blocks[order]
    starts = np.flatnonzero(np.r_[True, blocks[1:] != blocks[:-1]])
    for start, end in zip(starts.tolist(), [*starts[1:].tolist(), len(blocks)]):
        at = order[start:end]
        top = int(rows[at[0]]) // block_height * block_height
        left = int(cols[at[0]]) // block_width * block_width
        window = Window(left, top, min(block_width, dataset.width - left), min(block_height, dataset.height - top))
        out[at] = dataset.read(1, window=window)[rows[at] - top, cols[at] - left]
    return out


def sample_elevations(
    raster_dir: Path | str,
    points: Sequence[tuple[float, float]],
    landcover_dir: Path | str | None = None,
) -> list[float | None]:
    """Sample the DEM tiles in raster_dir at (lat, lon) points. Uncovered is None.

    Copernicus GLO-30 maps tree tops. Given WorldCover tiles in
    ``landcover_dir``, a point with trees within ``CANOPY_RADIUS_M`` is
    lowered toward its canopy floor (``_canopy_floor``) by the share of tree
    cover there; a point with none reads as it is (ADR-0030). FABDEM and
    USGS 3DEP are bare earth and always read as they are.
    """
    return sample_elevations_and_sources(raster_dir, points, landcover_dir)[0]


def sample_elevations_and_sources(
    raster_dir: Path | str,
    points: Sequence[tuple[float, float]],
    landcover_dir: Path | str | None = None,
) -> tuple[list[float | None], list[DemSource | None]]:
    """``sample_elevations``, and the source each height was read from (None uncovered).

    A point reads the tiles of its 1° cell (``dem_tiles``): USGS 3DEP's where
    the directory has one and it has data there, else the world source's.
    """
    import rasterio

    out: list[float | None] = [None] * len(points)
    read_from: list[DemSource | None] = [None] * len(points)
    tiles = dem_tiles(raster_dir)
    if not tiles:
        return out, read_from
    shares = None
    if COPERNICUS in dem_sources_of(raster_dir) and landcover_dir is not None and points:
        from dayhike.landcover import tree_share

        shares = np.nan_to_num(tree_share(landcover_dir, *np.array(points, dtype=float).T, CANOPY_RADIUS_M)).tolist()
    buckets: dict[str, list[int]] = {}
    for index, (lat, lon) in enumerate(points):
        buckets.setdefault(fabdem_tile_id(lat, lon), []).append(index)
    for tile_id, indices in buckets.items():
        if tile_id not in tiles:
            continue

        def read(source: DemSource, path: Path, at: list[int]) -> list[float | None]:
            with rasterio.open(path) as dataset:
                coords = _tile_coords(dataset, [points[indices[k]] for k in at])
                values = _read_heights(dataset, coords)
                if shares is not None and source is COPERNICUS:
                    near = [j for j, k in enumerate(at) if shares[indices[k]] > 0 and values[j] is not None]
                    if near:
                        floors = _canopy_floor(dataset, *np.array([coords[j] for j in near]).T)
                        for j, floor in zip(near, floors.tolist(), strict=True):
                            values[j] -= shares[indices[at[j]]] * (values[j] - floor)
            return values

        heights, sources = read_in_order(tiles[tile_id], len(indices), read)
        for index, value, source in zip(indices, heights, sources, strict=True):
            out[index], read_from[index] = value, source
    return out, read_from


def _tile_coords(dataset, points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """(lon, lat) of (lat, lon) ``points`` on one open DEM tile.

    A GLO-30 or FABDEM grid stops half a cell short of its tile's east and south
    edges; outside it a GLO-30 read gives 0 and a FABDEM read nodata. A point
    there reads the edge cell, as ``dem_heights`` does. A 3DEP grid runs past
    every edge, so no point of its 1° cell is moved.
    """
    box = dataset.bounds
    half_x, half_y = dataset.res[0] / 2, dataset.res[1] / 2
    return [
        (min(max(lon, box.left + half_x), box.right - half_x), min(max(lat, box.bottom + half_y), box.top - half_y))
        for lat, lon in points
    ]


def _read_heights(dataset, coords: Sequence[tuple[float, float]]) -> list[float | None]:
    """The height under each (lon, lat) of ``coords`` on an open tile, None at nodata."""
    nodata = dataset.nodata
    return [
        value if math.isfinite(value) and (nodata is None or value != nodata) else None
        for value in read_cells(dataset, *np.array(coords, dtype=float).T).tolist()
    ]


POINT_TILES_OPEN = 16  # tiles ``PointHeights`` keeps open per process


class PointHeights:
    """Heights of a few points at a time on the DEM tiles in ``raster_dir``,
    each the cell ``sample_elevations`` reads (Copernicus GLO-30 without its
    canopy correction), None off the tiles. Ingest orients lines by the heights
    of their ends (ADR-0048). It keeps the last ``POINT_TILES_OPEN`` tiles
    open, in each process apart, and every height it read."""

    def __init__(self, raster_dir: Path | str, tiles: dict[str, tuple[tuple[DemSource, Path], ...]]) -> None:
        self.raster_dir = Path(raster_dir)
        self.tiles = tiles
        self._pid: int | None = None
        self._open: dict[Path, object] = {}
        self._read: dict[tuple[float, float], float | None] = {}

    def __call__(self, points: Sequence[tuple[float, float]]) -> list[float | None]:
        if self._pid != os.getpid():  # a forked worker opens its own tiles
            self._pid, self._open, self._read = os.getpid(), {}, {}
        for point in points:
            if point not in self._read:
                files = self.tiles.get(fabdem_tile_id(*point), ())
                self._read[point] = read_in_order(files, 1, lambda _source, path, _at: self._heights(path, point))[0][0]
        return [self._read[point] for point in points]

    def _heights(self, path: Path, point: tuple[float, float]) -> list[float | None]:
        dataset = self._dataset(path)
        return _read_heights(dataset, _tile_coords(dataset, [point]))

    def _dataset(self, path: Path):
        if path in self._open:
            self._open[path] = self._open.pop(path)  # the newest
            return self._open[path]
        import rasterio

        dataset = rasterio.open(path)
        if len(self._open) >= POINT_TILES_OPEN:
            self._open.pop(next(iter(self._open))).close()
        self._open[path] = dataset
        return dataset


def point_heights(raster_dir: Path | str) -> PointHeights | None:
    """``PointHeights`` on the tiles in ``raster_dir``; None when it holds none."""
    raster_dir = Path(raster_dir)
    tiles = dem_tiles(raster_dir) if raster_dir.is_dir() else {}
    return PointHeights(raster_dir, tiles) if tiles else None


def require_worldcover(landcover_dir: Path | str | None) -> None:
    """Refuse to sample Copernicus GLO-30 without WorldCover tiles: its heights
    under trees would keep the canopy (ADR-0030)."""
    if landcover_dir is None or not any(Path(landcover_dir).glob("ESA_WorldCover_*_Map.tif")):
        raise ValueError(
            f"Copernicus GLO-30 needs WorldCover tiles to read the ground under trees, and {landcover_dir} "
            "has none: run `dayhike fetch-landcover` or pass --landcover"
        )


def _canopy_floor(dataset, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """The ground under a forest canopy at points on one open Copernicus tile.

    The cells within ``CANOPY_RADIUS_M`` of a point's cell are taken as a
    least-squares plane plus tree tops: the floor is the plane at the point
    plus the ``CANOPY_PERCENTILE`` of the cells' heights above the plane, so a
    slope stays and the canopy goes. Near the grid's edge the edge cells count
    again, at their own place, so the plane keeps the slope there too.
    """
    from rasterio.transform import rowcol
    from rasterio.windows import Window

    cell_y = dataset.res[1] * _METRES_PER_DEGREE
    cell_x = dataset.res[0] * _METRES_PER_DEGREE * math.cos(
        math.radians((dataset.bounds.bottom + dataset.bounds.top) / 2)
    )
    reach_y, reach_x = int(CANOPY_RADIUS_M // cell_y), int(CANOPY_RADIUS_M // cell_x)
    down, right = (offset.ravel() for offset in np.mgrid[-reach_y : reach_y + 1, -reach_x : reach_x + 1])
    within = np.hypot(down * cell_y, right * cell_x) <= CANOPY_RADIUS_M
    down, right = down[within], right[within]
    rows, cols = (np.asarray(v) for v in rowcol(dataset.transform, lon, lat))
    rows = np.clip(rows, 0, dataset.height - 1)
    cols = np.clip(cols, 0, dataset.width - 1)
    cell_rows = np.clip(rows[:, None] + down, 0, dataset.height - 1)
    cell_cols = np.clip(cols[:, None] + right, 0, dataset.width - 1)
    heights = np.empty(cell_rows.shape)
    # One read per block of cells the points fall in.
    blocks = rows // _CANOPY_BLOCK * (dataset.width // _CANOPY_BLOCK + 1) + cols // _CANOPY_BLOCK
    for block in np.unique(blocks):
        at = np.flatnonzero(blocks == block)
        top = max(int(rows[at].min()) - reach_y, 0)
        bottom = min(int(rows[at].max()) + reach_y + 1, dataset.height)
        left = max(int(cols[at].min()) - reach_x, 0)
        width = min(int(cols[at].max()) + reach_x + 1, dataset.width) - left
        grid = dataset.read(1, window=Window(left, top, width, bottom - top)).astype(float)
        heights[at] = grid[cell_rows[at] - top, cell_cols[at] - left]
    north = -(cell_rows - rows[:, None]) * cell_y
    east = (cell_cols - cols[:, None]) * cell_x
    # pinv: a window one cell wide has no slope that way.
    plane = np.stack([np.ones_like(east), east, north], axis=2)
    fit = (np.linalg.pinv(plane) @ heights[:, :, None])[:, :, 0]
    above = heights - (plane @ fit[:, :, None])[:, :, 0]
    return fit[:, 0] + np.percentile(above, CANOPY_PERCENTILE, axis=1)


def sample_dem(
    catalog_path: Path | str,
    raster_dir: Path | str,
    *,
    landcover_dir: Path | str | None = None,
    progress: Progress | None = None,
) -> int:
    """Write DEM elevations onto covered GeometryStore vertices and Catalog stats.

    The tiles in raster_dir name the sources: USGS 3DEP where it has a tile
    with data, else the one world source (``read_in_order``). Copernicus GLO-30 needs the
    WorldCover tiles in ``landcover_dir`` to read the ground under trees
    (``sample_elevations``). Copernicus GLO-30 and USGS 3DEP record their
    attribution in the GeometryStore while the directory holds their tiles;
    FABDEM, the default, needs none. Each DayHike sampled records in the
    Catalog's ``dayhikes.dem_source`` the register ids of the sources its
    heights were read from (ADR-0063).
    """
    catalog_path = Path(catalog_path)
    raster_dir = Path(raster_dir)
    workers()  # a bad DAYHIKE_WORKERS fails here, before the GeometryStore is open
    store_path = geometry_store_path_for(catalog_path)
    if not store_path.is_file():
        raise FileNotFoundError(store_path)
    sources = dem_sources_of(raster_dir)
    if not sources:
        return 0
    if COPERNICUS in sources:
        require_worldcover(landcover_dir)
    available = set(dem_tiles(raster_dir))
    south = min(_parse_tile(tile_id)[0] for tile_id in available)
    west = min(_parse_tile(tile_id)[1] for tile_id in available)
    north = max(_parse_tile(tile_id)[0] + 1 for tile_id in available)
    east = max(_parse_tile(tile_id)[1] + 1 for tile_id in available)
    sampled = 0
    touched: dict[int, set[str]] = {}
    if progress is not None:
        progress("dem sample: vertices")
    write = writer(store_path)
    try:
        write.execute("PRAGMA journal_mode=WAL")
        # Hikes share vertices, so each distinct point is sampled once, in raster
        # order, into ``points``; the vertices then take their point's height.
        write.execute(
            "CREATE TEMP TABLE points (latitude REAL NOT NULL, longitude REAL NOT NULL, "
            "ele REAL NOT NULL, register_id TEXT NOT NULL)"
        )
        read = sqlite3.connect(store_path)
        try:
            cursor = read.execute(
                "SELECT DISTINCT latitude, longitude FROM vertices "
                "WHERE latitude >= ? AND latitude < ? AND longitude >= ? AND longitude < ? "
                f"ORDER BY {raster_order_sql('latitude', 'longitude')}",
                (south, north, west, east),
            )

            def chunk_updates(rows: list[tuple]) -> list[tuple[float, float, float, str]]:
                # A chunk's (latitude, longitude, elevation, register id) of points, sampled in a worker.
                coverable = [
                    (float(lat), float(lon)) for lat, lon in rows if fabdem_tile_id(float(lat), float(lon)) in available
                ]
                if not coverable:
                    return []
                elevations, read_from = sample_elevations_and_sources(raster_dir, coverable, landcover_dir)
                return [
                    (lat, lon, elevation, source.register_id)
                    for (lat, lon), elevation, source in zip(coverable, elevations, read_from, strict=True)
                    if elevation is not None and source is not None
                ]

            points = 0
            chunks = iter(lambda: cursor.fetchmany(_VERTEX_CHUNK), [])
            for updates in parallel_iter(chunk_updates, chunks):
                write.executemany("INSERT INTO points VALUES (?, ?, ?, ?)", updates)
                points += len(updates)
                if progress is not None:
                    progress(f"dem sample: {points} points")
        finally:
            read.close()
        write.execute("CREATE INDEX points_at ON points (latitude, longitude)")
        # CROSS JOIN keeps the vertices as the outer loop and the index as the lookup.
        for dayhike_id, register_id in write.execute(
            "SELECT DISTINCT vertices.dayhike_id, points.register_id FROM vertices "
            "CROSS JOIN points ON points.latitude = vertices.latitude AND points.longitude = vertices.longitude"
        ).fetchall():
            touched.setdefault(dayhike_id, set()).add(register_id)
        sampled = write.execute(
            "UPDATE vertices SET ele = (SELECT ele FROM points WHERE points.latitude = vertices.latitude "
            "AND points.longitude = vertices.longitude) "
            "WHERE EXISTS (SELECT 1 FROM points WHERE points.latitude = vertices.latitude "
            "AND points.longitude = vertices.longitude)"
        ).rowcount
        if progress is not None:
            progress(f"dem sample: {sampled} vertices")
        write.execute(
            "INSERT OR REPLACE INTO geometry_meta (key, value) VALUES (?, ?)",
            (DEM_READ_KEY["sample-dem"], dem_read_by(raster_dir)),
        )
        for source, key, text in _ATTRIBUTIONS:
            if source in sources:
                write.execute("INSERT OR REPLACE INTO geometry_meta (key, value) VALUES (?, ?)", (key, text))
            else:
                write.execute("DELETE FROM geometry_meta WHERE key = ?", (key,))
        write.commit()
    finally:
        if write.in_transaction:
            write.rollback()
        write.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        write.execute("PRAGMA journal_mode=DELETE")
        write.close()
    if not touched:
        return 0
    if progress is not None:
        progress("dem sample: feature_vectors")
    _refresh_catalog_elevations(catalog_path, store_path, set(touched))
    _record_dem_sources(catalog_path, touched)
    remove_similarity(catalog_path, progress)
    return sampled


# The GeometryStore key of each source that records its attribution there.
_ATTRIBUTIONS = (
    (COPERNICUS, "copernicus_attribution", COPERNICUS_ATTRIBUTION),
    (USGS_3DEP, "usgs_3dep_attribution", USGS_3DEP_ATTRIBUTION),
)
# The register id of each such source, by its key in an export's attribution.
DEM_ATTRIBUTION_KEYS = {key.removesuffix("_attribution"): source.register_id for source, key, _ in _ATTRIBUTIONS}


def dem_source_value(register_ids: set[str]) -> str:
    """A DayHike's ``dem_source``: the register ids of the sources its heights
    were read from, in ``DEM_SOURCES`` order, comma-separated (one, but for a
    hike that crosses the edge of a regional source)."""
    return ",".join(source.register_id for source in DEM_SOURCES.values() if source.register_id in register_ids)


# Each step that reads the DEM records ``dem_read_by`` of its ``--dem`` directory, and ``export-serving``
# refuses when they differ: ingest and sample-dem under these GeometryStore keys, sample-trailgraph in
# the TrailGraph's ``trailgraph_meta`` and build-similarity in the similarity directory's ``meta.json``
# (both as ``dem_read``).
DEM_READ_KEY = {"ingest": "dem_read_ingest", "sample-dem": "dem_read_sample_dem"}


def dem_read_by(raster_dir: Path | str | None) -> str:
    """The register ids of the sources whose tiles ``raster_dir`` holds, as
    ``dem_source_value`` writes them; empty when it holds none (the step reads no DEM)."""
    if raster_dir is None or not Path(raster_dir).is_dir():
        return ""
    return dem_source_value({source.register_id for source in dem_sources_of(raster_dir)})


class DemSourceMismatch(ValueError):
    """DEM steps of one build read different elevation sources."""


def check_one_dem_source(read: Mapping[str, str]) -> None:
    """Refuse DEM steps that read different sources: heights of one source would
    be credited, and licensed, as another's (ADR-0030). ``read`` is each step's
    ``dem_read_by``; a step that read none is not in it."""
    if len(set(read.values())) > 1:
        steps = ", ".join(f"{step}={source}" for step, source in read.items())
        raise DemSourceMismatch(
            f"the steps read different elevation sources ({steps}); "
            "run every DEM step on the same --dem directory, then export again"
        )


def _record_dem_sources(catalog_path: Path, by_hike: dict[int, set[str]]) -> None:
    catalog = writer(catalog_path)
    try:
        if "dem_source" not in {row[1] for row in catalog.execute("PRAGMA table_info(dayhikes)")}:
            catalog.execute("ALTER TABLE dayhikes ADD COLUMN dem_source TEXT")
        catalog.executemany(
            "UPDATE dayhikes SET dem_source = ? WHERE id = ?",
            [(dem_source_value(ids), dayhike_id) for dayhike_id, ids in by_hike.items()],
        )
        catalog.commit()
    finally:
        catalog.close()


def _refresh_catalog_elevations(
    catalog_path: Path, store_path: Path, hike_ids: set[int]
) -> None:
    store = sqlite3.connect(store_path)
    catalog = writer(catalog_path)
    try:
        by_hike: dict[int, list[tuple[float, float, float]]] = {dayhike_id: [] for dayhike_id in hike_ids}
        ids = list(hike_ids)
        for start in range(0, len(ids), _HIKE_IN_CHUNK):
            chunk = ids[start : start + _HIKE_IN_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            for dayhike_id, lat, lon, elevation in store.execute(
                "SELECT dayhike_id, latitude, longitude, ele FROM vertices "
                f"WHERE dayhike_id IN ({placeholders}) ORDER BY dayhike_id, seq",
                chunk,
            ):
                by_hike[int(dayhike_id)].append(
                    (lat, lon, math.nan if elevation is None else float(elevation))
                )
        updates = [
            (*_elevation_stats(vertices), dayhike_id)
            for dayhike_id, vertices in by_hike.items()
            if any(math.isfinite(ele) for _, _, ele in vertices)
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


def _elevation_stats(vertices: Sequence[tuple[float, float, float]]) -> tuple[float, float, float]:
    """Lowest, highest and gain of (lat, lon, ele) vertices in walk order (NaN ele missing).

    Gain is the similarity facts' rule: spikes dropped, a 10 m dead-band, the
    larger of climb and descent.
    """
    from dayhike.similarity import elevation_gain

    lat, lon, ele = np.array(vertices, dtype=float).T
    known = ele[np.isfinite(ele)]
    return float(known.min()), float(known.max()), elevation_gain(lat, lon, ele)


def format_corner(lat_sw: int, lon_sw: int) -> str:
    """A 1° tile id from its southwest corner, as "N45E015" or "S09W072"."""
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
