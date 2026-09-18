"""Time Croatia ingest phases and prototype speedups. Not production code.

Read-only against OSM. Does not write data/catalog.sqlite.
"""

from __future__ import annotations

import resource
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.neighbors import BallTree

from dayhike.catalog import (
    AMENITY_KINDS,
    AMENITY_RADIUS_M,
    EARTH_RADIUS_M,
    MAX_LENGTH_M,
    MIN_LENGTH_M,
    TRAILHEAD_ACCESS_RADIUS_M,
    _drop_reason,
    _is_network_superroute,
    _linestring_length_m,
    _max_sac_scale,
    _pick_trailhead,
    _relation_vertices,
)
from dayhike.osm import parse_osm

PBF = Path("data/osm/croatia-latest.osm.pbf")
SAMPLE_HIKES = 25


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] rss={rss_mb():.0f}MB  {msg}", flush=True)


def haversine_min_np(
    lat: float, lon: float, lats: np.ndarray, lons: np.ndarray
) -> float:
    phi1 = np.radians(lat)
    phi2 = np.radians(lats)
    dphi = np.radians(lats - lat)
    dlambda = np.radians(lons - lon)
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlambda / 2) ** 2
    return float(np.min(2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(a))))


def pick_trailhead_np(
    points: list[tuple[float, float]], access_lats: np.ndarray, access_lons: np.ndarray
) -> tuple[float, float]:
    first = points[0]
    last = points[-1]
    if first == last or access_lats.size == 0:
        return first
    first_m = haversine_min_np(first[0], first[1], access_lats, access_lons)
    last_m = haversine_min_np(last[0], last[1], access_lats, access_lons)
    first_ok = first_m <= TRAILHEAD_ACCESS_RADIUS_M
    last_ok = last_m <= TRAILHEAD_ACCESS_RADIUS_M
    if first_ok and last_ok:
        return first if first_m <= last_m else last
    if last_ok:
        return last
    return first


def pick_trailhead_tree(
    points: list[tuple[float, float]], tree: BallTree
) -> tuple[float, float]:
    first = points[0]
    last = points[-1]
    if first == last:
        return first
    query = np.radians([[first[0], first[1]], [last[0], last[1]]])
    dist, _ = tree.query(query, k=1)
    first_m = float(dist[0, 0] * EARTH_RADIUS_M)
    last_m = float(dist[1, 0] * EARTH_RADIUS_M)
    first_ok = first_m <= TRAILHEAD_ACCESS_RADIUS_M
    last_ok = last_m <= TRAILHEAD_ACCESS_RADIUS_M
    if first_ok and last_ok:
        return first if first_m <= last_m else last
    if last_ok:
        return last
    return first


def amenity_facts_tree(
    lat: float,
    lon: float,
    tree: BallTree,
    amenity_ids: np.ndarray,
    kinds: np.ndarray,
) -> dict[str, tuple[int, float | None]]:
    query = np.radians([[lat, lon]])
    indices, distances = tree.query_radius(
        query, r=AMENITY_RADIUS_M / EARTH_RADIUS_M, return_distance=True
    )
    hits = indices[0]
    dists_m = distances[0] * EARTH_RADIUS_M
    by_kind: dict[str, dict[int, float]] = {kind: {} for kind in AMENITY_KINDS}
    for idx, dist_m in zip(hits, dists_m):
        kind = kinds[idx]
        amenity_id = int(amenity_ids[idx])
        prev = by_kind[kind].get(amenity_id)
        if prev is None or dist_m < prev:
            by_kind[kind][amenity_id] = float(dist_m)
    out: dict[str, tuple[int, float | None]] = {}
    for kind in AMENITY_KINDS:
        values = by_kind[kind]
        if not values:
            out[kind] = (0, None)
        else:
            out[kind] = (len(values), min(values.values()))
    return out


def amenity_facts_np(
    lat: float,
    lon: float,
    groups: list[tuple[str, np.ndarray, np.ndarray]],
) -> dict[str, tuple[int, float | None]]:
    distances: dict[str, list[float]] = {kind: [] for kind in AMENITY_KINDS}
    for kind, lats, lons in groups:
        if lats.size == 0:
            continue
        distance_m = haversine_min_np(lat, lon, lats, lons)
        if distance_m <= AMENITY_RADIUS_M:
            distances[kind].append(distance_m)
    out: dict[str, tuple[int, float | None]] = {}
    for kind in AMENITY_KINDS:
        vals = distances[kind]
        out[kind] = (0, None) if not vals else (len(vals), min(vals))
    return out


@dataclass(frozen=True)
class HikeGeom:
    relation: object
    vertices: list
    points: list[tuple[float, float]]
    length_m: float
    drop_reason: str | None


