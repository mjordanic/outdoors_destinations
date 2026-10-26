import json
import math
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from dayhike import Catalog, StaleSimilarity, build_catalog, build_similarity
from dayhike.similarity import (
    CALIBRATION_KEYS,
    CALIBRATION_QUANTILES,
    FACT_COLUMNS,
    FACILITIES_FLAG_COLUMNS,
    LANDCOVER_SHARE_COLUMNS,
    MEASURES,
    SURFACE_SHARE_COLUMNS,
    TWIN_STEP_M,
    AmenityPoints,
    SimilarityStore,
    calibrate,
    calibrate_similarity,
    dem_heights,
    elevation_gain,
    measure_scores,
    profile_shape,
    similarity_dir_for,
    solar_aspect_points,
    solar_aspect_southness,
    steepness,
    surface_class,
    surface_class_from_tag,
    trail_facts,
    twin_groups,
    walk_facts,
    waterside_share,
    weighted_mean,
)

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
EARTH_RADIUS_M = 6_371_000
BY_NAME = {measure.name: measure for measure in MEASURES}


def _store(rows: int, profile=None, quantiles=None, **columns) -> SimilarityStore:
    """A SimilarityStore of ``rows`` DayHikes with only the given fact columns."""
    facts = np.full((len(FACT_COLUMNS), rows), np.nan, dtype=np.float32)
    for name, values in columns.items():
        facts[FACT_COLUMNS.index(name)] = values
    return SimilarityStore(
        ids=np.arange(rows),
        facts=facts,
        profile=np.full((rows, 64), np.nan) if profile is None else np.asarray(profile),
        quantiles=np.full((len(CALIBRATION_KEYS), 4), np.nan)
        if quantiles is None
        else quantiles,
        twins=np.arange(rows),
    )


def _scores(name: str, **columns) -> np.ndarray:
    """Scores of the first DayHike against every DayHike on one Measure."""
    rows = len(next(iter(columns.values())))
    store = _store(rows, **columns)
    return measure_scores(store, BY_NAME[name], 0, np.arange(rows))


def test_ratio_is_smaller_over_larger_with_a_floor() -> None:
    scores = _scores("length", length_m=[10_000, 5_000, 20_000, 10_000, np.nan])
    assert scores[:4] == pytest.approx([1.0, 0.5, 0.5, 1.0])
    assert math.isnan(scores[4])
    # Under the 1 km floor every length counts as 1 km.
    assert _scores("length", length_m=[200, 900, 2_000]) == pytest.approx([1.0, 1.0, 0.5])


def test_tolerance_is_one_minus_the_difference_over_d() -> None:
    scores = _scores("highest", highest_m=[2_000, 1_800, 1_600, 500, np.nan])
    assert scores[:4] == pytest.approx([1.0, 0.5, 0.0, 0.0])
    assert math.isnan(scores[4])


def test_water_count_is_capped_before_the_tolerance() -> None:
    scores = _scores("water_count", water_count=[5, 3, 9, 0])
    assert scores == pytest.approx([1.0, 1.0, 1.0, 0.0])


def test_mix_is_the_shared_share_and_missing_with_any_share_missing() -> None:
    shares = {
        "paved_share": [0.5, 0.0, 0.5],
        "gravel_share": [0.5, 0.5, np.nan],
        "natural_share": [0.0, 0.5, 0.5],
        "rock_share": [0.0, 0.0, 0.0],
    }
    scores = _scores("surface", **shares)
    assert scores[:2] == pytest.approx([1.0, 0.5])
    assert math.isnan(scores[2])


def test_agree_is_one_when_flags_match_including_both_absent() -> None:
    assert _scores("hut_along", hut_along=[0, 0, 1]) == pytest.approx([1.0, 1.0, 0.0])
    assert _scores("hut_along", hut_along=[1, 1, 0]) == pytest.approx([1.0, 1.0, 0.0])


_FRACTION = np.linspace(0.0, 1.0, 64)
ONE_CLIMB_EARLY = np.interp(_FRACTION, [0.0, 0.3, 1.0], [0.0, 1.0, 0.0])
ONE_CLIMB_LATE = np.interp(_FRACTION, [0.0, 0.5, 1.0], [0.0, 1.0, 0.0])
MANY_CLIMBS = (1 - np.cos(_FRACTION * 8 * np.pi)) / 2
FLAT = np.zeros(64)
NEARLY_FLAT = np.array([(i * 7) % 6 for i in range(64)]) / 50.0
SHAPES = np.array([ONE_CLIMB_EARLY, ONE_CLIMB_LATE, MANY_CLIMBS, FLAT, NEARLY_FLAT, _FRACTION])


