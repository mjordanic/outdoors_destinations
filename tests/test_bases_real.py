"""The base finder on the real Europe and US export: known pairs rank high, with and without HikeFilters.

Slow, opt-in (DAYHIKE_REAL_DATA=1); reads DAYHIKE_SERVING_DIR (default DAYHIKE_DATA_DIR/web-serving),
whose ``bases/`` folder was written by ``dayhike export-bases``.

The known pairs are the base-finder plan's: four seeds, each with hand-picked analogues, the other
seeds being its controls. An analogue's figure is the share of Bases it is ahead of: every Base in a
better row of its BaseRanking counts as ahead of it, and a Base outside the ranking's
``BEST_HIKE_MATCHES`` is behind it. A control or analogue that is no row of the ranking is below them.
Every seed is checked at 30 minutes, Moab at 60. Many analogues lie near their seed (Ambleside for
Keswick, Courmayeur for Chamonix, Tarvisio, Kranjska Gora and Bovec for Bohinj), within
``OWN_AREA_REACHES`` of it or in its region (``OWN_REGION_SHARE``), where the page hides Bases as where
the Hiker has been. Those rules decide what the page shows, not how alike two Bases are, so the
known-pairs test sets them off.
"""

from __future__ import annotations

import sqlite3
import statistics

import numpy as np
import pytest
import rebuild_checks as rc

import dayhike.bases as bases_module
from dayhike.bases import MIN_HIKE_GROUPS, BaseRanking, base_hikes, base_profiles, base_row, rank_bases
from dayhike.hike_filter import HikeFilter, hike_mask
from dayhike.serving import ServingData
from dayhike.town_traits import TownTraits, TraitSetting

pytestmark = rc.real_data

# A town: (name, country, (lat, lon) near it), the GeoNames place of that name nearest the point.
KESWICK = ("Keswick", "GB", (54.60, -3.13))
CHAMONIX = ("Chamonix-Mont-Blanc", "FR", (45.92, 6.87))
BOHINJ = ("Bohinjska Bistrica", "SI", (46.27, 13.95))
MOAB = ("Moab", "US", (38.57, -109.55))
SEEDS = (KESWICK, CHAMONIX, BOHINJ, MOAB)
ANALOGUES = {
    KESWICK: (
        ("Betws-y-Coed", "GB", (53.09, -3.81)),
        ("Llanberis", "GB", (53.12, -4.13)),
        ("Ambleside", "GB", (54.43, -2.96)),
        ("Fort William", "GB", (56.82, -5.11)),
        ("Killarney", "IE", (52.06, -9.51)),
        ("Aviemore", "GB", (57.20, -3.83)),
    ),
    CHAMONIX: (
        ("Zermatt", "CH", (46.02, 7.75)),
        ("Grindelwald", "CH", (46.62, 8.04)),
        ("Courmayeur", "IT", (45.80, 6.97)),
        ("Cortina d'Ampezzo", "IT", (46.54, 12.14)),
    ),
    BOHINJ: (
        ("Tarvisio", "IT", (46.51, 13.59)),
        ("Kranjska Gora", "SI", (46.46, 13.78)),
        ("Bovec", "SI", (46.34, 13.55)),
    ),
    MOAB: (
        ("Springdale", "US", (37.19, -113.00)),
        ("Kanab", "US", (37.05, -112.53)),
        ("Escalante", "US", (37.77, -111.60)),
        ("LaVerkin", "US", (37.20, -113.27)),
        ("Torrey", "US", (38.30, -111.42)),
    ),
}
# Moab at 60 min, as in the plan's known pairs: Springdale, Kanab and Escalante have fewer hikes than
# MIN_HIKE_GROUPS within 30 min.
REACH = {KESWICK: 30, CHAMONIX: 30, BOHINJ: 30, MOAB: 60}
# Both Alpine: the plan calls this control fair (98.6 there); measured 97.4-98.1 here.
ALIKE_SEEDS = {frozenset((CHAMONIX, BOHINJ))}
ANALOGUES_MEDIAN = 98.0  # the plan's prototype: 98.9 at 40 % mix

