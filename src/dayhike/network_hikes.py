"""NetworkHikes: day outings chained from node-network legs (2c).

A node network maps its trails as one relation per signpost-to-signpost leg
(``network:type=node_network``). A leg is not a DayHike, but a chain of legs
can be a day's outing. ``leg_graph`` joins the legs' main walks at their
signposts. ``starts`` are signposts near parking or a place, one per place.
``candidates`` lists day-sized chains from a start. ``select`` keeps a
few distinct ones per start, loops first, and drops Twins of each other and
of relation DayHikes. ``chain_name`` names them from signposts, destinations
and places. ``network_hikes`` runs all of it, pure; ``network_source`` is the
built-hike source ingest runs it as (ADR-0038, ADR-0039).
"""

from __future__ import annotations

import heapq
import logging
import math
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property

import numpy as np
from sklearn.neighbors import BallTree

from dayhike.built import BuildContext, BuiltWalk, built_id, route_of
from dayhike.catalog import (
    EARTH_RADIUS_M,
    GAP_JOIN_M,
    KIND_RELATION,
    PLACE_RADIUS_M,
    MainWalk,
    NamedPoints,
    _haversine_m,
    _linestring_length_m,
    assemble_main_walk,
    declared_ends,
    named_points,
)
from dayhike.osm import OsmRelation, OsmWay
from dayhike.serving import VERSION_DIFFERS
from dayhike.similarity import CORRIDOR_M, TWIN_STEP_M, _even_points, twin_groups

Point = tuple[float, float]  # (lat, lon)

logger = logging.getLogger(__name__)

NETWORK_MIN_M = 3_000.0  # a NetworkHike is day-sized: at least this long ...
NETWORK_MAX_M = 25_000.0  # ... and at most this
NETWORK_EXPANSIONS = 20_000  # legs one start's search may try
NETWORK_CAP = 4  # chains kept per start ...
NETWORK_CAP_MAX = 8  # ... or one per named destination its lines reach, up to this
NETWORK_SHORTLIST = 2 * NETWORK_CAP  # distinct chains per start checked for Twins, twice its cap
START_PARKING_M = 500.0  # a signpost this close to parking starts chains ...
START_PLACE_M = 300.0  # ... and one this close to a place node
START_CLUSTER_M = 1_000.0  # signposts this close to a start's first one start with it
PARKING_ENTRY_M = 50.0  # a trail node this close to parking is where a walk from it begins ...
PARKING_WALK_M = 2 * START_PARKING_M  # ... and a NetworkHike walks at most this far from there to its start
DESTINATION_M = 200.0  # a line whose end is this close to a named Peak, Hut, Viewpoint or lake goes there
NETWORK_TARGET_M = 10_000.0  # a day's walk: of two chains alike otherwise, the one nearer this length is better
GUIDEPOST_M = 50.0  # a named guidepost this close names a signpost its legs do not
CROSSING_MAX_RATIO = 2.0  # a crossing longer than this times the straight jump is not walked
# A relation DayHike that starts farther than this from every leg is no chain's Twin:
# a Twin lies 80 % on a chain of at most NETWORK_MAX_M, so it is at most NETWORK_MAX_M / 0.8 long.
TWIN_REACH_M = 2 * NETWORK_MAX_M


@dataclass(frozen=True)
class Leg:
    """One leg's main walk: its relation, ordered (node id, point) vertices,
    length, the signposts at its first and last vertex, and the names its
    relation gives its two ends (``declared_ends``, an unnamed "?" end left
    out), in no known order."""

    relation_id: int
    vertices: tuple[tuple[int, Point], ...]
    length_m: float
    ends: tuple[int, int]
    names: tuple[str, ...]
    way_ids: tuple[int, ...]  # the member ways walked, in walking order


@dataclass(frozen=True)
class LegGraph:
    """Legs between signposts; a signpost is the point of its first leg end."""

    legs: tuple[Leg, ...]
    signposts: tuple[Point, ...]

    @cached_property
    def incident(self) -> dict[int, list[int]]:
        """Per signpost, the legs (indexes into ``legs``) to another signpost from it, in leg order."""
        incident: dict[int, list[int]] = {}
        for index, leg in enumerate(self.legs):
            a, b = leg.ends
            if a != b:
                incident.setdefault(a, []).append(index)
                incident.setdefault(b, []).append(index)
        return incident


@dataclass(frozen=True)
class NetworkHike:
    """A kept chain as the Catalog takes it: its name, (node id, point)
    vertices from its start, member ways walked and leg relation ids
    (``parts``) in walking order, its length, and how many times it still
    goes straight more than ``GAP_JOIN_M`` from one leg to the next. ``capped``
    are the (straight jump, shortest crossing found) metres of those jumps that
    keep the jump because every crossing is over ``CROSSING_MAX_RATIO`` times it."""

    name: str
    vertices: tuple[tuple[int, Point], ...]
    way_ids: tuple[int, ...]
    parts: tuple[int, ...]
    length_m: float
    jumps: int = 0
    capped: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class Start:
    """Signposts near one parking or place, close enough to count as one start."""

    signposts: tuple[int, ...]
    point: Point