def _profile_scores(a: int) -> np.ndarray:
    store = _store(len(SHAPES), profile=SHAPES)
    return measure_scores(store, BY_NAME["profile"], a, np.arange(len(SHAPES)))


def test_profile_is_one_for_itself_and_its_reverse_and_symmetric() -> None:
    reversed_store = _store(2, profile=np.array([ONE_CLIMB_EARLY, ONE_CLIMB_EARLY[::-1]]))
    assert measure_scores(reversed_store, BY_NAME["profile"], 0, np.arange(2)) == (
        pytest.approx([1.0, 1.0])
    )
    table = np.array([_profile_scores(a) for a in range(len(SHAPES))])
    assert np.diag(table) == pytest.approx(1.0)
    assert table == pytest.approx(table.T)
    assert ((table >= 0) & (table <= 1)).all()
    assert table[5, 3] == pytest.approx(0.5)  # a straight ramp against flat


def test_profile_matches_form_flat_with_flat() -> None:
    nearly_flat = _profile_scores(4)
    assert nearly_flat[3] >= 0.9
    assert nearly_flat[3] > nearly_flat[1]
    early = _profile_scores(0)
    assert early[1] > early[2]


def test_profile_is_missing_without_a_profile_shape() -> None:
    store = _store(2, profile=np.array([FLAT, np.full(64, np.nan)]))
    assert math.isnan(measure_scores(store, BY_NAME["profile"], 0, np.arange(2))[1])


def test_every_measure_is_one_for_itself_symmetric_and_bounded() -> None:
    rng = np.random.default_rng(1)
    rows = 30
    columns = {name: rng.random(rows) * 3_000 for name in FACT_COLUMNS}
    for flag in (*FACILITIES_FLAG_COLUMNS, "peak_summited", "viewpoint_along", "round_trip"):
        columns[flag] = rng.integers(0, 2, rows)
    for group in (LANDCOVER_SHARE_COLUMNS, SURFACE_SHARE_COLUMNS):
        shares = rng.dirichlet(np.ones(len(group)), rows)
        for index, name in enumerate(group):
            columns[name] = shares[:, index]
    store = _store(rows, profile=rng.random((rows, 64)), **columns)
    everyone = np.arange(rows)
    for measure in MEASURES:
        table = np.array([measure_scores(store, measure, a, everyone) for a in everyone])
        assert np.diag(table) == pytest.approx(1.0), measure.name
        assert table == pytest.approx(table.T), measure.name
        assert ((table >= 0) & (table <= 1 + 1e-6)).all(), measure.name
        paired = measure_scores(store, measure, everyone, everyone[::-1])
        assert paired == pytest.approx(table[everyone, everyone[::-1]]), measure.name


def test_monthly_measures_read_the_query_month_column() -> None:
    store = _store(2, temperature_c=[20, 20], temperature_c_01=[0, 10])
    regime = measure_scores(store, BY_NAME["temperature"], 0, np.arange(2))
    january = measure_scores(store, BY_NAME["temperature"], 0, np.arange(2), month=1)
    assert regime == pytest.approx([1.0, 1.0])
    assert january == pytest.approx([1.0, 0.0])
    # A Measure that is not monthly ignores the month.
    assert measure_scores(
        _store(2, highest_m=[0, 200]), BY_NAME["highest"], 0, np.arange(2), month=1
    ) == pytest.approx([1.0, 0.5])


def test_calibration_is_the_mid_rank_share_of_random_pairs_below() -> None:
    quantiles = np.full((len(CALIBRATION_KEYS), 4), np.nan)
    quantiles[CALIBRATION_KEYS.index("length")] = [0.2, 0.5, 0.5, 0.9]
    store = _store(1, quantiles=quantiles)
    scores = np.array([0.1, 0.2, 0.5, 0.7, 0.95, np.nan])
    calibrated = calibrate(store, "length", scores)
    assert calibrated[:5] == pytest.approx([0.0, 0.125, 0.5, 0.75, 1.0])
    assert math.isnan(calibrated[5])
    # A key without random-pair scores cannot be calibrated.
    assert np.isnan(calibrate(store, "profile", scores)).all()


