"""BaseRanking (ADR-0084): hand-made BaseProfiles with a known order, one test per rule."""

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import dayhike.bases as bases_module
from dayhike.bases import REACHES, BaseFacts, Bases, HikeTypes, _write_bases, rank_bases

SEED_AT = (45.0, 7.0)
FAR = 1.0  # degrees of latitude between Bases that are not near: 111 km, past every Reach's area


def _data(
    tmp_path: Path, towns: list[dict], table: np.ndarray | None = None, types: int = 4, places: tuple = ()
) -> SimpleNamespace:
    """A serving export's ``bases`` and ``places`` from hand-made towns.

    Each town is ``{"types": {HikeType: HikeGroups}, "at": (lat, lon), "country": "GB"}``, its
    HikeGroups its own, one hike each, all 10 minutes away. Town i is Base i with place id i + 1.
    A town may give its ``population``, its 12 ``months`` (the same in every Reach) and its places
    to ``stays`` and ``eats``; unknown without. It may give its ``name`` ("P" without), the
    ``minutes`` to its own HikeGroups, and ``shares``: an earlier town whose HikeGroups it reaches
    too, 10 minutes away. ``places`` are more places, no Base: (name, population, lat, lon).
    """
    group_types, reached, points = [], [], []
    for town in towns:
        mine = []
        for kind, count in sorted(town["types"].items()):
            mine += range(len(group_types), len(group_types) + count)
            group_types += [kind] * count
        minutes = np.full(len(mine), town.get("minutes", 10), dtype=np.uint8)
        groups = np.array(mine, dtype=np.int32)
        if "shares" in town:
            groups = np.r_[reached[town["shares"]][0], groups].astype(np.int32)
            minutes = np.r_[np.full(len(reached[town["shares"]][0]), 10), minutes].astype(np.uint8)
        reached.append((groups, minutes))
        points.append(town["at"])
    if table is None:
        table = np.eye(types, dtype=np.float32)
    hike_types = HikeTypes(types=np.zeros(0), middles=np.zeros(0), table=table.astype(np.float32), version="t")
    count = len(towns)
    population = np.array([town.get("population", np.nan) for town in towns], dtype=np.float64)
    months = np.array([town.get("months", [np.nan] * 12) for town in towns], dtype=np.float32)
    stays, eats = ([town.get(key, np.nan) for town in towns] for key in ("stays", "eats"))
    facts = BaseFacts(
        population=population, month_temperatures=np.repeat(months[:, None], len(REACHES), axis=1),
        stays=np.array(stays, dtype=np.float64), eats=np.array(eats, dtype=np.float64),
    )  # fmt: skip
    hikes = len(group_types)
    store = SimpleNamespace(ids=np.arange(hikes), catalog_digest="t")
    place_ids = np.arange(1, count + 1, dtype=np.int64)
    out = tmp_path / "export"
    out.mkdir()
    _write_bases(
        out, place_ids, np.array(points, dtype=np.float64), reached, np.arange(hikes),
        np.array(group_types, dtype=np.int16), hike_types, facts, store,
    )  # fmt: skip
    db = sqlite3.connect(out / "places.sqlite")
    db.execute("CREATE TABLE places (id INTEGER PRIMARY KEY, name TEXT, country TEXT, population INTEGER, lat REAL, lon REAL)")
    db.executemany(
        "INSERT INTO places VALUES (?, ?, ?, ?, ?, ?)",
        [
            (int(i), t.get("name", "P"), t.get("country", "GB"), int(np.nan_to_num(t.get("population", 0))), *t["at"])
            for i, t in zip(place_ids, towns)
        ]
        + [(count + 1 + i, name, "GB", people, lat, lon) for i, (name, people, lat, lon) in enumerate(places)],
    )
    db.commit()
    db.close()
    return SimpleNamespace(
        bases=Bases.open_dir(out / "bases", store),
        places=f"file:{out / 'places.sqlite'}?mode=ro&immutable=1",
        hikes=hikes,
        group_types=np.array(group_types),
        reached=reached,
    )


def _town(types: dict[int, int], step: int, **more) -> dict:
    """A town ``step`` times FAR north of the Hiker's."""
    return {"types": types, "at": (SEED_AT[0] + step * FAR, SEED_AT[1]), **more}


