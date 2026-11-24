import numpy as np
import pytest

from dayhike.hike_filter import HikeFilter, hike_mask
from dayhike.similarity import CALIBRATION_KEYS, FACT_COLUMNS, SimilarityStore

# Seven hikes, one row each. Hike 6 has no summit, water or round-trip entry (NaN), as a hike
# the map says nothing about.
LENGTH_M = [3_000, 5_000, 8_000, 15_000, 15_001, 4_999, 9_000]
CLIMB_M = [0, 100, 500, 500.5, 800, 1_200, 300]
HIGHEST_M = [200, 900, 1_499, 1_500, 2_400, 3_100, 700]
SUMMIT = [0, 1, 0, 1, 1, 1, np.nan]
WATERSIDE = [0.0, 0.15, 0.149, 0.6, 0.0, 0.2, np.nan]
ROUND_TRIP = [1, 0, 1, 0, 1, 0, np.nan]


def _store() -> SimilarityStore:
    rows = len(LENGTH_M)
    facts = np.full((len(FACT_COLUMNS), rows), np.nan, dtype=np.float32)
    for name, values in {
        "length_m": LENGTH_M,
        "elevation_gain_m": CLIMB_M,
        "highest_m": HIGHEST_M,
        "peak_summited": SUMMIT,
        "waterside_share": WATERSIDE,
        "round_trip": ROUND_TRIP,
    }.items():
        facts[FACT_COLUMNS.index(name)] = values
    return SimilarityStore(
        ids=np.arange(rows),
        facts=facts,
        profile=np.full((rows, 64), np.nan),
        quantiles=np.full((len(CALIBRATION_KEYS), 4), np.nan),
        twins=np.arange(rows),
    )


def _passing(**limits) -> list[int]:
    return np.flatnonzero(hike_mask(_store(), HikeFilter(**limits))).tolist()


def test_an_empty_filter_lets_every_hike_pass() -> None:
    mask = hike_mask(_store(), HikeFilter())
    assert mask.dtype == bool
    assert mask.tolist() == [True] * len(LENGTH_M)
    assert HikeFilter().is_empty()


def test_a_range_keeps_hikes_at_its_ends_and_between() -> None:
    assert _passing(climb_max_m=500) == [0, 1, 2, 6]
    assert _passing(climb_min_m=500, climb_max_m=800) == [2, 3, 4]
    assert _passing(length_min_km=5, length_max_km=15) == [1, 2, 3, 6]
    assert _passing(highest_max_m=1_500) == [0, 1, 2, 3, 6]
    assert _passing(highest_min_m=1_500) == [3, 4, 5]


def test_a_range_with_one_end_open_is_the_other_end_alone() -> None:
    assert _passing(climb_min_m=800) == [4, 5]
    assert _passing(length_max_km=4.999) == [0, 5]


def test_several_limits_must_all_hold() -> None:
    assert _passing(climb_max_m=500, length_min_km=5, length_max_km=15, highest_max_m=1_499) == [1, 2, 6]


def test_a_toggle_keeps_only_hikes_with_the_feature_and_drops_unknown_ones() -> None:
    assert _passing(summit=True) == [1, 3, 4, 5]
    assert _passing(round_trip=True) == [0, 2, 4]
    # Waterside counts from the Measure's dead band: 0.15 is beside water, 0.149 is dry.
    assert _passing(waterside=True) == [1, 3, 5]


def test_toggles_and_ranges_combine() -> None:
    assert _passing(summit=True, waterside=True, climb_max_m=500) == [1]


def test_a_filter_that_nothing_passes_is_an_all_no_mask() -> None:
    assert _passing(climb_min_m=5_000) == []


def test_a_range_refuses_a_hike_without_the_fact() -> None:
    store = _store()
    store.facts[FACT_COLUMNS.index("elevation_gain_m"), 2] = np.nan
    mask = hike_mask(store, HikeFilter(climb_max_m=500))
    assert np.flatnonzero(mask).tolist() == [0, 1, 6]


def test_the_mask_follows_the_store_it_is_given() -> None:
    store = _store()
    assert len(hike_mask(store, HikeFilter(summit=True))) == len(store.ids)


# The filter object a link carries (ADR-0073): the same object comes back, and a link names only
# what the Hiker set.


def test_a_link_object_holds_only_the_limits_set() -> None:
    assert HikeFilter().to_mapping() == {}
    chosen = HikeFilter(climb_max_m=500, length_min_km=5, length_max_km=15, summit=True)
    assert chosen.to_mapping() == {
        "climb_max_m": 500,
        "length_min_km": 5,
        "length_max_km": 15,
        "summit": True,
    }


def test_a_filter_survives_a_link() -> None:
    chosen = HikeFilter(
        climb_min_m=0, climb_max_m=500, length_min_km=5.5, highest_max_m=1_500,
        summit=True, waterside=True, round_trip=True,
    )  # fmt: skip
    assert HikeFilter.from_mapping(chosen.to_mapping()) == chosen
    assert HikeFilter.from_mapping({}) == HikeFilter()
    assert HikeFilter.from_mapping(None) == HikeFilter()


def test_a_toggle_set_to_false_is_left_out_of_a_link() -> None:
    assert HikeFilter(summit=False).to_mapping() == {}
    assert HikeFilter.from_mapping({"summit": False}) == HikeFilter()


def test_unknown_keys_are_refused_and_named() -> None:
    with pytest.raises(ValueError, match="difficulty_max"):
        HikeFilter.from_mapping({"climb_max_m": 500, "difficulty_max": 3})


@pytest.mark.parametrize(
    "bad",
    [
        {"climb_max_m": "500"},
        {"climb_max_m": True},
        {"climb_max_m": float("nan")},
        {"climb_max_m": float("inf")},
        {"climb_min_m": -1},
        {"length_min_km": 20, "length_max_km": 5},
        {"summit": "yes"},
        {"round_trip": 1},
    ],
)
def test_values_that_are_not_limits_are_refused(bad) -> None:
    with pytest.raises(ValueError):
        HikeFilter.from_mapping(bad)