def test_weighted_mean_renormalizes_over_the_scored_columns() -> None:
    scores = np.array([[1.0, 0.0], [1.0, np.nan], [np.nan, np.nan]])
    mean = weighted_mean(scores, np.array([100.0, 50.0]))
    assert mean[:2] == pytest.approx([2 / 3, 1.0])
    assert math.isnan(mean[2])


def _metres_north(metres, lat0=45.0):
    return lat0 + math.degrees(metres / EARTH_RADIUS_M)


def _metres_east(metres, lon0=15.0):
    return lon0 + math.degrees(metres / (EARTH_RADIUS_M * math.cos(math.radians(45.0))))


def _north_line(elevations, step_m=100.0):
    """Vertices due north of (45, 15), ``step_m`` apart, with these elevations."""
    lats = [_metres_north(i * step_m) for i in range(len(elevations))]
    return np.array(lats), np.full(len(lats), 15.0), np.array(elevations, dtype=float)


def test_profile_shape_is_64_points_scaled_by_the_range() -> None:
    shape = profile_shape(*_north_line([1000.0, 1200.0, 1500.0]))

    assert len(shape) == 64
    assert (shape[0], shape[-1]) == pytest.approx((0.0, 1.0))
    halfway = np.interp(0.5, np.linspace(0.0, 1.0, 64), shape)
    assert halfway == pytest.approx(0.4, abs=0.01)
    tent = profile_shape(*_north_line([0.0, 600.0, 0.0]))
    assert tent == pytest.approx(tent[::-1])


def test_profile_shape_follows_distance_not_vertex_count() -> None:
    shape = profile_shape(*_north_line([0.0, 400.0, 400.0, 400.0, 400.0]))
    fraction = np.linspace(0.0, 1.0, 64)
    assert shape[fraction >= 0.25] == pytest.approx(1.0)
    assert shape[fraction < 0.25] == pytest.approx(fraction[fraction < 0.25] * 4)


def test_profile_shape_does_not_stretch_a_nearly_flat_profile() -> None:
    noise = [300.0 + (i * 7) % 6 for i in range(64)]
    shape = profile_shape(*_north_line(noise))
    assert shape == pytest.approx((np.array(noise) - 300.0) / 50.0)


def test_profile_shape_skips_vertices_without_elevation() -> None:
    lats, lons, _ = _north_line([0.0] * 3)
    assert profile_shape(lats, lons, [0.0, math.nan, 400.0]) == pytest.approx(
        np.linspace(0.0, 1.0, 64)
    )
    assert profile_shape(lats, lons, [math.nan, 250.0, math.nan]) is None
    assert profile_shape(lats[:1], lons[:1], [100.0]) is None


def test_elevation_gain_is_the_larger_of_climb_and_descent() -> None:
    assert elevation_gain(np.array([100.0, 400.0, 300.0])) == 300.0
    assert elevation_gain(np.array([400.0, 100.0, 200.0])) == 300.0
    assert elevation_gain(np.array([100.0, math.nan, 200.0])) == 100.0
    assert math.isnan(elevation_gain(np.array([math.nan, math.nan])))


def test_elevation_gain_ignores_jitter_below_the_hysteresis() -> None:
    flat = np.array([5.0, 8.0, 4.0, 9.0, 3.0, 7.0, 5.0] * 20)
    assert elevation_gain(flat) == 0.0
    # A noisy climb counts its rise once, not every wobble on the way up.
    climb = np.array([100.0, 106.0, 101.0, 112.0, 108.0, 125.0, 119.0, 140.0])
    assert elevation_gain(climb) == 40.0


def test_climb_grade_is_the_gain_over_the_climbing_length() -> None:
    # 1 km up 200 m, then 1 km flat: the flat km does not dilute the grade.
    climb_grade, _ = steepness(*_north_line([*np.linspace(0, 200, 11), *[200.0] * 10]))
    assert climb_grade == pytest.approx(0.2)
    there_and_back = [*np.linspace(0, 200, 11), *np.linspace(200, 0, 11)[1:]]
    assert steepness(*_north_line(there_and_back))[0] == pytest.approx(0.2)


