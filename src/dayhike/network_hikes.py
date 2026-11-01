"""NetworkHikes: day outings chained from node-network legs (2c).

A node network maps its trails as one relation per signpost-to-signpost leg
(``network:type=node_network``). A leg is not a DayHike, but a chain of legs
can be a day's outing. ``leg_graph`` joins the legs' main walks at their
signposts. ``starts`` are signposts near parking or a place, one per place.
``candidates`` lists every day-sized chain from a start. ``select`` keeps a
few distinct ones per start, loops first, and drops Twins of each other and
of relation DayHikes. ``chain_name`` names them from signposts, destinations
and places. ``network_hikes`` runs all of it, pure: ingest wiring is apart.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from sklearn.neighbors import BallTree

from dayhike.catalog import (
    EARTH_RADIUS_M,
    GAP_JOIN_M,
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

NETWORK_MIN_M = 3_000.0  # a NetworkHike is day-sized: at least this long ...
NETWORK_MAX_M = 25_000.0  # ... and at most this
NETWORK_EXPANSIONS = 20_000  # legs one start's search may try
NETWORK_CAP = 4  # chains kept per start
NETWORK_SHORTLIST = 2 * NETWORK_CAP  # distinct chains per start checked for Twins
START_PARKING_M = 500.0  # a signpost this close to parking starts chains ...
START_PLACE_M = 300.0  # ... and one this close to a place node
START_CLUSTER_M = 1_000.0  # signposts this close to a start's first one start with it
DESTINATION_M = 200.0  # a line whose end is this close to a named Peak, Hut, Viewpoint or lake goes there
NETWORK_TARGET_M = 10_000.0  # a day's walk: of two chains alike otherwise, the one nearer this length is better
GUIDEPOST_M = 50.0  # a named guidepost this close names a signpost its legs do not


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


@dataclass(frozen=True)
class NetworkHike:
    """A kept chain as the Catalog takes it: its name, (node id, point)
    vertices from its start, member ways walked and leg relation ids
    (``parts``) in walking order, and its length."""

    name: str
    vertices: tuple[tuple[int, Point], ...]
    way_ids: tuple[int, ...]
    parts: tuple[int, ...]
    length_m: float


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
    Twins are left out.
    """
    graph = leg_graph(legs, ways, nodes)
    found = starts(graph, access_points, places)
    kept = select(graph, [candidates(graph, start) for start in found], destinations, relations)
    names = signpost_names(graph, guideposts)
    marked, settled = named_points(destinations), named_points(places)
    hikes = []
    for _, chain in kept:
        vertices = walk(graph, chain)
        way_ids = []
        for index, at in zip(chain.legs, chain.signposts):
            leg = graph.legs[index]
            way_ids += leg.way_ids if leg.ends[0] == at else leg.way_ids[::-1]
        hikes.append(
            NetworkHike(
                chain_name(graph, chain, names, marked, settled),
                tuple(vertices),
                tuple(way_ids),
                tuple(graph.legs[index].relation_id for index in chain.legs),
                _linestring_length_m([point for _, point in vertices]),
            )
        )
    return hikes


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
    for signpost, is_near in enumerate(near):
        if not is_near:
            continue
        point = graph.signposts[signpost]
        for members in found:
            if _haversine_m(*point, *graph.signposts[members[0]]) <= START_CLUSTER_M:
                members.append(signpost)
                break
        else:
            found.append([signpost])
    return [Start(tuple(members), graph.signposts[members[0]]) for members in found]


def candidates(graph: LegGraph, start: Start) -> list[Chain]:
    """Chains of at least two legs from ``start``'s signposts, ``NETWORK_MIN_M``
    to ``NETWORK_MAX_M`` long: every line that passes no signpost twice and
    every loop back to its first signpost, each loop once. The search stops
    after ``NETWORK_EXPANSIONS`` legs tried.
    """
    incident: dict[int, list[int]] = {}
    for index, leg in enumerate(graph.legs):
        a, b = leg.ends
        if a != b:
            incident.setdefault(a, []).append(index)
            incident.setdefault(b, []).append(index)
    found: list[Chain] = []
    loops_seen: set[frozenset[int]] = set()
    budget = NETWORK_EXPANSIONS
    for first in start.signposts:
        stack: list[tuple[tuple[int, ...], tuple[int, ...], float]] = [((), (first,), 0.0)]
        while stack and budget > 0:
            legs, signposts, length_m = stack.pop()
            deeper = []
            for index in incident.get(signposts[-1], ()):
                if index in legs:
                    continue
                budget -= 1
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
                deeper.append((chain.legs, chain.signposts, walked))
            stack += reversed(deeper)  # legs in listed order, depth first
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
    per_start: Sequence[Sequence[Chain]],
    destinations: Sequence[tuple[float, float, str]] = (),
    relations: Sequence[Sequence[Point]] = (),
) -> list[tuple[int, Chain]]:
    """The chains to keep, as (start index into ``per_start``, chain), best first.

    Loops come first, then lines that end at one of the named
    ``destinations`` (lat, lon, name), then other lines; within each, nearer
    ``NETWORK_TARGET_M`` (by ratio) first. Per start, best first, a chain is
    shortlisted when more than ``VERSION_DIFFERS`` of its length lies on legs
    that no chain shortlisted there walks, up to ``NETWORK_SHORTLIST``. Of
    those, at most ``NETWORK_CAP`` per start are kept, none a Twin of a better chain kept or
    of one of the relation DayHikes' walks ``relations`` (Twins of Twins
    count, as in ``twin_groups``).
    """
    reached = named_points(destinations)
    at_destination = {
        signpost for signpost, point in enumerate(graph.signposts) if _nearest(reached, point, DESTINATION_M)
    }

    def preference(chain: Chain) -> tuple[int, float]:
        kind = 0 if chain.loop else 1 if chain.signposts[-1] in at_destination else 2
        return kind, abs(math.log(chain.length_m / NETWORK_TARGET_M))

    shortlist: list[tuple[int, Chain]] = []
    for start, listed in enumerate(per_start):
        legs: list[set[int]] = []
        for chain in sorted(listed, key=preference):
            if len(legs) >= NETWORK_SHORTLIST:
                break
            walked = set(chain.legs)
            # Chains of one network share whole legs, so the share off another is in leg metres.
            if all(
                1.0 - sum(graph.legs[leg].length_m for leg in walked & other) / chain.length_m > VERSION_DIFFERS
                for other in legs
            ):
                legs.append(walked)
                shortlist.append((start, chain))
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
    return kept


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
