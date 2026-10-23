"""Main walk on the member-way graph: lines and loops (ADR-0012).

Each fixture is a tiny OSM file laid out on a kilometre grid around 45N 15E.
"""

import math
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from dayhike import build_catalog, geometry_store_path_for

KM_LAT = 1_000.0 / 6_371_000 * 180 / math.pi
RELATION_ID = 7001
WALK_GAP_M = 50.0


def _lat_lon(x_km: float, y_km: float) -> tuple[float, float]:
    lat = 45.0 + y_km * KM_LAT
    return lat, 15.0 + x_km * KM_LAT / math.cos(math.radians(lat))


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    phi1, phi2 = math.radians(a[0]), math.radians(b[0])
    dphi = phi2 - phi1
    dlambda = math.radians(b[1] - a[1])
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def _osm(
    nodes: dict[str, tuple[float, float]],
    ways: dict[int, str],
    members: list[int],
    parking: Sequence[tuple[float, float]] = (),
    road: tuple[tuple[float, float], tuple[float, float]] | None = None,
    roles: Mapping[int, str] = {},
) -> str:
    """Nodes by one-letter name at (x_km, y_km); ways as strings of node names.

    ``parking`` are parking nodes at (x_km, y_km). ``road`` is a drivable road
    from one (x_km, y_km) to another, not a member. ``roles`` maps a member
    way to its role; every other member has role "".
    """
    node_ids = {name: 100 + index for index, name in enumerate(nodes)}
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6">\n'
    for name, (x_km, y_km) in nodes.items():
        lat, lon = _lat_lon(x_km, y_km)
        xml += f'<node id="{node_ids[name]}" lat="{lat:.10f}" lon="{lon:.10f}" version="1"/>\n'
    for node_id, point in enumerate(parking, start=90):
        lat, lon = _lat_lon(*point)
        xml += (
            f'<node id="{node_id}" lat="{lat:.10f}" lon="{lon:.10f}" version="1">'
            '<tag k="amenity" v="parking"/></node>\n'
        )
    if road is not None:
        for node_id, point in zip((97, 98), road):
            lat, lon = _lat_lon(*point)
            xml += f'<node id="{node_id}" lat="{lat:.10f}" lon="{lon:.10f}" version="1"/>\n'
        xml += (
            '<way id="99" version="1"><nd ref="97"/><nd ref="98"/>'
            '<tag k="highway" v="tertiary"/></way>\n'
        )
    for way_id, names in ways.items():
        refs = "".join(f'<nd ref="{node_ids[name]}"/>' for name in names)
        xml += f'<way id="{way_id}" version="1">{refs}<tag k="highway" v="path"/></way>\n'
    listed = "".join(
        f'<member type="way" ref="{way_id}" role="{roles.get(way_id, "")}"/>'
        for way_id in members
    )
    xml += (
        f'<relation id="{RELATION_ID}" version="1">{listed}<tag k="type" v="route"/>'
        '<tag k="route" v="hiking"/><tag k="name" v="Shape"/></relation>\n</osm>\n'
    )
    return xml