def test_climb_grade_takes_the_direction_with_the_larger_climb() -> None:
    lat, lon, ele = _north_line([*np.linspace(300, 0, 11), *np.linspace(0, 100, 11)[1:]])
    assert steepness(lat, lon, ele)[0] == pytest.approx(0.3)
    assert steepness(lat[::-1], lon[::-1], ele[::-1]) == pytest.approx(steepness(lat, lon, ele))


def test_steepest_km_is_the_largest_height_change_over_1_km() -> None:
    assert steepness(*_north_line([0.0, 50.0, 450.0, 350.0], 1_000.0))[1] == pytest.approx(400.0)
    assert steepness(*_north_line([0.0, 30.0, 60.0], 400.0))[1] == pytest.approx(60.0)
    sparse = steepness(*_north_line([0.0, 100.0, 300.0], 1_000.0))
    dense = steepness(*_north_line([*np.linspace(0, 100, 11), *np.linspace(100, 300, 11)[1:]]))
    assert sparse == pytest.approx(dense)


def test_a_flat_walk_has_zero_steepness_and_no_ele_is_missing() -> None:
    assert steepness(*_north_line([500.0, 500.0, 500.0])) == (0.0, 0.0)
    lats, lons, _ = _north_line([0.0, 0.0])
    assert np.isnan(steepness(lats, lons, [math.nan, math.nan])).all()


@pytest.mark.parametrize(
    "surface, expected",
    [
        *[(v, "paved") for v in ("asphalt", "concrete", "concrete:plates", "paving_stones", "sett")],
        *[(v, "gravel") for v in ("compacted", "gravel", "fine_gravel", "unpaved")],
        *[(v, "natural") for v in ("ground", "dirt", "earth", "grass", "mud", "sand")],
        *[(v, "rock") for v in ("rock", "bare_rock", "stone")],
        ("Asphalt", "paved"),
        ("gravel;dirt", "gravel"),
    ],
)
def test_surface_tag_maps_to_a_class_and_beats_the_highway(surface, expected) -> None:
    assert surface_class_from_tag({"surface": surface}) == expected
    assert surface_class({"surface": surface, "highway": "path"}) == expected


@pytest.mark.parametrize(
    "tags, expected",
    [
        ({"highway": "residential"}, "paved"),
        ({"highway": "secondary_link"}, "paved"),
        ({"highway": "track", "tracktype": "grade1"}, "paved"),
        ({"highway": "track", "tracktype": "grade4"}, "natural"),
        ({"highway": "track"}, "gravel"),
        ({"highway": "path"}, "natural"),
        ({"highway": "via_ferrata"}, "rock"),
        ({"highway": "construction"}, None),
    ],
)
def test_untagged_way_is_classified_from_its_highway_kind(tags, expected) -> None:
    assert surface_class(tags) == expected


def test_surface_mix_is_length_fractions_over_classified_walked_ways() -> None:
    facts = trail_facts(
        [(300.0, "paved"), (100.0, "gravel"), (400.0, "natural"), (200.0, "rock"), (500.0, None)],
        loop=False,
    )
    assert [facts[c] for c in SURFACE_SHARE_COLUMNS] == pytest.approx([0.3, 0.1, 0.4, 0.2])
    assert np.isnan(list(trail_facts([(500.0, None)], loop=False).values())).all()


def test_tarmac_approach_is_the_paved_start_of_the_walk() -> None:
    walked = [(300.0, "paved"), (300.0, "natural"), (200.0, "paved")]
    assert trail_facts(walked, loop=False)["tarmac_approach_m"] == 300.0
    assert trail_facts(walked[::-1], loop=False)["tarmac_approach_m"] == 200.0
    # On a loop the shorter way out counts, whichever way it is walked.
    assert trail_facts(walked, loop=True)["tarmac_approach_m"] == 200.0
    assert trail_facts([(300.0, "natural"), (300.0, "paved")], loop=False)[
        "tarmac_approach_m"
    ] == 0.0
    assert trail_facts([(300.0, "paved"), (500.0, "paved")], loop=False)[
        "tarmac_approach_m"
    ] == 800.0
    assert trail_facts([(300.0, "paved"), (100.0, None)], loop=False)["tarmac_approach_m"] == 300.0


# A 1,000 m walk due north of (45, 15): vertices every 250 m.
WALK_LAT = np.array([_metres_north(250 * i) for i in range(5)])
WALK_LON = np.full(5, 15.0)
NO_POINTS = AmenityPoints(np.empty((0, 2)), [])


