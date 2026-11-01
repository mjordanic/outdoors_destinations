"""SuggestedLoops: RoundTrips drawn on the TrailGraph that a Ranking proposes itself (ADR-0020).

``find_loops`` runs at query time, from the start Junctions near the
Hiker's Origin, when there is one, and near the Trailheads of the best
Catalog results (ADR-0036). From each start a beam search grows
walks Segment by Segment and keeps the ``LOOP_BEAM`` whose elevation so far
is closest to the Seed's ElevationProfile, stretched to the Seed's length,
highest and lowest point and taken either way round. A walk never visits a
Junction twice, so it closes only back at its start, and it is dropped once
its way home along the network would make it longer than the length band.
The completed loops come back best first by a cheap guess at the core
Measures; the caller measures the best of them exactly. Expansions, Segments
read and a deadline bound the work of one request.
"""

from __future__ import annotations

import math
import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

from dayhike.catalog import EARTH_RADIUS_M
from dayhike.similarity import PROFILE_RANGE_FLOOR_M, TWIN_SHARE
from dayhike.trailgraph import TrailGraph, decode_coords, _haversine

LOOP_SEEDS = 30  # the best Catalog results whose Trailheads start the search
LOOP_START_M = 2_000.0  # start Junctions within this of such a Trailhead ...
LOOP_STARTS_PER_TRAILHEAD = 3  # ... the nearest this many
LOOP_ORIGIN_M = 5_000.0  # start Junctions within this of the Origin, searched first (ADR-0036) ...
LOOP_ORIGIN_STARTS = 20  # ... the nearest this many
LOOP_STARTS = 40  # and at most this many in all
LOOP_BAND = (0.75, 1.25)  # a loop's length against the Seed's
LOOP_MIN_M = 1_000.0  # the Catalog's shortest DayHike
LOOP_MAX_M = 40_000.0  # no loop is searched longer, to bound the search on one vCPU: a multi-day Seed gets none (ADR-0035)
LOOP_BEAM = 64  # partial loops kept per start
LOOP_SEGMENTS = 400  # the most Segments one loop walks
LOOP_KEPT = 200  # completed loops kept per start, best first
LOOP_EXPANSIONS = 400_000  # walks tried in one search, all starts together
LOOP_READ = 60_000  # Segments one search may read
_SAMPLES = 8  # heights per Segment the beam compares, equally spaced, both ends


@dataclass(frozen=True)
class LoopTarget:
    """What the beam steers by: the Seed's length, highest and lowest point,
    metres climbed (the larger of climb and descent) and ProfileShape (NaN or None missing)."""

    length_m: float
    highest_m: float
    lowest_m: float
    gain_m: float
    shape: np.ndarray | None


@dataclass(frozen=True)
class FoundLoop:
    """A loop the beam closed: its start Junction, the Segment ids walked in
    order, its guessed similarity (higher is better) and the start's drive hours."""

    start: int
    segments: tuple[int, ...]
    guess: float
    drive_hours: float | None


@dataclass
class _Read:
    """The Segments read so far in one search, by local index."""

    index: dict[int, int]
    ids: list[int]
    a: list[int]
    b: list[int]
    length: list[float]
    climb: list[tuple[float, float]]  # a to b, b to a
    samples: list[np.ndarray]  # _SAMPLES heights from a to b


