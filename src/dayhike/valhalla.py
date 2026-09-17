from __future__ import annotations

import json
import urllib.request
from typing import Sequence

from dayhike.catalog import Origin, Trailhead

DEFAULT_VALHALLA_URL = "http://127.0.0.1:8002"
# Matrix, not /isochrone. Chunk Trailheads so each POST stays a small batch.
MATRIX_BATCH_SIZE = 50


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
        times: list[float | None] = []
        for start in range(0, len(trailheads), self.batch_size):
            batch = trailheads[start : start + self.batch_size]
            times.extend(self._request(origin, batch))
        return times

    def _request(
        self, origin: Origin, trailheads: Sequence[Trailhead]
    ) -> list[float | None]:
        payload = {
            "sources": [{"lat": origin.latitude, "lon": origin.longitude}],
            "targets": [
                {"lat": trailhead.latitude, "lon": trailhead.longitude}
                for trailhead in trailheads
            ],
            # Car DriveTime from Origin to Trailhead; not an isochrone polygon.
            "costing": "auto",
        }
        request = urllib.request.Request(
            f"{self.base_url}/sources_to_targets",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            body = json.load(response)
        row = body["sources_to_targets"][0]
        return [_matrix_seconds(cell) for cell in row]


def _matrix_seconds(cell: object) -> float | None:
    # Missing time means unreachable: leave None; never invent a DriveTime.
    if not isinstance(cell, dict):
        return None
    time = cell.get("time")
    if time is None:
        return None
    return float(time)
