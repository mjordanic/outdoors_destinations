import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString

from dayhike import build_catalog
from dayhike.similarity import (
    CLIMATE_MONTH_COLUMNS,
    FACILITIES_FLAG_COLUMNS,
    altitude_facts,
    LANDCOVER_SHARE_COLUMNS,
    SURFACE_SHARE_COLUMNS,
    combined_score,
    corridor_amenities,
    effort_facts,
    facet_score,
    facilities_facts,
    landscape_facts,
    load_region,
    mix,
    multi_seed_score,
    presence,
    profile_facts,
    profile_dtw,
    profile_lockstep,
    profile_shape,
    ratio,
    similarity_facts_path_for,
    solar_aspect_southness,
    surface_class,
    surface_class_from_tag,
    tolerance,
    trail_facts,
    water_facts,
    weather_facts,
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


def test_profile_facts_are_the_profile_shape_per_dayhike(
    fixture_catalog: Path,
) -> None:
    vertices, _, dayhikes, _ = _whole_fixture(fixture_catalog)

    facts = profile_facts(dayhikes, vertices)

    assert list(facts.columns) == ["profile_shape"]
    assert set(facts.index) == set(dayhikes["id"])
    # Ridge Walk: 200 -> 500 -> 250 -> 400 m, so it starts at the bottom and
    # ends about two thirds up (the resampled top falls just short of 500 m).
    ridge = facts.at[1001, "profile_shape"]
    assert len(ridge) == 64
    assert (ridge[0], ridge.max()) == pytest.approx((0.0, 1.0))
    assert ridge[-1] == pytest.approx(200 / 300, abs=0.02)
    assert facts.at[1002, "profile_shape"] is None
    turned = profile_facts(dayhikes, _reversed(vertices)).at[1001, "profile_shape"]
    assert turned == pytest.approx(ridge[::-1])


def test_similarity_facts_table_sits_next_to_the_catalog() -> None:
    assert similarity_facts_path_for(Path("data/catalog.sqlite")) == Path(
        "data/catalog.similarity_facts.pkl"
    )


def _north_line(elevations, step_m=100.0):
    """Vertices due north of (45, 15), ``step_m`` apart, with these elevations."""
    dlat = math.degrees(step_m / 6_371_000)
    lats = [45.0 + i * dlat for i in range(len(elevations))]
    return lats, [15.0] * len(elevations), list(elevations)


def test_profile_shape_is_64_points_scaled_by_the_range() -> None:
    shape = profile_shape(*_north_line([1000.0, 1200.0, 1500.0]))

    assert len(shape) == 64
    assert (shape[0], shape[-1]) == pytest.approx((0.0, 1.0))
    # The middle vertex (200 m above the start) sits halfway along.
    halfway = np.interp(0.5, np.linspace(0.0, 1.0, 64), shape)
    assert halfway == pytest.approx(0.4, abs=0.01)
    tent = profile_shape(*_north_line([0.0, 600.0, 0.0]))
    assert tent == pytest.approx(tent[::-1])
    assert (tent[0], tent[31], tent[32]) == pytest.approx((0.0, 1.0, 1.0))


def test_profile_shape_follows_distance_not_vertex_count() -> None:
    # Climb over the first 100 m, then 300 m flat: the climb ends at 1/4.
    lats, lons, ele = _north_line([0.0, 400.0, 400.0, 400.0, 400.0])
    shape = profile_shape(lats, lons, ele)
    fraction = np.linspace(0.0, 1.0, 64)
    assert shape[fraction >= 0.25] == pytest.approx(1.0)
    assert shape[fraction < 0.25] == pytest.approx(fraction[fraction < 0.25] * 4)


def test_profile_shape_does_not_stretch_a_nearly_flat_profile() -> None:
    # 64 vertices, one per resampled point, with up to 5 m of noise.
    noise = [300.0 + (i * 7) % 6 for i in range(64)]
    shape = profile_shape(*_north_line(noise))

    assert shape == pytest.approx((np.array(noise) - 300.0) / 50.0)
    assert shape.max() == pytest.approx(0.1)


def test_profile_shape_skips_vertices_without_elevation() -> None:
    lats, lons, _ = _north_line([0.0] * 3)
    shape = profile_shape(lats, lons, [0.0, math.nan, 400.0])
    assert shape == pytest.approx(np.linspace(0.0, 1.0, 64))
    assert profile_shape(lats, lons, [math.nan, 250.0, math.nan]) is None
    assert profile_shape(lats[:1], lons[:1], [100.0]) is None


_FRACTION = np.linspace(0.0, 1.0, 64)
ONE_CLIMB_EARLY = np.interp(_FRACTION, [0.0, 0.3, 1.0], [0.0, 1.0, 0.0])
ONE_CLIMB_LATE = np.interp(_FRACTION, [0.0, 0.5, 1.0], [0.0, 1.0, 0.0])
MANY_CLIMBS = (1 - np.cos(_FRACTION * 8 * np.pi)) / 2
FLAT = np.zeros(64)
NEARLY_FLAT = np.array([(i * 7) % 6 for i in range(64)]) / 50.0
SHAPES = [ONE_CLIMB_EARLY, ONE_CLIMB_LATE, MANY_CLIMBS, FLAT, NEARLY_FLAT, _FRACTION]


@pytest.mark.parametrize("similarity", [profile_lockstep, profile_dtw])
def test_profile_similarity_identity_reversed_symmetry_and_bounds(similarity) -> None:
    for a in SHAPES:
        assert similarity(a, a) == pytest.approx(1.0)
        assert similarity(a, a[::-1]) == pytest.approx(1.0)
        for b in SHAPES:
            assert 0.0 <= similarity(a, b) <= 1.0
            assert similarity(a, b) == pytest.approx(similarity(b, a))
    assert similarity(_FRACTION, FLAT) == pytest.approx(0.5)


@pytest.mark.parametrize("similarity", [profile_lockstep, profile_dtw])
def test_profile_similarity_matches_form_flat_with_flat(similarity) -> None:
    assert similarity(NEARLY_FLAT, FLAT) >= 0.9
    assert similarity(NEARLY_FLAT, FLAT) > similarity(NEARLY_FLAT, ONE_CLIMB_LATE)
    assert similarity(ONE_CLIMB_EARLY, ONE_CLIMB_LATE) > similarity(
        ONE_CLIMB_EARLY, MANY_CLIMBS
    )


@pytest.mark.parametrize("similarity", [profile_lockstep, profile_dtw])
def test_profile_similarity_missing_shape_gives_none(similarity) -> None:
    assert similarity(None, FLAT) is None
    assert similarity(FLAT, math.nan) is None


def test_profile_dtw_never_scores_below_lockstep() -> None:
    for a in SHAPES:
        for b in SHAPES:
            assert profile_dtw(a, b) >= profile_lockstep(a, b) - 1e-12


def test_profile_dtw_lets_a_climb_shift_within_the_band() -> None:
    lockstep = profile_lockstep(ONE_CLIMB_EARLY, ONE_CLIMB_LATE)
    assert profile_dtw(ONE_CLIMB_EARLY, ONE_CLIMB_LATE) > lockstep + 0.02
    # With no band the warping path is the diagonal: lock-step again.
    assert profile_dtw(ONE_CLIMB_EARLY, ONE_CLIMB_LATE, band=0.0) == pytest.approx(
        lockstep
    )


def _metres_north(metres):
    return 45.0 + math.degrees(metres / 6_371_000)


def _metres_east(metres):
    return 15.0 + math.degrees(metres / (6_371_000 * math.cos(math.radians(45.0))))


def _chain(*points, dayhike_id=1):
    """Vertices of one DayHike from (lat, lon) points in chain order."""
    return pd.DataFrame(
        {
            "dayhike_id": dayhike_id,
            "seq": range(len(points)),
            "latitude": [lat for lat, _ in points],
            "longitude": [lon for _, lon in points],
        }
    )


def _amenity_points(kind, *points):
    return pd.DataFrame(
        {
            "osm_key": [f"n{i}" for i in range(len(points))],
            "kind": kind,
            "latitude": [lat for lat, _ in points],
            "longitude": [lon for _, lon in points],
        }
    )


# A 300 m segment due north of (45, 15).
SEGMENT = _chain((45.0, 15.0), (_metres_north(300), 15.0))


def test_corridor_is_in_at_90_m_and_out_at_110_m() -> None:
    water = _amenity_points(
        "drinking_water",
        (45.0, _metres_east(90)),
        (_metres_north(300), _metres_east(110)),
    )

    found = corridor_amenities(SEGMENT, water, kind="drinking_water", radius_m=100.0)

    assert list(found["osm_key"]) == ["n0"]
    assert found["distance_m"].iloc[0] == pytest.approx(90.0, abs=0.5)


def test_corridor_measures_to_the_line_not_only_to_vertices() -> None:
    # 150 m along a 300 m segment and 90 m aside: 175 m from the nearest vertex.
    beside_middle = _amenity_points("drinking_water", (_metres_north(150), _metres_east(90)))

    found = corridor_amenities(
        SEGMENT, beside_middle, kind="drinking_water", radius_m=100.0
    )

    assert list(found["osm_key"]) == ["n0"]
    assert found["distance_m"].iloc[0] == pytest.approx(90.0, abs=0.5)


def test_corridor_is_set_by_the_radius_and_the_kind() -> None:
    mixed = pd.concat(
        [
            _amenity_points("peak", (_metres_north(150), _metres_east(300))),
            _amenity_points("hut", (_metres_north(150), _metres_east(10))),
        ],
        ignore_index=True,
    )

    assert corridor_amenities(SEGMENT, mixed, kind="peak", radius_m=100.0).empty
    near = corridor_amenities(SEGMENT, mixed, kind="peak", radius_m=2_000.0)
    assert near["distance_m"].iloc[0] == pytest.approx(300.0, abs=2.0)
    assert corridor_amenities(SEGMENT, mixed, kind="shelter", radius_m=100.0).empty


def test_corridor_gives_each_dayhike_its_own_points_in_either_direction() -> None:
    other = _chain((45.5, 15.0), (_metres_north(300) + 0.5, 15.0), dayhike_id=2)
    both = pd.concat([SEGMENT, other], ignore_index=True)
    water = _amenity_points(
        "drinking_water", (_metres_north(150), _metres_east(50)), (45.5, _metres_east(20))
    )

    found = corridor_amenities(both, water, kind="drinking_water", radius_m=100.0)
    turned = corridor_amenities(
        both.assign(seq=both.groupby("dayhike_id")["seq"].transform("max") - both["seq"]),
        water,
        kind="drinking_water",
        radius_m=100.0,
    )

    assert sorted(zip(found["dayhike_id"], found["osm_key"])) == [(1, "n0"), (2, "n1")]
    assert turned.sort_values("dayhike_id").equals(found.sort_values("dayhike_id"))


def test_corridor_with_no_point_of_the_kind_is_empty() -> None:
    found = corridor_amenities(
        SEGMENT, _amenity_points("hut"), kind="drinking_water", radius_m=100.0
    )

    assert found.empty
    assert list(found.columns) == ["dayhike_id", "osm_key", "distance_m"]


def test_landscape_facts_put_the_fixture_peak_and_viewpoint_on_ridge_walk(
    fixture_catalog: Path,
) -> None:
    vertices, amenities, dayhikes, _ = _whole_fixture(fixture_catalog)

    facts = landscape_facts(dayhikes, vertices, amenities)

    assert list(facts.columns) == [
        *LANDCOVER_SHARE_COLUMNS,
        "peak_summited",
        "viewpoint_along",
        "peaks_around",
    ]
    assert set(facts.index) == set(dayhikes["id"])
    # The fixture Peak and Viewpoint sit on Ridge Walk's line (lon 15.0).
    assert facts.loc[1001, "peak_summited"]
    assert facts.loc[1001, "viewpoint_along"]
    assert facts.loc[1001, "peaks_around"] == 1
    # Valley Path is ~21 km away from them.
    assert not facts.loc[1002, "peak_summited"]
    assert not facts.loc[1002, "viewpoint_along"]
    assert facts.loc[1002, "peaks_around"] == 0
    assert landscape_facts(dayhikes, _reversed(vertices), amenities).equals(facts)


def test_landscape_facts_peak_summited_is_50_m_and_peaks_around_is_2_km() -> None:
    dayhikes = pd.DataFrame(
        {
            "id": [1],
            **{f"{n}_fraction": [None] for n in ("tree", "shrub", "grass", "bare", "snow")},
        }
    )
    peaks = _amenity_points(
        "peak",
        (_metres_north(150), _metres_east(40)),
        (_metres_north(150), _metres_east(1_900)),
        (_metres_north(150), _metres_east(2_100)),
    )

    facts = landscape_facts(dayhikes, SEGMENT, peaks)
    assert facts.loc[1, "peak_summited"]
    assert facts.loc[1, "peaks_around"] == 2
    far = landscape_facts(
        dayhikes,
        SEGMENT,
        _amenity_points("peak", (_metres_north(150), _metres_east(60))),
    )
    assert not far.loc[1, "peak_summited"]
    assert far.loc[1, "peaks_around"] == 1
    assert not landscape_facts(dayhikes, SEGMENT, peaks.iloc[0:0]).loc[1, "viewpoint_along"]


def test_landscape_facts_landcover_shares_include_the_clipped_leftover() -> None:
    def shares(*fractions):
        names = ("tree", "shrub", "grass", "bare", "snow")
        frame = pd.DataFrame(
            {"id": [1], **{f"{n}_fraction": [f] for n, f in zip(names, fractions)}}
        )
        return landscape_facts(frame, SEGMENT, _amenity_points("peak")).loc[1]

    split = shares(0.5, 0.1, 0.2, 0.1, 0.0)
    assert [split[c] for c in LANDCOVER_SHARE_COLUMNS] == pytest.approx(
        [0.5, 0.1, 0.2, 0.1, 0.0, 0.1]
    )
    assert shares(0.5, 0.3, 0.3, 0.0, 0.0)["other_share"] == 0.0
    missing = shares(None, 0.1, 0.2, 0.1, 0.0)
    assert missing[list(LANDCOVER_SHARE_COLUMNS)].isna().all()


@pytest.mark.parametrize(
    "surface, expected",
    [
        *[(v, "paved") for v in (
            "asphalt", "concrete", "concrete:plates", "concrete:lanes",
            "paving_stones", "sett", "cobblestone", "paved",
        )],
        *[(v, "gravel") for v in (
            "compacted", "gravel", "fine_gravel", "unpaved", "pebblestone",
        )],
        *[(v, "natural") for v in (
            "ground", "dirt", "earth", "grass", "mud", "sand", "woodchips",
        )],
        *[(v, "rock") for v in ("rock", "bare_rock", "stone")],
        ("Asphalt", "paved"),
        ("gravel;dirt", "gravel"),
    ],
)
def test_surface_tag_maps_to_a_class_and_beats_the_highway(surface, expected) -> None:
    tags = {"surface": surface, "highway": "path"}

    assert surface_class_from_tag(tags) == expected
    assert surface_class(tags) == expected


@pytest.mark.parametrize(
    "tags, expected",
    [
        *[({"highway": h}, "paved") for h in (
            "motorway", "trunk", "primary", "secondary", "tertiary", "residential",
            "service", "living_street", "pedestrian", "cycleway", "unclassified",
            "primary_link",
        )],
        ({"highway": "track", "tracktype": "grade1"}, "paved"),
        ({"highway": "track", "tracktype": "grade2"}, "gravel"),
        ({"highway": "track", "tracktype": "grade3"}, "gravel"),
        ({"highway": "track", "tracktype": "grade4"}, "natural"),
        ({"highway": "track", "tracktype": "grade5"}, "natural"),
        ({"highway": "track"}, "gravel"),
        ({"highway": "track", "tracktype": "grade9"}, "gravel"),
        *[({"highway": h}, "natural") for h in ("path", "footway", "steps", "bridleway")],
        ({"highway": "via_ferrata"}, "rock"),
        ({"highway": "construction"}, None),
        ({"highway": "ladder"}, None),
        ({}, None),
    ],
)
def test_untagged_way_is_classified_from_its_highway_kind(tags, expected) -> None:
    assert surface_class_from_tag(tags) is None
    assert surface_class(tags) == expected


def test_unlisted_surface_falls_back_to_the_highway() -> None:
    assert surface_class_from_tag({"surface": "metal", "highway": "path"}) is None
    assert surface_class({"surface": "metal", "highway": "path"}) == "natural"
    assert surface_class({"surface": "metal", "highway": "residential"}) == "paved"
    assert surface_class({"surface": "metal", "highway": "construction"}) is None
    assert surface_class({"surface": "metal"}) is None


def _trail_inputs(*ways, trailhead="south", chain_nodes=None):
    """One DayHike (id 1) on a straight line north, nodes every 100 m.

    Each way is (tags, first_node, last_node) in node indexes; the way list is
    the stored member order. ``trailhead`` is the end the Trailhead is at.
    """
    count = chain_nodes or max(last for _, _, last in ways) + 1
    lats = [_metres_north(100 * i) for i in range(count)]
    vertices = pd.DataFrame(
        {
            "dayhike_id": 1,
            "seq": range(count),
            "node_id": range(count),
            "latitude": lats,
            "longitude": 15.0,
        }
    )
    ways_frame = pd.DataFrame(
        {
            "dayhike_id": 1,
            "seq": range(len(ways)),
            "way_id": range(len(ways)),
            "node_ids": [json.dumps(list(range(f, l + 1))) for _, f, l in ways],
            "tags": [json.dumps(tags) for tags, _, _ in ways],
            "in_chain": [1 if tags.get("_in_chain", True) else 0 for tags, _, _ in ways],
            "geometry": [
                LineString([(15.0, lats[i]) for i in range(f, l + 1)])
                for _, f, l in ways
            ],
        }
    )
    dayhikes = pd.DataFrame(
        {
            "id": [1],
            "trailhead_latitude": [lats[0] if trailhead == "south" else lats[-1]],
            "trailhead_longitude": [15.0],
        }
    )
    return dayhikes, vertices, ways_frame


ASPHALT = {"highway": "service", "surface": "asphalt"}
TRACK = {"highway": "track", "tracktype": "grade3"}
PATH = {"highway": "path"}
ROCK = {"highway": "path", "surface": "rock"}


def test_surface_mix_is_length_fractions_over_in_chain_classified_ways() -> None:
    # 300 m asphalt, 100 m track, 400 m path, 200 m rock; a 500 m unclassified
    # way and a 500 m way outside the chain do not count.
    dayhikes, vertices, ways = _trail_inputs(
        (ASPHALT, 0, 3),
        (TRACK, 3, 4),
        (PATH, 4, 8),
        (ROCK, 8, 10),
        ({"highway": "construction"}, 10, 15),
        ({**PATH, "_in_chain": False}, 15, 20),
    )

    facts = trail_facts(dayhikes, vertices, ways)

    assert list(facts.columns) == [*SURFACE_SHARE_COLUMNS, "tarmac_approach_m"]
    mix_ = facts.loc[1, list(SURFACE_SHARE_COLUMNS)].to_numpy(float)
    assert mix_ == pytest.approx([0.3, 0.1, 0.4, 0.2], abs=0.002)
    assert mix_.sum() == pytest.approx(1.0)


def test_surface_mix_is_missing_when_no_in_chain_way_is_classified() -> None:
    dayhikes, vertices, ways = _trail_inputs(({"highway": "construction"}, 0, 3))

    facts = trail_facts(dayhikes, vertices, ways)

    assert facts.loc[1].isna().all()


def test_tarmac_approach_is_the_paved_length_before_the_first_unpaved_way() -> None:
    # 300 m asphalt, then a path, then asphalt again (not counted).
    dayhikes, vertices, ways = _trail_inputs(
        (ASPHALT, 0, 3), (PATH, 3, 6), (ASPHALT, 6, 8)
    )

    facts = trail_facts(dayhikes, vertices, ways)

    assert facts.loc[1, "tarmac_approach_m"] == pytest.approx(300.0, abs=1.0)


def test_tarmac_approach_starts_at_the_chain_end_nearest_the_trailhead() -> None:
    inputs = ((ASPHALT, 0, 3), (PATH, 3, 6), (ASPHALT, 6, 8))
    south = trail_facts(*_trail_inputs(*inputs, trailhead="south"))
    north = trail_facts(*_trail_inputs(*inputs, trailhead="north"))

    assert south.loc[1, "tarmac_approach_m"] == pytest.approx(300.0, abs=1.0)
    assert north.loc[1, "tarmac_approach_m"] == pytest.approx(200.0, abs=1.0)


def test_tarmac_approach_does_not_depend_on_the_stored_way_order() -> None:
    # Stored out of walking order, as when ingest glues a way onto the start.
    dayhikes, vertices, ways = _trail_inputs(
        (PATH, 3, 6), (ASPHALT, 0, 3), (ASPHALT, 6, 8)
    )

    facts = trail_facts(dayhikes, vertices, ways)

    assert facts.loc[1, "tarmac_approach_m"] == pytest.approx(300.0, abs=1.0)


def test_tarmac_approach_is_zero_when_the_first_way_is_unpaved() -> None:
    dayhikes, vertices, ways = _trail_inputs((PATH, 0, 3), (ASPHALT, 3, 6))

    assert trail_facts(dayhikes, vertices, ways).loc[1, "tarmac_approach_m"] == 0.0


def test_tarmac_approach_is_the_whole_length_when_everything_is_paved() -> None:
    dayhikes, vertices, ways = _trail_inputs((ASPHALT, 0, 3), (ASPHALT, 3, 8))

    facts = trail_facts(dayhikes, vertices, ways)

    assert facts.loc[1, "tarmac_approach_m"] == pytest.approx(800.0, abs=1.0)
    assert facts.loc[1, "paved_share"] == pytest.approx(1.0)


def test_tarmac_approach_of_a_loop_is_the_shorter_way_out() -> None:
    # The last way returns to node 0: both ends are at the Trailhead, and the
    # approach is 300 m of asphalt one way round, 200 m the other.
    dayhikes, vertices, ways = _trail_inputs(
        (ASPHALT, 0, 3), (PATH, 3, 6), (ASPHALT, 6, 8)
    )
    ways.loc[2, "node_ids"] = json.dumps([6, 7, 0])

    facts = trail_facts(dayhikes, vertices, ways)

    assert facts.loc[1, "tarmac_approach_m"] == pytest.approx(200.0, abs=1.0)
    assert trail_facts(dayhikes, _reversed(vertices), ways).equals(facts)


def test_trail_facts_are_the_same_for_the_reversed_vertex_chain() -> None:
    dayhikes, vertices, ways = _trail_inputs(
        (ASPHALT, 0, 3), (PATH, 3, 6), (ASPHALT, 6, 8)
    )

    assert trail_facts(dayhikes, _reversed(vertices), ways).equals(
        trail_facts(dayhikes, vertices, ways)
    )


def test_trail_facts_on_the_fixture_are_all_natural_path_with_no_tarmac(
    fixture_catalog: Path,
) -> None:
    vertices, _, dayhikes, ways = _whole_fixture(fixture_catalog)

    facts = trail_facts(dayhikes, vertices, ways)

    assert set(facts.index) == set(dayhikes["id"])
    assert facts.loc[1001, "natural_share"] == 1.0
    assert facts.loc[1001, "tarmac_approach_m"] == 0.0
    assert trail_facts(dayhikes, _reversed(vertices), ways).equals(facts)


def _write_dem_tile(dem_dir, elevation, *, grade_east=0.0, grade_north=0.0):
    """A small FABDEM-named tile around (45, 15) at FABDEM's 1 arc-second cells.

    Height rises ``grade_north`` metres per metre northward and ``grade_east``
    per metre eastward. ``elevation=None`` writes nodata everywhere.
    """
    import rasterio
    from rasterio.transform import from_origin

    cell = 1 / 3600
    west, north, size = 14.99, 45.01, 72
    rows, cols = np.mgrid[0:size, 0:size]
    lat = north - (rows + 0.5) * cell
    lon = west + (cols + 0.5) * cell
    north_m = np.radians(lat - 45.0) * 6_371_000
    east_m = np.radians(lon - 15.0) * 6_371_000 * math.cos(math.radians(45.0))
    if elevation is None:
        data = np.full((size, size), -9999.0)
    else:
        data = elevation + grade_north * north_m + grade_east * east_m
    dem_dir.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        dem_dir / "N45E015_FABDEM_V1-2.tif",
        "w",
        driver="GTiff",
        height=size,
        width=size,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(west, north, cell, cell),
        nodata=-9999.0,
    ) as dataset:
        dataset.write(data.astype("float32"), 1)


