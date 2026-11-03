"""NetworkHikes: chains of node-network legs (signpost to signpost) that make a day's outing.

Each leg graph is hand-built on a kilometre grid around 45N 15E: every named
point is one OSM node, every leg one way from its first point to its last.
"""

import gc
import logging
import math
import time
import weakref

from dayhike.built import BuildContext, BuiltWalk, built_id, route_of
from dayhike.network_hikes import (
    NETWORK_CAP,
    NETWORK_CAP_MAX,
    Start,
    candidates,
    chain_name,
    leg_graph,
    network_hikes,
    network_source,
    select,
    signpost_names,
    starts,
)
import dayhike.network_hikes as network_hikes_module
from dayhike.catalog import named_points
from dayhike.osm import OsmRelation, OsmSnapshot, OsmWay

KM_LAT = 1_000.0 / 6_371_000 * 180 / math.pi


def _lat_lon(x_km: float, y_km: float) -> tuple[float, float]:
    lat = 45.0 + y_km * KM_LAT
    return lat, 15.0 + x_km * KM_LAT / math.cos(math.radians(lat))


class Network:
    """Legs on named grid points: ``points`` maps a name to (x km, y km)."""

    def __init__(self, points: dict[str, tuple[float, float]]) -> None:
        self.node_ids = {name: 1 + index for index, name in enumerate(points)}
        self.nodes = {self.node_ids[name]: _lat_lon(*xy) for name, xy in points.items()}
        self.ways: dict[int, OsmWay] = {}
        self.legs: list[OsmRelation] = []

    def leg(self, relation_id: int, *points: str, name: str | None = None) -> None:
        way_id = 100 + len(self.ways)
        self.ways[way_id] = OsmWay(tuple(self.node_ids[point] for point in points), {"highway": "path"})
        tags = {"type": "route", "route": "hiking", "network": "lwn", "network:type": "node_network"}
        if name:
            tags["name"] = name
        self.legs.append(OsmRelation(relation_id, (("way", way_id),), tags, ("",)))

    def point(self, name: str) -> tuple[float, float]:
        return self.nodes[self.node_ids[name]]


def test_leg_ends_within_the_gap_join_one_signpost():
    # A-B and B-C share node B; C-D starts at C2, 30 m from C, a separate node.
    net = Network({"A": (0, 0), "B": (1, 0), "C": (2, 0), "C2": (2.03, 0), "D": (3, 0)})
    net.leg(1, "A", "B")
    net.leg(2, "B", "C")
    net.leg(3, "C2", "D")

    graph = leg_graph(net.legs, net.ways, net.nodes)

    assert len(graph.signposts) == 4
    ends = {leg.relation_id: set(leg.ends) for leg in graph.legs}
    assert len(ends[1] & ends[2]) == 1
    assert len(ends[2] & ends[3]) == 1
    assert not ends[1] & ends[3]


def _knooppunten_close_together() -> Network:
    # Knooppunten B, K and B3 stand 36 m and 32 m apart in a row (B to B3 68 m),
    # joined by connector legs B-K and K-B3 shorter than the gap join.
    net = Network({"A": (0, 0), "B": (2, 0), "K": (2.036, 0), "B3": (2.068, 0), "D": (2.068, -2)})
    net.leg(1, "A", "B")
    net.leg(2, "B", "K")
    net.leg(3, "K", "B3")
    net.leg(4, "B3", "D")
    return net


def test_a_network_hike_walks_the_connectors_between_knooppunten_close_together():
    # B and B3 are one signpost (joined through K), 68 m apart: the hike from A
    # to D walks the connector legs B-K and K-B3, not straight from B to B3.
    net = _knooppunten_close_together()

    (hike,) = network_hikes(net.legs, net.ways, net.nodes, [net.point("A")], [])

    assert [node for node, _ in hike.vertices] == [net.node_ids[name] for name in ("A", "B", "K", "B3", "D")]
    assert hike.parts == (1, 2, 3, 4)
    assert hike.way_ids == (100, 101, 102, 103)


