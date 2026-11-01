"""DayStages: day-sized pieces of a long tour, cut at huts, villages and parking.

Every tour is hand-built on a kilometre grid around 45N 15E: one vertex every
500 m, walking north unless a test says otherwise, and every cut point a
little east of the line.
"""

import math

from dayhike.stages import CutPoint, split

KM_LAT = 1_000.0 / 6_371_000 * 180 / math.pi


def _lat_lon(x_km: float, y_km: float) -> tuple[float, float]:
    lat = 45.0 + y_km * KM_LAT
    return lat, 15.0 + x_km * KM_LAT / math.cos(math.radians(lat))


def line(km: float, first_node: int = 1, x_km: float = 0.0) -> list[tuple[int, tuple[float, float]]]:
    """A tour ``km`` long walking north along x = ``x_km``, a vertex every 500 m."""
    return [(first_node + step, _lat_lon(x_km, step / 2)) for step in range(round(km * 2) + 1)]


def point(kind: str, name: str, at_km: float, off_km: float = 0.1, x_km: float = 0.0) -> CutPoint:
    lat, lon = _lat_lon(x_km + off_km, at_km)
    return CutPoint(kind, lat, lon, name)


def test_a_66_km_tour_with_three_huts_is_cut_at_the_huts():
    huts = [point("hut", "Refuge A", 16.5), point("hut", "Refuge B", 33), point("hut", "Refuge C", 49.5)]

    stages = split(line(66), huts)

    assert [(stage.start, stage.end) for stage in stages] == [
        (None, "Refuge A"),
        ("Refuge A", "Refuge B"),
        ("Refuge B", "Refuge C"),
        ("Refuge C", None),
    ]
    assert [round(stage.length_m) for stage in stages] == [16_500] * 4


def test_a_22_km_tour_is_not_split():
    assert split(line(22), [point("hut", "Refuge A", 11)]) == []


def test_a_hut_is_preferred_to_a_village_and_a_village_to_parking_near_the_middle():
    # The middle of a 34 km tour is at 17 km.
    village, hut, parking = point("place", "Village", 17, off_km=0.4), point("hut", "Refuge", 15), point("parking", None, 17)

    assert [stage.end for stage in split(line(34), [village, hut])][0] == "Refuge"
    (first, _) = split(line(34), [point("place", "Village", 15, off_km=0.4), parking])
    assert (first.end, round(first.length_m)) == ("Village", 15_000)
    (first, _) = split(line(34), [parking])
    assert (first.end, round(first.length_m)) == (None, 17_000)


def test_two_overlapping_tours_give_identical_stages_where_they_overlap():
    # Tour B comes in 16 km from the west and joins tour A at A's first hut,
    # then walks A to its end: the same nodes, the same huts.
    tour_a = line(66)
    branch = [(1_000 + step, _lat_lon(-16 + step / 2, 16.5)) for step in range(32)]
    tour_b = branch + tour_a[33:]
    huts = [point("hut", f"Refuge {km}", km) for km in (16.5, 33, 49.5)]
    villages = [point("place", f"Village {km}", km, off_km=0.3) for km in (25, 41, 58)]

    stages_a = split(tour_a, huts + villages)
    stages_b = split(tour_b, huts + villages)

    shared = [stage.vertices for stage in stages_a if stage.vertices[0][0] >= tour_b[len(branch)][0]]
    assert len(shared) >= 2
    assert shared == [stage.vertices for stage in stages_b][-len(shared) :]


def test_a_stretch_with_no_cut_point_for_over_25_km_gets_no_stage():
    # 80 km with huts at 16 and 32 km only, then 48 km with none; and 60 km
    # with a first hut only at 30 km.
    late = split(line(80), [point("hut", "Refuge A", 16), point("hut", "Refuge B", 32)])
    early = split(line(60), [point("hut", "Refuge C", 30), point("hut", "Refuge D", 45)])

    assert [(stage.start, stage.end) for stage in late] == [(None, "Refuge A"), ("Refuge A", "Refuge B")]
    assert [(stage.start, stage.end) for stage in early] == [("Refuge C", "Refuge D"), ("Refuge D", None)]
    assert all(8_000 <= stage.length_m <= 25_000 for stage in late + early)