def find_loops(
    graph: TrailGraph,
    target: LoopTarget,
    trailheads: Sequence[tuple[float, float]],
    *,
    limit: int,
    deadline: float,
    reach: Callable[[list[tuple[float, float]]], list[float | None]] | None = None,
    origin: tuple[float, float] | None = None,
) -> list[FoundLoop]:
    """The ``limit`` loops most like ``target`` by their guess, from Junctions
    near ``origin`` (lat, lon; within ``LOOP_ORIGIN_M``, searched first) and
    near ``trailheads`` (best first), best first; of loops that share a Twin's
    share of their Segments' length only the best.

    ``graph`` is a serving export's TrailGraph (it reads each Segment's climb
    each way). ``reach`` gives the drive hours to each start, None out of
    reach; such starts are left out. The search stops at ``deadline`` (``time.monotonic``)
    or after ``LOOP_EXPANSIONS`` walks with what it has found.
    """
    low, high = target.length_m * LOOP_BAND[0], min(target.length_m * LOOP_BAND[1], LOOP_MAX_M)
    low = max(low, LOOP_MIN_M)
    if not math.isfinite(target.length_m) or low >= high:
        return []
    with sqlite3.connect(f"file:{graph.path}?mode=ro", uri=True) as db:
        near = [] if origin is None else [(origin, LOOP_ORIGIN_M, LOOP_ORIGIN_STARTS)]
        starts = _starts(db, near + [(trailhead, LOOP_START_M, LOOP_STARTS_PER_TRAILHEAD) for trailhead in trailheads])
        hours: list[float | None] = [None] * len(starts)
        if reach is not None and starts:
            hours = reach([(lat, lon) for _, lat, lon in starts])
        read = _Read({}, [], [], [], [], [], [])
        found: list[FoundLoop] = []
        budget = LOOP_EXPANSIONS
        for (junction, lat, lon), drive in zip(starts, hours):
            if (reach is not None and drive is None) or budget <= 0 or time.monotonic() >= deadline:
                continue
            local = _local(db, read, lat, lon, high / 2)
            if local is None:
                continue
            loops, used = _beam(read, local, junction, target, low, high, budget, deadline)
            budget -= used
            found += [FoundLoop(junction, path, guess, drive) for path, guess in loops]
    found.sort(key=lambda loop: -loop.guess)
    # Of loops that share at least a Twin's share of each one's length, keep the best guess.
    metres = {segment: read.length[read.index[segment]] for loop in found for segment in loop.segments}
    kept: list[tuple[FoundLoop, set[int], float]] = []
    for loop in found:
        if len(kept) >= limit:
            break
        walked = set(loop.segments)
        total = sum(metres[segment] for segment in loop.segments)
        if not any(
            sum(metres[segment] for segment in walked & other) >= TWIN_SHARE * max(total, other_total)
            for _, other, other_total in kept
        ):
            kept.append((loop, walked, total))
    return [loop for loop, _, _ in kept]


def _starts(
    db: sqlite3.Connection, places: Sequence[tuple[tuple[float, float], float, int]]
) -> list[tuple[int, float, float]]:
    """(Junction id, lat, lon) of the start Junctions: per place ((lat, lon),
    metres, count) in order, the count nearest within its metres where two
    trail Segments meet, at most ``LOOP_STARTS`` in all."""
    starts: dict[int, tuple[float, float]] = {}
    for (lat, lon), radius_m, count in places:
        if len(starts) >= LOOP_STARTS:
            break
        grow_lat = math.degrees(radius_m / EARTH_RADIUS_M)
        grow_lon = grow_lat / max(math.cos(math.radians(lat)), 1e-6)
        near = db.execute(
            "SELECT DISTINCT j.id, j.latitude, j.longitude FROM segment_boxes b JOIN segments s ON s.id = b.id "
            "JOIN junctions j ON j.id IN (s.a, s.b) WHERE s.kind != 'link' AND j.degree >= 2 "
            "AND b.north >= ? AND b.south <= ? AND b.east >= ? AND b.west <= ?",
            (lat - grow_lat, lat + grow_lat, lon - grow_lon, lon + grow_lon),
        ).fetchall()
        if not near:
            continue
        points = np.array([row[1:] for row in near], dtype=float)
        metres = _haversine(np.full(len(near), lat), np.full(len(near), lon), points[:, 0], points[:, 1])
        taken = 0
        for at in np.argsort(metres, kind="stable").tolist():
            if metres[at] > radius_m or taken >= count or len(starts) >= LOOP_STARTS:
                break
            junction = int(near[at][0])
            if junction not in starts:
                starts[junction] = (float(near[at][1]), float(near[at][2]))
                taken += 1
    return [(junction, lat, lon) for junction, (lat, lon) in starts.items()]