@dataclass(frozen=True)
class Chain:
    """Legs walked in order from a start signpost: ``legs`` index ``LegGraph.legs``,
    ``signposts`` are the ones passed (one more than the legs). A loop ends
    where it starts; no signpost is passed twice otherwise."""

    legs: tuple[int, ...]
    signposts: tuple[int, ...]
    length_m: float

    @property
    def loop(self) -> bool:
        return self.signposts[0] == self.signposts[-1]


def network_source(context: BuildContext) -> list[BuiltWalk]:
    """The NetworkHikes of what ingest read, as built walks: ``network_hikes``
    over every ``network:type=node_network`` relation (ingest reads only
    hiking and foot routes), started at its parking and places, going to its
    named Peaks, Huts, Viewpoints and lakes, its signposts also named by its
    guideposts, none a Twin of a relation DayHike that starts within
    ``TWIN_REACH_M`` of a leg."""
    snapshot = context.snapshot
    legs = [relation for relation in snapshot.relations if relation.tags.get("network:type") == "node_network"]
    hikes = network_hikes(
        legs,
        snapshot.ways,
        snapshot.nodes,
        snapshot.access_points,
        snapshot.places,
        snapshot.destinations,
        snapshot.guideposts,
        relations=_relation_walks(context, legs),
    )
    if context.progress is not None:
        jumps = sum(hike.jumps for hike in hikes)
        context.progress(f"assemble: network hikes: {jumps} straight jumps over {GAP_JOIN_M} m left")
        capped = sum(len(hike.capped) for hike in hikes)
        context.progress(
            f"assemble: network hikes: {capped} crossings over {CROSSING_MAX_RATIO} times the straight jump left as jumps"
        )
    for hike in hikes:
        for straight_m, crossing_m in hike.capped:
            logger.warning(
                "network hike %d %s keeps a straight jump: straight jump %.0f m, crossing %.0f m, ratio %.2f"
                " (cap %.1f)",
                built_id(route_of(hike.vertices, hike.way_ids, snapshot.ways)[0]),
                hike.name,
                straight_m,
                crossing_m,
                crossing_m / straight_m,
                CROSSING_MAX_RATIO,
            )
    return [BuiltWalk("network", hike.name, hike.vertices, hike.way_ids, hike.parts) for hike in hikes]


def _relation_walks(context: BuildContext, legs: Sequence[OsmRelation]) -> list[list[Point]]:
    # The main walks of the relation DayHikes that start near a leg: only those can be a chain's Twin.
    snapshot = context.snapshot
    ends = [
        snapshot.nodes[node_ids[0]]
        for leg in legs
        for kind, member_id in leg.members
        if kind == "way" and member_id in snapshot.ways
        and (node_ids := snapshot.ways[member_id].node_ids) and node_ids[0] in snapshot.nodes
    ]
    hikes = [hike for hike in context.dayhikes if hike.kind == KIND_RELATION]
    if not ends or not hikes:
        return []
    trailheads = np.radians([(hike.trailhead.latitude, hike.trailhead.longitude) for hike in hikes])
    distance, _ = BallTree(np.radians(ends), metric="haversine").query(trailheads, k=1)
    near = {hike.id for hike, d in zip(hikes, distance[:, 0]) if d * EARTH_RADIUS_M <= TWIN_REACH_M}
    walks = (context.main_walk(relation) for relation in snapshot.relations if relation.id in near)
    return [[point for _, point in walk.vertices] for walk in walks if len(walk.vertices) >= 2]


