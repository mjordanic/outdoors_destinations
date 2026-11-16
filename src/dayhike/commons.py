"""Commons contributors: how many people put geotagged photos on Wikimedia
Commons within 150 m of a DayHike's line (#170).

The count is shown beside a hike and never scored: it changes no score, Twin
or Ranking. It is read from three Commons database dumps, not the throttled
API: ``geo_tags`` (where each file was taken), ``page`` (the file name of each
page id) and ``image`` (who uploaded each file). Uploaders are told apart only
by their account number, in memory, while counting; only the count per hike is
written anywhere.
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
from array import array
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from dayhike.catalog import EARTH_RADIUS_M, geometry_store_path_for
from dayhike.dem import Bounds
from dayhike.download import download

Progress = Callable[[str], None]

RADIUS_M = 150
# Line samples this far apart, at most; a sample's search circle is wider by half of it,
# so it finds every point within RADIUS_M of the line between samples.
_SAMPLE_M = 100
_HIKE_CHUNK = 2000  # DayHikes' lines held in memory at a time


@dataclass(frozen=True)
class CommonsPoints:
    """Where geotagged Commons files were taken, and an opaque number per uploader."""

    lat: np.ndarray
    lon: np.ndarray
    uploader: np.ndarray


DEFAULT_COMMONS_DIR = Path("data/commons")
DUMPS_URL = "https://dumps.wikimedia.org/commonswiki/latest/"
# The tables read, about 0.8, 18.8 and 7.4 GB gzipped in November 2026.
TABLES = ("geo_tags", "image", "page")


def dump_path(dumps_dir: Path | str, table: str) -> Path:
    return Path(dumps_dir) / f"commonswiki-latest-{table}.sql.gz"


def fetch_commons(dest: Path | str = DEFAULT_COMMONS_DIR, progress: Progress | None = None) -> list[Path]:
    """Download the Commons dumps ``count_commons`` reads into ``dest``,
    skipping any already there; return their paths."""
    Path(dest).mkdir(parents=True, exist_ok=True)
    paths = []
    for table in TABLES:
        path = dump_path(dest, table)
        if not path.is_file() or path.stat().st_size == 0:
            if progress is not None:
                progress(f"commons: downloading {path.name}")
            download(DUMPS_URL + path.name, str(path))
        paths.append(path)
    return paths


# A dump row is one line: "(values),". The patterns read the first columns only.
# geo_tags: gt_id, gt_page_id, gt_globe, gt_primary, gt_lat, gt_lon, ...; primary is the photo spot.
_GEO_TAG = re.compile(rb"\(\d+,(\d+),'earth',1,(-?\d+\.\d+),(-?\d+\.\d+),")
# page: page_id, page_namespace (6 is File:), page_title, ...
_FILE_PAGE = re.compile(rb"\((\d+),6,'((?:[^'\\]|\\.)*)',")
# image: img_name, ...; its last three columns are img_actor, img_timestamp, img_sha1.
_IMAGE = re.compile(rb"\('((?:[^'\\]|\\.)*)',")


def _rows(path: Path) -> Iterator[bytes]:
    """The row lines of a gzipped SQL dump, as written (names stay SQL-escaped)."""
    with subprocess.Popen(["gzip", "-dc", str(path)], stdout=subprocess.PIPE, bufsize=1 << 20) as gunzip:
        assert gunzip.stdout is not None
        for line in gunzip.stdout:
            if line.startswith(b"("):
                yield line
    if gunzip.returncode:
        raise OSError(f"gzip -dc {path} failed with exit code {gunzip.returncode}")


def _precise(coordinate: bytes) -> bool:
    """More than three decimals: a photo spot, not a place's round coordinate."""
    return len(coordinate.partition(b".")[2].rstrip(b"0")) > 3