def _local(db: sqlite3.Connection, read: _Read, lat: float, lon: float, radius_m: float) -> np.ndarray | None:
    """Local indexes of the trail and gap Segments whose box comes within
    ``radius_m`` of the point, reading those not read yet; None past ``LOOP_READ``."""
    grow_lat = math.degrees(radius_m / EARTH_RADIUS_M)
    grow_lon = grow_lat / max(math.cos(math.radians(lat)), 1e-6)
    ids = [
        segment
        for (segment,) in db.execute(
            "SELECT b.id FROM segment_boxes b JOIN segments s ON s.id = b.id WHERE s.kind != 'link' "
            "AND b.north >= ? AND b.south <= ? AND b.east >= ? AND b.west <= ?",
            (lat - grow_lat, lat + grow_lat, lon - grow_lon, lon + grow_lon),
        )
    ]
    missing = [segment for segment in ids if segment not in read.index]
    if len(read.ids) + len(missing) > LOOP_READ:
        return None
    for start in range(0, len(missing), 900):
        batch = missing[start : start + 900]
        for segment, a, b, length, climb_ab, climb_ba, coords, ele in db.execute(
            "SELECT id, a, b, length_m, climb_ab, climb_ba, coords, ele FROM segments "
            f"WHERE id IN ({','.join('?' * len(batch))})",
            batch,
        ):
            points = decode_coords(coords)
            heights = np.frombuffer(ele, dtype="<f4").astype(float)
            steps = _haversine(points[:-1, 0], points[:-1, 1], points[1:, 0], points[1:, 1])
            along = np.concatenate([[0.0], np.cumsum(steps)])
            known = np.isfinite(heights)
            samples = np.full(_SAMPLES, np.nan)
            if known.sum() >= 2 and along[-1] > 0:
                samples = np.interp(np.linspace(0.0, along[-1], _SAMPLES), along[known], heights[known])
            elif known.any():
                samples[:] = heights[known].mean()
            read.index[segment] = len(read.ids)
            read.ids.append(segment)
            read.a.append(a)
            read.b.append(b)
            read.length.append(max(float(length), 1e-3))
            read.climb.append(tuple(math.nan if climb is None else climb for climb in (climb_ab, climb_ba)))
            read.samples.append(samples)
    return np.array([read.index[segment] for segment in ids], dtype=np.int64)


