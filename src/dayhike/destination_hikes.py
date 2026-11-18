"""DestinationHikes (#124, ADR-0067): "{Destination} from {start}".

A walk from a car park to a named Peak, Hut, Viewpoint or lake along every way
a hiker may walk (the walkable graph), stored one way, start to destination; a
hiker walks it there and back. ``destination_hikes`` is pure: it searches one
piece of the walkable graph from each destination out to the car parks and
chooses which walks stay. ``search_destinations`` is a stage of ingest's own,
before the snapshot loads: it reads the walkable graph from the OSM extract
itself and searches it tile by tile, so it never needs the whole graph in
memory, and stores the walks it finds in the work file. ``destination_source``
is the built-hike source (ADR-0038) that chooses from them.
"""

from __future__ import annotations

import hashlib
import json
import math
import pickle
import shutil
import sqlite3
import subprocess
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from dayhike.built import BuildContext, BuiltWalk
from dayhike.catalog import EARTH_RADIUS_M, KIND_RELATION
from dayhike.osm import OsmWay
from dayhike.parallel import parallel_iter, workers
from dayhike.similarity import TWIN_STEP_M, _even_points, climb_and_descent, twin_groups

Point = tuple[float, float]  # (lat, lon)

KIND = "destination"  # the built DayHike's kind and NameOrigin
ONE_WAY_MIN_M = 1_500.0  # there and back at least 3 km ...
ONE_WAY_MAX_M = 12_500.0  # ... and at most 25 km,
BIG_ONE_WAY_MAX_M = 16_000.0  # or 32 km to a destination this high or with a Wikidata tag
BIG_ELE_M = 2_500.0
WALKS_PER_DESTINATION = 8  # at most; the rules below do the cutting (the Écrins check)
WALKS_PER_START = 8  # a car park starts at most this many, best-known destinations first
START_GAP_M = 2_000.0  # a further walk to a destination starts this far from every kept start ...
SHARED_MAX = 0.5  # ... and shares at most this much of its length with any one of them
MERGE_M = 150.0  # destinations this close are one, the best-known of them
DESTINATION_SNAP_M = 100.0  # a destination this close to a node of the graph is reached there
START_SNAP_M = 100.0  # a car park this close to a node of the graph starts walks there
ROAD_COST = 3.0  # a metre of road counts this many in the search ...
ROAD_SHARE_MAX = 0.3  # ... and a walk more than this much on roads is not built
CLIMB_COST = 10.0  # a metre up or down counts this many metres of walking
GRADE_COST = {"demanding_mountain_hiking": 1.25, "alpine_hiking": 1.6}  # T3, T4
MARKED_COST = 0.7  # a metre on a hiking relation's way
SEARCH_COST_MAX = 4.0  # the search gives up past this many times a destination's longest walk
DESCENT_MIN_M = 200.0  # a walk that descends more than this on the way out ...
DESCENT_SHARE = 0.25  # ... and more than this share of its climb ...
DESCENT_MARKED_SHARE = 0.6  # ... and lies less than this much on one marked route crosses a col: not built
NEAR_COPY_M = 150.0  # a relation DayHike passing this close to both ends ...
NEAR_COPY_LENGTH = 2.0  # ... at most this many times the walk there and back already goes there
VIA_M = 150.0  # a dropped Twin's destination this close to a kept walk, and farther from its end, is a via
FLAT_M = 10.0  # a segment whose heights stay within this counts only its ends' difference
REACH_SLACK_M = 1.0  # float rounding: the reach test (``_in_reach``) never drops a destination a walk reaches
ROAD_HIGHWAYS = frozenset({"residential", "unclassified", "tertiary", "service", "living_street"})
HARD_SAC = frozenset({"demanding_alpine_hiking", "difficult_alpine_hiking"})  # T5, T6


@dataclass(frozen=True)
class Destination:
    """A named Peak, Hut, Viewpoint or lake: ``points`` is its point, or a lake's
    outline; ``ele`` its height, ``wikidata`` and ``wikipedia`` whether it has such a
    tag; ``others`` the points of the destinations merged into it, ``other_names`` their names."""

    name: str
    kind: str
    points: tuple[Point, ...]
    ele: float | None = None
    wikidata: bool = False
    wikipedia: bool = False
    others: tuple[Point, ...] = ()
    other_names: tuple[str, ...] = ()

    @property
    def notability(self) -> tuple[int, float]:
        """Of two walks that are Twins, the one to the higher stays: a Wikidata
        tag, then a hut, then a lake, a viewpoint or a peak of 2,000 m, then the
        rest; height breaks ties."""
        if self.wikidata:
            tier = 3
        elif self.kind == "hut":
            tier = 2
        elif self.kind in ("lake", "viewpoint") or (self.ele or 0.0) >= 2_000:
            tier = 1
        else:
            tier = 0
        return tier, self.ele or 0.0

    @property
    def one_way_max_m(self) -> float:
        return BIG_ONE_WAY_MAX_M if self.wikidata or (self.ele or 0.0) >= BIG_ELE_M else ONE_WAY_MAX_M


@dataclass(frozen=True)
class Start:
    """A car park: its name for the walk's label (None: it starts no walk, but
    no walk passes it either) and its point, or its area's outline; ``hut`` the
    hut it is named after, and ``fallback`` its name for a walk to that hut."""

    name: str | None
    points: tuple[Point, ...]
    hut: str | None = None
    fallback: str | None = None


@dataclass(frozen=True)
class RelationWalk:
    """A Catalog hike a walk may copy: its length and its walk."""

    length_m: float
    points: tuple[Point, ...]


@dataclass(frozen=True, eq=False)
class DestinationHike:
    """One walk, start to destination: ``length_m``, ``climb_m`` and ``descent_m``
    one way; ``rank`` among its destination's walks, best first; ``twin`` why it
    is not kept (``relation``, ``built``, ``near-copy``, ``walk``, ``start cap``) or None."""

    name: str
    destination: Destination
    start: Start
    node_ids: np.ndarray  # its vertices from the start, as arrays: a Europe build holds some 300k walks
    points: np.ndarray  # (lat, lon) rows
    way_ids: tuple[int, ...]
    length_m: float
    climb_m: float
    descent_m: float
    cost: float
    rank: int
    twin: str | None = None
    via_way: str | None = None  # the named way it walks longest, to tell two of one name apart

    @property
    def vertices(self) -> tuple[tuple[int, Point], ...]:
        return tuple((int(node), (float(lat), float(lon))) for node, (lat, lon) in zip(self.node_ids, self.points))


def destination_hikes(
    ways: Mapping[int, OsmWay],
    nodes: Mapping[int, Point],
    destinations: Sequence[Destination],
    starts: Sequence[Start],
    heights: Mapping[int, float] | None = None,
    marked: Mapping[int, Sequence[int]] | None = None,
    relations: Sequence[RelationWalk] = (),
    built: Sequence[Sequence[Point]] = (),
) -> list[DestinationHike]:
    """Every destination's walks on the graph of ``ways``, those not kept with
    their ``twin`` reason. ``marked`` gives the hiking relations each way is in;
    ``relations`` and ``built`` are the Catalog hikes a walk may copy."""
    return choose(walks(ways, nodes, merged(destinations), starts, heights, marked), relations, built)


