"""Measure similarity: facts, Calibration and Ranking (ADR-0013).

``build_similarity`` turns every Catalog DayHike into one column of facts and
stores them, with the Calibration, in a sidecar directory next to the Catalog
(``similarity_dir_for``). Ranking reads only that directory and the Catalog: a
query scores its Seeds against every DayHike with numpy, one Measure at a time,
and nothing is rebuilt per request.

A Measure compares one fact of two DayHikes and gives a score in [0, 1], or
NaN when either DayHike lacks the fact. Calibration maps each raw score to the
share of random DayHike pairs that score lower on that Measure (ties count
half), so Measures on very different raw scales can be weighted against each
other.

Every DayHike has a second set of facts, measured with its way back to its
Trailhead (ADR-0019), with its own Calibration; a query compares Seeds and
candidates in one mode or the other (``SimilarityStore.mode``).
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.neighbors import BallTree

from dayhike.catalog import (
    EARTH_RADIUS_M,
    GAP_JOIN_M,
    SAC_SCALE_RANK,
    Catalog,
    RankedDayHike,
    Ranking,
    geometry_store_path_for,
    resolve_seed,
    similarity_dir_for,
    trailgraph_path_for,
)
from dayhike.dem import DEFAULT_DEM_DIR, FABDEM_VERSION, fabdem_tile_id
from dayhike.landcover import DEFAULT_LANDCOVER_DIR, _landcover_fractions, worldcover_tile_id
from dayhike.trailgraph import WAY_BACK_KINDS, ComposedWalk, TrailGraph, WayBack, round_trip, way_back

Progress = Callable[[str], None]

PROFILE_POINTS = 64
PROFILE_RANGE_FLOOR_M = 50.0
GAIN_HYSTERESIS_M = 10.0  # a climb or descent counts once it reaches this; DEM noise does not
SPIKE_RISE_M = 10.0  # a vertex at least this far above (or below) both neighbours ...
SPIKE_GRADE = 0.25  # ... and this steep on both sides is a DEM spike, not the trail
STEEPNESS_STEP_M = 50.0  # elevation resampled this often along the walk
STEEPEST_KM_WINDOW_M = 1_000.0
CORRIDOR_M = 100.0  # amenity along the DayHike (CONTEXT.md Corridor)
PEAK_SUMMITED_M = 50.0
PEAKS_AROUND_M = 2_000.0
TRAILHEAD_M = 500.0  # amenity at the Trailhead (CONTEXT.md Trailhead)
SEARCH_STEP_M = 100.0  # line points this far apart find candidate amenities
WATER_SOURCE_KINDS = ("drinking_water", "spring", "water_tap", "water_point")
SOLAR_ASPECT_POINTS = 32  # about this many vertices sampled per DayHike
SOLAR_ASPECT_OFFSET_ARCSEC = 2  # east and north offsets: two FABDEM cells
SOLAR_ASPECT_MIN_SLOPE_DEG = 5.0  # flatter points face no way
WATERSIDE_M = 200.0  # open water this close to the line counts (CONTEXT.md WaterSide)
WATERSIDE_STEP_M = 200.0  # a WaterSide probe this often along the walk
WATERSIDE_MIN_SHARE = 0.1  # of the WorldCover cells around a probe that are water
WATERSIDE_OVERVIEW = 4  # WorldCover read at 4 x its 10 m cells
WORLDCOVER_WATER = 80  # permanent water bodies: sea, lakes, wide rivers
TWIN_STEP_M = 50.0  # each walk sampled this often to compare routes
TWIN_M = 30.0  # a sample this close to the other walk lies on it
TWIN_SHARE = 0.8  # each walk this much on the other: the same route (CONTEXT.md Twin)
CALIBRATION_QUANTILES = 1_024
CALIBRATION_PAIRS = 100_000
CALIBRATION_SEED = 20261024
_HIKE_CHUNK = 2_000

LANDCOVER_SHARE_COLUMNS = (
    "tree_share",
    "shrub_share",
    "grass_share",
    "bare_share",
    "snow_share",
    "other_share",
)
SURFACE_SHARE_COLUMNS = ("paved_share", "gravel_share", "natural_share", "rock_share")
FACILITIES_FLAG_COLUMNS = (
    "hut_along",
    "restaurant_cafe_along",
    "shelter_along",
    "restaurant_cafe_trailhead",
    "parking_trailhead",
)
CLIMATE_NAMES = ("temperature_c", "precipitation_mm", "solar_radiation")
CLIMATE_MONTH_COLUMNS = tuple(
    f"{name}_{month:02d}" for month in range(1, 13) for name in CLIMATE_NAMES
)
# Per DayHike with its way back: the metres back, the climb and descent of the
# whole walk, and how it gets back (an index into ``WAY_BACK_KINDS``).
WAY_BACK_COLUMNS = ("back_m", "climb_m", "descent_m", "kind")
FACT_COLUMNS = (
    "length_m",
    "elevation_gain_m",
    "difficulty_rank",
    "highest_m",
    "lowest_m",
    *LANDCOVER_SHARE_COLUMNS,
    "peak_summited",
    "viewpoint_along",
    "peaks_around",
    *CLIMATE_NAMES,
    *CLIMATE_MONTH_COLUMNS,
    "southness",
    "water_count",
    "dry_stretch_m",
    "waterside_share",
    *FACILITIES_FLAG_COLUMNS,
    *SURFACE_SHARE_COLUMNS,
    "tarmac_approach_m",
    "round_trip",
    "climb_grade",
    "steepest_km_m",
)
_SURFACE_TAG_CLASS = {
    **dict.fromkeys(
        ("asphalt", "concrete", "paving_stones", "sett", "cobblestone", "paved"),
        "paved",
    ),
    **dict.fromkeys(
        ("compacted", "gravel", "fine_gravel", "unpaved", "pebblestone"), "gravel"
    ),
    **dict.fromkeys(
        ("ground", "dirt", "earth", "grass", "mud", "sand", "woodchips"), "natural"
    ),
    **dict.fromkeys(("rock", "bare_rock", "stone"), "rock"),
}
_ROAD_HIGHWAYS = frozenset(
    (
        "motorway", "trunk", "primary", "secondary", "tertiary", "residential",
        "service", "living_street", "pedestrian", "cycleway", "unclassified",
    )
)  # fmt: skip
_TRACKTYPE_CLASS = {
    "grade1": "paved",
    "grade2": "gravel",
    "grade3": "gravel",
    "grade4": "natural",
    "grade5": "natural",
}
# Amenity kinds searched along the line (``walk_facts`` ``along``).
ALONG_KINDS = ("hut", "restaurant_cafe", "shelter", "viewpoint", "peak", *WATER_SOURCE_KINDS)


@dataclass(frozen=True)
class Measure:
    """One fact compared between two DayHikes.

    ``kind`` is ``ratio`` (smaller over larger, each raised to ``constant``),
    ``tolerance`` (1 at equal values, 0 once they differ by ``constant``;
    values first capped at ``cap``), ``mix`` (shared share of fraction
    columns), ``agree`` (1 when two yes/no flags agree, both-absent agrees) or
    ``profile`` (1 - mean abs difference of two ProfileShapes, the better of
    forward or reversed). A ``monthly`` Measure reads the QueryMonth column of
    its fact when the query sets one.
    """

    name: str
    kind: str
    columns: tuple[str, ...]
    constant: float | None = None
    cap: float | None = None
    monthly: bool = False


MEASURES = (
    Measure("length", "ratio", ("length_m",), 1_000),
    Measure("elevation_gain", "ratio", ("elevation_gain_m",), 100),
    Measure("difficulty", "tolerance", ("difficulty_rank",), 3),
    Measure("highest", "tolerance", ("highest_m",), 400),
    Measure("lowest", "tolerance", ("lowest_m",), 400),
    Measure("profile", "profile", ()),
    Measure("landcover", "mix", LANDCOVER_SHARE_COLUMNS),
    Measure("peak_summited", "agree", ("peak_summited",)),
    Measure("viewpoint_along", "agree", ("viewpoint_along",)),
    Measure("peaks_around", "ratio", ("peaks_around",), 1),
    Measure("temperature", "tolerance", ("temperature_c",), 10, monthly=True),
    Measure("precipitation", "ratio", ("precipitation_mm",), 10, monthly=True),
    Measure("solar_radiation", "tolerance", ("solar_radiation",), 5_000, monthly=True),
    Measure("southness", "tolerance", ("southness",), 1),
    Measure("water_count", "tolerance", ("water_count",), 3, cap=3),
    Measure("dry_stretch", "ratio", ("dry_stretch_m",), 1_000),
    Measure("waterside", "tolerance", ("waterside_share",), 0.5),
    *(Measure(flag, "agree", (flag,)) for flag in FACILITIES_FLAG_COLUMNS),
    Measure("surface", "mix", SURFACE_SHARE_COLUMNS),
    Measure("tarmac_approach", "tolerance", ("tarmac_approach_m",), 2_000),
    Measure("round_trip", "agree", ("round_trip",)),
    Measure("climb_grade", "ratio", ("climb_grade",), 0.03),
    Measure("steepest_km", "ratio", ("steepest_km_m",), 50),
)
MEASURE_NAMES = tuple(measure.name for measure in MEASURES)
# On by default: the Measures that told good matches from poor ones for blind
# judges (ADR-0016); the others are off until the Hiker weights them.
CORE_MEASURES = (
    "length", "elevation_gain", "highest", "lowest", "profile", "landcover",
    "temperature", "precipitation", "solar_radiation", "waterside", "climb_grade",
)  # fmt: skip
DEFAULT_WEIGHTS = {name: 100.0 if name in CORE_MEASURES else 0.0 for name in MEASURE_NAMES}
# Calibration rows: every Measure, then each monthly Measure once per QueryMonth.
CALIBRATION_KEYS = (
    *MEASURE_NAMES,
    *(
        f"{measure.name}@{month:02d}"
        for month in range(1, 13)
        for measure in MEASURES
        if measure.monthly
    ),
)

_FACTS_VERSION = hashlib.sha256(
    json.dumps(
        [
            FACT_COLUMNS, PROFILE_POINTS, PROFILE_RANGE_FLOOR_M, GAIN_HYSTERESIS_M, SPIKE_RISE_M, SPIKE_GRADE,
            STEEPNESS_STEP_M, STEEPEST_KM_WINDOW_M, CORRIDOR_M, PEAK_SUMMITED_M, PEAKS_AROUND_M,
            TRAILHEAD_M, SEARCH_STEP_M, WATER_SOURCE_KINDS, SOLAR_ASPECT_POINTS,
            SOLAR_ASPECT_OFFSET_ARCSEC, SOLAR_ASPECT_MIN_SLOPE_DEG, WATERSIDE_M,
            WATERSIDE_STEP_M, WATERSIDE_MIN_SHARE, WATERSIDE_OVERVIEW, WORLDCOVER_WATER,
            TWIN_STEP_M, TWIN_M, TWIN_SHARE, sorted(_SURFACE_TAG_CLASS.items()), sorted(_ROAD_HIGHWAYS),
            sorted(_TRACKTYPE_CLASS.items()), GAP_JOIN_M, WAY_BACK_KINDS, WAY_BACK_COLUMNS,
        ]
    ).encode()
).hexdigest()[:16]  # fmt: skip
_CALIBRATION_VERSION = hashlib.sha256(
    json.dumps(
        [
            [vars(measure) for measure in MEASURES], CALIBRATION_KEYS,
            CALIBRATION_QUANTILES, CALIBRATION_PAIRS, CALIBRATION_SEED,
        ]
    ).encode()
).hexdigest()[:16]  # fmt: skip


class StaleSimilarity(ValueError):
    """The similarity directory does not match the Catalog or this code."""


@dataclass(frozen=True, eq=False)
class SimilarityStore:
    """Facts and Calibration of every Catalog DayHike, memory-mapped.

    ``facts`` is one float32 row per ``FACT_COLUMNS`` entry and one column per
    DayHike in ``ids`` order (NaN missing, yes/no as 0/1). ``profile`` is one
    ProfileShape row per DayHike (NaN when missing). ``twins`` gives each
    DayHike the first row of its group of Twins (``twin_groups``; its own row
    when it has none). ``quantiles`` holds ``CALIBRATION_QUANTILES`` sorted
    random-pair scores per ``CALIBRATION_KEYS`` entry.

    ``with_way_back`` is the same DayHikes measured with their way back to the
    Trailhead (ADR-0019): its own facts, ProfileShapes and Calibration, the
    same Twins, and ``way_back`` (``WAY_BACK_COLUMNS`` x DayHikes).
    """

    ids: np.ndarray
    facts: np.ndarray
    profile: np.ndarray
    twins: np.ndarray
    quantiles: np.ndarray
    catalog_digest: str | None = None
    with_way_back: SimilarityStore | None = None
    way_back: np.ndarray | None = None

    @classmethod
    def open(cls, catalog_path: Path | str) -> SimilarityStore:
        return cls.open_dir(similarity_dir_for(catalog_path))

    @classmethod
    def open_for(cls, catalog_path: Path | str, ids: Sequence[int]) -> SimilarityStore:
        """Open the store beside a Catalog, refusing one built from other Catalog data."""
        store = cls.open(catalog_path)
        if not np.array_equal(store.ids, ids):
            raise StaleSimilarity(
                f"{catalog_path}: similarity facts are for other DayHikes; "
                "run `dayhike build-similarity`"
            )
        if store.catalog_digest != catalog_digest(catalog_path):
            raise StaleSimilarity(
                f"{catalog_path}: similarity facts are for earlier Catalog values; "
                "run `dayhike build-similarity`"
            )
        return store

    @classmethod
    def open_dir(cls, directory: Path | str) -> SimilarityStore:
        """Open a similarity directory wherever it is (a serving export holds a copy)."""
        directory = Path(directory)
        meta_path = directory / "meta.json"
        if not meta_path.is_file():
            raise StaleSimilarity(
                f"{directory}: no similarity facts; run `dayhike build-similarity`"
            )
        meta = json.loads(meta_path.read_text())
        if meta["facts_version"] != _FACTS_VERSION:
            raise StaleSimilarity(
                f"{directory}: facts were built by other code; "
                "run `dayhike build-similarity`"
            )
        if meta["calibration_version"] != _CALIBRATION_VERSION:
            raise StaleSimilarity(
                f"{directory}: Calibration is for other Measures; "
                "run `dayhike build-similarity --calibrate-only`"
            )

        def load(name: str) -> np.ndarray:
            return np.load(directory / f"{name}.npy", mmap_mode="r")

        ids, twins, digest = load("ids"), load("twins"), meta.get("catalog_digest")
        return cls(
            ids=ids,
            facts=load("facts"),
            profile=load("profile"),
            twins=twins,
            quantiles=load("quantiles"),
            catalog_digest=digest,
            with_way_back=cls(
                ids=ids,
                facts=load("facts_back"),
                profile=load("profile_back"),
                twins=twins,
                quantiles=load("quantiles_back"),
                catalog_digest=digest,
                way_back=load("way_back"),
            ),
        )

    def column(self, name: str) -> np.ndarray:
        return self.facts[FACT_COLUMNS.index(name)]

    def mode(self, return_to_start: bool) -> SimilarityStore:
        """The DayHikes measured with their way back to the start, or one way."""
        return self.with_way_back if return_to_start else self


@dataclass(frozen=True, eq=False)
class OutsideSeed:
    """A Seed that is no Catalog DayHike (a ComposedHike), scored beside a
    store without joining it: its facts in ``FACT_COLUMNS`` order (NaN
    missing), its ProfileShape, and the store rows of the DayHikes it
    retraces, whose groups of Twins a Ranking from it leaves out.
    """

    facts: np.ndarray
    profile: np.ndarray
    twin_rows: np.ndarray

    @classmethod
    def of(
        cls, facts: Mapping[str, float], profile: np.ndarray | None, twin_rows: Sequence[int] = ()
    ) -> OutsideSeed:
        return cls(
            facts=np.array([facts.get(name, math.nan) for name in FACT_COLUMNS], dtype=np.float32),
            profile=np.asarray(
                np.full(PROFILE_POINTS, np.nan) if profile is None else profile, dtype=np.float32
            ),
            twin_rows=np.asarray(twin_rows, dtype=np.int64),
        )


def rank_dayhikes(
    catalog: Catalog,
    seeds: Sequence[str],
    weights: Mapping[str, float] | None = None,
    *,
    month: int | None = None,
    allowed_ids: Collection[int] | None = None,
    limit: int | None = None,
    return_to_start: bool = True,
) -> Ranking:
    """Rank Catalog DayHikes by Measure similarity to named Seeds.

    ``weights`` maps Measure names (``MEASURE_NAMES``) to 0-100; a Measure
    left out or at 0 is excluded. None is ``DEFAULT_WEIGHTS``. Each
    candidate's similarity to one Seed is the weighted mean of its calibrated
    Measure scores over the Measures both DayHikes have; with several Seeds the
    best Seed counts. Ties break on the same weighted mean of raw scores, then
    on DayHike id. ``score`` is that similarity: 0.5 is about a typical random
    pair, 1.0 beats every random pair on every weighted Measure. Of a group of
    Twins only the best is kept, and none of a Seed's Twins. Seeds are
    resolved with ``resolve_seed`` and omitted. A candidate with no weighted
    Measure scored is left out. ``allowed_ids`` restricts the candidates (for
    an upstream filter such as DriveTime). ``limit`` keeps only the best that
    many items (every candidate is still scored). QueryMonth selects that
    month's climate in the monthly Measures. ``return_to_start`` measures
    Seeds and candidates alike with their way back to the start (ADR-0019),
    else one way.
    """
    chosen = _measure_weights(weights)
    _check_month(month)
    seed_ids = sorted({resolve_seed(catalog, name) for name in seeds})
    store = catalog.similarity.mode(return_to_start)
    rows, scores = rank_rows(
        store,
        np.searchsorted(store.ids, seed_ids),
        chosen,
        month=month,
        allowed_ids=allowed_ids,
        limit=limit,
    )
    return Ranking(
        items=tuple(
            RankedDayHike(dayhike=catalog.dayhikes[row], score=float(score))
            for row, score in zip(rows, scores)
        )
    )


def rank_rows(
    store: SimilarityStore,
    seed_rows: Sequence[int | OutsideSeed] | np.ndarray,
    weights: Mapping[str, float] | None = None,
    *,
    month: int | None = None,
    allowed_ids: Collection[int] | None = None,
    limit: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """``rank_dayhikes`` on store rows: the ranked rows and their scores.

    Seeds are store rows, not names, so a caller that already knows the
    DayHike (a web page that picked it from a list) needs no Catalog. A Seed
    may also be an ``OutsideSeed``; the DayHikes it retraces count as its Twins.
    """
    chosen = _measure_weights(weights)
    _check_climate(store, month)
    seeds = list(seed_rows)
    inside = np.array([seed for seed in seeds if not isinstance(seed, OutsideSeed)], dtype=np.int64)
    twin_rows = np.concatenate([inside, *(seed.twin_rows for seed in seeds if isinstance(seed, OutsideSeed))])
    rows = np.arange(len(store.ids))
    if allowed_ids is not None:
        rows = rows[np.isin(store.ids, np.fromiter(allowed_ids, dtype=np.int64))]
    rows = rows[~np.isin(rows, inside)]
    similarity, raw = score_rows(store, seeds, rows, chosen, month=month)
    scored = ~np.isnan(similarity)
    rows, similarity, raw = rows[scored], similarity[scored], raw[scored]
    order = np.lexsort((store.ids[rows], -raw, -similarity))
    rows, similarity = rows[order], similarity[order]
    group = store.twins[rows]
    _, first = np.unique(group, return_index=True)
    keep = np.sort(first)
    keep = keep[~np.isin(group[keep], store.twins[twin_rows])][:limit]
    return rows[keep], similarity[keep]


def _check_climate(store: SimilarityStore, month: int | None) -> None:
    _check_month(month)
    if month is not None and np.isnan(store.column(f"temperature_c_{month:02d}")).all():
        raise ValueError("climate has not been sampled")


def _check_month(month: int | None) -> None:
    if month is not None and (
        isinstance(month, bool) or not isinstance(month, int) or not 1 <= month <= 12
    ):
        raise ValueError(f"QueryMonth must be an integer from 1 to 12, got {month!r}")


def score_rows(
    store: SimilarityStore,
    seed_rows: Sequence[int | OutsideSeed],
    rows: np.ndarray,
    weights: Mapping[str, float],
    *,
    month: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Similarity of ``rows`` to their best Seed: calibrated and raw weighted means.

    ``weights`` maps Measure names to positive weights. Both arrays are NaN
    where no weighted Measure is scored for any Seed. The raw mean belongs to
    the Seed with the best calibrated mean.
    """
    measures = [measure for measure in MEASURES if measure.name in weights]
    w = np.array([weights[measure.name] for measure in measures], dtype=float)
    best = np.full(len(rows), np.nan)
    best_raw = np.full(len(rows), np.nan)
    for seed in seed_rows:
        raw = np.column_stack(
            [measure_scores(store, measure, seed, rows, month=month) for measure in measures]
        )
        calibrated = np.column_stack(
            [
                calibrate(store, _calibration_key(measure, month), raw[:, j])
                for j, measure in enumerate(measures)
            ]
        )
        similarity = weighted_mean(calibrated, w)
        better = similarity > np.where(np.isnan(best), -np.inf, best)
        best = np.where(better, similarity, best)
        best_raw = np.where(better, weighted_mean(raw, w), best_raw)
    return best, best_raw


