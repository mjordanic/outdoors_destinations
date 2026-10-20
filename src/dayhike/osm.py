from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import osmium

HUT_TOURISM = frozenset({"alpine_hut", "wilderness_hut"})
RESTAURANT_CAFE_AMENITY = frozenset({"restaurant", "cafe"})
DRIVEABLE_HIGHWAY = frozenset(
    {
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
        "tertiary",
        "tertiary_link",
        "unclassified",
        "residential",
        "living_street",
        "service",
        "road",
        "track",
    }
)
_MEMBER_TYPE = {
    "n": "node",
    "w": "way",
    "r": "relation",
    "node": "node",
    "way": "way",
    "relation": "relation",
}
# OSM ele is free text: "300 m", "~1200", "1,5". Take the first number or skip.
_ELE_NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


@dataclass(frozen=True)
class OsmWay:
    node_ids: tuple[int, ...]
    tags: dict[str, str]


@dataclass(frozen=True)
class OsmRelation:
    id: int
    members: tuple[tuple[str, int], ...]
    tags: dict[str, str]


@dataclass
class OsmSnapshot:
    nodes: dict[int, tuple[float, float]] = field(default_factory=dict)
    ways: dict[int, OsmWay] = field(default_factory=dict)
    relations: list[OsmRelation] = field(default_factory=list)
    amenities: list[tuple[str, tuple[tuple[float, float], ...]]] = field(
        default_factory=list
    )
    node_elevations: dict[int, float] = field(default_factory=dict)
    access_points: list[tuple[float, float]] = field(default_factory=list)

    def merge(self, other: OsmSnapshot) -> None:
        self.nodes.update(other.nodes)
        self.ways.update(other.ways)
        relations = {rel.id: rel for rel in self.relations}
        relations.update({rel.id: rel for rel in other.relations})
        self.relations = list(relations.values())
        amenities = dict.fromkeys(self.amenities)
        amenities.update(dict.fromkeys(other.amenities))
        self.amenities = list(amenities)
        self.node_elevations.update(other.node_elevations)
        access_points = dict.fromkeys(self.access_points)
        access_points.update(dict.fromkeys(other.access_points))
        self.access_points = list(access_points)


def parse_osm(path: Path) -> OsmSnapshot:
    snapshot = OsmSnapshot()
    pending_relation_amenities: list[tuple[str, tuple[tuple[str, int], ...]]] = []
    for obj in osmium.FileProcessor(str(path)).with_locations():
        if obj.is_node():
            _take_node(snapshot, obj)
        elif obj.is_way():
            _take_way(snapshot, obj)
        elif obj.is_relation():
            members = tuple(
                (_MEMBER_TYPE.get(str(member.type), str(member.type)), int(member.ref))
                for member in obj.members
            )
            tags = dict(obj.tags)
            snapshot.relations.append(OsmRelation(int(obj.id), members, tags))
            kind = amenity_kind(tags)
            if kind is not None:
                pending_relation_amenities.append((kind, members))
    for kind, members in pending_relation_amenities:
        points = _member_points(snapshot, members)
        if points:
            snapshot.amenities.append((kind, points))
            if kind == "parking":
                snapshot.access_points.extend(points)
    return snapshot


def amenity_kind(tags: dict[str, str]) -> str | None:
    if tags.get("tourism") in HUT_TOURISM:
        return "hut"
    if tags.get("tourism") == "viewpoint":
        return "viewpoint"
    if tags.get("natural") == "peak":
        return "peak"
    amenity = tags.get("amenity")
    if amenity == "parking":
        return "parking"
    if amenity in RESTAURANT_CAFE_AMENITY:
        return "restaurant_cafe"
    if amenity == "drinking_water":
        return "drinking_water"
    if amenity == "shelter":
        return "shelter"
    if tags.get("drinking_water") == "no":
        return None
    if tags.get("natural") == "spring":
        return "spring"
    if tags.get("man_made") == "water_tap":
        return "water_tap"
    if amenity == "water_point":
        return "water_point"
    return None


def _parse_ele_m(raw: str | None) -> float | None:
    if raw is None:
        return None
    match = _ELE_NUMBER.search(raw.strip())
    if match is None:
        return None
    return float(match.group(0).replace(",", "."))


def _take_node(snapshot: OsmSnapshot, obj: object) -> None:
    location = obj.location
    if not location.valid():
        return
    point = (float(location.lat), float(location.lon))
    node_id = int(obj.id)
    snapshot.nodes[node_id] = point
    tags = dict(obj.tags)
    kind = amenity_kind(tags)
    if kind is not None:
        snapshot.amenities.append((kind, (point,)))
        if kind == "parking":
            snapshot.access_points.append(point)
    if tags.get("highway") in DRIVEABLE_HIGHWAY:
        snapshot.access_points.append(point)
    elevation_m = _parse_ele_m(tags.get("ele"))
    if elevation_m is not None:
        snapshot.node_elevations[node_id] = elevation_m


def _take_way(snapshot: OsmSnapshot, obj: object) -> None:
    node_ids: list[int] = []
    points: list[tuple[float, float]] = []
    for node in obj.nodes:
        node_ids.append(int(node.ref))
        loc = node.location
        if loc.valid():
            points.append((float(loc.lat), float(loc.lon)))
    tags = dict(obj.tags)
    snapshot.ways[int(obj.id)] = OsmWay(tuple(node_ids), tags)
    kind = amenity_kind(tags)
    if kind is not None and points:
        snapshot.amenities.append((kind, tuple(points)))
        if kind == "parking":
            snapshot.access_points.extend(points)
    if tags.get("highway") in DRIVEABLE_HIGHWAY:
        snapshot.access_points.extend(points)


def _member_points(
    snapshot: OsmSnapshot, members: tuple[tuple[str, int], ...]
) -> tuple[tuple[float, float], ...]:
    points: list[tuple[float, float]] = []
    for member_type, member_id in members:
        if member_type == "node":
            point = snapshot.nodes.get(member_id)
            if point is not None:
                points.append(point)
        elif member_type == "way":
            way = snapshot.ways.get(member_id)
            if way is None:
                continue
            for node_id in way.node_ids:
                point = snapshot.nodes.get(node_id)
                if point is not None:
                    points.append(point)
    return tuple(points)