def merged(destinations: Sequence[Destination]) -> list[Destination]:
    """``destinations`` with those whose points lie within ``MERGE_M`` of each
    other as one: named and ranked as the best-known of them, with the others'
    points as its ``others``, so it is reached at its own point, else where any
    of them meets the graph."""
    if len(destinations) < 2:
        return list(destinations)
    from sklearn.neighbors import BallTree

    owner = np.concatenate([np.full(len(destination.points), i) for i, destination in enumerate(destinations)])
    points = np.radians(np.concatenate([np.asarray(destination.points, dtype=float) for destination in destinations]))
    parent = list(range(len(destinations)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    near = BallTree(points, metric="haversine").query_radius(points, r=MERGE_M / EARTH_RADIUS_M)
    for i, hits in zip(owner.tolist(), near):
        for j in set(owner[hits].tolist()):
            parent[root(j)] = root(i)
    groups: dict[int, list[Destination]] = {}
    for i, destination in enumerate(destinations):
        groups.setdefault(root(i), []).append(destination)
    result = []
    for group in groups.values():
        best = max(group, key=lambda destination: (destination.notability, destination.name, destination.points[0]))
        rest = [destination for destination in group if destination is not best]
        others = tuple(point for destination in rest for point in destination.points)
        names = tuple(destination.name for destination in rest)
        result.append(replace(best, others=others, other_names=names) if rest else best)
    return result


def walks(
    ways: Mapping[int, OsmWay],
    nodes: Mapping[int, Point],
    destinations: Sequence[Destination],
    starts: Sequence[Start],
    heights: Mapping[int, float] | None = None,
    marked: Mapping[int, Sequence[int]] | None = None,
) -> list[DestinationHike]:
    """Each destination's walks, best first, before Twins: from each destination
    the search goes out to the car parks, never past one, weighing metres by the
    way (roads, grade, marked route) and climb; a car park's cheapest walk is kept
    when it passes the rules of length, roads, another side and descent."""
    in_reach = _in_reach(destinations, starts)
    if not any(in_reach):
        return []
    graph = _Graph(ways, nodes, heights or {}, marked or {})
    if not graph.size:
        return []
    start_at: dict[int, Start] = {}
    for start in sorted(starts, key=lambda start: start.name is None):  # a named car park names its node
        node = graph.snap(start.points, START_SNAP_M)
        if node is not None:
            start_at.setdefault(node, start)
    reached: dict[int, list[Destination]] = {}
    for destination in destinations:
        node = graph.snap(destination.points, DESTINATION_SNAP_M)
        if node is None and destination.others:
            node = graph.snap(destination.others, DESTINATION_SNAP_M)
        if node is not None:
            reached.setdefault(node, []).append(destination)
    if not start_at or not reached:
        return []
    # A destination out of reach is not searched, but still ends a segment: segment costs
    # count climb over the whole segment, so it splits the graph as it always did.
    graph.contract(set(start_at) | set(reached), set(start_at))
    searched = {id(destination) for destination, near in zip(destinations, in_reach) if near}
    found: list[DestinationHike] = []
    for node, here in reached.items():
        for destination in here:
            if id(destination) not in searched:
                continue
            candidates = graph.search(node, SEARCH_COST_MAX * destination.one_way_max_m)
            found += _chosen_walks(graph, destination, candidates, start_at)
    return found


def _in_reach(destinations: Sequence[Destination], starts: Sequence[Start]) -> list[bool]:
    """Whether each destination has a car park within its longest walk and both snaps, in a
    straight line from any of its points or those merged into it to any point of the car park.
    A walk is never shorter than that line less the snaps, so a destination with none has no walk."""
    if not destinations or not starts:
        return [False] * len(destinations)
    from sklearn.neighbors import BallTree

    tree = BallTree(np.radians([point for start in starts for point in start.points]), metric="haversine")
    points = [[*destination.points, *destination.others] for destination in destinations]
    distance, _ = tree.query(np.radians([point for here in points for point in here]), k=1)
    first = np.cumsum([0, *(len(here) for here in points[:-1])])
    nearest = np.minimum.reduceat(distance[:, 0], first) * EARTH_RADIUS_M
    return [
        bool(metres <= destination.one_way_max_m + START_SNAP_M + DESTINATION_SNAP_M + REACH_SLACK_M)
        for metres, destination in zip(nearest.tolist(), destinations)
    ]


def _chosen_walks(graph: _Graph, destination: Destination, candidates, start_at) -> list[DestinationHike]:
    kept: list[tuple[set[int], DestinationHike]] = []
    for cost, start_node, path, edges in candidates:
        if len(kept) == WALKS_PER_DESTINATION:
            break
        start = start_at[start_node]
        if start.hut in (destination.name, *destination.other_names):
            start = replace(start, name=start.fallback, hut=None)
        metres = float(graph.metres[edges].sum())
        if start.name is None or not ONE_WAY_MIN_M <= metres <= destination.one_way_max_m:
            continue
        if graph.metres[edges][graph.road[edges]].sum() > ROAD_SHARE_MAX * metres:
            continue
        point = graph.point(start_node)
        if any(_metres(*point, *other.points[0]) < START_GAP_M for _, other in kept):
            continue
        own = set(edges.tolist())
        if any(graph.metres[list(own & other)].sum() > SHARED_MAX * metres for other, _ in kept):
            continue
        climb, descent = graph.climb(path)
        if descent > DESCENT_MIN_M and descent > DESCENT_SHARE * climb and graph.marked_share(edges) < DESCENT_MARKED_SHARE:
            continue
        hike = DestinationHike(
            f"{destination.name} from {start.name}",
            destination,
            start,
            graph.ids[path],
            graph.coords[path],
            tuple(dict.fromkeys(int(graph.way_of[e]) for e in edges)),
            metres,
            climb,
            descent,
            float(cost),
            len(kept) + 1,
            via_way=graph.longest_named(edges),
        )
        kept.append((own, hike))
    return [hike for _, hike in kept]


def choose(
    hikes: Sequence[DestinationHike], relations: Sequence[RelationWalk] = (), built: Sequence[Sequence[Point]] = ()
) -> list[DestinationHike]:
    """``hikes`` with ``twin`` set on those not kept: a Twin of a relation hike or
    of an earlier source's hike; a near-copy (a relation DayHike at most twice
    the walk there and back passes both its ends); a Twin of a walk to a
    better-known or farther destination; past a car park's ``WALKS_PER_START``.
    A kept walk that a Twin to a destination on its way lost to is named via it."""
    hikes = sorted(hikes, key=lambda hike: (*(-x for x in hike.destination.notability), -hike.length_m, hike.name))
    if not hikes:
        return []
    given = [_route(points) for points in [*(relation.points for relation in relations), *built] if len(points) >= 2]
    routes = [_route(hike.points) for hike in hikes]
    groups = twin_groups([*given, *routes])[len(given) :]
    first: dict[int, int] = {}
    twin: list[str | None] = []
    for i, group in enumerate(groups):
        group = int(group)
        if group < len(given):
            twin.append("relation" if group < len(relations) else "built")
        elif group in first:
            twin.append("walk")
        else:
            first[group] = i
            twin.append(None)
    for i in _near_copies(hikes, relations, twin):
        twin[i] = "near-copy"
    by_start: dict[Point, list[int]] = {}
    for i, hike in enumerate(hikes):
        if twin[i] is None:
            by_start.setdefault(hike.start.points[0], []).append(i)
    for found in by_start.values():
        found.sort(key=lambda i: (*(-x for x in hikes[i].destination.notability), hikes[i].cost))
        for i in found[WALKS_PER_START:]:
            twin[i] = "start cap"
    names = [hike.name for hike in hikes]
    for i, hike in enumerate(hikes):
        if twin[i] != "walk":
            continue
        best = first[int(groups[i])]
        lost = hike.destination
        if twin[best] is None and lost.name != hikes[best].destination.name and _on_the_way(lost.points, routes[best]):
            if " via " not in names[best]:
                names[best] = f"{hikes[best].destination.name} via {lost.name} from {hikes[best].start.name}"
    counts = Counter(name for i, name in enumerate(names) if twin[i] is None)
    for i, hike in enumerate(hikes):
        if twin[i] is None and counts[names[i]] > 1 and hike.via_way:
            names[i] = f"{names[i]} via {hike.via_way}"
    return [replace(hike, name=names[i], twin=twin[i]) for i, hike in enumerate(hikes)]


def _near_copies(hikes: Sequence[DestinationHike], relations: Sequence[RelationWalk], twin: list[str | None]):
    """The rows of kept walks that a relation DayHike of at most ``NEAR_COPY_LENGTH``
    times their there-and-back length passes within ``NEAR_COPY_M`` of both ends."""
    if not relations:
        return []
    from sklearn.neighbors import BallTree

    routes = [_route(relation.points) for relation in relations if len(relation.points) >= 2]
    lengths = [relation.length_m for relation in relations if len(relation.points) >= 2]
    if not routes:
        return []
    owner = np.concatenate([np.full(len(route), i) for i, route in enumerate(routes)])
    tree = BallTree(np.radians(np.concatenate(routes)), metric="haversine")
    copies = []
    for i, hike in enumerate(hikes):
        if twin[i] is not None:
            continue
        ends = np.radians(hike.points[[0, -1]])
        start, end = ({int(owner[j]) for j in hits} for hits in tree.query_radius(ends, r=NEAR_COPY_M / EARTH_RADIUS_M))
        if any(lengths[j] <= NEAR_COPY_LENGTH * 2 * hike.length_m for j in start & end):
            copies.append(i)
    return copies


def _on_the_way(points: Sequence[Point], line: np.ndarray) -> bool:
    """Whether a point of ``points`` lies within ``VIA_M`` of ``line`` but not of its end."""
    lat, lon = np.radians(line).T
    for p_lat, p_lon in np.radians(points):
        apart = _haversine(p_lat, p_lon, lat, lon)
        if apart.min() <= VIA_M < apart[-1]:
            return True
    return False


def _route(points: Sequence[Point]) -> np.ndarray:
    points = np.asarray(points, dtype=float)
    return _even_points(points[:, 0], points[:, 1], TWIN_STEP_M)


def _haversine(lat1, lon1, lat2, lon2):
    """Metres between points given in radians, element by element."""
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.minimum(1.0, a)))