def _walk(*amenities, peaks=(), lat=WALK_LAT, lon=WALK_LON):
    """walk_facts of WALK with (kind, metres along, metres east) amenities."""
    along = AmenityPoints(
        [(_metres_north(m), _metres_east(e)) for _, m, e in amenities],
        [kind for kind, _, _ in amenities],
    )
    peak_points = AmenityPoints([(_metres_north(m), _metres_east(e)) for m, e in peaks], ["peak"] * len(peaks))
    return walk_facts(lat, lon, np.full(len(lat), np.nan), [], along, peak_points)


def test_corridor_is_in_at_90_m_and_out_at_110_m_from_the_line() -> None:
    assert _walk(("hut", 0, 90))["hut_along"] == 1.0
    assert _walk(("hut", 1_000, 110))["hut_along"] == 0.0
    # Beside the middle of a 250 m segment, 152 m from its nearest vertex.
    assert _walk(("hut", 125, 90))["hut_along"] == 1.0


def test_each_along_flag_is_set_by_its_own_kind() -> None:
    facts = _walk(("shelter", 500, 10), ("viewpoint", 100, 50))
    assert (facts["shelter_along"], facts["hut_along"]) == (1.0, 0.0)
    assert facts["viewpoint_along"] == 1.0
    assert (facts["restaurant_cafe_along"], facts["water_count"]) == (0.0, 0.0)


def test_peak_summited_is_50_m_and_peaks_around_is_2_km() -> None:
    facts = _walk(("peak", 500, 40), peaks=[(500, 40), (500, 1_900), (500, 2_100)])
    assert (facts["peak_summited"], facts["peaks_around"]) == (1.0, 2.0)
    assert _walk(("peak", 500, 60))["peak_summited"] == 0.0


def test_dry_stretch_is_the_longest_walk_between_water_sources() -> None:
    assert _walk()["dry_stretch_m"] == pytest.approx(1_000.0, abs=1.0)
    assert _walk(("drinking_water", 0, 10))["dry_stretch_m"] == pytest.approx(1_000.0, abs=1.0)
    assert _walk(("spring", 400, 10))["dry_stretch_m"] == pytest.approx(600.0, abs=1.0)
    facts = _walk(("water_tap", 750, 10), ("water_point", 200, 10), ("spring", 300, 10))
    assert facts["water_count"] == 3
    assert facts["dry_stretch_m"] == pytest.approx(450.0, abs=1.0)
    twice = _walk(("spring", 400, 10), ("drinking_water", 400, 10))
    assert (twice["water_count"], twice["dry_stretch_m"]) == (2, pytest.approx(600.0, abs=1.0))
    outside = _walk(("spring", 500, 90), ("spring", 100, 110))
    assert (outside["water_count"], outside["dry_stretch_m"]) == (1, pytest.approx(500.0, abs=1.0))


def test_walk_facts_do_not_depend_on_the_walking_direction() -> None:
    amenities = (("spring", 300, 30), ("hut", 800, 60), ("peak", 990, 20))
    forward = _walk(*amenities, peaks=[(300, 1_500)])
    backward = _walk(*amenities, peaks=[(300, 1_500)], lat=WALK_LAT[::-1], lon=WALK_LON[::-1])
    assert backward == pytest.approx(forward, nan_ok=True)


def _ring(end_gap_m):
    """A 1.5 km out-north, east, back-south walk ending ``end_gap_m`` east of its start."""
    lat = np.array([45.0, _metres_north(500), _metres_north(500), 45.0])
    lon = np.array([15.0, 15.0, _metres_east(500), _metres_east(end_gap_m)])
    return walk_facts(lat, lon, np.full(4, np.nan), [], NO_POINTS, NO_POINTS)


def test_round_trip_ends_within_50_m_of_its_trailhead() -> None:
    assert _ring(40)["round_trip"] == 1.0
    assert _ring(60)["round_trip"] == 0.0


def test_a_lollipop_is_not_a_round_trip() -> None:
    top = _metres_north(300)
    lat = np.array([45.0, top, _metres_north(600), _metres_north(600), top, top])
    lon = np.array([15.0, 15.0, 15.0, _metres_east(300), _metres_east(300), 15.0])
    facts = walk_facts(lat, lon, np.full(6, np.nan), [], NO_POINTS, NO_POINTS)
    assert facts["round_trip"] == 0.0


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
    north_m = np.radians(lat - 45.0) * EARTH_RADIUS_M
    east_m = np.radians(lon - 15.0) * EARTH_RADIUS_M * math.cos(math.radians(45.0))
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