# A 400 m walk due east of (45, 15), well inside the tile.
EAST_WALK = _chain(*[(45.0, _metres_east(100 * i)) for i in range(5)])


@pytest.mark.parametrize(
    ("grade_east", "grade_north", "expected"),
    [
        (0.0, 0.3, 1.0),  # rises northward: the slope faces south
        (0.0, -0.3, 0.0),  # faces north
        (-0.3, 0.0, 0.5),  # falls eastward: faces east
        (0.3, 0.0, 0.5),  # faces west
        (0.3, 0.3, (1 + math.cos(math.radians(45))) / 2),  # faces south-west
    ],
)
def test_southness_scores_south_1_north_0_east_and_west_half(
    tmp_path: Path, grade_east, grade_north, expected
) -> None:
    _write_dem_tile(tmp_path, 1000.0, grade_east=grade_east, grade_north=grade_north)

    southness = solar_aspect_southness(EAST_WALK, tmp_path)

    assert southness.loc[1] == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize("grade", [0.0, 0.03])  # flat, and under 2 degrees
def test_southness_leaves_out_flat_points_and_is_missing_with_none_sloped(
    tmp_path: Path, grade
) -> None:
    _write_dem_tile(tmp_path, 1000.0, grade_north=grade)

    assert math.isnan(solar_aspect_southness(EAST_WALK, tmp_path).loc[1])


