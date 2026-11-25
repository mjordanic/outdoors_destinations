"""Bases: towns a Hiker stays in, described by the HikeTypes in Reach (ADR-0084)."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sqlite3
import warnings
from collections.abc import Callable, Collection
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, fields
from pathlib import Path

import numpy as np
from numba import njit
from scipy.spatial import cKDTree
from sklearn.cluster import KMeans

from dayhike.catalog import EARTH_RADIUS_M, Origin, straight_line_distances_m
from dayhike.db import scoped
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
from dayhike.serving import REGION_SPLIT_LON, ServingData, _fold
from dayhike.steps import file_key
from dayhike.town_counts import TownCounts
from dayhike.town_traits import MILD_C, TownTraits, trait_cuts, trait_passes

HIKE_TYPES = 512
HIKE_TYPES_SEED = 20261124
_SPACE_PAIRS = 100_000  # random pairs that set each Measure's unit in the HikeType space

REACHES = (15, 30, REACH_MIN)  # minutes; the stored pairs are in one band per Reach, the shortest first
BASE_POPULATION = 300  # a candidate Base is a GeoNames place of at least this many people,
BASE_STAYS = 3  # or with at least this many places to stay (``town_counts``)
MIN_HIKE_GROUPS = 10  # a Base has at least this many HikeGroups in the longest Reach
CELL_M = 1_000  # a HikeGroup's Trailhead cell
NEAR_M = 120_000  # Trailheads farther in a straight line are not asked about: an hour on a motorway
RESUME_CHUNK = 1_000  # candidates whose Reach is kept together in the work folder
_BASES_LAYOUT = "groups-band-major-runs-v1"
BASES_FORMAT = 2  # what a server reads in ``bases/``; a folder of another format is left out
_BASE_FACTS = (
    "population-geonames-zero-unknown",  # ``base_facts``
    "month-temperatures-median-of-hikes-twins-once",  # ``base_facts``
    "stays-eats-town-counts-nan-unknown",  # ``base_facts``
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
    ``stays`` and ``eats`` are its places to stay and to eat (``dayhike.town_counts``), NaN when
    not counted.
    """

    population: np.ndarray
    month_temperatures: np.ndarray
    stays: np.ndarray | None = None  # None: none counted
    eats: np.ndarray | None = None


def base_facts(
    store: SimilarityStore,
    places_path: Path,
    place_ids: np.ndarray,
    reached: list[tuple[np.ndarray, np.ndarray]],
    group_of_hike: np.ndarray,
    town: TownCounts | None = None,
) -> BaseFacts:
    """The ``BaseFacts`` of the Bases ``place_ids``, each with its HikeGroups and their minutes in ``reached``.

    A population of 0 is GeoNames' unknown, as is a place ``places_path`` has no
    population for. A HikeGroup in Reach brings all its hikes, each group of Twins
    once (as its first row); a hike with no temperature in a month is left out of that month.
    Places to stay and eat are ``town``'s (NaN for a place it has no count of, or without it).
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
    stays, eats = _counted(town, place_ids)
    return BaseFacts(population=population, month_temperatures=months, stays=stays, eats=eats)


def _counted(counts: TownCounts | None, place_ids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The places to stay and eat of ``place_ids`` in ``counts``, NaN where it has none."""
    found = []
    for values in ("stays", "eats"):
        known = np.full(len(place_ids), np.nan)
        if counts is not None and len(counts.place_ids):
            at = np.minimum(np.searchsorted(counts.place_ids, place_ids), len(counts.place_ids) - 1)
            hit = counts.place_ids[at] == place_ids
            known[hit] = getattr(counts, values)[at[hit]]
        found.append(known)
    return found[0], found[1]


@dataclass(frozen=True)
class BaseProfiles:
    """The BaseProfile of every Base under one HikeFilter and Reach.

    Base i's profile is ``types[starts[i]:starts[i + 1]]``, in HikeType order, with
    ``counts`` its HikeGroups of each; a HikeType with none is left out. ``groups`` is
    each Base's passing HikeGroups, the sum of its counts.
    """

    starts: np.ndarray
    types: np.ndarray
    counts: np.ndarray
    groups: np.ndarray