def test_dem_heights_read_the_cell_holding_each_point(tmp_path: Path) -> None:
    _write_dem_tile(tmp_path, 1000.0, grade_north=0.1)
    heights = dem_heights(tmp_path, np.array([45.0, 45.0, 46.5]), np.array([15.0, 15.0, 15.0]))
    assert heights[0] == heights[1]
    assert heights[0] == pytest.approx(1000.0, abs=2.0)
    assert math.isnan(heights[2])  # no tile
    _write_dem_tile(tmp_path, None)
    assert math.isnan(dem_heights(tmp_path, np.array([45.0]), np.array([15.0]))[0])


def _southness(dem_dir, lat, lon) -> float:
    chosen = solar_aspect_points(len(lat))
    points = np.column_stack([lat[chosen], lon[chosen]])
    return solar_aspect_southness(dem_dir, points, np.zeros(len(chosen), dtype=int), 1)[0]


# A 400 m walk due east of (45, 15), well inside the tile.
EAST_LAT = np.full(5, 45.0)
EAST_LON = np.array([_metres_east(100 * i) for i in range(5)])


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

    assert _southness(tmp_path, EAST_LAT, EAST_LON) == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize("grade", [0.0, 0.03])  # flat, and under 2 degrees
def test_southness_leaves_out_flat_points_and_is_missing_with_none_sloped(
    tmp_path: Path, grade
) -> None:
    _write_dem_tile(tmp_path, 1000.0, grade_north=grade)

    assert math.isnan(_southness(tmp_path, EAST_LAT, EAST_LON))


def test_southness_is_missing_without_a_covering_tile_or_with_nodata(tmp_path: Path) -> None:
    assert math.isnan(_southness(tmp_path / "none", EAST_LAT, EAST_LON))
    _write_dem_tile(tmp_path, None)
    assert math.isnan(_southness(tmp_path, EAST_LAT, EAST_LON))


def test_southness_does_not_depend_on_the_walking_direction(tmp_path: Path) -> None:
    _write_dem_tile(tmp_path, 1000.0, grade_east=0.2, grade_north=0.1)
    lat = np.full(61, 45.0)
    lon = np.array([_metres_east(10 * i - 300) for i in range(61)])

    assert _southness(tmp_path, lat[::-1], lon[::-1]) == pytest.approx(
        _southness(tmp_path, lat, lon), abs=1e-12
    )


def _write_worldcover_tile(landcover_dir, water_east_of_m):
    """A small WorldCover-named tile around (45, 15) at its 10 m cells: water
    (class 80) east of ``water_east_of_m`` metres east of 15°, tree (10) west.
    """
    import rasterio
    from rasterio.transform import from_origin

    cell = 1 / 12_000
    west, north, size = 14.98, 45.02, 480
    lon = west + (np.arange(size) + 0.5) * cell
    data = np.where(lon > _metres_east(water_east_of_m), 80, 10).astype("uint8")
    landcover_dir.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        landcover_dir / "ESA_WorldCover_10m_2021_v200_N45E015_Map.tif",
        "w",
        driver="GTiff",
        height=size,
        width=size,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=from_origin(west, north, cell, cell),
        nodata=0,
    ) as dataset:
        dataset.write(np.broadcast_to(data, (size, size)), 1)


def _waterside(landcover_dir, lat, lon) -> float:
    points = np.column_stack([lat, lon])
    return waterside_share(landcover_dir, points, np.zeros(len(lat), dtype=int), 1)[0]


def test_waterside_is_the_share_of_the_walk_within_200_m_of_water(tmp_path: Path) -> None:
    _write_worldcover_tile(tmp_path, 1_000)
    north = np.array([_metres_north(100 * i + 100) for i in range(11)])

    assert _waterside(tmp_path, north, np.full(11, _metres_east(900))) == 1.0
    assert _waterside(tmp_path, north, np.full(11, 15.0)) == 0.0
    # Four of ten points 100 m from the water, six 1 km from it.
    beside = np.where(np.arange(10) < 4, _metres_east(900), 15.0)
    assert _waterside(tmp_path, north[:10], beside) == pytest.approx(0.4)


