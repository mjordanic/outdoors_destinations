"""Named-trail hikes: same-name paths in no hiking relation (ADR-0066).

In the US most trails are ``highway=path|footway|bridleway`` ways that share
a ``name`` and are in no route relation. ``named_trails`` joins the trail ways
(``is_trail_way``) of one name that share a node, or whose ends lie within
``GAP_JOIN_M``, into one piece, walks each piece as a relation's member ways
are walked (``assemble_main_walk``) and keeps the walks of at least
``MIN_LENGTH_M`` that are mostly a walkers' trail (``drop_reason``) and no
Twin of a relation DayHike. It reads the trail ways only a snapshot read with
the ``trails`` paths level or above holds (ADR-0065), so at the default
``graded`` level it builds nothing.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from dayhike.built import BuildContext, BuiltWalk
from dayhike.catalog import (
    EARTH_RADIUS_M,
    GAP_JOIN_M,
    MIN_LENGTH_M,
    PLACE_RADIUS_M,
    EndChoice,
    LowerStart,
    MainWalk,
    _access_tree,
    _linestring_length_m,
    assemble_main_walk,
    first_choice,
    named_points,
)
from dayhike.osm import OsmRelation, OsmSnapshot, OsmWay
from dayhike.similarity import TWIN_SHARE, TWIN_STEP_M, _even_points, twin_groups

KIND_TRAIL = "trail"
TRAIL_HIGHWAYS = ("path", "footway", "bridleway")
# A walk with more than this share of its metres on ways of one kind below is no hike.
NOT_A_TRAIL_SHARE = 0.5
PAVED_SURFACES = frozenset(
    {"paved", "asphalt", "concrete", "concrete:plates", "concrete:lanes", "paving_stones", "sett", "bricks", "brick",
     "cobblestone", "unhewn_cobblestone", "chipseal"}
)
MOTOR_KEYS = ("motorcycle", "atv", "ohv", "motor_vehicle")
OPEN = frozenset({"yes", "designated"})
NO_ACCESS = frozenset({"private", "no"})
FOOT_ALLOWED = frozenset({"yes", "designated", "permissive"})
TRAILHEAD_M = 100.0  # a line end this close to a highway=trailhead node is where the trail starts
# A name of only these words names no trail by itself ("Red Trail", "Blue", "Trail"): a park's blaze.
COLOUR_WORDS = frozenset(
    {"red", "orange", "yellow", "green", "blue", "purple", "violet", "pink", "white", "black", "brown", "grey", "gray",
     "silver", "gold", "tan", "teal"}
)
GENERIC_WORDS = frozenset({"trail", "path", "loop", "footpath"})


@dataclass(frozen=True)
class NamedTrail:
    """One same-name piece: its name, its ways in id order, its main walk and
    the ways it walks in walk order."""

    name: str
    way_ids: tuple[int, ...]
    walk: MainWalk
    walked: tuple[int, ...]


def named_trails(context: BuildContext) -> list[BuiltWalk]:
    """The built-hike source of named-trail hikes (ADR-0038), kind ``trail``:
    one per same-name piece of trail ways in no hiking relation whose main walk
    is at least ``MIN_LENGTH_M`` and mostly a walkers' trail. A trail that is a
    Twin of a relation DayHike, or of a trail kept before it, is left out. A
    name another hike also has, or one of a colour only, gets the nearest place."""
    snapshot = context.snapshot
    walked = walk_pieces(snapshot, trail_pieces(snapshot), context.lower)
    trails, dropped = [], Counter()
    for trail in walked:
        reason = drop_reason(trail, snapshot.ways)
        if reason is None:
            trails.append(trail)
        else:
            dropped[reason] += 1
    kept = without_twins(trails, context)
    names = trail_names(kept, context)
    if context.progress is not None:
        context.progress(
            f"named trails: {len(kept)} kept, {len(trails) - len(kept)} Twins and "
            f"{sum(dropped.values())} not a walkers' trail dropped "
            f"({', '.join(f'{count} {reason}' for reason, count in sorted(dropped.items())) or 'none'})"
        )
    return [
        BuiltWalk(kind=KIND_TRAIL, name=name, vertices=tuple(trail.walk.vertices), way_ids=trail.walked, parts=())
        for trail, name in zip(kept, names)
    ]


def is_trail_way(tags: Mapping[str, str]) -> bool:
    """Whether a way may be part of a named trail: a named path, footway or
    bridleway open to walkers that is no sidewalk, crossing or other
    ``footway=*`` kind, not indoors and not a golf cart path."""
    if tags.get("highway") not in TRAIL_HIGHWAYS or not (tags.get("name") or "").strip():
        return False
    if tags.get("foot") in NO_ACCESS or (tags.get("access") in NO_ACCESS and tags.get("foot") not in FOOT_ALLOWED):
        return False
    return not (tags.get("footway") or tags.get("indoor") or tags.get("golf") or tags.get("tunnel") == "building_passage")


def drop_reason(trail: NamedTrail, ways: Mapping[int, OsmWay]) -> str | None:
    """Why ``trail`` is no hike, or None: more than ``NOT_A_TRAIL_SHARE`` of
    its walked metres are ``urban`` (paved or lit: a city or park walk), a
    ``snowmobile`` corridor not signed for walkers, open to ``motor``
    vehicles, ``bike``-first or a ski ``piste``. Counted over the whole walk,
    so a paved bridge does not drop a trail."""
    metres: Counter[str] = Counter()
    for way_id, walked_m in zip(trail.way_ids, trail.walk.walked_m):
        for reason in _not_a_trail(ways[way_id].tags):
            metres[reason] += walked_m
    limit = NOT_A_TRAIL_SHARE * sum(trail.walk.walked_m)
    return next((reason for reason in ("urban", "snowmobile", "motor", "bike", "piste") if metres[reason] > limit), None)


def _not_a_trail(tags: Mapping[str, str]) -> set[str]:
    # What a way mostly made of ways like this one would be instead of a walkers' trail.
    foot_first = tags.get("foot") == "designated" or "sac_scale" in tags
    found = set()
    if tags.get("surface") in PAVED_SURFACES or tags.get("lit") == "yes":
        found.add("urban")
    if tags.get("snowmobile") == "designated" and tags.get("foot") not in FOOT_ALLOWED and "sac_scale" not in tags:
        found.add("snowmobile")
    if any(tags.get(key) in OPEN for key in MOTOR_KEYS):
        found.add("motor")
    if (tags.get("bicycle") == "designated" or "mtb:scale" in tags) and not foot_first:
        found.add("bike")
    if "piste:type" in tags and not foot_first:
        found.add("piste")
    return found


def trail_pieces(snapshot: OsmSnapshot) -> list[tuple[str, tuple[int, ...]]]:
    """(name, way ids) of each piece: trail ways in no hiking relation of one
    name (compared without case) joined where they share a node or two of
    their ends lie within ``GAP_JOIN_M`` (the gap a main walk joins, ADR-0012).
    A piece's ways are in id order, pieces in the order of their lowest way id."""
    parent: dict[int, int] = {}

    def root(way_id: int) -> int:
        while parent[way_id] != way_id:
            parent[way_id] = parent[parent[way_id]]
            way_id = parent[way_id]
        return way_id

    def join(a: int, b: int) -> None:
        a, b = root(a), root(b)
        if a != b:
            parent[max(a, b)] = min(a, b)

    names: dict[int, str] = {}
    at: dict[tuple[str, int], int] = {}  # (name, node) -> a way of that name through the node
    for way_id in sorted(snapshot.path_ways):
        way = snapshot.ways[way_id]
        if not is_trail_way(way.tags):
            continue
        names[way_id] = way.tags["name"].strip()
        parent[way_id] = way_id
        key = names[way_id].casefold()
        for node_id in way.node_ids:
            join(at.setdefault((key, node_id), way_id), way_id)
    ends = [
        (way_id, point)
        for way_id in names
        for node_id in (snapshot.ways[way_id].node_ids[0], snapshot.ways[way_id].node_ids[-1])
        if (point := snapshot.nodes.get(node_id)) is not None
    ]
    if ends:
        from sklearn.neighbors import BallTree

        at_ends = np.radians([point for _, point in ends])
        near = BallTree(at_ends, metric="haversine").query_radius(at_ends, r=GAP_JOIN_M / EARTH_RADIUS_M)
        for (way_id, _), found in zip(ends, near):
            for other in found:
                other_id = ends[other][0]
                if names[other_id].casefold() == names[way_id].casefold():
                    join(other_id, way_id)
    pieces: dict[int, list[int]] = {}
    for way_id in names:
        pieces.setdefault(root(way_id), []).append(way_id)
    return [(names[first], tuple(ways)) for first, ways in sorted(pieces.items())]


