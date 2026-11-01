"""The TrailGraph: the marked hiking network and graded paths as Junctions and Segments (ADR-0018, ADR-0037).

``write_trailgraph`` runs in ``build_catalog`` on the ingest work file, just
before it is deleted, and writes ``{catalog}.trailgraph.sqlite`` beside the
Catalog (``trailgraph_path_for``). Its input is every member way of every
``route=hiking`` or ``route=foot`` relation ingest read, kept as a DayHike or not,
and every graded path (``highway=path`` with a ``sac_scale``) no such relation
lists, with no relation id (ADR-0037):

- Ways are split at every node two of them share (or one repeats). Two ways
  of the same layer that cross with no node there get one at the crossing.
- A dead-end tip joins the nearest node of another way within the snap
  tolerance (20 m by default, at most ``GAP_JOIN_M``), when that node is more
  than ``SNAP_DETOUR_FACTOR`` times the tolerance away along the network and
  neither way is a bridge, a tunnel or on another layer. Each join is a
  ``gap`` Segment.
- Chains through nodes where only two ways of the same relations meet merge
  into one Segment, Junction to Junction.
- Two ways that pass within the tolerance with no join nearer than
  ``LINK_DETOUR_M`` along the network get one ``link`` Segment between their
  closest nodes: a Link is walked only when the Hiker picks "connect here".

Each Segment keeps its OSM way and relation ids, per-way metres and surface
class, strictest ``sac_scale``, full-resolution vertices with OSM ``ele``, and
how a ``Route`` names it walked each way.
The amenity points the Measures count near the network are kept too.

``sample_trailgraph`` stores per vertex what the Measures read from rasters
(DEM height, WorldCover class, CHELSA cell, slope southness) and the
WorldCover cells beside open water, so a server without rasters can compute
the facts of a ComposedHike. ``TrailGraph`` opens the file (or its serving
copy) and composes a hike from a ``Route``: the OSM ways it walks from OSM
node to OSM node, so a shared link holds across rebuilds. ``way_back`` finds
how any walk gets back to its start (ADR-0019), and ``round_trip`` measures a
walk with it.
"""

from __future__ import annotations

import heapq
import json
import math
import re
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from dayhike.catalog import (
    EARTH_RADIUS_M,
    GAP_JOIN_M,
    OSM_ATTRIBUTION,
    SAC_SCALE_RANK,
    SNAP_TOLERANCE_M,
    remove_similarity,
    trailgraph_path_for,
)

Progress = Callable[[str], None]

SNAP_DETOUR_FACTOR = 3  # a tip joins only a node more than this x the tolerance away along the network
LINK_DETOUR_M = 100.0  # ways passing this much farther apart along the network get a Link
MAX_WALK_M = 100_000.0  # the longest ComposedHike
WAY_BACK_SEARCH_M = 50_000.0  # the longest trail back searched; past it a walk is retraced (ADR-0035)
MAX_WALK_STEPS = 50_000  # the most vertex steps a ComposedHike walks: 500 Segments of Slovenia's mean 72 vertices, and room
TRAILHEAD_KINDS = ("parking", "restaurant_cafe")
_SEGMENT_CHUNK = 5_000
_NEVER = -(2**31)  # level of a bridge or tunnel: never joined
COORD_SCALE = 10_000_000  # OSM stores coordinates as 1e-7 degree integers


def _emit(progress: Progress | None, message: str) -> None:
    if progress is not None:
        progress(f"trailgraph: {message}")


# ---------- writing ----------


@dataclass
class _Network:
    """Every hiking member way and graded path as node indices, with what Segments keep of it."""

    node_id: np.ndarray  # OSM node id per node index; crossings get negative ids
    lat: np.ndarray
    lon: np.ndarray
    ele: np.ndarray  # OSM ele, NaN missing
    way_id: np.ndarray
    offsets: np.ndarray  # way w is vertex[offsets[w] : offsets[w + 1]]
    vertex: np.ndarray  # node index per way vertex
    level: np.ndarray  # per way: its layer, or _NEVER on a bridge or tunnel
    sac: list[str | None]
    surface: list[str | None]
    relations: list[list[int]]

    def way_of(self, position: np.ndarray) -> np.ndarray:
        return np.searchsorted(self.offsets, position, side="right") - 1


def write_trailgraph(
    path: Path | str,
    conn: sqlite3.Connection,
    *,
    snap_tolerance_m: float = SNAP_TOLERANCE_M,
    progress: Progress | None = None,
) -> None:
    """Write the TrailGraph of an ingest work file (see the module doc)."""
    check_snap_tolerance(snap_tolerance_m)
    path = Path(path)
    _emit(progress, "loading the network")
    net = _load_network(conn)
    _emit(progress, f"{len(net.way_id)} ways, {len(net.node_id)} nodes")
    crossings = _join_crossings(net)
    _emit(progress, f"{crossings} crossings without a node joined")
    uses = np.bincount(net.vertex, minlength=len(net.node_id))
    split = uses > 1
    first = _Pieces(net, split)
    node_way = _node_ways(net, uses)
    tips, near = _near_pairs(net, node_way, first, snap_tolerance_m)
    gaps = _tip_joins(net, first, tips, near, snap_tolerance_m)
    _emit(progress, f"{len(gaps)} dead-end tips joined")
    for _, target, _ in gaps:
        split[target] = True
    joined = _Pieces(net, split, gaps)
    links = _links(net, node_way, joined, near)
    _emit(progress, f"{len(links)} Links offered")
    for a, b, _ in links:
        split[a] = split[b] = True
    pieces = _Pieces(net, split)
    segments, junctions = _segments(net, pieces, gaps, links)
    _emit(progress, f"{len(junctions)} Junctions, {len(segments)} Segments")
    amenities = _amenities_near(conn, net, segments)
    _write(path, net, segments, junctions, amenities, conn, snap_tolerance_m)


def check_snap_tolerance(snap_tolerance_m: float) -> None:
    """Refuse a snap tolerance that is not above 0 and at most ``GAP_JOIN_M``."""
    if not 0 < snap_tolerance_m <= GAP_JOIN_M:
        raise ValueError(f"snap tolerance must be above 0 and at most {GAP_JOIN_M} m, got {snap_tolerance_m}")


def _load_network(conn: sqlite3.Connection) -> _Network:
    from dayhike.similarity import surface_class

    node_chunks = []
    cursor = conn.execute(
        "SELECT id, lat, lon, ifnull(ele, 'nan') FROM nodes "
        "UNION ALL SELECT id, lat, lon, ifnull(ele, 'nan') FROM graded_nodes ORDER BY id"
    )
    while rows := cursor.fetchmany(1_000_000):
        node_chunks.append(np.array(rows, dtype=float))
    nodes = np.concatenate(node_chunks) if node_chunks else np.empty((0, 4))
    node_id = nodes[:, 0].astype(np.int64)  # OSM ids are far below 2**53
    way_ids, refs, lengths, level, sac, surface = [], [], [], [], [], []
    for way_id, node_ids_json, tags_json in conn.execute(
        "SELECT id, node_ids, tags FROM ways UNION ALL SELECT id, node_ids, tags FROM graded_ways ORDER BY id"
    ):
        node_ids = json.loads(node_ids_json)
        tags = json.loads(tags_json)
        way_ids.append(way_id)
        refs.extend(node_ids)
        lengths.append(len(node_ids))
        level.append(_level(tags))
        scale = tags.get("sac_scale")
        sac.append(scale if scale in SAC_SCALE_RANK else None)
        surface.append(surface_class(tags))
    refs_array = np.array(refs, dtype=np.int64)
    at = np.minimum(np.searchsorted(node_id, refs_array), max(len(node_id) - 1, 0))
    found = node_id[at] == refs_array if len(node_id) else np.zeros(len(refs_array), dtype=bool)
    owner = np.repeat(np.arange(len(way_ids)), lengths)
    kept = np.bincount(owner[found], minlength=len(way_ids))
    position = {way_id: index for index, way_id in enumerate(way_ids)}
    relations: list[list[int]] = [[] for _ in way_ids]
    for rel_id, members_json in conn.execute("SELECT id, members FROM relations ORDER BY id"):
        for member_type, member_id in json.loads(members_json):
            index = position.get(member_id) if member_type == "way" else None
            if index is not None and (not relations[index] or relations[index][-1] != rel_id):
                relations[index].append(rel_id)
    return _Network(
        node_id=node_id,
        lat=nodes[:, 1].copy(),
        lon=nodes[:, 2].copy(),
        ele=nodes[:, 3].copy(),
        way_id=np.array(way_ids, dtype=np.int64),
        offsets=np.concatenate([[0], np.cumsum(kept)]).astype(np.int64),
        vertex=at[found].astype(np.int64),
        level=np.array(level, dtype=np.int64),
        sac=sac,
        surface=surface,
        relations=relations,
    )


def _level(tags: dict[str, str]) -> int:
    if tags.get("bridge", "no") != "no" or tags.get("tunnel", "no") != "no":
        return _NEVER
    try:
        return int(tags.get("layer", "0"))
    except ValueError:
        return _NEVER