# Section 2.2 of the plan: each seed under its HikeFilters, with the countries its best rows lie in.
ALPS = {"FR", "IT", "CH", "AT", "DE", "SI", "LI"}
FAMILY = {KESWICK: {"GB", "IE", "IM", "NO", "IS", "FO"}, CHAMONIX: ALPS, BOHINJ: ALPS, MOAB: {"US"}}
FILTERS = {
    "climb up to 500 m": HikeFilter(climb_max_m=500),
    "5 to 15 km": HikeFilter(length_min_km=5, length_max_km=15),
    "climb 200 to 700 m": HikeFilter(climb_min_m=200, climb_max_m=700),
    "climb 800 m or more": HikeFilter(climb_min_m=800),
    "climb up to 500 m and up to 12 km": HikeFilter(climb_max_m=500, length_max_km=12),
}
FILTERED = (
    (KESWICK, "climb up to 500 m"),
    (KESWICK, "5 to 15 km"),
    (KESWICK, "climb 200 to 700 m"),
    (KESWICK, "climb 800 m or more"),
    (CHAMONIX, "climb up to 500 m"),
    (CHAMONIX, "5 to 15 km"),
    (CHAMONIX, "climb 200 to 700 m"),
    (CHAMONIX, "climb 800 m or more"),
    (CHAMONIX, "climb up to 500 m and up to 12 km"),
    (BOHINJ, "climb up to 500 m"),
    (BOHINJ, "5 to 15 km"),
    (BOHINJ, "climb 800 m or more"),
    (MOAB, "climb up to 500 m"),
    (MOAB, "5 to 15 km"),
)
FAMILY_ROWS = 5  # of the best rows, how many lie in the seed's countries
FILTERED_REACH = 30


@pytest.fixture(scope="module")
def data() -> ServingData:
    served = ServingData(rc.SERVING_DIR)
    assert served.bases is not None, f"{rc.SERVING_DIR}: no bases/ folder; run `dayhike export-bases`"
    return served


@pytest.fixture(scope="module")
def all_hikes(data: ServingData) -> np.ndarray:
    return hike_mask(data.store, HikeFilter())


def _place(data: ServingData, town: tuple) -> int:
    name, country, (lat, lon) = town
    with sqlite3.connect(f"file:{data.directory / 'places.sqlite'}?mode=ro", uri=True) as db:
        found = db.execute(
            "SELECT id FROM places WHERE name = ? AND country = ? ORDER BY (lat - ?) * (lat - ?) + (lon - ?) * (lon - ?)",
            (name, country, lat, lat, lon, lon),
        ).fetchone()
    assert found, f"{name}, {country}: not in places.sqlite"
    return int(found[0])


def _ahead_of(data: ServingData, ranking: BaseRanking, place: int, mask: np.ndarray) -> float | None:
    """The percentage of the ranked Bases a Base is ahead of; None when it is no row's."""
    profiles = base_profiles(data.bases, ranking.reach, mask)
    groups = np.diff(np.r_[0, np.cumsum(profiles.counts)][profiles.starts])
    eligible = int((groups >= MIN_HIKE_GROUPS).sum())
    base, better = base_row(data.bases, place), 0
    for row in ranking.rows:
        if base in row.members:
            return 100 * (1 - better / eligible)
        better += len(row.members)
    return None


def _ranking(data: ServingData, seed: tuple, reach: int, mask: np.ndarray, traits=None) -> BaseRanking:
    return rank_bases(data, _place(data, seed), reach, mask, traits=traits)


def test_known_analogues_rank_among_the_best_and_controls_do_not(
    data: ServingData, all_hikes: np.ndarray, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bases_module, "OWN_AREA_REACHES", 0)
    monkeypatch.setattr(bases_module, "OWN_REGION_SHARE", 1.0)
    analogues, controls, report = [], [], []
    for seed in SEEDS:
        ranking = _ranking(data, seed, REACH[seed], all_hikes)
        for town in ANALOGUES[seed]:
            figure = _ahead_of(data, ranking, _place(data, town), all_hikes)
            report.append(f"{seed[0]} {REACH[seed]} min: analogue {town[0]} {figure}")
            analogues.append(0.0 if figure is None else figure)
        for other in SEEDS:
            if other is seed:
                continue
            figure = _ahead_of(data, ranking, _place(data, other), all_hikes)
            report.append(f"{seed[0]} {REACH[seed]} min: control {other[0]} {figure}")
            if frozenset((seed, other)) not in ALIKE_SEEDS:
                controls.append(0.0 if figure is None else figure)
    print("\n".join(report))
    assert statistics.median(analogues) >= ANALOGUES_MEDIAN, report
    assert max(controls) < statistics.median(analogues), report