def walk_pieces(
    snapshot: OsmSnapshot, pieces: Iterable[tuple[str, tuple[int, ...]]], lower: LowerStart | None = None
) -> list[NamedTrail]:
    """The main walk of each piece at least ``MIN_LENGTH_M`` long, started at
    the end with a trailhead node if only one has one, else by the height rule
    (``lower``, ADR-0048), else at the end nearer a trailhead node or parking."""
    trailheads = _access_tree(snapshot.trailheads)
    access = _access_tree([*snapshot.trailheads, *snapshot.access_points])
    trails: list[NamedTrail] = []
    for name, way_ids in pieces:
        ways = [snapshot.ways[way_id] for way_id in way_ids]
        if sum(_way_length_m(way, snapshot.nodes) for way in ways) < MIN_LENGTH_M:
            continue
        relation = OsmRelation(0, tuple(("way", way_id) for way_id in way_ids), {"name": name}, ("",) * len(way_ids))
        declared = first_choice(
            _trailhead_choice(trailheads), None if lower is None else lower.choice(ways, snapshot.nodes)
        )
        walk = assemble_main_walk(relation, snapshot.ways, snapshot.nodes, access, declared)
        if walk.length_m < MIN_LENGTH_M:
            continue
        order = {position: way_id for way_id, position in zip(way_ids, walk.walk_order) if position is not None}
        trails.append(NamedTrail(name, way_ids, walk, tuple(order[position] for position in sorted(order))))
    return trails


