"""DestinationHikes (#124, ADR-0067): "{Destination} from {start}", a walk from
a car park to a named Peak, Hut, Viewpoint or lake.

Each test draws a tiny network on a grid: ``KM`` degrees of latitude is 1 km,
and near the equator a degree of longitude is about as long.
"""

import math

from dayhike.destination_hikes import (
    Destination,
    RelationWalk,
    Start,
    destination_hikes,
)
from dayhike.osm import OsmWay

KM = 1 / 111.195  # degrees of latitude


class Net:
    """A walkable graph drawn line by line: each line a way of nodes every 250 m."""

    def __init__(self) -> None:
        self.ways: dict[int, OsmWay] = {}
        self.nodes: dict[int, tuple[float, float]] = {}
        self.heights: dict[int, float] = {}
        self.marked: dict[int, tuple[int, ...]] = {}
        self._next = 1

    def node(self, at_km: tuple[float, float], height: float | None = None) -> int:
        for node, point in self.nodes.items():
            if math.dist(point, (at_km[0] * KM, at_km[1] * KM)) < 1e-9:
                return node
        node = self._next
        self._next += 1
        self.nodes[node] = (at_km[0] * KM, at_km[1] * KM)
        if height is not None:
            self.heights[node] = height
        return node

    def line(self, *corners_km, heights=None, marked: tuple[int, ...] = (), **tags) -> int:
        """A way through ``corners_km`` (km north, km east); ``heights`` per corner, between them linear."""
        ids: list[int] = []
        for leg, (a, b) in enumerate(zip(corners_km, corners_km[1:])):
            steps = max(1, round(math.dist(a, b) * 4))
            for i in range(0 if leg == 0 else 1, steps + 1):
                t = i / steps
                height = None if heights is None else heights[leg] + (heights[leg + 1] - heights[leg]) * t
                ids.append(self.node((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), height))
        way_id = 100 + len(self.ways)
        self.ways[way_id] = OsmWay(tuple(ids), {"highway": "path", **tags})
        if marked:
            self.marked[way_id] = marked
        return way_id

    def hikes(self, destinations, starts, **kwargs):
        return destination_hikes(
            self.ways, self.nodes, destinations, starts, heights=self.heights or None, marked=self.marked, **kwargs
        )


def at(north_km: float, east_km: float) -> tuple[float, float]:
    return (north_km * KM, east_km * KM)


def kept(hikes):
    return [hike for hike in hikes if hike.twin is None]


HUT = Destination("Koča", "hut", (at(4, 0),))
DOLINA = Start("Dolina", (at(0, 0),))


def test_a_hut_at_the_end_of_a_path_from_a_car_park_is_one_walk_named_after_both() -> None:
    net = Net()
    net.line((0, 0), (4, 0))

    (hike,) = kept(net.hikes([HUT], [DOLINA]))

    assert hike.name == "Koča from Dolina"
    assert hike.vertices[0][1] == at(0, 0) and hike.vertices[-1][1] == at(4, 0)
    assert abs(hike.length_m - 4_000) < 5
    assert hike.way_ids == (100,)


def test_a_walk_is_1_5_to_12_5_km_one_way_and_up_to_16_km_to_a_big_destination() -> None:
    def walks(km: float, ele: float, **tags: bool) -> int:
        net = Net()
        net.line((0, 0), (km, 0))
        return len(kept(net.hikes([Destination("Vrh", "peak", (at(km, 0),), ele=ele, **tags)], [DOLINA])))

    assert walks(1, 1_400) == 0
    assert walks(12, 1_400) == 1
    assert walks(13.5, 1_400) == 0
    assert walks(13.5, 2_864) == 1
    assert walks(13.5, 669, wikidata=True) == 1
    assert walks(13.5, 669, wikipedia=True) == 0
    assert walks(17, 2_864) == 0


def test_a_walk_never_passes_another_car_park() -> None:
    net = Net()
    net.line((0, 0), (6, 0))
    hut = Destination("Koča", "hut", (at(6, 0),))
    upper = Start("Planina", (at(3, 0),))

    hikes = kept(net.hikes([hut], [DOLINA, upper]))

    assert [hike.name for hike in hikes] == ["Koča from Planina"]


def test_a_drive_up_destination_gets_its_walks_only_from_another_side() -> None:
    net = Net()
    net.line((0, 0), (4, 0), (8, 0))
    beside = Start("Cesta", (at(3.8, 0),))  # a car park beside the hut
    north = Start("Sever", (at(8, 0),))

    hikes = kept(net.hikes([HUT], [DOLINA, beside, north]))

    assert [hike.name for hike in hikes] == ["Koča from Sever"]