def network_hikes(
    legs: Sequence[OsmRelation],
    ways: Mapping[int, OsmWay],
    nodes: Mapping[int, Point],
    access_points: Sequence[Point],
    places: Sequence[tuple[float, float, str]],
    destinations: Sequence[tuple[float, float, str]] = (),
    guideposts: Sequence[tuple[float, float, str]] = (),
    relations: Sequence[Sequence[Point]] = (),
) -> list[NetworkHike]:
    """The NetworkHikes of node-network ``legs``, best first: ``leg_graph``,
    then ``candidates`` from every one of its ``starts``, ``select`` and
    ``chain_name``. ``relations`` are the relation DayHikes' walks, whose
    Twins are left out. A kept chain walks from one leg to the next across a
    signpost along the legs touching it when their ends lie farther apart than
    ``GAP_JOIN_M`` (see ``_connect``), unless that crossing is more than
    ``CROSSING_MAX_RATIO`` times the straight jump: then it jumps, as when no
    crossing is found. It favours walking legs over stepping between their
    ends, and takes the shortest crossing only when that favourite is too long.

    A kept chain that starts within ``START_PARKING_M`` of parking starts at
    the parking: it first walks there from it along ``ways`` (see
    ``_parking_walks``), and a loop walks back to where it started from the
    node it ends on, where such a walk is found, not again
    along the same trail where the chain itself walks on along it. Where no
    such walk is found, or it would take the hike out of ``NETWORK_MIN_M`` to
    ``NETWORK_MAX_M``, the hike starts at its signpost.
    """
    graph = leg_graph(legs, ways, nodes)
    found = starts(graph, access_points, places)
    kept = select(graph, (candidates(graph, start) for start in found), destinations, relations)
    firsts = {}
    returns = set()  # per loop ending on another node than it starts, (end node, start node)
    for _, chain in kept:
        leg = graph.legs[chain.legs[0]]
        first, point = leg.vertices[0] if leg.ends[0] == chain.signposts[0] else leg.vertices[-1]
        firsts[first] = point
        if chain.loop:
            leg = graph.legs[chain.legs[-1]]
            end = leg.vertices[-1][0] if leg.ends[0] == chain.signposts[-2] else leg.vertices[0][0]
            if end != first:
                returns.add((end, first))
    from_parking, to_parking = _parking_walks(ways, nodes, access_points, firsts, returns)
    names = signpost_names(graph, guideposts)
    marked, settled = named_points(destinations), named_points(places)
    touching: dict[int, list[int]] = {}  # per signpost, the legs with an end joined into it
    for index, leg in enumerate(graph.legs):
        for at in set(leg.ends):
            touching.setdefault(at, []).append(index)
    hikes = []
    for _, chain in kept:
        vertices: list[tuple[int, Point]] = []
        way_ids: list[int] = []
        parts: list[int] = []
        jumps = 0
        capped: list[tuple[float, float]] = []
        last = None
        for index, at in zip(chain.legs, chain.signposts):
            leg = graph.legs[index]
            forward = leg.ends[0] == at
            steps = list(leg.vertices if forward else leg.vertices[::-1])
            straight_m = _haversine_m(*vertices[-1][1], *steps[0][1]) if vertices else 0.0
            if straight_m > GAP_JOIN_M:
                walked = [via for via in touching[at] if via not in (last, index)]
                connected = None
                crossing_m = 0.0
                for gap_cost in (GAP_JOIN_M, 0.0):
                    found = _connect(
                        graph, at, touching[at], walked, vertices[-1][0], steps[0][0], ways, nodes, gap_cost
                    )
                    if found is None:
                        break
                    crossing = [vertices[-1][1], *(point for _, point in found[0]), steps[0][1]]
                    crossing_m = _linestring_length_m(crossing)
                    if crossing_m <= CROSSING_MAX_RATIO * straight_m:
                        connected = found
                        break
                if connected is None:
                    jumps += 1
                    if crossing_m:  # a crossing was found, but every one is over the cap
                        capped.append((straight_m, crossing_m))
                else:
                    path, via_ways, via_legs = connected
                    vertices += path
                    way_ids += via_ways
                    parts += [graph.legs[via].relation_id for via in via_legs]
            for vertex in steps:
                if not vertices or vertices[-1][1] != vertex[1]:
                    vertices.append(vertex)
            way_ids += leg.way_ids if forward else leg.way_ids[::-1]
            parts.append(leg.relation_id)
            last = index
        if vertices[0][0] in from_parking:
            back = None
            if chain.loop:
                end = vertices[-1][0]
                back = from_parking[end] if end == vertices[0][0] else to_parking.get((end, vertices[0][0]))
            vertices, way_ids = _start_at_parking(vertices, way_ids, *from_parking[vertices[0][0]], back)
        hikes.append(
            NetworkHike(
                chain_name(graph, chain, names, marked, settled),
                tuple(vertices),
                tuple(way_ids),
                tuple(parts),
                _linestring_length_m([point for _, point in vertices]),
                jumps,
                tuple(capped),
            )
        )
    return hikes