def without_twins(trails: list[NamedTrail], context: BuildContext) -> list[NamedTrail]:
    """``trails`` less each Twin of a relation DayHike or of a trail before it."""
    if not trails:
        return []
    relations = {relation.id: relation for relation in context.snapshot.relations}
    lengths = [trail.walk.length_m for trail in trails]
    given = [
        _route(context.main_walk(relations[hike.id]).vertices)
        for hike in context.dayhikes
        if hike.id in relations and TWIN_SHARE * min(lengths) <= hike.length_m <= max(lengths) / TWIN_SHARE
    ]
    # A group's root is its first row, so a group holding a relation has a root below len(given).
    groups = twin_groups([*given, *(_route(trail.walk.vertices) for trail in trails)])[len(given) :]
    kept, kept_groups = [], set()
    for trail, group in zip(trails, groups):
        if group >= len(given) and group not in kept_groups:
            kept_groups.add(int(group))
            kept.append(trail)
    return kept


def trail_names(trails: Sequence[NamedTrail], context: BuildContext) -> list[str]:
    """Each trail's label: its ways' name, followed by the nearest place
    within ``PLACE_RADIUS_M`` of its start ("Red Trail near Concord") when
    another trail or a relation DayHike has the same name or the name is a
    colour or "Trail" only (``COLOUR_WORDS``, ``GENERIC_WORDS``)."""
    counts = Counter(name.casefold() for name in [*(t.name for t in trails), *(h.name for h in context.dayhikes)])
    places = named_points(context.snapshot.places)
    labels = []
    for trail in trails:
        place = None
        if places is not None and (counts[trail.name.casefold()] > 1 or _bare_name(trail.name)):
            place = places.nearest(*trail.walk.trailhead, PLACE_RADIUS_M)
        labels.append(f"{trail.name} near {place}" if place else trail.name)
    return labels


def _bare_name(name: str) -> bool:
    # A colour or "Trail" only: a blaze that names no trail outside its park.
    words = set(re.findall(r"\w+", name.casefold()))
    return bool(words) and words <= COLOUR_WORDS | GENERIC_WORDS


def _trailhead_choice(tree) -> EndChoice | None:
    """The end within ``TRAILHEAD_M`` of a trailhead node when the other is not."""
    if tree is None:
        return None

    def choose(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float] | None:
        distance, _ = tree.query(np.radians([a, b]), k=1)
        near_a, near_b = (float(d) * EARTH_RADIUS_M <= TRAILHEAD_M for d in distance[:, 0])
        if near_a == near_b:
            return None
        return a if near_a else b

    return choose


def _way_length_m(way: OsmWay, nodes: Mapping[int, tuple[float, float]]) -> float:
    return _linestring_length_m([nodes[node_id] for node_id in way.node_ids if node_id in nodes])


def _route(vertices) -> np.ndarray:
    # The walk as (lat, lon) rows every TWIN_STEP_M, as Twins are compared.
    points = np.array([point for _, point in vertices])
    return _even_points(points[:, 0], points[:, 1], TWIN_STEP_M)