def test_a_network_hike_steps_along_leg_ends_across_a_wide_signpost_with_no_connector():
    # Legs end at P1, P2 and P3, 30 m apart in a row (P1 to P3 60 m), one
    # signpost with no leg between them: from A to D the hike steps P1, P2, P3,
    # never straight from P1 to P3.
    net = Network({"A": (0, 0), "P1": (2, 0), "P2": (2.03, 0), "P3": (2.06, 0), "E": (2.03, 2), "D": (2.06, -2)})
    net.leg(1, "A", "P1")
    net.leg(2, "P2", "E")
    net.leg(3, "P3", "D")

    hikes = network_hikes(net.legs, net.ways, net.nodes, [net.point("A")], [])

    (hike,) = [hike for hike in hikes if hike.parts == (1, 3)]
    assert [node for node, _ in hike.vertices] == [net.node_ids[name] for name in ("A", "P1", "P2", "P3", "D")]
    assert hike.way_ids == (100, 102)
    assert hike.jumps == 0


def test_a_network_hike_reaches_a_connector_that_starts_beside_the_leg_end():
    # Leg A-B ends at B; connector B2-K-B3 starts at B2, 20 m from B, not at
    # B; B to B3 is 65 m. The hike steps B to B2 and walks the connector.
    net = Network({"A": (0, 0), "B": (2, 0), "B2": (2.02, 0), "K": (2.04, 0), "B3": (2.065, 0), "D": (2.065, -2)})
    net.leg(1, "A", "B")
    net.leg(2, "B2", "K", "B3")
    net.leg(3, "B3", "D")

    (hike,) = network_hikes(net.legs, net.ways, net.nodes, [net.point("A")], [])

    assert [node for node, _ in hike.vertices] == [net.node_ids[name] for name in ("A", "B", "B2", "K", "B3", "D")]
    assert hike.parts == (1, 2, 3)
    assert hike.way_ids == (100, 101, 102)
    assert hike.jumps == 0


def test_a_crossing_does_not_walk_back_along_the_next_leg():
    # Leg 1 ends at X; leg 2 starts at Y, 60 m on, and its way runs back
    # through X. Leg 3 ends at Z between them. The hike steps X, Z, Y rather
    # than walk leg 2's way from X to Y before walking it again.
    net = Network({"A": (0, 0), "X": (2, 0), "Z": (2.03, 0), "Y": (2.06, 0), "D": (2, -2), "E": (2.03, 2)})
    net.leg(1, "A", "X")
    net.leg(2, "Y", "X", "D")
    net.leg(3, "Z", "E")

    hikes = network_hikes(net.legs, net.ways, net.nodes, [net.point("A")], [])

    (hike,) = [hike for hike in hikes if hike.parts[:2] == (1, 2)]
    assert hike.parts == (1, 2)
    assert hike.way_ids == (100, 101)
    assert [node for node, _ in hike.vertices] == [net.node_ids[name] for name in ("A", "X", "Z", "Y", "X", "D")]


def _curved_crossing(bend_km: float) -> Network:
    # B and B3 are 51 m apart, one signpost through K between them. Leg B-Y-B3
    # bends out to Y, ``bend_km`` off the line: a crossing cheaper than the two
    # steps through K, and longer the farther Y lies.
    net = Network({
        "A": (0, 0), "B": (2, 0), "K": (2.0255, 0), "B3": (2.051, 0),
        "Y": (2.0255, bend_km), "E": (2.0255, -2), "D": (2.051, 2),
    })
    net.leg(1, "A", "B")
    net.leg(2, "K", "E")
    net.leg(3, "B3", "D")
    net.leg(4, "B", "Y", "B3")
    return net


def _hike_to_d(net: Network):
    hikes = network_hikes(net.legs, net.ways, net.nodes, [net.point("A")], [])
    (hike,) = [hike for hike in hikes if hike.vertices[-1][0] == net.node_ids["D"]]
    return hike


def test_a_crossing_at_most_twice_the_straight_jump_is_walked():
    net = _curved_crossing(0.042)  # 98 m round Y, 1.93 times the 51 m jump

    hike = _hike_to_d(net)

    assert [node for node, _ in hike.vertices] == [net.node_ids[name] for name in ("A", "B", "Y", "B3", "D")]
    assert hike.jumps == 0