def test_southness_is_missing_without_a_covering_tile_or_with_nodata(
    tmp_path: Path,
) -> None:
    assert math.isnan(solar_aspect_southness(EAST_WALK, tmp_path / "none").loc[1])
    _write_dem_tile(tmp_path, None)
    assert math.isnan(solar_aspect_southness(EAST_WALK, tmp_path).loc[1])


def test_southness_does_not_depend_on_the_walking_direction(tmp_path: Path) -> None:
    _write_dem_tile(tmp_path, 1000.0, grade_east=0.2, grade_north=0.1)
    long_walk = _chain(*[(45.0, _metres_east(10 * i - 300)) for i in range(61)])

    forward = solar_aspect_southness(long_walk, tmp_path)

    assert solar_aspect_southness(_reversed(long_walk), tmp_path).loc[1] == pytest.approx(
        forward.loc[1], abs=1e-12
    )


def _sample_ridge_walk_climate(catalog_path: Path) -> None:
    """Ridge Walk (1001) gets twelve months and its regime; Valley Path none."""
    import sqlite3

    connection = sqlite3.connect(catalog_path)
    try:
        connection.executemany(
            "INSERT INTO climate_months VALUES (1001, ?, ?, ?, ?)",
            [(m, m - 5.0, 10.0 * m, 1000.0 * m) for m in range(1, 13)],
        )
        connection.execute(
            "UPDATE feature_vectors SET temperature_c = 2.0, "
            "precipitation_mm = 65.0, solar_radiation = 7000.0 WHERE dayhike_id = 1001"
        )
        connection.commit()
    finally:
        connection.close()


