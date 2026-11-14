"""External hike sources: DayHikes from open official trail registries (ADR-0062).

An external source (``ExternalSource``) is a sources register id, a ``fetch``
step that caches the source's GeoJSON under ``data/external/<source>/`` and a
``read`` step that turns the cached file into ``ExternalHike``s: name, line
from its start, stable record id and the record's page. Ingest adds each one as
a DayHike of kind ``external``, its own row and its own line, never merged
into an OSM hike, so the external layer keeps its own licence; one that is a
Twin of a relation DayHike is dropped (the Twin rule). Its id is a hash of
``(source, source_ref)``, in a range below every built id and above every OSM
relation id.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dayhike.catalog import GAP_JOIN_M, KIND_EXTERNAL, MIN_LENGTH_M, DayHike, _dayhike, _haversine_m, _linestring_length_m
from dayhike.sources import require_used

EXTERNAL_ID_MIN = 2**39  # every external id is at least this; OSM relation ids are about 2.2e7
EXTERNAL_ID_MAX = 2**40  # and below every built id (BUILT_ID_MIN)
EXTERNAL_DIR = Path("data/external")

Point = tuple[float, float]  # (lat, lon)


@dataclass(frozen=True)
class ExternalHike:
    """One record of an external source: its id there (``source_ref``), its
    name, its line from its start and the record's page."""

    ref: str
    name: str
    points: tuple[Point, ...]
    url: str | None = None


@dataclass(frozen=True)
class ExternalSource:
    """A sources register id, how to cache its data in a directory (returns the
    file) and how to read the cached file."""

    id: str
    fetch: Callable[[Path], Path]
    read: Callable[[Path], Iterator[ExternalHike]]


def external_id(source: str, ref: str) -> int:
    """The Catalog id of the external hike ``ref`` of ``source``."""
    digest = int.from_bytes(hashlib.sha256(f"{source}\n{ref}".encode()).digest()[:8], "big")
    return EXTERNAL_ID_MIN + digest % (EXTERNAL_ID_MAX - EXTERNAL_ID_MIN)


def cache_path(source_id: str, root: Path | str = EXTERNAL_DIR) -> Path:
    """Where ``fetch_external`` caches a source's GeoJSON."""
    return Path(root) / source_id / f"{source_id}.geojson"


def fetch_external(source_id: str, root: Path | str = EXTERNAL_DIR) -> Path:
    """Cache the data of the external source ``source_id`` under ``root``."""
    source = _source(source_id)
    directory = cache_path(source_id, root).parent
    directory.mkdir(parents=True, exist_ok=True)
    return source.fetch(directory)


def read_external(source_id: str, root: Path | str = EXTERNAL_DIR) -> list[ExternalHike]:
    """The hikes of the external source ``source_id`` from its cache; refused
    when the register does not list it as used."""
    require_used({source_id})
    path = cache_path(source_id, root)
    if not path.is_file():
        raise FileNotFoundError(f"No cached data for {source_id} at {path}: run `dayhike fetch-external --source {source_id}`")
    return list(_source(source_id).read(path))


def external_dayhike(source_id: str, hike: ExternalHike, snapshot, amenity_index) -> DayHike:
    """The DayHike of an external hike: its own line, no OSM way, no difficulty
    (the source's grading is not SAC), amenity facts from OpenStreetMap at its start."""
    return _dayhike(
        external_id(source_id, hike.ref),
        hike.name,
        KIND_EXTERNAL,
        [(0, point) for point in hike.points],
        None,
        snapshot,
        amenity_index,
        kind=KIND_EXTERNAL,
        source=source_id,
        source_ref=hike.ref,
        source_url=hike.url,
    )


