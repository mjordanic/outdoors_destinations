from __future__ import annotations

import math
import os
import sqlite3
import tempfile
from collections import OrderedDict, deque
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from dayhike.catalog import (
    EARTH_RADIUS_M,
    LANDCOVER_FEATURE_NAMES,
    ensure_column,
    geometry_store_path_for,
    remove_similarity,
)
from dayhike.db import writer
from dayhike.dem import Bounds, DEFAULT_DEM_BOUNDS, format_corner, raster_block
from dayhike.download import download as _download
from dayhike.parallel import parallel_iter, workers

Progress = Callable[[str], None]
Retrieve = Callable[[str, str], None]
DEFAULT_LANDCOVER_DIR = Path("data/landcover")
WORLDCOVER_MAP = (
    "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"
)
WORLDCOVER_ATTRIBUTION = (
    "© ESA WorldCover project 2021 / contains modified Copernicus Sentinel data "
    "(2021) processed by ESA WorldCover consortium"
)
_VERTEX_CHUNK = 50_000
_HIKE_IN_CHUNK = 500
# Europe has 267 tiles; the macOS default soft limit is 256 open files.
_MAX_OPEN_TILES = 128
_SHARE_BLOCK = 1024  # WorldCover cells per side of the blocks ``tree_share`` reads at a time
_METRES_PER_DEGREE = EARTH_RADIUS_M * math.pi / 180
_WORLDCOVER_NODATA = 0
_TREE = 10
_SHRUB = 20
_GRASS = 30
_BARE = 60
_SNOW = 70
_SHARE_CODES = (_TREE, _SHRUB, _GRASS, _BARE, _SNOW)
# A step between two vertices longer than ALONG_MIN_M is also read along its
# length, at the middles of equal pieces at most ALONG_STEP_M long (ADR-0059).
ALONG_MIN_M = 40.0
ALONG_STEP_M = 20.0


def worldcover_tile_url(tile_id: str) -> str:
    """HTTPS URL of one 3×3° WorldCover 2021 v200 Map COG (public bucket)."""
    filename = f"ESA_WorldCover_10m_2021_v200_{tile_id}_Map.tif"
    return f"{WORLDCOVER_MAP}/{filename}"


def worldcover_tile_id(lat: float, lon: float) -> str:
    """Return the WorldCover 3° tile id whose southwest corner contains this point."""
    lat_sw = int(math.floor(lat / 3) * 3)
    lon_sw = int(math.floor(lon / 3) * 3)
    return format_corner(lat_sw, lon_sw)


def worldcover_tiles_covering(bounds: Bounds) -> list[str]:
    """3° WorldCover tiles whose southwest-corner cells intersect the box."""
    lat_start = int(math.floor(bounds.south / 3) * 3)
    lat_end = int(math.floor(bounds.north / 3) * 3)
    lon_start = int(math.floor(bounds.west / 3) * 3)
    lon_end = int(math.floor(bounds.east / 3) * 3)
    return [
        format_corner(lat, lon)
        for lat in range(lat_start, lat_end + 1, 3)
        for lon in range(lon_start, lon_end + 1, 3)
    ]