def _parking_walks(
    ways: Mapping[int, OsmWay],
    nodes: Mapping[int, Point],
    access_points: Sequence[Point],
    sources: Mapping[int, Point],
    returns: Iterable[tuple[int, int]],
) -> tuple[
    dict[int, tuple[list[tuple[int, Point]], list[int]]],
    dict[tuple[int, int], tuple[list[tuple[int, Point]], list[int]]],
]:
    """Per source node (id to point), the shortest walk to it along ``ways``
    from a node within ``PARKING_ENTRY_M`` of an access point (parking) that
    lies within ``START_PARKING_M`` of the source, of at most
    ``PARKING_WALK_M``: its (node id, point) vertices from there to the
    source, and the ways it walks, in walking order. A source that close to
    parking itself, or with no such walk, has none. And per (end node, source)
    of ``returns``, the shortest such walk to the end node from the node the
    source's walk starts at, where both are found."""
    if not access_points or not sources:
        return {}, {}
    ids = list(sources)
    tree = BallTree(np.radians(access_points), metric="haversine")
    near = tree.query_radius(np.radians([sources[node] for node in ids]), r=START_PARKING_M / EARTH_RADIUS_M)
    parkings = {node: [access_points[i] for i in found] for node, found in zip(ids, near) if len(found)}
    # Only ways with a node in a grid cell next to a source's: a cell is PARKING_WALK_M wide or more,
    # so every node a walk of at most that reaches lies in one.
    cell_lat = math.degrees(PARKING_WALK_M / EARTH_RADIUS_M)
    widest = max((abs(sources[node][0]) for node in parkings), default=0.0) + cell_lat
    cell_lon = cell_lat / math.cos(math.radians(min(widest, 89.0)))

    def cell(point: Point) -> tuple[int, int]:
        return math.floor(point[0] / cell_lat), math.floor(point[1] / cell_lon)

    returns = [(end, source) for end, source in returns if source in parkings and end in nodes]
    cells = {
        (row + dr, column + dc)
        for row, column in map(cell, [*(sources[node] for node in parkings), *(nodes[end] for end, _ in returns)])
        for dr in (-1, 0, 1)
        for dc in (-1, 0, 1)
    }
    edges: dict[int, list[tuple[int, float, int]]] = {}
    for way_id, way in ways.items():
        on_way = [node for node in way.node_ids if node in nodes]
        if not any(cell(nodes[node]) in cells for node in on_way):
            continue
        for a, b in zip(on_way, on_way[1:]):
            metres = _haversine_m(*nodes[a], *nodes[b])
            edges.setdefault(a, []).append((b, metres, way_id))
            edges.setdefault(b, []).append((a, metres, way_id))

    def at_parking(node: int, near: Sequence[Point]) -> bool:
        return any(_haversine_m(*nodes[node], *point) <= PARKING_ENTRY_M for point in near)

    def walk(source: int, reached: Callable[[int], bool]) -> tuple[list[tuple[int, Point]], list[int]] | None:
        came: dict[int, tuple[float, int, int] | None] = {source: None}  # cost, way, node towards the source
        done: set[int] = set()
        queue = [(0.0, source)]
        while queue:
            cost, node = heapq.heappop(queue)
            if node in done:
                continue
            done.add(node)
            if reached(node):
                path, way_ids = [], []
                while came[node] is not None:
                    _, way_id, after = came[node]
                    path.append((node, nodes[node]))
                    if not way_ids or way_ids[-1] != way_id:
                        way_ids.append(way_id)
                    node = after
                return [*path, (source, nodes[source])], way_ids
            for there, step, way_id in edges.get(node, ()):
                walked = cost + step
                if there not in done and walked <= PARKING_WALK_M and (there not in came or walked < came[there][0]):
                    came[there] = (walked, way_id, node)
                    heapq.heappush(queue, (walked, there))
        return None

    walks = {}
    for source, near in parkings.items():
        if not at_parking(source, near):
            found = walk(source, lambda node, near=near: at_parking(node, near))
            if found is not None:
                walks[source] = found
    backs = {}
    for end, source in returns:
        if source in walks:
            entry = walks[source][0][0][0]
            found = walk(end, lambda node, entry=entry: node == entry)
            if found is not None:
                backs[end, source] = found
    return walks, backs


def _start_at_parking(
    vertices: Sequence[tuple[int, Point]],
    way_ids: Sequence[int],
    path: Sequence[tuple[int, Point]],
    path_ways: Sequence[int],
    back: tuple[Sequence[tuple[int, Point]], Sequence[int]] | None,
) -> tuple[list[tuple[int, Point]], list[int]]:
    """The hike along ``vertices`` and ``way_ids`` walked first along
    ``path`` (ways ``path_ways``) from the parking to its first vertex and
    then, when given ``back`` (a loop's walk from parking to its last vertex
    and its ways), back along that at the end. Where the hike walks on along
    the path, it starts (or ends) where the path leaves it. Unchanged when
    that takes it out of ``NETWORK_MIN_M`` to ``NETWORK_MAX_M``."""
    walked = [*path[:-1], *vertices]
    ways = [*path_ways, *way_ids]
    if back is not None:
        back_path, back_ways = back
        walked += back_path[::-1][1:]
        ways += back_ways[::-1]
        _unretrace(walked, len(path) + len(vertices) - 2)
    _unretrace(walked, len(path) - 1)
    if not NETWORK_MIN_M <= _linestring_length_m([point for _, point in walked]) <= NETWORK_MAX_M:
        return list(vertices), list(way_ids)
    return walked, ways


def _unretrace(vertices: list[tuple[int, Point]], at: int) -> None:
    # Drop the walk out and back that turns at vertices[at].
    while 0 < at < len(vertices) - 1 and vertices[at - 1][0] == vertices[at + 1][0]:
        del vertices[at : at + 2]
        at -= 1