def test_a_crossing_within_twice_the_straight_jump_is_walked_when_the_favourite_is_over():
    # Round Y is 107 m, 2.10 times the 51 m jump; stepping through K is 51 m.
    net = _curved_crossing(0.047)

    hike = _hike_to_d(net)

    assert [node for node, _ in hike.vertices] == [net.node_ids[name] for name in ("A", "B", "K", "B3", "D")]
    assert hike.parts == (1, 3)
    assert hike.jumps == 0


def _crossing_over_the_cap() -> Network:
    # B and B3 are 51 m apart, one signpost through leg ends K1, K2 and K3
    # that bend 80 m north (177 m across); leg B-Y-B3 bends 47 m south (107 m).
    # Every crossing is over 2 times the jump.
    net = Network({
        "A": (0, 0), "B": (2, 0), "B3": (2.051, 0), "Y": (2.0255, -0.047), "D": (2.051, -2),
        "K1": (2, 0.045), "K2": (2.0255, 0.08), "K3": (2.051, 0.045),
        "E1": (1.5, 2), "E2": (2.0255, 2), "E3": (2.6, 2),
    })
    net.leg(1, "A", "B")
    net.leg(2, "K1", "E1")
    net.leg(3, "B3", "D")
    net.leg(4, "B", "Y", "B3")
    net.leg(5, "K2", "E2")
    net.leg(6, "K3", "E3")
    return net


def test_a_crossing_over_twice_the_straight_jump_keeps_the_jump():
    net = _crossing_over_the_cap()

    hike = _hike_to_d(net)

    assert [node for node, _ in hike.vertices] == [net.node_ids[name] for name in ("A", "B", "B3", "D")]
    assert hike.parts == (1, 3)
    assert hike.jumps == 1


def _network_source(net: Network, caplog) -> tuple[list[str], list[str], list[BuiltWalk], OsmSnapshot]:
    # What ingest runs: the network source over a snapshot of ``net``, started at A.
    snapshot = OsmSnapshot(
        nodes=net.nodes, ways=net.ways, relations=net.legs, access_points=[net.point("A")]
    )
    lines: list[str] = []
    context = BuildContext(snapshot, [], lambda relation: None, lines.append)
    with caplog.at_level(logging.WARNING, logger="dayhike.network_hikes"):
        walks = network_source(context)
    return lines, [record.getMessage() for record in caplog.records], walks, snapshot


def test_a_crossing_over_the_cap_logs_one_warning_and_the_hike_is_kept(caplog):
    net = _crossing_over_the_cap()

    lines, warnings, walks, snapshot = _network_source(net, caplog)

    (walk,) = [walk for walk in walks if walk.vertices[-1][0] == net.node_ids["D"]]
    hike_id = built_id(route_of(walk.vertices, walk.way_ids, snapshot.ways)[0])
    (warning,) = warnings
    assert [record.levelno for record in caplog.records] == [logging.WARNING]
    assert f"{hike_id} {walk.name}" in warning
    assert "straight jump 51 m" in warning
    assert "crossing 107 m" in warning  # the shortest crossing found
    assert "ratio 2.10" in warning
    assert "assemble: network hikes: 1 straight jumps over 50 m left" in lines
    assert "assemble: network hikes: 1 crossings over 2.0 times the straight jump left as jumps" in lines


def test_a_crossing_within_the_cap_logs_no_warning(caplog):
    net = _curved_crossing(0.047)  # round Y is 2.10 times the jump, but stepping through K is walked

    lines, warnings, _, _ = _network_source(net, caplog)

    assert warnings == []
    assert "assemble: network hikes: 0 crossings over 2.0 times the straight jump left as jumps" in lines


FIGURE_EIGHT = {
    "S": (0, 0),
    "N1": (1.5, 1.5), "N2": (0, 3), "N3": (-1.5, 1.5),
    "S1": (1.5, -1.5), "S2": (0, -3), "S3": (-1.5, -1.5),
}