def joined_line(parts: Sequence[Sequence[Point]], start: Point | None = None) -> list[Point]:
    """One line from the parts of a (multi)line: the longest part, extended by
    every part whose end lies within ``GAP_JOIN_M`` of its ends; the parts left
    over are left out. It starts at the end nearer ``start`` when given."""
    pieces = [list(part) for part in parts if len(part) >= 2]
    if not pieces:
        return []
    pieces.sort(key=_linestring_length_m, reverse=True)
    line, rest = pieces[0], pieces[1:]
    joined = True
    while joined and rest:
        joined = False
        for piece in rest:
            for candidate in (piece, piece[::-1]):
                if _haversine_m(*line[-1], *candidate[0]) <= GAP_JOIN_M:
                    line = line + candidate[1:]
                elif _haversine_m(*candidate[-1], *line[0]) <= GAP_JOIN_M:
                    line = candidate[:-1] + line
                else:
                    continue
                rest.remove(piece)
                joined = True
                break
            if joined:
                break
    if start is not None and _haversine_m(*start, *line[-1]) < _haversine_m(*start, *line[0]):
        line.reverse()
    return line


def long_enough(hike: ExternalHike) -> bool:
    """Whether an external hike meets the DayHike floor of 1 km (ADR-0033)."""
    return _linestring_length_m(hike.points) >= MIN_LENGTH_M


# Sport Ireland Trail Routes (register id ie-sport-ireland), CC BY 4.0, credit "Sport Ireland".
SPORT_IRELAND_LAYER = (
    "https://services-eu1.arcgis.com/CltcWyRoZmdwaB7T/ArcGIS/rest/services/GetIrelandActiveTrailRoutes/FeatureServer/0"
)
SPORT_IRELAND_FIELDS = ("TrailID", "Name", "Activity", "Website", "Latitude", "Longitude")
SPORT_IRELAND_PAGE = 500  # records per request; the layer serves at most 2,000


def fetch_sport_ireland(directory: Path) -> Path:
    """The walking trails of the Sport Ireland trail register as one GeoJSON
    FeatureCollection in WGS84, fetched page by page."""
    features: list[dict] = []
    while True:
        query = urlencode(
            {
                "where": "Activity='Walking'",
                "outFields": ",".join(SPORT_IRELAND_FIELDS),
                "outSR": 4326,
                "orderByFields": "TrailID",
                "resultOffset": len(features),
                "resultRecordCount": SPORT_IRELAND_PAGE,
                "f": "geojson",
            }
        )
        request = Request(f"{SPORT_IRELAND_LAYER}/query?{query}", headers={"User-Agent": "dayhike"})
        with urlopen(request, timeout=120) as response:
            page = json.load(response)
        if "error" in page:
            raise OSError(f"{SPORT_IRELAND_LAYER}: {page['error']}")
        features += page["features"]
        if not page["features"] or not page.get("properties", {}).get("exceededTransferLimit"):
            break
    path = directory / "ie-sport-ireland.geojson"
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    tmp.replace(path)
    return path


def read_sport_ireland(path: Path) -> Iterator[ExternalHike]:
    """The walking trails of a cached Sport Ireland file, each from its listed
    start (``Latitude``, ``Longitude``) when it has one."""
    for feature in json.loads(Path(path).read_text())["features"]:
        properties, geometry = feature["properties"], feature.get("geometry")
        if properties.get("Activity") != "Walking" or not geometry or not properties.get("Name"):
            continue
        if geometry["type"] == "LineString":
            parts = [geometry["coordinates"]]
        elif geometry["type"] == "MultiLineString":
            parts = geometry["coordinates"]
        else:
            continue
        lat, lon = properties.get("Latitude"), properties.get("Longitude")
        start = (lat, lon) if _number(lat) and _number(lon) else None
        line = joined_line([[(point[1], point[0]) for point in part] for part in parts], start)
        if len(line) >= 2:
            yield ExternalHike(
                ref=f"trail/{properties['TrailID']}",
                name=properties["Name"].strip(),
                points=tuple(line),
                url=properties.get("Website") or None,
            )


def _number(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


EXTERNAL_SOURCES = {
    "ie-sport-ireland": ExternalSource("ie-sport-ireland", fetch_sport_ireland, read_sport_ireland),
}


def _source(source_id: str) -> ExternalSource:
    if source_id not in EXTERNAL_SOURCES:
        raise ValueError(f"no external source {source_id}; known: {', '.join(sorted(EXTERNAL_SOURCES))}")
    return EXTERNAL_SOURCES[source_id]
