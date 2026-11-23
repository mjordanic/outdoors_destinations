from __future__ import annotations

import json
import re
from array import array
from bisect import bisect_left
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import islice

import numpy as np

HUT_TOURISM = frozenset({"alpine_hut", "wilderness_hut"})
PLACE_KINDS = frozenset({"city", "town", "village", "hamlet"})
RESTAURANT_CAFE_AMENITY = frozenset({"restaurant", "cafe"})
_MEMBER_TYPE = {
    "n": "node",
    "w": "way",
    "r": "relation",
    "node": "node",
    "way": "way",
    "relation": "relation",
}
# OSM ele is free text: "300 m", "~1200", "1,5", "1,500", "3'937", "1200 ft".
# Take the first number or skip: a comma or apostrophe before exactly three
# digits separates thousands, else a comma is a decimal point; feet become metres.
_ELE_NUMBER = re.compile(
    r"(?P<grouped>[-+]?\d{1,3}(?:[,'’]\d{3})+(?![\d]|[,'’]\d)(?:\.\d+)?)|(?P<plain>[-+]?\d+(?:[.,]\d+)?)"
)
_ELE_FEET = re.compile(r"\s*(?:ft\b|feet\b|foot\b|['’′])", re.IGNORECASE)
FOOT_M = 0.3048


@dataclass(frozen=True)
class OsmWay:
    node_ids: tuple[int, ...]
    tags: dict[str, str]


@dataclass(frozen=True)
class OsmRelation:
    id: int
    members: tuple[tuple[str, int], ...]
    tags: dict[str, str]
    # OSM member roles, one per member, in member order ("" when unset).
    roles: tuple[str, ...] = ()


class NodeStore:
    """Nodes (id, lat, lon, ele) as sorted arrays, read like the dicts of an
    ``OsmSnapshot``: ``points`` maps an id to its (lat, lon), ``elevations``
    an id to its ele where it has one. Europe's work file holds ~62 M nodes:
    as a dict of tuples about 10 GB, here 32 bytes a node."""

    LOAD_CHUNK = 1_000_000

    def __init__(self, ids: np.ndarray, lat: array, lon: array, ele: np.ndarray) -> None:
        has_ele = ~np.isnan(ele)
        self.points = _Points(ids, lat, lon)
        self.elevations = _Elevations(ids[has_ele], array("d", ele[has_ele].tobytes()))

    @classmethod
    def from_rows(cls, rows: Iterable[tuple[int, float, float, float | None]]) -> NodeStore:
        """From (id, lat, lon, ele or None) rows in any order, read a chunk at a time."""
        rows = iter(rows)
        ids, lat, lon, ele = [], array("d"), array("d"), []
        while chunk := list(islice(rows, cls.LOAD_CHUNK)):
            ids.append(np.fromiter((row[0] for row in chunk), dtype=np.int64, count=len(chunk)))
            values = np.array(chunk, dtype=float)  # an ele of None reads as NaN
            lat.frombytes(values[:, 1].tobytes())
            lon.frombytes(values[:, 2].tobytes())
            ele.append(values[:, 3])
        if not ids:
            return cls(np.empty(0, dtype=np.int64), array("d"), array("d"), np.empty(0))
        all_ids = np.concatenate(ids)
        order = np.argsort(all_ids, kind="stable")
        if np.any(order != np.arange(len(order))):
            lat = array("d", np.frombuffer(lat, dtype=float)[order].tolist())
            lon = array("d", np.frombuffer(lon, dtype=float)[order].tolist())
        return cls(all_ids[order], lat, lon, np.concatenate(ele)[order])