class Walk:
    def __init__(
        self, tmp_path: Path, nodes, ways, members, parking=(), road=None, roles={}
    ) -> None:
        osm = tmp_path / "shape.osm"
        osm.write_text(_osm(nodes, ways, members, parking, road, roles))
        catalog_path = tmp_path / "catalog.sqlite"
        catalog = build_catalog(osm, catalog_path)
        self.hike = next((hike for hike in catalog.dayhikes if hike.id == RELATION_ID), None)
        if self.hike is None:
            return
        names = {100 + index: name for index, name in enumerate(nodes)}
        connection = sqlite3.connect(geometry_store_path_for(catalog_path))
        try:
            self.vertices = "".join(
                names[node_id]
                for (node_id,) in connection.execute(
                    "SELECT node_id FROM vertices WHERE dayhike_id = ? ORDER BY seq",
                    (RELATION_ID,),
                )
            )
            self.points = [
                (lat, lon)
                for lat, lon in connection.execute(
                    "SELECT latitude, longitude FROM vertices WHERE dayhike_id = ? "
                    "ORDER BY seq",
                    (RELATION_ID,),
                )
            ]
            self.ways = list(
                connection.execute(
                    "SELECT way_id, in_chain, leftover, walked_m, walk_order FROM ways "
                    "WHERE dayhike_id = ? ORDER BY seq",
                    (RELATION_ID,),
                )
            )
            (self.unconnected_m,) = connection.execute(
                "SELECT unconnected_m FROM dayhikes WHERE id = ?", (RELATION_ID,)
            ).fetchone()
        finally:
            connection.close()
        way_steps = set()
        for names_on_way in ways.values():
            for first, second in zip(names_on_way, names_on_way[1:]):
                way_steps |= {first + second, second + first}
        self.jumps = [
            _haversine_m(start, end)
            for pair, start, end in zip(
                (a + b for a, b in zip(self.vertices, self.vertices[1:])),
                self.points,
                self.points[1:],
            )
            if pair not in way_steps and _haversine_m(start, end) > WALK_GAP_M
        ]

    @property
    def trailhead(self) -> tuple[float, float]:
        return self.hike.trailhead.latitude, self.hike.trailhead.longitude

    def way(self, way_id: int):
        """(in_chain, leftover, walked_m, walk_order) of the first listing of a way."""
        return next(row[1:] for row in self.ways if row[0] == way_id)


def _walked(walk_order: int, metres: float):
    return (1, None, pytest.approx(metres, abs=1.0), walk_order)


def _left(reason: str):
    return (0, reason, 0.0, None)


LINE = {"a": (0, 0), "b": (0, 2), "c": (0, 4), "d": (0, 6)}
LINE_WAYS = {1: "ab", 2: "bc", 3: "cd"}


def _assert_line_abcd(walk: Walk) -> None:
    assert walk.hike.length_m == pytest.approx(6_000.0, abs=1.0)
    assert walk.trailhead == pytest.approx(_lat_lon(*LINE["a"]))
    assert walk.vertices == "abcd"
    assert walk.way(1) == _walked(0, 2_000.0)
    assert walk.way(2) == _walked(1, 2_000.0)
    assert walk.way(3) == _walked(2, 2_000.0)
    assert walk.jumps == []


def test_a_line_in_member_order_is_walked_from_its_first_member(tmp_path: Path) -> None:
    walk = Walk(tmp_path, LINE, LINE_WAYS, [1, 2, 3])

    _assert_line_abcd(walk)
    assert walk.unconnected_m == pytest.approx(0.0, abs=0.01)


def test_a_line_listed_out_of_order_is_walked_whole(tmp_path: Path) -> None:
    walk = Walk(tmp_path, LINE, LINE_WAYS, [1, 3, 2])

    _assert_line_abcd(walk)
    assert [row[0] for row in walk.ways] == [1, 3, 2]
    assert walk.unconnected_m == pytest.approx(0.0, abs=0.01)


def test_a_short_spur_listed_in_the_middle_is_a_dead_end(tmp_path: Path) -> None:
    nodes = {**LINE, "s": (0.3, 2)}
    walk = Walk(tmp_path, nodes, {**LINE_WAYS, 4: "bs"}, [1, 4, 2, 3])

    _assert_line_abcd(walk)
    assert walk.way(4) == _left("dead_end")
    assert walk.unconnected_m == pytest.approx(300.0, abs=1.0)


def test_a_small_variant_loop_on_a_line_is_a_shortcut_and_both_real_ends_stay(
    tmp_path: Path,
) -> None:
    # Way 2 bends east through m; way 4 is a straight variant from b to c. The
    # cycle b-m-c-b is about 27% of the piece, so the piece is a line.
    nodes = {"a": (0, 0), "b": (0, 3), "m": (0.3, 3.5), "c": (0, 4), "d": (0, 7)}
    ways = {1: "ab", 2: "bmc", 3: "cd", 4: "bc"}
    walk = Walk(tmp_path, nodes, ways, [1, 2, 4, 3])

    bend_m = 2 * math.hypot(0.3, 0.5) * 1_000.0
    assert walk.hike.length_m == pytest.approx(6_000.0 + bend_m, abs=1.0)
    assert walk.trailhead == pytest.approx(_lat_lon(*nodes["a"]))
    assert walk.vertices == "abmcd"
    assert walk.way(1) == _walked(0, 3_000.0)
    assert walk.way(2) == _walked(1, bend_m)
    assert walk.way(3) == _walked(2, 3_000.0)
    assert walk.way(4) == _left("shortcut")
    assert walk.unconnected_m == pytest.approx(1_000.0, abs=1.0)
    assert walk.jumps == []


