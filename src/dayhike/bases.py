"""Bases: towns a Hiker stays in, described by the HikeTypes in Reach (ADR-0084)."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sqlite3
import warnings
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, fields
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree
from sklearn.cluster import KMeans

from dayhike.catalog import EARTH_RADIUS_M
from dayhike.highways import HighwaySnaps
from dayhike.npy import map_npy
from dayhike.reach import APPROACH_SPEED_KMH, BAND_STEP_MIN, REACH_MIN, STRAIGHT_SPEED_KMH, reach_minutes
from dayhike.similarity import (
    _CALIBRATION_VERSION,
    _FACTS_VERSION,
    CORE_MEASURES,
    MEASURES,
    SimilarityStore,
    _measure_weights,
    _measured,
    score_rows,
)
from dayhike.steps import file_key

HIKE_TYPES = 512
HIKE_TYPES_SEED = 20261124
_SPACE_PAIRS = 100_000  # random pairs that set each Measure's unit in the HikeType space

REACHES = (15, 30, REACH_MIN)  # minutes; the stored pairs are in one band per Reach, the shortest first
BASE_POPULATION = 300  # a candidate Base is a GeoNames place of at least this many people
MIN_HIKE_GROUPS = 10  # a Base has at least this many HikeGroups in the longest Reach
CELL_M = 1_000  # a HikeGroup's Trailhead cell
NEAR_M = 120_000  # Trailheads farther in a straight line are not asked about: an hour on a motorway
RESUME_CHUNK = 1_000  # candidates whose Reach is kept together in the work folder
_BASES_LAYOUT = "groups-band-major-runs-v1"
BASES_FORMAT = 1  # what a server reads in ``bases/``; a folder of another format is left out
_BASE_FACTS = (
    "population-geonames-zero-unknown",  # ``base_facts``
    "month-temperatures-median-of-hikes-twins-once",  # ``base_facts``
)
_REACH_ESTIMATE = (STRAIGHT_SPEED_KMH, APPROACH_SPEED_KMH, BAND_STEP_MIN, REACH_MIN)


@dataclass(frozen=True)
class HikeTypes:
    """The HikeType of every DayHike and the score between HikeTypes.

    ``types`` is one HikeType per DayHike in the store's row order; ``middles``
    the row of each HikeType's middle hike, the one nearest its centre;
    ``table`` the site's score between the middle hikes, the mean of both
    ways round, 1 on the diagonal. ``version`` names the code that made them.
    """

    types: np.ndarray
    middles: np.ndarray
    table: np.ndarray
    version: str


def hike_types(store: SimilarityStore, count: int = HIKE_TYPES) -> HikeTypes:
    """Cluster the store's DayHikes into ``count`` HikeTypes by k-means with a fixed seed.

    The space is the six core Measures, each as the Measure compares it (the
    logarithm of a ratio Measure's floored value, a tolerance Measure's value
    past its dead band, a mix Measure's shares) and scaled so that a random
    pair of DayHikes is one unit apart on it. A missing value sits at the
    Measure's median, a value missing for every DayHike at 0. A store with
    fewer distinct points in that space gets one HikeType per point.
    """
    space = _space(store)
    count = min(count, len(np.unique(space, axis=0)))
    kmeans = KMeans(n_clusters=count, n_init=1, random_state=HIKE_TYPES_SEED).fit(space)
    types = kmeans.labels_.astype(np.int16)
    off_centre = np.linalg.norm(space - kmeans.cluster_centers_[types], axis=1)
    by_type = np.lexsort((off_centre, types))
    middles = by_type[np.searchsorted(types[by_type], np.arange(count))]
    weights = _measure_weights(None)
    one_way = np.vstack([score_rows(store, [int(middle)], middles, weights)[0] for middle in middles])
    table = ((one_way + one_way.T) / 2).astype(np.float32)
    np.fill_diagonal(table, 1.0)
    return HikeTypes(types=types, middles=middles, table=table, version=_version(count))


def _space(store: SimilarityStore) -> np.ndarray:
    rng = np.random.default_rng(HIKE_TYPES_SEED)
    rows = len(store.ids)
    left, right = rng.integers(0, rows, size=(2, _SPACE_PAIRS))
    parts = []
    for measure in (m for m in MEASURES if m.name in CORE_MEASURES):
        values = np.column_stack([_measured(measure, store.column(name)) for name in measure.columns])
        if measure.kind == "ratio":
            values = np.log(values)
        known = ~np.isnan(values).all(axis=0)
        values[:, known] = np.where(
            np.isnan(values[:, known]), np.nanmedian(values[:, known], axis=0), values[:, known]
        )
        values[:, ~known] = 0.0  # missing for every DayHike: no help in telling them apart
        apart = np.linalg.norm(values[left] - values[right], axis=1).mean()
        parts.append(values / apart if apart > 0 else values)
    return np.column_stack(parts)


def _version(count: int) -> str:
    return hashlib.sha256(
        json.dumps(
            [
                count, HIKE_TYPES_SEED, _SPACE_PAIRS, CORE_MEASURES, _FACTS_VERSION, _CALIBRATION_VERSION,
                "kmeans-lloyd-one-init",  # sklearn KMeans, n_init=1 (``hike_types``)
                "middle-nearest-centre",  # a HikeType's middle hike (``hike_types``)
                "table-mean-both-ways",  # the table is symmetric (``hike_types``)
            ]
        ).encode()
    ).hexdigest()[:16]  # fmt: skip


@dataclass(frozen=True)
class BaseFacts:
    """What a Base is like besides its hikes, for the TownTraits.

    ``population`` is each Base's GeoNames population, NaN when unknown;
    ``month_temperatures`` (Bases, Reaches, 12) the median temperature in each
    month of the hikes in each of ``REACHES``, NaN when none of them has one.
    """

    population: np.ndarray
    month_temperatures: np.ndarray


def base_facts(
    store: SimilarityStore,
    places_path: Path,
    place_ids: np.ndarray,
    reached: list[tuple[np.ndarray, np.ndarray]],
    group_of_hike: np.ndarray,
) -> BaseFacts:
    """The ``BaseFacts`` of the Bases ``place_ids``, each with its HikeGroups and their minutes in ``reached``.

    A population of 0 is GeoNames' unknown, as is a place ``places_path`` has no
    population for. A HikeGroup in Reach brings all its hikes, each group of Twins
    once (as its first row); a hike with no temperature in a month is left out of that month.
    """
    db = sqlite3.connect(f"file:{places_path}?mode=ro", uri=True)
    try:
        known = dict(db.execute("SELECT id, population FROM places WHERE population > 0"))
    finally:
        db.close()
    population = np.array([known.get(int(place), np.nan) for place in place_ids], dtype=np.float64)
    temperatures = np.column_stack([store.column(f"temperature_c_{m:02d}") for m in range(1, 13)]).astype(np.float32)
    rows = np.flatnonzero(np.asarray(store.twins) == np.arange(len(store.ids)))
    rows = rows[np.argsort(group_of_hike[rows], kind="stable")]
    starts = np.r_[0, np.cumsum(np.bincount(group_of_hike[rows], minlength=int(group_of_hike.max(initial=-1)) + 1))]
    months = np.full((len(place_ids), len(REACHES), 12), np.nan, dtype=np.float32)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # a month no hike in Reach has a temperature for
        for base, (groups, minutes) in enumerate(reached):
            for r, reach in enumerate(REACHES):
                within = groups[minutes <= reach]
                counts = starts[within + 1] - starts[within]
                if counts.sum():
                    at = np.repeat(starts[within] - (np.cumsum(counts) - counts), counts) + np.arange(counts.sum())
                    months[base, r] = np.nanmedian(temperatures[rows[at]], axis=0)
    return BaseFacts(population=population, month_temperatures=months)


def export_bases(
    out_dir: Path | str,
    valhalla_url: str,
    *,
    workers: int = 1,
    chunk: int = RESUME_CHUNK,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Write ``bases/`` into the serving export ``out_dir``: the Bases and the HikeGroups in their Reach.

    Candidates are the ``places.sqlite`` places of ``BASE_POPULATION`` people or more; a
    Base is one with ``MIN_HIKE_GROUPS`` HikeGroups within ``REACH_MIN`` minutes
    (``reach_minutes``, asked of the highways-only Valhalla at ``valhalla_url``, ``workers``
    candidates at a time). A HikeGroup is the hikes of one HikeType whose Trailheads start in
    one ``CELL_M`` cell, a hike of a group of Twins taking its first row's HikeType and cell;
    its minutes from a Base are the smallest over its hikes' Trailheads, rounded up.

    Files, every array ``.npy``:

    - ``place_ids``, ``points``: each Base's GeoNames id and (lat, lon), by id.
    - ``group_types``: each HikeGroup's HikeType; HikeGroups are numbered in HikeType order.
    - ``group_starts``, ``group_hikes``: the hikes (similarity rows) of HikeGroup g are
      ``group_hikes[group_starts[g]:group_starts[g + 1]]``.
    - ``pair_groups``, ``pair_minutes``: the (Base, HikeGroup) pairs, band-major (every
      Base's pairs within the first Reach, then every Base's beyond it within the second,
      and so on), then by Base, then by HikeGroup and so by HikeType.
    - ``run_offsets``, ``run_types``: run r, the pairs of one band, Base and HikeType, is
      ``run_offsets[r]:run_offsets[r + 1]`` of the pairs, of HikeType ``run_types[r]``.
    - ``base_runs``: the runs of band b and Base i are ``base_runs[s]:base_runs[s + 1]``,
      s = b * Bases + i.
    - ``type_table``: the score between HikeTypes (``hike_types``).
    - ``population``, ``month_temperatures``: each Base's ``BaseFacts``.
    - ``meta.json``, written last, so a folder without it is incomplete: the folder's format,
      the versions it was made with and of which DayHikes (``Bases.open_dir``).

    Each ``chunk`` candidates' Reach is kept in ``bases.work/`` as it is found, so a
    stopped run started again with the same inputs goes on from there.
    """
    out = Path(out_dir)
    store = SimilarityStore.open_dir(out / "similarity")
    types = hike_types(store)
    trailheads = np.load(out / "trailheads.npy").reshape(-1, 2)
    group_of_hike, group_types = _hike_groups(types.types, store.twins, trailheads)
    place_ids, places = _candidates(out / "places.sqlite")
    points, point_of_hike = np.unique(trailheads, axis=0, return_inverse=True)
    point_of_hike = point_of_hike.reshape(-1)
    hikes_by_point = np.argsort(point_of_hike, kind="stable")
    point_starts = np.searchsorted(point_of_hike[hikes_by_point], np.arange(len(points) + 1))
    snaps = HighwaySnaps(valhalla_url, out / "snaps.npz")
    snaps.path = None  # the export's snaps are read, not written
    snaps.of([(float(lat), float(lon)) for lat, lon in points])  # all known up front: the workers only look up
    near_points = cKDTree(_unit(points))
    chord = 2 * math.sin(NEAR_M / (2 * EARTH_RADIUS_M))

    def in_reach(base: int) -> tuple[np.ndarray, np.ndarray]:
        place = (float(places[base, 0]), float(places[base, 1]))
        near = np.array(near_points.query_ball_point(_unit(places[base : base + 1])[0], chord), dtype=int)
        if not len(near):
            return np.zeros(0, dtype=np.int32), np.zeros(0, dtype=np.uint8)
        minutes = reach_minutes(place, points[near], snaps)
        reached = ~np.isnan(minutes)
        near, minutes = near[reached], minutes[reached]
        starts, counts = point_starts[near], point_starts[near + 1] - point_starts[near]
        at = np.repeat(starts - (np.cumsum(counts) - counts), counts) + np.arange(counts.sum())
        groups, minutes = group_of_hike[hikes_by_point[at]], np.repeat(minutes, counts)
        order = np.lexsort((minutes, groups))
        groups, minutes = groups[order], minutes[order]
        first = _starts(groups)
        return groups[first].astype(np.int32), np.ceil(minutes[first]).astype(np.uint8)

    work = out / "bases.work"
    key = _work_key(types.version, chunk, file_key(out / "snaps.npz"), group_of_hike, trailheads, place_ids, places)
    if not (work / "key.txt").is_file() or (work / "key.txt").read_text() != key:
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir()
        (work / "key.txt").write_text(key)
    found: list[tuple[np.ndarray, np.ndarray]] = []
    with ThreadPoolExecutor(workers) as pool:
        for start in range(0, len(place_ids), chunk):
            path = work / f"chunk-{start // chunk:06d}.npz"
            if not path.is_file():
                bases = range(start, min(start + chunk, len(place_ids)))
                snaps.of([(float(lat), float(lon)) for lat, lon in places[bases.start : bases.stop]])
                reached = list(pool.map(in_reach, bases))
                partial = work / f"chunk-{start // chunk:06d}.partial.npz"
                np.savez(
                    partial,
                    counts=np.array([len(groups) for groups, _ in reached], dtype=np.int64),
                    groups=np.concatenate([groups for groups, _ in reached]),
                    minutes=np.concatenate([minutes for _, minutes in reached]),
                )
                os.replace(partial, path)
            with np.load(path) as saved:
                ends = np.cumsum(saved["counts"])
                found += list(zip(np.split(saved["groups"], ends[:-1]), np.split(saved["minutes"], ends[:-1])))
            if progress is not None:
                progress(f"export-bases: Reach of {len(found):,} of {len(place_ids):,} candidate places")
    kept = np.array([i for i, (groups, _) in enumerate(found) if len(groups) >= MIN_HIKE_GROUPS], dtype=int)
    reached = [found[i] for i in kept]
    facts = base_facts(store, out / "places.sqlite", place_ids[kept], reached, group_of_hike)
    target = _write_bases(out, place_ids[kept], places[kept], reached, group_of_hike, group_types, types, facts, store)
    shutil.rmtree(work)
    return target


def _starts(values: np.ndarray) -> np.ndarray:
    """Whether each value of a sorted array is the first of its run."""
    first = np.ones(len(values), dtype=bool)
    first[1:] = values[1:] != values[:-1]
    return first


def _hike_groups(types: np.ndarray, twins: np.ndarray, trailheads: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The HikeGroup of each hike and the HikeType of each HikeGroup, HikeGroups in HikeType order."""
    first = np.asarray(twins)
    metres_per_degree = EARTH_RADIUS_M * math.pi / 180
    y = np.floor(trailheads[first, 0] * metres_per_degree / CELL_M)
    across = metres_per_degree * np.cos(np.radians((y + 0.5) * CELL_M / metres_per_degree))
    x = np.floor(trailheads[first, 1] * across / CELL_M)
    keys = np.column_stack([types[first], y, x]).astype(np.int64)
    found, group_of_hike = np.unique(keys, axis=0, return_inverse=True)
    return group_of_hike.reshape(-1), found[:, 0].astype(np.int16)


def _candidates(places_path: Path) -> tuple[np.ndarray, np.ndarray]:
    """GeoNames ids and (lat, lon) of the places of ``BASE_POPULATION`` people or more, by id."""
    db = sqlite3.connect(f"file:{places_path}?mode=ro", uri=True)
    try:
        rows = db.execute(
            "SELECT id, lat, lon FROM places WHERE population >= ? ORDER BY id", (BASE_POPULATION,)
        ).fetchall()
    finally:
        db.close()
    ids = np.array([row[0] for row in rows], dtype=np.int64)
    return ids, np.array([row[1:] for row in rows], dtype=np.float64).reshape(-1, 2)


def _unit(points: np.ndarray) -> np.ndarray:
    lat, lon = np.radians(points[:, 0]), np.radians(points[:, 1])
    return np.column_stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])


def _work_key(
    version: str,
    chunk: int,
    snaps_key: list | None,
    group_of_hike: np.ndarray,
    trailheads: np.ndarray,
    ids: np.ndarray,
    places: np.ndarray,
) -> str:
    """What a kept chunk of Reach depends on, beyond the Valhalla it was asked of."""
    digest = hashlib.sha256(
        json.dumps([version, chunk, snaps_key, _REACH_ESTIMATE, REACHES, CELL_M, NEAR_M, _BASES_LAYOUT]).encode()
    )
    for array in (group_of_hike, trailheads, ids, places):
        digest.update(np.ascontiguousarray(array).tobytes())
    return digest.hexdigest()


def _write_bases(
    out: Path,
    place_ids: np.ndarray,
    places: np.ndarray,
    reached: list[tuple[np.ndarray, np.ndarray]],
    group_of_hike: np.ndarray,
    group_types: np.ndarray,
    types: HikeTypes,
    facts: BaseFacts,
    store: SimilarityStore,
) -> Path:
    """Write the ``bases/`` folder (``export_bases``) to a partial folder, then put it in place."""
    count = len(place_ids)
    groups = np.concatenate([g for g, _ in reached]) if reached else np.zeros(0, dtype=np.int32)
    minutes = np.concatenate([m for _, m in reached]) if reached else np.zeros(0, dtype=np.uint8)
    base = np.repeat(np.arange(count, dtype=np.int32), [len(g) for g, _ in reached])
    band = np.searchsorted(REACHES[:-1], minutes, side="left").astype(np.int8)
    order = np.lexsort((groups, base, band))
    groups, minutes, base, band = groups[order], minutes[order], base[order], band[order]
    slot = band.astype(np.int64) * count + base
    pair_types = group_types[groups]
    starts = np.flatnonzero(_starts(slot) | _starts(pair_types))
    group_hikes = np.argsort(group_of_hike, kind="stable").astype(np.int32)
    arrays = {
        "place_ids": place_ids,
        "points": places,
        "group_types": group_types,
        "group_starts": np.r_[0, np.cumsum(np.bincount(group_of_hike, minlength=len(group_types)))].astype(np.int64),
        "group_hikes": group_hikes,
        "pair_groups": groups.astype(np.int32),
        "pair_minutes": minutes.astype(np.uint8),
        "run_offsets": np.r_[starts, len(groups)].astype(np.int64),
        "run_types": pair_types[starts].astype(np.int16),
        "base_runs": np.searchsorted(slot[starts], np.arange(len(REACHES) * count + 1)).astype(np.int64),
        "type_table": types.table,
        "population": facts.population,
        "month_temperatures": facts.month_temperatures,
    }
    target, partial = out / "bases", out / "bases.partial"
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir()
    for name, values in arrays.items():
        np.save(partial / f"{name}.npy", values)
    meta = {
        "format": BASES_FORMAT,
        "version": hashlib.sha256(
            json.dumps(
                [types.version, _BASES_LAYOUT, BASE_POPULATION, MIN_HIKE_GROUPS, CELL_M, NEAR_M, _REACH_ESTIMATE]
                + [_BASE_FACTS]
            ).encode()
        ).hexdigest()[:16],
        "hike_types_version": types.version,
        "facts_version": _FACTS_VERSION,
        "dayhikes": _dayhikes_digest(store.ids),
        "reaches": list(REACHES),
        "bases": count,
        "hike_groups": len(group_types),
        "pairs": len(groups),
    }
    (partial / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    shutil.rmtree(target, ignore_errors=True)
    os.replace(partial, target)
    return target


def _dayhikes_digest(ids: np.ndarray) -> str:
    """Which DayHikes, in which row order, a ``bases/`` folder was made of."""
    return hashlib.sha256(np.ascontiguousarray(ids, dtype=np.int64).tobytes()).hexdigest()[:16]


@dataclass(frozen=True)
class Bases:
    """A serving export's ``bases/`` folder (``export_bases``), its arrays memory-mapped read-only."""

    meta: dict
    place_ids: np.ndarray
    points: np.ndarray
    population: np.ndarray
    month_temperatures: np.ndarray
    group_types: np.ndarray
    group_starts: np.ndarray
    group_hikes: np.ndarray
    pair_groups: np.ndarray
    pair_minutes: np.ndarray
    run_offsets: np.ndarray
    run_types: np.ndarray
    base_runs: np.ndarray
    type_table: np.ndarray

    @classmethod
    def open_dir(cls, directory: Path | str, ids: np.ndarray) -> Bases | None:
        """The ``bases/`` folder at ``directory``, or None without a complete one, or with one
        of another ``BASES_FORMAT``, of other hike facts or of other DayHikes than ``ids``."""
        directory = Path(directory)
        meta_path = directory / "meta.json"
        if not meta_path.is_file():
            return None
        meta = json.loads(meta_path.read_text())
        if (
            meta.get("format") != BASES_FORMAT
            or meta.get("facts_version") != _FACTS_VERSION
            or meta.get("dayhikes") != _dayhikes_digest(ids)
        ):
            return None
        arrays = {field.name: map_npy(directory / f"{field.name}.npy") for field in fields(cls) if field.name != "meta"}
        return cls(meta=meta, **arrays)
