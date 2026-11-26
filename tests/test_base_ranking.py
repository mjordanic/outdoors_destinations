"""BaseRanking (ADR-0084): hand-made BaseProfiles with a known order, one test per rule."""

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import dayhike.bases as bases_module
from dayhike.bases import REACHES, BaseFacts, Bases, HikeTypes, _write_bases, find_bases, near_towns, rank_bases
from dayhike.serving import _fold
from dayhike.town_traits import TownTraits, TraitSetting

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
    db.execute("CREATE TABLE place_names (name TEXT, place INTEGER, alternate INTEGER, PRIMARY KEY (name, place))")
    db.executemany(
        "INSERT INTO place_names VALUES (?, ?, 0)",
        [(_fold(t.get("name", "P")), int(i)) for i, t in zip(place_ids, towns)]
        + [(_fold(name), count + 1 + i) for i, (name, *_) in enumerate(places)],
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
def test_every_row_is_scored_by_the_formula(tmp_path, seed, monkeypatch):
    monkeypatch.setattr(bases_module, "MIN_ROW_SCORE", 0.0)  # every Base past a random pair's is a row
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
    "Rifugio Scalorbi", "Malga Quarazza", "Gasthof", ("Bar Alpino", "IT"), ("Bar Sport", "CH"), "Hotel Castell", "Grand'Albergo",
    "Albergo della Posta", "Zona Industriale", "Area Produttiva", "Industriale Artigianale", "Industriegebiet Nord",
    "Burningham Industrial", "Collegio Santa Croce", "Cantoniera della Presolana", "Continental Townhouse Condominium",
    "Aspen Park Condo", "Gateway Trailer Park", "Camelot Mobile Home Park", "Foxcroft Apartments",
    "Carter Subdivision", "Stobcross (historical)",
    # ADR-0086, the second visitor check
    ("Holloman Air Force Base", "US"), ("Soapstone Summer Home Area", "US"), "Drumcroy Lodges",
    "Hafan y Mor Holiday Park", "Riverside Caravan Park",
)  # fmt: skip
TOWNS = (
    "Braunau am Inn", "Potters Bar", "Bar-sur-Seine", "San Martino Buon Albergo", "Red Lodge", "Hütte",
    ("Bar Harbor", "US"), ("Bar Nunn", "US"), ("Bar Mills", "US"), "Bar Hill", ("Bar", "ME"),
)  # fmt: skip


def _named(name: str | tuple[str, str]) -> dict:
    name, country = (name, "GB") if isinstance(name, str) else name
    return {"name": name, "country": country}


@pytest.mark.parametrize("name", NOT_TOWNS)
def test_a_place_that_is_no_town_is_not_listed(tmp_path, name):
    data = _data(tmp_path, [_town(ALIKE, 0), _town(ALIKE, 1, **_named(name)), _town(ALIKE, 2, name="Keswick")])
    assert [row.base for row in rank_bases(data, 1, 30, _all(data)).rows] == [2]


@pytest.mark.parametrize("name", TOWNS)
def test_a_town_whose_name_has_such_a_word_is_listed(tmp_path, name):
    data = _data(tmp_path, [_town(ALIKE, 0), _town(ALIKE, 1, **_named(name))])
    assert [row.base for row in rank_bases(data, 1, 30, _all(data)).rows] == [1]


def test_a_row_is_named_after_the_town_it_is_scored_as_with_the_bigger_towns_under_it(tmp_path):
    """Round 4 of the visitor check undid round 3's "Colwyn Bay figures for Bethesda" (ADR-0089): a row is named
    after the town whose figures it shows, the biggest about as alike as its best, and a bigger town is listed under it."""
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
    assert row.base == 2 and row.place_id == 3 and not hasattr(row, "named")
    assert row.score == pytest.approx(_stretched(0.4 * 0.975 + 0.3 * 1 + 0.3 * 0.975)) and row.hike_groups == 40
    assert set(row.members) == {1, 2, 3, 4}
    assert row.towns == (3, 4, 1)  # the other towns of the area, biggest first