def test_waterside_is_missing_without_a_covering_tile(tmp_path: Path) -> None:
    assert math.isnan(_waterside(tmp_path, np.array([45.0]), np.array([15.0])))


def _route(start_m, end_m, east_m=0.0) -> np.ndarray:
    """A walk due north along ``east_m`` east of 15°, sampled as ``twin_groups`` takes it."""
    north = np.arange(start_m, end_m + 1, TWIN_STEP_M)
    return np.column_stack(
        [[_metres_north(m) for m in north], np.full(len(north), _metres_east(east_m))]
    ).astype(np.float32)


def test_twins_are_routes_that_each_lie_80_percent_on_the_other() -> None:
    routes = [
        _route(0, 3_000),
        _route(0, 3_000)[::-1],  # the same route walked the other way
        _route(0, 3_000, east_m=100),  # beside it, not on it
        _route(0, 4_500),  # holds the first, but a third of it is elsewhere
        _route(-500, 3_000),  # 86 % on the first
        None,
    ]

    assert twin_groups(routes).tolist() == [0, 0, 2, 3, 0, 5]


def _fixture_catalog(tmp_path: Path) -> Path:
    catalog_path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, catalog_path)
    return catalog_path


def _fixture_column(directory: Path, name: str) -> dict[int, float]:
    ids = np.load(directory / "ids.npy")
    facts = np.load(directory / "facts.npy")
    return dict(zip(ids.tolist(), facts[FACT_COLUMNS.index(name)].tolist()))


def test_build_similarity_writes_the_facts_of_every_catalog_dayhike(tmp_path: Path) -> None:
    catalog_path = _fixture_catalog(tmp_path)

    directory = build_similarity(catalog_path, tmp_path / "dem")

    assert directory == similarity_dir_for(catalog_path) == tmp_path / "catalog.similarity"
    catalog = Catalog.open(catalog_path)
    assert np.load(directory / "ids.npy").tolist() == [hike.id for hike in catalog.dayhikes]
    facts = np.load(directory / "facts.npy")
    assert facts.shape == (len(FACT_COLUMNS), len(catalog.dayhikes))
    assert facts.dtype == np.float32
    # Ridge Walk (1001) passes every fixture amenity; Valley Path none.
    for name in (
        "hut_along", "restaurant_cafe_along", "shelter_along", "restaurant_cafe_trailhead",
        "parking_trailhead", "peak_summited", "viewpoint_along",
    ):  # fmt: skip
        assert _fixture_column(directory, name)[1001] == 1.0, name
        assert _fixture_column(directory, name)[1002] == 0.0, name
    assert _fixture_column(directory, "water_count")[1001] == 4
    assert _fixture_column(directory, "natural_share")[1001] == 1.0
    assert _fixture_column(directory, "tarmac_approach_m")[1001] == 0.0
    assert _fixture_column(directory, "round_trip")[1001] == 0.0
    assert _fixture_column(directory, "difficulty_rank")[1001] == 2
    assert _fixture_column(directory, "highest_m")[1001] == 500
    # No DEM tile: SolarAspect is missing, never 0.
    assert np.isnan(list(_fixture_column(directory, "southness").values())).all()
    profile = np.load(directory / "profile.npy")
    assert profile.shape == (len(catalog.dayhikes), 64)
    assert not np.isnan(profile[0]).any()


def test_build_similarity_carries_the_catalog_climate_months(tmp_path: Path) -> None:
    catalog_path = _fixture_catalog(tmp_path)
    connection = sqlite3.connect(catalog_path)
    connection.executemany(
        "INSERT INTO climate_months VALUES (1001, ?, ?, ?, ?)",
        [(m, m - 5.0, 10.0 * m, 1000.0 * m) for m in range(1, 13)],
    )
    connection.execute("UPDATE feature_vectors SET temperature_c = 2.0 WHERE dayhike_id = 1001")
    connection.commit()
    connection.close()

    directory = build_similarity(catalog_path, tmp_path / "dem")

    assert _fixture_column(directory, "temperature_c")[1001] == 2.0
    assert _fixture_column(directory, "temperature_c_01")[1001] == -4.0
    assert _fixture_column(directory, "precipitation_mm_07")[1001] == 70.0
    assert _fixture_column(directory, "solar_radiation_12")[1001] == 12_000.0
    assert math.isnan(_fixture_column(directory, "temperature_c_01")[1002])