def _figure_eight() -> Network:
    # Two loops of four 2.1 km legs (8.5 km) that meet only at signpost S.
    net = Network(FIGURE_EIGHT)
    for relation_id, (a, b) in enumerate(
        [("S", "N1"), ("N1", "N2"), ("N2", "N3"), ("N3", "S"), ("S", "S1"), ("S1", "S2"), ("S2", "S3"), ("S3", "S")],
        start=1,
    ):
        net.leg(relation_id, a, b)
    return net


def _start_at(graph, point: tuple[float, float]) -> Start:
    (signpost,) = [index for index, at in enumerate(graph.signposts) if at == point]
    return Start((signpost,), point)


def _relations(graph, chain) -> list[int]:
    return [graph.legs[leg].relation_id for leg in chain.legs]


def test_a_figure_eight_offers_its_two_loops_from_the_middle():
    net = _figure_eight()
    graph = leg_graph(net.legs, net.ways, net.nodes)

    chains = candidates(graph, _start_at(graph, net.point("S")))

    loops = {frozenset(_relations(graph, chain)) for chain in chains if chain.loop}
    assert loops == {frozenset({1, 2, 3, 4}), frozenset({5, 6, 7, 8})}


def _petals(count: int) -> Network:
    # From S, ``count`` 5 km loops S-P-Q-S (2 km out, 1 km across, 2 km back) that share only S.
    points = {"S": (0.0, 0.0)}
    for i in range(count):
        for name, turn in (("P", -15), ("Q", 15)):
            angle = math.radians(i * 360 / count + turn)
            points[f"{name}{i}"] = (2 * math.cos(angle), 2 * math.sin(angle))
    net = Network(points)
    for i in range(count):
        net.leg(10 * i + 1, "S", f"P{i}")
        net.leg(10 * i + 2, f"P{i}", f"Q{i}")
        net.leg(10 * i + 3, f"Q{i}", "S")
    return net


def test_a_start_keeps_at_most_the_cap_of_distinct_chains():
    net = _petals(6)
    graph = leg_graph(net.legs, net.ways, net.nodes)
    start = _start_at(graph, net.point("S"))

    kept = select(graph, [candidates(graph, start)])

    assert NETWORK_CAP == 4
    assert len(kept) == 4


def _star(count: int) -> tuple[Network, list[tuple[float, float, str]]]:
    # From S, ``count`` 5 km two-leg lines S-Ai-Bi, evenly round; a named peak at every Bi.
    points = {"S": (0.0, 0.0)}
    for i in range(count):
        angle = math.radians(i * 360 / count)
        points[f"A{i}"] = (2.5 * math.cos(angle), 2.5 * math.sin(angle))
        points[f"B{i}"] = (5 * math.cos(angle), 5 * math.sin(angle))
    net = Network(points)
    for i in range(count):
        net.leg(10 * i + 1, "S", f"A{i}")
        net.leg(10 * i + 2, f"A{i}", f"B{i}")
    return net, [(*net.point(f"B{i}"), f"Vrh {i}") for i in range(count)]


def test_a_start_keeps_a_chain_per_destination_it_reaches_up_to_the_highest_cap():
    for count, kept_count in ((6, 6), (10, NETWORK_CAP_MAX)):
        net, peaks = _star(count)
        graph = leg_graph(net.legs, net.ways, net.nodes)

        kept = select(graph, [candidates(graph, _start_at(graph, net.point("S")))], destinations=peaks)

        assert NETWORK_CAP_MAX == 8
        assert len(kept) == kept_count


def test_a_start_reaching_few_destinations_keeps_the_cap():
    net, peaks = _star(6)
    graph = leg_graph(net.legs, net.ways, net.nodes)

    kept = select(graph, [candidates(graph, _start_at(graph, net.point("S")))], destinations=peaks[:2])

    assert len(kept) == NETWORK_CAP


def test_a_figure_eight_keeps_two_loops_and_no_near_copies():
    net = _figure_eight()
    graph = leg_graph(net.legs, net.ways, net.nodes)
    start = _start_at(graph, net.point("S"))

    kept = select(graph, [candidates(graph, start)])

    assert sorted(sorted(_relations(graph, chain)) for _, chain in kept) == [[1, 2, 3, 4], [5, 6, 7, 8]]


