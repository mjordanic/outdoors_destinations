from __future__ import annotations

import math
import re
import shutil
import sqlite3
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np

from dayhike.catalog import geometry_store_path_for, remove_similarity

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


@dataclass(frozen=True)
class DemSource:
    """An elevation map in 1° GeoTIFF tiles, each named by its southwest corner.

    FABDEM is the default: bare earth, forests and buildings removed (ADR-0008).
    Copernicus GLO-30 is the alternative: a surface model, tree tops and roofs
    included, free for commercial use (ADR-0027).
    """

    name: str
    label: str
    file_format: str  # one tile's file name, from {ns}{lat}{ew}{lon} of its corner
    url_format: str  # its HTTPS URL, from {stem} (file name without .tif) and {archive}
    excluded: frozenset[str] = frozenset()  # land tiles the source does not publish

    def tile_file(self, tile_id: str) -> str:
        lat, lon = _parse_tile(tile_id)
        return self.file_format.format(
            ns="N" if lat >= 0 else "S", lat=abs(lat), ew="E" if lon >= 0 else "W", lon=abs(lon)
        )

    def tile_url(self, tile_id: str) -> str:
        file = self.tile_file(tile_id)
        return self.url_format.format(
            stem=file.removesuffix(".tif"), archive=fabdem_archive_id(tile_id), file=file
        )

    def tiles_in(self, raster_dir: Path) -> dict[str, Path]:
        """This source's tiles in raster_dir, by tile id."""
        return {_tile_of_file(path.name): path for path in raster_dir.glob(self._pattern())}

    def has_tiles_in(self, raster_dir: Path) -> bool:
        return next(raster_dir.glob(self._pattern()), None) is not None

    def _pattern(self) -> str:
        return re.sub(r"\{[^}]*\}", "*", self.file_format)


FABDEM = DemSource(
    "fabdem",
    "FABDEM",
    "{ns}{lat:02d}{ew}{lon:03d}_FABDEM_" + FABDEM_VERSION + ".tif",
    FABDEM_DATASET + "/{archive}/{file}",
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
)


DEM_SOURCES = {source.name: source for source in (FABDEM, COPERNICUS)}


def dem_source_of(raster_dir: Path | str) -> DemSource | None:
    """The source whose tiles raster_dir holds; None when it holds none.

    One directory holds one source, so a DayHike is never measured on two maps.
    """
    raster_dir = Path(raster_dir)
    found = [source for source in DEM_SOURCES.values() if source.has_tiles_in(raster_dir)]
    if len(found) > 1:
        labels = " and ".join(source.label for source in found)
        raise ValueError(f"{raster_dir} holds tiles of {labels}; keep one elevation source per directory")
    return found[0] if found else None


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


def sample_elevations(
    raster_dir: Path | str,
    points: Sequence[tuple[float, float]],
) -> list[float | None]:
    """Sample the DEM tiles in raster_dir at (lat, lon) points. Uncovered is None."""
    import rasterio

    raster_dir = Path(raster_dir)
    out: list[float | None] = [None] * len(points)
    source = dem_source_of(raster_dir)
    if source is None:
        return out
    buckets: dict[str, list[int]] = {}
    for index, (lat, lon) in enumerate(points):
        buckets.setdefault(fabdem_tile_id(lat, lon), []).append(index)
    for tile_id, indices in buckets.items():
        path = raster_dir / source.tile_file(tile_id)
        if not path.is_file():
            continue
        coords = [(points[index][1], points[index][0]) for index in indices]
        with rasterio.open(path) as dataset:
            nodata = dataset.nodata
            box = dataset.bounds
            for index, (lon, lat), sample in zip(indices, coords, dataset.sample(coords), strict=True):
                # A GLO-30 grid stops half a cell short of its tile's east and south
                # edges, and outside it a read gives 0, not nodata.
                if not (box.left <= lon < box.right and box.bottom < lat <= box.top):
                    continue
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
    """Write DEM elevations onto covered GeometryStore vertices and Catalog stats.

    The tiles in raster_dir name the source. Copernicus GLO-30 records its
    attribution in the GeometryStore; FABDEM, the default, removes it.
    """
    catalog_path = Path(catalog_path)
    raster_dir = Path(raster_dir)
    store_path = geometry_store_path_for(catalog_path)
    if not store_path.is_file():
        raise FileNotFoundError(store_path)
    source = dem_source_of(raster_dir)
    if source is None:
        return 0
    available = set(source.tiles_in(raster_dir))
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
            if source is COPERNICUS:
                write.execute(
                    "INSERT OR REPLACE INTO geometry_meta (key, value) VALUES ('copernicus_attribution', ?)",
                    (COPERNICUS_ATTRIBUTION,),
                )
            else:
                write.execute("DELETE FROM geometry_meta WHERE key = 'copernicus_attribution'")
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
        progress("dem sample: feature_vectors")
    _refresh_catalog_elevations(catalog_path, store_path, touched)
    remove_similarity(catalog_path, progress)
    return sampled


def _refresh_catalog_elevations(
    catalog_path: Path, store_path: Path, hike_ids: set[int]
) -> None:
    store = sqlite3.connect(store_path)
    catalog = sqlite3.connect(catalog_path)
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


def _format_corner(lat_sw: int, lon_sw: int) -> str:
    ns = "N" if lat_sw >= 0 else "S"
    ew = "E" if lon_sw >= 0 else "W"
    return f"{ns}{abs(lat_sw):02d}{ew}{abs(lon_sw):03d}"


def _tile_of_file(name: str) -> str:
    match = re.search(r"([NS]\d{2})(?:_00_)?([EW]\d{3})", name)
    assert match is not None, name
    return match[1] + match[2]


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