def _connect(
    graph: LegGraph,
    signpost: int,
    touching: Sequence[int],
    walked: Sequence[int],
    source: int,
    target: int,
    ways: Mapping[int, OsmWay],
    nodes: Mapping[int, Point],
    gap_cost: float,
) -> tuple[list[tuple[int, Point]], list[int], list[int]] | None:
    """The shortest walk from node ``source`` to node ``target``, two leg ends
    at ``signpost``, along the ways of the legs ``walked`` (the legs
    ``touching`` it, indexes into ``graph.legs`` with an end joined into it,
    but the chain's own legs either side) and straight gaps of at most
    ``GAP_JOIN_M`` between the ends there of every leg ``touching`` it, a gap costing
    ``gap_cost`` more than its length: the (node id, point) vertices after
    ``source`` and before ``target``, and the ways and the legs it walks, in
    walking order. None when no such walk joins them.

    Signposts join leg ends link by link, so leg ends closer than
    ``GAP_JOIN_M`` to each other are one signpost, whose ends may lie farther
    apart than that."""
    edges: dict[int, list[tuple[int, float, int | None, int | None]]] = {}
    ends: set[int] = set()
    for index in touching:
        leg = graph.legs[index]
        for (node, _), end in ((leg.vertices[0], leg.ends[0]), (leg.vertices[-1], leg.ends[1])):
            if end == signpost:
                ends.add(node)
    for index in walked:
        for way_id in graph.legs[index].way_ids:
            on_way = [node for node in ways[way_id].node_ids if node in nodes]
            for a, b in zip(on_way, on_way[1:]):
                metres = _haversine_m(*nodes[a], *nodes[b])
                edges.setdefault(a, []).append((b, metres, way_id, index))
                edges.setdefault(b, []).append((a, metres, way_id, index))
    for a in ends:
        for b in ends:
            if a != b and (metres := _haversine_m(*nodes[a], *nodes[b])) <= GAP_JOIN_M:
                edges.setdefault(a, []).append((b, metres + gap_cost, None, None))
    came: dict[int, tuple[float, int | None, int | None, int] | None] = {source: None}  # cost, way, leg, node before
    done: set[int] = set()
    queue = [(0.0, source)]
    while queue:
        cost, node = heapq.heappop(queue)
        if node in done:
            continue
        done.add(node)
        if node == target:
            break
        for there, step, way_id, index in edges.get(node, ()):
            if there not in done and (there not in came or cost + step < came[there][0]):
                came[there] = (cost + step, way_id, index, node)
                heapq.heappush(queue, (cost + step, there))
    if target not in done:
        return None
    path, via_ways, via_legs = [], [], []
    node = target
    while came[node] is not None:
        _, way_id, index, node = came[node]
        path.append(node)
        if way_id is not None and (not via_ways or via_ways[-1] != way_id):
            via_ways.append(way_id)
        if index is not None and (not via_legs or via_legs[-1] != index):
            via_legs.append(index)
    return (
        [(node, nodes[node]) for node in path[::-1][1:]],
        via_ways[::-1],
        via_legs[::-1],
    )


def leg_graph(
    legs: Sequence[OsmRelation],
    ways: Mapping[int, OsmWay],
    nodes: Mapping[int, Point],
) -> LegGraph:
    """The legs' main walks, their ends within ``GAP_JOIN_M`` joined into one signpost."""
    walks = []
    for relation in legs:
        walk = assemble_main_walk(relation, ways, nodes, None)
        if len(walk.vertices) >= 2:
            walks.append((relation, walk))
    ends = [point for _, walk in walks for point in (walk.vertices[0][1], walk.vertices[-1][1])]
    signpost_of, signposts = _join(ends)
    return LegGraph(
        tuple(
            Leg(
                relation.id,
                tuple(walk.vertices),
                walk.length_m,
                (signpost_of[2 * i], signpost_of[2 * i + 1]),
                tuple(name for name in declared_ends(relation.tags) or () if name != "?"),
                _walked_ways(relation, ways, walk),
            )
            for i, (relation, walk) in enumerate(walks)
        ),
        signposts,
    )


def signpost_names(graph: LegGraph, guideposts: Sequence[tuple[float, float, str]] = ()) -> list[str | None]:
    """Each signpost's name, or None: the end name that most of its legs give,
    when at least two do; else the other end name of a leg whose far end has
    the leg's other name; else a named guidepost (lat, lon, name) within ``GUIDEPOST_M``.
    """
    votes: list[Counter[str]] = [Counter() for _ in graph.signposts]
    for leg in graph.legs:
        for signpost in set(leg.ends):
            votes[signpost].update(set(leg.names))
    names: list[str | None] = []
    for counts in votes:
        top = counts.most_common(2)
        unique = top and top[0][1] >= 2 and (len(top) == 1 or top[1][1] < top[0][1])
        names.append(top[0][0] if unique else None)
    changed = True
    while changed:
        changed = False
        for leg in graph.legs:
            if len(set(leg.names)) != 2:
                continue
            for here, there in (leg.ends, leg.ends[::-1]):
                if names[here] is None and names[there] in leg.names:
                    names[here] = next(name for name in leg.names if name != names[there])
                    changed = True
    marked = named_points(guideposts)
    return [name or _nearest(marked, point, GUIDEPOST_M) for name, point in zip(names, graph.signposts)]