def base_profiles(bases: Bases, reach: int, hike_mask: np.ndarray) -> BaseProfiles:
    """Count, for every Base, the HikeGroups of each HikeType within ``reach`` minutes that pass.

    ``bases`` is the ``bases/`` folder (``Bases.open_dir``); ``hike_mask`` says of each
    hike (similarity row) whether it passes the HikeFilter. A HikeGroup passes when
    any of its hikes does, and counts once. One pass over the bands up to ``reach``: a
    shorter Reach never reads a longer one's band. A run of a HikeType whose HikeGroups all
    pass, or none, is counted from its length alone.
    """
    if reach not in REACHES:
        raise ValueError(f"reach must be one of {REACHES} minutes, not {reach}")
    hike_mask = np.asarray(hike_mask, dtype=np.bool_)
    if len(hike_mask) != len(bases.group_hikes):
        raise ValueError(f"hike_mask has {len(hike_mask)} hikes, the bases have {len(bases.group_hikes)}")
    starts, types, counts, groups = _profile_pass(
        REACHES.index(reach) + 1,
        len(bases.place_ids),
        len(bases.type_table),
        hike_mask,
        np.asarray(bases.group_starts),
        np.asarray(bases.group_hikes),
        np.asarray(bases.group_types),
        np.asarray(bases.base_runs),
        np.asarray(bases.run_offsets),
        np.asarray(bases.run_types),
        np.asarray(bases.pair_groups),
    )
    return BaseProfiles(starts=starts, types=types, counts=counts, groups=groups)


@njit
def _profile_pass(bands, bases, type_count, hike_mask, group_starts, group_hikes, group_types, base_runs, run_offsets, run_types, pair_groups):  # fmt: skip
    """``base_profiles``'s loop: which HikeGroups pass, then per Base the passing ones per HikeType.

    A Base's bands are summed per HikeType and read back in HikeType order from a bitmap of the
    HikeTypes seen, not sorted.
    """
    groups = len(group_starts) - 1
    passes = np.zeros(groups, dtype=np.int32)
    in_type = np.zeros(type_count, dtype=np.int64)
    passing_in_type = np.zeros(type_count, dtype=np.int64)
    for g in range(groups):
        for at in range(group_starts[g], group_starts[g + 1]):
            if hike_mask[group_hikes[at]]:
                passes[g] = 1
                break
        in_type[group_types[g]] += 1
        passing_in_type[group_types[g]] += passes[g]
    whole = np.zeros(type_count, dtype=np.int8)  # 1: every HikeGroup of the HikeType passes, -1: none, 0: some
    for kind in range(type_count):
        if passing_in_type[kind] == in_type[kind]:
            whole[kind] = 1
        elif passing_in_type[kind] == 0:
            whole[kind] = -1
    most = base_runs[bands * bases] - base_runs[0]  # every run in Reach: at most one HikeType each
    starts = np.empty(bases + 1, dtype=np.int64)
    types = np.empty(most, dtype=np.int16)
    counts = np.empty(most, dtype=np.int32)
    passing_groups = np.zeros(bases, dtype=np.int64)
    per_type = np.zeros(type_count, dtype=np.int64)
    words = (type_count + 63) // 64
    seen = np.zeros(words, dtype=np.uint64)  # the HikeTypes with a run in Reach of the Base, a bit each
    one = np.uint64(1)
    found = 0
    for base in range(bases):
        starts[base] = found
        for band in range(bands):
            slot = band * bases + base
            for run in range(base_runs[slot], base_runs[slot + 1]):
                kind = run_types[run]
                passing = (run_offsets[run + 1] - run_offsets[run]) * (whole[kind] == 1)
                if whole[kind] == 0:
                    for pair in range(run_offsets[run], run_offsets[run + 1]):
                        passing += passes[pair_groups[pair]]
                per_type[kind] += passing
                seen[kind >> 6] |= one << np.uint64(kind & 63)
        for word in range(words):  # in HikeType order: lowest bit first
            bits = seen[word]
            seen[word] = 0
            while bits:
                low = bits & (~bits + one)
                bits ^= low
                kind = word * 64 + _DE_BRUIJN[np.int64((low * _DE_BRUIJN_MAGIC) >> np.uint64(58))]
                types[found] = kind
                counts[found] = per_type[kind]
                passing_groups[base] += per_type[kind]
                found += per_type[kind] > 0
                per_type[kind] = 0
    starts[bases] = found
    return starts, types[:found], counts[:found], passing_groups


_DE_BRUIJN_MAGIC = np.uint64(0x03F79D71B4CB0A89)  # a single bit times this, top 6 bits: its place in _DE_BRUIJN
_DE_BRUIJN = np.array([
    0, 1, 48, 2, 57, 49, 28, 3, 61, 58, 50, 42, 38, 29, 17, 4, 62, 55, 59, 36, 53, 51, 43, 22, 45, 39, 33, 30, 24, 18,
    12, 5, 63, 47, 56, 27, 60, 41, 37, 16, 54, 35, 52, 21, 44, 32, 23, 11, 46, 26, 40, 15, 34, 20, 31, 10, 25, 14, 19, 9,
    13, 8, 7, 6,
], dtype=np.int64)  # fmt: skip