def test_a_hut_reachable_from_two_valleys_keeps_a_walk_from_each() -> None:
    net = Net()
    net.line((0, 0), (4, 0))
    net.line((9, 0), (4, 0))
    sever = Start("Sever", (at(9, 0),))

    hikes = kept(net.hikes([HUT], [DOLINA, sever]))

    assert sorted((hike.name, hike.rank) for hike in hikes) == [("Koča from Dolina", 1), ("Koča from Sever", 2)]


def test_a_further_walk_must_start_2_km_from_every_other_and_share_at_most_half() -> None:
    net = Net()
    net.line((0, 0), (4, 0))
    net.line((0, 1), (4, 0))  # its own path from a car park 1 km east
    net.line((0.5, -3), (0.5, 0))  # joins the first walk 500 m up, from 3 km west
    east = Start("Vzhod", (at(0, 1),))
    west = Start("Zahod", (at(0.5, -3),))

    hikes = kept(net.hikes([HUT], [DOLINA, east, west]))

    assert [hike.name for hike in hikes] == ["Koča from Dolina"]


def test_a_destination_keeps_at_most_8_walks() -> None:
    net = Net()
    starts = []
    for arm in range(10):
        angle = 2 * math.pi * arm / 10
        end = (4 + 5 * math.cos(angle), 5 * math.sin(angle))
        net.line(end, (4, 0))
        starts.append(Start(f"Dolina {arm}", (at(*end),)))

    hikes = kept(net.hikes([HUT], starts))

    assert len(hikes) == 8


def test_a_start_with_no_name_builds_no_walk() -> None:
    net = Net()
    net.line((0, 0), (4, 0))

    assert kept(net.hikes([HUT], [Start(None, (at(0, 0),))])) == []


def test_roads_cost_triple_and_a_walk_mostly_on_road_is_not_built() -> None:
    net = Net()
    net.line((0, 0), (4, 0), highway="residential")
    net.line((0, 0), (0, 1), (4, 1), (4, 0))  # 2 km longer on a path

    (hike,) = kept(net.hikes([HUT], [DOLINA]))
    assert abs(hike.length_m - 6_000) < 10

    road = Net()
    road.line((0, 0), (4, 0), highway="residential")
    assert kept(road.hikes([HUT], [DOLINA])) == []


def test_t5_ways_and_via_ferratas_are_not_walked_but_a_t4_path_with_cables_is() -> None:
    for tags in ({"sac_scale": "demanding_alpine_hiking"}, {"highway": "via_ferrata"}):
        net = Net()
        net.line((0, 0), (4, 0), **tags)
        assert kept(net.hikes([HUT], [DOLINA])) == []
    # A T4 path with cables, such as the last metres to Triglav, is walked.
    net = Net()
    net.line((0, 0), (4, 0), sac_scale="alpine_hiking", via_ferrata_scale="1+")
    assert len(kept(net.hikes([HUT], [DOLINA]))) == 1


def test_the_search_weighs_climb_and_marked_routes() -> None:
    # Over a col 100 m above the hut (4 km) or round by the valley (5.5 km): climb takes
    # the valley, unless the col path is a marked route.
    def walk(marked: tuple[int, ...]) -> float:
        net = Net()
        net.line((0, 0), (2, 0), (4, 0), heights=(1_000, 1_500, 1_400), marked=marked)
        net.line((0, 0), (0, 0.75), (4, 0.75), (4, 0), heights=(1_000, 1_000, 1_400, 1_400))
        (hike,) = kept(net.hikes([HUT], [DOLINA]))
        return hike.length_m

    assert abs(walk(()) - 5_500) < 10
    assert abs(walk((7,)) - 4_000) < 10


def test_a_walk_that_drops_over_a_col_is_not_built_unless_it_follows_one_marked_route() -> None:
    def walks(marked: tuple[int, ...]) -> list[str]:
        net = Net()
        net.line((0, 0), (4, 0), heights=(1_000, 1_500))
        net.line((9, 0), (6, 0), (4, 0), heights=(1_200, 1_900, 1_500), marked=marked)
        sever = Start("Sever", (at(9, 0),))
        return sorted(hike.name for hike in kept(net.hikes([HUT], [DOLINA, sever])))

    assert walks(()) == ["Koča from Dolina"]
    assert walks((7,)) == ["Koča from Dolina", "Koča from Sever"]


