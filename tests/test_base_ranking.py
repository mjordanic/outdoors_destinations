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


def _data(tmp_path: Path, towns: list[dict], table: np.ndarray | None = None, types: int = 4) -> SimpleNamespace:
    """A serving export's ``bases`` and ``places`` from hand-made towns.

    Each town is ``{"types": {HikeType: HikeGroups}, "at": (lat, lon), "country": "GB"}``, its
    HikeGroups its own, one hike each, all 10 minutes away. Town i is Base i with place id i + 1.
    A town may give its ``population`` and its 12 ``months`` (the same in every Reach); unknown without.
    """
    group_types, reached, points = [], [], []
    for town in towns:
        mine = []
        for kind, count in sorted(town["types"].items()):
            mine += range(len(group_types), len(group_types) + count)
            group_types += [kind] * count
        reached.append((np.array(mine, dtype=np.int32), np.full(len(mine), 10, dtype=np.uint8)))
        points.append(town["at"])
    if table is None:
        table = np.eye(types, dtype=np.float32)
    hike_types = HikeTypes(types=np.zeros(0), middles=np.zeros(0), table=table.astype(np.float32), version="t")
    count = len(towns)
    population = np.array([town.get("population", np.nan) for town in towns], dtype=np.float64)
    months = np.array([town.get("months", [np.nan] * 12) for town in towns], dtype=np.float32)
    facts = BaseFacts(population=population, month_temperatures=np.repeat(months[:, None], len(REACHES), axis=1))
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
        "INSERT INTO places VALUES (?, 'P', ?, 0, ?, ?)",
        [(int(i), t.get("country", "GB"), *t["at"]) for i, t in zip(place_ids, towns)],
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
    assert [row.base for row in ranking.rows] == [3, 2, 1]
    same, half, unlike = ranking.rows
    assert (same.score, same.mix, same.coverage, same.back, same.enough) == pytest.approx((1, 1, 1, 1, 1))
    # shares (0.5, 0.5, 0) against (0.5, 0.25, 0.25): overlap 0.75; every Hiker's kind is there; 3/4 of it is theirs
    assert (half.mix, half.coverage, half.back) == pytest.approx((0.75, 1.0, 0.75))
    assert half.score == pytest.approx(0.4 * 0.75 + 0.3 * 1.0 + 0.3 * 0.75)
    assert unlike.score == pytest.approx(0)
    assert same.place_id == 4 and same.hike_groups == 40
    assert ranking.seed == 0 and ranking.seed_groups == 40 and not ranking.too_few


def test_near_hike_types_count_partly(tmp_path):
    table = np.eye(4, dtype=np.float32)
    table[0, 3] = table[3, 0] = 0.8  # credit (0.8 - 0.6) / 0.4 = 0.5
    table[0, 2] = table[2, 0] = 0.6  # a score of 0.6 is no better than a random pair: no credit
    data = _data(tmp_path, [_town({0: 20}, 0), _town({3: 20}, 1), _town({2: 20}, 2)], table)
    near, random = rank_bases(data, 1, 30, _all(data)).rows
    assert near.base == 1 and random.base == 2
    # each spread over itself and the near type, 2 : 1, so (2/3, 1/3) against (1/3, 2/3)
    assert (near.mix, near.coverage, near.back) == pytest.approx((2 / 3, 0.5, 0.5))
    assert random.score == pytest.approx(0)


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