def measure_scores(
    store: SimilarityStore,
    measure: Measure,
    a: int | np.ndarray | OutsideSeed,
    b: np.ndarray,
    *,
    month: int | None = None,
) -> np.ndarray:
    """Raw scores of one Measure between DayHike rows ``a`` and ``b``.

    ``a`` is one row (a Seed), an ``OutsideSeed``, or an array of rows paired with ``b``.
    """

    def seed_column(name: str) -> np.ndarray:
        if isinstance(a, OutsideSeed):
            return a.facts[FACT_COLUMNS.index(name)]
        return store.column(name)[a]

    if measure.kind == "profile":
        left = a.profile if isinstance(a, OutsideSeed) else store.profile[a]
        right = store.profile[b]
        forward = np.abs(right - left).mean(axis=-1)
        backward = np.abs(right - left[..., ::-1]).mean(axis=-1)
        return 1.0 - np.minimum(forward, backward)
    columns = [
        f"{name}_{month:02d}" if measure.monthly and month is not None else name
        for name in measure.columns
    ]
    if measure.kind == "mix":
        shared = sum(np.minimum(seed_column(name), store.column(name)[b]) for name in columns)
        return np.minimum(shared, 1.0)
    left, right = seed_column(columns[0]), store.column(columns[0])[b]
    if measure.kind == "agree":
        return np.where(np.isnan(left) | np.isnan(right), np.nan, left == right)
    if measure.kind == "ratio":
        left = np.maximum(left, measure.constant)
        right = np.maximum(right, measure.constant)
        return np.minimum(left, right) / np.maximum(left, right)
    if measure.cap is not None:
        left, right = np.minimum(left, measure.cap), np.minimum(right, measure.cap)
    return np.maximum(0.0, 1.0 - np.abs(left - right) / measure.constant)