def _metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return float(_haversine(*np.radians([lat1, lon1, lat2, lon2])))


class _Graph:
    """The walkable graph of some ways, node by node in arrays: each edge its
    metres, way, whether it is road and what a metre of it counts. ``contract``
    joins it into segments between junctions, ``search`` finds walks on them."""

    def __init__(
        self,
        ways: Mapping[int, OsmWay],
        nodes: Mapping[int, Point],
        heights: Mapping[int, float],
        marked: Mapping[int, Sequence[int]],
    ) -> None:
        a, b, way_of, factor, road = [], [], [], [], []
        self.names: dict[int, str] = {}
        for way_id, way in ways.items():
            tags = way.tags
            # A via ferrata is its own highway; a T4 path with a few cables (``via_ferrata_scale``,
            # Triglav's summit ridge) is walked like any T4 path.
            if tags.get("sac_scale") in HARD_SAC or tags.get("highway") == "via_ferrata":
                continue
            is_road = tags.get("highway") in ROAD_HIGHWAYS
            cost = ROAD_COST if is_road else GRADE_COST.get(tags.get("sac_scale", ""), 1.0)
            if marked.get(way_id):
                cost *= MARKED_COST
            if tags.get("name"):
                self.names[way_id] = tags["name"]
            on_way = [node for node in way.node_ids if node in nodes]
            for here, there in zip(on_way, on_way[1:]):
                if here != there:
                    a.append(here)
                    b.append(there)
                    way_of.append(way_id)
                    factor.append(cost)
                    road.append(is_road)
        self.size = len(a)
        if not self.size:
            return
        self.marked = marked
        self.ids = np.unique(np.array(a + b, dtype=np.int64))
        ai = np.searchsorted(self.ids, np.array(a, dtype=np.int64))
        bi = np.searchsorted(self.ids, np.array(b, dtype=np.int64))
        self.coords = np.array([nodes[int(node)] for node in self.ids], dtype=float)
        factor_arr = np.array(factor)
        # One edge per pair of nodes, the cheapest of the ways that join them.
        low, high = np.minimum(ai, bi), np.maximum(ai, bi)
        order = np.lexsort((factor_arr, high, low))
        pairs = low[order] * len(self.ids) + high[order]
        keep = order[np.concatenate([[True], pairs[1:] != pairs[:-1]])]
        self.a, self.b = ai[keep], bi[keep]
        self.way_of = np.array(way_of, dtype=np.int64)[keep]
        self.factor = factor_arr[keep]
        self.road = np.array(road, dtype=bool)[keep]
        lat, lon = np.radians(self.coords).T
        self.metres = _haversine(lat[self.a], lon[self.a], lat[self.b], lon[self.b])
        self.heights = np.array([heights.get(int(node), np.nan) for node in self.ids], dtype=float)
        self._tree = None

    def point(self, index: int) -> Point:
        lat, lon = self.coords[index]
        return (float(lat), float(lon))

    def snap(self, points: Sequence[Point], radius_m: float) -> int | None:
        """The node nearest any of ``points``, when within ``radius_m``."""
        if self._tree is None:
            from sklearn.neighbors import BallTree

            self._tree = BallTree(np.radians(self.coords), metric="haversine")
        distance, index = self._tree.query(np.radians(points), k=1)
        best = int(distance[:, 0].argmin())
        return int(index[best, 0]) if float(distance[best, 0]) * EARTH_RADIUS_M <= radius_m else None

    def contract(self, anchors: set[int], starts: set[int]) -> None:
        """Segments between junctions (a node with other than two neighbours, or
        one of ``anchors``), and the search graph over them: a car park among
        ``starts`` is a dead end, reached at a sink of its own, so no walk passes it."""
        from scipy.sparse import csr_matrix

        count = len(self.ids)
        src = np.concatenate([self.a, self.b])
        dst = np.concatenate([self.b, self.a])
        edge = np.concatenate([np.arange(len(self.a))] * 2)
        order = np.argsort(src, kind="stable")
        dst, edge = dst[order].tolist(), edge[order].tolist()
        indptr = np.searchsorted(src[order], np.arange(count + 1)).tolist()
        junction = (np.diff(indptr) != 2).tolist()
        for node in anchors:
            junction[node] = True
        used = bytearray(len(self.a))
        self.segments: list[tuple[list[int], list[int]]] = []  # nodes, edges
        ends, costs = [], []
        factor = self.factor * self.metres
        for first in (node for node in range(count) if junction[node]):
            for k in range(indptr[first], indptr[first + 1]):
                if used[edge[k]]:
                    continue
                path, edges = [first, dst[k]], [edge[k]]
                used[edge[k]] = 1
                while not junction[path[-1]]:
                    k0 = indptr[path[-1]]
                    k1 = k0 + 1 if edge[k0] == edges[-1] else k0
                    used[edge[k1]] = 1
                    edges.append(edge[k1])
                    path.append(dst[k1])
                if path[-1] == first:
                    continue  # a loop back to its junction: never on a shortest walk
                up, down = self.climb(path)
                ends.append((first, path[-1]))
                costs.append(float(factor[edges].sum()) + CLIMB_COST * (up + down))
                self.segments.append((path, edges))
        junctions = np.flatnonzero(junction)
        self.junction_of = {int(node): i for i, node in enumerate(junctions)}
        self.node_of = junctions.tolist()
        sink = {node: len(junctions) + i for i, node in enumerate(sorted(starts))}
        self.start_of_sink = {index: node for node, index in sink.items()}
        self.sinks = np.array(sorted(self.start_of_sink), dtype=np.int64)
        size = len(junctions) + len(sink)
        u = np.array([self.junction_of[s] for s, _ in ends] + [self.junction_of[t] for _, t in ends], dtype=np.int64)
        v_nodes = [t for _, t in ends] + [s for s, _ in ends]
        v = np.array([sink.get(node, self.junction_of[node]) for node in v_nodes], dtype=np.int64)
        cost = np.maximum(np.array(costs + costs), 1e-6)
        segment = np.concatenate([np.arange(len(ends))] * 2)
        forward = np.concatenate([np.ones(len(ends), bool), np.zeros(len(ends), bool)])
        order = np.lexsort((cost, v, u))  # the cheapest of parallel segments first
        u, v, cost, segment, forward = u[order], v[order], cost[order], segment[order], forward[order]
        first_of_pair = np.concatenate([[True], (u[1:] != u[:-1]) | (v[1:] != v[:-1])]) if len(u) else np.array([], bool)
        u, v, cost = u[first_of_pair], v[first_of_pair], cost[first_of_pair]
        self.arc_segment, self.arc_forward = segment[first_of_pair], forward[first_of_pair]
        self.arc_ptr = np.searchsorted(u, np.arange(size + 1))
        self.arc_of = dict(zip(zip(u.tolist(), v.tolist()), range(len(u))))  # (from, to): its arc
        self.search_graph = csr_matrix((cost, v, self.arc_ptr), shape=(size, size))

    def search(self, node: int, limit: float):
        """From the destination at ``node``, each car park reached, cheapest first:
        (cost, its node, the nodes from it to the destination, the edges), each
        walk traced back only when it is asked for."""
        from scipy.sparse.csgraph import dijkstra

        source = self.junction_of[node]
        cost, before = dijkstra(self.search_graph, indices=source, return_predecessors=True, limit=limit)
        sinks = self.sinks[np.isfinite(cost[self.sinks])]
        for sink in sinks[np.argsort(cost[sinks], kind="stable")].tolist():
            start = self.start_of_sink[sink]
            if start != node:
                yield (float(cost[sink]), start, *self._traced(source, sink, before))

    def _traced(self, source: int, sink: int, before: np.ndarray) -> tuple[list[int], np.ndarray]:
        """The nodes and edges of the walk from the car park at ``sink`` to ``source``."""
        nodes: list[list[int]] = []
        edges: list[list[int]] = []
        here = sink
        while here != source:  # back from the car park, arc by arc, each walked away from the destination
            back = int(before[here])
            arc = self.arc_of[(back, here)]
            on, steps = self.segments[int(self.arc_segment[arc])]
            if self.arc_forward[arc]:
                on, steps = on[::-1], steps[::-1]
            nodes.append(on[:-1])
            edges.append(steps)
            here = back
        path = [node for piece in nodes for node in piece] + [self.node_of[source]]
        return path, np.array([edge for piece in edges for edge in piece], dtype=np.int64)

    def climb(self, path: Sequence[int]) -> tuple[float, float]:
        """Metres up and down along ``path``, the Catalog's count (spikes dropped,
        a dead-band); a stretch whose heights stay within ``FLAT_M`` only its ends' difference."""
        heights = self.heights[list(path)]
        known = heights[np.isfinite(heights)]
        if len(known) < 2:
            return 0.0, 0.0
        if known.max() - known.min() < FLAT_M:
            rise = float(known[-1] - known[0])
            return max(rise, 0.0), max(-rise, 0.0)
        lat, lon = self.coords[list(path)].T
        up, down = climb_and_descent(lat, lon, heights)
        return float(up), float(down)

    def marked_share(self, edges: np.ndarray) -> float:
        """The largest share of the walk on one hiking relation's ways."""
        on: Counter = Counter()
        for e in edges.tolist():
            for relation in self.marked.get(int(self.way_of[e]), ()):
                on[relation] += float(self.metres[e])
        total = float(self.metres[edges].sum())
        return max(on.values()) / total if on and total else 0.0

    def longest_named(self, edges: np.ndarray) -> str | None:
        on: Counter = Counter()
        for e in edges.tolist():
            name = self.names.get(int(self.way_of[e]))
            if name:
                on[name] += float(self.metres[e])
        return on.most_common(1)[0][0] if on else None