def _all(data) -> np.ndarray:
    return np.ones(data.hikes, dtype=bool)


def test_known_order_and_parts(tmp_path):
    data = _data(
        tmp_path,
        [
            _town({0: 20, 1: 20}, 0),
            _town({2: 40}, 1),  # nothing alike
            _town({0: 20, 1: 10, 2: 10}, 2),  # half of it is the Hiker's kind
            _town({0: 20, 1: 20}, 3),  # the same
        ],
    )
    ranking = rank_bases(data, 1, 30, _all(data))
    assert [row.base for row in ranking.rows] == [3, 2]  # nothing alike is no row
    same, half = ranking.rows
    assert (same.score, same.mix, same.coverage, same.back, same.enough) == pytest.approx((1, 1, 1, 1, 1))
    # shares (0.5, 0.5, 0) against (0.5, 0.25, 0.25): overlap 0.75; every Hiker's kind is there; 3/4 of it is theirs
    assert (half.mix, half.coverage, half.back) == pytest.approx((0.75, 1.0, 0.75))
    assert half.score == pytest.approx(_stretched(0.4 * 0.75 + 0.3 * 1.0 + 0.3 * 0.75))
    assert same.place_id == 4 and same.hike_groups == 40
    assert ranking.seed == 0 and ranking.seed_groups == 40 and not ranking.too_few


def test_near_hike_types_count_partly(tmp_path, monkeypatch):
    monkeypatch.setitem(bases_module.SCORE_FLOOR, 30, 0.0)  # the parts, every row listed
    table = np.eye(4, dtype=np.float32)
    table[0, 3] = table[3, 0] = 0.8  # credit (0.8 - 0.6) / 0.4 = 0.5
    table[0, 2] = table[2, 0] = 0.6  # a score of 0.6 is no better than a random pair: no credit
    data = _data(tmp_path, [_town({0: 20}, 0), _town({3: 20}, 1), _town({2: 20}, 2)], table)
    (near,) = rank_bases(data, 1, 30, _all(data)).rows  # the random one scores 0: no row
    assert near.base == 1
    # each spread over itself and the near type, 2 : 1, so (2/3, 1/3) against (1/3, 2/3)
    assert (near.mix, near.coverage, near.back) == pytest.approx((2 / 3, 0.5, 0.5))


def test_enough_hikes_on_filtered_counts(tmp_path):
    data = _data(tmp_path, [_town({0: 40}, 0), _town({0: 20}, 1), _town({0: 10}, 2), _town({0: 80}, 3)])
    rows = {row.base: row for row in rank_bases(data, 1, 30, _all(data)).rows}
    assert rows[1].enough == pytest.approx(1)  # half of the Hiker's
    assert rows[2].enough == pytest.approx(np.sqrt(10 / 20))
    assert rows[2].score == pytest.approx(np.sqrt(10 / 20))
    assert rows[3].enough == pytest.approx(1)  # capped
    mask = _all(data)
    mask[data.reached[0][0][:20]] = False  # the Hiker's Base keeps 20 of its 40: half of it is 10
    rows = {row.base: row for row in rank_bases(data, 1, 30, mask).rows}
    assert rows[2].enough == pytest.approx(1)


def test_minimum_after_filtering(tmp_path):
    data = _data(tmp_path, [_town({0: 20}, 0), _town({0: 12}, 1), _town({0: 20}, 2)])
    assert {row.base for row in rank_bases(data, 1, 30, _all(data)).rows} == {1, 2}
    mask = _all(data)
    mask[data.reached[1][0][:3]] = False  # 9 of its HikeGroups pass
    assert {row.base for row in rank_bases(data, 1, 30, mask).rows} == {2}


def test_too_few_of_your_hikes_pass(tmp_path):
    data = _data(tmp_path, [_town({0: 12}, 0), _town({0: 20}, 1)])
    mask = _all(data)
    mask[data.reached[0][0][:7]] = False
    ranking = rank_bases(data, 1, 30, mask)
    assert ranking.too_few and ranking.seed_groups == 5 and ranking.rows == []