def _spokes(lengths_km: list[float], loop: bool = False) -> Network:
    # From S, one straight two-leg line per length, 60 degrees apart; with
    # ``loop`` also a 3.5 km loop S-L1-L2-S to the south-west.
    points = {"S": (0.0, 0.0), "L1": (-0.6, -1.4), "L2": (-1.4, -0.6)}
    for i, length in enumerate(lengths_km):
        angle = math.radians(i * 60)
        for name, share in (("A", 0.5), ("B", 1.0)):
            points[f"{name}{i}"] = (share * length * math.cos(angle), share * length * math.sin(angle))
    net = Network(points)
    for i in range(len(lengths_km)):
        net.leg(10 * i + 1, "S", f"A{i}")
        net.leg(10 * i + 2, f"A{i}", f"B{i}")
    if loop:
        net.leg(91, "S", "L1")
        net.leg(92, "L1", "L2")
        net.leg(93, "L2", "S")
    return net


def _kept_lengths_km(graph, kept) -> list[int]:
    return sorted(round(chain.length_m / 1000) for _, chain in kept)


def test_lines_nearest_a_days_length_are_kept():
    net = _spokes([4, 6, 10, 14, 20, 24])
    graph = leg_graph(net.legs, net.ways, net.nodes)
    start = _start_at(graph, net.point("S"))

    kept = select(graph, [candidates(graph, start)])

    assert _kept_lengths_km(graph, kept) == [6, 10, 14, 20]


def test_a_loop_beats_lines_of_a_better_length():
    net = _spokes([10, 10, 10, 10, 10], loop=True)
    graph = leg_graph(net.legs, net.ways, net.nodes)
    start = _start_at(graph, net.point("S"))

    kept = select(graph, [candidates(graph, start)])

    assert kept[0][1].loop
    assert _kept_lengths_km(graph, kept) == [4, 10, 10, 10]


def test_a_line_to_a_named_destination_beats_lines_of_a_better_length():
    net = _spokes([10, 10, 10, 10, 10, 4])
    graph = leg_graph(net.legs, net.ways, net.nodes)
    start = _start_at(graph, net.point("S"))
    peak = (*_lat_lon(4 * math.cos(math.radians(300)), 4 * math.sin(math.radians(300)) + 0.1), "Mali vrh")

    kept = select(graph, [candidates(graph, start)], destinations=[peak])

    assert _kept_lengths_km(graph, kept) == [4, 10, 10, 10]


def _hut_by_a_detour() -> Network:
    # Hut H is 3.5 km from S straight (S-A-H) or 8.4 km by a detour (S-X-A-H)
    # that ends on the same last leg. Nearer 10 km, the detour comes first and
    # leaves the direct line too little of its own to be shortlisted too.
    net = Network({"S": (0, 0), "A": (1.5, 0), "H": (3.5, 0), "X": (0, -3)})
    net.leg(1, "S", "A")
    net.leg(2, "A", "H")
    net.leg(3, "S", "X")
    net.leg(4, "X", "A")
    return net


def test_a_line_to_a_destination_is_its_direct_line_not_a_detour_nearer_a_days_length():
    net = _hut_by_a_detour()
    graph = leg_graph(net.legs, net.ways, net.nodes)
    lines = [chain for chain in candidates(graph, _start_at(graph, net.point("S"))) if not chain.loop]
    hut = (*net.point("H"), "Koča")

    kept = select(graph, [lines], destinations=[hut])

    assert [_relations(graph, chain) for _, chain in kept] == [[1, 2]]


def test_the_detour_stays_when_the_direct_line_is_a_twin_of_a_relation_dayhike():
    net = _hut_by_a_detour()
    graph = leg_graph(net.legs, net.ways, net.nodes)
    lines = [chain for chain in candidates(graph, _start_at(graph, net.point("S"))) if not chain.loop]
    hut = (*net.point("H"), "Koča")
    dayhike = [net.point(name) for name in ("S", "A", "H")]

    kept = select(graph, [lines], destinations=[hut], relations=[dayhike])

    assert [_relations(graph, chain) for _, chain in kept] == [[3, 4, 2]]