def geometries(snapshot) -> list[HikeGeom]:
    out: list[HikeGeom] = []
    for relation in snapshot.relations:
        if relation.tags.get("route") != "hiking":
            continue
        vertices = _relation_vertices(relation, snapshot.nodes, snapshot.ways)
        points = [point for _, point in vertices]
        length_m = _linestring_length_m(points)
        if _is_network_superroute(relation, length_m):
            reason = "network"
        elif not (relation.tags.get("name") or "").strip():
            reason = "unnamed"
        elif length_m < MIN_LENGTH_M or length_m > MAX_LENGTH_M:
            reason = "length"
        else:
            reason = None
        out.append(HikeGeom(relation, vertices, points, length_m, reason))
    return out


def fingerprint(hike_id: int, trailhead, facts) -> tuple:
    return (
        hike_id,
        round(trailhead[0], 7),
        round(trailhead[1], 7),
        tuple((kind, facts[kind][0], None if facts[kind][1] is None else round(facts[kind][1], 2)) for kind in AMENITY_KINDS),
    )


def main() -> None:
    if not PBF.exists():
        raise SystemExit(f"missing {PBF}")

    log(f"parse {PBF} ({PBF.stat().st_size / 1e6:.0f}MB)")
    t0 = time.perf_counter()
    snapshot = parse_osm(PBF)
    parse_s = time.perf_counter() - t0
    log(
        f"parse done in {parse_s:.1f}s  nodes={len(snapshot.nodes):,}  "
        f"ways={len(snapshot.ways):,}  rels={len(snapshot.relations):,}  "
        f"access={len(snapshot.access_points):,}  amenities={len(snapshot.amenities):,}"
    )

    t0 = time.perf_counter()
    geoms = geometries(snapshot)
    geom_s = time.perf_counter() - t0
    kept_geoms = [g for g in geoms if g.drop_reason is None]
    dropped = defaultdict(int)
    for g in geoms:
        if g.drop_reason:
            dropped[g.drop_reason] += 1
    log(
        f"geometry-once {geom_s:.1f}s  hiking={len(geoms):,}  kept={len(kept_geoms):,}  "
        f"dropped={dict(dropped)}"
    )

    t0 = time.perf_counter()
    n_hiking = 0
    for relation in snapshot.relations:
        if relation.tags.get("route") != "hiking":
            continue
        n_hiking += 1
        _drop_reason(relation, snapshot.nodes, snapshot.ways)
    drop_s = time.perf_counter() - t0
    log(f"drop_reason baseline (rebuilds geometry) {drop_s:.1f}s  hiking={n_hiking:,}")

    sample = kept_geoms[:SAMPLE_HIKES]
    t0 = time.perf_counter()
    python_trailheads = [_pick_trailhead(g.points, snapshot.access_points) for g in sample]
    python_th_s = time.perf_counter() - t0
    python_th_full_s = python_th_s / len(sample) * len(kept_geoms)
    log(
        f"trailhead python sample {len(sample)} in {python_th_s:.2f}s  "
        f"extrapolated all {len(kept_geoms)} = {python_th_full_s:.0f}s"
    )

    access = np.array(snapshot.access_points, dtype=float)
    access_lats, access_lons = access[:, 0], access[:, 1]
    unique_access = np.unique(access, axis=0)
    log(
        f"access array {len(access):,}  unique={len(unique_access):,}  "
        f"dup_ratio={len(access) / max(len(unique_access), 1):.2f}"
    )

    t0 = time.perf_counter()
    np_trailheads_sample = [
        pick_trailhead_np(g.points, access_lats, access_lons) for g in sample
    ]
    np_th_sample_s = time.perf_counter() - t0
    np_th_full_est = np_th_sample_s / len(sample) * len(kept_geoms)
    log(
        f"trailhead numpy sample {len(sample)} in {np_th_sample_s:.2f}s  "
        f"extrapolated all = {np_th_full_est:.1f}s"
    )

    t0 = time.perf_counter()
    tree = BallTree(np.radians(access), metric="haversine")
    tree_build_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    tree_trailheads = [pick_trailhead_tree(g.points, tree) for g in kept_geoms]
    tree_th_s = time.perf_counter() - t0
    log(f"trailhead BallTree build {tree_build_s:.2f}s  query all {tree_th_s:.2f}s")

    t0 = time.perf_counter()
    unique_tree = BallTree(np.radians(unique_access), metric="haversine")
    unique_build_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    unique_trailheads = [pick_trailhead_tree(g.points, unique_tree) for g in kept_geoms]
    unique_th_s = time.perf_counter() - t0
    log(f"trailhead unique BallTree build {unique_build_s:.2f}s  query all {unique_th_s:.2f}s")

    amenity_lats: list[float] = []
    amenity_lons: list[float] = []
    amenity_ids: list[int] = []
    amenity_kinds: list[str] = []
    groups: list[tuple[str, np.ndarray, np.ndarray]] = []
    for i, (kind, points) in enumerate(snapshot.amenities):
        if not points:
            continue
        arr = np.array(points, dtype=float)
        groups.append((kind, arr[:, 0], arr[:, 1]))
        for lat, lon in points:
            amenity_lats.append(lat)
            amenity_lons.append(lon)
            amenity_ids.append(i)
            amenity_kinds.append(kind)
    amenity_coords = np.column_stack([amenity_lats, amenity_lons])
    amenity_ids_np = np.array(amenity_ids)
    kinds_np = np.array(amenity_kinds)
    log(
        f"amenity objects={len(snapshot.amenities):,}  vertices={len(amenity_lats):,}"
    )

    t0 = time.perf_counter()
    python_facts = []
    from dayhike.catalog import _amenity_facts_at

    for g, th in zip(sample, python_trailheads):
        facts = _amenity_facts_at(th[0], th[1], snapshot.amenities)
        python_facts.append(
            {
                "hut": (facts.hut.count, facts.hut.nearest_m),
                "parking": (facts.parking.count, facts.parking.nearest_m),
                "restaurant_cafe": (facts.restaurant_cafe.count, facts.restaurant_cafe.nearest_m),
                "drinking_water": (facts.drinking_water.count, facts.drinking_water.nearest_m),
            }
        )
    python_am_s = time.perf_counter() - t0
    python_am_full = python_am_s / len(sample) * len(kept_geoms)
    log(
        f"amenities python sample {len(sample)} in {python_am_s:.2f}s  "
        f"extrapolated all = {python_am_full:.0f}s"
    )

    t0 = time.perf_counter()
    np_facts_sample = [
        amenity_facts_np(th[0], th[1], groups) for th in np_trailheads_sample
    ]
    np_am_sample_s = time.perf_counter() - t0
    np_am_full_est = np_am_sample_s / len(sample) * len(kept_geoms)
    log(
        f"amenities numpy sample {len(sample)} in {np_am_sample_s:.2f}s  "
        f"extrapolated all = {np_am_full_est:.1f}s"
    )

    t0 = time.perf_counter()
    amenity_tree = BallTree(np.radians(amenity_coords), metric="haversine")
    am_tree_build_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    tree_facts = [
        amenity_facts_tree(th[0], th[1], amenity_tree, amenity_ids_np, kinds_np)
        for th in tree_trailheads
    ]
    am_tree_s = time.perf_counter() - t0
    log(f"amenities BallTree build {am_tree_build_s:.2f}s  query all {am_tree_s:.2f}s")

    th_mismatch_np = sum(
        a != b for a, b in zip(python_trailheads, np_trailheads_sample)
    )
    th_mismatch_tree = sum(
        a != b for a, b in zip(python_trailheads, tree_trailheads[: len(sample)])
    )
    th_mismatch_unique = sum(
        a != b for a, b in zip(tree_trailheads, unique_trailheads)
    )
    fact_mismatch_np = 0
    fact_mismatch_tree = 0
    for i, py in enumerate(python_facts):
        np_f = np_facts_sample[i]
        tr_f = tree_facts[i]
        for kind in AMENITY_KINDS:
            if py[kind][0] != np_f[kind][0]:
                fact_mismatch_np += 1
            elif py[kind][1] is not None and abs(py[kind][1] - (np_f[kind][1] or 0)) > 0.05:
                fact_mismatch_np += 1
            if py[kind][0] != tr_f[kind][0]:
                fact_mismatch_tree += 1
            elif py[kind][1] is not None and abs(py[kind][1] - (tr_f[kind][1] or 0)) > 0.05:
                fact_mismatch_tree += 1

    print()
    print("=== SUMMARY ===")
    print(f"parse_osm                 {parse_s:8.1f}s")
    print(f"geometry once (all hike)  {geom_s:8.1f}s")
    print(f"drop_reason twice geom    {drop_s:8.1f}s")
    print(f"trailhead python (est)    {python_th_full_s:8.1f}s")
    print(f"trailhead numpy (est)     {np_th_full_est:8.1f}s")
    print(f"trailhead BallTree        {tree_build_s + tree_th_s:8.1f}s")
    print(f"trailhead unique BallTree {unique_build_s + unique_th_s:8.1f}s")
    print(f"amenities python (est)    {python_am_full:8.1f}s")
    print(f"amenities numpy (est)     {np_am_full_est:8.1f}s")
    print(f"amenities BallTree        {am_tree_build_s + am_tree_s:8.1f}s")
    print(
        f"mismatches sample: trailhead np={th_mismatch_np} tree={th_mismatch_tree} "
        f"unique_vs_all={th_mismatch_unique}  facts np={fact_mismatch_np} tree={fact_mismatch_tree}"
    )
    current_post = drop_s + python_th_full_s + python_am_full
    recommended = geom_s + unique_build_s + unique_th_s + am_tree_build_s + am_tree_s
    print(f"current post-parse (est)  {current_post:8.1f}s")
    print(f"geom-once+BallTree (meas) {recommended:8.1f}s")
    print(f"parse stays               {parse_s:8.1f}s")
    print(f"end-to-end current (est)  {parse_s + current_post:8.1f}s")
    print(f"end-to-end recommended    {parse_s + recommended:8.1f}s")


if __name__ == "__main__":
    main()
