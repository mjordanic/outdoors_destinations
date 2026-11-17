"""Every hike name in a build is unique (ADR-0068): ``dayhike.names``.

Hikes are hand-built on a kilometre grid around 45N 15E, as in
``test_named_trails``; the placeholder names are real ones from the US
named-trail preview.
"""

import math

import pytest

from dayhike.catalog import _dayhike
from dayhike.names import is_placeholder, unique_names
from dayhike.osm import OsmSnapshot

KM_LAT = 1_000.0 / 6_371_000 * 180 / math.pi


def _lat_lon(x_km: float, y_km: float) -> tuple[float, float]:
    lat = 45.0 + y_km * KM_LAT
    return lat, 15.0 + x_km * KM_LAT / math.cos(math.radians(lat))


def _hike(hike_id: int, name: str, x_km: float, kind: str = "relation"):
    """A 2 km DayHike whose Trailhead is at (x km, 0)."""
    vertices = [(0, _lat_lon(x_km, 0)), (0, _lat_lon(x_km, 2))]
    return _dayhike(hike_id, name, "osm_name", vertices, None, OsmSnapshot(), None, kind=kind)


def _point(x_km: float, y_km: float, name: str) -> tuple[float, float, str]:
    return (*_lat_lon(x_km, y_km), name)


def _names(hikes, towns=(), landmarks=()) -> list[str]:
    return [hike.name for hike in unique_names(hikes, list(towns), list(landmarks))]


@pytest.mark.parametrize(
    "name",
    [
        "Road", "Trail", "Rd", "Loop", "Connector", "Firebreak", "Shortcut", "Spur", "Access Road", "Jeep Road",
        "Fire Road", "Logging road", "Foot Path", "Bridle path", "Unnamed Trail", "Spur Trail", "Connector Trail",
        "Forest Route 2165", "Forest Route 5S08", "FS 700D", "FS Road 5608-210", "Forest Road 9711-113",
        "National Forest Development Road 70", "Forest Access Road #3", "FR 441", "392", "02030", "Trail 1",
    ],
)
def test_a_name_of_only_road_or_trail_words_and_codes_is_a_placeholder(name: str) -> None:
    assert is_placeholder(name)


@pytest.mark.parametrize(
    "name",
    [
        "Pig Farm Road", "Bellyache Springs Road", "Old Tioga Road", "Forest Route 33E32 (Cannell Trail)",
        "Blue Trail", "Blue", "Ridge Trail", "Appalachian Trail", "Grand Canyon Trail", "Hike near Somers",
        "Kraus Ridge Trail #275", "1777 Trail", "4T Trail", "1777 W Trail",
    ],
)
def test_a_name_with_a_word_of_its_own_is_no_placeholder(name: str) -> None:
    assert not is_placeholder(name)


def test_unique_names_are_kept_as_they_are() -> None:
    hikes = [_hike(1, "Ridge Trail", 0), _hike(2, "Lake Trail", 20), _hike(3, "Hike near Concord", 40)]

    assert unique_names(hikes, [_point(1, 0, "Concord")], []) == hikes


def test_a_placeholder_becomes_hike_near_the_nearest_town_with_its_number_as_code() -> None:
    hikes = [_hike(1, "Road", 0), _hike(2, "Forest Route 2165", 20, kind="trail")]
    towns = [_point(1, 0, "Concord"), _point(21, 0, "Idyllwild")]

    assert _names(hikes, towns) == ["Hike near Concord", "Hike near Idyllwild (Forest Route 2165)"]


def test_with_no_town_within_5_km_a_placeholder_takes_a_landmark_else_the_nearest_town() -> None:
    hikes = [_hike(1, "Trail", 0, kind="trail"), _hike(2, "Road", 40, kind="trail")]
    towns = [_point(60, 0, "Stanley")]  # 20 km from the second, 60 km from the first
    landmarks = [_point(2, 1, "Bald Peak")]

    assert _names(hikes, towns, landmarks) == ["Hike near Bald Peak", "Hike near Stanley"]


def test_a_placeholder_with_nowhere_named_keeps_its_name() -> None:
    assert _names([_hike(1, "Road", 0)]) == ["Road"]


def test_a_shared_name_keeps_its_words_and_adds_where_each_hike_starts() -> None:
    hikes = [_hike(1, "Blue Trail", 0), _hike(2, "Blue Trail", 20, kind="trail"), _hike(3, "Lake Trail", 40)]
    towns = [_point(1, 0, "Somers"), _point(21, 0, "Concord"), _point(41, 0, "Dover")]

    assert _names(hikes, towns) == ["Blue Trail near Somers", "Blue Trail near Concord", "Lake Trail"]


def test_names_differing_only_in_case_are_shared() -> None:
    hikes = [_hike(1, "Blue Trail", 0), _hike(2, "blue trail", 20)]
    towns = [_point(1, 0, "Somers"), _point(21, 0, "Concord")]

    assert _names(hikes, towns) == ["Blue Trail near Somers", "blue trail near Concord"]


def test_hikes_near_one_town_try_a_landmark_then_a_number() -> None:
    hikes = [_hike(3, "Hike near Somers", 0), _hike(1, "Hike near Somers", 1), _hike(2, "Hike near Somers", 0.2)]
    towns = [_point(0.5, 0, "Somers")]
    landmarks = [_point(-1, 0, "Bald Peak"), _point(2, 0, "Mud Lake")]

    assert _names(hikes, towns, landmarks) == [
        "Hike near Bald Peak (2)",
        "Hike near Mud Lake",
        "Hike near Bald Peak (1)",
    ]


def test_hikes_that_would_all_move_to_one_landmark_are_numbered_where_they_are() -> None:
    hikes = [
        _hike(3, "Blue Trail", 0),
        _hike(1, "Blue Trail", 1),
        _hike(2, "Hike near Somers", 0),
        _hike(4, "Hike near Somers", 1),
    ]
    towns = [_point(0.5, 0, "Somers")]
    landmarks = [_point(-1, 0, "Bald Peak")]

    assert _names(hikes, towns, landmarks) == [
        "Blue Trail near Somers (2)",
        "Blue Trail near Somers (1)",
        "Hike near Somers (1)",
        "Hike near Somers (2)",
    ]


def test_a_unique_name_is_not_changed_to_make_room_for_another() -> None:
    hikes = [_hike(1, "Ridge Trail near Concord", 0), _hike(2, "Ridge Trail", 0.5), _hike(3, "Ridge Trail", 20)]
    towns = [_point(1, 0, "Concord"), _point(21, 0, "Dover")]

    assert _names(hikes, towns) == [
        "Ridge Trail near Concord",
        "Ridge Trail near Concord (2)",
        "Ridge Trail near Dover",
    ]


def test_every_label_comes_out_unique() -> None:
    given = ["Trail", "Trail", "Road", "Trail 1", "Trail 1"] * 4
    hikes = [_hike(index, name, index % 3) for index, name in enumerate(given)]
    towns = [_point(0, 0, "Somers")]

    names = _names(hikes, towns)

    assert len({name.casefold() for name in names}) == len(hikes)