def test_a_stray_piece_listed_first_is_disconnected(tmp_path: Path) -> None:
    nodes = {**LINE, "x": (10, 0), "y": (10, 1)}
    walk = Walk(tmp_path, nodes, {**LINE_WAYS, 5: "xy"}, [5, 1, 2, 3])

    _assert_line_abcd(walk)
    assert walk.way(5) == _left("disconnected")
    assert walk.unconnected_m == pytest.approx(1_000.0, abs=1.0)


def test_pieces_30_m_apart_are_joined_by_their_gap(tmp_path: Path) -> None:
    nodes = {"a": (0, 0), "b": (0, 3), "e": (0, 3.03), "c": (0, 6)}
    walk = Walk(tmp_path, nodes, {1: "ab", 2: "ec"}, [1, 2])

    assert walk.hike.length_m == pytest.approx(6_000.0, abs=1.0)
    assert walk.trailhead == pytest.approx(_lat_lon(*nodes["a"]))
    assert walk.vertices == "abec"
    assert walk.way(1) == _walked(0, 3_000.0)
    assert walk.way(2) == _walked(1, 2_970.0)
    assert walk.unconnected_m == pytest.approx(0.0, abs=0.01)
    assert walk.jumps == []


def test_pieces_500_m_apart_leave_the_shorter_disconnected(tmp_path: Path) -> None:
    nodes = {"e": (0, 4.5), "f": (0, 6.5), "a": (0, 0), "b": (0, 4)}
    walk = Walk(tmp_path, nodes, {1: "ef", 2: "ab"}, [1, 2])

    assert walk.hike.length_m == pytest.approx(4_000.0, abs=1.0)
    assert walk.trailhead == pytest.approx(_lat_lon(*nodes["a"]))
    assert walk.vertices == "ab"
    assert walk.way(1) == _left("disconnected")
    assert walk.way(2) == _walked(0, 4_000.0)
    assert walk.unconnected_m == pytest.approx(2_000.0, abs=1.0)
    assert walk.jumps == []


def test_a_way_left_mid_way_by_the_route_is_walked_in_part(tmp_path: Path) -> None:
    # Way 1 runs a-x-b; the route turns east at x, leaving x-b as a stub.
    nodes = {"a": (0, 0), "x": (0, 3), "b": (0, 3.2), "f": (3, 3)}
    walk = Walk(tmp_path, nodes, {1: "axb", 2: "xf"}, [1, 2])

    assert walk.hike.length_m == pytest.approx(6_000.0, abs=1.0)
    assert walk.trailhead == pytest.approx(_lat_lon(*nodes["a"]))
    assert walk.vertices == "axf"
    assert walk.way(1) == _walked(0, 3_000.0)
    assert walk.way(2) == _walked(1, 3_000.0)
    assert walk.unconnected_m == pytest.approx(200.0, abs=1.0)
    assert walk.jumps == []


def test_a_line_whose_trailhead_is_the_last_members_end_starts_there(tmp_path: Path) -> None:
    walk = Walk(tmp_path, LINE, LINE_WAYS, [1, 2, 3], parking=[(0, 6.1)])

    assert walk.trailhead == pytest.approx(_lat_lon(*LINE["d"]))
    assert walk.vertices == "dcba"
    assert walk.way(3) == _walked(0, 2_000.0)
    assert walk.way(2) == _walked(1, 2_000.0)
    assert walk.way(1) == _walked(2, 2_000.0)
    assert walk.unconnected_m == pytest.approx(0.0, abs=0.01)
    assert walk.jumps == []