def test_coverage_only_for_the_best_by_mix_and_back(tmp_path, monkeypatch):
    monkeypatch.setattr(bases_module, "BEST_HIKE_MATCHES", 2)
    data = _data(
        tmp_path,
        [
            _town({0: 20, 1: 20}, 0),
            _town({0: 20, 1: 20}, 1),
            _town({0: 14, 1: 14, 2: 12}, 2),  # 0.79 in the end, and 0.79 on mix and back with full coverage
            _town({0: 40}, 3),  # 0.8 on mix and back with full coverage, but covers half: 0.65
        ],
    )
    assert [row.base for row in rank_bases(data, 1, 30, _all(data)).rows] == [1, 3]


def test_one_row_per_area(tmp_path):
    reach_km = 30 / 60 * 28.0  # the straight-line speed of the Reach estimate
    north = lambda km: SEED_AT[0] + 3 * FAR + km / 111.2  # noqa: E731
    data = _data(
        tmp_path,
        [
            _town({0: 20, 1: 20}, 0),
            {"types": {0: 20, 1: 20}, "at": (north(0), SEED_AT[1])},  # the area's best
            {"types": {0: 20, 1: 18, 2: 2}, "at": (north(1.9 * reach_km), SEED_AT[1])},  # joins it
            {"types": {0: 20, 1: 16, 2: 4}, "at": (north(-2.1 * reach_km), SEED_AT[1])},  # beyond twice the Reach
            {"types": {0: 20, 1: 14, 2: 6}, "at": (north(-4.0 * reach_km), SEED_AT[1])},  # joins the second row
        ],
    )
    rows = rank_bases(data, 1, 30, _all(data)).rows
    assert [row.base for row in rows] == [1, 3]
    assert rows[0].members == (1, 2) and rows[1].members == (3, 4)


def test_own_area_left_out(tmp_path):
    reach_km = 15 / 60 * 28.0
    data = _data(
        tmp_path,
        [
            _town({0: 20}, 0),
            {"types": {0: 20}, "at": (SEED_AT[0] + 2.9 * reach_km / 111.2, SEED_AT[1])},  # where they have been
            {"types": {0: 20}, "at": (SEED_AT[0] + 3.1 * reach_km / 111.2, SEED_AT[1])},
        ],
    )
    assert [row.base for row in rank_bases(data, 1, 15, _all(data)).rows] == [2]


def test_country_and_region(tmp_path):
    data = _data(
        tmp_path,
        [
            _town({0: 20}, 0, country="FR"),
            _town({0: 20}, 1, country="IT"),
            _town({0: 20}, 2, country="FR"),
            {"types": {0: 20}, "at": (38.6, -109.5), "country": "US"},
        ],
    )
    rows = rank_bases(data, 1, 30, _all(data)).rows
    assert {(row.base, row.country, row.region) for row in rows} == {(1, "IT", "europe"), (2, "FR", "europe"), (3, "US", "us")}
    assert {row.base for row in rank_bases(data, 1, 30, _all(data), countries=["FR", "US"]).rows} == {2, 3}
    assert {row.base for row in rank_bases(data, 1, 30, _all(data), regions=["us"]).rows} == {3}


def test_no_places_no_country(tmp_path):
    data = _data(tmp_path, [_town({0: 20}, 0), _town({0: 20}, 1, country="IT")])
    data.places = None
    assert [(row.base, row.country) for row in rank_bases(data, 1, 30, _all(data)).rows] == [(1, "")]


def test_not_a_base(tmp_path):
    data = _data(tmp_path, [_town({0: 20}, 0), _town({0: 20}, 1)])
    with pytest.raises(KeyError):
        rank_bases(data, 99, 30, _all(data))


def test_mix_only_for_the_best_by_back_and_enough(tmp_path, monkeypatch):
    data = _data(
        tmp_path,
        [
            _town({0: 20, 1: 20}, 0),
            _town({0: 40}, 1),  # all of the Hiker's first kind: back 1, mix 0.5
            _town({0: 20, 1: 10, 2: 10}, 2),  # back 0.75, mix 0.75: better on mix and back together
            _town({0: 5, 1: 5}, 3),  # the same kinds, but a quarter of the hikes: back 1 times enough 0.71
        ],
    )
    assert [row.base for row in rank_bases(data, 1, 30, _all(data)).rows] == [2, 3, 1]
    monkeypatch.setattr(bases_module, "PRESELECT_BASES", 1)
    assert [row.base for row in rank_bases(data, 1, 30, _all(data)).rows] == [1]
    monkeypatch.setattr(bases_module, "PRESELECT_BASES", 2)
    assert [row.base for row in rank_bases(data, 1, 30, _all(data)).rows] == [2, 1]