def _walked_ways(relation: OsmRelation, ways: Mapping[int, OsmWay], walk: MainWalk) -> tuple[int, ...]:
    # MainWalk's per-listing lists follow the member ways found in the snapshot.
    listed = [member_id for kind, member_id in relation.members if kind == "way" and member_id in ways]
    order = {position: way_id for way_id, position in zip(listed, walk.walk_order) if position is not None}
    return tuple(order[position] for position in sorted(order))


def _join(points: Sequence[Point]) -> tuple[list[int], tuple[Point, ...]]:
    # Union points within GAP_JOIN_M; each group's signpost is its first point.
    if not points:
        return [], ()
    parent = list(range(len(points)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    tree = BallTree(np.radians(points), metric="haversine")
    for i, near in enumerate(tree.query_radius(np.radians(points), r=GAP_JOIN_M / EARTH_RADIUS_M)):
        for j in near:
            a, b = sorted((root(i), root(int(j))))
            parent[b] = a
    index: dict[int, int] = {}
    signpost_of = [index.setdefault(root(i), len(index)) for i in range(len(points))]
    return signpost_of, tuple(points[group] for group in index)


def starts(
    graph: LegGraph,
    access_points: Sequence[Point],
    places: Sequence[tuple[float, float, str]],
) -> list[Start]:
    """Where chains start: signposts within ``START_PARKING_M`` of an access
    point (parking) or ``START_PLACE_M`` of a place (lat, lon, name). In
    signpost order, each such signpost joins the first start whose first
    signpost is within ``START_CLUSTER_M``, else starts a new one there.
    """
    near = [False] * len(graph.signposts)
    for points, radius_m in ((access_points, START_PARKING_M), ([(lat, lon) for lat, lon, _ in places], START_PLACE_M)):
        if not points or not graph.signposts:
            continue
        tree = BallTree(np.radians(points), metric="haversine")
        distance, _ = tree.query(np.radians(graph.signposts), k=1)
        near = [was or float(d[0]) * EARTH_RADIUS_M <= radius_m for was, d in zip(near, distance)]
    found: list[list[int]] = []
    # Starts by grid cell of their first signpost; a cell is START_CLUSTER_M wide or more
    # everywhere, so a start within START_CLUSTER_M lies in the cell or one next to it.
    cell_lat = math.degrees(START_CLUSTER_M / EARTH_RADIUS_M)
    widest = max((abs(lat) for lat, _ in graph.signposts), default=0.0) + cell_lat
    cell_lon = cell_lat / math.cos(math.radians(min(widest, 89.0)))
    cells: dict[tuple[int, int], list[int]] = {}
    for signpost, is_near in enumerate(near):
        if not is_near:
            continue
        point = graph.signposts[signpost]
        row, column = math.floor(point[0] / cell_lat), math.floor(point[1] / cell_lon)
        nearby = (
            start
            for r in (row - 1, row, row + 1)
            for c in (column - 1, column, column + 1)
            for start in cells.get((r, c), ())
            if _haversine_m(*point, *graph.signposts[found[start][0]]) <= START_CLUSTER_M
        )
        first = min(nearby, default=None)
        if first is not None:
            found[first].append(signpost)
        else:
            cells.setdefault((row, column), []).append(len(found))
            found.append([signpost])
    return [Start(tuple(members), graph.signposts[members[0]]) for members in found]


def candidates(graph: LegGraph, start: Start) -> list[Chain]:
    """Chains of at least two legs from ``start``'s signposts, ``NETWORK_MIN_M``
    to ``NETWORK_MAX_M`` long: every line that passes no signpost twice and
    every loop back to its first signpost, each loop once. The search tries
    at most ``NETWORK_EXPANSIONS`` legs, shared equally by every first leg
    from every start signpost, in order, each passing on what it leaves unused.
    """
    incident = graph.incident
    found: list[Chain] = []
    loops_seen: set[frozenset[int]] = set()
    roots = [(first, index) for first in start.signposts for index in incident.get(first, ())]
    budget = NETWORK_EXPANSIONS
    for n, (first, root) in enumerate(roots):
        left = budget // (len(roots) - n)
        budget -= left
        stack: list[tuple[tuple[int, ...], tuple[int, ...], float, Sequence[int]]] = [((), (first,), 0.0, (root,))]
        while stack and left > 0:
            legs, signposts, length_m, options = stack.pop()
            deeper = []
            for index in options:
                if index in legs:
                    continue
                left -= 1
                leg = graph.legs[index]
                at = leg.ends[1] if leg.ends[0] == signposts[-1] else leg.ends[0]
                walked = length_m + leg.length_m
                if walked > NETWORK_MAX_M:
                    continue
                chain = Chain((*legs, index), (*signposts, at), walked)
                if at == first:
                    if len(chain.legs) >= 2 and walked >= NETWORK_MIN_M and frozenset(chain.legs) not in loops_seen:
                        loops_seen.add(frozenset(chain.legs))
                        found.append(chain)
                    continue
                if at in signposts:
                    continue
                if len(chain.legs) >= 2 and walked >= NETWORK_MIN_M:
                    found.append(chain)
                deeper.append((chain.legs, chain.signposts, walked, incident.get(at, ())))
            stack += reversed(deeper)  # legs in listed order, depth first
        budget += left
    return found


def walk(graph: LegGraph, chain: Chain) -> list[tuple[int, Point]]:
    """The chain's (node id, point) vertices from its start, each leg in walking direction."""
    vertices: list[tuple[int, Point]] = []
    for index, at in zip(chain.legs, chain.signposts):
        leg = graph.legs[index]
        steps = leg.vertices if leg.ends[0] == at else leg.vertices[::-1]
        for vertex in steps:
            if not vertices or vertices[-1][1] != vertex[1]:
                vertices.append(vertex)
    return vertices


def select(
    graph: LegGraph,
    per_start: Iterable[Iterable[Chain]],
    destinations: Sequence[tuple[float, float, str]] = (),
    relations: Sequence[Sequence[Point]] = (),
) -> list[tuple[int, Chain]]:
    """The chains to keep, as (start index into ``per_start``, chain), best first.
    ``per_start`` is read once, one start at a time, so it may be lazy.

    Loops come first, then lines that end at one of the named
    ``destinations`` (lat, lon, name), then other lines; within each, nearer
    ``NETWORK_TARGET_M`` (by ratio) first. Per start, best first, a chain is
    shortlisted when more than ``VERSION_DIFFERS`` of its length lies on legs
    that no chain shortlisted there walks, up to twice the start's cap. A
    start's cap is the number of named destinations its lines reach, at least
    ``NETWORK_CAP`` and at most ``NETWORK_CAP_MAX``. Of its first
    ``NETWORK_SHORTLIST``, at most ``NETWORK_CAP`` per start are kept, none a
    Twin of a better chain kept or of one of the relation DayHikes' walks
    ``relations`` (Twins of Twins count, as in ``twin_groups``). Then, best
    first, its other shortlisted chains are kept up to its cap, none a Twin of
    a relation DayHike or of a chain kept before, so none takes the place of
    a chain kept before.

    A kept line to a destination is then replaced by that destination's
    direct line from the start, its shortest line, when that is shorter and
    no other line shortlisted there is it or goes to the destination before
    it. The direct line takes the line's place in the order and needs no
    share of its own; the line it replaces stays when the direct line is a
    Twin of a relation DayHike or of another chain kept (see ``_widened``).
    """
    reached = named_points(destinations)
    destination_of = {
        signpost: name
        for signpost, point in enumerate(graph.signposts)
        if (name := _nearest(reached, point, DESTINATION_M))
    }

    def goes_to(chain: Chain) -> str | None:
        return None if chain.loop else destination_of.get(chain.signposts[-1])

    def preference(chain: Chain) -> tuple[int, float]:
        kind = 0 if chain.loop else 1 if goes_to(chain) else 2
        return kind, abs(math.log(chain.length_m / NETWORK_TARGET_M))

    shortlist: list[tuple[int, Chain]] = []
    extra: list[tuple[int, Chain]] = []  # shortlisted past NETWORK_SHORTLIST, at a start with a higher cap
    caps: list[int] = []
    direct_of: dict[int, Chain] = {}  # by id of a shortlisted chain, the direct line that may replace it
    for start, listed in enumerate(per_start):
        listed = list(listed)
        direct: dict[str, Chain] = {}
        for chain in listed:
            if (name := goes_to(chain)) and (name not in direct or chain.length_m < direct[name].length_m):
                direct[name] = chain
        caps.append(min(NETWORK_CAP_MAX, max(NETWORK_CAP, len(direct))))
        legs: list[set[int]] = []
        picked: list[Chain] = []
        for chain in sorted(listed, key=preference):
            if len(legs) >= 2 * caps[start]:
                break
            walked = set(chain.legs)
            # Chains of one network share whole legs, so the share off another is in leg metres.
            if all(
                1.0 - sum(graph.legs[leg].length_m for leg in walked & other) / chain.length_m > VERSION_DIFFERS
                for other in legs
            ):
                legs.append(walked)
                (shortlist if len(legs) <= NETWORK_SHORTLIST else extra).append((start, chain))
                picked.append(chain)
        # The first line shortlisted to a destination may become its direct line, unless that is shortlisted too.
        served = {id(chain) for chain in picked}
        for chain in picked:
            if (name := goes_to(chain)) and id(direct[name]) not in served and direct[name].length_m < chain.length_m:
                served.add(id(direct[name]))
                direct_of[id(chain)] = direct[name]
    given = [_even_points(*np.array(walk).T, TWIN_STEP_M) for walk in relations]
    # A group's root is its first row, so a group holding a relation has a root below len(given).
    groups = twin_groups([*given, *(_route(graph, chain) for _, chain in shortlist)])[len(given) :]
    kept: list[tuple[int, Chain]] = []
    kept_groups: set[int] = set()
    count: Counter[int] = Counter()
    for row in sorted(range(len(shortlist)), key=lambda row: preference(shortlist[row][1])):
        start, chain = shortlist[row]
        if count[start] >= NETWORK_CAP or groups[row] < len(given) or groups[row] in kept_groups:
            continue
        kept_groups.add(int(groups[row]))
        count[start] += 1
        kept.append((start, chain))
    taken = {id(chain) for _, chain in kept}
    left = [row for row in shortlist if id(row[1]) not in taken] + extra
    widened = _widened(graph, kept, sorted(left, key=lambda row: preference(row[1])), caps, direct_of, given)
    return [(start, chain) for start, chain, _ in sorted(widened, key=lambda row: preference(row[2]))]


def _widened(
    graph: LegGraph,
    kept: Sequence[tuple[int, Chain]],
    extra: Sequence[tuple[int, Chain]],
    caps: Sequence[int],
    direct_of: Mapping[int, Chain],
    given: Sequence[np.ndarray],
) -> list[tuple[int, Chain, Chain]]:
    """The ``kept`` (start, chain) and, best first, the ``extra`` ones up to
    their start's cap in ``caps``; then each of them replaced by its direct
    line in ``direct_of``. An extra chain or a direct line is left out when it
    is a Twin of a relation DayHike's walk (``given``) or of a chain kept or
    put in before it (but the one a direct line replaces). It is grouped apart
    from ``kept``, so it never joins two kept chains into one group and never
    takes the place of one. As (start, chain, the chain it replaced or itself)."""
    chains = [chain for _, chain in (*kept, *extra)]
    directs = [direct_of[id(chain)] for chain in chains if id(chain) in direct_of]
    groups = [int(group) for group in twin_groups([*given, *(_route(graph, chain) for chain in (*chains, *directs))])]
    group_of = {id(chain): group for chain, group in zip(chains, groups[len(given) :])}
    direct_group = {id(chain): group for chain, group in zip(directs, groups[len(given) + len(chains) :])}
    chosen = list(kept)
    held = Counter(group_of[id(chain)] for _, chain in kept)
    count = Counter(start for start, _ in kept)
    for start, chain in extra:
        group = group_of[id(chain)]
        if count[start] < caps[start] and group >= len(given) and not held[group]:
            held[group] += 1
            count[start] += 1
            chosen.append((start, chain))
    widened = []
    taken: set[int] = set()
    for start, chain in chosen:
        line = direct_of.get(id(chain))
        if line is not None:
            group = direct_group[id(line)]
            if group >= len(given) and not held[group] - (group_of[id(chain)] == group) and group not in taken:
                taken.add(group)
                widened.append((start, line, chain))
                continue
        widened.append((start, chain, chain))
    return widened


def _route(graph: LegGraph, chain: Chain) -> np.ndarray:
    # The chain's walk as (lat, lon) rows every TWIN_STEP_M, as Twins are compared.
    points = np.array([point for _, point in walk(graph, chain)])
    return _even_points(points[:, 0], points[:, 1], TWIN_STEP_M)


def chain_name(
    graph: LegGraph,
    chain: Chain,
    names: Sequence[str | None],
    destinations: NamedPoints | None = None,
    places: NamedPoints | None = None,
) -> str:
    """"Loop from {start} via {destination}" or "{start} – {destination}".

    The start is its signpost's name (``names``, see ``signpost_names``),
    else the nearest of ``places`` within ``PLACE_RADIUS_M``. A loop goes via
    the named ``destinations`` point farthest from its start within
    ``CORRIDOR_M`` of the walk, else via its farthest named signpost but its start. A line
    goes to a ``destinations`` point within ``DESTINATION_M`` of its end, else
    to its end signpost's name, else to the place nearest its end when that
    is not the start's. Without a start or destination the name says less:
    "Loop from {start}", "Hike to {destination}", ..., else "Network hike".
    """
    first = graph.signposts[chain.signposts[0]]
    start = names[chain.signposts[0]] or _nearest(places, first, PLACE_RADIUS_M)
    if not chain.loop:
        last = graph.signposts[chain.signposts[-1]]
        end = _nearest(destinations, last, DESTINATION_M) or names[chain.signposts[-1]]
        if end is None and (place := _nearest(places, last, PLACE_RADIUS_M)) != start:
            end = place
        if start and end:
            return f"{start} – {end}"
        return f"Hike from {start}" if start else f"Hike to {end}" if end else "Network hike"
    vertices = sorted((point for _, point in walk(graph, chain)), key=lambda point: -_haversine_m(*first, *point))
    via = next((name for point in vertices if (name := _nearest(destinations, point, CORRIDOR_M))), None)
    if via is None:
        passed = sorted(chain.signposts[1:-1], key=lambda signpost: -_haversine_m(*first, *graph.signposts[signpost]))
        via = next((names[signpost] for signpost in passed if names[signpost]), None)
    if start and via:
        return f"Loop from {start} via {via}"
    return f"Loop from {start}" if start else f"Loop via {via}" if via else "Network hike"


def _nearest(points: NamedPoints | None, point: Point, radius_m: float) -> str | None:
    return None if points is None else points.nearest(*point, radius_m)