def test_a_row_with_no_village_in_its_area_keeps_the_name_it_is_scored_as(tmp_path):
    north = SEED_AT[0] + 3 * FAR
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0),
            {"types": ALIKE, "at": (north, SEED_AT[1])},  # the area's best, a hamlet of unknown size
            {"types": {0: 20, 1: 19, 2: 1}, "at": (north + 0.1, SEED_AT[1]), "population": 40},  # nearly as alike
            {"types": {0: 20, 1: 14, 2: 6}, "at": (north - 0.1, SEED_AT[1]), "population": 60},  # bigger, less alike
        ],
    )
    (row,) = rank_bases(data, 1, 30, _all(data)).rows
    assert row.base == 2
    assert row.towns == (1, 3)


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


# Round 2 of the visitor check (ADR-0086).
def test_a_row_is_named_after_a_town_even_when_a_hamlet_of_its_area_is_more_alike(tmp_path):
    """Thanei (16 people) over Silandro: the row is the town's, with the town's own score."""
    north = SEED_AT[0] + 3 * FAR
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0),
            {"types": ALIKE, "at": (north, SEED_AT[1]), "population": 16},  # the hamlet, the area's best
            {"types": {0: 20, 1: 10, 2: 10}, "at": (north + 0.1, SEED_AT[1]), "population": 4_500},  # far less alike
        ],
    )
    (row,) = rank_bases(data, 1, 30, _all(data)).rows
    assert row.base == 2
    assert row.score == pytest.approx(_stretched(0.4 * 0.75 + 0.3 * 1 + 0.3 * 0.75))
    assert row.members == (1, 2) and row.towns == (1,)


def test_a_village_names_a_row_with_no_town_in_its_area_and_a_hamlet_only_with_neither(tmp_path):
    north = SEED_AT[0] + 3 * FAR
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0),
            {"types": ALIKE, "at": (north, SEED_AT[1])},  # unknown size, the best
            {"types": {0: 20, 1: 14, 2: 6}, "at": (north + 0.1, SEED_AT[1]), "population": 333},  # a village
            {"types": {0: 20, 1: 18, 2: 2}, "at": (north + 0.2, SEED_AT[1]), "population": 40},
            _town(ALIKE, 6),  # alone, of unknown size: names its own row
        ],
    )
    rows = rank_bases(data, 1, 30, _all(data)).rows
    assert [row.base for row in rows] == [4, 2]
    assert set(rows[1].members) == {1, 2, 3}


def test_two_rows_are_never_named_after_neighbours(tmp_path):
    """Joppa Pans and Brunstane, 800 m apart, each named a row of its own area."""
    reach_km = 30 / 60 * 28.0
    north = lambda km: SEED_AT[0] + 3 * FAR + km / 111.2  # noqa: E731
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0),
            {"types": ALIKE, "at": (north(0), SEED_AT[1])},  # a hamlet, the best
            {"types": {0: 20, 1: 18, 2: 2}, "at": (north(1.5 * reach_km), SEED_AT[1]), "population": 2_000},
            {"types": {0: 20, 1: 18, 2: 3}, "at": (north(-2.5 * reach_km), SEED_AT[1])},  # a second area
            {"types": {0: 20, 1: 17, 2: 3}, "at": (north(1.6 * reach_km), SEED_AT[1]), "population": 3_000},
        ],
    )
    rows = rank_bases(data, 1, 30, _all(data)).rows
    named = [row.base for row in rows]
    assert len(rows) == 2 and len({2, 4} & set(named)) == 1  # one of the two towns names the row, the other joins it


def test_a_row_scoring_next_to_nothing_is_not_listed(tmp_path):
    """Moab at 15 min listed rows of 0.00 to 0.06: a row under MIN_ROW_SCORE (0.1) is none."""
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0),
            _town({0: 20, 2: 20}, 1),  # mix 0.5, coverage 0.5, back 0.5: 0.5 raw, 0.17 as shown
            _town({0: 16, 2: 24}, 2),  # mix 0.4, coverage 0.5, back 0.4: 0.43 raw, 0.05 as shown
        ],
    )
    rows = rank_bases(data, 1, 30, _all(data)).rows
    assert [row.base for row in rows] == [1]
    assert rows[0].score == pytest.approx(_stretched(0.5))