# The source: what it reads of the OSM file, tile by tile.

WALK_HIGHWAYS = (
    "path", "footway", "track", "bridleway", "steps", "pedestrian",
    "unclassified", "residential", "service", "tertiary", "living_street",
)  # fmt: skip
HUT_TOURISM = ("alpine_hut", "wilderness_hut")
CLOSED_HUT_KEYS = ("disused:tourism", "abandoned:tourism")
LAKE_WATER = ("lake", "reservoir", "pond")
# The extract this source reads, beside the catalog extract: every walkable way, the car
# parks, the destinations with their tags (closed huts too), lakes and what names a start.
WALK_TAG_FILTERS = (
    "w/highway=" + ",".join(WALK_HIGHWAYS),
    "nw/amenity=parking",
    "n/natural=peak,saddle",
    "nw/tourism=alpine_hut,wilderness_hut,viewpoint",
    "nw/disused:tourism=alpine_hut,wilderness_hut",
    "nw/abandoned:tourism=alpine_hut,wilderness_hut",
    "wr/water=" + ",".join(LAKE_WATER),
    "n/place=city,town,village,hamlet,locality",
    "n/information=guidepost",
    "n/mountain_pass=yes",
)
PEAK_MIN_ELE_M = 1_000.0  # a peak with no Wikidata or Wikipedia tag counts from this height
CELL_DEG = 0.25  # the way store finds ways by cells of this many degrees ...
TILE_DEG = 1.0  # ... and the search runs on tiles of this many, with a margin of the longest walk
MARGIN_M = BIG_ONE_WAY_MAX_M + 1_000.0
TOWN_M = 1_000.0  # a car park without a name takes a town or village this close ...
HAMLET_M = 1_000.0  # ... else a hamlet or locality, else a hut, this close ...
FAR_TOWN_M = 3_000.0  # ... else a town or village this close ...
LANDMARK_M = 300.0  # ... else a named guidepost, saddle or pass this close; else it starts no walk
TOWNS = ("city", "town", "village")
GENERIC_PARKING = frozenset({"parking", "parkplatz", "parkirišče", "parcheggio", "car park", "p", "parking lot"})
LARGE_EXTRACT_BYTES = 2 * 1024**3  # a larger extract keeps node locations in a file, not in memory