def fetch_worldcover(
    dest: Path | str,
    bounds: Bounds = DEFAULT_DEM_BOUNDS,
    *,
    retrieve: Retrieve | None = None,
    progress: Progress | None = None,
    max_workers: int = 4,
) -> list[Path]:
    """Download missing WorldCover 2021 v200 Map COGs covering bounds into dest."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    retrieve = retrieve or _download

    def fetch_one(tile_id: str) -> Path | None:
        path = dest / f"ESA_WorldCover_10m_2021_v200_{tile_id}_Map.tif"
        if path.exists() and path.stat().st_size > 0:
            return path
        if progress is not None:
            progress(f"landcover fetch: {tile_id}")
        try:
            retrieve(worldcover_tile_url(tile_id), str(path))
        except FileNotFoundError:
            if path.exists():
                path.unlink()
            return None
        if path.exists() and path.stat().st_size > 0:
            return path
        return None

    tiles = worldcover_tiles_covering(bounds)
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
                    progress(f"landcover fetch error: {exc}")
                failed.append(futures[future])
                continue
            if path is not None:
                paths.append(path)
    if failed:
        raise RuntimeError(
            f"landcover fetch failed for tile(s): {', '.join(sorted(failed))}"
        )
    paths.sort()
    return paths


def sample_landcover_classes(
    raster_dir: Path | str,
    points: Sequence[tuple[float, float]],
) -> list[int | None]:
    """Sample land-cover GeoTIFFs at (lat, lon). Uncovered is None."""
    rasters = _raster_paths(Path(raster_dir))
    if not rasters:
        return [None] * len(points)
    with ExitStack() as stack:
        tiles = _Tiles(rasters)
        stack.callback(tiles.close)
        return _sample_open_datasets(tiles, points)


def tree_share(
    raster_dir: Path | str, lat: np.ndarray, lon: np.ndarray, radius_m: float
) -> np.ndarray:
    """Share of the WorldCover cells within ``radius_m`` north, south, east and
    west of each point (a square) that are tree cover; NaN where no tile
    (named ``ESA_WorldCover_10m_2021_v200_{tile}_Map.tif``) covers the point.
    Near a tile's edge the square is cut at the edge.
    """
    import rasterio
    from rasterio.windows import Window

    lat, lon = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
    out = np.full(len(lat), np.nan)
    tiles: dict[str, list[int]] = {}
    for index, (y, x) in enumerate(zip(lat.tolist(), lon.tolist())):
        tiles.setdefault(worldcover_tile_id(y, x), []).append(index)
    for tile_id, indices in tiles.items():
        path = Path(raster_dir) / f"ESA_WorldCover_10m_2021_v200_{tile_id}_Map.tif"
        if not path.is_file():
            continue
        at = np.array(indices)
        with rasterio.open(path) as dataset:
            rows, cols = (np.asarray(v) for v in rasterio.transform.rowcol(dataset.transform, lon[at], lat[at]))
            inside = (rows >= 0) & (rows < dataset.height) & (cols >= 0) & (cols < dataset.width)
            at, rows, cols = at[inside], rows[inside], cols[inside]
            reach_y = round(radius_m / (dataset.res[1] * _METRES_PER_DEGREE))
            reach_x = np.round(
                radius_m / (dataset.res[0] * _METRES_PER_DEGREE * np.cos(np.radians(lat[at])))
            ).astype(int)
            # One read per block of cells the points fall in.
            blocks = rows // _SHARE_BLOCK * (dataset.width // _SHARE_BLOCK + 1) + cols // _SHARE_BLOCK
            for block in np.unique(blocks):
                here = np.flatnonzero(blocks == block)
                top = max(int(rows[here].min()) - reach_y, 0)
                bottom = min(int(rows[here].max()) + reach_y + 1, dataset.height)
                left = max(int((cols[here] - reach_x[here]).min()), 0)
                right = min(int((cols[here] + reach_x[here]).max()) + 1, dataset.width)
                trees = dataset.read(1, window=Window(left, top, right - left, bottom - top)) == _TREE
                summed = np.zeros((trees.shape[0] + 1, trees.shape[1] + 1))
                summed[1:, 1:] = trees.cumsum(axis=0).cumsum(axis=1)
                y0 = np.clip(rows[here] - reach_y - top, 0, trees.shape[0])
                y1 = np.clip(rows[here] + reach_y + 1 - top, 0, trees.shape[0])
                x0 = np.clip(cols[here] - reach_x[here] - left, 0, trees.shape[1])
                x1 = np.clip(cols[here] + reach_x[here] + 1 - left, 0, trees.shape[1])
                count = summed[y1, x1] - summed[y0, x1] - summed[y1, x0] + summed[y0, x0]
                out[at[here]] = count / ((y1 - y0) * (x1 - x0))
    return out


def _raster_paths(raster_dir: Path) -> list[Path]:
    return sorted(raster_dir.glob("*.tif")) + sorted(raster_dir.glob("*.tiff"))


class _Tiles:
    """Rasters opened on first use, at most ``_MAX_OPEN_TILES`` at a time.

    Each raster is opened once while it stays among the most recently used;
    its bounds and nodata stay known after it is closed, so a chunk with no
    point on it never reopens it.
    """

    def __init__(self, paths: Sequence[Path]) -> None:
        self.paths = list(paths)
        self._extents: dict[int, tuple[object, float | None]] = {}
        self._open: OrderedDict[int, object] = OrderedDict()

    def extent(self, index: int) -> tuple[object, float | None]:
        if index not in self._extents:
            self.dataset(index)
        return self._extents[index]

    def dataset(self, index: int):
        import rasterio

        if index in self._open:
            self._open.move_to_end(index)
            return self._open[index]
        if len(self._open) >= _MAX_OPEN_TILES:
            self._open.popitem(last=False)[1].close()
        dataset = rasterio.open(self.paths[index])
        self._open[index] = dataset
        self._extents[index] = (dataset.bounds, dataset.nodata)
        return dataset

    def close(self) -> None:
        while self._open:
            self._open.popitem()[1].close()


def _sample_open_datasets(
    tiles: _Tiles,
    points: Sequence[tuple[float, float]],
) -> list[int | None]:
    """First-tile-wins sampling over the tiles in sorted file order."""
    from dayhike.dem import read_cells

    out: list[int | None] = [None] * len(points)
    lat, lon = np.array(points, dtype=float).reshape(-1, 2).T
    remaining = np.ones(len(points), dtype=bool)
    for tile in range(len(tiles.paths)):
        if not remaining.any():
            break
        bounds, nodata = tiles.extent(tile)
        indices = np.flatnonzero(
            remaining & (bounds.left <= lon) & (lon <= bounds.right) & (bounds.bottom <= lat) & (lat <= bounds.top)
        )
        if not len(indices):
            continue
        values = read_cells(tiles.dataset(tile), lon[indices], lat[indices]).tolist()
        for index, value in zip(indices.tolist(), values, strict=True):
            if not math.isfinite(value):
                continue
            if nodata is not None and value == nodata:
                continue
            if value == _WORLDCOVER_NODATA:
                continue
            out[index] = int(value)
            remaining[index] = False
    return out


class _Runs:
    """Records spooled to one file in runs, each run sorted by an int64 key, so
    the records of a range of keys are one slice of each run."""

    def __init__(self, path: Path, dtype: np.dtype) -> None:
        self.path, self.dtype = path, dtype
        self._file = open(path, "wb")
        self._starts: list[int] = []  # where each run begins in the file, in records
        self._keys: list[np.ndarray] = []  # the distinct keys of each run
        self._firsts: list[np.ndarray] = []  # the first record of each, then the run's length

    def add(self, records: np.ndarray, keys: np.ndarray) -> None:
        """Spool a run: ``records`` sorted by their ``keys``."""
        if not len(records):
            return
        self._starts.append(self._size())
        records.tofile(self._file)
        distinct, first = np.unique(keys, return_index=True)
        self._keys.append(distinct)
        self._firsts.append(np.append(first, len(records)))

    def _size(self) -> int:
        return self._starts[-1] + int(self._firsts[-1][-1]) if self._starts else 0

    def parts(self, limit: int) -> list[tuple[int, int]]:
        """Ranges [low, high) of keys in order, each with at most ``limit``
        records unless one key alone has more."""
        if not self._keys:
            return []
        keys, at = np.unique(np.concatenate(self._keys), return_inverse=True)
        counts = np.bincount(at, weights=np.concatenate([np.diff(first) for first in self._firsts]))
        parts, low, size = [], int(keys[0]), 0
        for key, count in zip(keys.tolist(), counts.astype(np.int64).tolist()):
            if size and size + count > limit:
                parts.append((low, key))
                low, size = key, 0
            size += count
        parts.append((low, int(keys[-1]) + 1))
        return parts

    def read(self, low: int, high: int) -> np.ndarray:
        """The records of keys in [low, high), run by run."""
        self._file.flush()
        slices = []
        with open(self.path, "rb") as spooled:
            for start, keys, first in zip(self._starts, self._keys, self._firsts, strict=True):
                begin, end = first[np.searchsorted(keys, [low, high])].tolist()
                spooled.seek((start + begin) * self.dtype.itemsize)
                slices.append(np.fromfile(spooled, self.dtype, count=end - begin))
        return np.concatenate(slices)

    def close(self) -> None:
        self._file.close()
        self.path.unlink(missing_ok=True)


# A vertex as read; the step from it to the next vertex of its hike; what a
# vertex whose step has a class or reads takes: its class (``_NO_CLASS`` for
# none), and where its step's reads along are in the spooled reads.
_VERTEX = np.dtype([("rowid", "<i8"), ("hike", "<i8"), ("lat", "<f8"), ("lon", "<f8")])
_STEP = np.dtype([("rowid", "<i8"), ("hike", "<i8"), ("lat", "<f8"), ("lon", "<f8"), ("nlat", "<f8"), ("nlon", "<f8")])
_UPDATE = np.dtype([("rowid", "<i8"), ("hike", "<i8"), ("code", "<i8"), ("at", "<i8"), ("size", "<i8")])
_NO_CLASS = np.iinfo(np.int64).min
_SPOOL_ROWS = 1_000_000  # vertices read, and spooled as one run, at a time
_PART_ROWS = 4_000_000  # steps (or updates) held in memory at a time, unless one 1/7° block has more


def _spool_steps(store_path: Path, steps: _Runs) -> None:
    """Spool each vertex's step to the next vertex of its hike (the last
    vertex's step is to itself), in runs sorted by ``raster_block``."""
    read = sqlite3.connect(store_path)
    try:
        cursor = read.execute("SELECT rowid, dayhike_id, latitude, longitude FROM vertices ORDER BY dayhike_id, seq")
        held = np.zeros(0, _VERTEX)  # the last vertex read: its next one is not read yet
        while True:
            rows = cursor.fetchmany(_SPOOL_ROWS)
            vertices = np.concatenate([held, np.fromiter(rows, _VERTEX, len(rows))])
            if not len(vertices):
                return
            # After the last vertex read comes only itself.
            following = np.concatenate([vertices[1:], vertices[-1:]])
            if rows:
                vertices, following, held = vertices[:-1], following[:-1], vertices[-1:]
            same = following["hike"] == vertices["hike"]
            run = np.empty(len(vertices), _STEP)
            for name in ("rowid", "hike", "lat", "lon"):
                run[name] = vertices[name]
            run["nlat"] = np.where(same, following["lat"], vertices["lat"])
            run["nlon"] = np.where(same, following["lon"], vertices["lon"])
            keys = raster_block(run["lat"], run["lon"])
            order = np.argsort(keys, kind="stable")
            steps.add(run[order], keys[order])
            if not rows:
                return
    finally:
        read.close()


