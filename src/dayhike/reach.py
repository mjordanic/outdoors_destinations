"""Reach: estimated minutes by car from a Base to Trailhead points, made once at build time.

The estimate is the smaller of two (ADR-0084). The straight line at STRAIGHT_SPEED_KMH
repairs near pairs that a minor road joins. The highway estimate is for far pairs: both
ends' straight-line approach to their highway points at APPROACH_SPEED_KMH, plus the
middle of the BAND_STEP_MIN band of one isochrone from the Base's highway point. This
module is the one place the estimate is made, so a better one can replace it.
"""

from __future__ import annotations

import urllib.error

import numpy as np
import shapely

from dayhike.catalog import Origin, straight_line_distances_m
from dayhike.highways import HighwaySnaps, isochrone_polygons
from dayhike.valhalla import Place

STRAIGHT_SPEED_KMH = 28.0
APPROACH_SPEED_KMH = 20.0
BAND_STEP_MIN = 5
REACH_MIN = 60  # the longest Reach


def reach_minutes(base: Place, points: np.ndarray, snaps: HighwaySnaps) -> np.ndarray:
    """Estimated minutes from `base` to each (lat, lon) row of `points`; NaN over REACH_MIN."""
    points = np.asarray(points, dtype=float).reshape(-1, 2)
    straight = straight_line_distances_m(Origin(*base), points) / (STRAIGHT_SPEED_KMH / 3.6) / 60
    minutes = np.fmin(straight, _highway_minutes(base, points, snaps))
    minutes[minutes > REACH_MIN] = np.nan
    return minutes


def _highway_minutes(base: Place, points: np.ndarray, snaps: HighwaySnaps) -> np.ndarray:
    """Minutes by the highway estimate, NaN beyond REACH_MIN or with no highway at either end."""
    minutes = np.full(len(points), np.nan)
    (start,) = snaps.of([base])
    found = snaps.of(list(zip(points[:, 0].tolist(), points[:, 1].tolist())))
    at = np.array([i for i, snap in enumerate(found) if snap is not None], dtype=int)
    if start is None or not len(at):
        return minutes
    snapped = np.array([found[i] for i in at], dtype=float)
    approach = (start[2] + snapped[:, 2]) / (APPROACH_SPEED_KMH / 3.6) / 60
    near = approach < REACH_MIN
    at, snapped, approach = at[near], snapped[near], approach[near]
    edges = list(range(BAND_STEP_MIN, REACH_MIN + 1, BAND_STEP_MIN))
    try:
        contours = isochrone_polygons(snaps.base_url, start[:2], edges)
    except urllib.error.HTTPError as error:
        if error.code != 400:  # Valhalla cannot spread from this highway point: as no highway
            raise
        return minutes
    upper = np.full(len(at), np.nan)  # the upper edge of each point's band
    alive = np.arange(len(at))
    for edge, contour in zip(reversed(edges), reversed(contours)):
        alive = alive[shapely.contains_xy(contour, snapped[alive, 1], snapped[alive, 0])]
        upper[alive] = edge
    minutes[at] = upper - BAND_STEP_MIN / 2 + approach
    return minutes