def test_the_direct_line_to_a_destination_on_a_loop_is_kept_beside_the_loop():
    # The 8.1 km loop S-A-H-B-S passes hut H; the direct line S-A-H (3.5 km)
    # lies wholly on it and is still kept, in place of the 8.4 km detour S-X-A-H.
    net = _hut_by_a_detour()
    net.nodes[len(net.nodes) + 1] = _lat_lon(2, 1.5)
    net.node_ids["B"] = len(net.nodes)
    net.leg(5, "H", "B")
    net.leg(6, "B", "S")
    graph = leg_graph(net.legs, net.ways, net.nodes)
    hut = (*net.point("H"), "Koča")
    # The one loop through H; the others would take the detour's legs too.
    chains = [
        chain
        for chain in candidates(graph, _start_at(graph, net.point("S")))
        if not chain.loop or sorted(_relations(graph, chain)) == [1, 2, 5, 6]
    ]

    kept = select(graph, [chains], destinations=[hut])

    lines = [_relations(graph, chain) for _, chain in kept if not chain.loop]
    assert [1, 2] in lines
    assert [3, 4, 2] not in lines


def test_a_twin_of_a_relation_dayhike_is_dropped():
    net = _figure_eight()
    graph = leg_graph(net.legs, net.ways, net.nodes)
    start = _start_at(graph, net.point("S"))
    # The same northern loop is a DayHike already, mapped from N2 the other way round.
    dayhike = [net.point(name) for name in ("N2", "N1", "S", "N3", "N2")]

    kept = select(graph, [candidates(graph, start)], relations=[dayhike])

    loops = [sorted(_relations(graph, chain)) for _, chain in kept if chain.loop]
    assert loops == [[5, 6, 7, 8]]


def test_the_same_loop_from_two_starts_is_kept_once():
    net = _figure_eight()
    graph = leg_graph(net.legs, net.ways, net.nodes)
    starts = [_start_at(graph, net.point("S")), _start_at(graph, net.point("N2"))]

    kept = select(graph, [candidates(graph, start) for start in starts])

    northern = [start for start, chain in kept if chain.loop and sorted(_relations(graph, chain)) == [1, 2, 3, 4]]
    assert len(northern) == 1


def test_signposts_near_parking_or_a_place_start_chains_once_per_place():
    # P1 is 300 m from one parking, P2 400 m from another and 600 m from P1;
    # V is 250 m from a village; M and W have neither near.
    net = Network({"P1": (0, 0), "P2": (0.6, 0), "M": (3, 0), "V": (6, 0), "W": (6.4, 2)})
    net.leg(1, "P1", "P2")
    net.leg(2, "P2", "M")
    net.leg(3, "M", "V")
    net.leg(4, "V", "W")
    graph = leg_graph(net.legs, net.ways, net.nodes)
    parking = [_lat_lon(-0.3, 0), _lat_lon(0.6, 0.4)]
    village = (*_lat_lon(6, 0.25), "Vas")

    found = starts(graph, parking, [village])

    names = {point: name for name, point in ((n, net.point(n)) for n in ("P1", "P2", "M", "V", "W"))}
    assert sorted(sorted(names[graph.signposts[i]] for i in start.signposts) for start in found) == [
        ["P1", "P2"],
        ["V"],
    ]


def test_a_signpost_takes_the_name_its_legs_share():
    # Legs are named after their two signposts; "?" marks an unnamed end.
    net = Network({"T": (0, 0), "L": (1, 0), "P": (0, 1), "X": (-1, 0), "G": (0, -1)})
    net.leg(1, "T", "L", name="Croix de Toulouse - Serre Lan")
    net.leg(2, "P", "T", name="Le Pinet - Croix de Toulouse")
    net.leg(3, "T", "X", name="Croix de Toulouse - ?")
    net.leg(4, "G", "T")
    graph = leg_graph(net.legs, net.ways, net.nodes)

    names = signpost_names(graph, guideposts=[(*_lat_lon(0, -1.02), "Granon")])

    by_point = {graph.signposts[i]: name for i, name in enumerate(names)}
    assert [by_point[net.point(n)] for n in ("T", "L", "P", "X", "G")] == [
        "Croix de Toulouse",
        "Serre Lan",
        "Le Pinet",
        None,
        "Granon",
    ]


