from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Callable
from urllib.request import urlretrieve as default_urlretrieve

from dayhike.regions import DEFAULT_REGION, load_region

# The default region's extracts (dayhike/data/regions/alps-dinarides.toml).
GEOFABRIK_EXTRACTS: tuple[str, ...] = load_region(DEFAULT_REGION).extracts

UrlRetrieve = Callable[[str, str], object]


def fetch_geofabrik_extracts(
    dest: Path | str,
    urlretrieve: UrlRetrieve = default_urlretrieve,
    urls: Sequence[str] = GEOFABRIK_EXTRACTS,
) -> list[Path]:
    """Download the Geofabrik PBFs (default: the default region's) into dest. Does not ingest them."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for url in urls:
        path = dest / url.rsplit("/", 1)[1]
        urlretrieve(url, str(path))
        paths.append(path)
    return paths
