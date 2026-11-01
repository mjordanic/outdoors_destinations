"""Built DayHikes: Catalog hikes that are not one OSM relation (ADR-0038).

A built-hike source (``BuiltHikeSource``) gets what ingest read and the
relation DayHikes it kept (``BuildContext``) and returns ``BuiltWalk``s:
NetworkHikes chained from node-network legs, DayStages cut from long tours.
``built_dayhike`` makes each one a DayHike like any other. Its id is a hash of
its Route, the OSM way/node steps it walks (ADR-0018), in a range above every
OSM relation id, so it is the same after a rebuild that leaves the walk as it
was.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from dayhike.catalog import (
    DayHike,
    MainWalk,
    _dayhike,
    _haversine_m,
    _strictest_sac_scale,
)
from dayhike.osm import OsmRelation, OsmSnapshot, OsmWay

BUILT_ID_MIN = 2**40  # every built id is at least this; OSM relation ids are about 2.2e7
BUILT_ID_MAX = 2**53  # and below this, so JavaScript reads it exactly

Point = tuple[float, float]  # (lat, lon)


@dataclass(frozen=True)
class BuiltWalk:
    """One walk a source builds: ``kind`` (``network`` or ``stage``, also its
    NameOrigin), its label, ordered (node id, point) vertices from its
    Trailhead, the OSM ways it walks, and the OSM relations it is built from."""

    kind: str
    name: str
    vertices: tuple[tuple[int, Point], ...]
    way_ids: tuple[int, ...]
    parts: tuple[int, ...]


@dataclass(frozen=True)
class BuildContext:
    """What a source may build from: the snapshot ingest read, the relation
    DayHikes it kept, and the main walk of any relation."""

    snapshot: OsmSnapshot
    dayhikes: Sequence[DayHike]
    main_walk: Callable[[OsmRelation], MainWalk]


BuiltHikeSource = Callable[[BuildContext], Iterable[BuiltWalk]]


def built_dayhike(
    walk: BuiltWalk, snapshot: OsmSnapshot, amenity_index
) -> tuple[DayHike, list[tuple[int, float]]]:
    """The DayHike of ``walk`` and the (way id, metres walked) of each way it
    walks, first walked first."""
    route, walked = route_of(walk.vertices, walk.way_ids, snapshot.ways)
    hike = _dayhike(
        built_id(route),
        walk.name,
        walk.kind,
        walk.vertices,
        _strictest_sac_scale(walked, snapshot.ways),
        snapshot,
        amenity_index,
        kind=walk.kind,
        parts=walk.parts,
        route=route,
    )
    return hike, list(walked.items())


def built_id(route: str) -> int:
    """The Catalog id of the built DayHike that walks ``route``."""
    digest = int.from_bytes(hashlib.sha256(route.encode()).digest()[:8], "big")
    return BUILT_ID_MIN + digest % (BUILT_ID_MAX - BUILT_ID_MIN)


def route_of(
    vertices: Sequence[tuple[int, Point]], way_ids: Sequence[int], ways: Mapping[int, OsmWay]
) -> tuple[str, dict[int, float]]:
    """The Route text of the walk along ``vertices`` (``start:step,...``, a step
    ``{way}.{node}`` along one of ``way_ids``, negative against its node order,
    or ``g.{node}`` over a joined gap) and the metres it walks on each way."""
    on_ways: dict[int, list[tuple[int, int]]] = {}
    for way_id in dict.fromkeys(way_ids):
        for position, node_id in enumerate(ways[way_id].node_ids):
            on_ways.setdefault(node_id, []).append((way_id, position))
    steps: list[str] = []
    walked: dict[int, float] = {}
    last = None
    for (here, at), (there, to) in zip(vertices, vertices[1:]):
        way = _way_between(on_ways.get(here, []), on_ways.get(there, []))
        if way is None:
            step = "g"
        else:
            walked[abs(way)] = walked.get(abs(way), 0.0) + _haversine_m(*at, *to)
            step = str(way)
        if step == last and step != "g":
            steps[-1] = f"{step}.{there}"
        else:
            steps.append(f"{step}.{there}")
        last = step
    return f"{vertices[0][0]}:{','.join(steps)}", walked


def _way_between(here: list[tuple[int, int]], there: list[tuple[int, int]]) -> int | None:
    """The way two consecutive vertices are next to each other on, negative
    when walked against its node order; None when no way holds both."""
    best = None
    for way_id, position in here:
        for other_id, other in there:
            if other_id == way_id and (best is None or abs(other - position) < best[0]):
                best = (abs(other - position), way_id if other > position else -way_id)
    return None if best is None else best[1]
