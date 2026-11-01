"""DayStages: day-sized pieces of a long tour (ADR-0040).

A relation DayHike longer than ``STAGE_SPLIT_M`` is a tour of more than a day.
``split`` cuts its main walk into DayStages of ``STAGE_MIN_M`` to
``STAGE_MAX_M``, as near ``STAGE_TARGET_M`` as the cut points allow: huts
near the line first, then places, then parking. Cut points are OSM objects,
the same for every tour that passes them, so tours that share a stretch cut
it alike.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from sklearn.neighbors import BallTree

from dayhike.catalog import EARTH_RADIUS_M
from dayhike.similarity import _step_m

Point = tuple[float, float]  # (lat, lon)

STAGE_SPLIT_M = 25_000.0  # a tour longer than this is cut into DayStages
STAGE_MIN_M = 8_000.0  # a DayStage is at least this long ...
STAGE_MAX_M = 25_000.0  # ... and at most this
STAGE_TARGET_M = 16_000.0  # a day's stage: the nearer this, the better
CUT_RADIUS_M = {"hut": 200.0, "place": 500.0, "parking": 200.0}  # a cut point this close to the line can end a stage
# What ending a stage at each kind costs, in units of a stage STAGE_MIN_M off its target:
# a hut is preferred to a place a few km nearer the target, a place likewise to parking.
CUT_COST = {"hut": 0.0, "place": 0.25, "parking": 0.5}
SKIP_COST_PER_M = 1e-3  # a km of tour left out of every stage costs more than any stage


@dataclass(frozen=True)
class CutPoint:
    """Where a stage may end: a ``kind`` of ``CUT_RADIUS_M`` at (lat, lon), and its name."""

    kind: str
    lat: float
    lon: float
    name: str | None


@dataclass(frozen=True)
class DayStage:
    """One day of a tour: its (node id, point) vertices in the tour's walking
    order, its length, and the names of the cut points it starts and ends at
    (None at a tour end with no cut point)."""

    vertices: tuple[tuple[int, Point], ...]
    length_m: float
    start: str | None
    end: str | None


def split(walk: Sequence[tuple[int, Point]], points: Sequence[CutPoint]) -> list[DayStage]:
    """The DayStages of the tour along ``walk``, in walking order; none when it
    is at most ``STAGE_SPLIT_M`` long.

    Each of ``points`` within its kind's ``CUT_RADIUS_M`` of a vertex may end a
    stage at the vertex nearest it; so may the walk's two ends. Of every way to
    cut the walk there into stages of ``STAGE_MIN_M`` to ``STAGE_MAX_M``, the
    cheapest is taken: each stage costs its squared distance from
    ``STAGE_TARGET_M`` (1 at ``STAGE_MIN_M``) plus ``CUT_COST`` of the point it
    ends at (huts, then places, then parking), and each km left out of every
    stage ``SKIP_COST_PER_M``, more than a stage. A stretch with no cut point
    for more than ``STAGE_MAX_M`` gets no stage.
    """
    lat = np.array([point[0] for _, point in walk])
    lon = np.array([point[1] for _, point in walk])
    along = np.concatenate([[0.0], np.cumsum(_step_m(lat, lon))])
    if along[-1] <= STAGE_SPLIT_M:
        return []
    cuts = [(0, None), *_cuts(lat, lon, points), (len(walk) - 1, None)]
    # best[j]: the lowest cost of the walk from its start to cut j, the cut
    # before and whether a stage ends at j; a stretch left without a stage
    # costs more than any stage could, per km.
    best: list[tuple[float, int, bool]] = [(0.0, -1, False)]
    for j in range(1, len(cuts)):
        gap_m = along[cuts[j][0]] - along[cuts[j - 1][0]]
        best.append((best[j - 1][0] + SKIP_COST_PER_M * gap_m, j - 1, False))
        for i in range(j - 1, -1, -1):
            length_m = along[cuts[j][0]] - along[cuts[i][0]]
            if length_m > STAGE_MAX_M:
                break
            if length_m < STAGE_MIN_M:
                continue
            end = cuts[j][1]
            cost = (
                best[i][0]
                + ((length_m - STAGE_TARGET_M) / (STAGE_TARGET_M - STAGE_MIN_M)) ** 2
                + (0.0 if end is None else CUT_COST[end.kind])
            )
            if cost < best[j][0]:
                best[j] = (cost, i, True)
    chosen = []
    j = len(cuts) - 1
    while j > 0:
        _, i, stage = best[j]
        if stage:
            chosen.append((i, j))
        j = i
    stages = []
    for i, j in reversed(chosen):
        (first, start), (last, end) = cuts[i], cuts[j]
        stages.append(
            DayStage(
                tuple(walk[first : last + 1]),
                float(along[last] - along[first]),
                start and start.name,
                end and end.name,
            )
        )
    return stages


def _cuts(lat: np.ndarray, lon: np.ndarray, points: Sequence[CutPoint]) -> list[tuple[int, CutPoint]]:
    # Each point within its radius of the walk, at the vertex nearest it.
    if not points:
        return []
    tree = BallTree(np.radians(np.column_stack([lat, lon])), metric="haversine")
    distance, vertex = tree.query(np.radians([(point.lat, point.lon) for point in points]), k=1)
    cuts = [
        (int(at[0]), point)
        for point, d, at in zip(points, distance, vertex)
        if d[0] * EARTH_RADIUS_M <= CUT_RADIUS_M[point.kind]
    ]
    return sorted(cuts, key=lambda cut: cut[0])