def test_a_district_of_a_big_city_beyond_its_centre_or_a_suburb_of_a_sprawl_is_left_out(tmp_path):
    """Joppa Pans, 6.8 km from Edinburgh's point, and Summerlin South, in Las Vegas' sprawl, are in a city;
    Glossop, 31,000 people within 8 km, is not."""
    km = 1 / 111.2
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0),
            _town(ALIKE, 1),  # a district: 6.8 km from a city of 514,000, under 100,000 within 5 km
            _town(ALIKE, 2),  # a suburb: 160,000 people 7 km off, none within 5 km
            _town(ALIKE, 3, population=17_825),  # a town by a city's edge
            _town(ALIKE, 4),
        ],
        places=(
            ("Edinburgh", 514_000, SEED_AT[0] + FAR + 6.8 * km, SEED_AT[1]),
            ("Spring Valley", 80_000, SEED_AT[0] + 2 * FAR + 7 * km, SEED_AT[1]),
            ("Paradise", 80_000, SEED_AT[0] + 2 * FAR - 7 * km, SEED_AT[1]),
            ("Hyde", 31_000, SEED_AT[0] + 3 * FAR + 7 * km, SEED_AT[1]),
            ("Manchester", 395_000, SEED_AT[0] + 3 * FAR + 20 * km, SEED_AT[1]),
        ),
    )
    assert sorted(row.base for row in rank_bases(data, 1, 30, _all(data)).rows) == [3, 4]


def test_a_town_typed_is_found_before_a_hamlet_of_its_name_and_the_one_with_beds_first(tmp_path):
    """"Cortina" took a hamlet of 44 people near Verona before Cortina d'Ampezzo, "Interlaken" the US one."""
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0, name="Cortina", population=44, stays=3),
            _town(ALIKE, 1, name="Cortina d'Ampezzo", population=5_423, stays=51),
            _town(ALIKE, 2, name="Interlaken", population=7_321, stays=0, country="US"),
            _town(ALIKE, 3, name="Interlaken", population=5_067, stays=78, country="CH"),
        ],
    )
    assert find_bases(data, "Cortina", 5) == ([1, 0], False)
    assert find_bases(data, "interlaken", 5) == ([3, 2], False)


def test_a_village_typed_exactly_is_found_before_bigger_towns_its_name_starts(tmp_path):
    """"Alta" (Utah, about 380 people, a hiking and ski base) came after Altamura, Altadena and the like."""
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0, name="Altamura", population=70_000, stays=40, country="IT"),
            _town(ALIKE, 1, name="Altadena", population=42_000, stays=5, country="US"),
            _town(ALIKE, 2, name="Altaussee", population=1_800, stays=60, country="AT"),
            _town(ALIKE, 3, name="Alta", population=383, stays=12, country="US"),
        ],
    )
    found, guess = find_bases(data, "Alta", 5)
    assert found[0] == 3 and not guess
    assert find_bases(data, "Alta", 1) == ([3], False)


def test_a_name_one_typo_away_is_said_to_be_a_guess(tmp_path):
    data = _data(tmp_path, [_town(ALIKE, 0, name="Keswick", population=4_658), _town(ALIKE, 1, name="Kendal")])
    assert find_bases(data, "keswik", 5) == ([0], True)
    assert find_bases(data, "kes", 5) == ([0], False)


def test_a_town_is_said_to_be_near_a_bigger_town_not_a_district_of_its_own(tmp_path):
    """"Munich · near Bogenhausen", "London · near Soho": the town named is bigger, and not far off."""
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0, name="Munich", population=1_505_005),
            {"types": ALIKE, "at": (SEED_AT[0] + 0.03, SEED_AT[1]), "name": "Bogenhausen", "population": 80_000},
            _town(ALIKE, 5, name="Faraway", population=3_000),  # the nearest bigger, Bogenhausen, is 555 km off
            {"types": ALIKE, "at": (SEED_AT[0] + 5 * FAR + 0.3, SEED_AT[1]), "name": "Midtown", "population": 20_000},
        ],
    )
    near = near_towns(data, [0, 1, 2])
    assert near[0] is None  # no bigger town anywhere near Munich
    assert near[1] == {"name": "Munich", "km": 3}
    assert near[2] == {"name": "Midtown", "km": 33}