def _beam(
    read: _Read,
    local: np.ndarray,
    start: int,
    target: LoopTarget,
    low: float,
    high: float,
    budget: int,
    deadline: float,
) -> tuple[list[tuple[tuple[int, ...], float]], int]:
    """The loops through ``start`` within ``low``..``high`` metres the beam
    closes, as (Segment ids, guess), best first, and the expansions it used."""
    a = np.array(read.a, dtype=np.int64)[local]
    b = np.array(read.b, dtype=np.int64)[local]
    length = np.array(read.length)[local]
    climb = np.array(read.climb, dtype=float).reshape(-1, 2)[local]
    samples = np.array(read.samples, dtype=float).reshape(-1, _SAMPLES)[local]
    nodes, ends = np.unique(np.concatenate([a, b]), return_inverse=True)
    la, lb = ends[: len(local)], ends[len(local) :]
    where = np.searchsorted(nodes, start)
    if where >= len(nodes) or nodes[where] != start:
        return [], 0
    home = int(where)
    # The way home from every Junction: Dijkstra over the shortest Segment between each pair.
    pair = np.minimum(la, lb) * len(nodes) + np.maximum(la, lb)
    order = np.lexsort((length, pair))
    first = order[np.concatenate([[True], pair[order][1:] != pair[order][:-1]])]
    first = first[la[first] != lb[first]]
    weights = csr_matrix(
        (
            np.concatenate([length[first], length[first]]),
            (np.concatenate([la[first], lb[first]]), np.concatenate([lb[first], la[first]])),
        ),
        shape=(len(nodes), len(nodes)),
    )
    back = dijkstra(weights, directed=True, indices=home, limit=high / 2)
    # Every way along a Segment: from, to, Segment (local), forwards; rings only close at once.
    ring = la == lb
    steps = np.flatnonzero(~ring)
    leave = np.concatenate([la[steps], lb[steps]])
    reach = np.concatenate([lb[steps], la[steps]])
    segment = np.concatenate([steps, steps])
    forward = np.concatenate([np.ones(len(steps), bool), np.zeros(len(steps), bool)])
    keep = np.isfinite(back[reach]) & np.isfinite(back[leave])
    leave, reach, segment, forward = leave[keep], reach[keep], segment[keep], forward[keep]
    by = np.argsort(leave, kind="stable")
    leave, reach, segment, forward = leave[by], reach[by], segment[by], forward[by]
    first_out = np.searchsorted(leave, np.arange(len(nodes) + 1))
    heights = np.where(forward[:, None], samples[segment], samples[segment][:, ::-1])
    rise = np.where(forward, climb[segment, 0], climb[segment, 1])
    fall = np.where(forward, climb[segment, 1], climb[segment, 0])

    profile = _target_profile(target)
    closed: list[tuple[np.ndarray, float]] = []  # local Segment indexes walked, guess
    # A ring at the start is a loop of one Segment.
    for s in np.flatnonzero(ring & (la == home)).tolist():
        if low <= length[s] <= high:
            error = _error(profile, np.zeros(1), length[s : s + 1], samples[s : s + 1], target.length_m)[0]
            known = samples[s][np.isfinite(samples[s])]
            top, bottom = (known.max(), known.min()) if known.size else (np.nan, np.nan)
            guess = _guess(target, profile, length[s], error, top, bottom, *np.nan_to_num(climb[s]))
            closed.append((np.array([s]), guess))

    # The beam: per walk its Junctions and steps so far, metres, height error
    # against the stretched profile each way round, top, bottom, climb and descent.
    walked_nodes = np.full((1, LOOP_SEGMENTS + 1), -1, dtype=np.int64)
    walked_nodes[0, 0] = home
    walked_steps = np.full((1, LOOP_SEGMENTS), -1, dtype=np.int64)
    metres, error = np.zeros(1), np.zeros((1, 2))
    top, bottom = np.full(1, -np.inf), np.full(1, np.inf)
    up, down = np.zeros(1), np.zeros(1)
    used = 0
    for depth in range(LOOP_SEGMENTS):
        if not len(metres) or used >= budget or time.monotonic() >= deadline:
            break
        here = walked_nodes[:, depth]
        count = first_out[here + 1] - first_out[here]
        parent = np.repeat(np.arange(len(metres)), count)
        step = np.arange(count.sum()) - np.repeat(np.cumsum(count) - count, count) + np.repeat(first_out[here], count)
        used += len(step)
        to = reach[step]
        walked = metres[parent] + length[segment[step]]
        closing = to == home
        allowed = walked + back[to] <= high
        if depth == 0:
            allowed &= ~closing
        else:
            # Closing on the Segment it left by is no loop; otherwise no Junction twice.
            revisits = (walked_nodes[parent, : depth + 1] == to[:, None]).any(axis=1)
            allowed &= np.where(closing, segment[step] != segment[walked_steps[parent, 0]], ~revisits)
        parent, step, to = parent[allowed], step[allowed], to[allowed]
        walked, closing = walked[allowed], closing[allowed]
        heights_now = heights[step]
        new_error = error[parent] + _error(profile, metres[parent], length[segment[step]], heights_now, target.length_m)
        new_top = np.fmax(top[parent], np.max(np.where(np.isnan(heights_now), -np.inf, heights_now), axis=1))
        new_bottom = np.fmin(bottom[parent], np.min(np.where(np.isnan(heights_now), np.inf, heights_now), axis=1))
        new_up = up[parent] + np.nan_to_num(rise[step])
        new_down = down[parent] + np.nan_to_num(fall[step])
        for i in np.flatnonzero(closing & (walked >= low)).tolist():
            path = segment[np.append(walked_steps[parent[i], :depth], step[i])]
            guess = _guess(target, profile, walked[i], new_error[i], new_top[i], new_bottom[i], new_up[i], new_down[i])
            closed.append((path, guess))
        if len(closed) > 4 * LOOP_KEPT:
            closed.sort(key=lambda item: -item[1])
            del closed[LOOP_KEPT:]
        go = np.flatnonzero(~closing)
        if not len(go):
            break
        mismatch = np.zeros(len(go)) if profile is None else new_error[go].min(axis=1) / walked[go] / profile[2]
        overshoot = np.maximum(walked[go] + back[to[go]] - target.length_m, 0.0) / target.length_m
        best = go[np.argsort(mismatch + overshoot, kind="stable")[:LOOP_BEAM]]
        walked_nodes = walked_nodes[parent[best]]
        walked_nodes[:, depth + 1] = to[best]
        walked_steps = walked_steps[parent[best]]
        walked_steps[:, depth] = step[best]
        metres, error, top, bottom = walked[best], new_error[best], new_top[best], new_bottom[best]
        up, down = new_up[best], new_down[best]
    closed.sort(key=lambda item: -item[1])
    ids = np.array(read.ids, dtype=np.int64)[local]
    loops: list[tuple[tuple[int, ...], float]] = []
    seen: set[frozenset[int]] = set()
    for path, guess in closed:
        walked_ids = tuple(ids[path].tolist())
        if frozenset(walked_ids) not in seen:
            seen.add(frozenset(walked_ids))
            loops.append((walked_ids, guess))
        if len(loops) >= LOOP_KEPT:
            break
    return loops, used