def test_a_twin_of_a_relation_hike_and_a_near_copy_of_one_are_dropped() -> None:
    net = Net()
    net.line((0, 0), (4, 0))
    twin = RelationWalk(4_000, (at(0, 0), at(4, 0)))
    loop = RelationWalk(14_000, (at(0, 0.1), at(0, 3), at(4, 3), at(4, 0.1)))  # 100 m from both ends, 14 <= 2 x 8 km

    assert [hike.twin for hike in net.hikes([HUT], [DOLINA], relations=[twin])] == ["relation"]
    assert [hike.twin for hike in net.hikes([HUT], [DOLINA], relations=[loop])] == ["near-copy"]
    far = RelationWalk(20_000, loop.points)
    assert kept(net.hikes([HUT], [DOLINA], relations=[far]))


def test_of_twin_walks_to_two_destinations_the_better_known_stays_named_via_the_other() -> None:
    net = Net()
    net.line((0, 0), (4, 0), (4.3, 0))
    hut = Destination("Dom", "hut", (at(4, 0.05),), ele=2_500)  # 50 m beside the path
    peak = Destination("Triglav", "peak", (at(4.3, 0),), ele=2_864, wikidata=True)

    (hike,) = kept(net.hikes([hut, peak], [DOLINA]))

    assert hike.name == "Triglav via Dom from Dolina"


def test_destinations_within_150_m_are_one_named_after_the_better_known() -> None:
    net = Net()
    net.line((0, 0), (4, 0), (4.1, 0))
    view = Destination("Razgled", "viewpoint", (at(4.1, 0),), wikidata=True)  # 100 m past the hut

    assert [hike.name for hike in net.hikes([HUT, view], [DOLINA])] == ["Razgled from Dolina"]
    # Reached where either meets the graph: a peak 120 m off the path's end, beside the hut.
    peak = Destination("Vrh", "peak", (at(4.12, 0),), ele=2_100)
    short = Net()
    short.line((0, 0), (4, 0))
    assert [hike.name for hike in short.hikes([HUT, peak], [DOLINA])] == ["Koča from Dolina"]
    assert [hike.name for hike in short.hikes([peak], [DOLINA])] == []


def test_a_lake_is_reached_where_a_path_meets_its_shore() -> None:
    net = Net()
    net.line((0, 0), (4, 0))
    lake = Destination("Jezero", "lake", (at(4.5, 0.5), at(4.05, 0.0)))

    (hike,) = kept(net.hikes([lake], [DOLINA]))

    assert hike.name == "Jezero from Dolina"
    assert hike.vertices[-1][1] == at(4, 0)


def test_a_car_park_starts_at_most_8_walks() -> None:
    net = Net()
    destinations = []
    for arm in range(10):
        angle = 2 * math.pi * arm / 10
        end = (3 * math.cos(angle), 3 * math.sin(angle))
        net.line((0, 0), end)
        destinations.append(Destination(f"Vrh {arm}", "peak", (at(*end),), ele=1_000 + arm))

    hikes = kept(net.hikes(destinations, [DOLINA]))

    assert sorted(hike.destination.name for hike in hikes) == [f"Vrh {arm}" for arm in range(2, 10)]


def test_one_name_twice_gets_the_way_it_takes() -> None:
    net = Net()
    net.line((0, 0), (4, 0), name="Spodnja pot")
    net.line((0, 3), (4, 0), name="Zgornja pot")

    hikes = kept(net.hikes([HUT], [DOLINA, Start("Dolina", (at(0, 3),))]))

    assert sorted(hike.name for hike in hikes) == ["Koča from Dolina via Spodnja pot", "Koča from Dolina via Zgornja pot"]


def test_a_hut_merged_with_a_lake_beside_it_is_reached_at_the_hut_not_the_far_shore() -> None:
    net = Net()
    net.line((0, 0), (4, 0))  # to the hut, 15 m off the path's end
    net.line((0, 0), (0, 2), (4.06, 2))  # to the lake's far shore
    hut = Destination("Koča", "hut", (at(4, 0.015),))
    lake = Destination("Jezero", "lake", tuple(at(4.06, east / 4) for east in range(9)))  # 60 m north of the hut, 2 km long

    (hike,) = kept(net.hikes([hut, lake], [DOLINA]))

    assert hike.name == "Koča from Dolina"
    assert hike.vertices[-1][1] == at(4, 0)


def test_a_car_park_named_after_a_hut_takes_its_other_name_for_a_walk_to_that_hut() -> None:
    net = Net()
    net.line((0, 0), (4, 0))
    net.line((0, 0), (-3, 0))
    peak = Destination("Vrh", "peak", (at(-3, 0),), ele=1_500)

    def names(start: Start) -> list[str]:
        return sorted(hike.name for hike in kept(net.hikes([HUT, peak], [start])))

    assert names(Start("Koča", (at(0, 0),), hut="Koča", fallback="Vas")) == ["Koča from Vas", "Vrh from Koča"]
    assert names(Start("Koča", (at(0, 0),), hut="Koča")) == ["Vrh from Koča"]