DEST_WORKERS_ENV = "DAYHIKE_DEST_WORKERS"  # the tile search's workers; DAYHIKE_WORKERS's count when unset


SEARCH_VERSION = 1  # part of the search's key: change it when a change of code changes what the search stores


def search_destinations(work_path: Path, source_paths: Sequence[Path], dem_dir: Path | None, progress=None) -> None:
    """Every walk to a destination in the OSM files ``source_paths``, tile by tile, into
    ingest's work file ``work_path`` for ``destination_source``. Ingest runs it after its
    passes, before the snapshot loads, so the tile search's ``DAYHIKE_DEST_WORKERS`` workers
    fork from a small process. What it reads (``walkable_ways``, ``walkable_cells`` and the
    destinations and car parks, ``destination_search``) and each tile's walks as the tile
    is done (``destination_tiles``) stay in the work file, so a run stopped part way and
    resumed reads nothing again and searches only the tiles not done, while its key (the
    walk extracts, the cells read, the DEM folder, ``SEARCH_VERSION``) is unchanged."""
    with closing(sqlite3.connect(work_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS destination_search (key TEXT NOT NULL, destinations BLOB NOT NULL, starts BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS destination_tiles (row INTEGER, col INTEGER, walks BLOB NOT NULL, PRIMARY KEY (row, col));
            """
        )
        extracts = [_walk_extract(path, progress) for path in source_paths]
        cells = _wanted_cells(conn)
        search_key = json.dumps({
            "version": SEARCH_VERSION,
            "extracts": [[str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns, located] for path, located in extracts],
            "cells": hashlib.sha256(np.array(sorted(cells), dtype=np.int64).tobytes()).hexdigest(),
            "dem": str(dem_dir),
        })  # fmt: skip
        stored = conn.execute("SELECT key, destinations, starts FROM destination_search").fetchone()
        if stored is not None and stored[0] == search_key:
            destinations, starts = pickle.loads(stored[1]), pickle.loads(stored[2])
        else:
            conn.executescript(
                """
                DELETE FROM destination_search;
                DELETE FROM destination_tiles;
                DROP TABLE IF EXISTS walkable_ways;
                DROP TABLE IF EXISTS walkable_cells;
                """
            )
            found = _Read()
            with tempfile.TemporaryDirectory(prefix="dayhike-destinations-") as work:
                for extract, located in extracts:
                    _read_walkable(extract, work_path, cells, found, Path(work), located)
            destinations = found.destinations(dem_dir)
            starts = found.starts()
            del found  # the car parks' tags: not for the workers to inherit
            conn.execute(
                "INSERT INTO destination_search VALUES (?, ?, ?)", (search_key, pickle.dumps(destinations), pickle.dumps(starts))
            )
            conn.commit()
        tiles: dict[tuple[int, int], list[Destination]] = {}
        for destination in destinations:
            tiles.setdefault(_tile(destination.points[0]), []).append(destination)
        done = set(conn.execute("SELECT row, col FROM destination_tiles").fetchall())
        left = sorted(set(tiles) - done)
        if progress is not None:
            progress(
                f"destinations: {len(destinations)} destinations and {len(starts)} car parks read; "
                f"searching {len(left)} tiles of {len(tiles)}"
            )
        by_tile = _starts_by_tile(starts, left)
        task = _TileTask(work_path, _marked(conn), dem_dir, progress)
        items = [(key, [*tiles[key], *_near_edge(tiles, key)], by_tile.get(key, [])) for key in left]
        count = 0
        # Each tile's walks go to the work file as it is done, not held here.
        for (tile, _, _), found_here in zip(items, parallel_iter(task, items, workers(DEST_WORKERS_ENV))):
            conn.execute("INSERT INTO destination_tiles VALUES (?, ?, ?)", (*tile, pickle.dumps(found_here)))
            conn.commit()
            count += len(found_here)
    if progress is not None:
        progress(f"destinations: {count} walks found on {len(left)} tiles")


def destination_source(context: BuildContext) -> list[BuiltWalk]:
    """The DestinationHikes of the OSM files ingest read (``context.source_paths``): of the
    walks ``search_destinations`` stored in the work file, those that are no Twin."""
    if not context.source_paths:
        return []
    with closing(sqlite3.connect(context.work_path)) as conn:
        hikes = [
            hike
            for (walks_here,) in conn.execute("SELECT walks FROM destination_tiles ORDER BY row, col")
            for hike in pickle.loads(walks_here)
        ]
        hikes = choose(hikes, *_copied(context, hikes))
        kept = [hike for hike in hikes if hike.twin is None]
        if context.progress is not None:
            context.progress(f"assemble: destination hikes: {len(kept)} kept of {len(hikes)} walks")
        extra = _ways(conn, {way_id for hike in kept for way_id in hike.way_ids} - set(context.snapshot.ways))
    built = []
    for hike in kept:
        ways = {way_id: extra[way_id][0] for way_id in hike.way_ids if way_id in extra}
        nodes = {node: point for way_id in ways for node, point in extra[way_id][1].items()}
        built.append(BuiltWalk(KIND, hike.name, hike.vertices, hike.way_ids, (), ways, nodes))
    return built


class _TileTask:
    """One tile's walks, in a worker of its own: its destinations merged (with those
    of the tiles beside it near its edge, keeping the merged ones whose best-known
    destination is its own), and unless no car park of the tile is within reach of
    them, the ways within reach of them from the store, their heights from the DEM,
    and the search."""

    def __init__(self, store: Path, marked, dem_dir: Path | None, progress=None) -> None:
        self.store, self.marked, self.dem_dir, self.progress = store, marked, dem_dir, progress

    def __call__(self, item: tuple[tuple[int, int], list[Destination], list[Start]]) -> list[DestinationHike]:
        (row, col), destinations, starts = item
        destinations = [destination for destination in merged(destinations) if _tile(destination.points[0]) == (row, col)]
        far = len(destinations) - sum(_in_reach(destinations, starts))
        if far == len(destinations):
            if self.progress is not None:
                self.progress(
                    f"destinations: tile {row} N {col} E: none of {len(destinations)} destinations "
                    "within reach of a car park"
                )
            return []
        south, west, north, east = _with_margin(row * TILE_DEG, col * TILE_DEG, (row + 1) * TILE_DEG, (col + 1) * TILE_DEG)
        with closing(sqlite3.connect(self.store)) as conn:
            cells = [_cell(lat, lon) for lat in _steps(south, north) for lon in _steps(west, east)]
            ways, nodes = {}, {}
            for chunk in range(0, len(cells), 500):
                part = cells[chunk : chunk + 500]
                for way_id, ids, lats, lons, tags in conn.execute(
                    "SELECT id, node_ids, lats, lons, tags FROM walkable_ways WHERE id IN "
                    f"(SELECT way FROM walkable_cells WHERE cell IN ({','.join('?' * len(part))}))",
                    part,
                ):
                    if way_id in ways:
                        continue
                    node_ids = np.frombuffer(ids, dtype=np.int64)
                    ways[way_id] = OsmWay(tuple(node_ids.tolist()), json.loads(tags))
                    nodes.update(zip(node_ids.tolist(), zip(np.frombuffer(lats).tolist(), np.frombuffer(lons).tolist())))
        heights: dict[int, float] = {}
        if self.dem_dir is not None and nodes:
            from dayhike.dem import sample_elevations

            ids = list(nodes)
            heights = {node: h for node, h in zip(ids, sample_elevations(self.dem_dir, [nodes[n] for n in ids])) if h is not None}
        found = walks(ways, nodes, destinations, starts, heights, self.marked)
        if self.progress is not None:
            self.progress(
                f"destinations: tile {row} N {col} E: {len(found)} walks to {len(destinations)} "
                f"destinations ({far} out of reach of a car park) on {len(ways)} ways"
            )
        return found


def _starts_by_tile(starts: Sequence[Start], keys) -> dict[tuple[int, int], list[Start]]:
    """The car parks of each tile of ``keys``, in their order: those with a point in the
    tile or its margin (``_with_margin``). Each car park is tested against the tiles
    near it, not each tile against every car park."""
    boxes = {
        (row, col): _with_margin(row * TILE_DEG, col * TILE_DEG, (row + 1) * TILE_DEG, (col + 1) * TILE_DEG)
        for row, col in keys
    }
    lon_reach = {row: col * TILE_DEG - west for (row, col), (_, west, _, _) in boxes.items()}  # a row's margin
    _, _, lat_reach, _ = _with_margin(0.0, 0.0, 0.0, 0.0)
    by_tile: dict[tuple[int, int], list[Start]] = {}
    for start in starts:
        lats, lons = [lat for lat, _ in start.points], [lon for _, lon in start.points]
        low, high = min(lats) - lat_reach, max(lats) + lat_reach
        for row in range(math.floor(low / TILE_DEG) - 1, math.floor(high / TILE_DEG) + 2):
            if row not in lon_reach:
                continue
            west, east = min(lons) - lon_reach[row], max(lons) + lon_reach[row]
            for col in range(math.floor(west / TILE_DEG) - 1, math.floor(east / TILE_DEG) + 2):
                box = boxes.get((row, col))
                if box is None:
                    continue
                south, west_edge, north, east_edge = box
                if any(south <= lat <= north and west_edge <= lon <= east_edge for lat, lon in start.points):
                    by_tile.setdefault((row, col), []).append(start)
    return by_tile


def _marked(conn: sqlite3.Connection) -> dict[int, list[int]]:
    """The hiking relations each way is in, from the work file's relations, as the snapshot reads them."""
    marked: dict[int, list[int]] = {}
    for rel_id, members in conn.execute("SELECT id, members FROM relations"):
        for kind, way_id in json.loads(members):
            if kind == "way":
                marked.setdefault(way_id, []).append(rel_id)
    return marked


def _copied(context: BuildContext, hikes: Sequence[DestinationHike]) -> tuple[list[RelationWalk], list[np.ndarray]]:
    """The relation DayHikes and earlier sources' walks that a walk may be a Twin
    or near-copy of: those on a way some walk takes."""
    walked = {way_id for hike in hikes for way_id in hike.way_ids}
    relations = {relation.id: relation for relation in context.snapshot.relations}
    copied = []
    for hike in context.dayhikes:
        relation = relations.get(hike.id)
        if hike.kind != KIND_RELATION or relation is None:
            continue
        if any(kind == "way" and way_id in walked for kind, way_id in relation.members):
            vertices = context.main_walk(relation).vertices
            if len(vertices) >= 2:
                copied.append(RelationWalk(hike.length_m, tuple(point for _, point in vertices)))
    built = [np.array([point for _, point in walk.vertices]) for walk in context.built if walked.intersection(walk.way_ids)]
    return copied, [points for points in built if len(points) >= 2]


def _ways(conn: sqlite3.Connection, way_ids: set[int]) -> dict[int, tuple[OsmWay, dict[int, Point]]]:
    """The stored ways of ``way_ids`` (``walkable_ways``), each with its nodes' points."""
    found = {}
    ids = sorted(way_ids)
    for chunk in range(0, len(ids), 500):
        part = ids[chunk : chunk + 500]
        for way_id, node_ids, lats, lons, tags in conn.execute(
            f"SELECT id, node_ids, lats, lons, tags FROM walkable_ways WHERE id IN ({','.join('?' * len(part))})", part
        ):
            node_ids = np.frombuffer(node_ids, dtype=np.int64).tolist()
            points = zip(np.frombuffer(lats).tolist(), np.frombuffer(lons).tolist())
            found[way_id] = (OsmWay(tuple(node_ids), json.loads(tags)), dict(zip(node_ids, points)))
    return found


def _cell(lat: float, lon: float) -> int:
    return (math.floor(lat / CELL_DEG) + 1_000) * 10_000 + math.floor(lon / CELL_DEG) + 1_000


def _steps(low: float, high: float) -> list[float]:
    """A latitude or longitude in each cell from ``low`` to ``high``."""
    first = math.floor(low / CELL_DEG)
    return [(i + 0.5) * CELL_DEG for i in range(first, math.floor(high / CELL_DEG) + 1)]


def _tile(point: Point) -> tuple[int, int]:
    return math.floor(point[0] / TILE_DEG), math.floor(point[1] / TILE_DEG)


def _near_edge(tiles: Mapping[tuple[int, int], Sequence[Destination]], key: tuple[int, int]) -> list[Destination]:
    """The destinations of the tiles beside ``key`` with a point within ``MERGE_M`` of it."""
    row, col = key
    south, west, north, east = _with_margin(row * TILE_DEG, col * TILE_DEG, (row + 1) * TILE_DEG, (col + 1) * TILE_DEG, MERGE_M)
    return [
        destination
        for beside in ((row + dr, col + dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1))
        if beside != key
        for destination in tiles.get(beside, ())
        if any(south <= lat <= north and west <= lon <= east for lat, lon in destination.points)
    ]


def _with_margin(
    south: float, west: float, north: float, east: float, margin_m: float = MARGIN_M
) -> tuple[float, float, float, float]:
    lat_margin = margin_m / 111_195.0
    lon_margin = lat_margin / max(0.05, math.cos(math.radians(min(89.0, max(abs(south), abs(north))))))
    return south - lat_margin, west - lon_margin, north + lat_margin, east + lon_margin


def _wanted_cells(conn: sqlite3.Connection) -> set[int]:
    """The cells within reach of a destination or hut ingest read (the snapshot's ``destinations``
    and huts, from the work file's amenities): only their ways are stored."""
    from dayhike.ingest import DESTINATION_KINDS  # ingest imports this module

    points = [
        (lat, lon)
        for kind, lat, lon, name in conn.execute("SELECT kind, lat, lon, json_extract(tags, '$.name') FROM amenities")
        if (name and kind in DESTINATION_KINDS) or kind == "hut"
    ]
    return _cells_near(points)


def _cells_near(points) -> set[int]:
    """The cells within ``MARGIN_M`` of any of ``points``."""
    cells: set[int] = set()
    for lat, lon in points:
        south, west, north, east = _with_margin(lat, lon, lat, lon)
        cells.update(_cell(a, b) for a in _steps(south, north) for b in _steps(west, east))
    return cells


def _walk_extract(source: Path, progress) -> tuple[Path, bool]:
    """The source's walk extract (``WALK_TAG_FILTERS``), cut with osmium, its ways carrying
    their nodes' locations, and kept beside it like the catalog extract; and whether its ways
    carry them: the source itself, without osmium or a PBF, does not."""
    osmium_bin = shutil.which("osmium")
    name = source.name
    if osmium_bin is None or not name.lower().endswith(".pbf") or name.endswith(".catalog.osm.pbf"):
        return source, False
    stem = name[: -len(".osm.pbf")] if name.endswith(".osm.pbf") else source.stem
    cache = source.with_name(f"{stem}.walk.osm.pbf")
    filters = cache.with_name(cache.name + ".filters")
    # The last line stamps an extract whose ways carry their nodes' locations: one cut before has none.
    text = "\n".join([*WALK_TAG_FILTERS, "add-locations-to-ways"]) + "\n"
    if (
        cache.is_file()
        and cache.stat().st_mtime_ns >= source.stat().st_mtime_ns
        and filters.is_file()
        and filters.read_text() == text
    ):
        return cache, True
    if progress is not None:
        progress(f"filter: {name} -> {cache.name}")
    writing = cache.with_name(f"{stem}.walk.writing.osm.pbf")
    # osmium's node location index, in a file: Europe's is some 13 GB, which no process holds in memory.
    with tempfile.TemporaryDirectory(prefix="dayhike-walk-extract-") as index:
        try:
            cut = subprocess.Popen(
                [osmium_bin, "tags-filter", str(source.resolve()), *WALK_TAG_FILTERS, "-o", "-", "-f", "pbf", "--no-progress"],
                stdout=subprocess.PIPE,
            )
            locate = subprocess.Popen(
                [
                    osmium_bin, "add-locations-to-ways", "-", "-F", "pbf", "-o", str(writing), "--overwrite",
                    "--index-type", f"sparse_file_array,{Path(index) / 'locations.bin'}", "--ignore-missing-nodes",
                    "--no-progress",
                ],
                stdin=cut.stdout,
            )  # fmt: skip
            cut.stdout.close()  # only the second reads it, so it ends the first if it stops early
            codes = (cut.wait(), locate.wait())
            if any(codes):
                raise subprocess.CalledProcessError(max(codes), "osmium tags-filter | osmium add-locations-to-ways")
            writing.replace(cache)
            filters.write_text(text)
        finally:
            writing.unlink(missing_ok=True)
    return cache, True


class _Read:
    """The points the walk extract holds: destinations, car parks and what names a car park."""

    def __init__(self) -> None:
        self.found: list[tuple[str, str, tuple[Point, ...], dict[str, str]]] = []  # kind, name, points, tags
        self.parking: list[tuple[tuple[Point, ...], dict[str, str]]] = []
        self.names: list[tuple[str, Point, str]] = []  # kind, point, name
        self.lakes: dict[int, tuple[str, list[Point]]] = {}  # a lake relation's name and outline
        self.lake_ways: dict[int, int] = {}  # an outline way: its lake relation

    def node(self, tags: dict[str, str], point: Point) -> None:
        self._point(tags, (point,))
        name = tags.get("name")
        if not name:
            return
        if tags.get("place") in (*TOWNS, "hamlet", "locality"):
            self.names.append((tags["place"], point, name))
        elif tags.get("information") == "guidepost" or tags.get("natural") == "saddle" or tags.get("mountain_pass") == "yes":
            self.names.append(("landmark", point, name))

    def area(self, tags: dict[str, str], points: tuple[Point, ...]) -> None:
        self._point(tags, points)
        if tags.get("name") and tags.get("natural") == "water" and tags.get("water") in LAKE_WATER:
            self.found.append(("lake", tags["name"], points, tags))

    def _point(self, tags: dict[str, str], points: tuple[Point, ...]) -> None:
        if tags.get("amenity") == "parking" and tags.get("access") not in (
            "private", "no", "customers",
        ):  # fmt: skip
            self.parking.append((points, tags))
        name = tags.get("name")
        if not name:
            return
        if tags.get("natural") == "peak":
            self.found.append(("peak", name, points, tags))
        elif any(tags.get(key) in HUT_TOURISM for key in ("tourism", *CLOSED_HUT_KEYS)):
            self.found.append(("hut", name, points, tags))
        elif tags.get("tourism") == "viewpoint":
            self.found.append(("viewpoint", name, points, tags))

    def destinations(self, dem_dir: Path | None) -> list[Destination]:
        """Every named hut, viewpoint and lake, and every named peak with a Wikidata
        or Wikipedia tag or at least ``PEAK_MIN_ELE_M`` high (its ``ele`` tag, else the DEM)."""
        found = list(self.found)
        found += [("lake", name, tuple(points), {}) for name, points in self.lakes.values() if points]
        heights: list[float | None] = [_ele(tags) for _, _, _, tags in found]
        unknown = [i for i, height in enumerate(heights) if height is None and found[i][0] == "peak"]
        if dem_dir is not None and unknown:
            from dayhike.dem import sample_elevations

            for i, height in zip(unknown, sample_elevations(dem_dir, [found[i][2][0] for i in unknown])):
                heights[i] = height
        destinations = []
        for (kind, name, points, tags), ele in zip(found, heights):
            wikidata, wikipedia = bool(tags.get("wikidata")), bool(tags.get("wikipedia"))
            if kind == "peak" and not (wikidata or wikipedia) and (ele is None or ele < PEAK_MIN_ELE_M):
                continue
            if kind == "lake" and len(points) > 256:
                points = points[:: math.ceil(len(points) / 256)]
            destinations.append(Destination(name, kind, tuple(points), ele, wikidata, wikipedia))
        return destinations

    def starts(self) -> list[Start]:
        """The car parks, each named by its own name, else by a place, hut or landmark near it."""
        from sklearn.neighbors import BallTree

        trees = {}
        huts = [("hut", points[0], name) for kind, name, points, _ in self.found if kind == "hut"]
        for kinds in (TOWNS, ("hamlet", "locality"), ("hut",), ("landmark",)):
            named = [(point, name) for kind, point, name in [*self.names, *huts] if kind in kinds]
            if named:
                trees[kinds] = (BallTree(np.radians([point for point, _ in named]), metric="haversine"), named)

        def nearest(kinds, point: Point, radius_m: float) -> str | None:
            if kinds not in trees:
                return None
            tree, named = trees[kinds]
            distance, index = tree.query(np.radians([point]), k=1)
            return named[int(index[0, 0])][1] if distance[0, 0] * EARTH_RADIUS_M <= radius_m else None

        starts = []
        for points, tags in self.parking:
            name, hut, fallback = tags.get("name"), None, None
            if not name or name.strip().lower() in GENERIC_PARKING:
                centre = tuple(np.mean(points, axis=0).tolist())
                name = nearest(TOWNS, centre, TOWN_M) or nearest(("hamlet", "locality"), centre, HAMLET_M)
                if name is None:
                    hut = nearest(("hut",), centre, HAMLET_M)
                    fallback = nearest(TOWNS, centre, FAR_TOWN_M) or nearest(("landmark",), centre, LANDMARK_M)
                    name = hut or fallback
            starts.append(Start(name, points, hut, fallback if hut else None))
        return starts


def _ele(tags: dict[str, str]) -> float | None:
    try:
        return float(tags.get("ele", "").replace(",", ".").split()[0])
    except (ValueError, IndexError):
        return None


def _read_walkable(path: Path, store: Path, cells: set[int], found: _Read, work: Path, located: bool) -> None:
    """Store (in the SQLite file ``store``) the walkable ways of ``path`` that touch ``cells``, or are within reach
    of a destination node or closed hut it holds (with their nodes' points, by cell),
    and gather its points into ``found``. ``located``: its ways carry their nodes'
    locations (the walk extract); else they are kept while it is read, in memory or
    for a file over 2 GB in a file in ``work``."""
    import osmium

    # A node of each closed hut drawn as a way, which the catalog does not read: its point, or its id to look up.
    hut_points, hut_nodes = [], []
    first_pass = osmium.FileProcessor(str(path), osmium.osm.RELATION | osmium.osm.WAY)
    for obj in first_pass.with_filter(osmium.filter.KeyFilter("water", *CLOSED_HUT_KEYS)):
        tags = dict(obj.tags)
        if obj.is_way():
            if tags.get("name") and len(obj.nodes) and any(tags.get(key) in HUT_TOURISM for key in CLOSED_HUT_KEYS):
                first = obj.nodes[0]
                if not located:
                    hut_nodes.append(first.ref)
                elif first.location.valid():
                    hut_points.append((first.location.lat, first.location.lon))
            continue
        if tags.get("name") and tags.get("natural") == "water" and tags.get("water") in LAKE_WATER:
            found.lakes[obj.id] = (tags["name"], [])
            for member in obj.members:
                if member.type == "w" and member.role != "inner":
                    found.lake_ways[member.ref] = obj.id
    conn = sqlite3.connect(store)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS walkable_ways (id INTEGER PRIMARY KEY, node_ids BLOB, lats BLOB, lons BLOB, tags TEXT)"
    )
    conn.execute("CREATE TABLE IF NOT EXISTS walkable_cells (cell INTEGER, way INTEGER)")
    rows, cell_rows = [], []
    processor = osmium.FileProcessor(str(path), osmium.osm.NODE | osmium.osm.WAY)
    if not located:
        big = path.stat().st_size > LARGE_EXTRACT_BYTES
        processor = processor.with_locations(f"sparse_file_array,{work / 'locations.bin'}" if big else "flex_mem")
    read_nodes = len(found.found)
    for obj in processor.with_filter(osmium.filter.EmptyTagFilter().enable_for(osmium.osm.NODE)):
        if obj.is_node():
            found.node(dict(obj.tags), (obj.location.lat, obj.location.lon))
            continue
        if read_nodes is not None:  # the file's nodes come first: their destinations are known
            near = [points[0] for _, _, points, _ in found.found[read_nodes:]] + hut_points
            for node in hut_nodes:
                try:
                    location = processor.node_location_storage.get(node)
                except KeyError:
                    continue
                near.append((location.lat, location.lon))
            cells = cells | _cells_near(near)
            read_nodes = None
        tags = dict(obj.tags)
        located = [(node.ref, node.location.lat, node.location.lon) for node in obj.nodes if node.location.valid()]
        if len(located) < 2:
            continue
        points = tuple((lat, lon) for _, lat, lon in located)
        if obj.id in found.lake_ways:
            found.lakes[found.lake_ways[obj.id]][1].extend(points)
        if obj.is_closed() and "highway" not in tags or tags.get("area") == "yes":
            found.area(tags, points)
            continue
        if tags.get("highway") not in WALK_HIGHWAYS or tags.get("service") == "driveway":
            if tags.get("amenity") == "parking":
                found.area(tags, points)
            continue
        if tags.get("access") in ("private", "no") and tags.get("foot") not in ("yes", "designated", "permissive"):
            continue
        way_cells = {_cell(lat, lon) for _, lat, lon in located} & cells
        if not way_cells:
            continue
        ids, lats, lons = zip(*located)
        rows.append((
            obj.id, np.array(ids, dtype=np.int64).tobytes(), np.array(lats).tobytes(), np.array(lons).tobytes(),
            json.dumps(tags),
        ))  # fmt: skip
        cell_rows += [(cell, obj.id) for cell in way_cells]
        if len(rows) >= 50_000:
            _write_ways(conn, rows, cell_rows)
            rows, cell_rows = [], []
    _write_ways(conn, rows, cell_rows)
    conn.execute("CREATE INDEX IF NOT EXISTS walkable_cells_cell ON walkable_cells (cell)")
    conn.commit()
    conn.close()


def _write_ways(conn: sqlite3.Connection, rows, cell_rows) -> None:
    conn.executemany("INSERT OR IGNORE INTO walkable_ways VALUES (?, ?, ?, ?, ?)", rows)
    conn.executemany("INSERT INTO walkable_cells VALUES (?, ?)", cell_rows)
    conn.commit()