def test_load_region_carries_the_twelve_climate_months(fixture_catalog: Path) -> None:
    _sample_ridge_walk_climate(fixture_catalog)

    _, _, dayhikes, _ = _whole_fixture(fixture_catalog)

    hikes = dayhikes.set_index("id")
    assert hikes.at[1001, "temperature_c_01"] == -4.0
    assert hikes.at[1001, "precipitation_mm_07"] == 70.0
    assert hikes.at[1001, "solar_radiation_12"] == 12_000.0
    assert hikes.loc[1002, list(CLIMATE_MONTH_COLUMNS)].isna().all()


def test_weather_facts_are_the_regime_the_months_and_southness(
    fixture_catalog: Path,
) -> None:
    _sample_ridge_walk_climate(fixture_catalog)
    vertices, _, dayhikes, _ = _whole_fixture(fixture_catalog)

    facts = weather_facts(dayhikes, vertices, fixture_catalog.parent / "dem")

    assert list(facts.columns) == [
        "temperature_c",
        "precipitation_mm",
        "solar_radiation",
        *CLIMATE_MONTH_COLUMNS,
        "southness",
    ]
    assert set(facts.index) == set(dayhikes["id"])
    ridge = facts.loc[1001]
    assert (ridge["temperature_c"], ridge["precipitation_mm"], ridge["solar_radiation"]) == (
        2.0,
        65.0,
        7000.0,
    )
    assert ridge["temperature_c_07"] == 2.0
    # The fixture has no DEM tile: southness is missing, never 0.
    assert facts["southness"].isna().all()
    assert facts.loc[1002].isna().all()