@pytest.mark.parametrize(("seed", "name"), FILTERED, ids=[f"{seed[0]}, {name}" for seed, name in FILTERED])
def test_a_seed_under_a_hike_filter_ranks_on_its_passing_hikes_alone(
    data: ServingData, all_hikes: np.ndarray, seed: tuple, name: str
) -> None:
    mask = hike_mask(data.store, FILTERS[name])
    base = base_row(data.bases, _place(data, seed))
    _, _, passing = base_hikes(data.bases, base, FILTERED_REACH, mask)
    _, _, every = base_hikes(data.bases, base, FILTERED_REACH, all_hikes)
    ranking = _ranking(data, seed, FILTERED_REACH, mask)
    unfiltered = _ranking(data, seed, FILTERED_REACH, all_hikes)

    assert ranking.seed_groups == passing < every
    assert not ranking.too_few and ranking.rows
    countries = [row.country for row in ranking.rows[:FAMILY_ROWS]]
    assert set(countries) <= FAMILY[seed], countries
    best = [row.base for row in ranking.rows[:10]]
    assert best != [row.base for row in unfiltered.rows[:10]], "the filter changes nothing at the top"
    for row in ranking.rows[:10]:
        _, _, groups = base_hikes(data.bases, row.base, FILTERED_REACH, mask)
        assert groups == row.hike_groups >= MIN_HIKE_GROUPS


def test_a_seed_with_too_few_passing_hikes_is_not_ranked(data: ServingData) -> None:
    mask = hike_mask(data.store, HikeFilter(climb_min_m=800))
    ranking = _ranking(data, MOAB, 15, mask)
    assert ranking.too_few and not ranking.rows


def test_size_like_mine_brings_up_towns_of_chamonix_size_at_a_small_hike_cost(
    data: ServingData, all_hikes: np.ndarray
) -> None:
    plain = _ranking(data, CHAMONIX, 30, all_hikes)
    sized = _ranking(data, CHAMONIX, 30, all_hikes, TownTraits(size=TraitSetting(like_mine=3)))
    population = np.asarray(data.bases.population)
    mine = population[base_row(data.bases, _place(data, CHAMONIX))]

    def typical(rows) -> float:
        return float(np.nanmedian([abs(np.log(population[row.base] / mine)) for row in rows[:10]]))

    assert typical(sized.rows) < typical(plain.rows) / 2
    assert sized.rows[0].hike_score >= plain.rows[0].hike_score - 0.08
    assert all(row.score >= row.hike_score / 2 for row in sized.rows)


def test_mild_months_of_the_seeds_are_their_hiking_seasons(data: ServingData) -> None:
    from dayhike.town_traits import MILD_C

    temperatures = np.asarray(data.bases.month_temperatures)
    seasons = {KESWICK: range(5, 11), CHAMONIX: range(6, 10), MOAB: (3, 4, 5, 9, 10)}
    for seed, months in seasons.items():
        t = temperatures[base_row(data.bases, _place(data, seed)), 1]  # 30 minutes
        mild = tuple(m + 1 for m in range(12) if MILD_C[0] <= t[m] <= MILD_C[1])
        assert set(months) <= set(mild), (seed[0], mild, np.round(t, 1).tolist())
        assert not {1, 12} & set(mild), (seed[0], mild)


def test_a_stays_filter_still_fills_a_page_of_rows(
    data: ServingData, all_hikes: np.ndarray, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``BEST_HIKE_MATCHES`` best leave a page of rows, or every row a longer list would have: Moab's
    few towns with 20 places to stay are what scores above a random pair's."""
    traits = TownTraits(stays=TraitSetting(at_least=20), eats=TraitSetting(at_least=20))
    for seed in SEEDS:
        ranking = _ranking(data, seed, 30, all_hikes, traits)
        with monkeypatch.context() as longer:
            longer.setattr(bases_module, "BEST_HIKE_MATCHES", bases_module.PRESELECT_BASES)
            every = _ranking(data, seed, 30, all_hikes, traits)
        assert len(ranking.rows) >= min(20, len(every.rows)), (seed[0], len(ranking.rows), len(every.rows))
        if seed is not MOAB:
            assert len(ranking.rows) >= 20, (seed[0], len(ranking.rows))