def commons_points(dumps_dir: Path | str, bounds: Bounds, progress: Progress | None = None) -> CommonsPoints:
    """The photo spot and uploader of every geotagged Commons file inside
    ``bounds``, read from the ``geo_tags``, ``page`` and ``image`` dumps in
    ``dumps_dir``. A spot is the file's primary coordinate on Earth with more
    than three decimals in both latitude and longitude. The uploader is the
    account number of the file's current version, kept in memory only.

    Europe holds about 25 million spots, so the joins run on sorted numpy
    arrays, looked up a chunk of dump rows at a time, not on dicts."""
    pages, lats, lons = array("q"), array("d"), array("d")
    for line in _rows(dump_path(dumps_dir, "geo_tags")):
        match = _GEO_TAG.match(line)
        if match is None or not (_precise(match[2]) and _precise(match[3])):
            continue
        lat, lon = float(match[2]), float(match[3])
        if bounds.south <= lat <= bounds.north and bounds.west <= lon <= bounds.east:
            pages.append(int(match[1]))
            lats.append(lat)
            lons.append(lon)
    # One spot per page, its first; sorted by page id.
    page_ids, first = np.unique(np.frombuffer(pages, dtype=np.int64), return_index=True)
    lat, lon = np.frombuffer(lats)[first], np.frombuffer(lons)[first]
    del pages, lats, lons
    if progress is not None:
        progress(f"commons: {len(page_ids)} geotagged pages in the bounds")
    # The page dump names each spot's file: (hash of its SQL-escaped name, spot index).
    names, spots = array("q"), array("q")
    for ids, hashes in _chunks(_rows(dump_path(dumps_dir, "page")), _file_page):
        found = _find(page_ids, ids)
        names.extend(hashes[found >= 0])
        spots.extend(found[found >= 0])
    order = np.argsort(np.frombuffer(names, dtype=np.int64))
    names_sorted = np.frombuffer(names, dtype=np.int64)[order]
    spots_by_name = np.frombuffer(spots, dtype=np.int64)[order]
    del names, spots
    uploader = np.full(len(page_ids), -1, dtype=np.int64)
    for hashes, actors in _chunks(_rows(dump_path(dumps_dir, "image")), _image):
        found = _find(names_sorted, hashes)
        uploader[spots_by_name[found[found >= 0]]] = actors[found >= 0]
    known = uploader >= 0
    if progress is not None:
        progress(f"commons: {int(known.sum())} files with an uploader")
    return CommonsPoints(lat[known], lon[known], uploader[known])


def _file_page(line: bytes) -> tuple[int, int] | None:
    match = _FILE_PAGE.match(line)
    return None if match is None else (int(match[1]), hash(match[2]))


def _image(line: bytes) -> tuple[int, int] | None:
    match = _IMAGE.match(line)
    return None if match is None else (hash(match[1]), int(line.rstrip(b",;\r\n").rsplit(b",", 3)[1]))