CREDIT_FROM = 0.6  # a HikeType score at or under this is no better than a random pair's: no credit
MIX_SHARE = 0.4  # of the hike score; coverage is the rest, half each way (ADR-0084)
PRESELECT_BASES = 20_000  # Bases, best by back times enough, that get a mix: the mix step is the slow one
BEST_HIKE_MATCHES = 6_000  # of those, best by mix and back, that get a coverage, and a row
AREA_REACHES = 2  # a Base this many Reaches from a better row joins it
OWN_AREA_REACHES = 3  # Bases this many Reaches from the Hiker's are where they have been
# What a BaseRanking's score means (ADR-0025), beside every row of "Find a base"; served in /api/info.
BASE_SCORE_HELP = {
    "overall": "How alike the hikes in reach of this town are to those in reach of yours, from 0 to 1. Higher "
    "is more alike: 1 is the same kinds of hike in the same shares.",
    "sections": (
        {
            "title": "Kinds of hike",
            "text": f"Every hike is given one of {HIKE_TYPES} kinds, made from the measures that compare two hikes: "
            "length, climb, height, landscape, water, weather and the rest. Two towns are compared on the kinds "
            "of hike they have in reach, not hike by hike; hikes that start close together and are of one kind "
            "count once.",
        },
        {
            "title": "How the score is made",
            "text": f"{round(MIX_SHARE * 100)} % of the score is the mix: how far the two towns have the same kinds "
            f"of hike in the same shares, near kinds counting as partly the same. {round((1 - MIX_SHARE) * 100)} % "
            "is coverage, half each way: how many of the kinds near your town this town has too, and how many of "
            "its kinds your town has. A town with far fewer of these hikes than yours is scored down.",
        },
        {
            "title": "Your hike filters and town traits",
            "text": "Only the hikes that pass your hike filters count, for both towns. A town trait set to "
            "“like mine” takes part of the score off a town unlike yours on it, more the more it "
            "matters, and the row says how much; a town with no data on a trait counts as half like yours.",
        },
        {
            "title": "One row per area",
            "text": "Towns close together have much the same hikes in reach, so each row is an area, named after "
            "its best town. Towns near your own are left out: that is where you have been.",
        },
    ),
}


@dataclass(frozen=True)
class BaseMatch:
    """One row of a BaseRanking: an area, named after its best Base.

    ``hike_score`` is ``(mix, coverage, back)`` weighed 40, 30, 30, times ``enough``;
    ``score`` the hike score after the "like mine" TownTraits, each taking its share in
    ``cuts`` (trait name, share) off; ``hike_groups`` the Base's HikeGroups that pass;
    ``members`` the Bases of its area, best first, ``base`` the first. Bases are rows of the
    ``bases/`` folder.
    """

    base: int
    place_id: int
    score: float
    hike_score: float
    mix: float
    coverage: float
    back: float
    enough: float
    hike_groups: int
    country: str
    region: str
    members: tuple[int, ...]
    cuts: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class BaseRanking:
    """The Bases whose hikes in ``reach`` are like those of the Hiker's Base ``seed``, best first.

    ``seed_groups`` is how many of the Hiker's HikeGroups pass the HikeFilter; with
    under ``MIN_HIKE_GROUPS`` of them there is nothing to compare and no row.
    """

    seed: int
    reach: int
    seed_groups: int
    rows: list[BaseMatch]

    @property
    def too_few(self) -> bool:
        """Too few of the Hiker's hikes pass the HikeFilter to rank."""
        return self.seed_groups < MIN_HIKE_GROUPS