# A 1,000 m walk due north of (45, 15): vertices every 250 m.
WALK = _chain(*[(_metres_north(250 * i), 15.0) for i in range(5)])
WALK_HIKE = pd.DataFrame({"id": [1]})


def _water_at(*metres_along, aside_m=10.0):
    return _amenity_points(
        "drinking_water",
        *[(_metres_north(m), _metres_east(aside_m)) for m in metres_along],
    )


def _water(points, chain=WALK):
    return water_facts(WALK_HIKE, chain, points).loc[1]


def test_dry_stretch_with_no_water_is_the_length_of_the_dayhike() -> None:
    facts = _water(_water_at())

    assert facts["water_count"] == 0
    assert facts["dry_stretch_m"] == pytest.approx(1_000.0, abs=1.0)


def test_dry_stretch_with_water_at_the_start_is_still_the_whole_walk() -> None:
    facts = _water(_water_at(0.0))

    assert facts["water_count"] == 1
    assert facts["dry_stretch_m"] == pytest.approx(1_000.0, abs=1.0)


def test_dry_stretch_with_one_water_point_in_the_middle_is_the_longer_side() -> None:
    assert _water(_water_at(400.0))["dry_stretch_m"] == pytest.approx(600.0, abs=1.0)
    assert _water(_water_at(700.0))["dry_stretch_m"] == pytest.approx(700.0, abs=1.0)