def _formula(data, me: int, base: int) -> tuple[float, float, float]:
    """``(mix, coverage, back)`` of ``base`` against ``me`` straight from ADR-0084, on dense shares."""
    table = np.asarray(data.bases.type_table)
    credit = np.clip((table - np.float32(bases_module.CREDIT_FROM)) / (1 - bases_module.CREDIT_FROM), 0, 1)
    credit = credit.astype(np.float64)
    spread = credit / credit.sum(axis=1, keepdims=True)
    shares = []
    for town in (me, base):
        groups, _ = data.reached[town]
        counts = np.bincount(data.group_types[groups], minlength=len(table)).astype(np.float64)
        shares.append(counts / counts.sum())
    mine, theirs = shares
    mix = np.minimum(mine @ spread, theirs @ spread).sum()
    coverage = sum(mine[j] * credit[j, theirs > 0].max() for j in np.flatnonzero(mine))
    back = sum(theirs[t] * credit[mine > 0, t].max() for t in np.flatnonzero(theirs))
    return mix, coverage, back


@pytest.mark.parametrize("seed", range(3))
def test_every_row_is_scored_by_the_formula(tmp_path, seed):
    rng = np.random.default_rng(seed)
    types = 40
    table = rng.random((types, types)).astype(np.float32)
    table = (table + table.T) / 2
    np.fill_diagonal(table, 1.0)
    towns = []
    for step in range(60):
        kinds = rng.choice(types, rng.integers(1, 25), replace=False)
        towns.append(_town({int(k): int(rng.integers(1, 8)) for k in kinds} | {int(kinds[0]): 10}, step))
    data = _data(tmp_path, towns, table=table, types=types)

    rows = rank_bases(data, 1, 30, _all(data)).rows

    raw = {base: np.dot((0.4, 0.3, 0.3), _formula(data, 0, base)) for base in range(1, 60)}
    assert {row.base for row in rows} == {base for base, score in raw.items() if score > bases_module.SCORE_FLOOR[30]}
    assert len(rows) >= 10
    assert [row.score for row in rows] == sorted((row.score for row in rows), reverse=True)
    for row in rows:
        assert (row.mix, row.coverage, row.back) == pytest.approx(_formula(data, 0, row.base), abs=1e-12)
        assert row.score == pytest.approx(_stretched(raw[row.base]) * row.enough * row.sure, abs=1e-12)


@pytest.mark.parametrize("seed", range(3))
def test_the_best_do_not_depend_on_the_groups_that_bound_mix(tmp_path, monkeypatch, seed):
    rng = np.random.default_rng(seed)
    types = 30
    table = rng.random((types, types)).astype(np.float32)
    table = (table + table.T) / 2
    np.fill_diagonal(table, 1.0)
    towns = [
        _town({int(k): int(rng.integers(1, 8)) for k in rng.choice(types, rng.integers(1, 20), replace=False)} | {0: 10}, step)
        for step in range(80)
    ]
    data = _data(tmp_path, towns, table=table, types=types)
    monkeypatch.setattr(bases_module, "BEST_HIKE_MATCHES", 7)
    monkeypatch.setattr(bases_module, "MIX_CHUNK", 3)

    found = []
    for blocks in (1, 4, types):  # one group bounds mix by 1; one HikeType each, by mix itself
        monkeypatch.setattr(bases_module, "MIX_BLOCKS", blocks)
        monkeypatch.setattr(bases_module, "_CREDIT", {})
        found.append([(row.base, row.members, row.score) for row in rank_bases(data, 1, 30, _all(data)).rows])

    assert len(found[0]) == 7
    assert found[1] == found[0] and found[2] == found[0]


ALIKE = {0: 20, 1: 20}  # the Hiker's hikes in the tests below