def _join_crossings(net: _Network) -> int:
    """Give two same-layer ways that cross with no node there a node at the crossing."""
    import shapely

    starts, ends = net.offsets[:-1], net.offsets[1:]
    have = np.flatnonzero(ends - starts >= 2)
    if len(have) < 2:
        return 0
    lines = np.array(
        [
            shapely.linestrings(np.column_stack([net.lon[net.vertex[s:e]], net.lat[net.vertex[s:e]]]))
            for s, e in zip(starts[have], ends[have])
        ],
        dtype=object,
    )
    left, right = shapely.STRtree(lines).query(lines, predicate="crosses")
    inserts: dict[int, list[tuple[float, int]]] = {}
    new_points: list[tuple[float, float]] = []
    for i, j in zip(left.tolist(), right.tolist()):
        a, b = int(have[i]), int(have[j])
        if i >= j or net.level[a] == _NEVER or net.level[a] != net.level[b]:
            continue
        corners = {
            (float(net.lon[node]), float(net.lat[node]))
            for way in (a, b)
            for node in net.vertex[net.offsets[way] : net.offsets[way + 1]].tolist()
        }
        for point in shapely.get_parts(shapely.intersection(lines[i], lines[j])):
            if shapely.get_type_id(point) != 0:  # a shared stretch, not a crossing
                continue
            x, y = shapely.get_x(point), shapely.get_y(point)
            if (x, y) in corners:
                continue
            node = len(net.node_id) + len(new_points)
            new_points.append((y, x))
            inserts.setdefault(a, []).append((float(shapely.line_locate_point(lines[i], point)), node))
            inserts.setdefault(b, []).append((float(shapely.line_locate_point(lines[j], point)), node))
    if not new_points:
        return 0
    positions, values = [], []
    for way, found in sorted(inserts.items()):
        ids = net.vertex[net.offsets[way] : net.offsets[way + 1]]
        along = np.concatenate(
            [[0.0], np.cumsum(np.hypot(np.diff(net.lon[ids]), np.diff(net.lat[ids])))]
        )
        for distance, node in sorted(found):
            after = min(int(np.searchsorted(along, distance, side="right")), len(ids) - 1)
            positions.append(net.offsets[way] + max(after, 1))
            values.append(node)
    order = np.argsort(positions, kind="stable")
    positions, values = np.array(positions)[order], np.array(values)[order]
    owner = net.way_of(positions - 1)
    net.vertex = np.insert(net.vertex, positions, values)
    net.offsets = net.offsets + np.concatenate(
        [[0], np.cumsum(np.bincount(owner, minlength=len(net.way_id)))]
    )
    count = len(new_points)
    new_lat, new_lon = np.round(np.array(new_points).T * COORD_SCALE) / COORD_SCALE  # as OSM would store them
    net.node_id = np.concatenate([net.node_id, -np.arange(1, count + 1)])
    net.lat = np.concatenate([net.lat, new_lat])
    net.lon = np.concatenate([net.lon, new_lon])
    net.ele = np.concatenate([net.ele, np.full(count, np.nan)])
    return count


class _Pieces:
    """Ways split at ``split`` nodes (and their ends) into pieces, with ``extra``
    edges (node, node, metres); shortest distances along them.

    As in ``catalog._split_ways``, a piece that would start where it ends is
    split once more in its middle.
    """

    def __init__(
        self, net: _Network, split: np.ndarray, extra: Sequence[tuple[int, int, float]] = ()
    ) -> None:
        vertex, offsets = net.vertex, net.offsets
        steps = _haversine(net.lat[vertex[:-1]], net.lon[vertex[:-1]], net.lat[vertex[1:]], net.lon[vertex[1:]])
        boundary = offsets[1:-1]
        steps[boundary[(boundary > 0) & (boundary < len(vertex))] - 1] = 0.0  # no step between ways
        self.cum = np.concatenate([[0.0], np.cumsum(steps)])
        cut = split[vertex].copy()
        nonempty = offsets[1:] > offsets[:-1]
        cut[offsets[:-1][nonempty]] = True
        cut[offsets[1:][nonempty] - 1] = True
        at = np.flatnonzero(cut)
        owner = net.way_of(at)
        same = owner[:-1] == owner[1:]
        start, end = at[:-1][same], at[1:][same]
        closed = vertex[start] == vertex[end]
        middle = start + (end - start + 1) // 2
        keep_open = ~closed
        split_closed = closed & (end - start + 1 > 2)
        self.start = np.concatenate([start[keep_open], start[split_closed], middle[split_closed]])
        self.end = np.concatenate([end[keep_open], middle[split_closed], end[split_closed]])
        order = np.argsort(self.start, kind="stable")
        self.start, self.end = self.start[order], self.end[order]
        self.way = net.way_of(self.start)
        self.u, self.v = vertex[self.start], vertex[self.end]
        self.length = self.cum[self.end] - self.cum[self.start]
        n = len(net.node_id)
        self.piece_of = np.full(n, -1, dtype=np.int64)
        self.along = np.zeros(n)
        inner = _ranges(self.start + 1, self.end - self.start - 1)
        owner_piece = np.repeat(np.arange(len(self.start)), self.end - self.start - 1)
        self.piece_of[vertex[inner]] = owner_piece
        self.along[vertex[inner]] = self.cum[inner] - self.cum[self.start[owner_piece]]
        extra_u = np.array([e[0] for e in extra], dtype=np.int64)
        extra_v = np.array([e[1] for e in extra], dtype=np.int64)
        extra_m = np.array([e[2] for e in extra], dtype=float)
        frm = np.concatenate([self.u, self.v, extra_u, extra_v])
        to = np.concatenate([self.v, self.u, extra_v, extra_u])
        metres = np.concatenate([self.length, self.length, extra_m, extra_m])
        order = np.argsort(frm, kind="stable")
        self._to, self._metres = to[order], metres[order]
        self._nodes, first = np.unique(frm[order], return_index=True)
        self._first = np.append(first, len(frm))
        self.degree = np.bincount(np.concatenate([self.u, self.v]), minlength=n)

    def _neighbours(self, node: int) -> Iterator[tuple[int, float]]:
        at = int(np.searchsorted(self._nodes, node))
        if at < len(self._nodes) and self._nodes[at] == node:
            for k in range(self._first[at], self._first[at + 1]):
                yield int(self._to[k]), float(self._metres[k])

    def _entries(self, node: int) -> list[tuple[int, float]]:
        piece = self.piece_of[node]
        if piece < 0:
            return [(node, 0.0)]
        along = float(self.along[node])
        return [(int(self.u[piece]), along), (int(self.v[piece]), float(self.length[piece]) - along)]

    def within(self, a: int, b: int, limit_m: float) -> bool:
        """Whether nodes ``a`` and ``b`` are at most ``limit_m`` apart along the network."""
        if a == b:
            return True
        if self.piece_of[a] >= 0 and self.piece_of[a] == self.piece_of[b]:
            if abs(self.along[a] - self.along[b]) <= limit_m:
                return True
        targets = dict(self._entries(b))
        best: dict[int, float] = {}
        heap = [(metres, node) for node, metres in self._entries(a) if metres <= limit_m]
        heapq.heapify(heap)
        while heap:
            metres, node = heapq.heappop(heap)
            if node in best:
                continue
            best[node] = metres
            if node in targets and metres + targets[node] <= limit_m:
                return True
            for other, step in self._neighbours(node):
                if other not in best and metres + step <= limit_m:
                    heapq.heappush(heap, (metres + step, other))
        return False


def _node_ways(net: _Network, uses: np.ndarray) -> np.ndarray:
    """Way index of each node on one way only, else -1 (several ways or none)."""
    node_way = np.full(len(net.node_id), -1, dtype=np.int64)
    once = uses[net.vertex] == 1
    node_way[net.vertex[once]] = net.way_of(np.flatnonzero(once))
    return node_way


def _node_levels(net: _Network) -> np.ndarray:
    level = np.full(len(net.node_id), _NEVER, dtype=np.int64)
    # The level of the last way listing a node; ways meeting at a node share it.
    level[net.vertex] = net.level[net.way_of(np.arange(len(net.vertex)))]
    return level


def _near_pairs(
    net: _Network, node_way: np.ndarray, pieces: _Pieces, tolerance_m: float
) -> tuple[np.ndarray, np.ndarray]:
    """Dead-end tips, and (node, node, metres) rows of nodes of different ways
    within ``tolerance_m`` on the same level (closest first)."""
    level = _node_levels(net)
    multi = np.flatnonzero((node_way < 0) & (np.bincount(net.vertex, minlength=len(net.node_id)) > 0))
    ways_of: dict[int, set[int]] = {int(node): set() for node in multi}
    if len(multi):
        is_multi = np.zeros(len(net.node_id), dtype=bool)
        is_multi[multi] = True
        at = np.flatnonzero(is_multi[net.vertex])
        for node, way in zip(net.vertex[at].tolist(), net.way_of(at).tolist()):
            ways_of[node].add(way)
    rows = []
    on_way = np.flatnonzero(level != _NEVER)
    for a, b, metres in _pairs_within(net.lat[on_way], net.lon[on_way], tolerance_m):
        a, b = on_way[a], on_way[b]
        keep = level[a] == level[b]
        simple = (node_way[a] >= 0) & (node_way[b] >= 0)
        keep &= ~simple | (node_way[a] != node_way[b])
        for k in np.flatnonzero(keep & ~simple):
            first = ways_of.get(int(a[k]), {int(node_way[a[k]])})
            second = ways_of.get(int(b[k]), {int(node_way[b[k]])})
            keep[k] = not first & second
        rows.append(np.column_stack([a[keep], b[keep], metres[keep]]))
    near = np.concatenate(rows) if rows else np.empty((0, 3))
    near = near[np.lexsort((near[:, 1], near[:, 0], near[:, 2]))] if len(near) else near
    tips = np.flatnonzero(pieces.degree == 1)
    return tips, near


