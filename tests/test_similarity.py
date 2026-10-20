import math
from pathlib import Path

import pytest

from dayhike import build_catalog
from dayhike.similarity import (
    altitude_facts,
    combined_score,
    effort_facts,
    facet_score,
    load_region,
    mix,
    multi_seed_score,
    presence,
    ratio,
    similarity_facts_path_for,
    tolerance,
)

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"


def test_ratio_is_smaller_over_larger_and_symmetric() -> None:
    assert ratio(15.0, 20.0, floor=1.0) == pytest.approx(0.75)
    assert ratio(20.0, 15.0, floor=1.0) == pytest.approx(0.75)
    assert ratio(5.0, 10.0, floor=1.0) == pytest.approx(0.5)
    assert ratio(7.0, 7.0, floor=1.0) == 1.0


def test_ratio_floors_tiny_values() -> None:
    assert ratio(10.0, 30.0, floor=100.0) == 1.0
    assert ratio(0.0, 0.0, floor=100.0) == 1.0
    assert ratio(50.0, 400.0, floor=100.0) == pytest.approx(0.25)


def test_ratio_missing_input_gives_none() -> None:
    assert ratio(None, 5.0, floor=1.0) is None
    assert ratio(5.0, math.nan, floor=1.0) is None


def test_tolerance_scores_difference_against_d() -> None:
    assert tolerance(1800.0, 2000.0, d=1000.0) == pytest.approx(0.8)
    assert tolerance(2000.0, 1800.0, d=1000.0) == pytest.approx(0.8)
    assert tolerance(300.0, 2000.0, d=1000.0) == 0.0
    assert tolerance(2.0, 2.0, d=3.0) == 1.0


def test_tolerance_missing_input_gives_none() -> None:
    assert tolerance(None, 2.0, d=3.0) is None
    assert tolerance(2.0, math.nan, d=3.0) is None


def test_mix_is_the_matching_share() -> None:
    half_forest_half_grass = [0.5, 0.5, 0.0]
    all_forest = [1.0, 0.0, 0.0]
    assert mix(half_forest_half_grass, all_forest) == pytest.approx(0.5)
    assert mix(all_forest, half_forest_half_grass) == pytest.approx(0.5)
    assert mix(all_forest, all_forest) == 1.0
    assert mix(all_forest, [0.0, 0.0, 1.0]) == 0.0


def test_mix_missing_input_gives_none() -> None:
    assert mix(None, [1.0, 0.0]) is None
    assert mix([math.nan, 1.0], [1.0, 0.0]) is None


def test_presence_is_the_share_of_agreeing_flags() -> None:
    assert presence([False, False], [False, False]) == 1.0
    assert presence([True, False, True, False], [True, True, False, False]) == 0.5
    assert presence([True], [False]) == 0.0


def test_presence_ignores_missing_flags_and_none_when_nothing_compares() -> None:
    assert presence([True, None], [True, True]) == 1.0
    assert presence([None], [True]) is None
    assert presence(None, [True]) is None


def test_facet_score_is_the_mean_of_non_missing_measures() -> None:
    assert facet_score([0.5, 1.0]) == pytest.approx(0.75)
    assert facet_score([0.5, None, 1.0]) == pytest.approx(0.75)
    assert facet_score([0.2, math.nan]) == pytest.approx(0.2)
    assert facet_score([None, None]) is None


def test_combined_score_is_the_weighted_mean_in_any_weight_scale() -> None:
    scores = {"facilities": 1.0, "altitude": 0.5, "landscape": 0.0}
    score, coverage = combined_score(
        scores, {"facilities": 50, "altitude": 30, "landscape": 20}
    )
    assert score == pytest.approx(0.65)
    assert coverage == 1.0
    assert combined_score(
        scores, {"facilities": 5, "altitude": 3, "landscape": 2}
    ) == pytest.approx((0.65, 1.0))


def test_combined_score_ignores_zero_weights() -> None:
    score, coverage = combined_score(
        {"effort": 1.0, "profile": 0.0}, {"effort": 1, "profile": 0}
    )
    assert score == 1.0
    assert coverage == 1.0


def test_combined_score_drops_missing_facets_and_reports_coverage() -> None:
    score, coverage = combined_score(
        {"facilities": None, "altitude": 0.5, "landscape": 0.0},
        {"facilities": 50, "altitude": 30, "landscape": 20},
    )
    assert score == pytest.approx(0.3)
    assert coverage == pytest.approx(0.5)


def test_combined_score_is_missing_when_no_facet_is_scored() -> None:
    assert combined_score(
        {"effort": None, "altitude": math.nan}, {"effort": 1, "altitude": 1}
    ) == (None, 0.0)


