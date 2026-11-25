"""Places to stay and restaurants, cafes and pubs near each place, counted from OSM extracts (ADR-0084).

The counts are the values of two TownTraits, places to stay (stays) and eats, and let a small
place with beds be a Base (``dayhike.bases``).
"""

from __future__ import annotations

import json
import math
import shutil
import sqlite3
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import osmium
from scipy.spatial import cKDTree

from dayhike.catalog import EARTH_RADIUS_M
from dayhike.parallel import parallel_map

STAYS = ("tourism", ("hotel", "guest_house", "hostel", "chalet", "apartment", "motel", "camp_site", "alpine_hut"))
EATS = ("amenity", ("restaurant", "cafe", "pub", "bar"))
KINDS = (STAYS, EATS)  # kind 0 is a place to stay, kind 1 a place to eat
TOWN_RADIUS_M = 2_000  # counted within this straight line of the place's GeoNames point


@dataclass(frozen=True)
class TownCounts:
    """For each place of a ``places.sqlite``, by id: the places to stay and to eat within
    ``radius_m``; with the OSM points they were counted from (``points`` (lat, lon), ``kinds``
    0 stay, 1 eat), so another radius can be counted without reading the extracts again."""

    place_ids: np.ndarray
    stays: np.ndarray
    eats: np.ndarray
    radius_m: float
    points: np.ndarray
    kinds: np.ndarray
    sources: tuple[str, ...]

    def save(self, path: Path | str) -> Path:
        path = Path(path)
        partial = path.with_name(path.name + ".partial.npz")
        meta = {"radius_m": self.radius_m, "sources": list(self.sources), "kinds": [list(k) for k in KINDS]}
        np.savez(
            partial,
            place_ids=self.place_ids, stays=self.stays, eats=self.eats,
            points=self.points, kinds=self.kinds, meta=np.array(json.dumps(meta)),
        )  # fmt: skip
        partial.replace(path)
        return path

    @classmethod
    def load(cls, path: Path | str) -> TownCounts:
        with np.load(path) as saved:
            meta = json.loads(str(saved["meta"]))
            return cls(
                place_ids=saved["place_ids"], stays=saved["stays"], eats=saved["eats"],
                radius_m=float(meta["radius_m"]), points=saved["points"], kinds=saved["kinds"],
                sources=tuple(meta["sources"]),
            )  # fmt: skip


def count_town_amenities(
    extracts: Sequence[Path | str],
    places_path: Path | str,
    *,
    workers: int,
    radius_m: float = TOWN_RADIUS_M,
) -> TownCounts:
    """Count, for every place of ``places_path``, the OSM places to stay (``STAYS``) and to eat
    (``EATS``) within ``radius_m`` of it, read from ``extracts`` (``workers`` at a time).

    A node counts at its point, a way at the middle of its nodes, a relation at the middle of
    its member ways'; an object tagged as both a place to stay and to eat counts as each. An
    object in more than one extract counts once."""
    found = parallel_map(_amenity_points, [Path(p) for p in extracts], count=workers)
    keys = np.concatenate([k for k, _, _ in found]) if found else np.zeros(0, dtype=np.int64)
    points = np.concatenate([p for _, p, _ in found]) if found else np.zeros((0, 2))
    kinds = np.concatenate([k for _, _, k in found]) if found else np.zeros(0, dtype=np.uint8)
    _, first = np.unique(np.column_stack([keys, kinds]), axis=0, return_index=True)
    first = np.sort(first)
    return count_near(points[first], kinds[first], places_path, radius_m, workers=workers,
                      sources=tuple(Path(p).name for p in extracts))  # fmt: skip


def count_near(
    points: np.ndarray,
    kinds: np.ndarray,
    places_path: Path | str,
    radius_m: float,
    *,
    workers: int,
    sources: tuple[str, ...] = (),
) -> TownCounts:
    """The ``TownCounts`` of the places of ``places_path`` within ``radius_m`` of ``points``."""
    db = sqlite3.connect(f"file:{places_path}?mode=ro", uri=True)
    try:
        rows = db.execute("SELECT id, lat, lon FROM places ORDER BY id").fetchall()
    finally:
        db.close()
    place_ids = np.array([row[0] for row in rows], dtype=np.int64)
    places = _unit(np.array([row[1:] for row in rows], dtype=np.float64).reshape(-1, 2))
    chord = 2 * math.sin(radius_m / (2 * EARTH_RADIUS_M))
    counts = []
    for kind in range(len(KINDS)):
        near = points[kinds == kind]
        if not len(near) or not len(places):
            counts.append(np.zeros(len(places), dtype=np.int32))
            continue
        tree = cKDTree(_unit(near))
        counts.append(tree.query_ball_point(places, chord, return_length=True, workers=workers).astype(np.int32))
    return TownCounts(
        place_ids=place_ids, stays=counts[0], eats=counts[1], radius_m=float(radius_m),
        points=points, kinds=kinds.astype(np.uint8), sources=sources,
    )  # fmt: skip


def _amenity_points(extract: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The (type, id) keys, (lat, lon) points and kinds of the places to stay and eat in ``extract``:
    cut out with osmium-tool first (with the nodes and ways they are made of), then read; read
    whole without osmium-tool on PATH (fine for a small file, slow for a country)."""
    osmium_bin = shutil.which("osmium")
    keys, points, kinds = [], [], []
    with tempfile.TemporaryDirectory() as tmp:
        cut = extract
        if osmium_bin is not None:
            cut = Path(tmp) / "amenities.osm.pbf"
            expressions = [f"nwr/{key}={','.join(values)}" for key, values in KINDS]
            subprocess.run(
                [osmium_bin, "tags-filter", str(extract), *expressions, "-o", str(cut), "--overwrite", "--no-progress"],
                check=True,
            )
        way_middles: dict[int, tuple[float, float]] = {}
        processor = osmium.FileProcessor(str(cut)).with_locations()
        processor = processor.with_filter(osmium.filter.KeyFilter(STAYS[0], EATS[0]).enable_for(osmium.osm.NODE))
        for obj in processor:
            middle = None
            if obj.is_node():
                if obj.location.valid():
                    middle = (obj.location.lat, obj.location.lon)
                kind_of = 0
            elif obj.is_way():
                nodes = [(n.lat, n.lon) for n in obj.nodes if n.location.valid()]
                if len(nodes) > 1 and nodes[0] == nodes[-1]:
                    nodes = nodes[:-1]
                if nodes:
                    middle = tuple(np.mean(nodes, axis=0))
                    way_middles[obj.id] = middle
                kind_of = 1
            else:
                members = [way_middles[m.ref] for m in obj.members if m.type == "w" and m.ref in way_middles]
                if members:
                    middle = tuple(np.mean(members, axis=0))
                kind_of = 2
            if middle is None:
                continue
            for kind, (key, values) in enumerate(KINDS):
                if obj.tags.get(key) in values:
                    keys.append(obj.id * 4 + kind_of)
                    points.append(middle)
                    kinds.append(kind)
    return (
        np.array(keys, dtype=np.int64),
        np.array(points, dtype=np.float64).reshape(-1, 2),
        np.array(kinds, dtype=np.uint8),
    )


def _unit(points: np.ndarray) -> np.ndarray:
    lat, lon = np.radians(points[:, 0]), np.radians(points[:, 1])
    return np.column_stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])
