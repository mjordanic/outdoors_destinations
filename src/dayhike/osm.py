from __future__ import annotations

import re
from array import array
from bisect import bisect_left
from collections.abc import Iterable, Iterator, Mapping
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


@dataclass
class OsmSnapshot:
    nodes: Mapping[int, tuple[float, float]] = field(default_factory=dict)  # a NodeStore's, from a work file
    ways: dict[int, OsmWay] = field(default_factory=dict)
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