def calibrate(store: SimilarityStore, key: str, scores: np.ndarray) -> np.ndarray:
    """Share of random pairs that score lower on ``key`` (ties count half).

    NaN stays NaN; a key without random-pair scores gives NaN.
    """
    quantiles = store.quantiles[CALIBRATION_KEYS.index(key)]
    return _mid_rank(quantiles, scores)


def weighted_mean(scores: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Row-wise mean of ``scores`` columns weighted by ``weights``, skipping NaN.

    The weights of the scored columns renormalize; a row with none is NaN.
    """
    present = ~np.isnan(scores)
    total = present @ weights
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(total > 0, np.where(present, scores, 0.0) @ weights / total, np.nan)


def _mid_rank(sorted_values: np.ndarray, scores: np.ndarray) -> np.ndarray:
    """Share of ``sorted_values`` below each score, ties counting half.

    One search over the distinct values: ``below`` counts the values under
    each score and ``ties`` those equal to it.
    """
    if sorted_values.size == 0 or np.isnan(sorted_values).all():
        return np.full(np.shape(scores), np.nan)
    distinct, first, count = np.unique(sorted_values, return_index=True, return_counts=True)
    at = np.searchsorted(distinct, scores, side="left")
    below = np.append(first, len(sorted_values))[at]
    nearest = np.minimum(at, len(distinct) - 1)
    ties = np.where(distinct[nearest] == scores, count[nearest], 0)
    return np.where(np.isnan(scores), np.nan, (below + ties / 2) / len(sorted_values))


def _calibration_key(measure: Measure, month: int | None) -> str:
    return measure.name if month is None or not measure.monthly else f"{measure.name}@{month:02d}"


def _measure_weights(weights: Mapping[str, float] | None) -> dict[str, float]:
    if weights is None:
        weights = DEFAULT_WEIGHTS
    unknown = sorted(set(weights) - set(MEASURE_NAMES))
    if unknown:
        raise ValueError(f"unknown Measure(s): {', '.join(unknown)}")
    for name, weight in weights.items():
        if not 0 <= weight <= 100:
            raise ValueError(f"Measure weight must be 0-100, got {name}={weight}")
    chosen = {name: float(weight) for name, weight in weights.items() if weight > 0}
    if not chosen:
        raise ValueError("at least one Measure needs a weight above 0")
    return chosen


def build_similarity(
    catalog_path: Path | str,
    dem_dir: Path | str = DEFAULT_DEM_DIR,
    *,
    landcover_dir: Path | str = DEFAULT_LANDCOVER_DIR,
    progress: Progress | None = None,
) -> Path:
    """Write the facts, Twins and Calibration of every Catalog DayHike, one
    way and with its way back.

    Reads the Catalog and its GeometryStore (read-only), FABDEM tiles in
    ``dem_dir`` for SolarAspect and WorldCover tiles in ``landcover_dir`` for
    WaterSide, and the TrailGraph for each way back (its per-vertex samples,
    so after ``sample_trailgraph``), then calibrates (``calibrate_similarity``).
    Run it after ingest and every sample step; it replaces the directory.
    """
    started = time.monotonic()
    catalog_path = Path(catalog_path)
    directory = similarity_dir_for(catalog_path)
    digest = catalog_digest(catalog_path)
    arrays = _build_facts(catalog_path, Path(dem_dir), Path(landcover_dir), progress)
    directory.mkdir(exist_ok=True)
    for path in directory.glob("*"):
        path.unlink()
    for name, values in arrays.items():
        np.save(directory / f"{name}.npy", values)
    ids = arrays["ids"]
    (directory / "meta.json").write_text(
        json.dumps(
            {
                "facts_version": _FACTS_VERSION,
                "calibration_version": None,
                "catalog_digest": digest,
                "fact_columns": FACT_COLUMNS,
                "dayhikes": len(ids),
                "facts_seconds": round(time.monotonic() - started, 1),
            },
            indent=1,
        )
    )
    return calibrate_similarity(catalog_path, progress=progress)


def catalog_digest(catalog_path: Path | str) -> str:
    """Digest of the Catalog values the facts are built from.

    Covers every ``feature_vectors`` and ``climate_months`` row, so a sample
    step that changes them (same DayHikes, new values) changes the digest.
    """
    digest = hashlib.sha256()
    catalog = sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True)
    try:
        for table, key in (("feature_vectors", "dayhike_id"), ("climate_months", "dayhike_id, month")):
            for row in catalog.execute(f"SELECT * FROM {table} ORDER BY {key}"):
                digest.update(repr(row).encode())
    finally:
        catalog.close()
    return digest.hexdigest()[:16]


def calibrate_similarity(
    catalog_path: Path | str, *, progress: Progress | None = None
) -> Path:
    """Recompute the Calibration from the stored facts.

    ``CALIBRATION_PAIRS`` random pairs of distinct DayHikes are scored on every
    ``CALIBRATION_KEYS`` entry; ``CALIBRATION_QUANTILES`` evenly spaced
    quantiles of the scored ones are kept per key, for the facts one way and
    with the way back alike. Rerun after a Measure constant changes.
    """
    directory = similarity_dir_for(catalog_path)
    meta = json.loads((directory / "meta.json").read_text())
    if meta["facts_version"] != _FACTS_VERSION:
        raise StaleSimilarity(f"{directory}: facts are stale; run `dayhike build-similarity`")
    if progress is not None:
        progress("similarity: calibration")
    started = time.monotonic()
    ids = np.load(directory / "ids.npy")
    rng = np.random.default_rng(CALIBRATION_SEED)
    left, right = rng.integers(0, len(ids), size=(2, CALIBRATION_PAIRS))
    distinct = left != right
    left, right = left[distinct], right[distinct]
    by_name = {measure.name: measure for measure in MEASURES}
    at = (np.arange(CALIBRATION_QUANTILES) + 0.5) / CALIBRATION_QUANTILES
    for suffix in ("", "_back"):
        store = SimilarityStore(
            ids=ids,
            facts=np.load(directory / f"facts{suffix}.npy"),
            profile=np.load(directory / f"profile{suffix}.npy"),
            twins=np.load(directory / "twins.npy"),
            quantiles=np.empty((0, 0), dtype=np.float32),
        )
        scores = np.column_stack(
            [
                measure_scores(
                    store,
                    by_name[key.split("@")[0]],
                    left,
                    right,
                    month=int(key.split("@")[1]) if "@" in key else None,
                )
                for key in CALIBRATION_KEYS
            ]
        ).astype(np.float32)
        quantiles = np.full((len(CALIBRATION_KEYS), CALIBRATION_QUANTILES), np.nan, np.float32)
        for row, column in enumerate(scores.T):
            scored = column[~np.isnan(column)]
            if scored.size:
                quantiles[row] = np.quantile(scored, at, method="inverted_cdf")
        np.save(directory / f"quantiles{suffix}.npy", quantiles)
    meta.update(
        calibration_version=_CALIBRATION_VERSION,
        calibration_keys=CALIBRATION_KEYS,
        calibration_pairs=int(distinct.sum()),
        calibration_seconds=round(time.monotonic() - started, 1),
    )
    (directory / "meta.json").write_text(json.dumps(meta, indent=1))
    return directory


def _build_facts(
    catalog_path: Path, dem_dir: Path, landcover_dir: Path, progress: Progress | None
) -> dict[str, np.ndarray]:
    """Ids, facts (``FACT_COLUMNS`` x DayHikes, float32), ProfileShapes and
    Twins; the facts and ProfileShapes with the way back (``_back``) and
    ``way_back`` (``WAY_BACK_COLUMNS`` x DayHikes)."""
    column = {name: index for index, name in enumerate(FACT_COLUMNS)}
    catalog = sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True)
    try:
        rows = catalog.execute(
            "SELECT f.dayhike_id, t.latitude, t.longitude, f.length_m, "
            "f.difficulty_ordinal, f.elevation_max_m, f.elevation_min_m, "
            "f.tree_fraction, f.shrub_fraction, f.grass_fraction, f.bare_fraction, "
            "f.snow_fraction, f.temperature_c, f.precipitation_mm, f.solar_radiation "
            "FROM feature_vectors f JOIN trailheads t ON t.dayhike_id = f.dayhike_id "
            "ORDER BY f.dayhike_id"
        ).fetchall()
        summary = np.array(rows, dtype=float).reshape(-1, 15)
        months = catalog.execute(
            "SELECT dayhike_id, month, temperature_c, precipitation_mm, solar_radiation "
            "FROM climate_months"
        ).fetchall()
    finally:
        catalog.close()
    ids = summary[:, 0].astype(np.int64)
    n = len(ids)
    facts = np.full((len(FACT_COLUMNS), n), np.nan, dtype=np.float32)
    profile = np.full((n, PROFILE_POINTS), np.nan, dtype=np.float32)
    for index, name in enumerate(("length_m", "difficulty_rank", "highest_m", "lowest_m")):
        facts[column[name]] = summary[:, 3 + index]
    shares = summary[:, 7:12]
    shares = np.column_stack([shares, np.clip(1.0 - shares.sum(axis=1), 0.0, None)])
    shares[np.isnan(shares).any(axis=1)] = np.nan
    for index, name in enumerate(LANDCOVER_SHARE_COLUMNS):
        facts[column[name]] = shares[:, index]
    for index, name in enumerate(CLIMATE_NAMES):
        facts[column[name]] = summary[:, 12 + index]
    months = np.array(months, dtype=float).reshape(-1, 5)
    month_rows = np.searchsorted(ids, months[:, 0].astype(np.int64))
    for month in range(1, 13):
        at = months[:, 1] == month
        for index, name in enumerate(CLIMATE_NAMES):
            facts[column[f"{name}_{month:02d}"], month_rows[at]] = months[at, 2 + index]

    graph_path = trailgraph_path_for(catalog_path)
    graph = TrailGraph(graph_path) if graph_path.is_file() else None
    geo = sqlite3.connect(f"file:{geometry_store_path_for(catalog_path)}?mode=ro", uri=True)
    try:
        kinds, amenity_points = _amenities(geo)

        def of_kinds(*wanted: str) -> AmenityPoints:
            keep = np.isin(kinds, wanted)
            return AmenityPoints(amenity_points[keep], kinds[keep])

        along, peaks = of_kinds(*ALONG_KINDS), of_kinds("peak")
        trailheads = np.radians(summary[:, 1:3])
        for name, kind in (
            ("restaurant_cafe_trailhead", "restaurant_cafe"),
            ("parking_trailhead", "parking"),
        ):
            near = of_kinds(kind).tree.query_radius(
                trailheads, r=TRAILHEAD_M / EARTH_RADIUS_M, count_only=True
            )
            facts[column[name]] = near > 0
        back_facts, back_profile = facts.copy(), profile.copy()
        back_rows = np.zeros((len(WAY_BACK_COLUMNS), n), dtype=np.float32)
        back_rows[1:3] = np.nan
        has_landcover = "landcover" in {row[1] for row in geo.execute("PRAGMA table_info(vertices)")}
        probes: dict[str, list[np.ndarray]] = {"": [], "_back": []}
        probe_rows: dict[str, list[np.ndarray]] = {"": [], "_back": []}
        water_probes: dict[str, list[np.ndarray]] = {"": [], "_back": []}
        water_rows: dict[str, list[np.ndarray]] = {"": [], "_back": []}
        routes: list[np.ndarray | None] = [None] * n
        for start in range(0, n, _HIKE_CHUNK):
            chunk = ids[start : start + _HIKE_CHUNK]
            walks = _walks(geo, int(chunk[0]), int(chunk[-1]), has_landcover)
            for row, dayhike_id in enumerate(chunk, start=start):
                walk = walks.get(int(dayhike_id))
                if walk is None:
                    for target in (facts, back_facts):
                        target[column["round_trip"], row] = 0.0
                        for name in ("peak_summited", "viewpoint_along", "peaks_around"):
                            target[column[name], row] = 0.0
                        for name in FACILITIES_FLAG_COLUMNS[:3]:
                            target[column[name], row] = 0.0
                    continue
                lat, lon, ele, ways, codes = walk
                one_way = ComposedWalk(
                    None, None, None, None, lat, lon, ele, codes,
                    np.full(len(lat), -1), np.full(len(lat), np.nan), ways, None, {},
                )  # fmt: skip
                back = way_back(lat, lon, graph)
                trip = round_trip(one_way, back)
                for suffix, measured, target, shapes in (
                    ("", one_way, facts, profile),
                    ("_back", trip, back_facts, back_profile),
                ):
                    for name, value in walk_facts(
                        measured.lat, measured.lon, measured.ele, measured.walked, along, peaks
                    ).items():
                        target[column[name], row] = value
                    shape = profile_shape(measured.lat, measured.lon, measured.ele)
                    if shape is not None:
                        shapes[row] = shape
                    chosen = solar_aspect_points(len(measured.lat))
                    probes[suffix].append(np.column_stack([measured.lat[chosen], measured.lon[chosen]]))
                    probe_rows[suffix].append(np.full(chosen.size, row))
                    if len(measured.lat) >= 2:
                        water_probes[suffix].append(_even_points(measured.lat, measured.lon, WATERSIDE_STEP_M))
                        water_rows[suffix].append(np.full(len(water_probes[suffix][-1]), row))
                if len(lat) >= 2:
                    routes[row] = _even_points(lat, lon, TWIN_STEP_M).astype(np.float32)
                _add_way_back(back_facts[:, row], len(lat), back, trip, graph, has_landcover)
                back_rows[:, row] = (
                    back.metres, *climb_and_descent(trip.lat, trip.lon, trip.ele), WAY_BACK_KINDS.index(back.kind)
                )
            if progress is not None:
                progress(f"similarity facts: {min(start + _HIKE_CHUNK, n)}/{n} DayHikes")
    finally:
        geo.close()
    for suffix, target in (("", facts), ("_back", back_facts)):
        if progress is not None:
            progress(f"similarity facts{suffix}: SolarAspect")
        points = np.concatenate(probes[suffix]) if probes[suffix] else np.empty((0, 2))
        owners = np.concatenate(probe_rows[suffix]) if probe_rows[suffix] else np.empty(0, dtype=int)
        target[column["southness"]] = solar_aspect_southness(dem_dir, points, owners, n)
        if progress is not None:
            progress(f"similarity facts{suffix}: WaterSide")
        points = np.concatenate(water_probes[suffix]) if water_probes[suffix] else np.empty((0, 2))
        owners = np.concatenate(water_rows[suffix]) if water_rows[suffix] else np.empty(0, dtype=int)
        target[column["waterside_share"]] = waterside_share(landcover_dir, points, owners, n)
    if progress is not None:
        progress("similarity facts: Twins")
    return {
        "ids": ids,
        "facts": facts,
        "profile": profile,
        "twins": twin_groups(routes),
        "facts_back": back_facts,
        "profile_back": back_profile,
        "way_back": back_rows,
    }


def _add_way_back(
    facts: np.ndarray, one_way: int, back: WayBack, trip: ComposedWalk, graph: TrailGraph | None, has_landcover: bool
) -> None:
    """Bring the Catalog's own facts of one DayHike (its column of ``facts``)
    to the walk with its way back: ``trip``, whose first ``one_way`` vertices
    are the DayHike's. Length adds the metres back; highest, lowest and
    Difficulty take the way back's too; LandCover counts every vertex of the
    trip (when the GeometryStore has its classes); climate averages the
    vertices back with the DayHike's, weighted by the vertex count.
    """
    column = {name: index for index, name in enumerate(FACT_COLUMNS)}
    facts[column["length_m"]] += back.metres
    tail = trip.ele[one_way:]
    tail = tail[np.isfinite(tail)]
    if tail.size:
        facts[column["highest_m"]] = np.fmax(facts[column["highest_m"]], tail.max())
        facts[column["lowest_m"]] = np.fmin(facts[column["lowest_m"]], tail.min())
    if back.walk is not None and back.walk.sac_scale is not None:
        facts[column["difficulty_rank"]] = np.fmax(facts[column["difficulty_rank"]], SAC_SCALE_RANK[back.walk.sac_scale])
    codes = trip.landcover[trip.landcover > 0]
    if has_landcover and codes.size:
        fractions = list(_landcover_fractions(codes.tolist()))
        fractions.append(max(1.0 - sum(fractions), 0.0))
        facts[[column[name] for name in LANDCOVER_SHARE_COLUMNS]] = fractions
    cells = trip.climate_cell[one_way:]
    cells = cells[cells >= 0]
    if graph is None or not cells.size:
        return
    climate = graph._climate(cells)  # (cells, 12, 3)
    months = [f"{name}_{month:02d}" for month in range(1, 13) for name in CLIMATE_NAMES]
    tails = [*climate.reshape(len(cells), -1).T, climate[:, 5:8, 0].mean(axis=1), climate[:, :, 1].mean(axis=1), climate[:, 5:8, 2].mean(axis=1)]
    for name, values in zip([*months, *CLIMATE_NAMES], tails):
        own = facts[column[name]]
        weight = 0 if np.isnan(own) else one_way
        facts[column[name]] = ((own * weight if weight else 0.0) + values.sum()) / (weight + len(values))


def _amenities(geo: sqlite3.Connection) -> tuple[np.ndarray, np.ndarray]:
    """Kind and (lat, lon) of every GeometryStore amenity point."""
    rows = geo.execute("SELECT kind, latitude, longitude FROM amenities").fetchall()
    kinds = np.array([kind for kind, _, _ in rows], dtype=object)
    points = np.array([(lat, lon) for _, lat, lon in rows], dtype=float).reshape(-1, 2)
    return kinds, points


class AmenityPoints:
    """Amenity points ((lat, lon) rows and their kinds) with a haversine BallTree."""

    def __init__(self, points: np.ndarray, kinds: Sequence[str]) -> None:
        self.points = np.asarray(points, dtype=float).reshape(-1, 2)
        self.kinds = np.asarray(kinds, dtype=object)
        # An empty tree still answers queries: one dummy point at the South Pole.
        self.tree = BallTree(
            np.radians(self.points if len(self.points) else np.array([[-90.0, 0.0]])),
            metric="haversine",
        )

    def near_line(
        self, lat: np.ndarray, lon: np.ndarray, line: np.ndarray, radius_m: float
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Kind, distance to the line and position along it of the points that
        may lie within ``radius_m`` of the line (filter on the distance).

        ``line`` points (radians) are at most ``SEARCH_STEP_M`` apart, so every
        point within ``radius_m`` of the line is within hypot(radius, step / 2)
        of one of them.
        """
        reach = math.hypot(radius_m, SEARCH_STEP_M / 2) + 1.0
        hits = self.tree.query_radius(line, r=reach / EARTH_RADIUS_M)
        hits = np.unique(np.concatenate(hits)).astype(int)
        hits = hits[hits < len(self.points)]
        distance, position = nearest_on_line(
            lat, lon, self.points[hits, 0], self.points[hits, 1]
        )
        return self.kinds[hits], distance, position


def _walks(
    geo: sqlite3.Connection, first_id: int, last_id: int, has_landcover: bool = False
) -> dict[int, tuple[np.ndarray, np.ndarray, np.ndarray, list[tuple[float, str | None]], np.ndarray]]:
    """DayHikeGeometry (lat, lon, ele), walked ways and per-vertex WorldCover
    class (0 missing, and without ``has_landcover``) for DayHikes in an id range.

    Ways are (walked metres, surface class) in walk order; Leftovers are out.
    """
    vertices = geo.execute(
        f"SELECT dayhike_id, latitude, longitude, ele, {'landcover' if has_landcover else 'NULL'} FROM vertices "
        "WHERE dayhike_id BETWEEN ? AND ? ORDER BY dayhike_id, seq",
        (first_id, last_id),
    ).fetchall()
    ways: dict[int, list[tuple[float, str | None]]] = {}
    for dayhike_id, walked_m, tags in geo.execute(
        "SELECT dayhike_id, walked_m, tags FROM ways WHERE dayhike_id BETWEEN ? AND ? "
        "AND walk_order IS NOT NULL ORDER BY dayhike_id, walk_order",
        (first_id, last_id),
    ):
        ways.setdefault(dayhike_id, []).append(
            (float(walked_m), surface_class(json.loads(tags)))
        )
    if not vertices:
        return {}
    table = np.array(
        [(i, lat, lon, math.nan if ele is None else ele, code or 0) for i, lat, lon, ele, code in vertices],
        dtype=float,
    )
    hike_ids, starts = np.unique(table[:, 0], return_index=True)
    ends = np.append(starts[1:], len(table))
    return {
        int(dayhike_id): (
            table[start:end, 1],
            table[start:end, 2],
            table[start:end, 3],
            ways.get(int(dayhike_id), []),
            table[start:end, 4].astype(np.int64),
        )
        for dayhike_id, start, end in zip(hike_ids, starts, ends)
    }


def walk_facts(
    lat: np.ndarray,
    lon: np.ndarray,
    ele: np.ndarray,
    walked: Sequence[tuple[float, str | None]],
    along: AmenityPoints,
    peaks: AmenityPoints,
) -> dict[str, float]:
    """Facts of one DayHike from its DayHikeGeometry and its walked ways.

    ``lat``, ``lon``, ``ele`` are the vertices in walk order (NaN ele
    missing); ``walked`` is (metres, surface class) per walked way in walk
    order. ``along`` holds the ``ALONG_KINDS`` points, ``peaks`` the Peaks.
    Along the line (distance to the line, not to its vertices): a Hut,
    restaurant or cafe, Shelter or Viewpoint in the ``CORRIDOR_M`` Corridor,
    a Peak within ``PEAK_SUMMITED_M``, the Peaks within ``PEAKS_AROUND_M``,
    and the WaterSources in the Corridor with the DryStretch between them.
    None of the facts depend on the walking direction.
    """
    round_trip = _haversine_m(lat[0], lon[0], lat[-1], lon[-1]) <= GAP_JOIN_M
    facts: dict[str, float] = {
        "elevation_gain_m": elevation_gain(lat, lon, ele),
        "round_trip": float(round_trip),
        **dict(zip(("climb_grade", "steepest_km_m"), steepness(lat, lon, ele))),
        **trail_facts(walked, loop=round_trip),
    }
    kinds, distance, position = np.empty(0, dtype=object), np.empty(0), np.empty(0)
    peak_distance = np.empty(0)
    length = _path_length_m(lat, lon)
    if len(lat) >= 2:
        line = np.radians(_densify(lat, lon, SEARCH_STEP_M))
        kinds, distance, position = along.near_line(lat, lon, line, CORRIDOR_M)
        _, peak_distance, _ = peaks.near_line(lat, lon, line, PEAKS_AROUND_M)

    def within(kind: str, radius_m: float) -> np.ndarray:
        return position[(kinds == kind) & (distance <= radius_m)]

    water = np.concatenate([within(kind, CORRIDOR_M) for kind in WATER_SOURCE_KINDS])
    edges = np.concatenate([[0.0], np.sort(water), [length]])
    facts.update(
        hut_along=float(within("hut", CORRIDOR_M).size > 0),
        restaurant_cafe_along=float(within("restaurant_cafe", CORRIDOR_M).size > 0),
        shelter_along=float(within("shelter", CORRIDOR_M).size > 0),
        viewpoint_along=float(within("viewpoint", CORRIDOR_M).size > 0),
        peak_summited=float(within("peak", PEAK_SUMMITED_M).size > 0),
        peaks_around=float((peak_distance <= PEAKS_AROUND_M).sum()),
        water_count=float(water.size) if len(lat) >= 2 else math.nan,
        dry_stretch_m=float(np.diff(edges).max()) if len(lat) >= 2 else math.nan,
    )
    return facts


def nearest_on_line(
    chain_lat: Sequence[float],
    chain_lon: Sequence[float],
    point_lat: Sequence[float],
    point_lon: Sequence[float],
) -> tuple[np.ndarray, np.ndarray]:
    """Distance (m) from each point to a vertex chain, and the position of the
    closest place on the chain (metres from the first vertex).

    Closeness is measured on a flat plane around the chain; positions use
    haversine along the chain, so they add up to the chain length.
    """
    lat = np.asarray(chain_lat, dtype=float)
    lon = np.asarray(chain_lon, dtype=float)
    plat = np.asarray(point_lat, dtype=float)
    plon = np.asarray(point_lon, dtype=float)
    if plat.size == 0 or lat.size < 2:
        return np.empty(0), np.empty(0)
    segment = _step_m(lat, lon)
    start = np.concatenate([[0.0], np.cumsum(segment)])
    lat0, lon0 = lat.mean(), lon.mean()
    scale = math.cos(math.radians(lat0))
    x = np.radians(lon - lon0) * EARTH_RADIUS_M * scale
    y = np.radians(lat - lat0) * EARTH_RADIUS_M
    px = np.radians(plon - lon0) * EARTH_RADIUS_M * scale
    py = np.radians(plat - lat0) * EARTH_RADIUS_M
    dx, dy = np.diff(x), np.diff(y)
    flat = dx**2 + dy**2
    # Share of each segment, per (point, segment), clipped to the segment.
    along = np.clip(
        ((px[:, None] - x[:-1]) * dx + (py[:, None] - y[:-1]) * dy)
        / np.where(flat > 0, flat, 1.0),
        0.0,
        1.0,
    )
    miss = np.hypot(x[:-1] + along * dx - px[:, None], y[:-1] + along * dy - py[:, None])
    nearest = miss.argmin(axis=1)
    rows = np.arange(len(px))
    return (
        miss[rows, nearest],
        start[nearest] + along[rows, nearest] * segment[nearest],
    )


def _densify(lat: np.ndarray, lon: np.ndarray, step_m: float) -> np.ndarray:
    """(lat, lon) rows in degrees with points inserted so that consecutive
    points are at most ``step_m`` apart.
    """
    pieces = np.maximum(1, np.ceil(_step_m(lat, lon) / step_m)).astype(int)
    start = np.repeat(np.arange(len(pieces)), pieces)
    along = (np.arange(pieces.sum()) - np.repeat(np.cumsum(pieces) - pieces, pieces)) / (
        pieces[start]
    )
    dense_lat = lat[start] + along * (lat[start + 1] - lat[start])
    dense_lon = lon[start] + along * (lon[start + 1] - lon[start])
    return np.column_stack([np.append(dense_lat, lat[-1]), np.append(dense_lon, lon[-1])])


def _even_points(lat: np.ndarray, lon: np.ndarray, step_m: float) -> np.ndarray:
    """(lat, lon) rows at equal distance steps of about ``step_m`` along the
    vertices, both ends included.
    """
    distance = np.concatenate([[0.0], np.cumsum(_step_m(lat, lon))])
    at = np.linspace(0.0, distance[-1], max(2, round(distance[-1] / step_m) + 1))
    return np.column_stack([np.interp(at, distance, lat), np.interp(at, distance, lon)])


def elevation_gain(lat: np.ndarray, lon: np.ndarray, ele: np.ndarray) -> float:
    """Metres climbed: the larger of the climb and the descent (see
    ``climb_and_descent``), so the walking direction does not matter. NaN
    when no vertex has elevation.
    """
    return max(climb_and_descent(lat, lon, ele))


def climb_and_descent(lat: np.ndarray, lon: np.ndarray, ele: np.ndarray) -> tuple[float, float]:
    """Metres climbed and metres descended along the vertices in walk order.

    Vertices without elevation are skipped; none with it gives NaN for both.
    First a DEM spike is dropped: a vertex at least ``SPIKE_RISE_M`` above
    or below both its neighbours with a grade of at least ``SPIKE_GRADE`` on
    both sides (the ends stay). Then a change counts only once it moves
    ``GAIN_HYSTERESIS_M`` from the last counted elevation, so DEM jitter
    between vertices does not add up on long flat or cross-slope walks.
    """
    ele = np.asarray(ele, dtype=float)
    known = np.isfinite(ele)
    along = np.concatenate([[0.0], np.cumsum(_step_m(np.asarray(lat, float), np.asarray(lon, float)))])
    kept: list[tuple[float, float]] = []  # (metres along, elevation)
    for here in zip(along[known].tolist(), ele[known].tolist()):
        while len(kept) >= 2 and _is_spike(kept[-2], kept[-1], here):
            kept.pop()
        kept.append(here)
    if not kept:
        return math.nan, math.nan
    climb = descent = 0.0
    last = kept[0][1]
    for _, metres in kept[1:]:
        if metres - last >= GAIN_HYSTERESIS_M:
            climb += metres - last
            last = metres
        elif last - metres >= GAIN_HYSTERESIS_M:
            descent += last - metres
            last = metres
    return climb, descent


def _is_spike(before: tuple[float, float], vertex: tuple[float, float], after: tuple[float, float]) -> bool:
    rise, fall = vertex[1] - before[1], vertex[1] - after[1]
    return (
        rise * fall > 0
        and min(abs(rise), abs(fall)) >= SPIKE_RISE_M
        and abs(rise) >= SPIKE_GRADE * (vertex[0] - before[0])
        and abs(fall) >= SPIKE_GRADE * (after[0] - vertex[0])
    )


def profile_shape(
    latitude: Sequence[float],
    longitude: Sequence[float],
    elevation: Sequence[float],
) -> np.ndarray | None:
    """ProfileShape of one ElevationProfile given as vertices in walk order.

    Elevation is resampled at ``PROFILE_POINTS`` equal steps of the
    cumulative-distance fraction 0 to 1 (haversine along every vertex), then the
    minimum is subtracted and the result divided by max(range,
    ``PROFILE_RANGE_FLOOR_M``), so a nearly flat DayHike stays nearly flat.
    Vertices without elevation are skipped; fewer than two with elevation gives
    None.
    """
    resampled = _resample(latitude, longitude, elevation, points=PROFILE_POINTS)
    if resampled is None:
        return None
    low = resampled.min()
    return (resampled - low) / max(resampled.max() - low, PROFILE_RANGE_FLOOR_M)


def steepness(
    latitude: Sequence[float], longitude: Sequence[float], elevation: Sequence[float]
) -> tuple[float, float]:
    """ClimbGrade and SteepestKm (metres) of one DayHike, NaN without elevation.

    Elevation is resampled about every ``STEEPNESS_STEP_M`` along the walk.
    ClimbGrade is the gain over the length of the steps that climb, in the
    walking direction with the larger climb. SteepestKm is the largest height
    change between two points ``STEEPEST_KM_WINDOW_M`` apart; a shorter walk
    uses its two ends. Fewer than two vertices with elevation gives NaN.
    """
    length = _path_length_m(np.asarray(latitude, float), np.asarray(longitude, float))
    points = max(2, round(length / STEEPNESS_STEP_M) + 1)
    resampled = _resample(latitude, longitude, elevation, points=points)
    if resampled is None:
        return math.nan, math.nan
    spacing = length / (points - 1)
    rise = np.diff(resampled)
    up, down = rise[rise > 0], -rise[rise < 0]
    climb = up if up.sum() >= down.sum() else down
    window = min(round(STEEPEST_KM_WINDOW_M / spacing), points - 1)
    return (
        float(climb.sum() / (climb.size * spacing)) if climb.size else 0.0,
        float(np.abs(resampled[window:] - resampled[:-window]).max()),
    )


def _resample(
    latitude: Sequence[float],
    longitude: Sequence[float],
    elevation: Sequence[float],
    *,
    points: int,
) -> np.ndarray | None:
    """Elevation at ``points`` equal distance steps along the vertices."""
    lat = np.asarray(latitude, dtype=float)
    lon = np.asarray(longitude, dtype=float)
    ele = np.asarray(elevation, dtype=float)
    distance = np.concatenate([[0.0], np.cumsum(_step_m(lat, lon))])
    has_ele = np.isfinite(ele)
    if has_ele.sum() < 2 or distance[-1] <= 0:
        return None
    at, ele = distance[has_ele], ele[has_ele]
    keep = np.concatenate([[True], np.diff(at) > 0])
    return np.interp(np.linspace(0.0, distance[-1], points), at[keep], ele[keep])


def surface_class_from_tag(tags: Mapping[str, str]) -> str | None:
    """Surface class (paved, gravel, natural, rock) of a way's ``surface`` tag.

    The first ``;`` token is used, lowercased; ``concrete:*`` counts as
    concrete. A missing or unlisted surface gives None.
    """
    token = tags.get("surface", "").split(";")[0].strip().lower()
    if token.startswith("concrete:"):
        token = "concrete"
    return _SURFACE_TAG_CLASS.get(token)


def surface_class(tags: Mapping[str, str]) -> str | None:
    """Surface class of a way: its surface tag, else its highway kind.

    Roads are paved; a track follows its tracktype (grade1 paved, grade2-3
    gravel, grade4-5 natural, none or other gravel); path, footway, steps and
    bridleway are natural; via_ferrata is rock. Anything else is None (left
    out of the SurfaceMix denominator).
    """
    from_tag = surface_class_from_tag(tags)
    if from_tag is not None:
        return from_tag
    highway = tags.get("highway", "")
    if highway.removesuffix("_link") in _ROAD_HIGHWAYS:
        return "paved"
    if highway == "track":
        return _TRACKTYPE_CLASS.get(tags.get("tracktype", ""), "gravel")
    if highway in ("path", "footway", "steps", "bridleway"):
        return "natural"
    if highway == "via_ferrata":
        return "rock"
    return None


def trail_facts(walked: Sequence[tuple[float, str | None]], *, loop: bool) -> dict[str, float]:
    """SurfaceMix shares and TarmacApproach of the walked ways, in walk order.

    ``walked`` is (metres walked, surface class) per way. SurfaceMix is four
    length fractions over the classified ways; unclassified ways are left out
    of the denominator. TarmacApproach is the paved metres from the start of
    the walk until the first way that is not paved (an unclassified way ends
    it). On a ``loop`` the shorter of the two directions counts. Everything is
    NaN when no walked way is classified.
    """
    classified = [(length, kind) for length, kind in walked if kind is not None]
    total = sum(length for length, _ in classified)
    if total <= 0:
        return dict.fromkeys((*SURFACE_SHARE_COLUMNS, "tarmac_approach_m"), math.nan)
    approaches = [_paved_run(walked)]
    if loop:
        approaches.append(_paved_run(walked[::-1]))
    return {
        **{
            f"{kind}_share": sum(length for length, k in classified if k == kind) / total
            for kind in ("paved", "gravel", "natural", "rock")
        },
        "tarmac_approach_m": min(approaches),
    }


def _paved_run(walked: Sequence[tuple[float, str | None]]) -> float:
    run = 0.0
    for length, kind in walked:
        if kind != "paved":
            break
        run += length
    return run


def solar_aspect_points(count: int) -> np.ndarray:
    """Vertex positions sampled for SolarAspect: about ``SOLAR_ASPECT_POINTS``,
    every k-th counted from the nearer end, so either walking direction picks
    the same set.
    """
    position = np.arange(count)
    step = max(1, math.ceil(count / SOLAR_ASPECT_POINTS))
    return position[np.minimum(position, count - 1 - position) % step == 0]


def solar_aspect_southness(
    dem_dir: Path | str, points: np.ndarray, owners: np.ndarray, n: int
) -> np.ndarray:
    """SolarAspect southness per DayHike row: 1 due south, 0 due north, 0.5
    east or west, the mean over its sloped sample points.

    ``points`` are (lat, lon) rows chosen by ``solar_aspect_points``;
    ``owners`` gives each point's DayHike row, out of ``n`` rows.

    At each point FABDEM is read at the point and ``SOLAR_ASPECT_OFFSET_ARCSEC``
    east and north of it; the downslope direction gives (1 - its north
    component) / 2. Points flatter than ``SOLAR_ASPECT_MIN_SLOPE_DEG`` or
    without a height are left out; a DayHike with none left is NaN.
    """
    southness = slope_southness(dem_dir, points[:, 0], points[:, 1])
    sloped = ~np.isnan(southness)
    count = np.bincount(owners[sloped], minlength=n)
    total = np.bincount(owners[sloped], weights=southness[sloped], minlength=n)
    with np.errstate(invalid="ignore"):
        return np.where(count > 0, total / count, np.nan)


def slope_southness(dem_dir: Path | str, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Southness of the slope at each point (see ``solar_aspect_southness``),
    NaN where it is flatter than ``SOLAR_ASPECT_MIN_SLOPE_DEG`` or has no height.
    """
    offset = SOLAR_ASPECT_OFFSET_ARCSEC / 3600
    lat, lon = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
    here, east, north = dem_heights(
        dem_dir,
        np.concatenate([lat, lat, lat + offset]),
        np.concatenate([lon, lon + offset, lon]),
    ).reshape(3, -1)
    north_m = math.radians(offset) * EARTH_RADIUS_M
    east_m = north_m * np.cos(np.radians(lat))
    rise_east = (east - here) / east_m
    rise_north = (north - here) / north_m
    grade = np.hypot(rise_east, rise_north)
    with np.errstate(invalid="ignore", divide="ignore"):
        sloped = np.degrees(np.arctan(grade)) >= SOLAR_ASPECT_MIN_SLOPE_DEG
        # Downslope points against the gradient; its north component is
        # -rise_north / grade, so a slope rising northward faces south.
        return np.where(sloped, (1 + rise_north / grade) / 2, np.nan)


def waterside_share(
    landcover_dir: Path | str, points: np.ndarray, owners: np.ndarray, n: int
) -> np.ndarray:
    """WaterSide per DayHike row: the share of its probe points beside open water.

    ``points`` are (lat, lon) rows every ``WATERSIDE_STEP_M`` along each walk;
    ``owners`` gives each point's DayHike row, out of ``n`` rows. A point is
    beside water when at least ``WATERSIDE_MIN_SHARE`` of the WorldCover cells
    within ``WATERSIDE_M`` of it (a square, read at ``WATERSIDE_OVERVIEW`` times
    the 10 m cells and cut at the tile edge) are permanent water: sea, lakes,
    wide rivers. Points without a tile are left out; a DayHike with none left
    is NaN.
    """
    beside = np.full(len(points), np.nan)
    lat, lon = points[:, 0], points[:, 1]
    corners = np.column_stack([np.floor(lat / 3), np.floor(lon / 3)]).astype(int) * 3
    unique, tiles = np.unique(corners, axis=0, return_inverse=True)
    order = np.argsort(tiles.ravel(), kind="stable")
    bounds = np.searchsorted(tiles.ravel()[order], np.arange(len(unique) + 1))
    for tile, (south, west) in enumerate(unique):
        water = WaterTile.open(landcover_dir, south, west)
        if water is None:
            continue
        at = order[bounds[tile] : bounds[tile + 1]]
        rows, cols = water.cells(lat[at], lon[at])
        beside[at] = water.beside(rows, cols, lat[at])
    known = ~np.isnan(beside)
    count = np.bincount(owners[known], minlength=n)
    total = np.bincount(owners[known], weights=beside[known], minlength=n)
    with np.errstate(invalid="ignore"):
        return np.where(count > 0, total / count, np.nan)


@dataclass(frozen=True)
class WaterTile:
    """One WorldCover tile read at ``WATERSIDE_OVERVIEW`` times its cells, for WaterSide.

    ``summed`` is the summed-area table of its permanent-water cells, so the
    water cells in any window are four reads.
    """

    transform: object  # rasterio Affine of the overview grid
    height: int
    width: int
    summed: np.ndarray

    @classmethod
    def open(cls, landcover_dir: Path | str, south: int, west: int) -> WaterTile | None:
        """The tile whose southwest corner is (south, west), or None without one."""
        import rasterio

        tile_id = worldcover_tile_id(south, west)
        path = Path(landcover_dir) / f"ESA_WorldCover_10m_2021_v200_{tile_id}_Map.tif"
        if not path.is_file():
            return None
        with rasterio.open(path) as dataset:
            height = dataset.height // WATERSIDE_OVERVIEW
            width = dataset.width // WATERSIDE_OVERVIEW
            water = dataset.read(1, out_shape=(height, width)) == WORLDCOVER_WATER
            transform = dataset.transform @ rasterio.Affine.scale(
                dataset.width / width, dataset.height / height
            )
        summed = np.zeros((height + 1, width + 1), dtype=np.int32)
        summed[1:, 1:] = water.cumsum(axis=0, dtype=np.int32).cumsum(axis=1, dtype=np.int32)
        return cls(transform, height, width, summed)

    def cells(self, lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """The overview row and column of each point (clipped to the grid)."""
        import rasterio

        rows, cols = rasterio.transform.rowcol(self.transform, lon, lat)
        return (
            np.clip(np.atleast_1d(np.asarray(rows)), 0, self.height - 1),
            np.clip(np.atleast_1d(np.asarray(cols)), 0, self.width - 1),
        )

    def beside(self, rows: np.ndarray, cols: np.ndarray, lat: np.ndarray) -> np.ndarray:
        """Whether each cell is beside open water, seen from a point at ``lat``:
        at least ``WATERSIDE_MIN_SHARE`` of the cells within ``WATERSIDE_M``
        (a square cut at the tile edge) are water.
        """
        cell_m = math.radians(abs(self.transform.e)) * EARTH_RADIUS_M
        reach_rows = round(WATERSIDE_M / cell_m)
        reach_cols = np.round(WATERSIDE_M / (cell_m * np.cos(np.radians(lat)))).astype(int)
        top, bottom = np.maximum(rows - reach_rows, 0), np.minimum(rows + reach_rows + 1, self.height)
        left, right = np.maximum(cols - reach_cols, 0), np.minimum(cols + reach_cols + 1, self.width)
        summed = self.summed
        count = summed[bottom, right] - summed[top, right] - summed[bottom, left] + summed[top, left]
        return count >= WATERSIDE_MIN_SHARE * (bottom - top) * (right - left)


def twin_groups(routes: Sequence[np.ndarray | None]) -> np.ndarray:
    """First row of each DayHike's group of Twins (its own row without one).

    ``routes`` holds per DayHike row its walk as (lat, lon) rows every
    ``TWIN_STEP_M``, or None. Two DayHikes are Twins (the same route, walked
    either way) when at least ``TWIN_SHARE`` of each one's points lie within
    ``TWIN_M`` of the other's line; Twins of Twins join one group. Only pairs
    whose lengths and point centres allow that share are compared.
    """
    parent = np.arange(len(routes))

    def root(row: int) -> int:
        while parent[row] != row:
            parent[row] = parent[parent[row]]
            row = parent[row]
        return row

    have = [row for row, route in enumerate(routes) if route is not None]
    if not have:
        return parent
    length = np.array([_path_length_m(routes[row][:, 0], routes[row][:, 1]) for row in have])
    centre = np.radians([routes[row].mean(axis=0) for row in have])
    # Twins' centres lie within the off-route share of the longer walk.
    reach = np.maximum(TWIN_M, (1 - TWIN_SHARE) * length / TWIN_SHARE) / EARTH_RADIUS_M
    near = BallTree(centre, metric="haversine").query_radius(centre, r=reach)

    for i, others in enumerate(near):
        for j in others[others > i]:
            if min(length[i], length[j]) < TWIN_SHARE * max(length[i], length[j]):
                continue
            if are_twins(routes[have[i]], routes[have[j]]):
                first, second = sorted((root(have[i]), root(have[j])))
                parent[second] = first
    return np.array([root(row) for row in range(len(routes))])


def are_twins(a: np.ndarray, b: np.ndarray) -> bool:
    """Whether two walks, as (lat, lon) rows every ``TWIN_STEP_M``, are Twins:
    at least ``TWIN_SHARE`` of each one's points lie within ``TWIN_M`` of the
    other's line.
    """

    def share_on(a: np.ndarray, b: np.ndarray) -> float:
        distance, _ = nearest_on_line(b[:, 0], b[:, 1], a[:, 0], a[:, 1])
        return float((distance <= TWIN_M).mean())

    return share_on(a, b) >= TWIN_SHARE and share_on(b, a) >= TWIN_SHARE


def dem_heights(dem_dir: Path | str, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """FABDEM height of the cell holding each point; NaN without a tile or data.

    Each covering tile is read once. A point in the half-cell margin outside
    its tile's grid reads the tile's edge cell.
    """
    import rasterio

    heights = np.full(len(lat), np.nan)
    corners = np.column_stack([np.floor(lat), np.floor(lon)]).astype(int)
    unique, tiles = np.unique(corners, axis=0, return_inverse=True)
    order = np.argsort(tiles.ravel(), kind="stable")
    bounds = np.searchsorted(tiles.ravel()[order], np.arange(len(unique) + 1))
    for tile, (south, west) in enumerate(unique):
        tile_id = fabdem_tile_id(south, west)
        path = Path(dem_dir) / f"{tile_id}_FABDEM_{FABDEM_VERSION}.tif"
        if not path.is_file():
            continue
        at = order[bounds[tile] : bounds[tile + 1]]
        with rasterio.open(path) as dataset:
            rows, cols = rasterio.transform.rowcol(dataset.transform, lon[at], lat[at])
            rows = np.clip(np.asarray(rows), 0, dataset.height - 1)
            cols = np.clip(np.asarray(cols), 0, dataset.width - 1)
            values = dataset.read(1)[rows, cols].astype(float)
            if dataset.nodata is not None:
                values[values == dataset.nodata] = np.nan
        heights[at] = values
    return heights


def _step_m(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Haversine metres between consecutive vertices."""
    lat, lon = np.radians(lat), np.radians(lon)
    return 2 * EARTH_RADIUS_M * np.arcsin(
        np.sqrt(
            np.sin(np.diff(lat) / 2) ** 2
            + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(np.diff(lon) / 2) ** 2
        )
    )


def _path_length_m(lat: np.ndarray, lon: np.ndarray) -> float:
    return float(_step_m(lat, lon).sum())


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return _path_length_m(np.array([lat1, lat2]), np.array([lon1, lon2]))