def test_multi_seed_score_is_the_best_combined_score_to_any_seed() -> None:
    assert multi_seed_score([0.4, 0.9, 0.6]) == 0.9
    assert multi_seed_score([None, 0.3]) == 0.3
    assert multi_seed_score([None, math.nan]) is None


@pytest.mark.parametrize(
    "score",
    [
        lambda a, b: ratio(a, b, floor=1.0),
        lambda a, b: tolerance(a, b, d=3.0),
        lambda a, b: mix([a / 10, 1 - a / 10], [b / 10, 1 - b / 10]),
        lambda a, b: presence([a > 4, a > 7], [b > 4, b > 7]),
    ],
)
def test_measures_give_one_on_identity_are_symmetric_and_bounded(score) -> None:
    values = [0.0, 0.5, 2.0, 5.0, 8.0, 10.0]
    for a in values:
        assert score(a, a) == pytest.approx(1.0)
        for b in values:
            assert 0.0 <= score(a, b) <= 1.0
            assert score(a, b) == pytest.approx(score(b, a))


@pytest.fixture
def fixture_catalog(tmp_path: Path) -> Path:
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    return catalog_path


def test_load_region_keeps_dayhikes_whose_trailhead_is_in_the_box(
    fixture_catalog: Path,
) -> None:
    vertices, amenities, dayhikes, ways = load_region(
        fixture_catalog, lat_range=(44.9, 45.1), lon_range=(14.9, 15.05)
    )

    assert list(dayhikes["id"]) == [1001]
    assert set(vertices["dayhike_id"]) == {1001}
    assert set(ways["dayhike_id"]) == {1001}
    for frame in (vertices, amenities, dayhikes, ways):
        assert frame.crs == "EPSG:4326"
    ridge = dayhikes.iloc[0]
    assert ridge["name"] == "Ridge Walk"
    assert ridge.geometry.geom_type == "LineString"
    assert ridge["difficulty"] == "mountain_hiking"
    assert ridge["difficulty_ordinal"] == 2
    assert ridge["elevation_max_m"] == 500.0
    assert (ridge["trailhead_latitude"], ridge["trailhead_longitude"]) == (45.0, 15.0)
    assert ways.loc[ways["in_chain"] == 1].geometry.notna().all()
    assert {"hut", "peak", "drinking_water"} <= set(amenities["kind"])


def test_load_region_keeps_amenities_near_the_region_only(
    fixture_catalog: Path,
) -> None:
    vertices, amenities, dayhikes, ways = load_region(
        fixture_catalog, lat_range=(45.15, 45.25), lon_range=(15.05, 15.15)
    )

    assert list(dayhikes["name"]) == ["Valley Path"]
    assert amenities.empty


def _whole_fixture(catalog_path: Path):
    return load_region(catalog_path, lat_range=(44.0, 47.0), lon_range=(14.0, 16.0))


def _reversed(vertices):
    last = vertices.groupby("dayhike_id")["seq"].transform("max")
    return vertices.assign(seq=last - vertices["seq"])


def test_effort_facts_are_length_direction_free_gain_and_difficulty_rank(
    fixture_catalog: Path,
) -> None:
    vertices, _, dayhikes, _ = _whole_fixture(fixture_catalog)

    facts = effort_facts(dayhikes, vertices)

    assert list(facts.columns) == ["length_m", "elevation_gain_m", "difficulty_rank"]
    assert set(facts.index) == set(dayhikes["id"])
    ridge = facts.loc[1001]
    assert ridge["length_m"] == pytest.approx(10_000.0, abs=1.0)
    # Ridge Walk climbs 450 m forward (200→500→250→400) and 250 m reversed.
    assert ridge["elevation_gain_m"] == pytest.approx(450.0)
    assert ridge["difficulty_rank"] == 2
    valley = facts.loc[1002]
    assert valley["length_m"] == pytest.approx(5_000.0, abs=1.0)
    assert math.isnan(valley["elevation_gain_m"])
    assert math.isnan(valley["difficulty_rank"])
    assert effort_facts(dayhikes, _reversed(vertices)).equals(facts)


def test_altitude_facts_are_highest_and_lowest_point(fixture_catalog: Path) -> None:
    _, _, dayhikes, _ = _whole_fixture(fixture_catalog)

    facts = altitude_facts(dayhikes)

    assert list(facts.columns) == ["highest_m", "lowest_m"]
    assert (facts.loc[1001, "highest_m"], facts.loc[1001, "lowest_m"]) == (500.0, 200.0)
    assert facts.loc[1002].isna().all()


def test_similarity_facts_table_sits_next_to_the_catalog() -> None:
    assert similarity_facts_path_for(Path("data/catalog.sqlite")) == Path(
        "data/catalog.similarity_facts.pkl"
    )