def test_dry_stretch_with_several_water_points_is_the_longest_stretch() -> None:
    # Stretches: 0-200, 200-300, 300-750, 750-1000.
    facts = _water(_water_at(750.0, 200.0, 300.0))

    assert facts["water_count"] == 3
    assert facts["dry_stretch_m"] == pytest.approx(450.0, abs=1.0)


def test_two_water_points_at_the_same_position_count_twice_and_split_once() -> None:
    facts = _water(_water_at(400.0, 400.0))

    assert facts["water_count"] == 2
    assert facts["dry_stretch_m"] == pytest.approx(600.0, abs=1.0)


def test_water_count_includes_only_corridor_points() -> None:
    inside = _water_at(500.0, aside_m=90.0)
    outside = _water_at(100.0, aside_m=110.0)

    facts = _water(pd.concat([inside, outside], ignore_index=True))

    assert facts["water_count"] == 1
    # The point 110 m aside does not split the walk at 100 m.
    assert facts["dry_stretch_m"] == pytest.approx(500.0, abs=1.0)


def test_water_position_is_along_the_line_not_the_nearest_vertex() -> None:
    # Beside the middle of one 600 m segment, 90 m aside: 300 m from either vertex.
    chain = _chain((45.0, 15.0), (_metres_north(600), 15.0), (_metres_north(700), 15.0))

    facts = _water(_water_at(150.0, aside_m=90.0), chain=chain)

    assert facts["dry_stretch_m"] == pytest.approx(550.0, abs=1.0)