def rank_bases(
    data: ServingData,
    seed: int,
    reach: int,
    hike_mask: np.ndarray,
    countries: Collection[str] | None = None,
    regions: Collection[str] | None = None,
    traits: TownTraits | None = None,
) -> BaseRanking:
    """The BaseRanking of every Base against the one at GeoNames place ``seed`` (ADR-0084).

    ``data`` is the ``ServingData`` (its ``bases`` and ``places``); ``hike_mask`` the HikeFilter,
    which both Bases are described under. Mix (shares of HikeTypes overlapping, each spread
    onto near HikeTypes by credit) and back (the other Base's HikeTypes finding a near one of
    the Hiker's) are scored for the ``PRESELECT_BASES`` best by back times enough of the Bases
    with at least ``MIN_HIKE_GROUPS`` passing HikeGroups, kept to ``countries`` (ISO codes) and
    ``regions`` (``REGIONS`` names) when given (back for all of them); coverage (the way round) only for the ``BEST_HIKE_MATCHES`` best of them with
    coverage taken as full. A Base with under half the Hiker's HikeGroups is scored down by
    the square root of the ratio. Bases within ``OWN_AREA_REACHES`` of the Hiker's are left
    out, and one within ``AREA_REACHES`` of a better row joins it, both in a straight line at
    the Reach estimate's straight-line speed. ``traits`` (all ignored when None) leave out the
    Bases that fail a TownTrait filter before the best are chosen, and their "like mine"
    settings then cut the hike score of those best (``town_traits``), on which the rows are
    ordered. Raises KeyError when ``seed`` is not a Base.
    """
    bases = data.bases
    at = np.flatnonzero(np.asarray(bases.place_ids) == seed)
    if not len(at):
        raise KeyError(seed)
    me = int(at[0])
    profiles = base_profiles(bases, reach, hike_mask)
    starts, types, counts, groups = profiles.starts, profiles.types, profiles.counts, profiles.groups
    if groups[me] < MIN_HIKE_GROUPS:
        return BaseRanking(seed=me, reach=reach, seed_groups=int(groups[me]), rows=[])

    credit, spread, blocks = _credit(bases)
    mine = slice(starts[me], starts[me + 1])
    my_types, my_shares = types[mine].astype(np.int64), counts[mine] / groups[me]
    points = np.asarray(bases.points, dtype=np.float64)
    reach_m = reach / 60 * STRAIGHT_SPEED_KMH * 1_000
    region = np.where(points[:, 1] < REGION_SPLIT_LON, "us", "europe")
    country = _base_countries(data)

    keep = groups >= MIN_HIKE_GROUPS
    keep &= straight_line_distances_m(Origin(*points[me]), points) > OWN_AREA_REACHES * reach_m
    if countries is not None:
        keep &= np.isin(country, list(countries))
    if regions is not None:
        keep &= np.isin(region, list(regions))
    traits = traits or TownTraits()
    town = {
        "size": np.asarray(bases.population),
        "stays": np.asarray(bases.stays),
        "eats": np.asarray(bases.eats),
        "months": np.asarray(bases.month_temperatures)[:, REACHES.index(reach)],
    }
    keep &= trait_passes(traits, town)
    candidates = np.flatnonzero(keep)
    my_best = credit[my_types].max(axis=0)
    back = _back(candidates, starts, types, counts, groups, my_best)
    enough = np.minimum(1.0, np.sqrt(groups[candidates] / (groups[me] / 2)))
    chosen = np.lexsort((candidates, -back * enough))[:PRESELECT_BASES]
    candidates, back, enough = candidates[chosen], back[chosen], enough[chosen]
    rest = (1 - MIX_SHARE) / 2
    mix = _mix_of_the_best(candidates, starts, types, counts, groups, my_shares @ spread[my_types], spread, blocks, back, enough)
    first = np.where(np.isnan(mix), -np.inf, (MIX_SHARE * mix + rest * back + rest) * enough)
    best = np.lexsort((candidates, -first))[:BEST_HIKE_MATCHES]
    candidates, mix, back, enough = candidates[best], mix[best], back[best], enough[best]
    near_first = np.argsort(-credit, axis=1, kind="stable")  # each HikeType's near ones, best first
    coverage = _coverage(candidates, starts, types, my_types, my_shares, credit, near_first, (credit > 0).sum(axis=1))
    hike_score = (MIX_SHARE * mix + rest * coverage + rest * back) * enough
    cuts = trait_cuts(traits, town, me, candidates)
    score = hike_score * np.prod([1 - cut for _, cut in cuts], axis=0) if cuts else hike_score

    order = np.lexsort((candidates, -score))
    area_of = _areas(np.radians(points[candidates[order]]), AREA_REACHES * reach_m)
    areas: list[list[int]] = [[] for _ in range(area_of.max(initial=-1) + 1)]
    for i, area in zip(order.tolist(), area_of.tolist()):
        areas[area].append(i)
    found = []
    for area in areas:
        i, base = area[0], int(candidates[area[0]])
        found.append(
            BaseMatch(
                base=base, place_id=int(bases.place_ids[base]), score=float(score[i]),
                hike_score=float(hike_score[i]), mix=float(mix[i]),
                coverage=float(coverage[i]), back=float(back[i]), enough=float(enough[i]),
                hike_groups=int(groups[base]), country=str(country[base]), region=str(region[base]),
                members=tuple(int(candidates[j]) for j in area),
                cuts=tuple((name, float(cut[i])) for name, cut in cuts),
            )  # fmt: skip
        )
    return BaseRanking(seed=me, reach=reach, seed_groups=int(groups[me]), rows=found)