def test_a_line_without_parking_within_5_km_starts_at_its_member_order_end(
    tmp_path: Path,
) -> None:
    # Parking 5.5 km past d: too far for a Trailhead.
    walk = Walk(tmp_path, LINE, LINE_WAYS, [1, 2, 3], parking=[(0, 11.5)])

    _assert_line_abcd(walk)


def test_a_drivable_road_by_a_lines_last_end_does_not_make_it_the_trailhead(
    tmp_path: Path,
) -> None:
    # A road 100 m past d and no parking: access points are parking only.
    walk = Walk(tmp_path, LINE, LINE_WAYS, [1, 2, 3], road=((0, 6.1), (0.5, 6.5)))

    _assert_line_abcd(walk)


def test_access_is_looked_for_at_the_main_walks_ends(tmp_path: Path) -> None:
    # The stray piece is listed first; parking near d is over 5 km from it.
    nodes = {**LINE, "x": (20, 0), "y": (20, 1.5)}
    walk = Walk(tmp_path, nodes, {**LINE_WAYS, 5: "xy"}, [5, 1, 2, 3], parking=[(0, 6.1)])

    assert walk.trailhead == pytest.approx(_lat_lon(*LINE["d"]))
    assert walk.vertices == "dcba"
    assert walk.jumps == []


def test_the_length_band_reads_the_main_walk(tmp_path: Path) -> None:
    # Listed 1, 3, 2: a one-pass chain kept only a-b (5 km) + b-c (10 km).
    nodes = {"a": (0, 0), "b": (0, 5), "c": (0, 15), "d": (0, 25)}
    too_long = Walk(tmp_path, nodes, {1: "ab", 2: "bc", 3: "cd"}, [1, 3, 2])

    assert too_long.hike is None


def test_a_relation_whose_first_member_is_a_short_stray_is_kept(tmp_path: Path) -> None:
    # A one-pass chain kept only the 500 m stray listed first.
    nodes = {**LINE, "x": (10, 0), "y": (10, 0.5)}
    walk = Walk(tmp_path, nodes, {**LINE_WAYS, 5: "xy"}, [5, 1, 2, 3])

    assert walk.hike is not None
    assert walk.hike.length_m == pytest.approx(6_000.0, abs=1.0)
    assert walk.jumps == []


SQUARE = {"a": (0, 0), "b": (0, 3), "m": (1.5, 3), "c": (3, 3), "d": (3, 0)}
SQUARE_WAYS = {1: "ab", 2: "bmc", 3: "cd", 4: "da"}


def test_a_loop_starts_at_its_vertex_nearest_parking_in_member_direction(
    tmp_path: Path,
) -> None:
    # Parking is 50 m north of m, mid-way along the second member.
    walk = Walk(tmp_path, SQUARE, SQUARE_WAYS, [1, 2, 3, 4], parking=[(1.5, 3.05)])

    assert walk.hike.length_m == pytest.approx(12_000.0, abs=1.0)
    assert walk.trailhead == pytest.approx(_lat_lon(*SQUARE["m"]))
    assert walk.vertices == "mcdabm"
    assert walk.way(2) == _walked(0, 3_000.0)
    assert walk.way(3) == _walked(1, 3_000.0)
    assert walk.way(4) == _walked(2, 3_000.0)
    assert walk.way(1) == _walked(3, 3_000.0)
    assert walk.unconnected_m == pytest.approx(0.0, abs=0.01)
    assert walk.jumps == []


def test_a_loop_without_access_starts_where_member_order_starts_it(tmp_path: Path) -> None:
    # The only parking is over 5 km from every vertex: the loop is still kept.
    walk = Walk(tmp_path, SQUARE, SQUARE_WAYS, [1, 2, 3, 4], parking=[(-6, 1.5)])

    assert walk.hike is not None
    assert walk.hike.length_m == pytest.approx(12_000.0, abs=1.0)
    assert walk.trailhead == pytest.approx(_lat_lon(*SQUARE["a"]))
    assert walk.vertices == "abmcda"
    assert walk.way(1) == _walked(0, 3_000.0)
    assert walk.way(4) == _walked(3, 3_000.0)
    assert walk.jumps == []