def test_water_points_are_only_drinking_water_of_this_dayhike() -> None:
    mixed = pd.concat(
        [
            _water_at(500.0),
            _amenity_points("hut", (_metres_north(100), _metres_east(10))),
        ],
        ignore_index=True,
    )
    other = _chain((45.5, 15.0), (45.6, 15.0), dayhike_id=2)
    both = pd.concat([WALK, other], ignore_index=True)

    facts = water_facts(pd.DataFrame({"id": [1, 2]}), both, mixed)

    assert list(facts.columns) == ["water_count", "dry_stretch_m"]
    assert list(facts["water_count"]) == [1, 0]
    assert facts.loc[1, "dry_stretch_m"] == pytest.approx(500.0, abs=1.0)
    assert facts.loc[2, "dry_stretch_m"] == pytest.approx(11_119.5, abs=5.0)


def test_water_facts_are_the_same_for_the_reversed_chain() -> None:
    points = _water_at(100.0, 150.0, 800.0)

    facts = water_facts(WALK_HIKE, WALK, points)
    turned = water_facts(WALK_HIKE, _reversed(WALK), points)

    assert turned.loc[1, "water_count"] == facts.loc[1, "water_count"]
    assert turned.loc[1, "dry_stretch_m"] == pytest.approx(facts.loc[1, "dry_stretch_m"])


def test_water_facts_on_the_fixture_put_its_water_point_on_ridge_walk(
    fixture_catalog: Path,
) -> None:
    vertices, amenities, dayhikes, _ = _whole_fixture(fixture_catalog)

    facts = water_facts(dayhikes, vertices, amenities)

    assert set(facts.index) == set(dayhikes["id"])
    assert facts.loc[1001, "water_count"] == 1
    assert 0 < facts.loc[1001, "dry_stretch_m"] < dayhikes.set_index("id").at[1001, "length_m"]
    assert facts.loc[1002, "water_count"] == 0
    assert facts.loc[1002, "dry_stretch_m"] == pytest.approx(
        dayhikes.set_index("id").at[1002, "length_m"], rel=0.01
    )
    turned = water_facts(dayhikes, _reversed(vertices), amenities)
    assert turned["water_count"].equals(facts["water_count"])
    assert turned["dry_stretch_m"].to_numpy() == pytest.approx(facts["dry_stretch_m"].to_numpy())


# Facilities: a DayHike whose Trailhead is (45, 15) and whose line runs 1,000 m
# north of it.
FACILITIES_HIKE = pd.DataFrame(
    {"id": [1], "trailhead_latitude": [45.0], "trailhead_longitude": [15.0]}
)


def _facilities(*groups, chain=WALK):
    points = pd.concat(
        [_amenity_points(kind, *spots) for kind, spots in groups], ignore_index=True
    )
    points["osm_key"] = [f"n{i}" for i in range(len(points))]
    return facilities_facts(FACILITIES_HIKE, chain, points).loc[1]


def test_facilities_facts_are_five_flags_in_the_documented_order() -> None:
    facts = facilities_facts(
        FACILITIES_HIKE, WALK, _amenity_points("hut", (_metres_north(500), _metres_east(10)))
    )

    assert list(facts.columns) == list(FACILITIES_FLAG_COLUMNS)
    assert FACILITIES_FLAG_COLUMNS == (
        "hut_along",
        "restaurant_cafe_along",
        "shelter_along",
        "restaurant_cafe_trailhead",
        "parking_trailhead",
    )


def test_a_hut_in_the_corridor_sets_only_the_hut_flag() -> None:
    flags = _facilities(("hut", [(_metres_north(700), _metres_east(50))]))

    assert flags.to_dict() == {
        "hut_along": True,
        "restaurant_cafe_along": False,
        "shelter_along": False,
        "restaurant_cafe_trailhead": False,
        "parking_trailhead": False,
    }