def _stretched(raw: float, reach: int = 30) -> float:
    """A raw hike score as a row shows it: from the floor of a random pair of Bases (0) up to 1."""
    floor = bases_module.SCORE_FLOOR[reach]
    return max(0.0, (raw - floor) / (1 - floor))


def test_scores_run_from_a_random_pair_s_up_to_1_and_no_better_is_no_row(tmp_path):
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0),
            _town(ALIKE, 1),  # the same
            _town({0: 20, 1: 10, 2: 10}, 2),  # mix 0.75, coverage 1, back 0.75
            _town({0: 10, 2: 30}, 3),  # mix 0.25, coverage 0.5, back 0.25: less alike than a typical random pair
        ],
    )
    rows = {row.base: row for row in rank_bases(data, 1, 30, _all(data)).rows}
    assert rows[1].score == pytest.approx(1)
    assert rows[2].score == pytest.approx(_stretched(0.4 * 0.75 + 0.3 * 1 + 0.3 * 0.75))
    assert 0.4 * 0.25 + 0.3 * 0.5 + 0.3 * 0.25 < bases_module.SCORE_FLOOR[30] and 3 not in rows


def test_a_town_with_hikes_enough_for_a_week_is_not_scored_down_for_having_fewer(tmp_path):
    week = bases_module.ENOUGH_GROUPS
    data = _data(tmp_path, [_town({0: 10 * week}, 0), _town({0: week}, 1), _town({0: week // 2}, 2)])
    rows = {row.base: row for row in rank_bases(data, 1, 30, _all(data)).rows}
    assert rows[1].enough == pytest.approx(1) and rows[1].score == pytest.approx(1)
    assert rows[2].enough == pytest.approx(np.sqrt((week // 2) / week))


def test_too_few_of_your_hikes_to_compare_on_scores_every_row_down(tmp_path):
    few = bases_module.ENOUGH_GROUPS // 2
    data = _data(tmp_path, [_town({0: few}, 0), _town({0: few}, 1), _town({0: 4 * few}, 2)])
    rows = rank_bases(data, 1, 30, _all(data)).rows
    sure = np.sqrt(few / bases_module.ENOUGH_GROUPS)
    assert [row.sure for row in rows] == pytest.approx([sure, sure])
    assert [row.score for row in rows] == pytest.approx([sure, sure])  # the same kinds of hike, but few of them


@pytest.mark.parametrize("reach", REACHES)
def test_a_town_whose_hikes_are_mostly_within_an_hour_of_yours_is_left_out(tmp_path, reach):
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0, minutes=10),
            _town({0: 10, 1: 10}, 1, shares=0),  # 40 of its 60 HikeGroups are the Hiker's
            _town({0: 25, 1: 25}, 2, shares=0),  # 40 of 90: under half
            _town(ALIKE, 3),
            # its nearest hikes, within 15 minutes, are the Hiker's
            {"types": {0: 40, 1: 40}, "at": (SEED_AT[0], SEED_AT[1] + 2 * FAR), "shares": 0, "minutes": 50},
        ],
    )
    assert {row.base for row in rank_bases(data, 1, reach, _all(data)).rows} == {2, 3}


def test_a_town_in_a_city_is_left_out(tmp_path):
    people = bases_module.URBAN_PEOPLE
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0),
            _town(ALIKE, 1, population=1_000),  # a district: a city lives around it
            _town(ALIKE, 2, population=people),  # a city itself
            _town(ALIKE, 3, population=people // 2),  # a town, with towns around it
        ],
        places=(
            ("City", people, SEED_AT[0] + FAR + 0.02, SEED_AT[1]),  # 2 km off
            ("Town", people // 3, SEED_AT[0] + 3 * FAR + 0.02, SEED_AT[1]),
        ),
    )
    assert [row.base for row in rank_bases(data, 1, 30, _all(data)).rows] == [3]


NOT_TOWNS = (
    "Rifugio Scalorbi", "Malga Quarazza", "Gasthof", "Bar Alpino", "Hotel Castell", "Grand'Albergo",
    "Albergo della Posta", "Zona Industriale", "Area Produttiva", "Industriale Artigianale", "Industriegebiet Nord",
    "Burningham Industrial", "Collegio Santa Croce", "Cantoniera della Presolana", "Continental Townhouse Condominium",
    "Aspen Park Condo", "Gateway Trailer Park", "Camelot Mobile Home Park", "Foxcroft Apartments",
    "Carter Subdivision", "Stobcross (historical)",
)  # fmt: skip
TOWNS = ("Braunau am Inn", "Potters Bar", "Bar-sur-Seine", "San Martino Buon Albergo", "Red Lodge", "Hütte")


@pytest.mark.parametrize("name", NOT_TOWNS)
def test_a_place_that_is_no_town_is_not_listed(tmp_path, name):
    data = _data(tmp_path, [_town(ALIKE, 0), _town(ALIKE, 1, name=name), _town(ALIKE, 2, name="Keswick")])
    assert [row.base for row in rank_bases(data, 1, 30, _all(data)).rows] == [2]


@pytest.mark.parametrize("name", TOWNS)
def test_a_town_whose_name_has_such_a_word_is_listed(tmp_path, name):
    data = _data(tmp_path, [_town(ALIKE, 0), _town(ALIKE, 1, name=name)])
    assert [row.base for row in rank_bases(data, 1, 30, _all(data)).rows] == [1]


def test_a_row_is_named_after_the_biggest_town_about_as_alike_as_its_best(tmp_path):
    north = SEED_AT[0] + 3 * FAR
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0),
            {"types": ALIKE, "at": (north, SEED_AT[1])},  # the area's best, a hamlet of unknown size
            {"types": {0: 20, 1: 19, 2: 1}, "at": (north + 0.1, SEED_AT[1]), "population": 3_000},  # nearly as alike
            {"types": {0: 20, 1: 14, 2: 6}, "at": (north - 0.1, SEED_AT[1]), "population": 9_000},  # bigger, less alike
            {"types": {0: 20, 1: 19, 2: 1}, "at": (north + 0.2, SEED_AT[1]), "population": 40},  # as alike, smaller
        ],
    )
    (row,) = rank_bases(data, 1, 30, _all(data)).rows
    assert row.base == 2 and row.place_id == 3
    assert row.score == pytest.approx(_stretched(0.4 * 0.975 + 0.3 * 1 + 0.3 * 0.975)) and row.hike_groups == 40
    assert set(row.members) == {1, 2, 3, 4}
    assert row.towns == (3, 4, 1)  # the other towns of the area, biggest first


