"""DriveTime on highways-only Valhalla tiles, as an estimate.

Tiles of motorway, trunk and primary roads build for all of Europe and answer
fast, but most Trailheads are not on them. A Trailhead is snapped onto the
highway tiles, and the straight-line distance from the Trailhead to that point
is added at APPROACH_SPEED_KMH. One isochrone from the Origin, cut into
CONTOUR_STEP_MIN bands, places every snapped point in a band with a
point-in-polygon test; only points whose band straddles the time limit go to
the matrix for an exact time. The Origin gets the same straight-line approach
to its own snap point.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Sequence

import numpy as np
import shapely
from shapely.geometry import shape

from dayhike.catalog import Origin, Trailhead, straight_line_distances_m
from dayhike.valhalla import (
    DEFAULT_VALHALLA_URL,
    MATRIX_BATCH_SIZE,
    SEARCH_CUTOFF_M,
    WIDE_SEARCH_CUTOFF_M,
    Place,
    ValhallaMatrixClient,
    locate,
    post_json,
)

APPROACH_SPEED_KMH = 35.0  # driven over the straight line to the highway point
CONTOUR_STEP_MIN = 20
# Isochrone polygons are simplified to this many metres; a Valhalla default is finer.
CONTOUR_GENERALIZE_M = 300
# Polygon edges are approximate: a point this close to its band's edge may belong to the next.
CONTOUR_MARGIN_S = 300
CONTOUR_RING_DEG = 0.15  # about 17 km north-south
LOCATE_BATCH_SIZE = 1_000
LOCATE_WORKERS = 4

Snap = tuple[float, float, float]  # highway latitude, longitude, metres from the place


def _approach_seconds(metres: float) -> float:
    return metres / (APPROACH_SPEED_KMH / 3.6)


class HighwaySnaps:
    """Where each place snaps onto the highway tiles. None: no highway within 100 km.

    Asking Valhalla is the slow part of a query, so answers are kept, and written
    to `path` when one is given.
    """

    def __init__(self, base_url: str = DEFAULT_VALHALLA_URL, path: Path | None = None) -> None:
        self.base_url = base_url
        self.path = path
        self._known: dict[Place, Snap | None] = {}
        if path is not None and path.exists():
            with np.load(path) as saved:
                for row in zip(*(saved[name] for name in ("lat", "lon", "snap_lat", "snap_lon", "metres"))):
                    lat, lon, snap_lat, snap_lon, metres = map(float, row)
                    self._known[(lat, lon)] = None if math.isnan(snap_lat) else (snap_lat, snap_lon, metres)

    def of(self, places: Sequence[Place]) -> list[Snap | None]:
        missing = [p for p in dict.fromkeys(places) if p not in self._known]
        if missing:
            batches = [missing[i : i + LOCATE_BATCH_SIZE] for i in range(0, len(missing), LOCATE_BATCH_SIZE)]
            with ThreadPoolExecutor(LOCATE_WORKERS) as pool:
                for batch, snapped in zip(batches, pool.map(self._snap, batches)):
                    self._known.update(zip(batch, snapped))
            self._save()
        return [self._known[p] for p in places]

    def _snap(self, places: Sequence[Place]) -> list[Snap | None]:
        snapped = locate(self.base_url, places)
        # Remote places: look again, farther out, for the few that found nothing.
        retry = [i for i, found in enumerate(snapped) if found is None]
        if retry:
            wide = locate(self.base_url, [places[i] for i in retry], WIDE_SEARCH_CUTOFF_M)
            for i, found in zip(retry, wide):
                snapped[i] = found
        snaps: list[Snap | None] = [None] * len(places)
        found_at = [i for i, found in enumerate(snapped) if found is not None]
        if found_at:
            pairs = [(places[i], snapped[i]) for i in found_at]
            metres = _metres_between([a for a, _ in pairs], [b for _, b in pairs])
            for i, (_, (lat, lon)), m in zip(found_at, pairs, metres):
                snaps[i] = (lat, lon, float(m))
        return snaps

    def _save(self) -> None:
        if self.path is None:
            return
        nan = float("nan")
        rows = [(p, s or (nan, nan, nan)) for p, s in self._known.items()]
        np.savez_compressed(
            self.path,
            lat=[p[0] for p, _ in rows],
            lon=[p[1] for p, _ in rows],
            snap_lat=[s[0] for _, s in rows],
            snap_lon=[s[1] for _, s in rows],
            metres=[s[2] for _, s in rows],
        )


def _metres_between(places: Sequence[Place], others: Sequence[Place]) -> np.ndarray:
    a, b = np.radians(places), np.radians(others)
    h = np.sin((b[:, 0] - a[:, 0]) / 2) ** 2 + np.cos(a[:, 0]) * np.cos(b[:, 0]) * np.sin((b[:, 1] - a[:, 1]) / 2) ** 2
    return 2 * 6_371_000 * np.arcsin(np.sqrt(h))


class HighwayDriveTimeClient:
    """DriveTime estimates up to `max_hours`; later ones come back None, like unreachable.

    After a call, `unreachable` holds the Trailhead places with no highway within
    100 km.
    """

    def __init__(
        self,
        max_hours: float,
        snaps: HighwaySnaps,
        batch_size: int = MATRIX_BATCH_SIZE,
    ) -> None:
        self.max_hours = max_hours
        self.snaps = snaps
        self.base_url = snaps.base_url
        self.unreachable: list[Place] = []
        self._matrix = ValhallaMatrixClient(self.base_url, batch_size)

    def sources_to_targets(
        self, origin: Origin, trailheads: Sequence[Trailhead]
    ) -> list[float | None]:
        start = self._snap_origin(origin)
        (start_m,) = straight_line_distances_m(origin, [start])
        budget_s = self.max_hours * 3600
        places = list(dict.fromkeys((t.latitude, t.longitude) for t in trailheads))
        snap_of = {p: s for p, s in zip(places, self.snaps.of(places)) if s is not None}
        self.unreachable = [p for p in places if p not in snap_of]
        # Seconds no highway route covers: the Origin's and the Trailhead's approach.
        approach_s = {p: _approach_seconds(start_m + s[2]) for p, s in snap_of.items()}
        near = [p for p in snap_of if approach_s[p] < budget_s]
        points = [snap_of[p][:2] for p in near]
        times: dict[Place, float | None] = {}
        borderline: list[Place] = []
        for place, (low_s, high_s) in zip(near, self._bands(start, points)):
            if high_s is None:  # beyond the budget
                continue
            if high_s + CONTOUR_MARGIN_S + approach_s[place] <= budget_s:
                times[place] = (low_s + high_s) / 2 + approach_s[place]
            elif low_s - CONTOUR_MARGIN_S + approach_s[place] <= budget_s:
                borderline.append(place)
        if borderline:
            highway_s = self._matrix.seconds_to_places(
                Origin(*start), [snap_of[p][:2] for p in borderline]
            )
            for place in borderline:
                found = highway_s[snap_of[place][:2]]
                times[place] = None if found is None else found + approach_s[place]
        return [times.get((t.latitude, t.longitude)) for t in trailheads]

    def _bands(
        self, start: Place, points: Sequence[Place]
    ) -> list[tuple[float, float | None]]:
        """(low, high) highway seconds to each point.

        None for high: beyond the budget. Infinite: only near enough to the edge of
        the largest contour that the polygon cannot be trusted; ask the matrix.
        """
        if not points:
            return []
        minutes = [*np.arange(CONTOUR_STEP_MIN, self.max_hours * 60, CONTOUR_STEP_MIN), self.max_hours * 60]
        contours = self._isochrone(start, minutes)
        lats, lons = np.array([p[0] for p in points]), np.array([p[1] for p in points])
        edges = [0.0] + [m * 60 for m in minutes]
        bands: list[tuple[float, float | None]] = [(0.0, None)] * len(points)
        # Isochrones skip roads that the matrix reaches (the far side of a ferry, say),
        # so a ring around the largest contour is checked exactly too.
        ring = shapely.buffer(contours[-1], CONTOUR_RING_DEG)
        alive = np.flatnonzero(shapely.contains_xy(ring, lons, lats))
        for i in alive:
            bands[i] = (0.0, math.inf)
        for index in range(len(minutes), 0, -1):
            alive = alive[shapely.contains_xy(contours[index - 1], lons[alive], lats[alive])]
            for i in alive:
                bands[i] = (edges[index - 1], edges[index])
        return bands

    def _isochrone(self, start: Place, minutes: Sequence[float]) -> list:
        body = post_json(
            self.base_url,
            "/isochrone",
            {
                "locations": [{"lat": start[0], "lon": start[1]}],
                "costing": "auto",
                "contours": [{"time": float(m)} for m in minutes],
                "polygons": True,
                "denoise": 0,
                "generalize": CONTOUR_GENERALIZE_M,
            },
        )
        by_minutes = {
            float(f["properties"]["contour"]): shape(f["geometry"])
            for f in body["features"]  # type: ignore[index]
        }
        polygons = [by_minutes[float(m)] for m in minutes]
        shapely.prepare(polygons)
        return polygons

    def _snap_origin(self, origin: Origin) -> Place:
        place = (origin.latitude, origin.longitude)
        for cutoff in (SEARCH_CUTOFF_M, WIDE_SEARCH_CUTOFF_M):
            (snapped,) = locate(self.base_url, [place], cutoff)
            if snapped is not None:
                return snapped
        raise ValueError(
            f"no highway within {WIDE_SEARCH_CUTOFF_M // 1000} km of Origin "
            f"{origin.latitude},{origin.longitude}"
        )