def _target_profile(target: LoopTarget) -> tuple[np.ndarray, np.ndarray, float] | None:
    """The Seed's ElevationProfile back in metres, from its ProfileShape, highest
    and lowest point: (distance fractions, heights, height range); None without them."""
    if target.shape is None or np.isnan(target.shape).any() or not (
        math.isfinite(target.highest_m) and math.isfinite(target.lowest_m)
    ):
        return None
    scale = max(target.highest_m - target.lowest_m, PROFILE_RANGE_FLOOR_M)
    return np.linspace(0.0, 1.0, len(target.shape)), target.lowest_m + np.asarray(target.shape, float) * scale, scale


def _error(
    profile: tuple[np.ndarray, np.ndarray, float] | None,
    start_m: np.ndarray,
    length_m: np.ndarray,
    heights: np.ndarray,
    target_m: float,
) -> np.ndarray:
    """Per step (rows), metres walked times the mean height difference from the
    stretched profile walked forwards and backwards; a height missing counts none."""
    if profile is None:
        return np.zeros((len(start_m), 2))
    grid, metres, _ = profile
    at = np.clip((start_m[:, None] + np.linspace(0.0, 1.0, _SAMPLES)[None, :] * length_m[:, None]) / target_m, 0.0, 1.0)
    errors = []
    for fraction in (at, 1.0 - at):
        miss = np.abs(heights - np.interp(fraction, grid, metres))
        errors.append(np.where(np.isnan(miss), 0.0, miss).mean(axis=1) * length_m)
    return np.column_stack(errors)


def _guess(
    target: LoopTarget,
    profile: tuple[np.ndarray, np.ndarray, float] | None,
    metres: float,
    error: np.ndarray,
    top: float,
    bottom: float,
    up: float,
    down: float,
) -> float:
    """A cheap stand-in for the length and elevation Measures (raw
    scores, as ``Measure`` defines them): the mean of those both sides have."""
    scores = [min(max(metres, 1_000.0), max(target.length_m, 1_000.0)) / max(metres, target.length_m, 1_000.0)]
    gain = max(up, down)
    if math.isfinite(target.gain_m):
        scores.append(min(max(gain, 100.0), max(target.gain_m, 100.0)) / max(gain, target.gain_m, 100.0))
    for seed, loop in ((target.highest_m, top), (target.lowest_m, bottom)):
        if math.isfinite(seed) and math.isfinite(loop):
            scores.append(max(0.0, 1.0 - abs(seed - loop) / 400.0))
    if profile is not None:
        scores.append(max(0.0, 1.0 - float(np.min(error)) / metres / profile[2]))
    return float(np.mean(scores))