class _SortedIds(Mapping):
    """Sorted ids found by a bucket of the id range, then a C bisect within it:
    a lookup is a few Python ops, not a numpy call per id."""

    def __init__(self, ids: np.ndarray) -> None:
        self._ids = array("q", ids.astype(np.int64).tobytes())
        self._first, self._shift, self._buckets = 0, 0, 0
        self._starts = array("q", [0])
        if len(ids):
            self._first = int(ids[0])
            span = int(ids[-1]) - self._first + 1
            # Buckets a few ids wide on average: under 4 bytes a node.
            self._shift = (span // len(ids)).bit_length() + 1
            self._buckets = (span >> self._shift) + 1
            edges = self._first + (np.arange(self._buckets + 1, dtype=np.int64) << self._shift)
            self._starts = array("q", ids.searchsorted(edges).astype(np.int64).tobytes())

    def _at(self, node_id) -> int:
        bucket = (node_id - self._first) >> self._shift
        if 0 <= bucket < self._buckets:
            end = self._starts[bucket + 1]
            at = bisect_left(self._ids, node_id, self._starts[bucket], end)
            if at < end and self._ids[at] == node_id:
                return at
        return -1

    def __contains__(self, node_id) -> bool:
        return self._at(node_id) >= 0

    def __iter__(self) -> Iterator[int]:
        return iter(self._ids)

    def __len__(self) -> int:
        return len(self._ids)


class _Points(_SortedIds):
    def __init__(self, ids: np.ndarray, lat: array, lon: array) -> None:
        super().__init__(ids)
        self._lat, self._lon = lat, lon

    def __getitem__(self, node_id) -> tuple[float, float]:
        at = self._at(node_id)
        if at < 0:
            raise KeyError(node_id)
        return self._lat[at], self._lon[at]

    def get(self, node_id, default=None):
        at = self._at(node_id)
        return default if at < 0 else (self._lat[at], self._lon[at])


class _Elevations(_SortedIds):
    def __init__(self, ids: np.ndarray, ele: array) -> None:
        super().__init__(ids)
        self._ele = ele

    def __getitem__(self, node_id) -> float:
        at = self._at(node_id)
        if at < 0:
            raise KeyError(node_id)
        return self._ele[at]

    def get(self, node_id, default=None):
        at = self._at(node_id)
        return default if at < 0 else self._ele[at]


class WayStore(_SortedIds):
    """Ways (id, node ids, tags) as flat arrays, read like the dict of an
    ``OsmSnapshot``: ``ways[way_id]`` is its ``OsmWay``. Every way's node ids
    are one array cut at offsets, and ways of the same tags share one dict
    (read only). Europe's work file holds ~5 M ways: as OsmWays of tuples and
    dicts about 1-2 kB each, here about 8 B a node. It iterates in load order,
    like the dict it replaces; ways ``update`` adds, ones it did not load
    (those a source read), come after."""

    def __init__(self, ids: array, offsets: array, nodes: array, tag_at: array, tags: list[dict[str, str]]) -> None:
        loaded = np.frombuffer(ids, dtype=np.int64) if len(ids) else np.empty(0, dtype=np.int64)
        order = np.argsort(loaded, kind="stable")
        super().__init__(loaded[order])
        self._position = array("q", order.astype(np.int64).tobytes())  # each sorted id's place in load order
        self._loaded, self._offsets, self._nodes, self._tag_at, self._tags = ids, offsets, nodes, tag_at, tags
        self._added: dict[int, OsmWay] = {}

    @classmethod
    def from_rows(cls, rows: Iterable[tuple[int, str, str]]) -> WayStore:
        """From work-file (id, node ids JSON, tags JSON) rows, one id once."""
        ids, offsets, nodes, tag_at = array("q"), array("q", [0]), array("q"), array("q")
        interned: dict[str, int] = {}
        tags: list[dict[str, str]] = []
        for way_id, node_ids_json, tags_json in rows:
            ids.append(way_id)
            nodes.extend(json.loads(node_ids_json))
            offsets.append(len(nodes))
            at = interned.setdefault(tags_json, len(tags))
            if at == len(tags):
                tags.append(json.loads(tags_json))
            tag_at.append(at)
        return cls(ids, offsets, nodes, tag_at, tags)

    def __getitem__(self, way_id) -> OsmWay:
        at = self._at(way_id)
        if at < 0:
            return self._added[way_id]
        position = self._position[at]
        return OsmWay(
            tuple(self._nodes[self._offsets[position] : self._offsets[position + 1]]),
            self._tags[self._tag_at[position]],
        )

    def __contains__(self, way_id) -> bool:
        return self._at(way_id) >= 0 or way_id in self._added

    def __iter__(self) -> Iterator[int]:
        yield from self._loaded
        yield from self._added

    def __len__(self) -> int:
        return len(self._loaded) + len(self._added)

    def update(self, ways: Mapping[int, OsmWay]) -> None:
        self._added.update(ways)


class WaysWithout(Mapping[int, OsmWay]):
    """``ways`` read without the ways ``left_out``, in the same order: a way
    is built only when it is read, none are copied."""

    def __init__(self, ways: Mapping[int, OsmWay], left_out: Collection[int]) -> None:
        self._ways, self._left_out = ways, left_out

    def __getitem__(self, way_id) -> OsmWay:
        if way_id in self._left_out:
            raise KeyError(way_id)
        return self._ways[way_id]

    def __contains__(self, way_id) -> bool:
        return way_id not in self._left_out and way_id in self._ways

    def __iter__(self) -> Iterator[int]:
        return (way_id for way_id in self._ways if way_id not in self._left_out)

    def __len__(self) -> int:
        return len(self._ways) - sum(1 for way_id in self._left_out if way_id in self._ways)


class WayEnds:
    """The ways of a snapshot by their end nodes, as two sorted arrays: about
    16 B a way end, so Europe's ~5 M ways take ~160 MB."""

    def __init__(self, ways: Mapping[int, OsmWay]) -> None:
        if isinstance(ways, WayStore):
            ids = np.frombuffer(ways._loaded, dtype=np.int64) if len(ways._loaded) else np.empty(0, dtype=np.int64)
            offsets = np.frombuffer(ways._offsets, dtype=np.int64)
            nodes = np.frombuffer(ways._nodes, dtype=np.int64) if len(ways._nodes) else np.empty(0, dtype=np.int64)
            has_nodes = offsets[1:] > offsets[:-1]
            ids = ids[has_nodes]
            first, last = nodes[offsets[:-1][has_nodes]], nodes[offsets[1:][has_nodes] - 1]
            rest = ways._added
        else:
            ids, first, last, rest = np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64), ways
        extra = [(way_id, way.node_ids[0], way.node_ids[-1]) for way_id, way in rest.items() if way.node_ids]
        if extra:
            more = np.array(extra, dtype=np.int64)
            ids, first, last = np.concatenate([ids, more[:, 0]]), np.concatenate([first, more[:, 1]]), np.concatenate([last, more[:, 2]])
        ends, owners = np.concatenate([first, last]), np.concatenate([ids, ids])
        order = np.argsort(ends, kind="stable")
        self._ends, self._owners = ends[order], owners[order]

    def ways_at(self, node_ids: Sequence[int]) -> set[int]:
        """The ids of the ways that start or end at any of ``node_ids``."""
        wanted = np.asarray(node_ids, dtype=np.int64)
        starts = self._ends.searchsorted(wanted, side="left")
        stops = self._ends.searchsorted(wanted, side="right")
        hit = stops > starts
        return {int(way_id) for start, stop in zip(starts[hit], stops[hit]) for way_id in self._owners[start:stop]}