def _pairs_within(
    lat: np.ndarray, lon: np.ndarray, radius_m: float
) -> Iterator[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """(first, second, metres) of the points within ``radius_m`` of each other,
    first < second, one 1° tile at a time so memory stays bounded."""
    from scipy.spatial import cKDTree

    if len(lat) < 2:
        return
    tile_lat, tile_lon = np.floor(lat).astype(np.int64), np.floor(lon).astype(np.int64)
    key = (tile_lat + 90) * 360 + (tile_lon + 180)
    order = np.argsort(key, kind="stable")
    keys, first = np.unique(key[order], return_index=True)
    bounds = dict(zip(keys.tolist(), zip(first.tolist(), np.append(first[1:], len(order)).tolist())))
    margin_lat = math.degrees(radius_m / EARTH_RADIUS_M) * 1.01
    chord = 2 * math.sin(radius_m / (2 * EARTH_RADIUS_M))
    xyz = _unit(lat, lon)
    for tile in keys.tolist():
        south, west = tile // 360 - 90, tile % 360 - 180
        margin_lon = margin_lat / max(math.cos(math.radians(max(abs(south), abs(south + 1)))), 1e-6)
        start, stop = bounds[tile]
        chosen = [order[start:stop]]
        for d_lat in (-1, 0, 1):
            for d_lon in (-1, 0, 1):
                other = tile + d_lat * 360 + d_lon
                if (d_lat, d_lon) == (0, 0) or other not in bounds:
                    continue
                near = order[bounds[other][0] : bounds[other][1]]
                inside = (
                    (lat[near] >= south - margin_lat) & (lat[near] < south + 1 + margin_lat)
                    & (lon[near] >= west - margin_lon) & (lon[near] < west + 1 + margin_lon)
                )  # fmt: skip
                chosen.append(near[inside])
        points = np.concatenate(chosen)
        pairs = cKDTree(xyz[points]).query_pairs(chord, output_type="ndarray")
        if not len(pairs):
            continue
        a, b = points[pairs[:, 0]], points[pairs[:, 1]]
        a, b = np.minimum(a, b), np.maximum(a, b)
        mine = key[a] == tile
        a, b = a[mine], b[mine]
        yield a, b, _haversine(lat[a], lon[a], lat[b], lon[b])


def _tip_joins(
    net: _Network, pieces: _Pieces, tips: np.ndarray, near: np.ndarray, tolerance_m: float
) -> list[tuple[int, int, float]]:
    """(tip, node, metres): each dead-end tip joined to its nearest node of
    another way that is more than ``SNAP_DETOUR_FACTOR`` x the tolerance away
    along the network, closest first; a tip joined to is no longer one."""
    is_tip = np.zeros(len(net.node_id), dtype=bool)
    is_tip[tips] = True
    done: set[int] = set()
    gaps = []
    for a, b, metres in near.tolist():
        a, b = int(a), int(b)
        for tip, target in ((a, b), (b, a)):
            if not is_tip[tip] or tip in done:
                continue
            if pieces.within(tip, target, SNAP_DETOUR_FACTOR * tolerance_m):
                continue
            done.add(tip)
            if is_tip[target]:
                done.add(target)
            gaps.append((tip, target, float(metres)))
            break
    return gaps


def _links(
    net: _Network, node_way: np.ndarray, pieces: _Pieces, near: np.ndarray
) -> list[tuple[int, int, float]]:
    """(node, node, metres): per pair of ways, their closest nodes within the
    tolerance, when they are more than ``LINK_DETOUR_M`` apart along the network."""
    if not len(near):
        return []
    first_way = np.full(len(net.node_id), -1, dtype=np.int64)
    first_way[net.vertex[::-1]] = net.way_of(np.arange(len(net.vertex)))[::-1]
    a, b = near[:, 0].astype(np.int64), near[:, 1].astype(np.int64)
    way_a, way_b = first_way[a], first_way[b]
    pair = np.minimum(way_a, way_b) * len(net.way_id) + np.maximum(way_a, way_b)
    _, closest = np.unique(pair, return_index=True)  # ``near`` is closest first
    links = []
    for k in np.sort(closest).tolist():
        if not pieces.within(int(a[k]), int(b[k]), LINK_DETOUR_M):
            links.append((int(a[k]), int(b[k]), float(near[k, 2])))
    return links


@dataclass
class _Segment:
    kind: str  # trail, gap or link
    nodes: np.ndarray  # node indices, a to b
    step_way: np.ndarray  # way index of each step, -1 for a gap or link
    step_dir: np.ndarray  # 1 where a step follows its way's node order, else -1


def _segments(
    net: _Network,
    pieces: _Pieces,
    gaps: Sequence[tuple[int, int, float]],
    links: Sequence[tuple[int, int, float]],
) -> tuple[list[_Segment], np.ndarray]:
    """Merge pieces through nodes where only two meet into Segments; return
    them (trail and gap Segments, then Links) and the Junction node indices."""
    n_pieces = len(pieces.start)
    gap_u = np.array([g[0] for g in gaps], dtype=np.int64)
    gap_v = np.array([g[1] for g in gaps], dtype=np.int64)
    edge_u = np.concatenate([pieces.u, gap_u])
    edge_v = np.concatenate([pieces.v, gap_v])
    n = len(net.node_id)
    degree = np.bincount(np.concatenate([edge_u, edge_v]), minlength=n)
    junction = degree != 2
    junction[gap_u] = junction[gap_v] = True
    for a, b, _ in links:
        junction[a] = junction[b] = True
    junction &= degree > 0
    ends = np.concatenate([edge_u, edge_v])
    edges = np.concatenate([np.arange(len(edge_u)), np.arange(len(edge_u))])
    order = np.argsort(ends, kind="stable")
    incident_nodes, first = np.unique(ends[order], return_index=True)
    incident_first = np.append(first, len(order))
    incident_edges = edges[order]
    # Where only two ways meet but they belong to other relations (one stage
    # ends, the next begins), a Segment ends too: its relations are its ways'.
    groups: dict[tuple[int, ...], int] = {}
    group = np.array([groups.setdefault(tuple(r), len(groups)) for r in net.relations] + [-1], dtype=np.int64)
    edge_group = group[np.concatenate([pieces.way, np.full(len(gap_u), -1)])]
    pairs = np.flatnonzero(np.diff(incident_first) == 2)
    meet = incident_nodes[pairs]
    first_group = edge_group[incident_edges[incident_first[pairs]]]
    second_group = edge_group[incident_edges[incident_first[pairs] + 1]]
    junction[meet[first_group != second_group]] = True

    def incident(node: int) -> np.ndarray:
        at = int(np.searchsorted(incident_nodes, node))
        return incident_edges[incident_first[at] : incident_first[at + 1]]

    def edge_nodes(edge: int, forward: bool) -> tuple[np.ndarray, np.ndarray]:
        if edge < n_pieces:
            nodes = net.vertex[pieces.start[edge] : pieces.end[edge] + 1]
            ways = np.full(len(nodes) - 1, pieces.way[edge])
        else:
            nodes = np.array([gap_u[edge - n_pieces], gap_v[edge - n_pieces]])
            ways = np.array([-1])
        return (nodes, ways) if forward else (nodes[::-1], ways[::-1])

    used = np.zeros(len(edge_u), dtype=bool)
    segments: list[_Segment] = []
    extra_junctions: list[int] = []

    def walk(start: int, edge: int) -> None:
        node, chain_nodes, chain_ways, chain_dirs = start, [np.array([start])], [], []
        while True:
            used[edge] = True
            forward = edge_u[edge] == node
            nodes, ways = edge_nodes(edge, forward)
            chain_nodes.append(nodes[1:])
            chain_ways.append(ways)
            chain_dirs.append(np.full(len(ways), 1 if forward else -1))
            node = int(nodes[-1])
            if junction[node] or edge >= n_pieces:
                break
            following = [e for e in incident(node).tolist() if not used[e] and e < n_pieces]
            if not following:
                break
            edge = following[0]
        all_nodes, all_ways, all_dirs = (np.concatenate(c) for c in (chain_nodes, chain_ways, chain_dirs))
        kind = "gap" if all_ways[0] == -1 and len(all_ways) == 1 else "trail"
        if all_nodes[0] == all_nodes[-1]:  # a Segment never ends where it starts
            middle = len(all_nodes) // 2
            extra_junctions.append(int(all_nodes[middle]))
            segments.append(_Segment(kind, all_nodes[: middle + 1], all_ways[:middle], all_dirs[:middle]))
            segments.append(_Segment(kind, all_nodes[middle:], all_ways[middle:], all_dirs[middle:]))
        else:
            segments.append(_Segment(kind, all_nodes, all_ways, all_dirs))

    for node in np.flatnonzero(junction).tolist():
        for edge in incident(node).tolist():
            if not used[edge]:
                walk(node, edge)
    for edge in range(len(edge_u)):  # rings with no Junction on them
        if not used[edge]:
            start = _ring_start(net, edge, edge_u, edge_v, used, incident)
            junction[start] = True
            walk(start, int(next(e for e in incident(start).tolist() if not used[e])))
    junction[extra_junctions] = True
    for a, b, _ in links:
        segments.append(_Segment("link", np.array([a, b]), np.array([-1]), np.array([1])))
    return segments, np.flatnonzero(junction)


def _ring_start(
    net: _Network,
    edge: int,
    edge_u: np.ndarray,
    edge_v: np.ndarray,
    used: np.ndarray,
    incident: Callable[[int], np.ndarray],
) -> int:
    """The piece end with the lowest OSM node id on the ring of unused edges
    through ``edge``: where a ring with no Junction starts, the same in every build."""
    ends, todo, seen = set(), [edge], {edge}
    while todo:
        current = todo.pop()
        for end in (int(edge_u[current]), int(edge_v[current])):
            ends.add(end)
            for other in incident(end).tolist():
                if not used[other] and other not in seen:
                    seen.add(other)
                    todo.append(other)
    return min(ends, key=lambda node: (net.node_id[node] <= 0, abs(int(net.node_id[node]))))


def _amenities_near(
    conn: sqlite3.Connection, net: _Network, segments: Sequence[_Segment]
) -> list[tuple]:
    """Work-file amenity points a ComposedHike can count: (osm_key, kind, lat,
    lon, name, ele tag) of those within the reach of their kind of the network."""
    from scipy.spatial import cKDTree

    from dayhike.similarity import (
        ALONG_KINDS,
        CORRIDOR_M,
        PEAKS_AROUND_M,
        SEARCH_STEP_M,
        TRAILHEAD_M,
        _densify,
    )

    reach = {kind: CORRIDOR_M for kind in ALONG_KINDS}
    reach.update({"peak": PEAKS_AROUND_M, **dict.fromkeys(TRAILHEAD_KINDS, TRAILHEAD_M)})
    rows = conn.execute(
        "SELECT osm_key, kind, lat, lon, json_extract(tags, '$.name'), json_extract(tags, '$.ele') "
        f"FROM amenities WHERE kind IN ({','.join('?' * len(reach))}) ORDER BY osm_key",
        tuple(reach),
    ).fetchall()
    if not rows or not segments:
        return []
    line = np.concatenate(
        [
            _densify(net.lat[s.nodes], net.lon[s.nodes], SEARCH_STEP_M)
            for s in segments
            if s.kind != "link"
        ]
    )
    points = np.array([(lat, lon) for _, _, lat, lon, _, _ in rows], dtype=float)
    distance, _ = cKDTree(_unit(line[:, 0], line[:, 1])).query(_unit(points[:, 0], points[:, 1]))
    metres = 2 * EARTH_RADIUS_M * np.arcsin(np.minimum(distance / 2, 1.0))
    limit = np.array([math.hypot(reach[kind], SEARCH_STEP_M / 2) + 1.0 for _, kind, *_ in rows])
    return [row for row, keep in zip(rows, metres <= limit) if keep]


def _write(
    path: Path,
    net: _Network,
    segments: Sequence[_Segment],
    junctions: np.ndarray,
    amenities: Sequence[tuple],
    conn: sqlite3.Connection,
    snap_tolerance_m: float,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    out = sqlite3.connect(tmp)
    try:
        out.executescript(
            """
            CREATE TABLE trailgraph_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE junctions (
                id INTEGER PRIMARY KEY,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                degree INTEGER NOT NULL,
                key TEXT NOT NULL
            );
            CREATE TABLE segments (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL,
                a INTEGER NOT NULL,
                b INTEGER NOT NULL,
                length_m REAL NOT NULL,
                sac_scale TEXT,
                way_ids TEXT NOT NULL,
                relation_ids TEXT NOT NULL,
                parts TEXT NOT NULL,
                node_ids BLOB NOT NULL,
                coords BLOB NOT NULL,
                ele BLOB NOT NULL,
                landcover BLOB,
                climate_cell BLOB,
                southness BLOB,
                step_ab TEXT NOT NULL,
                step_ba TEXT NOT NULL
            );
            CREATE TABLE segment_ways (
                way_id INTEGER NOT NULL,
                segment_id INTEGER NOT NULL,
                PRIMARY KEY (way_id, segment_id)
            ) WITHOUT ROWID;
            CREATE VIRTUAL TABLE segment_boxes USING rtree(id, south, north, west, east);
            CREATE TABLE relations (id INTEGER PRIMARY KEY, name TEXT, ref TEXT, network TEXT);
            CREATE TABLE amenities (
                id INTEGER PRIMARY KEY,
                osm_key TEXT NOT NULL,
                kind TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                name TEXT,
                ele TEXT
            );
            CREATE VIRTUAL TABLE amenity_boxes USING rtree(id, south, north, west, east);
            """
        )
        degree = np.zeros(len(net.node_id), dtype=np.int64)
        for segment in segments:
            if segment.kind != "link":
                degree[segment.nodes[0]] += 1
                degree[segment.nodes[-1]] += 1
        out.executemany(
            "INSERT INTO junctions VALUES (?, ?, ?, ?, ?)",
            (
                (int(net.node_id[j]), float(net.lat[j]), float(net.lon[j]), int(degree[j]), _junction_key(net, j))
                for j in junctions.tolist()
            ),
        )
        for start in range(0, len(segments), _SEGMENT_CHUNK):
            rows, boxes = [], []
            for number, segment in enumerate(segments[start : start + _SEGMENT_CHUNK], start=start + 1):
                rows.append(_segment_row(number, segment, net))
                lat, lon = net.lat[segment.nodes], net.lon[segment.nodes]
                boxes.append((number, lat.min(), lat.max(), lon.min(), lon.max()))
            out.executemany(f"INSERT INTO segments VALUES ({','.join('?' * 17)})", rows)
            out.executemany(
                "INSERT OR IGNORE INTO segment_ways VALUES (?, ?)",
                ((way, row[0]) for row in rows for way in json.loads(row[6])),
            )
            out.executemany("INSERT INTO segment_boxes VALUES (?, ?, ?, ?, ?)", boxes)
        out.executemany(
            "INSERT INTO relations VALUES (?, ?, ?, ?)",
            conn.execute(
                "SELECT id, json_extract(tags, '$.name'), json_extract(tags, '$.ref'), "
                "json_extract(tags, '$.network') FROM relations ORDER BY id"
            ),
        )
        out.executemany(
            "INSERT INTO amenities VALUES (?, ?, ?, ?, ?, ?, ?)",
            ((number, *row) for number, row in enumerate(amenities, start=1)),
        )
        out.executemany(
            "INSERT INTO amenity_boxes VALUES (?, ?, ?, ?, ?)",
            ((number, lat, lat, lon, lon) for number, (_, _, lat, lon, _, _) in enumerate(amenities, start=1)),
        )
        out.executescript(
            "CREATE INDEX junction_keys ON junctions (key);"
            "CREATE INDEX segment_a ON segments (a);"
            "CREATE INDEX segment_b ON segments (b);"
        )
        out.executemany(
            "INSERT INTO trailgraph_meta VALUES (?, ?)",
            [
                ("attribution", OSM_ATTRIBUTION),
                ("snap_tolerance_m", json.dumps(snap_tolerance_m)),
                ("link_detour_m", json.dumps(LINK_DETOUR_M)),
            ],
        )
        out.commit()
    except Exception:
        out.close()
        tmp.unlink(missing_ok=True)
        raise
    out.close()
    tmp.replace(path)


def _segment_row(number: int, segment: _Segment, net: _Network) -> tuple:
    nodes, step_way = segment.nodes, segment.step_way
    lat, lon = net.lat[nodes], net.lon[nodes]
    steps = _haversine(lat[:-1], lon[:-1], lat[1:], lon[1:])
    runs: list[list] = []  # way index, direction, metres, steps
    for way, direction, metres in zip(step_way.tolist(), segment.step_dir.tolist(), steps.tolist()):
        if runs and runs[-1][:2] == [way, direction]:
            runs[-1][2] += metres
            runs[-1][3] += 1
        else:
            runs.append([way, direction, metres, 1])
    ways = [int(w) for w in dict.fromkeys(step_way.tolist()) if w >= 0]
    scales = [net.sac[w] for w in ways if net.sac[w] is not None]
    relations = sorted({rel for w in ways for rel in net.relations[w]})
    step_ab, step_ba = _steps(net, segment, runs)
    return (
        number,
        segment.kind,
        int(net.node_id[nodes[0]]),
        int(net.node_id[nodes[-1]]),
        float(steps.sum()),
        max(scales, key=SAC_SCALE_RANK.__getitem__) if scales else None,
        json.dumps([int(net.way_id[w]) for w in ways]),
        json.dumps(relations),
        json.dumps(
            [
                [None, round(metres, 3), None, count, None]
                if way < 0
                else [direction * int(net.way_id[way]), round(metres, 3), net.surface[way], count, net.sac[way]]
                for way, direction, metres, count in runs
            ]
        ),
        net.node_id[nodes].astype("<i8").tobytes(),
        encode_coords(lat, lon),
        net.ele[nodes].astype("<f4").tobytes(),
        None,
        None,
        None,
        step_ab,
        step_ba,
    )


def _junction_key(net: _Network, node: int) -> str:
    """A node's name in a shared link, the same in every build: its OSM node
    id, or ``c{lat}_{lon}`` (1e-7 degrees) for a crossing ingest added."""
    node_id = int(net.node_id[node])
    if node_id > 0:
        return str(node_id)
    return f"c{round(float(net.lat[node]) * COORD_SCALE)}_{round(float(net.lon[node]) * COORD_SCALE)}"


def _steps(net: _Network, segment: _Segment, runs: Sequence[list]) -> tuple[str, str]:
    """The Segment walked from a and from b, as a shared link names it (see ``Route``)."""
    nodes = segment.nodes
    if segment.kind != "trail":
        tag = segment.kind[0]
        return f"{tag}.{_junction_key(net, nodes[-1])}", f"{tag}.{_junction_key(net, nodes[0])}"
    bounds = np.cumsum([0, *(run[3] for run in runs)])
    ab = [
        f"{direction * int(net.way_id[way])}.{_junction_key(net, nodes[end])}"
        for (way, direction, _, _), end in zip(runs, bounds[1:].tolist())
    ]
    ba = [
        f"{-direction * int(net.way_id[way])}.{_junction_key(net, nodes[start])}"
        for (way, direction, _, _), start in zip(runs[::-1], bounds[:-1][::-1].tolist())
    ]
    return ",".join(ab), ",".join(ba)


# ---------- sampling ----------


def sample_trailgraph(
    catalog_path: Path | str,
    *,
    dem_dir: Path | str,
    landcover_dir: Path | str,
    climate_dir: Path | str,
    progress: Progress | None = None,
) -> int:
    """Store per TrailGraph vertex what the Measures read from rasters.

    The same samplers as ``sample-dem``, ``sample-landcover``,
    ``sample-climate`` and ``build-similarity`` read the same rasters, so a
    ComposedHike that walks a DayHike's own vertices gets its facts:

    - ``ele``: the DEM (FABDEM, or Copernicus GLO-30, which needs the
      WorldCover tiles to read the ground under trees) where a tile covers
      the vertex, else its OSM ``ele``;
    - ``landcover``: the WorldCover class (0 uncovered);
    - ``climate_cell``: the CHELSA cell, when all 36 rasters have a value
      there (-1 otherwise), and ``climate_cells`` its 36 values;
    - ``southness``: the slope southness (NaN flat or uncovered);
    - ``water_tiles`` and ``water_cells``: per WorldCover tile its overview
      grid and the cells near the network that are beside open water.

    A missing raster directory leaves its values as they are. The similarity
    facts read these samples on each way back, so they are deleted, as
    after every sample step. Returns the number of vertices.
    """
    from dayhike.climate import _climate_rasters
    from dayhike.dem import COPERNICUS, dem_source_of, require_worldcover, sample_elevations
    from dayhike.landcover import _raster_paths, sample_landcover_classes
    from dayhike.similarity import slope_southness

    path = trailgraph_path_for(catalog_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    dem_dir, landcover_dir, climate_dir = Path(dem_dir), Path(landcover_dir), Path(climate_dir)
    source = dem_source_of(dem_dir)
    has_dem = source is not None
    if source is COPERNICUS:
        require_worldcover(landcover_dir)
    has_landcover = bool(_raster_paths(landcover_dir)) if landcover_dir.is_dir() else False
    rasters = _climate_rasters(climate_dir) if climate_dir.is_dir() else []
    grid = _climate_grid(rasters) if rasters else None
    db = sqlite3.connect(path)
    sampled = 0
    cells: set[int] = set()
    try:
        ids = [row[0] for row in db.execute("SELECT id FROM segments ORDER BY id")]
        for start in range(0, len(ids), _SEGMENT_CHUNK):
            chunk = db.execute(
                "SELECT id, coords, ele, landcover, climate_cell, southness FROM segments "
                "WHERE id BETWEEN ? AND ? ORDER BY id",
                (ids[start], ids[min(start + _SEGMENT_CHUNK, len(ids)) - 1]),
            ).fetchall()
            coords = [decode_coords(row[1]) for row in chunk]
            sizes = [len(c) for c in coords]
            lat, lon = np.concatenate(coords).T
            ele = np.concatenate([np.frombuffer(row[2], dtype="<f4") for row in chunk]).astype(float)
            landcover = np.concatenate([_blob(row[3], "u1", len(c), 0) for row, c in zip(chunk, coords)])
            climate = np.concatenate([_blob(row[4], "<i4", len(c), -1) for row, c in zip(chunk, coords)])
            southness = np.concatenate([_blob(row[5], "<f4", len(c), np.nan) for row, c in zip(chunk, coords)])
            points = list(zip(lat.tolist(), lon.tolist()))
            if has_dem:
                heights = sample_elevations(dem_dir, points, landcover_dir)
                ele = np.array([e if h is None else h for e, h in zip(ele.tolist(), heights)], dtype=float)
                southness = slope_southness(dem_dir, lat, lon)
            if has_landcover:
                classes = sample_landcover_classes(landcover_dir, points)
                landcover = np.array([c or 0 for c in classes], dtype=np.uint8)
            if grid is not None:
                climate = _climate_cells(rasters, grid, lat, lon)
                cells.update(climate[climate >= 0].tolist())
            bounds = np.cumsum([0, *sizes])
            db.executemany(
                "UPDATE segments SET ele = ?, landcover = ?, climate_cell = ?, southness = ? WHERE id = ?",
                [
                    (
                        ele[s:e].astype("<f4").tobytes(),
                        landcover[s:e].astype("u1").tobytes(),
                        climate[s:e].astype("<i4").tobytes(),
                        southness[s:e].astype("<f4").tobytes(),
                        row[0],
                    )
                    for row, s, e in zip(chunk, bounds[:-1], bounds[1:])
                ],
            )
            sampled += len(lat)
            _emit(progress, f"sampled {sampled} vertices")
        if grid is not None:
            _write_climate_cells(db, rasters, grid, sorted(cells))
        if has_landcover:
            _write_water(db, landcover_dir, progress)
        db.commit()
    finally:
        db.close()
    remove_similarity(catalog_path, progress)  # a way back reads these samples (ADR-0019)
    return sampled


def _blob(value: bytes | None, dtype: str, size: int, missing: float) -> np.ndarray:
    if value is None:
        return np.full(size, missing, dtype=dtype)
    return np.frombuffer(value, dtype=dtype)


def _climate_grid(rasters: Sequence[tuple[str, int, Path]]) -> tuple:
    """The one grid (transform, height, width) every climate raster shares."""
    import rasterio

    grids = set()
    for _, _, raster in rasters:
        with rasterio.open(raster) as dataset:
            grids.add((tuple(dataset.transform)[:6], dataset.height, dataset.width))
    if len(grids) != 1:
        raise ValueError("the climate rasters are not on one grid; refetch them with `dayhike fetch-climate`")
    return grids.pop()


def _climate_cells(rasters, grid: tuple, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """The climate grid cell of each point (row x width + col), -1 where a raster has no value."""
    from rasterio import Affine

    from dayhike.climate import _raster_values

    transform, height, width = Affine(*grid[0]), grid[1], grid[2]
    covered = np.ones(len(lat), dtype=bool)
    for _, _, raster in rasters:
        covered &= ~np.isnan(_raster_values(raster, lat, lon))
    cols, rows = ~transform @ (lon, lat)
    cell = np.floor(rows).astype(np.int64) * width + np.floor(cols).astype(np.int64)
    return np.where(covered, cell, -1)


def _write_climate_cells(db: sqlite3.Connection, rasters, grid: tuple, cells: list[int]) -> None:
    from rasterio import Affine

    from dayhike.climate import CHELSA_VARIABLES, _raster_values

    transform, width = Affine(*grid[0]), grid[2]
    cell = np.array(cells, dtype=np.int64)
    lon, lat = transform @ (cell % width + 0.5, cell // width + 0.5)
    values = np.full((len(cell), 12, len(CHELSA_VARIABLES)), np.nan, dtype=np.float32)
    for variable, month, raster in rasters:
        values[:, month - 1, CHELSA_VARIABLES.index(variable)] = _raster_values(raster, lat, lon)
    db.execute("DROP TABLE IF EXISTS climate_cells")
    db.execute("CREATE TABLE climate_cells (cell INTEGER PRIMARY KEY, months BLOB NOT NULL)")
    db.executemany(
        "INSERT INTO climate_cells VALUES (?, ?)",
        ((int(c), v.astype("<f4").tobytes()) for c, v in zip(cell, values)),
    )
    db.execute("INSERT OR REPLACE INTO trailgraph_meta VALUES ('climate_grid', ?)", (json.dumps(grid),))


def _write_water(db: sqlite3.Connection, landcover_dir: Path, progress: Progress | None) -> None:
    """Per WorldCover tile, the overview cells near the network beside open water.

    Every point a ComposedHike can probe lies on a Segment's straight steps;
    the steps are walked every ``_WATER_STEP_M`` and each cell they pass and
    its eight neighbours are tested, at the cell's centre latitude.
    """
    from dayhike.similarity import WaterTile, _densify

    db.execute("DROP TABLE IF EXISTS water_tiles")
    db.execute("DROP TABLE IF EXISTS water_cells")
    db.execute(
        "CREATE TABLE water_tiles (south INTEGER NOT NULL, west INTEGER NOT NULL, transform TEXT NOT NULL,"
        " height INTEGER NOT NULL, width INTEGER NOT NULL, PRIMARY KEY (south, west))"
    )
    db.execute(
        "CREATE TABLE water_cells (south INTEGER NOT NULL, west INTEGER NOT NULL, cell INTEGER NOT NULL,"
        " PRIMARY KEY (south, west, cell)) WITHOUT ROWID"
    )
    box = db.execute("SELECT min(south), max(north), min(west), max(east) FROM segment_boxes").fetchone()
    if box[0] is None:
        return
    for south in range(int(math.floor(box[0] / 3)) * 3, int(box[1]) + 3, 3):
        for west in range(int(math.floor(box[2] / 3)) * 3, int(box[3]) + 3, 3):
            tile = WaterTile.open(landcover_dir, south, west)
            if tile is None:
                continue
            found = [
                decode_coords(coords)
                for (coords,) in db.execute(
                    "SELECT s.coords FROM segment_boxes b JOIN segments s ON s.id = b.id "
                    "WHERE b.north >= ? AND b.south <= ? AND b.east >= ? AND b.west <= ? AND s.kind != 'link'",
                    (south, south + 3, west, west + 3),
                )
            ]
            db.execute(
                "INSERT INTO water_tiles VALUES (?, ?, ?, ?, ?)",
                (south, west, json.dumps(tuple(tile.transform)[:6]), tile.height, tile.width),
            )
            if not found:
                continue
            dense = np.concatenate([_densify(c[:, 0], c[:, 1], _WATER_STEP_M) for c in found])
            inside = (np.floor(dense[:, 0] / 3) * 3 == south) & (np.floor(dense[:, 1] / 3) * 3 == west)
            rows, cols = tile.cells(dense[inside, 0], dense[inside, 1])
            near = np.unique(
                np.concatenate(
                    [
                        np.clip(rows + dr, 0, tile.height - 1) * tile.width + np.clip(cols + dc, 0, tile.width - 1)
                        for dr in (-1, 0, 1)
                        for dc in (-1, 0, 1)
                    ]
                )
            )
            rows, cols = near // tile.width, near % tile.width
            _, centre_lat = tile.transform @ (cols + 0.5, rows + 0.5)
            beside = near[tile.beside(rows, cols, np.asarray(centre_lat))]
            db.executemany(
                "INSERT INTO water_cells VALUES (?, ?, ?)", ((south, west, int(c)) for c in beside)
            )
            _emit(progress, f"water cells of tile {south},{west}: {len(beside)} of {len(near)}")


_WATER_STEP_M = 5.0  # well under a WorldCover overview cell (40 m by about 28 m in Europe)


# ---------- composing ----------


_JUNCTION_KEY = r"\d+|c-?\d+_-?\d+"
_STEP = re.compile(rf"(?:-?\d+|g|l)\.(?:{_JUNCTION_KEY})")


@dataclass(frozen=True)
class Route:
    """A ComposedHike as the Hiker drew it, named by OSM ids so that a shared
    link outlives a rebuild on a newer snapshot.

    ``start`` is the key of the node it starts at: its OSM node id, or
    ``c{lat}_{lon}`` in 1e-7 degrees for a crossing ingest added. Each step
    walks one OSM way from the node reached so far, ``{way}.{node}``: the way
    id (negative against the way's node order) and the key of the node where
    the walk leaves that way; ``g.{node}`` crosses a gap Segment and
    ``l.{node}`` a Link. Steps name no Junction or Segment, so a rebuild that
    adds or drops Junctions along the way still finds the same walk. As text
    (``str``, ``parse``) it is ``start:step,step,...``.
    """

    start: str
    steps: tuple[str, ...]

    @classmethod
    def parse(cls, text: str) -> Route:
        start, _, steps = text.partition(":")
        route = cls(start, tuple(steps.split(",")) if steps else ())
        if not re.fullmatch(_JUNCTION_KEY, start) or not all(_STEP.fullmatch(step) for step in route.steps):
            raise RouteError(f"not a composed hike: {text[:80]!r}")
        return route

    def __str__(self) -> str:
        return f"{self.start}:{','.join(self.steps)}"


@dataclass
class ComposedWalk:
    """The stitched vertices of a Route and what the facts need of its Segments."""

    route: Route | None  # None for a way back
    start: int | None  # the start Junction's id in this TrailGraph; None where the walk cuts a Segment
    segments: list[int] | None  # the Segment ids walked in this TrailGraph; None where it cuts one
    steps: list[str] | None  # each of them walked, as ``Route`` names it
    lat: np.ndarray
    lon: np.ndarray
    ele: np.ndarray
    landcover: np.ndarray  # WorldCover class, 0 missing
    climate_cell: np.ndarray  # -1 missing
    southness: np.ndarray
    walked: list[tuple[float, str | None]]  # (metres, surface class) per walked way, first walked first
    sac_scale: str | None
    relation_m: dict[int, float]  # metres walked along each OSM relation, first walked first


class RouteError(ValueError):
    """The Route is not one walk on this TrailGraph."""


WAY_BACK_KINDS = ("none", "trail", "step", "retrace")


@dataclass
class WayBack:
    """How a walk gets from its last vertex back to its first (ADR-0019).

    ``kind`` is ``none`` (it ends where it starts), ``step`` (a straight step
    over a gap of at most ``GAP_JOIN_M``, as the main walk joins its ways),
    ``trail`` (the shortest walk over the TrailGraph, in ``walk``) or
    ``retrace`` (the walk itself, backwards), the shortest of them; ``metres``
    is its length.
    """

    kind: str
    metres: float
    walk: ComposedWalk | None = None


def way_back(lat: np.ndarray, lon: np.ndarray, graph: TrailGraph | None) -> WayBack:
    """The way back of the walk with these vertices (see ``WayBack``).

    Ends at most ``GAP_JOIN_M`` apart take the straight step. Otherwise the
    shortest walk over the TrailGraph's trails and gaps (never a Link), when
    both ends are TrailGraph vertices and it is shorter than retracing the
    walk and than ``WAY_BACK_SEARCH_M``; else the walk retraced. Without a
    TrailGraph there is no trail. The bound keeps the search's work that of a
    day hike's for a walk of any length (ADR-0035).
    """
    if len(lat) < 2:
        return WayBack("none", 0.0)
    gap = float(_haversine(lat[-1:], lon[-1:], lat[:1], lon[:1])[0])
    if gap == 0.0:
        return WayBack("none", 0.0)
    if gap <= GAP_JOIN_M:
        return WayBack("step", gap)
    retrace = float(_haversine(lat[:-1], lon[:-1], lat[1:], lon[1:]).sum())
    limit = min(retrace - 1.0, WAY_BACK_SEARCH_M)
    trail = None if graph is None or gap >= limit else graph.trail_back(lat, lon, limit)
    if trail is None:
        return WayBack("retrace", retrace)
    return WayBack("trail", float(_haversine(trail.lat[:-1], trail.lon[:-1], trail.lat[1:], trail.lon[1:]).sum()), trail)


def round_trip(walk: ComposedWalk, back: WayBack) -> ComposedWalk:
    """The walk followed by its way back, as one walk that ends where it starts.

    Only a trail back adds climate cells: a step or a retrace walks no new
    ground, so the walk's mean climate stands (as the Catalog's does).
    """
    columns = ("lat", "lon", "ele", "landcover", "climate_cell", "southness")
    if back.kind == "none":
        return walk
    if back.kind == "trail":
        tail = {column: getattr(back.walk, column)[1:] for column in columns}
        walked, scale, relations = back.walk.walked, back.walk.sac_scale, back.walk.relation_m
    elif back.kind == "step":
        tail = {column: getattr(walk, column)[:1] for column in columns}
        walked, scale, relations = [], None, {}
    else:
        tail = {column: getattr(walk, column)[::-1][1:] for column in columns}
        walked, scale, relations = walk.walked[::-1], walk.sac_scale, walk.relation_m
    if back.kind != "trail":
        tail["climate_cell"] = np.full(len(tail["lat"]), -1, dtype=walk.climate_cell.dtype)
    scales = [s for s in (walk.sac_scale, scale) if s is not None]
    merged = dict(walk.relation_m)
    for relation, metres in relations.items():
        merged[relation] = merged.get(relation, 0.0) + metres
    return replace(
        walk,
        **{column: np.concatenate([getattr(walk, column), tail[column]]) for column in columns},
        walked=[*walk.walked, *walked],
        sac_scale=max(scales, key=SAC_SCALE_RANK.__getitem__) if scales else None,
        relation_m=merged,
    )


def _decode(row: tuple) -> tuple[list[np.ndarray], list, np.ndarray, list[int]]:
    """A Segment row's per-vertex columns (lat, lon, ele, landcover, climate
    cell, southness), its runs, the run of each step and its relation ids."""
    _, _, _, _, relation_ids, parts, _, coords, ele, landcover, climate, southness, _, _ = row
    points = decode_coords(coords)
    size = len(points)
    runs = json.loads(parts)
    vertices = [
        points[:, 0],
        points[:, 1],
        np.frombuffer(ele, dtype="<f4").astype(float),
        _blob(landcover, "u1", size, 0).astype(np.int64),
        _blob(climate, "<i4", size, -1).astype(np.int64),
        _blob(southness, "<f4", size, np.nan).astype(float),
    ]
    return vertices, runs, np.repeat(np.arange(len(runs)), [run[3] for run in runs]), json.loads(relation_ids)


def _describe(key: str) -> str:
    if key.startswith("c"):
        lat, lon = (int(value) / COORD_SCALE for value in key[1:].split("_"))
        return f"the trail crossing at {lat:.7f}, {lon:.7f}"
    return f"OSM node {key}"


def _vertex_keys(node_ids: bytes, coords: bytes) -> list[str]:
    """The key (see ``Route``) of each vertex of a Segment."""
    fixed = np.frombuffer(coords, dtype="<i4").reshape(-1, 2).tolist()
    return [
        str(node) if node > 0 else f"c{lat}_{lon}"
        for node, (lat, lon) in zip(np.frombuffer(node_ids, dtype="<i8").tolist(), fixed)
    ]


_SEGMENT_COLUMNS = (
    "id, kind, a, b, relation_ids, parts, node_ids, coords, ele, landcover, climate_cell, southness, step_ab, step_ba"
)


class TrailGraph:
    """A TrailGraph file (or its serving copy), opened read-only."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        self._uri = f"file:{self.path}?mode=ro"
        self._graph: tuple | None = None  # what ``trail_back`` searches, read once

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self._uri, uri=True)

    def walk(self, route: Route) -> ComposedWalk:
        """Follow the Route's steps from its start node and stitch what they walk.

        A step follows its OSM way from the node reached so far, through any
        Junctions, to its node, wherever that now lies on a Segment; a
        Segment may be walked more than once. Where OSM edits leave a step
        with no such walk, the RouteError says what changed: the way is off
        the network, a node is no longer on the way, or the way no longer
        leads from one to the other. The vertices are kept as drawn: a
        shared vertex once, a vertex at the same place as the one before it
        dropped (as DayHikeGeometry).
        """
        if not route.steps:
            raise RouteError("a composed hike needs at least one Segment")
        rows: dict[int, tuple] = {}
        ways: dict[int, tuple[dict, set]] = {}
        keys: dict[int, list[str]] = {}
        pieces: list[list[int]] = []  # [segment id, first vertex, last vertex], walked in that order
        here, total = route.start, 0
        with self._db() as db:
            for number, step in enumerate(route.steps, start=1):
                way, _, end = step.partition(".")
                where = f"step {number} of this hike"
                if way in ("g", "l"):
                    walked = self._cross(db, rows, "gap" if way == "g" else "link", here, end, where)
                else:
                    walked = self._follow(db, rows, ways, keys, int(way), here, end, where)
                total += len(walked)
                if total > MAX_WALK_STEPS:
                    raise RouteError(
                        f"{where}: a composed hike may walk at most {MAX_WALK_STEPS:,} vertex steps; draw a shorter one"
                    )
                for segment, first, last in walked:
                    previous = pieces[-1] if pieces else None
                    if previous and previous[0] == segment and previous[2] == first and (
                        (previous[2] - previous[1]) * (last - first) > 0
                    ):
                        previous[2] = last
                    else:
                        pieces.append([segment, first, last])
                here = end
        return self._stitch(route, rows, pieces)

    def walk_segments(self, start: int, segments: Sequence[int]) -> ComposedWalk:
        """The walk along whole Segments of this TrailGraph from Junction
        ``start``, each from the end the walk so far reached, with the Route
        a shared link names it by (what ``walk`` of that Route stitches)."""
        rows: dict[int, tuple] = {}
        with self._db() as db:
            self._load(db, rows, f"WHERE id IN ({','.join('?' * len(segments))})", tuple(segments))
            (key,) = db.execute("SELECT key FROM junctions WHERE id = ?", (start,)).fetchone()
        pieces, here = [], start
        for segment in segments:
            last = len(rows[segment][7]) // 8 - 1
            forward = rows[segment][2] == here
            pieces.append([segment, 0, last] if forward else [segment, last, 0])
            here = rows[segment][3] if forward else rows[segment][2]
        walk = self._stitch(None, rows, pieces)
        return replace(walk, route=Route(key, tuple(",".join(walk.steps).split(","))))

    def _load(self, db: sqlite3.Connection, rows: dict[int, tuple], where: str, values: tuple) -> list[tuple]:
        found = db.execute(f"SELECT {_SEGMENT_COLUMNS} FROM segments s {where}", values).fetchall()
        for row in found:
            rows.setdefault(row[0], row)
        return found

    def _cross(
        self, db: sqlite3.Connection, rows: dict[int, tuple], kind: str, here: str, end: str, where: str
    ) -> list[tuple[int, int, int]]:
        ids = dict(db.execute("SELECT key, id FROM junctions WHERE key IN (?, ?)", (here, end)))
        found = []
        if here in ids and end in ids:
            found = self._load(
                db, rows, "WHERE kind = ? AND ((a = ? AND b = ?) OR (a = ? AND b = ?)) ORDER BY id LIMIT 1",
                (kind, ids[here], ids[end], ids[end], ids[here]),
            )  # fmt: skip
        if not found:
            what = "the gap" if kind == "gap" else "the connect-here link"
            raise RouteError(f"{where}: {what} from {_describe(here)} to {_describe(end)} is no longer on the marked network")
        segment, a = found[0][0], found[0][2]
        return [(segment, 0, 1) if a == ids[here] else (segment, 1, 0)]

    def _follow(
        self,
        db: sqlite3.Connection,
        rows: dict[int, tuple],
        ways: dict[int, tuple[dict, set]],
        keys: dict[int, list[str]],
        way: int,
        here: str,
        end: str,
        where: str,
    ) -> list[tuple[int, int, int]]:
        """The steps (segment, vertex, next vertex) along OSM way ``|way|`` from
        ``here`` to ``end``, in its node order when ``way`` is positive; ``ways``
        keeps each way walked so far and ``keys`` each Segment's vertex keys."""
        if way not in ways:
            ways[way] = self._way(db, rows, keys, way, where)
        leaving, on_way = ways[way]
        for node in (here, end):
            if node not in on_way:
                raise RouteError(f"{where}: {_describe(node)} is no longer on OSM way {abs(way)}")
        steps, node = [], here
        while node != end or not steps:
            if node not in leaving or len(steps) > len(leaving):
                raise RouteError(
                    f"{where}: OSM way {abs(way)} no longer leads from {_describe(here)} to {_describe(end)}"
                )
            segment, first, last, node = leaving[node]
            steps.append((segment, first, last))
        return steps

    def _way(
        self, db: sqlite3.Connection, rows: dict[int, tuple], keys: dict[int, list[str]], way: int, where: str
    ) -> tuple[dict, set]:
        """The step leaving each node of OSM way ``|way|`` (segment, vertex, next
        vertex, next node), walked in its node order when ``way`` is positive, and its nodes."""
        found = self._load(
            db, rows, "JOIN segment_ways w ON w.segment_id = s.id WHERE w.way_id = ? ORDER BY s.id", (abs(way),)
        )
        if not found:
            raise RouteError(f"{where}: OSM way {abs(way)} is no longer on the marked network")
        leaving: dict[str, tuple[int, int, int, str]] = {}
        on_way: set[str] = set()
        for row in found:
            if row[0] not in keys:
                keys[row[0]] = _vertex_keys(row[6], row[7])
            vertex = keys[row[0]]
            at = 0
            for signed, _, _, count, _ in json.loads(row[5]):
                if signed is not None and abs(signed) == abs(way):
                    for k in range(at, at + count):
                        on_way.update((vertex[k], vertex[k + 1]))
                        if (signed > 0) == (way > 0):
                            leaving.setdefault(vertex[k], (row[0], k, k + 1, vertex[k + 1]))
                        else:
                            leaving.setdefault(vertex[k + 1], (row[0], k + 1, k, vertex[k]))
                at += count
        return leaving, on_way

    def _stitch(self, route: Route | None, rows: dict[int, tuple], pieces: list[list[int]]) -> ComposedWalk:
        columns: list[list[np.ndarray]] = [[], [], [], [], [], []]
        walked: dict[int, list] = {}
        scales: set[str] = set()
        relations: dict[int, float] = {}
        whole, walk_m = True, 0.0
        decoded: dict[int, tuple] = {}  # each Segment once, however many pieces walk it
        for number, (segment, first, last) in enumerate(pieces):
            if segment not in decoded:
                decoded[segment] = _decode(rows[segment])
            vertices, runs, run_of_step, relation_ids = decoded[segment]
            whole &= {first, last} == {0, len(vertices[0]) - 1}
            low, high = min(first, last), max(first, last)
            values = [v[low : high + 1] if first < last else v[low : high + 1][::-1] for v in vertices]
            for column, value in zip(columns, values):
                column.append(value if number == 0 else value[1:])
            step_runs = run_of_step[low:high]
            metres = _haversine(values[0][:-1], values[1][:-1], values[0][1:], values[1][1:])
            walk_m += float(metres.sum())
            if walk_m > MAX_WALK_M:
                raise RouteError(f"a composed hike may be at most {MAX_WALK_M / 1000:.0f} km long; draw a shorter one")
            for run, step_m in zip(step_runs[:: 1 if first < last else -1].tolist(), metres.tolist()):
                way, _, surface, _, sac_scale = runs[run]
                if way is not None:
                    walked.setdefault(abs(way), [0.0, surface])[0] += step_m
                if sac_scale is not None:
                    scales.add(sac_scale)
            for relation in relation_ids:
                relations[relation] = relations.get(relation, 0.0) + float(metres.sum())
        lat, lon, ele, landcover, climate, southness = (np.concatenate(column) for column in columns)
        keep = np.concatenate([[True], (np.diff(lat) != 0) | (np.diff(lon) != 0)])
        first_row = rows[pieces[0][0]]
        return ComposedWalk(
            route=route,
            start=(first_row[2] if pieces[0][1] == 0 else first_row[3]) if whole else None,
            segments=[piece[0] for piece in pieces] if whole else None,
            steps=[rows[s][12] if f == 0 else rows[s][13] for s, f, _ in pieces] if whole else None,
            lat=lat[keep],
            lon=lon[keep],
            ele=ele[keep],
            landcover=landcover[keep],
            climate_cell=climate[keep],
            southness=southness[keep],
            walked=[(metres, surface) for metres, surface in walked.values()],
            sac_scale=max(scales, key=SAC_SCALE_RANK.__getitem__) if scales else None,
            relation_m=relations,
        )

    def trail_back(self, lat: np.ndarray, lon: np.ndarray, limit_m: float) -> ComposedWalk | None:
        """The shortest walk over trail and gap Segments (never a Link) from
        the walk's last vertex to its first, when both are TrailGraph vertices
        and it is shorter than ``limit_m``; else None. The search stops at
        the best way found, so its work grows with the ground within that
        distance, not with the TrailGraph."""
        rows: dict[int, tuple] = {}
        with self._db() as db:
            end = self._locate(db, rows, lat[-1], lon[-1])
            start = self._locate(db, rows, lat[0], lon[0])
            if end is None or start is None:
                return None
            nodes, start_of, neighbour, weight, segment_of = self._network()

            def ends(segment: int, vertex: int, along: np.ndarray) -> list[tuple[int, float, int]]:
                """(node, metres to it, its vertex) for both ends of the Segment."""
                a, b = np.searchsorted(nodes, rows[segment][2:4])
                return [(int(a), along[vertex], 0), (int(b), along[-1] - along[vertex], len(along) - 1)]

            sources, targets = ends(*end), ends(*start)
            best, via = limit_m, None
            if end[0] == start[0] and abs(end[2][end[1]] - start[2][start[1]]) < best:
                best, via = abs(end[2][end[1]] - start[2][start[1]]), ()
            # Dijkstra from both ends of the end's Segment, each with its metres to it.
            reach: dict[int, list[tuple[int, float]]] = {}
            for t, (node, to_target, _) in enumerate(targets):
                reach.setdefault(node, []).append((t, to_target))
            settled: dict[int, tuple[int, int, int]] = {}  # node: (Junction before it, source, entry to it)
            heap = [(to_source, node, -1, s, -1) for s, (node, to_source, _) in enumerate(sources)]
            heapq.heapify(heap)
            while heap:
                metres, node, before, s, k = heapq.heappop(heap)
                if metres >= best:
                    break
                if node in settled:
                    continue
                settled[node] = (before, s, k)
                for t, to_target in reach.get(node, ()):
                    if metres + to_target < best:
                        best, via = metres + to_target, (node, t)
                first, after = int(start_of[node]), int(start_of[node + 1])
                for k, other, step_m in zip(
                    range(first, after), neighbour[first:after].tolist(), weight[first:after].tolist()
                ):
                    if other not in settled:
                        heapq.heappush(heap, (metres + step_m, other, node, s, k))
            if via is None:
                return None
            if not via:
                return self._stitch(None, rows, [[end[0], end[1], start[1]]])
            reached, t = via
            chain = [reached]
            while settled[chain[-1]][0] != -1:
                chain.append(settled[chain[-1]][0])
            s = settled[reached][1]
            chain.reverse()
            middle = [int(segment_of[settled[y][2]]) for y in chain[1:]]
            if middle:
                self._load(db, rows, f"WHERE id IN ({','.join('?' * len(middle))})", tuple(middle))
        pieces = [[end[0], end[1], sources[s][2]]]
        for x, segment in zip(chain, middle):
            last = len(rows[segment][7]) // 8 - 1
            pieces.append([segment, 0, last] if np.searchsorted(nodes, rows[segment][2]) == x else [segment, last, 0])
        pieces.append([start[0], targets[t][2], start[1]])
        return self._stitch(None, rows, [piece for piece in pieces if piece[1] != piece[2]])

    def _locate(
        self, db: sqlite3.Connection, rows: dict[int, tuple], lat: float, lon: float
    ) -> tuple[int, int, np.ndarray] | None:
        """A Segment (not a Link) with a vertex exactly at this point: its id,
        the vertex and the metres along it to each vertex; None off the network."""
        fixed = np.round(np.array([lat, lon]) * COORD_SCALE).astype("<i4")
        found = self._load(
            db, rows,
            "WHERE s.kind != 'link' AND s.id IN (SELECT id FROM segment_boxes "
            "WHERE south <= ? AND north >= ? AND west <= ? AND east >= ?) ORDER BY s.id",
            (float(lat), float(lat), float(lon), float(lon)),
        )  # fmt: skip
        for row in found:
            at = np.flatnonzero((np.frombuffer(row[7], dtype="<i4").reshape(-1, 2) == fixed).all(axis=1))
            if at.size:
                points = decode_coords(row[7])
                steps = _haversine(points[:-1, 0], points[:-1, 1], points[1:, 0], points[1:, 1])
                return row[0], int(at[0]), np.concatenate([[0.0], np.cumsum(steps)])
        return None

    def _network(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """The Junction ids (sorted); per Junction index its neighbours along
        the shortest Segment to each (Links and rings left out), as arrays of
        the first entry per Junction, then per entry the neighbour, metres
        and that Segment's id."""
        if self._graph is None:
            chunks = []
            with self._db() as db:
                cursor = db.execute("SELECT id, a, b, length_m FROM segments WHERE kind != 'link'")
                while rows := cursor.fetchmany(100_000):  # arrays chunk by chunk, not a million tuples at once
                    chunks.append(np.array(rows, dtype=float))
            found = np.concatenate(chunks) if chunks else np.empty((0, 4))
            ids = found[:, 0].astype(np.int64)  # ids and OSM node ids are far below 2**53
            ends = found[:, 1:3].astype(np.int64)
            metres = found[:, 3].copy()
            nodes = np.unique(ends)
            low, high = np.sort(np.searchsorted(nodes, ends), axis=1).T
            shortest = np.argsort(metres, kind="stable")
            shortest = shortest[low[shortest] != high[shortest]]
            _, first = np.unique(low[shortest] * len(nodes) + high[shortest], return_index=True)
            keep = shortest[first]
            weight = np.maximum(metres[keep], 1e-3)
            here = np.concatenate([low[keep], high[keep]])
            order = np.argsort(here, kind="stable")
            start_of = np.concatenate([[0], np.cumsum(np.bincount(here, minlength=len(nodes)))]).astype(np.int64)
            neighbour = np.concatenate([high[keep], low[keep]])[order].astype(np.int32)
            step_m = np.concatenate([weight, weight])[order].astype(np.float32)
            segment_of = np.concatenate([ids[keep], ids[keep]])[order]
            self._graph = (nodes, start_of, neighbour, step_m, segment_of)
        return self._graph

    def amenities_near(self, lat: np.ndarray, lon: np.ndarray, radius_m: float) -> list[tuple]:
        """(id, kind, lat, lon, name, ele tag) of the amenity points in the walk's
        box grown by ``radius_m`` (a superset of those within it)."""
        grow_lat = math.degrees(radius_m / EARTH_RADIUS_M)
        grow_lon = grow_lat / max(math.cos(math.radians(max(abs(lat.min()), abs(lat.max())))), 1e-6)
        with self._db() as db:
            return db.execute(
                "SELECT a.id, a.kind, a.latitude, a.longitude, a.name, a.ele FROM amenity_boxes b "
                "JOIN amenities a ON a.id = b.id WHERE b.north >= ? AND b.south <= ? AND b.east >= ? "
                "AND b.west <= ? ORDER BY a.id",
                (lat.min() - grow_lat, lat.max() + grow_lat, lon.min() - grow_lon, lon.max() + grow_lon),
            ).fetchall()

    def facts(self, walk: ComposedWalk) -> tuple[dict[str, float], np.ndarray | None]:
        """Every ``FACT_COLUMNS`` fact of a ComposedHike (NaN missing) and its ProfileShape.

        The same functions as ``build-similarity`` on the stitched vertices,
        with the per-vertex samples standing in for the rasters.
        """
        from dayhike.similarity import (
            ALONG_KINDS,
            CLIMATE_NAMES,
            FACT_COLUMNS,
            LANDCOVER_SHARE_COLUMNS,
            PEAKS_AROUND_M,
            TRAILHEAD_M,
            WATERSIDE_STEP_M,
            AmenityPoints,
            _even_points,
            _haversine_m,
            _path_length_m,
            profile_shape,
            solar_aspect_points,
            walk_facts,
        )

        lat, lon, ele = walk.lat, walk.lon, walk.ele
        facts = dict.fromkeys(FACT_COLUMNS, math.nan)
        rows = self.amenities_near(lat, lon, PEAKS_AROUND_M)
        kinds = np.array([row[1] for row in rows], dtype=object)
        points = np.array([(row[2], row[3]) for row in rows], dtype=float).reshape(-1, 2)

        def of_kinds(*wanted: str) -> AmenityPoints:
            keep = np.isin(kinds, wanted)
            return AmenityPoints(points[keep], kinds[keep])

        facts["length_m"] = _path_length_m(lat, lon)
        facts.update(walk_facts(lat, lon, ele, walk.walked, of_kinds(*ALONG_KINDS), of_kinds("peak")))
        known = ele[np.isfinite(ele)]
        if known.size:
            facts["highest_m"], facts["lowest_m"] = float(known.max()), float(known.min())
        if walk.sac_scale is not None:
            facts["difficulty_rank"] = float(SAC_SCALE_RANK[walk.sac_scale])
        codes = walk.landcover[walk.landcover > 0]
        if codes.size:
            from dayhike.landcover import _landcover_fractions

            shares = list(_landcover_fractions(codes.tolist()))
            shares.append(max(1.0 - sum(shares), 0.0))
            facts.update(zip(LANDCOVER_SHARE_COLUMNS, shares))
        cells = walk.climate_cell[walk.climate_cell >= 0]
        if cells.size:
            months = self._climate(cells).mean(axis=0)  # 12 x 3
            for month in range(12):
                for index, name in enumerate(CLIMATE_NAMES):
                    facts[f"{name}_{month + 1:02d}"] = float(months[month, index])
            facts["temperature_c"] = float(months[5:8, 0].mean())
            facts["precipitation_mm"] = float(months[:, 1].mean())
            facts["solar_radiation"] = float(months[5:8, 2].mean())
        southness = walk.southness[solar_aspect_points(len(lat))]
        southness = southness[~np.isnan(southness)]
        if southness.size:
            facts["southness"] = float(southness.mean())
        if len(lat) >= 2:
            beside = self._beside_water(_even_points(lat, lon, WATERSIDE_STEP_M))
            beside = beside[~np.isnan(beside)]
            if beside.size:
                facts["waterside_share"] = float(beside.mean())
        for kind in TRAILHEAD_KINDS:
            near = [
                _haversine_m(lat[0], lon[0], point[0], point[1]) <= TRAILHEAD_M
                for point, k in zip(points, kinds)
                if k == kind
            ]
            facts[f"{kind}_trailhead"] = float(any(near))
        return facts, profile_shape(lat, lon, ele)

    def _climate(self, cells: np.ndarray) -> np.ndarray:
        """(len(cells), 12, 3) climate of each cell, in the CHELSA variable order."""
        unique, inverse = np.unique(cells, return_inverse=True)
        with self._db() as db:
            months = {
                cell: np.frombuffer(blob, dtype="<f4").reshape(12, 3)
                for start in range(0, len(unique), 900)
                for cell, blob in db.execute(
                    f"SELECT cell, months FROM climate_cells WHERE cell IN ({','.join('?' * len(unique[start : start + 900]))})",
                    unique[start : start + 900].tolist(),
                )
            }
        return np.stack([months[int(cell)] for cell in unique]).astype(float)[inverse]

    def _beside_water(self, points: np.ndarray) -> np.ndarray:
        """1 or 0 for each probe point beside open water or not, NaN without a WorldCover tile."""
        from rasterio import Affine

        from dayhike.similarity import WaterTile

        beside = np.full(len(points), np.nan)
        lat, lon = points[:, 0], points[:, 1]
        corners = np.column_stack([np.floor(lat / 3), np.floor(lon / 3)]).astype(int) * 3
        with self._db() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'water_tiles'").fetchone() is None:
                return beside
            for south, west in {tuple(corner) for corner in corners.tolist()}:
                row = db.execute(
                    "SELECT transform, height, width FROM water_tiles WHERE south = ? AND west = ?", (south, west)
                ).fetchone()
                if row is None:
                    continue
                at = np.flatnonzero((corners[:, 0] == south) & (corners[:, 1] == west))
                tile = WaterTile(Affine(*json.loads(row[0])), row[1], row[2], np.empty((0, 0)))
                rows, cols = tile.cells(lat[at], lon[at])
                cells = (rows * tile.width + cols).tolist()
                wet = {
                    cell
                    for start in range(0, len(cells), 900)
                    for (cell,) in db.execute(
                        f"SELECT cell FROM water_cells WHERE south = ? AND west = ? AND cell IN "
                        f"({','.join('?' * len(cells[start : start + 900]))})",
                        (south, west, *cells[start : start + 900]),
                    )
                }
                beside[at] = [float(cell in wet) for cell in cells]
        return beside


# ---------- helpers ----------


def encode_coords(lat: np.ndarray, lon: np.ndarray) -> bytes:
    """(lat, lon) rows in OSM's fixed point, 1e-7 degrees as int32: exact for OSM nodes."""
    return np.round(np.column_stack([lat, lon]) * COORD_SCALE).astype("<i4").tobytes()


def decode_coords(blob: bytes) -> np.ndarray:
    """Inverse of ``encode_coords``: (lat, lon) rows in degrees, as osmium reads them."""
    return np.frombuffer(blob, dtype="<i4").reshape(-1, 2).astype(float) / COORD_SCALE


def _haversine(lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((phi2 - phi1) / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(a))


def _unit(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    phi, lam = np.radians(lat), np.radians(lon)
    return np.column_stack([np.cos(phi) * np.cos(lam), np.cos(phi) * np.sin(lam), np.sin(phi)])


def _ranges(starts: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """Concatenated ``range(start, start + count)`` for each pair."""
    counts = np.maximum(counts, 0)
    total = int(counts.sum())
    if total == 0:
        return np.empty(0, dtype=np.int64)
    offsets = np.repeat(starts - np.concatenate([[0], np.cumsum(counts)[:-1]]), counts)
    return np.arange(total) + offsets
