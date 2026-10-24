from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Sequence

from dayhike.catalog import Origin, Trailhead, straight_line_distances_m

DEFAULT_VALHALLA_URL = "http://127.0.0.1:8002"
# Matrix, not /isochrone. Chunk Trailheads so each POST stays a small batch.
MATRIX_BATCH_SIZE = 2_500  # Valhalla's default matrix limit: one source, 2500 targets
# Valhalla's own snap radius; a second try with the wide one finds remote Trailheads.
SEARCH_CUTOFF_M = 35_000
WIDE_SEARCH_CUTOFF_M = 100_000
NO_SUITABLE_EDGES = 171  # Valhalla error_code: a location has no road in reach

Place = tuple[float, float]  # (latitude, longitude)


def post_json(base_url: str, path: str, payload: dict) -> object:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def locate(
    base_url: str, places: Sequence[Place], search_cutoff_m: int = SEARCH_CUTOFF_M
) -> list[Place | None]:
    """Where Valhalla snaps each place onto a car road; None if no road is in reach."""
    body = post_json(
        base_url,
        "/locate",
        {
            "locations": [
                {"lat": lat, "lon": lon, "search_cutoff": search_cutoff_m}
                for lat, lon in places
            ],
            "costing": "auto",
        },
    )
    snapped: list[Place | None] = []
    for found in body:  # type: ignore[attr-defined]
        edges = found.get("edges")
        snapped.append(
            (edges[0]["correlated_lat"], edges[0]["correlated_lon"]) if edges else None
        )
    return snapped


class ValhallaMatrixClient:
    """Valhalla `/sources_to_targets` adapter. Times are seconds; None is unreachable."""

    def __init__(
        self,
        base_url: str = DEFAULT_VALHALLA_URL,
        batch_size: int = MATRIX_BATCH_SIZE,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.batch_size = batch_size

    def sources_to_targets(
        self, origin: Origin, trailheads: Sequence[Trailhead]
    ) -> list[float | None]:
        times = self.seconds_to_places(
            origin, [(t.latitude, t.longitude) for t in trailheads]
        )
        return [times[(t.latitude, t.longitude)] for t in trailheads]

    def seconds_to_places(
        self, origin: Origin, places: Sequence[Place]
    ) -> dict[Place, float | None]:
        # Places sharing a spot (one car park, several DayHikes) are one target.
        # Nearest first, so each batch's search from Origin stays as small as it can.
        unique = list(dict.fromkeys(places))
        distances_m = straight_line_distances_m(origin, unique)
        nearest_first = [unique[i] for i in distances_m.argsort()]
        times: dict[Place, float | None] = {}
        for start in range(0, len(nearest_first), self.batch_size):
            batch = nearest_first[start : start + self.batch_size]
            times.update(zip(batch, self._request(origin, batch)))
        return times

    def _request(
        self, origin: Origin, places: Sequence[Place], origin_snaps: bool = False
    ) -> list[float | None]:
        try:
            body = self._post(origin, places)
        except urllib.error.HTTPError as error:
            # Valhalla fails the whole request when one location has no road within
            # its search cutoff. If the Origin snaps, split to find the target; a
            # lone target that fails is unreachable.
            if error.code != 400 or _error_code(error) != NO_SUITABLE_EDGES:
                raise
            if not origin_snaps:
                self._check_origin_snaps(origin)
            if len(places) == 1:
                return [None]
            half = len(places) // 2
            return self._request(origin, places[:half], True) + self._request(
                origin, places[half:], True
            )
        row = body["sources_to_targets"][0]  # type: ignore[index]
        return [_matrix_seconds(cell) for cell in row]

    def _post(self, origin: Origin, places: Sequence[Place]) -> object:
        payload = {
            "sources": [{"lat": origin.latitude, "lon": origin.longitude}],
            "targets": [{"lat": lat, "lon": lon} for lat, lon in places],
            # Car DriveTime from Origin to Trailhead; not an isochrone polygon.
            "costing": "auto",
        }
        return post_json(self.base_url, "/sources_to_targets", payload)

    def _check_origin_snaps(self, origin: Origin) -> None:
        try:
            self._post(origin, [(origin.latitude, origin.longitude)])
        except urllib.error.HTTPError as error:
            if error.code != 400 or _error_code(error) != NO_SUITABLE_EDGES:
                raise
            raise ValueError(
                f"no road within {SEARCH_CUTOFF_M // 1000} km of Origin "
                f"{origin.latitude},{origin.longitude}"
            ) from error


def _error_code(error: urllib.error.HTTPError) -> object:
    try:
        return json.load(error).get("error_code")
    except (ValueError, AttributeError):
        return None


def _matrix_seconds(cell: object) -> float | None:
    # Missing time means unreachable: leave None; never invent a DriveTime.
    if not isinstance(cell, dict):
        return None
    time = cell.get("time")
    if time is None:
        return None
    return float(time)