def test_a_town_is_said_to_be_near_only_a_bigger_town_of_its_country_close_by(tmp_path):
    """Round 4 (H10, B7): "Garmisch-Partenkirchen near Hötting" (in Austria), "Moab near Fruita, 96 km",
    "Portland near Seattle, 233 km": a town far off, or across a border, tells no one where a town is."""
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0, name="Garmisch-Partenkirchen", population=27_000, country="DE"),
            {"types": ALIKE, "at": (SEED_AT[0] + 0.25, SEED_AT[1]), "name": "Hötting", "population": 30_000, "country": "AT"},
            {"types": ALIKE, "at": (SEED_AT[0] + 0.45, SEED_AT[1]), "name": "Weilheim", "population": 40_000, "country": "DE"},
            _town(ALIKE, 3, name="Moab", population=5_000, country="US"),
            {"types": ALIKE, "at": (SEED_AT[0] + 3 * FAR + 0.86, SEED_AT[1]), "name": "Fruita", "population": 13_000, "country": "US"},
        ],
    )
    near = near_towns(data, [0, 3])
    assert near[0] == {"name": "Weilheim", "km": 50}  # not Hötting, nearer but in another country
    assert near[1] is None  # Fruita is 96 km off


def test_a_row_named_after_a_bigger_town_says_which_town_of_it_is_the_best(tmp_path):
    """Blaenau-Ffestiniog (0.88) inside a row named Caernarfon (0.86): the row says so, so the best is not hidden."""
    north = SEED_AT[0] + 3 * FAR
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0),
            {"types": ALIKE, "at": (north, SEED_AT[1]), "population": 4_800},  # the best town
            {"types": {0: 20, 1: 19, 2: 1}, "at": (north + 0.1, SEED_AT[1]), "population": 9_700},  # bigger, about as alike
            {"types": {0: 20, 1: 19, 2: 1}, "at": (north + 0.2, SEED_AT[1]), "population": 20_000, "minutes": 10},
        ],
    )
    (row,) = rank_bases(data, 1, 30, _all(data)).rows
    assert row.base == 3 and row.best == 1 and row.best_score == pytest.approx(1)
    assert row.towns == (1, 2)  # the best first, then the biggest


def test_a_big_town_with_almost_no_places_to_stay_is_left_out(tmp_path):
    """Dakota Ridge (33,892 people, 2 places to stay) and Parker, Denver's suburbs, led Estes Park's list."""
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0),
            _town(ALIKE, 1, population=33_892, stays=2),  # where people live, not where visitors stay
            _town(ALIKE, 2, population=33_892, stays=12),
            _town(ALIKE, 3, population=33_892),  # its places to stay not counted
            _town(ALIKE, 4, population=15_000, stays=0),  # a small town
        ],
    )
    assert sorted(row.base for row in rank_bases(data, 1, 30, _all(data)).rows) == [2, 3, 4]


def test_the_town_choices_are_said_to_leave_no_town_only_when_they_do(tmp_path):
    """A hike filter that leaves no other town enough hikes is no fault of the town choices (ADR-0086)."""
    data = _data(tmp_path, [_town(ALIKE, 0, population=4_000), _town(ALIKE, 1, population=4_000)])
    mine = np.zeros(data.hikes, dtype=bool)
    mine[: sum(ALIKE.values())] = True  # only the Hiker's own hikes pass
    default = TownTraits(size=TraitSetting(up_to=50_000))

    strict = rank_bases(data, 1, 30, mine, traits=default)
    assert strict.rows == [] and not strict.traits_leave_none
    none = rank_bases(data, 1, 30, _all(data), traits=TownTraits(size=TraitSetting(at_least=99_999)))
    assert none.rows == [] and none.traits_leave_none
    assert not rank_bases(data, 1, 30, _all(data), traits=default).traits_leave_none
