"""TownTraits (ADR-0084): hand-made towns ranked through rank_bases, one test per rule."""

import numpy as np
import pytest
from test_base_ranking import _all, _data, _town

import dayhike.bases as bases_module
from dayhike.bases import rank_bases
from dayhike.town_traits import TownTraits, TraitSetting, months_similarity, ratio_similarity

ALIKE = {0: 20, 1: 20}  # the Hiker's hikes
NEARLY = {0: 20, 1: 18, 2: 2}  # a little less alike
LESS = {0: 20, 1: 14, 2: 6}
MILD = [0, 2, 6, 10, 14, 18, 20, 19, 15, 10, 4, 1]  # mild (8-22 C) April to October


def _bases(rows) -> list[int]:
    return [row.base for row in rows]


def test_similarities():
    assert ratio_similarity(np.array([100.0]), np.array([400.0, 100.0, 1600.0, np.nan])) == pytest.approx(
        [0.5, 1, 0.25, np.nan], nan_ok=True
    )
    a = np.array(MILD, dtype=np.float32)
    b = np.vstack([a, a + 5, a + 20, np.full(12, np.nan)])
    b[1, :6] = np.nan  # only the months both towns have count
    assert months_similarity(a, b) == pytest.approx([1, 0.5, 0, np.nan], nan_ok=True)


def test_all_ignored_equals_hikes_only(tmp_path):
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0, population=4_000, months=MILD),
            _town(NEARLY, 1, population=300),
            _town(LESS, 2, population=4_000, months=MILD),
            _town(ALIKE, 3),
        ],
    )
    plain = rank_bases(data, 1, 30, _all(data))
    ignored = rank_bases(data, 1, 30, _all(data), traits=TownTraits())
    assert ignored == plain
    assert all(row.score == row.hike_score and row.cuts == () for row in ignored.rows)


def test_a_filter_drops_the_right_towns(tmp_path):
    hot = MILD[:6] + [35] + MILD[7:]  # too hot in July
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0, population=4_000, months=MILD),
            _town(ALIKE, 1, population=1_000, months=MILD),
            _town(ALIKE, 2, population=80_000, months=MILD),
            _town(ALIKE, 3, months=MILD),  # population unknown
            _town(ALIKE, 4, population=2_000, months=hot),
        ],
    )
    rank = lambda **traits: set(_bases(rank_bases(data, 1, 30, _all(data), traits=TownTraits(**traits)).rows))  # noqa: E731
    assert rank(size=TraitSetting(up_to=50_000)) == {1, 3, 4}  # an unknown size passes "up to"
    assert rank(size=TraitSetting(at_least=2_000)) == {2, 4}  # and fails "at least"
    assert rank(size=TraitSetting(at_least=1_000, up_to=2_000)) == {1, 4}
    assert rank(months=TraitSetting(mild_in=(4, 10))) == {1, 2, 3, 4}
    assert rank(months=TraitSetting(mild_in=(4, 7))) == {1, 2, 3}
    assert rank(stays=TraitSetting(at_least=1)) == set()  # no Base has its places to stay counted yet


def test_filter_before_the_best_hike_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(bases_module, "BEST_HIKE_MATCHES", 1)
    data = _data(
        tmp_path,
        [_town(ALIKE, 0, population=4_000), _town(ALIKE, 1, population=90_000), _town(NEARLY, 2, population=4_000)],
    )
    traits = TownTraits(size=TraitSetting(up_to=50_000))
    assert _bases(rank_bases(data, 1, 30, _all(data), traits=traits).rows) == [2]


def test_like_mine_on_size_moves_the_same_size_town_up(tmp_path):
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0, population=4_000),
            _town(ALIKE, 1, population=300),
            _town(NEARLY, 2, population=4_000),
        ],
    )
    assert _bases(rank_bases(data, 1, 30, _all(data)).rows) == [1, 2]
    rows = rank_bases(data, 1, 30, _all(data), traits=TownTraits(size=TraitSetting(like_mine=3))).rows
    assert _bases(rows) == [2, 1]
    small, same = rows[1], rows[0]
    cut = 3 / 6 * (1 - (300 / 4_000) ** 0.5)
    assert small.cuts == (("size", pytest.approx(cut)),)
    assert small.score == pytest.approx(small.hike_score * (1 - cut))
    assert same.cuts == (("size", pytest.approx(0)),) and same.score == pytest.approx(same.hike_score)