def test_a_shelter_does_not_set_the_hut_flag_and_a_hut_not_the_shelter_flag() -> None:
    shelter = _facilities(("shelter", [(_metres_north(700), _metres_east(50))]))
    hut = _facilities(("hut", [(_metres_north(700), _metres_east(50))]))

    assert (shelter["shelter_along"], shelter["hut_along"]) == (True, False)
    assert (hut["hut_along"], hut["shelter_along"]) == (True, False)


def test_a_restaurant_or_cafe_in_the_corridor_is_along_not_at_the_trailhead() -> None:
    flags = _facilities(("restaurant_cafe", [(_metres_north(800), _metres_east(30))]))

    assert flags["restaurant_cafe_along"]
    assert not flags["restaurant_cafe_trailhead"]


def test_along_flags_stop_at_the_100_m_corridor() -> None:
    inside = _facilities(("hut", [(_metres_north(700), _metres_east(90))]))
    outside = _facilities(("hut", [(_metres_north(700), _metres_east(110))]))

    assert inside["hut_along"]
    assert not outside["hut_along"]


def test_trailhead_flags_stop_at_500_m_from_the_trailhead() -> None:
    # Due east of the Trailhead, away from the line (which runs north).
    near = _facilities(
        ("restaurant_cafe", [(45.0, _metres_east(450))]), ("parking", [(45.0, _metres_east(450))])
    )
    far = _facilities(
        ("restaurant_cafe", [(45.0, _metres_east(550))]), ("parking", [(45.0, _metres_east(550))])
    )

    assert near["restaurant_cafe_trailhead"] and near["parking_trailhead"]
    assert not far["restaurant_cafe_trailhead"] and not far["parking_trailhead"]
    # Not along the line either way.
    assert not near["restaurant_cafe_along"]


def test_trailhead_distance_is_from_the_catalog_trailhead_not_the_line_start() -> None:
    # The Trailhead is the far end of the line; the amenity sits 300 m from it
    # and 700 m from the first vertex.
    hike = FACILITIES_HIKE.assign(trailhead_latitude=_metres_north(1_000))
    spots = _amenity_points("parking", (_metres_north(1_000), _metres_east(300)))

    flags = facilities_facts(hike, WALK, spots).loc[1]

    assert flags["parking_trailhead"]
    assert not facilities_facts(FACILITIES_HIKE, WALK, spots).loc[1, "parking_trailhead"]


def test_facilities_ignore_other_kinds_other_dayhikes_and_use_each_trailhead() -> None:
    far_walk = _chain((45.5, 15.0), (45.6, 15.0), dayhike_id=2)
    both = pd.concat([WALK, far_walk], ignore_index=True)
    hikes = pd.DataFrame(
        {"id": [1, 2], "trailhead_latitude": [45.0, 45.5], "trailhead_longitude": [15.0, 15.0]}
    )
    points = pd.concat(
        [
            _amenity_points("hut", (_metres_north(500), _metres_east(10))),
            _amenity_points("drinking_water", (_metres_north(100), _metres_east(10))),
            _amenity_points("viewpoint", (_metres_north(100), _metres_east(10))),
        ],
        ignore_index=True,
    )
    points["osm_key"] = [f"n{i}" for i in range(len(points))]

    facts = facilities_facts(hikes, both, points)

    assert list(facts.index) == [1, 2]
    assert list(facts["hut_along"]) == [True, False]
    assert not facts.drop(columns="hut_along").to_numpy().any()


def test_facilities_with_no_amenities_at_all_are_all_false() -> None:
    facts = facilities_facts(FACILITIES_HIKE, WALK, _amenity_points("hut"))

    assert not facts.to_numpy().any()


def test_facilities_flags_are_the_same_for_the_reversed_chain() -> None:
    points = pd.concat(
        [
            _amenity_points("hut", (_metres_north(300), _metres_east(20))),
            _amenity_points("parking", (45.0, _metres_east(200))),
        ],
        ignore_index=True,
    )
    points["osm_key"] = [f"n{i}" for i in range(len(points))]

    facts = facilities_facts(FACILITIES_HIKE, WALK, points)
    turned = facilities_facts(FACILITIES_HIKE, _reversed(WALK), points)

    assert turned.equals(facts)


def test_facilities_facts_on_the_fixture_find_each_amenity_on_ridge_walk(
    fixture_catalog: Path,
) -> None:
    vertices, amenities, dayhikes, _ = _whole_fixture(fixture_catalog)

    facts = facilities_facts(dayhikes, vertices, amenities)

    assert set(facts.index) == set(dayhikes["id"])
    # The fixture Hut, cafe and Shelter sit on Ridge Walk's line (lon 15.0); its
    # cafe (100 m) and parking (400 m) are inside 500 m of the Trailhead.
    assert facts.loc[1001].to_dict() == {
        "hut_along": True,
        "restaurant_cafe_along": True,
        "shelter_along": True,
        "restaurant_cafe_trailhead": True,
        "parking_trailhead": True,
    }
    assert not facts.loc[1002].to_numpy().any()
    assert facilities_facts(dayhikes, _reversed(vertices), amenities).equals(facts)