def _chunks(
    rows: Iterator[bytes], parse: Callable[[bytes], tuple[int, int] | None], size: int = 1_000_000
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """The two numbers ``parse`` reads from each row, as two arrays of up to ``size`` rows."""
    firsts, seconds = array("q"), array("q")
    for line in rows:
        pair = parse(line)
        if pair is not None:
            firsts.append(pair[0])
            seconds.append(pair[1])
            if len(firsts) == size:
                yield np.array(firsts, dtype=np.int64), np.array(seconds, dtype=np.int64)
                firsts, seconds = array("q"), array("q")
    if firsts:
        yield np.array(firsts, dtype=np.int64), np.array(seconds, dtype=np.int64)


def _find(sorted_keys: np.ndarray, keys: np.ndarray) -> np.ndarray:
    """Each key's index in ``sorted_keys``, or -1 where it is not there."""
    if len(sorted_keys) == 0:
        return np.full(len(keys), -1, dtype=np.int64)
    at = np.searchsorted(sorted_keys, keys).clip(max=len(sorted_keys) - 1)
    return np.where(sorted_keys[at] == keys, at, -1)


def count_commons(catalog_path: Path | str, dumps_dir: Path | str, progress: Progress | None = None) -> int:
    """Write each DayHike's Commons contributors into the Catalog's
    ``dayhikes.commons_contributors``; return how many DayHikes were counted.
    Only the counts are written: no uploader leaves memory."""
    catalog_path = Path(catalog_path)
    geo = sqlite3.connect(f"file:{geometry_store_path_for(catalog_path)}?mode=ro", uri=True)
    try:
        south, north, west, east = geo.execute(
            "SELECT MIN(latitude), MAX(latitude), MIN(longitude), MAX(longitude) FROM vertices"
        ).fetchone()
        margin = 0.01  # degrees: more than 150 m at any latitude hikes reach
        points = commons_points(
            dumps_dir, Bounds(south - margin, west - margin, north + margin, east + margin), progress
        )
        tree = _tree(points)
        counts: dict[int, int] = {}
        rows = geo.execute("SELECT dayhike_id, latitude, longitude FROM vertices ORDER BY dayhike_id, seq")
        walks: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        for dayhike_id, vertices in groupby(rows, key=lambda row: row[0]):
            coords = np.array([(lat, lon) for _, lat, lon in vertices], dtype=np.float64)
            walks[dayhike_id] = (coords[:, 0], coords[:, 1])
            if len(walks) == _HIKE_CHUNK:
                counts |= contributor_counts(walks, points, tree=tree)
                walks = {}
                if progress is not None:
                    progress(f"commons: {len(counts)} DayHikes counted")
        counts |= contributor_counts(walks, points, tree=tree)
    finally:
        geo.close()
    catalog = sqlite3.connect(catalog_path)
    try:
        if "commons_contributors" not in {row[1] for row in catalog.execute("PRAGMA table_info(dayhikes)")}:
            catalog.execute("ALTER TABLE dayhikes ADD COLUMN commons_contributors INTEGER")
        catalog.executemany(
            "UPDATE dayhikes SET commons_contributors = ? WHERE id = ?",
            [(count, dayhike_id) for dayhike_id, count in counts.items()],
        )
        catalog.commit()
    finally:
        catalog.close()
    return len(counts)


def _unit_xyz(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    phi, lam = np.radians(lat), np.radians(lon)
    return np.column_stack((np.cos(phi) * np.cos(lam), np.cos(phi) * np.sin(lam), np.sin(phi)))


def _tree(points: CommonsPoints) -> cKDTree | None:
    return cKDTree(_unit_xyz(points.lat, points.lon)) if len(points.lat) else None


def contributor_counts(
    walks: Mapping[int, tuple[np.ndarray, np.ndarray]],
    points: CommonsPoints,
    radius_m: float = RADIUS_M,
    tree: cKDTree | None = None,
) -> dict[int, int]:
    """For each DayHike id's line (latitudes, longitudes), the number of
    distinct uploaders with a point within ``radius_m`` of it. ``tree`` is
    ``points``' spatial index when the caller already built it."""
    if tree is None:
        tree = _tree(points)
    search = (radius_m + _SAMPLE_M / 2) / EARTH_RADIUS_M
    counts = {}
    for dayhike_id, (lat, lon) in walks.items():
        lat, lon = np.asarray(lat, dtype=np.float64), np.asarray(lon, dtype=np.float64)
        if tree is None or len(lat) == 0:
            counts[dayhike_id] = 0
            continue
        # A local flat frame in metres around the line: plenty exact at 150 m.
        x_scale = np.radians(1) * EARTH_RADIUS_M * np.cos(np.radians(lat.mean()))
        y_scale = np.radians(1) * EARTH_RADIUS_M
        x, y = (lon - lon[0]) * x_scale, (lat - lat[0]) * y_scale
        sample_x, sample_y = _densify(x, y, _SAMPLE_M)
        near = tree.query_ball_point(
            _unit_xyz(lat[0] + sample_y / y_scale, lon[0] + sample_x / x_scale), search, return_sorted=False
        )
        candidates = np.unique(np.concatenate([np.asarray(n, dtype=np.int64) for n in near]))
        if len(candidates):
            px = (points.lon[candidates] - lon[0]) * x_scale
            py = (points.lat[candidates] - lat[0]) * y_scale
            within = _distance_to_line(px, py, x, y) <= radius_m
            counts[dayhike_id] = len(np.unique(points.uploader[candidates[within]]))
        else:
            counts[dayhike_id] = 0
    return counts


def _densify(x: np.ndarray, y: np.ndarray, step_m: float) -> tuple[np.ndarray, np.ndarray]:
    """The line's vertices plus points on each segment no more than ``step_m`` apart."""
    xs, ys = [x[:1]], [y[:1]]
    for i in range(1, len(x)):
        pieces = max(1, int(np.ceil(np.hypot(x[i] - x[i - 1], y[i] - y[i - 1]) / step_m)))
        t = np.arange(1, pieces + 1) / pieces
        xs.append(x[i - 1] + t * (x[i] - x[i - 1]))
        ys.append(y[i - 1] + t * (y[i] - y[i - 1]))
    return np.concatenate(xs), np.concatenate(ys)


def _distance_to_line(px: np.ndarray, py: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Each point's distance in metres to the nearest segment (or the vertex, for one vertex)."""
    if len(x) == 1:
        return np.hypot(px - x[0], py - y[0])
    best = np.full(len(px), np.inf)
    ax, ay, dx, dy = x[:-1], y[:-1], np.diff(x), np.diff(y)
    length2 = dx * dx + dy * dy
    for start in range(0, len(px), 512):
        qx, qy = px[start : start + 512, None], py[start : start + 512, None]
        t = np.clip(((qx - ax) * dx + (qy - ay) * dy) / np.where(length2 > 0, length2, 1), 0, 1)
        best[start : start + 512] = np.hypot(qx - ax - t * dx, qy - ay - t * dy).min(axis=1)
    return best