def _named_figure_eight(extra: dict[str, tuple[float, float]] = {}) -> tuple[Network, object]:
    net = Network(FIGURE_EIGHT | extra)
    names = {"S": "Briançon", "N1": "Serre Lan", "N2": "Croix de Toulouse", "N3": "Le Pinet"}
    names |= {"S1": "Fontchristiane", "S2": "Pont de Cervières", "S3": "Le Fontenil"}
    for relation_id, (a, b) in enumerate(
        [("S", "N1"), ("N1", "N2"), ("N2", "N3"), ("N3", "S"), ("S", "S1"), ("S1", "S2"), ("S2", "S3"), ("S3", "S")],
        start=1,
    ):
        net.leg(relation_id, a, b, name=f"{names[a]} - {names[b]}")
    return net, leg_graph(net.legs, net.ways, net.nodes)


def _chain_through(graph, chains, relations: list[int]):
    (chain,) = [chain for chain in chains if _relations(graph, chain) == relations]
    return chain


def test_a_loop_is_named_from_its_start_via_a_destination_on_it():
    net, graph = _named_figure_eight()
    chains = candidates(graph, _start_at(graph, net.point("S")))
    loop = next(chain for chain in chains if chain.loop and set(_relations(graph, chain)) == {1, 2, 3, 4})
    peak = (*_lat_lon(0.05, 2.95), "Croix de Toulouse summit")

    named = chain_name(graph, loop, signpost_names(graph), destinations=named_points([peak]))
    plain = chain_name(graph, loop, signpost_names(graph))

    assert named == "Loop from Briançon via Croix de Toulouse summit"
    assert plain == "Loop from Briançon via Croix de Toulouse"


def test_a_line_is_named_from_its_start_to_where_it_goes():
    net, graph = _named_figure_eight()
    chains = candidates(graph, _start_at(graph, net.point("S")))
    line = _chain_through(graph, chains, [1, 2])
    peak = (*_lat_lon(0.05, 2.95), "Croix de Toulouse summit")

    assert chain_name(graph, line, signpost_names(graph)) == "Briançon – Croix de Toulouse"
    assert chain_name(graph, line, signpost_names(graph), destinations=named_points([peak])) == "Briançon – Croix de Toulouse summit"


def test_unnamed_signposts_take_the_nearest_place():
    net = _figure_eight()
    graph = leg_graph(net.legs, net.ways, net.nodes)
    chains = candidates(graph, _start_at(graph, net.point("S")))
    names = signpost_names(graph)
    places = named_points([(*_lat_lon(0.1, 0.1), "Briançon"), (*_lat_lon(0, 3.5), "Les Combes")])
    loop = next(chain for chain in chains if chain.loop)

    assert chain_name(graph, _chain_through(graph, chains, [1, 2]), names, places=places) == "Briançon – Les Combes"
    assert chain_name(graph, _chain_through(graph, chains, [1, 2]), names) == "Network hike"
    assert chain_name(graph, loop, names, places=places) == "Loop from Briançon"


def test_network_hikes_are_the_kept_chains_ready_for_the_catalog():
    net, _ = _named_figure_eight({"E": (4, 0)})
    net.leg(9, "S", "E")  # a lone 4 km leg is no NetworkHike on its own
    parking = [_lat_lon(0.1, 0)]

    hikes = network_hikes(net.legs, net.ways, net.nodes, parking, places=[])

    loops = sorted(hikes, key=lambda hike: hike.parts)
    assert [hike.parts for hike in loops] == [(1, 2, 3, 4), (5, 6, 7, 8)]
    north = loops[0]
    assert north.name == "Loop from Briançon via Croix de Toulouse"
    assert north.vertices[0][1] == north.vertices[-1][1] == net.point("S")
    assert [node_id for node_id, _ in north.vertices] == [net.node_ids[n] for n in ("S", "N1", "N2", "N3", "S")]
    assert north.way_ids == (100, 101, 102, 103)
    assert round(north.length_m) == round(4 * 2 ** 0.5 * 1500)