PENTAGON = {"a": (0, 0), "b": (0, 3), "c": (4, 3), "d": (4, 0), "e": (2, 0)}
PENTAGON_WAYS = {1: "ab", 2: "bc", 3: "cd", 4: "de", 5: "ea", 6: "be"}
CHORD_M = math.hypot(2, 3) * 1_000.0


def _assert_pentagon_loop(walk: Walk) -> None:
    assert walk.hike.length_m == pytest.approx(14_000.0, abs=1.0)
    assert walk.trailhead == pytest.approx(_lat_lon(*PENTAGON["a"]))
    assert walk.vertices == "abcdea"
    assert walk.way(1) == _walked(0, 3_000.0)
    assert walk.way(2) == _walked(1, 4_000.0)
    assert walk.way(3) == _walked(2, 3_000.0)
    assert walk.way(4) == _walked(3, 2_000.0)
    assert walk.way(5) == _walked(4, 2_000.0)
    assert walk.unconnected_m == pytest.approx(CHORD_M, abs=1.0)
    assert walk.jumps == []


GAPPED_SQUARE = {**SQUARE, "e": (0.03, 0)}  # e is 30 m from a
GAPPED_SQUARE_WAYS = {1: "ab", 2: "bmc", 3: "cd", 4: "de"}


def test_a_route_closed_by_a_30_m_gap_is_a_loop_from_its_vertex_nearest_parking(
    tmp_path: Path,
) -> None:
    # The route ends at e, 30 m from where it starts at a. Parking is 50 m
    # north of m, mid-way along the second member.
    walk = Walk(
        tmp_path, GAPPED_SQUARE, GAPPED_SQUARE_WAYS, [1, 2, 3, 4], parking=[(1.5, 3.05)]
    )

    assert walk.hike.length_m == pytest.approx(12_000.0, abs=1.0)
    assert walk.trailhead == pytest.approx(_lat_lon(*GAPPED_SQUARE["m"]))
    assert walk.vertices == "mcdeabm"
    assert walk.points[0] == walk.points[-1]
    assert walk.way(2) == _walked(0, 3_000.0)
    assert walk.way(3) == _walked(1, 3_000.0)
    assert walk.way(4) == _walked(2, 2_970.0)
    assert walk.way(1) == _walked(3, 3_000.0)
    assert walk.unconnected_m == pytest.approx(0.0, abs=0.01)
    assert walk.jumps == []


def test_a_tip_30_m_from_the_middle_of_a_way_is_not_joined(tmp_path: Path) -> None:
    # The route hooks back and ends at e, 30 m from m in the middle of way 1.
    nodes = {"a": (0, 0), "m": (0, 2), "b": (0, 4), "c": (2, 4), "d": (2, 2), "e": (0.03, 2)}
    ways = {1: "amb", 2: "bcd", 3: "de"}
    walk = Walk(tmp_path, nodes, ways, [1, 2, 3])

    assert walk.hike.length_m == pytest.approx(9_970.0, abs=1.0)
    assert walk.trailhead == pytest.approx(_lat_lon(*nodes["a"]))
    assert walk.vertices == "ambcde"
    assert walk.way(1) == _walked(0, 4_000.0)
    assert walk.way(2) == _walked(1, 4_000.0)
    assert walk.way(3) == _walked(2, 1_970.0)
    assert walk.unconnected_m == pytest.approx(0.0, abs=0.01)
    assert walk.jumps == []


def test_a_tip_30_m_from_the_tip_of_a_role_leftover_is_not_joined(tmp_path: Path) -> None:
    # Way 4 is an alternative from c that ends at e, 30 m from the line's end d.
    nodes = {**LINE, "e": (0.03, 6)}
    walk = Walk(
        tmp_path, nodes, {**LINE_WAYS, 4: "ce"}, [1, 2, 3, 4], roles={4: "alternative"}
    )

    _assert_line_abcd(walk)
    assert walk.way(4) == _left("role")
    assert walk.unconnected_m == pytest.approx(math.hypot(0.03, 2) * 1_000.0, abs=1.0)