@njit
def _areas(points, within_m):
    """``rank_bases``'s area of each Base, best first, at ``points`` (lat, lon in radians): that
    of the first earlier area lead within ``within_m`` metres in a straight line, else its own
    (the great-circle formula of ``straight_line_distances_m``)."""
    area_of = np.empty(len(points), dtype=np.int64)
    leads = np.empty((len(points), 2))
    count = 0
    for i in range(len(points)):
        phi0, lam0 = points[i, 0], points[i, 1]
        area_of[i] = -1
        for lead in range(count):
            a = math.sin((leads[lead, 0] - phi0) / 2) ** 2 + math.cos(phi0) * math.cos(leads[lead, 0]) * math.sin((leads[lead, 1] - lam0) / 2) ** 2
            if 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a)) <= within_m:
                area_of[i] = lead
                break
        if area_of[i] < 0:
            leads[count, 0], leads[count, 1] = phi0, lam0
            area_of[i] = count
            count += 1
    return area_of


_COUNTRIES: dict[int, tuple[Bases, np.ndarray]] = {}  # by the ``Bases`` read: each Base's country code


def _base_countries(data: ServingData) -> np.ndarray:
    """Each Base's ISO country code from ``data.places``, "" for none; read once per ``bases/`` folder."""
    kept = _COUNTRIES.get(id(data.bases))
    if kept is None or kept[0] is not data.bases:
        known = {}
        if data.places is not None:
            db = sqlite3.connect(data.places, uri=True)
            try:
                known = dict(db.execute("SELECT id, country FROM places"))
            finally:
                db.close()
        codes = np.array([known.get(int(place)) or "" for place in data.bases.place_ids], dtype="<U2")
        _COUNTRIES.clear()
        _COUNTRIES[id(data.bases)] = kept = (data.bases, codes)
    return kept[1]


def base_row(bases: Bases, place: int) -> int:
    """The row of the Base at GeoNames place ``place`` (Bases are in place id order); KeyError when none."""
    at = int(np.searchsorted(bases.place_ids, place))
    if at >= len(bases.place_ids) or bases.place_ids[at] != place:
        raise KeyError(place)
    return at


def describe_bases(data: ServingData, rows: Collection[int], reach: int | None = None) -> list[dict]:
    """Each Base of ``rows`` as the API names it: GeoNames place id, name and country (from
    ``data.places``, blank without it), population (None when unknown), where it is, and with
    ``reach`` the median temperature of its hikes within it each month (None when unknown)."""
    bases = data.bases
    places = [int(bases.place_ids[row]) for row in rows]
    known = {}
    if data.places is not None and places:
        with scoped(sqlite3.connect(data.places, uri=True)) as db:
            known = {
                place: (name, country)
                for place, name, country in db.execute(
                    f"SELECT id, name, country FROM places WHERE id IN ({','.join('?' * len(places))})", places
                )
            }
    found = []
    for row, place in zip(rows, places):
        name, country = known.get(place, ("", ""))
        population = float(bases.population[row])
        found.append(
            {
                "id": place,
                "name": name,
                "country": country or "",
                "population": None if math.isnan(population) else int(population),
                "lat": float(bases.points[row, 0]),
                "lon": float(bases.points[row, 1]),
            }
        )
        if reach is not None:
            months = np.asarray(bases.month_temperatures[row, REACHES.index(reach)], dtype=np.float64)
            found[-1]["months"] = [None if math.isnan(t) else round(float(t), 1) for t in months]
    return found


def find_bases(data: ServingData, query: str, limit: int) -> list[int]:
    """The Bases (rows) whose place has a name starting with ``query``, folded and ordered as
    ``ServingData.find_place`` does; places that are no Base are passed over."""
    needle = _fold(query).strip()
    if data.places is None or not needle:
        return []
    found = []
    with scoped(sqlite3.connect(data.places, uri=True)) as db:
        for (place,) in db.execute(
            "SELECT p.id FROM place_names n JOIN places p ON p.id = n.place WHERE n.name >= ? AND n.name < ? "
            "GROUP BY p.id ORDER BY MIN(CASE WHEN n.name = ? THEN n.alternate ELSE 2 END), p.population DESC, p.id",
            (needle, needle + "\U0010ffff", needle),
        ):
            try:
                found.append(base_row(data.bases, place))
            except KeyError:
                continue
            if len(found) == limit:
                break
    return found