def _grid(size: int = 12) -> Network:
    # A knooppunt-like grid of 1.5 km legs offers thousands of chains from one signpost.
    net = Network({f"{i},{j}": (1.5 * i, 1.5 * j) for i in range(size) for j in range(size)})
    for i in range(size):
        for j in range(size):
            if i + 1 < size:
                net.leg(1 + len(net.legs), f"{i},{j}", f"{i + 1},{j}")
            if j + 1 < size:
                net.leg(1 + len(net.legs), f"{i},{j}", f"{i},{j + 1}")
    return net


def test_a_dense_network_keeps_the_cap_in_bounded_time():
    net = _grid()
    graph = leg_graph(net.legs, net.ways, net.nodes)
    started = time.monotonic()

    kept = select(graph, [candidates(graph, _start_at(graph, net.point("5,5")))])

    assert time.monotonic() - started < 20
    assert len(kept) == NETWORK_CAP
    assert len({chain.legs[0] for _, chain in kept}) > 1


def test_a_dense_start_searches_every_signpost_and_every_first_leg():
    net = _grid()
    graph = leg_graph(net.legs, net.ways, net.nodes)
    here, there = _start_at(graph, net.point("5,5")), _start_at(graph, net.point("5,6"))
    start = Start(here.signposts + there.signposts, here.point)

    chains = candidates(graph, start)

    assert {chain.signposts[0] for chain in chains} == set(start.signposts)
    for signpost in start.signposts:
        first_legs = {chain.legs[0] for chain in chains if chain.signposts[0] == signpost}
        assert len(first_legs) == 4


def test_network_hikes_hold_one_starts_candidates_at_a_time(monkeypatch):
    # Three parkings far apart on the grid: three starts, each with thousands of candidates.
    net = _grid()
    parking = [net.point("1,1"), net.point("5,5"), net.point("10,10")]
    held: list[weakref.ref] = []

    class Tracked(list):
        pass

    def tracked_candidates(graph, start):
        gc.collect()
        assert all(ref() is None for ref in held[:-1])
        chains = Tracked(candidates(graph, start))
        held.append(weakref.ref(chains))
        return chains

    monkeypatch.setattr(network_hikes_module, "candidates", tracked_candidates)

    hikes = network_hikes(net.legs, net.ways, net.nodes, parking, places=[])

    assert len(held) == 3
    assert hikes


def test_a_loop_does_not_go_via_its_own_start():
    net = _figure_eight()
    graph = leg_graph(net.legs, net.ways, net.nodes)
    loop = next(chain for chain in candidates(graph, _start_at(graph, net.point("S"))) if chain.loop)
    names = [None] * len(graph.signposts)
    names[loop.signposts[0]] = "Briançon"

    assert chain_name(graph, loop, names) == "Loop from Briançon"


def _triangles(count: int) -> tuple[Network, list[tuple[float, float]]]:
    # ``count`` separate 6.8 km triangles of legs 10 km apart, a parking by each one's first corner.
    points = {}
    for k in range(count):
        x, y = 10.0 * (k % 100), 10.0 * (k // 100)
        points |= {f"{k}a": (x, y), f"{k}b": (x + 2, y), f"{k}c": (x + 2, y + 2)}
    net = Network(points)
    for k in range(count):
        net.leg(3 * k + 1, f"{k}a", f"{k}b")
        net.leg(3 * k + 2, f"{k}b", f"{k}c")
        net.leg(3 * k + 3, f"{k}c", f"{k}a")
    return net, [_lat_lon(10.0 * (k % 100), 10.0 * (k // 100) + 0.03) for k in range(count)]


def test_network_hikes_take_time_in_proportion_to_the_networks():
    # Europe has tens of thousands of starts: a step per start that reads every leg or start would not end.
    def seconds(count: int) -> float:
        net, parking = _triangles(count)
        started = time.monotonic()
        hikes = network_hikes(net.legs, net.ways, net.nodes, parking, places=[])
        assert len(hikes) == count
        return time.monotonic() - started

    small, large = seconds(1_000), seconds(4_000)

    assert large < 8 * small  # 4 times the networks: 4 times the time, not 16