def test_places_to_stay_and_eat_from_the_bases_folder(tmp_path):
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0, stays=141, eats=76),
            _town(ALIKE, 1, stays=0, eats=3),
            _town(NEARLY, 2, stays=94, eats=58),
        ],
    )
    assert _bases(rank_bases(data, 1, 30, _all(data)).rows) == [1, 2]
    like = TownTraits(stays=TraitSetting(like_mine=2), eats=TraitSetting(like_mine=2))
    rows = rank_bases(data, 1, 30, _all(data), traits=like).rows
    assert _bases(rows) == [2, 1]
    assert rows[1].cuts == (
        ("stays", pytest.approx(2 / 6 * (1 - (1 / 142) ** 0.5))),
        ("eats", pytest.approx(2 / 6 * (1 - (4 / 77) ** 0.5))),
    )
    filtered = TownTraits(stays=TraitSetting(at_least=3), eats=TraitSetting(at_least=5))
    assert _bases(rank_bases(data, 1, 30, _all(data), traits=filtered).rows) == [2]


def test_months_like_mine(tmp_path):
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0, months=MILD),
            _town(ALIKE, 1, months=[m + 8 for m in MILD]),
            _town(NEARLY, 2, months=MILD),
        ],
    )
    rows = rank_bases(data, 1, 30, _all(data), traits=TownTraits(months=TraitSetting(like_mine=2))).rows
    assert _bases(rows) == [2, 1]
    assert dict(rows[1].cuts)["months"] == pytest.approx(2 / 6 * 0.8)


@pytest.mark.parametrize("weight", [1, 2, 3])
def test_a_trait_never_cuts_more_than_its_weight(tmp_path, weight):
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0, population=10_000_000, months=MILD),
            _town(ALIKE, 1, population=1, months=[m + 40 for m in MILD]),
        ],
    )
    setting = TraitSetting(like_mine=weight)
    (row,) = rank_bases(data, 1, 30, _all(data), traits=TownTraits(size=setting, months=setting)).rows
    cuts = dict(row.cuts)
    assert 0 < cuts["size"] <= weight / 6 and cuts["months"] == pytest.approx(weight / 6)
    assert row.score == pytest.approx(row.hike_score * (1 - cuts["size"]) * (1 - cuts["months"]))


def test_a_town_of_unknown_value_counts_as_half_alike(tmp_path):
    data = _data(tmp_path, [_town(ALIKE, 0, population=4_000), _town(ALIKE, 1), _town(ALIKE, 2, population=4_000)])
    rows = rank_bases(data, 1, 30, _all(data), traits=TownTraits(size=TraitSetting(like_mine=3))).rows
    cuts = {base: dict(row.cuts)["size"] for base, row in zip(_bases(rows), rows)}
    assert cuts == {1: pytest.approx(0.25), 2: 0}  # important: half of the half an unlike town loses
    assert _bases(rows) == [2, 1]


def test_an_unknown_value_of_the_hikers_own_costs_nothing(tmp_path):
    data = _data(tmp_path, [_town(ALIKE, 0, population=4_000), _town(ALIKE, 1), _town(ALIKE, 2, population=300)])
    setting = TraitSetting(like_mine=3)
    rows = rank_bases(data, 2, 30, _all(data), traits=TownTraits(size=setting, stays=setting, eats=setting)).rows
    assert all(row.score == row.hike_score for row in rows)  # the Hiker's own size and counts are unknown
    assert dict(rows[0].cuts) == {"size": 0, "stays": 0, "eats": 0}


def test_a_trait_never_brings_in_a_town_outside_the_best(tmp_path, monkeypatch):
    monkeypatch.setattr(bases_module, "BEST_HIKE_MATCHES", 2)
    data = _data(
        tmp_path,
        [
            _town(ALIKE, 0, population=4_000),
            _town(ALIKE, 1, population=1),
            _town(NEARLY, 2, population=1),
            _town(LESS, 3, population=4_000),  # the same size, but third by hikes
        ],
    )
    rows = rank_bases(data, 1, 30, _all(data), traits=TownTraits(size=TraitSetting(like_mine=3))).rows
    assert set(_bases(rows)) == {1, 2}


@pytest.mark.parametrize(
    "setting",
    [
        dict(like_mine=4),
        dict(like_mine=-1),
        dict(like_mine=2, up_to=10),  # one setting per trait
        dict(at_least=10, up_to=5),
        dict(mild_in=(13,)),
        dict(at_least=-1),
    ],
)
def test_settings_refused(setting):
    with pytest.raises(ValueError):
        TraitSetting(**setting)


def test_limits_on_the_wrong_trait_refused():
    with pytest.raises(ValueError):
        TownTraits(size=TraitSetting(mild_in=(7,)))
    with pytest.raises(ValueError):
        TownTraits(months=TraitSetting(at_least=3))
