"""Main walk on the member-way graph: line shapes (ADR-0012, ticket 03).

Each fixture is a tiny OSM file laid out on a kilometre grid around 45N 15E.
"""

import math
import sqlite3
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
    parking: tuple[float, float] | None = None,
) -> str:
    """Nodes by one-letter name at (x_km, y_km); ways as strings of node names."""
    node_ids = {name: 100 + index for index, name in enumerate(nodes)}
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6">\n'
    for name, (x_km, y_km) in nodes.items():
        lat, lon = _lat_lon(x_km, y_km)
        xml += f'<node id="{node_ids[name]}" lat="{lat:.10f}" lon="{lon:.10f}" version="1"/>\n'
    if parking is not None:
        lat, lon = _lat_lon(*parking)
        xml += (
            f'<node id="99" lat="{lat:.10f}" lon="{lon:.10f}" version="1">'
            '<tag k="amenity" v="parking"/></node>\n'
        )
    for way_id, names in ways.items():
        refs = "".join(f'<nd ref="{node_ids[name]}"/>' for name in names)
        xml += f'<way id="{way_id}" version="1">{refs}<tag k="highway" v="path"/></way>\n'
    listed = "".join(f'<member type="way" ref="{way_id}" role=""/>' for way_id in members)
    xml += (
        f'<relation id="{RELATION_ID}" version="1">{listed}<tag k="type" v="route"/>'
        '<tag k="route" v="hiking"/><tag k="name" v="Shape"/></relation>\n</osm>\n'
    )
    return xml


class Walk:
    def __init__(self, tmp_path: Path, nodes, ways, members, parking=None) -> None:
        osm = tmp_path / "shape.osm"
        osm.write_text(_osm(nodes, ways, members, parking))
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


def test_a_parallel_variant_is_a_shortcut_and_both_real_ends_stay(tmp_path: Path) -> None:
    # Way 2 bends east through m; way 4 is a straight variant from b to c.
    nodes = {**LINE, "m": (0.5, 3)}
    ways = {1: "ab", 2: "bmc", 3: "cd", 4: "bc"}
    walk = Walk(tmp_path, nodes, ways, [1, 2, 4, 3])

    bend_m = 2 * math.hypot(0.5, 1.0) * 1_000.0
    assert walk.hike.length_m == pytest.approx(4_000.0 + bend_m, abs=1.0)
    assert walk.trailhead == pytest.approx(_lat_lon(*LINE["a"]))
    assert walk.vertices == "abmcd"
    assert walk.way(1) == _walked(0, 2_000.0)
    assert walk.way(2) == _walked(1, bend_m)
    assert walk.way(3) == _walked(2, 2_000.0)
    assert walk.way(4) == _left("shortcut")
    assert walk.unconnected_m == pytest.approx(2_000.0, abs=1.0)
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
    walk = Walk(tmp_path, LINE, LINE_WAYS, [1, 2, 3], parking=(0, 6.1))

    assert walk.trailhead == pytest.approx(_lat_lon(*LINE["d"]))
    assert walk.vertices == "dcba"
    assert walk.way(3) == _walked(0, 2_000.0)
    assert walk.way(2) == _walked(1, 2_000.0)
    assert walk.way(1) == _walked(2, 2_000.0)
    assert walk.unconnected_m == pytest.approx(0.0, abs=0.01)
    assert walk.jumps == []


def test_access_is_looked_for_at_the_main_walks_ends(tmp_path: Path) -> None:
    # The stray piece is listed first; parking near d is over 5 km from it.
    nodes = {**LINE, "x": (20, 0), "y": (20, 1.5)}
    walk = Walk(tmp_path, nodes, {**LINE_WAYS, 5: "xy"}, [5, 1, 2, 3], parking=(0, 6.1))

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
