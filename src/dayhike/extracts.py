from __future__ import annotations

from pathlib import Path
from typing import Callable
from urllib.request import urlretrieve as default_urlretrieve

GEOFABRIK_EXTRACTS: tuple[str, ...] = (
    "https://download.geofabrik.de/europe/croatia-latest.osm.pbf",
    "https://download.geofabrik.de/europe/slovenia-latest.osm.pbf",
    "https://download.geofabrik.de/europe/bosnia-herzegovina-latest.osm.pbf",
    "https://download.geofabrik.de/europe/montenegro-latest.osm.pbf",
    "https://download.geofabrik.de/europe/austria-latest.osm.pbf",
    "https://download.geofabrik.de/europe/switzerland-latest.osm.pbf",
    "https://download.geofabrik.de/europe/italy/nord-est-latest.osm.pbf",
    "https://download.geofabrik.de/europe/germany/bayern-latest.osm.pbf",
)

UrlRetrieve = Callable[[str, str], object]


def fetch_geofabrik_extracts(
    dest: Path | str,
    urlretrieve: UrlRetrieve = default_urlretrieve,
) -> list[Path]:
    """Download the PRD Geofabrik PBFs into dest. Does not ingest them."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for url in GEOFABRIK_EXTRACTS:
        path = dest / url.rsplit("/", 1)[1]
        urlretrieve(url, str(path))
        paths.append(path)
    return paths