def test_of_three_tips_within_50_m_the_closest_pair_is_joined(tmp_path: Path) -> None:
    # Tips a and e are 30 m apart; the spur from k ends at f, 38 m from both.
    nodes = {**GAPPED_SQUARE, "k": (0, 0.5), "f": (0.015, -0.035)}
    ways = {**GAPPED_SQUARE_WAYS, 1: "akb", 5: "kf"}
    walk = Walk(tmp_path, nodes, ways, [1, 2, 3, 4, 5])

    assert walk.hike.length_m == pytest.approx(12_000.0, abs=1.0)
    assert walk.trailhead == pytest.approx(_lat_lon(*nodes["a"]))
    assert walk.vertices == "akbmcdea"
    assert walk.way(1) == _walked(0, 3_000.0)
    assert walk.way(2) == _walked(1, 3_000.0)
    assert walk.way(3) == _walked(2, 3_000.0)
    assert walk.way(4) == _walked(3, 2_970.0)
    assert walk.way(5) == _left("dead_end")
    assert walk.unconnected_m == pytest.approx(math.hypot(0.015, 0.535) * 1_000.0, abs=1.0)
    assert walk.jumps == []


def test_a_shortcut_across_a_loop_is_a_shortcut(tmp_path: Path) -> None:
    walk = Walk(tmp_path, PENTAGON, PENTAGON_WAYS, [1, 2, 3, 4, 5, 6])

    _assert_pentagon_loop(walk)
    assert walk.way(6) == _left("shortcut")


def test_a_loop_then_b_e_b_is_walked_as_the_loop_once(tmp_path: Path) -> None:
    # Members a-b-c-d-e-a-b-e-b: a-b listed twice, b-e listed twice.
    walk = Walk(tmp_path, PENTAGON, PENTAGON_WAYS, [1, 2, 3, 4, 5, 1, 6, 6])

    _assert_pentagon_loop(walk)
    assert [row[:3] for row in walk.ways] == [
        (1, 1, None),
        (2, 1, None),
        (3, 1, None),
        (4, 1, None),
        (5, 1, None),
        (1, 0, "repeat"),
        (6, 0, "shortcut"),
        (6, 0, "repeat"),
    ]


def test_a_short_side_trip_off_a_loop_is_a_dead_end(tmp_path: Path) -> None:
    nodes = {**SQUARE, "s": (1.5, 3.3)}
    walk = Walk(tmp_path, nodes, {**SQUARE_WAYS, 5: "ms"}, [1, 2, 5, 3, 4])

    assert walk.hike.length_m == pytest.approx(12_000.0, abs=1.0)
    assert walk.trailhead == pytest.approx(_lat_lon(*SQUARE["a"]))
    assert walk.vertices == "abmcda"
    assert walk.way(2) == _walked(1, 3_000.0)
    assert walk.way(5) == _left("dead_end")
    assert walk.unconnected_m == pytest.approx(300.0, abs=1.0)
    assert walk.jumps == []


def _assert_lollipop(walk: Walk, nodes, stem_m: float) -> None:
    assert walk.hike.length_m == pytest.approx(12_000.0 + stem_m, abs=1.0)
    assert walk.trailhead == pytest.approx(_lat_lon(*nodes["t"]))
    assert walk.vertices == "tabmcda"
    assert walk.way(5) == _walked(0, stem_m)
    assert walk.way(1) == _walked(1, 3_000.0)
    assert walk.way(2) == _walked(2, 3_000.0)
    assert walk.way(3) == _walked(3, 3_000.0)
    assert walk.way(4) == _walked(4, 3_000.0)
    assert walk.unconnected_m == pytest.approx(0.0, abs=0.01)
    assert walk.jumps == []


def test_a_lollipop_stem_ending_at_parking_is_walked_from_the_parking(
    tmp_path: Path,
) -> None:
    nodes = {**SQUARE, "t": (0, -2)}
    walk = Walk(tmp_path, nodes, {**SQUARE_WAYS, 5: "at"}, [1, 2, 3, 4, 5], parking=[(0, -2.05)])

    _assert_lollipop(walk, nodes, 2_000.0)