def _distinct_steps(spooled: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The distinct (lat, lon, next lat, next lon) of spooled steps in raster
    order, then by the next vertex, and the index of each spooled step's among them."""
    columns = [spooled[name] for name in ("lat", "lon", "nlat", "nlon")]
    order = np.lexsort([*reversed(columns), raster_block(columns[0], columns[1])])
    columns = [column[order] for column in columns]
    new = np.zeros(len(order), dtype=bool)
    new[:1] = True
    for column in columns:
        new[1:] |= column[1:] != column[:-1]
    of = np.empty(len(order), dtype=np.int64)
    of[order] = np.cumsum(new) - 1
    return np.column_stack([column[new] for column in columns]), of


def sample_landcover(
    catalog_path: Path | str,
    raster_dir: Path | str,
    *,
    progress: Progress | None = None,
) -> int:
    """Write land-cover classes onto covered vertices, the classes read along
    each step over ``ALONG_MIN_M`` onto its first vertex, and Catalog fractions."""
    catalog_path = Path(catalog_path)
    raster_dir = Path(raster_dir)
    workers()  # a bad DAYHIKE_WORKERS fails here, before the GeometryStore is open
    store_path = geometry_store_path_for(catalog_path)
    if not store_path.is_file():
        raise FileNotFoundError(store_path)
    rasters = _raster_paths(raster_dir)
    if not rasters:
        return 0
    if progress is not None:
        progress("landcover sample: vertices")
    sampled = 0
    touched: set[int] = set()
    stack = ExitStack()
    write = writer(store_path)
    try:
        write.execute("PRAGMA journal_mode=WAL")
        ensure_column(write, "vertices", "landcover", "INTEGER")
        ensure_column(write, "vertices", "landcover_along", "BLOB")
        tiles = _Tiles(rasters)
        stack.callback(tiles.close)
        # Hikes share vertices and steps, so each distinct step (a vertex and the
        # next one, the last vertex of a hike being a step to itself) is sampled
        # once, in raster order; the vertices then take their step's. The steps
        # are sorted and made distinct in numpy, a part of the raster at a time,
        # from runs spooled to disk, so memory stays bounded at any store size.
        spool = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="dayhike-landcover-")))
        steps = _Runs(spool / "steps", _STEP)
        stack.callback(steps.close)
        updates = _Runs(spool / "updates", _UPDATE)
        stack.callback(updates.close)
        along = stack.enter_context(open(spool / "along", "wb"))
        _spool_steps(store_path, steps)
        # Each process reads through rasters it opened itself, never another's.
        opened = {os.getpid(): tiles}

        def chunk_classes(step: np.ndarray) -> tuple[np.ndarray, np.ndarray, bytes]:
            # A chunk's class of each step's first vertex (``_NO_CLASS`` for none),
            # the length of its reads along (0 for none) and those reads, in a worker.
            if os.getpid() not in opened:
                opened[os.getpid()] = _Tiles(rasters)
            # The last vertex of a hike has no next one: a step of no length, not read.
            counts, points = step_reads(*step.T)
            wanted = [*zip(step[:, 0].tolist(), step[:, 1].tolist()), *map(tuple, points.tolist())]
            # A point that begins several steps, or lies along several, is read once.
            distinct = list(dict.fromkeys(wanted))
            found = dict(zip(distinct, _sample_open_datasets(opened[os.getpid()], distinct), strict=True))
            classes = [found[point] for point in wanted]
            vertex, reads = classes[: len(step)], classes[len(step) :]
            ends = np.cumsum(counts).tolist()
            blobs = []
            for count, end in zip(counts.tolist(), ends, strict=True):
                read_along = reads[end - count : end]
                blobs.append(bytes(c or 0 for c in read_along) if any(c is not None for c in read_along) else b"")
            codes = np.array([_NO_CLASS if code is None else code for code in vertex], dtype=np.int64)
            return codes, np.array([len(blob) for blob in blobs], dtype=np.int64), b"".join(blobs)

        parts: deque[tuple[np.ndarray, np.ndarray, int]] = deque()  # spooled steps not yet sampled whole

        def chunks() -> Iterator[np.ndarray]:
            for low, high in steps.parts(_PART_ROWS):
                spooled = steps.read(low, high)
                distinct, of = _distinct_steps(spooled)
                parts.append((spooled[["rowid", "hike"]], of, len(distinct)))
                for start in range(0, len(distinct), _VERTEX_CHUNK):
                    yield distinct[start : start + _VERTEX_CHUNK]

        sampled_steps, in_part, written = 0, 0, 0
        results: list[tuple[np.ndarray, np.ndarray, bytes]] = []
        for result in parallel_iter(chunk_classes, chunks()):
            results.append(result)
            sampled_steps += len(result[0])
            in_part += len(result[0])
            if progress is not None:
                progress(f"landcover sample: {sampled_steps} steps")
            spooled, of, count = parts[0]
            if in_part < count:
                continue
            # The part is sampled whole: spool what each of its vertices takes.
            parts.popleft()
            in_part = 0
            codes = np.concatenate([codes for codes, _sizes, _reads in results])
            sizes = np.concatenate([sizes for _codes, sizes, _reads in results])
            reads = b"".join(reads for _codes, _sizes, reads in results)
            results = []
            along.write(reads)
            at = written + np.cumsum(sizes) - sizes
            written += len(reads)
            code, at, size = codes[of], at[of], sizes[of]
            take = (code != _NO_CLASS) | (size > 0)
            update = np.empty(int(take.sum()), _UPDATE)
            update["rowid"], update["hike"] = spooled["rowid"][take], spooled["hike"][take]
            update["code"], update["at"], update["size"] = code[take], at[take], size[take]
            update = update[np.argsort(update["rowid"])]
            updates.add(update, update["rowid"] // _PART_ROWS)
        steps.close()
        along.flush()
        reads = np.memmap(spool / "along", dtype="u1", mode="r") if written else np.zeros(0, dtype="u1")
        # The vertices in table order, so the writes go through it once.
        for low, high in updates.parts(_PART_ROWS):
            update = updates.read(low, high)
            update = update[np.argsort(update["rowid"])]
            write.executemany(
                "UPDATE vertices SET landcover = COALESCE(?, landcover), landcover_along = ? WHERE rowid = ?",
                (
                    (
                        None if code == _NO_CLASS else code,
                        reads[at : at + size].tobytes() if size else None,
                        rowid,
                    )
                    for rowid, code, at, size in zip(
                        update["rowid"].tolist(), update["code"].tolist(), update["at"].tolist(), update["size"].tolist()
                    )
                ),
            )
            sampled += int((update["code"] != _NO_CLASS).sum())
            touched.update(np.unique(update["hike"]).tolist())
        if progress is not None:
            progress(f"landcover sample: {sampled} vertices")
        if sampled:
            write.execute(
                "INSERT OR REPLACE INTO geometry_meta (key, value) VALUES (?, ?)",
                ("worldcover_attribution", WORLDCOVER_ATTRIBUTION),
            )
        write.commit()
    finally:
        if write.in_transaction:
            write.rollback()
        write.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        write.execute("PRAGMA journal_mode=DELETE")
        stack.close()
        write.close()
    if not touched:
        return 0
    if progress is not None:
        progress("landcover sample: feature_vectors")
    _refresh_catalog_fractions(catalog_path, store_path, touched)
    remove_similarity(catalog_path, progress)
    return sampled


def _refresh_catalog_fractions(
    catalog_path: Path, store_path: Path, hike_ids: set[int]
) -> None:
    store = sqlite3.connect(store_path)
    catalog = writer(catalog_path)
    try:
        for name in LANDCOVER_FEATURE_NAMES:
            ensure_column(catalog, "feature_vectors", name, "REAL")
        ids = sorted(hike_ids)
        updates = []
        # A chunk of hikes at a time: their vertices are never all in memory.
        for start in range(0, len(ids), _HIKE_IN_CHUNK):
            chunk = ids[start : start + _HIKE_IN_CHUNK]
            by_hike: dict[int, list[tuple[int, float, float, bytes | None]]] = {dayhike_id: [] for dayhike_id in chunk}
            placeholders = ",".join("?" * len(chunk))
            for dayhike_id, code, lat, lon, along in store.execute(
                "SELECT dayhike_id, landcover, latitude, longitude, landcover_along FROM vertices "
                f"WHERE dayhike_id IN ({placeholders}) ORDER BY dayhike_id, seq",
                chunk,
            ):
                by_hike[int(dayhike_id)].append((code or 0, lat, lon, along))
            for dayhike_id, rows in by_hike.items():
                codes, lat, lon = np.array([row[:3] for row in rows], dtype=float).reshape(-1, 3).T
                if np.any(codes > 0):
                    along = along_rows_of_vertices([row[3] for row in rows])
                    updates.append((*_landcover_fractions(codes, lat, lon, along), dayhike_id))
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


def _landcover_fractions(
    codes: np.ndarray, lat: np.ndarray, lon: np.ndarray, along: np.ndarray | None = None
) -> tuple[float, float, float, float, float]:
    """Tree, shrub, grass, bare and snow shares of a line by length.

    ``codes`` is the WorldCover class of each vertex of the line (0 where it
    has none) and ``along`` the classes read along its steps (``along_rows``;
    None when none were read). A step read along is shared by its reads, each
    standing for an equal piece of it; every other step goes half to each of
    its two vertices. Vertices and reads without a class are left out, and
    the others share the length; a step whose reads all lack a class goes to
    its vertices. Needs at least one classified vertex.

    The vertex weights are summed exactly (``math.fsum``), so a class's
    weights never add up to more than all of them and no share is above 1.
    """
    codes = np.asarray(codes)
    steps = step_lengths_m(np.asarray(lat, dtype=float), np.asarray(lon, dtype=float))
    metres = np.zeros(len(_SHARE_CODES) + 1)
    read = np.zeros(len(steps), dtype=bool)
    if along is not None:
        counts = np.asarray(along, dtype=float)[:-1]
        read = counts[:, :-1].sum(axis=1) > 0
        metres = (steps[read, None] * counts[read, :-1] / counts[read, -1:]).sum(axis=0)
    halves = np.where(read, 0.0, steps / 2)
    weights = np.zeros(len(codes))
    weights[:-1] += halves
    weights[1:] += halves
    classified = codes > 0
    codes, weights = codes[classified], weights[classified]
    total = math.fsum(weights) + metres.sum()
    if total <= 0:  # every classified vertex on one spot
        weights, total = np.ones(len(codes)), float(len(codes))
    return tuple(
        float((math.fsum(weights[codes == code]) + metres[index]) / total) for index, code in enumerate(_SHARE_CODES)
    )


def step_lengths_m(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Haversine metres of each step between consecutive vertices."""
    return _haversine_m(lat[:-1], lon[:-1], lat[1:], lon[1:])


def _haversine_m(lat0: np.ndarray, lon0: np.ndarray, lat1: np.ndarray, lon1: np.ndarray) -> np.ndarray:
    phi0, phi1 = np.radians(lat0), np.radians(lat1)
    a = np.sin((phi1 - phi0) / 2) ** 2 + np.cos(phi0) * np.cos(phi1) * np.sin(np.radians(lon1 - lon0) / 2) ** 2
    return EARTH_RADIUS_M * 2 * np.arcsin(np.sqrt(a))


def along_points(lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Where a line is read along its steps (``step_reads`` of each step
    between consecutive vertices)."""
    lat, lon = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
    return step_reads(lat[:-1], lon[:-1], lat[1:], lon[1:])


def step_reads(
    lat0: np.ndarray, lon0: np.ndarray, lat1: np.ndarray, lon1: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """The number of reads on each step from (``lat0``, ``lon0``) to
    (``lat1``, ``lon1``), 0 on a step of at most ``ALONG_MIN_M``, and the
    (lat, lon) of the reads, step by step: the middles of equal pieces at
    most ``ALONG_STEP_M`` long."""
    lat0, lon0, lat1, lon1 = (np.asarray(v, dtype=float) for v in (lat0, lon0, lat1, lon1))
    steps = _haversine_m(lat0, lon0, lat1, lon1)
    counts = np.where(steps > ALONG_MIN_M, np.ceil(steps / ALONG_STEP_M), 0).astype(np.int64)
    step = np.repeat(np.arange(len(counts)), counts)
    within = np.arange(len(step)) - np.repeat(np.cumsum(counts) - counts, counts)
    t = (within + 0.5) / counts[step] if len(step) else np.zeros(0)
    points = np.column_stack([lat0[step] + t * (lat1[step] - lat0[step]), lon0[step] + t * (lon1[step] - lon0[step])])
    return counts, points.reshape(-1, 2)


def along_rows(counts: np.ndarray, codes: np.ndarray) -> np.ndarray:
    """The classes read along a line's steps as one row per vertex: the reads
    on the step to the next vertex that are tree, shrub, grass, bare, snow and
    any other class, then all its reads (classed or not). The last vertex's
    row is zero. ``counts`` is the number of reads per step and ``codes``
    their classes, step by step (0 where none)."""
    counts, codes = np.asarray(counts, dtype=np.int64), np.asarray(codes, dtype=np.int64)
    rows = np.zeros((len(counts) + 1, len(_SHARE_CODES) + 2), dtype=np.int64)
    rows[:-1, -1] = counts
    step = np.repeat(np.arange(len(counts)), counts)
    for column, code in enumerate(_SHARE_CODES):
        np.add.at(rows[:, column], step[codes == code], 1)
    other = (codes > 0) & ~np.isin(codes, _SHARE_CODES)
    np.add.at(rows[:, len(_SHARE_CODES)], step[other], 1)
    return rows


def along_rows_of_vertices(blobs: Sequence[bytes | None]) -> np.ndarray:
    """``along_rows`` from the GeometryStore's ``landcover_along`` of each
    vertex of a line (the classes read on the step after it, or None)."""
    reads = [np.frombuffer(blob, dtype="u1") if blob else np.zeros(0, dtype="u1") for blob in blobs[:-1]]
    codes = np.concatenate(reads) if reads else np.zeros(0, dtype="u1")
    return along_rows(np.array([len(r) for r in reads], dtype=np.int64), codes)