def test_calibration_keeps_sorted_random_pair_quantiles_per_key(tmp_path: Path) -> None:
    directory = build_similarity(_fixture_catalog(tmp_path), tmp_path / "dem")

    quantiles = np.load(directory / "quantiles.npy")
    assert quantiles.shape == (len(CALIBRATION_KEYS), CALIBRATION_QUANTILES)
    length = quantiles[CALIBRATION_KEYS.index("length")]
    assert (np.diff(length) >= 0).all()
    # Only Ridge Walk has a Difficulty, so no pair can be scored on it.
    assert np.isnan(quantiles[CALIBRATION_KEYS.index("difficulty")]).all()


def test_similarity_store_refuses_facts_or_calibration_from_other_code(tmp_path: Path) -> None:
    catalog_path = _fixture_catalog(tmp_path)
    with pytest.raises(StaleSimilarity, match="build-similarity"):
        SimilarityStore.open(catalog_path)
    directory = build_similarity(catalog_path, tmp_path / "dem")
    meta_path = directory / "meta.json"
    meta = json.loads(meta_path.read_text())

    meta_path.write_text(json.dumps({**meta, "calibration_version": "old"}))
    with pytest.raises(StaleSimilarity, match="--calibrate-only"):
        SimilarityStore.open(catalog_path)
    calibrate_similarity(catalog_path)
    assert SimilarityStore.open(catalog_path).facts.shape[0] == len(FACT_COLUMNS)

    meta_path.write_text(json.dumps({**meta, "facts_version": "old"}))
    with pytest.raises(StaleSimilarity, match="build-similarity"):
        SimilarityStore.open(catalog_path)
    with pytest.raises(StaleSimilarity):
        calibrate_similarity(catalog_path)


def test_catalog_refuses_similarity_facts_for_other_dayhikes(tmp_path: Path) -> None:
    catalog_path = _fixture_catalog(tmp_path)
    build_similarity(catalog_path, tmp_path / "dem")
    connection = sqlite3.connect(catalog_path)
    for table in ("climate_months", "amenity_facts", "feature_vectors", "trailheads"):
        connection.execute(f"DELETE FROM {table} WHERE dayhike_id = 1003")
    connection.execute("DELETE FROM dayhikes WHERE id = 1003")
    connection.commit()
    connection.close()

    with pytest.raises(StaleSimilarity, match="other DayHikes"):
        Catalog.open(catalog_path).similarity


def test_store_refuses_facts_built_before_a_resample_with_the_same_ids(tmp_path: Path) -> None:
    import shutil

    from dayhike import sample_dem

    catalog_path = _fixture_catalog(tmp_path)
    _write_dem_tile(tmp_path / "dem_a", 1000.0)
    _write_dem_tile(tmp_path / "dem_b", 2000.0)
    assert sample_dem(catalog_path, tmp_path / "dem_a") > 0
    directory = build_similarity(catalog_path, tmp_path / "dem_a")
    shutil.copytree(directory, tmp_path / "kept.similarity")
    assert Catalog.open(catalog_path).similarity.ids.size > 0

    assert sample_dem(catalog_path, tmp_path / "dem_b") > 0
    shutil.copytree(tmp_path / "kept.similarity", directory)

    with pytest.raises(StaleSimilarity, match="build-similarity"):
        Catalog.open(catalog_path).similarity


def test_calibration_is_identical_when_rerun(tmp_path: Path) -> None:
    catalog_path = _fixture_catalog(tmp_path)
    directory = build_similarity(catalog_path, tmp_path / "dem")
    first = {name: (directory / name).read_bytes() for name in ("quantiles.npy",)}

    calibrate_similarity(catalog_path)

    assert {name: (directory / name).read_bytes() for name in first} == first


def test_quantiles_are_monotone_and_in_unit_interval(tmp_path: Path) -> None:
    directory = build_similarity(_fixture_catalog(tmp_path), tmp_path / "dem")

    quantiles = np.load(directory / "quantiles.npy")
    scored = quantiles[~np.isnan(quantiles).all(axis=1)]
    assert scored.size
    assert not np.isnan(scored).any()
    assert (np.diff(scored, axis=1) >= 0).all()
    assert ((scored >= 0) & (scored <= 1)).all()
