import numpy as np
import pytest

from dayhike.highways import HighwaySnaps
from dayhike.reach import reach_minutes
from test_highways import _locate, _serve, _square_isochrone, _stop

BASE = (0.0, 0.0)


def test_near_pair_off_the_highways_gets_the_straight_line_time() -> None:
    # Every place snaps 0.1 degrees of longitude (about 11.1 km at the equator) west:
    # two approaches at 20 km/h take 67 minutes, so the highway estimate is over the hour.
    server, url = _serve(
        {
            "/locate": _locate(lambda lat, lon, cutoff: (lat, lon - 0.1)),
            "/isochrone": _square_isochrone,
        }
    )
    try:
        (minutes,) = reach_minutes(BASE, np.array([[0.05, 0.0]]), HighwaySnaps(url))
    finally:
        _stop(server)

    # 0.05 degrees of latitude is 5.56 km; at 28 km/h that is 11.9 minutes.
    assert minutes == pytest.approx(11.9, abs=0.1)


def test_far_pair_gets_the_highway_time() -> None:
    server, url = _serve(
        {
            "/locate": _locate(lambda lat, lon, cutoff: (lat, lon)),
            "/isochrone": _square_isochrone,
        }
    )
    try:
        (minutes,) = reach_minutes(BASE, np.array([[0.28, 0.0]]), HighwaySnaps(url))
    finally:
        _stop(server)

    # 31.1 km is 66.7 minutes on the straight line; the point is inside the 30 minute
    # contour and outside the 25: the middle of that band.
    assert minutes == 27.5


def test_a_point_over_the_hour_is_left_out() -> None:
    server, url = _serve(
        {
            "/locate": _locate(lambda lat, lon, cutoff: (lat, lon)),
            "/isochrone": _square_isochrone,
        }
    )
    try:
        minutes = reach_minutes(
            BASE,
            np.array(
                [
                    [0.03, 0.0],  # inside the 5 minute contour
                    [0.9, 0.0],  # outside the 60 minute contour, 100 km on the straight line
                    [0.55, 0.0],  # inside the 60 minute contour, 61 km on the straight line
                ]
            ),
            HighwaySnaps(url),
        )
    finally:
        _stop(server)

    np.testing.assert_array_equal(minutes, [2.5, np.nan, 57.5])


def test_highway_time_adds_both_approaches_at_20_kmh() -> None:
    # Every place snaps 0.01 degrees of longitude (1.11 km) west: 2.22 km at 20 km/h is 6.7 minutes.
    server, url = _serve(
        {
            "/locate": _locate(lambda lat, lon, cutoff: (lat, lon - 0.01)),
            "/isochrone": _square_isochrone,
        }
    )
    try:
        (minutes,) = reach_minutes(BASE, np.array([[0.28, 0.0]]), HighwaySnaps(url))
    finally:
        _stop(server)

    assert minutes == pytest.approx(27.5 + 6.7, abs=0.1)


def test_a_base_with_no_highway_gets_straight_line_times_only() -> None:
    server, url = _serve(
        {
            "/locate": _locate(lambda lat, lon, cutoff: None if (lat, lon) == BASE else (lat, lon)),
            "/isochrone": _square_isochrone,
        }
    )
    try:
        minutes = reach_minutes(BASE, np.array([[0.05, 0.0], [0.28, 0.0]]), HighwaySnaps(url))
    finally:
        _stop(server)

    np.testing.assert_allclose(minutes, [11.9, np.nan], atol=0.1)


def test_a_base_whose_highway_point_valhalla_cannot_spread_from_gets_straight_line_times_only() -> None:
    # The real Europe tiles answer 400 ("_M_range_check") to an isochrone from some snapped
    # highway points; that Base is then as one with no highway, not the end of export-bases.
    server, url = _serve(
        {
            "/locate": _locate(lambda lat, lon, cutoff: (lat, lon)),
            "/isochrone": lambda body: (400, {"error_code": 0, "error": "vector::_M_range_check"}),
        }
    )
    try:
        minutes = reach_minutes(BASE, np.array([[0.05, 0.0], [0.28, 0.0]]), HighwaySnaps(url))
    finally:
        _stop(server)

    np.testing.assert_allclose(minutes, [11.9, np.nan], atol=0.1)