def test_a_long_only_stem_without_access_is_walked(tmp_path: Path) -> None:
    nodes = {**SQUARE, "t": (0, -1.5)}
    walk = Walk(tmp_path, nodes, {**SQUARE_WAYS, 5: "at"}, [1, 2, 3, 4, 5])

    _assert_lollipop(walk, nodes, 1_500.0)


def test_two_long_dead_ends_off_a_loop_away_from_access_are_dead_ends(
    tmp_path: Path,
) -> None:
    # Parking is 1 km past t: too far for a stem, near enough to start at a.
    nodes = {**SQUARE, "t": (0, -1.5), "u": (4.5, 3)}
    ways = {**SQUARE_WAYS, 5: "at", 6: "cu"}
    walk = Walk(tmp_path, nodes, ways, [1, 2, 3, 4, 5, 6], parking=[(0, -2.5)])

    assert walk.hike.length_m == pytest.approx(12_000.0, abs=1.0)
    assert walk.trailhead == pytest.approx(_lat_lon(*SQUARE["a"]))
    assert walk.vertices == "abmcda"
    assert walk.way(5) == _left("dead_end")
    assert walk.way(6) == _left("dead_end")
    assert walk.unconnected_m == pytest.approx(3_000.0, abs=1.0)
    assert walk.jumps == []


def test_a_figure_eight_is_walked_whole(tmp_path: Path) -> None:
    nodes = {
        "x": (0, 0), "p": (0, 2), "q": (-2, 2), "r": (-2, 0),
        "s": (2, 0), "t": (2, -2), "u": (0, -2),
    }
    ways = {1: "xp", 2: "pq", 3: "qr", 4: "rx", 5: "xs", 6: "st", 7: "tu", 8: "ux"}
    walk = Walk(tmp_path, nodes, ways, [1, 2, 3, 4, 5, 6, 7, 8])

    assert walk.hike.length_m == pytest.approx(16_000.0, abs=1.0)
    assert walk.trailhead == pytest.approx(_lat_lon(*nodes["x"]))
    assert walk.vertices == "xpqrxstux"
    assert [row[4] for row in walk.ways] == list(range(8))
    assert all(row[1:3] == (1, None) for row in walk.ways)
    assert walk.unconnected_m == pytest.approx(0.0, abs=0.01)
    assert walk.jumps == []


def test_access_is_looked_for_along_a_loop(tmp_path: Path) -> None:
    # Parking 50 m from c, over 5 km from a where member order starts the loop.
    nodes = {"a": (0, 0), "b": (0, 4), "c": (4, 4), "d": (4, 0)}
    ways = {1: "ab", 2: "bc", 3: "cd", 4: "da"}
    parking = (4 + 0.05 / math.sqrt(2), 4 + 0.05 / math.sqrt(2))
    walk = Walk(tmp_path, nodes, ways, [1, 2, 3, 4], parking=[parking])

    assert walk.trailhead == pytest.approx(_lat_lon(*nodes["c"]))
    assert walk.vertices == "cdabc"
    assert walk.jumps == []


def test_of_two_dead_ends_near_access_the_nearest_is_the_stem(tmp_path: Path) -> None:
    # Parking 150 m past u and 50 m past t: t's dead end is the stem.
    nodes = {**SQUARE, "t": (0, -2), "u": (5, 3)}
    ways = {**SQUARE_WAYS, 5: "cu", 6: "at"}
    walk = Walk(
        tmp_path, nodes, ways, [1, 2, 3, 4, 5, 6], parking=[(0, -2.05), (5.15, 3)]
    )

    assert walk.hike.length_m == pytest.approx(14_000.0, abs=1.0)
    assert walk.trailhead == pytest.approx(_lat_lon(*nodes["t"]))
    assert walk.vertices == "tabmcda"
    assert walk.way(6) == _walked(0, 2_000.0)
    assert walk.way(5) == _left("dead_end")
    assert walk.unconnected_m == pytest.approx(2_000.0, abs=1.0)
    assert walk.jumps == []