def nearest_base(bases: Bases, origin: Origin) -> tuple[int, float]:
    """The Base (row) nearest ``origin`` in a straight line, and how far it is in metres."""
    metres = straight_line_distances_m(origin, np.asarray(bases.points, dtype=np.float64))
    row = int(np.argmin(metres))
    return row, float(metres[row])


def base_hikes(bases: Bases, base: int, reach: int, hike_mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    """The hikes (similarity rows) that pass ``hike_mask`` in the HikeGroups within ``reach``
    minutes of Base ``base``, each with its HikeGroup's minutes, nearest first, and how many
    HikeGroups pass (as ``base_profiles`` counts them)."""
    count = len(bases.place_ids)
    runs = np.asarray(bases.base_runs)
    offsets = np.asarray(bases.run_offsets)
    pairs = np.concatenate(
        [
            np.arange(offsets[runs[slot]], offsets[runs[slot + 1]])
            for slot in (band * count + base for band in range(REACHES.index(reach) + 1))
        ]
    )
    group_starts, group_hikes = np.asarray(bases.group_starts), np.asarray(bases.group_hikes)
    hikes, minutes, passing = [], [], 0
    for group, group_minutes in zip(np.asarray(bases.pair_groups)[pairs], np.asarray(bases.pair_minutes)[pairs]):
        members = group_hikes[group_starts[group] : group_starts[group + 1]]
        members = members[hike_mask[members]]
        if len(members):
            passing += 1
            hikes += members.tolist()
            minutes += [int(group_minutes)] * len(members)
    order = np.lexsort((hikes, minutes))
    return np.asarray(hikes, dtype=np.int64)[order], np.asarray(minutes, dtype=np.int64)[order], passing


@njit
def _back(candidates, starts, types, counts, groups, my_best):
    """``rank_bases``'s back of each candidate Base: its HikeTypes, by share, finding the Hiker's best credit."""
    back = np.zeros(len(candidates))
    for i in range(len(candidates)):
        base = candidates[i]
        for at in range(starts[base], starts[base + 1]):
            back[i] += counts[at] / groups[base] * my_best[types[at]]
    return back


MIX_BLOCKS = 64  # groups of HikeTypes whose spread shares bound a Base's mix from above
MIX_CHUNK = 2_000  # candidate Bases given a mix at a time, past the first BEST_HIKE_MATCHES

_CREDIT: dict[int, tuple[Bases, tuple[np.ndarray, np.ndarray, np.ndarray]]] = {}  # by the ``Bases`` read


def _credit(bases: Bases) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The credit between HikeTypes, each HikeType's credit spread (rows summing to 1), and
    ``MIX_BLOCKS`` groups of HikeTypes alike in spread (a group number each); once per ``bases/`` folder."""
    kept = _CREDIT.get(id(bases))
    if kept is None or kept[0] is not bases:
        table = np.asarray(bases.type_table)  # credit in its own float32: a stored 0.6 is no better than random
        credit = np.clip((table - table.dtype.type(CREDIT_FROM)) / (1 - CREDIT_FROM), 0, 1).astype(np.float64)
        spread = credit / credit.sum(axis=1, keepdims=True)
        count = min(MIX_BLOCKS, len(np.unique(spread, axis=0)))
        blocks = KMeans(n_clusters=count, n_init=1, random_state=HIKE_TYPES_SEED).fit(spread).labels_
        _CREDIT.clear()
        _CREDIT[id(bases)] = kept = (bases, (credit, spread, blocks.astype(np.int64)))
    return kept[1]


def _mix_of_the_best(candidates, starts, types, counts, groups, my_spread, spread, blocks, back, enough):  # fmt: skip
    """``rank_bases``'s mix of the candidates that may be among the ``BEST_HIKE_MATCHES`` best by
    mix, back and enough; NaN for the rest, which cannot be.

    A Base's mix is at most that of its spread shares summed in each group of ``blocks``
    against the Hiker's (the smaller of two sums is at least the sum of the smaller), so mix is
    worked out best bound first, until the next bound is under the ``BEST_HIKE_MATCHES``-th
    best worked out. Which Bases are the best does not depend on how good the groups are.
    """
    rest = (1 - MIX_SHARE) / 2
    one_hot = np.zeros((len(spread), blocks.max() + 1))
    one_hot[np.arange(len(spread)), blocks] = 1.0
    bound = _mix(candidates, starts, types, counts, groups, my_spread @ one_hot, spread @ one_hot)
    bound = (MIX_SHARE * np.minimum(bound, 1.0) + rest * back + rest) * enough + 1e-9  # past rounding
    order = np.argsort(-bound, kind="stable")
    mix = np.full(len(candidates), np.nan)
    done = 0
    while done < len(order):
        take = order[done : done + (BEST_HIKE_MATCHES if done == 0 else MIX_CHUNK)]
        mix[take] = _mix(candidates[take], starts, types, counts, groups, my_spread, spread)
        done += len(take)
        if done >= BEST_HIKE_MATCHES and done < len(order):
            first = (MIX_SHARE * mix[order[:done]] + rest * back[order[:done]] + rest) * enough[order[:done]]
            if bound[order[done]] < np.partition(first, done - BEST_HIKE_MATCHES)[done - BEST_HIKE_MATCHES]:
                break
    return mix


def _mix(candidates, starts, types, counts, groups, my_spread, spread):
    """``rank_bases``'s mix of each candidate Base against the Hiker's spread shares ``my_spread``:
    the Base's shares, each spread onto near HikeTypes by ``spread``, against the Hiker's. Only the
    HikeTypes in the Hiker's spread can add to it."""
    mine = my_spread > 0
    rows, cols = np.nonzero(spread * mine)
    indptr = np.searchsorted(rows, np.arange(len(spread) + 1))
    return _mix_pass(candidates, starts, types, counts, groups, my_spread, indptr, cols, spread[rows, cols], np.flatnonzero(mine))  # fmt: skip


@njit
def _mix_pass(candidates, starts, types, counts, groups, my_spread, indptr, indices, values, mine):
    """``_mix``'s loop; the spread is sparse, by HikeType (``indptr``), and ``mine`` the HikeTypes
    of the Hiker's spread."""
    mix = np.zeros(len(candidates))
    spread = np.zeros(len(my_spread))
    for i in range(len(candidates)):
        base = candidates[i]
        for at in range(starts[base], starts[base + 1]):
            share = counts[at] / groups[base]
            kind = types[at]
            for k in range(indptr[kind], indptr[kind + 1]):
                spread[indices[k]] += share * values[k]
        for near in mine:
            mix[i] += min(spread[near], my_spread[near])
        spread[:] = 0.0
    return mix


@njit
def _coverage(candidates, starts, types, my_types, my_shares, credit, near_first, near_count):
    """``rank_bases``'s coverage: each of the Hiker's HikeTypes, by share, finds its best credit near each Base.

    ``near_first`` is each HikeType's row of HikeTypes by credit, best first, of which the
    first ``near_count`` give any: the best credit is the first of them the Base has.
    """
    coverage = np.zeros(len(candidates))
    has = np.zeros(len(credit), dtype=np.bool_)
    for i in range(len(candidates)):
        base = candidates[i]
        for at in range(starts[base], starts[base + 1]):
            has[types[at]] = True
        for j in range(len(my_types)):
            kind = my_types[j]
            best = 0.0
            for k in range(near_count[kind]):
                near = near_first[kind, k]
                if has[near]:
                    best = credit[kind, near]
                    break
            coverage[i] += my_shares[j] * best
        for at in range(starts[base], starts[base + 1]):
            has[types[at]] = False
    return coverage


def export_bases(
    out_dir: Path | str,
    valhalla_url: str,
    *,
    workers: int = 1,
    chunk: int = RESUME_CHUNK,
    progress: Callable[[str], None] | None = None,
    town_counts: Path | str | None = None,
) -> Path:
    """Write ``bases/`` into the serving export ``out_dir``: the Bases and the HikeGroups in their Reach.

    Candidates are the ``places.sqlite`` places of ``BASE_POPULATION`` people or more, or with
    ``BASE_STAYS`` places to stay or more in the saved ``TownCounts`` at ``town_counts``; a
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
    - ``population``, ``month_temperatures``, ``stays``, ``eats``: each Base's ``BaseFacts``.
    - ``meta.json``, written last, so a folder without it is incomplete: the folder's format,
      the versions and build inputs it was made with and of which DayHikes (``Bases.open_dir``).

    Each ``chunk`` candidates' Reach is kept in ``bases.work/`` as it is found, so a
    stopped run started again with the same inputs goes on from there.
    """
    out = Path(out_dir)
    store = SimilarityStore.open_dir(out / "similarity")
    types = hike_types(store)
    trailheads = np.load(out / "trailheads.npy").reshape(-1, 2)
    group_of_hike, group_types = _hike_groups(types.types, store.twins, trailheads)
    counts = None if town_counts is None else TownCounts.load(town_counts)
    place_ids, places = _candidates(out / "places.sqlite", counts)
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
    facts = base_facts(store, out / "places.sqlite", place_ids[kept], reached, group_of_hike, counts)
    town = None if counts is None else {"radius_m": counts.radius_m, "sources": list(counts.sources)}
    target = _write_bases(
        out, place_ids[kept], places[kept], reached, group_of_hike, group_types, types, facts, store, inputs=key,
        town_counts=town,
    )  # fmt: skip
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


def _candidates(places_path: Path, counts: TownCounts | None = None) -> tuple[np.ndarray, np.ndarray]:
    """GeoNames ids and (lat, lon) of the places of ``BASE_POPULATION`` people or more, or with
    ``BASE_STAYS`` places to stay or more in ``counts``, by id."""
    db = sqlite3.connect(f"file:{places_path}?mode=ro", uri=True)
    try:
        rows = db.execute("SELECT id, lat, lon, population FROM places ORDER BY id").fetchall()
    finally:
        db.close()
    ids = np.array([row[0] for row in rows], dtype=np.int64)
    population = np.array([row[3] or 0 for row in rows], dtype=np.int64)
    stays, _ = _counted(counts, ids)
    keep = (population >= BASE_POPULATION) | (stays >= BASE_STAYS)
    return ids[keep], np.array([row[1:3] for row in rows], dtype=np.float64).reshape(-1, 2)[keep]


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
    inputs: str | None = None,
    town_counts: dict | None = None,
) -> Path:
    """Write the ``bases/`` folder (``export_bases``) to a partial folder, then put it in place."""
    count = len(place_ids)
    groups = np.concatenate([g for g, _ in reached]) if reached else np.zeros(0, dtype=np.int32)
    minutes = np.concatenate([m for _, m in reached]) if reached else np.zeros(0, dtype=np.uint8)
    base = np.repeat(np.arange(count, dtype=np.int32), [len(g) for g, _ in reached])
    band = np.searchsorted(REACHES[:-1], minutes, side="left").astype(np.int8)
    order = np.lexsort((groups, base, band))
    groups, minutes, base, band = groups[order], minutes[order], base[order], band[order]
    if len(groups) >= 2**31:
        raise ValueError(f"{len(groups):,} Base-HikeGroup pairs: more than the int32 run index holds")
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
        "run_offsets": np.r_[starts, len(groups)].astype(np.int32),  # pairs number under 2**31
        "run_types": pair_types[starts].astype(np.int16),
        "base_runs": np.searchsorted(slot[starts], np.arange(len(REACHES) * count + 1)).astype(np.int64),
        "type_table": types.table,
        "population": facts.population,
        "month_temperatures": facts.month_temperatures,
        "stays": np.full(count, np.nan) if facts.stays is None else np.asarray(facts.stays, dtype=np.float64),
        "eats": np.full(count, np.nan) if facts.eats is None else np.asarray(facts.eats, dtype=np.float64),
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
                [types.version, _BASES_LAYOUT, BASE_POPULATION, BASE_STAYS, MIN_HIKE_GROUPS, CELL_M, NEAR_M]
                + [_REACH_ESTIMATE]
                + [_BASE_FACTS]
            ).encode()
        ).hexdigest()[:16],
        "hike_types_version": types.version,
        "inputs": inputs,
        "facts_version": _FACTS_VERSION,
        "dayhikes": _dayhikes_digest(store.ids),
        "catalog_digest": store.catalog_digest,
        "town_counts": town_counts,
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
    stays: np.ndarray
    eats: np.ndarray
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
    def open_dir(cls, directory: Path | str, store: SimilarityStore) -> Bases | None:
        """The ``bases/`` folder at ``directory``, or None without a complete one, or with one
        of another ``BASES_FORMAT``, or made from other DayHikes or Catalog values than ``store``."""
        directory = Path(directory)
        meta_path = directory / "meta.json"
        if not meta_path.is_file():
            return None
        meta = json.loads(meta_path.read_text())
        if (
            meta.get("format") != BASES_FORMAT
            or meta.get("facts_version") != _FACTS_VERSION
            or meta.get("dayhikes") != _dayhikes_digest(store.ids)
            or meta.get("catalog_digest") != store.catalog_digest
        ):
            return None
        arrays = {field.name: map_npy(directory / f"{field.name}.npy") for field in fields(cls) if field.name != "meta"}
        return cls(meta=meta, **arrays)