@dataclass
class OsmSnapshot:
    nodes: Mapping[int, tuple[float, float]] = field(default_factory=dict)  # a NodeStore's, from a work file
    ways: dict[int, OsmWay] | WayStore = field(default_factory=dict)  # a WayStore, from a work file
    relations: list[OsmRelation] = field(default_factory=list)
    amenities: list[tuple[str, tuple[tuple[float, float], ...]]] = field(
        default_factory=list
    )
    node_elevations: Mapping[int, float] = field(default_factory=dict)
    access_points: list[tuple[float, float]] = field(default_factory=list)
    # (lat, lon, name) of named settlements, and of every named point (places and amenities).
    places: list[tuple[float, float, str]] = field(default_factory=list)
    named_points: list[tuple[float, float, str]] = field(default_factory=list)
    # (lat, lon, name) of named Peaks, Huts, Viewpoints and lakes a NetworkHike may go to (ADR-0039).
    destinations: list[tuple[float, float, str]] = field(default_factory=list)
    # (lat, lon, name) of named guideposts: they name signposts (ADR-0039).
    guideposts: list[tuple[float, float, str]] = field(default_factory=list)
    # (lat, lon) of highway=trailhead nodes, read with the paths option: where a named trail starts (ADR-0066).
    trailheads: list[tuple[float, float]] = field(default_factory=list)
    # Ids of the ways in ``ways`` no hiking relation lists, read with the paths option (ADR-0065):
    # only the TrailGraph walks them, no DayHike does.
    path_ways: frozenset[int] = frozenset()
    # ``ways`` by their end nodes, built when a main walk first needs it (``ingest._way_ends``).
    way_ends: WayEnds | None = field(default=None, repr=False, compare=False)


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
    if tags.get("place") in PLACE_KINDS and tags.get("name"):
        return "place"
    return None


def _parse_ele_m(raw: str | None) -> float | None:
    if raw is None:
        return None
    match = _ELE_NUMBER.search(raw)
    if match is None:
        return None
    if match["grouped"]:
        value = float(re.sub(r"[,'’]", "", match["grouped"]))
    else:
        value = float(match["plain"].replace(",", "."))
    return value * FOOT_M if _ELE_FEET.match(raw, match.end()) else value