def test_rows_are_in_the_order_of_the_towns_they_are_named_after(tmp_path):
    north = SEED_AT[0] + 3 * FAR
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0),
            {"types": ALIKE, "at": (north, SEED_AT[1])},
            {"types": {0: 20, 1: 19, 2: 1}, "at": (north + 0.1, SEED_AT[1]), "population": 3_000},  # names that area
            _town({0: 20, 1: 20, 2: 1}, 6),  # between the two: the first row
        ],
    )
    rows = rank_bases(data, 1, 30, _all(data)).rows
    assert [row.base for row in rows] == [3, 2]
    assert [row.score for row in rows] == sorted((row.score for row in rows), reverse=True)


def test_a_base_outside_the_united_states_west_of_the_atlantic_is_not_listed(tmp_path):
    data = _data(
        tmp_path,
        [
            {"types": ALIKE, "at": (38.6, -109.5), "country": "US"},
            {"types": ALIKE, "at": (31.0, -110.0), "country": "MX"},  # its hikes in reach are those across the border
            {"types": ALIKE, "at": (49.1, -114.0), "country": "CA"},
            {"types": ALIKE, "at": (36.0, -112.0), "country": "US"},
        ],
    )
    assert [row.base for row in rank_bases(data, 1, 30, _all(data)).rows] == [3]


def test_warming_reads_the_places_before_the_first_ranking(tmp_path, monkeypatch):
    monkeypatch.setattr(bases_module, "_PLACES", {})
    data = _data(tmp_path, [_town(ALIKE, 0), _town(ALIKE, 1, name="Gasthof")])
    bases_module.warm_bases(data)
    assert bases_module._PLACES[id(data.bases)][1][1].tolist() == [True, False]
