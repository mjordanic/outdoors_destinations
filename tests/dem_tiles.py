"""A FABDEM tile for tests whose height changes with latitude only."""

from collections.abc import Callable
from pathlib import Path

import numpy as np

from dayhike.dem import FABDEM, fabdem_tile_id

ROWS = 3_672  # one row every 1/3600 degree (about 31 m), as FABDEM, 0.01 degree past each edge
EDGE = 0.01  # so a point on the tile's south edge is on its grid


def write_dem_tile(directory: Path, lat: float, lon: float, height_at: Callable[[np.ndarray], np.ndarray]) -> Path:
    """The FABDEM tile holding (lat, lon) in ``directory``, its rows ``height_at`` their latitudes."""
    import rasterio
    from rasterio.transform import from_bounds

    directory.mkdir(parents=True, exist_ok=True)
    south, west = int(np.floor(lat)) - EDGE, int(np.floor(lon)) - EDGE
    north, east = south + 1 + 2 * EDGE, west + 1 + 2 * EDGE
    path = directory / FABDEM.tile_file(fabdem_tile_id(lat, lon))
    row_lats = north - (np.arange(ROWS) + 0.5) * (north - south) / ROWS
    data = np.repeat(height_at(row_lats).astype("float32")[:, None], 4, axis=1)
    with rasterio.open(
        path, "w", driver="GTiff", height=ROWS, width=4, count=1, dtype="float32", crs="EPSG:4326",
        transform=from_bounds(west, south, east, north, 4, ROWS), nodata=-9999.0,
    ) as dataset:  # fmt: skip
        dataset.write(data, 1)
    return path
