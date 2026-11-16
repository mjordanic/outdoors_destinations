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
import io
import json
import math
import xml.etree.ElementTree as ET
import zipfile
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


# `fi` and `no` are the fetch names of those register ids (`fetch-external --source fi`).
_SOURCE_ALIASES = {"fi": "fi-lipas", "no": "no-turrutebasen"}


def fetch_external(source_id: str, root: Path | str = EXTERNAL_DIR) -> Path:
    """Cache the data of the external source ``source_id`` under ``root``."""
    source_id = _SOURCE_ALIASES.get(source_id, source_id)
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
    line, _ = _joined_and_rest(parts)
    if line and start is not None and _haversine_m(*start, *line[-1]) < _haversine_m(*start, *line[0]):
        line.reverse()
    return line


def _joined_and_rest(parts: Sequence[Sequence[Point]]) -> tuple[list[Point], list[list[Point]]]:
    """The line ``joined_line`` builds, and the parts it leaves over."""
    pieces = [list(part) for part in parts if len(part) >= 2]
    if not pieces:
        return [], []
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
    return line, rest


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


# Lipas (register id fi-lipas), CC BY 4.0. The licence page's example credit is
# "Jyväskylän yliopisto, Lipas Liikuntapaikat.fi" (the register name).
# Hiking routes are type 4405 and nature trails type 4404, status active.
# Both use the 1 km floor at ingest (ADR-0033, ADR-0062). A 2–3 km floor for
# nature trails was not taken: many are real walks of 1–3 km, and dropping them
# here would hide them from the ingest too-short count.
LIPAS_API = "https://api.lipas.fi/v2/sports-sites"
LIPAS_PAGE = 100
LIPAS_TYPES = (4405, 4404)
LIPAS_RECORD = "https://www.lipas.fi/liikuntapaikka/{}"


def fetch_lipas(directory: Path) -> Path:
    """Active Lipas hiking routes and nature trails as one GeoJSON
    FeatureCollection, fetched 100 records per page."""
    items: list[dict] = []
    page = 1
    while True:
        query = urlencode(
            (
                ("type-codes", LIPAS_TYPES[0]),
                ("type-codes", LIPAS_TYPES[1]),
                ("statuses", "active"),
                ("page", page),
                ("page-size", LIPAS_PAGE),
            )
        )
        request = Request(f"{LIPAS_API}?{query}", headers={"User-Agent": "dayhike"})
        with urlopen(request, timeout=120) as response:
            payload = json.load(response)
        batch = payload.get("items") or []
        items.extend(batch)
        total_pages = int((payload.get("pagination") or {}).get("total-pages") or 1)
        if not batch or page >= total_pages:
            break
        page += 1
    features = []
    for item in items:
        feature = _lipas_feature(item)
        if feature is not None:
            features.append(feature)
    path = directory / "fi-lipas.geojson"
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    tmp.replace(path)
    return path


def read_lipas(path: Path) -> Iterator[ExternalHike]:
    """Active hiking routes and nature trails of a cached Lipas file.

    A record's parts whose ends lie within ``GAP_JOIN_M`` are one walk (as
    ``joined_line`` joins them), and ``source_ref`` is the Lipas id. Parts that
    do not join it, branches included, are separate pieces of the same name:
    ``source_ref`` is ``{id}/{n}``, numbered from 1 in order
    of each piece's start (latitude, then longitude). The page is the Lipas
    record, ``https://www.lipas.fi/liikuntapaikka/{id}``.
    """
    for feature in json.loads(Path(path).read_text())["features"]:
        properties = feature.get("properties") or {}
        name = str(properties.get("name") or "").strip()
        lipas_id = properties.get("lipas-id")
        if (
            properties.get("status") != "active"
            or properties.get("type-code") not in LIPAS_TYPES
            or not name
            or lipas_id is None
        ):
            continue
        lines = _joined_components(_geometry_parts(feature.get("geometry")))
        if not lines:
            continue
        url = LIPAS_RECORD.format(lipas_id)
        if len(lines) == 1:
            yield ExternalHike(ref=str(lipas_id), name=name, points=tuple(lines[0]), url=url)
            continue
        ordered = sorted(lines, key=lambda line: (line[0][0], line[0][1]))
        for index, line in enumerate(ordered, start=1):
            yield ExternalHike(ref=f"{lipas_id}/{index}", name=name, points=tuple(line), url=url)


def _lipas_feature(item: dict) -> dict | None:
    """One Lipas API record as a GeoJSON feature, or none when it is not a walk."""
    type_code = (item.get("type") or {}).get("type-code")
    name = str(item.get("name") or "").strip()
    lipas_id = item.get("lipas-id")
    if item.get("status") != "active" or type_code not in LIPAS_TYPES or not name or lipas_id is None:
        return None
    parts = _api_lines(item)
    if not parts:
        return None
    geometry = (
        {"type": "LineString", "coordinates": parts[0]}
        if len(parts) == 1
        else {"type": "MultiLineString", "coordinates": parts}
    )
    return {
        "type": "Feature",
        "properties": {"lipas-id": lipas_id, "name": name, "status": "active", "type-code": type_code},
        "geometry": geometry,
    }


def _api_lines(item: dict) -> list[list[list[float]]]:
    """The record's lines as ``[lon, lat]`` parts, elevation dropped."""
    features = ((item.get("location") or {}).get("geometries") or {}).get("features") or []
    parts: list[list[list[float]]] = []
    for feature in features:
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "LineString":
            continue
        line = [
            [point[0], point[1]]
            for point in geometry.get("coordinates") or []
            if isinstance(point, (list, tuple)) and len(point) >= 2 and _number(point[0]) and _number(point[1])
        ]
        if len(line) >= 2:
            parts.append(line)
    return parts


def _geometry_parts(geometry: dict | None) -> list[list[Point]]:
    """Line parts of a cached geometry as ``(lat, lon)``."""
    if not geometry:
        return []
    if geometry.get("type") == "LineString":
        raw_parts = [geometry.get("coordinates") or []]
    elif geometry.get("type") == "MultiLineString":
        raw_parts = geometry.get("coordinates") or []
    else:
        return []
    parts: list[list[Point]] = []
    for part in raw_parts:
        line = [
            (point[1], point[0])
            for point in part
            if isinstance(point, (list, tuple)) and len(point) >= 2 and _number(point[0]) and _number(point[1])
        ]
        if len(line) >= 2:
            parts.append(line)
    return parts


def _joined_components(parts: Sequence[Sequence[Point]]) -> list[list[Point]]:
    """One line per group of parts whose ends meet within ``GAP_JOIN_M``, and
    each part a group's line leaves over (a branch) as a line of its own."""
    pieces = [list(part) for part in parts if len(part) >= 2]
    parent = list(range(len(pieces)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def ends_meet(left: Sequence[Point], right: Sequence[Point]) -> bool:
        return any(_haversine_m(*left[end], *right[other]) <= GAP_JOIN_M for end in (0, -1) for other in (0, -1))

    for i, left in enumerate(pieces):
        for j in range(i + 1, len(pieces)):
            if ends_meet(left, pieces[j]):
                parent[find(j)] = find(i)
    groups: dict[int, list[list[Point]]] = {}
    for index, piece in enumerate(pieces):
        groups.setdefault(find(index), []).append(piece)
    lines = []
    for group in groups.values():
        line, rest = _joined_and_rest(group)
        lines.append(line)
        lines.extend(rest)
    return lines


# Turrutebasen (register id no-turrutebasen), CC BY 4.0. Credit Kartverket.
# Foot routes only. A national GPX `<rte>` is one segment: `<name>` is the
# route number, `<desc>` the route name, `<src>` the maintainer, `<type>` the
# kind. Segments of one route number join as Lipas parts do. "Ukjent" is an
# empty name. A long route stays one hike (ADR-0062); the 1 km floor is ingest's.
_MISSING_ROUTE_NAME = "ukjent"
TURRUTE_UUID = "d1422d17-6d95-4ef1-96ab-8af31744dd63"
TURRUTE_ORDER = "https://nedlasting.geonorge.no/api/order"


def read_turrutebasen(path: Path) -> Iterator[ExternalHike]:
    """Foot routes of a cached Turrutebasen file, segments of one ``rutenummer``
    chained.

    Parts whose ends meet within ``GAP_JOIN_M`` are one walk, and ``source_ref``
    is the route number. Parts that do not join it, branches included, are
    separate pieces of the same name: ``source_ref`` is ``{rutenummer}/{n}``,
    numbered from 1 by each piece's start (latitude, then longitude). The name
    is ``rutenavn`` (``Ukjent`` counts as none), else the maintainer and the
    route number, else the route number. Ski, bike and other route types are
    left out. There is no per-route page.
    """
    grouped: dict[str, list[list[Point]]] = {}
    route_names: dict[str, list[str]] = {}
    maintainers: dict[str, list[str]] = {}
    for feature in json.loads(Path(path).read_text())["features"]:
        properties = feature.get("properties") or {}
        if properties.get("type") != "Fotrute":
            continue
        number = str(properties.get("rutenummer") or "").strip()
        if not number:
            continue
        parts = _geometry_parts(feature.get("geometry"))
        if not parts:
            continue
        grouped.setdefault(number, []).extend(parts)
        route_names.setdefault(number, []).append(str(properties.get("rutenavn") or "").strip())
        maintainers.setdefault(number, []).append(str(properties.get("maintainer") or "").strip())
    for number, parts in grouped.items():
        lines = [line for line in _joined_components(parts) if len(line) >= 2]
        if not lines:
            continue
        name = _turrute_name(route_names[number], maintainers[number], number)
        if len(lines) == 1:
            yield ExternalHike(ref=number, name=name, points=tuple(lines[0]))
            continue
        ordered = sorted(lines, key=lambda line: (line[0][0], line[0][1]))
        for index, line in enumerate(ordered, start=1):
            yield ExternalHike(ref=f"{number}/{index}", name=name, points=tuple(line))


def _turrute_name(route_names: Sequence[str], maintainers: Sequence[str], number: str) -> str:
    """``rutenavn`` when one is set, else ``{maintainer} {rutenummer}``, else the number."""
    names = [name for name in route_names if name and name.casefold() != _MISSING_ROUTE_NAME]
    if names:
        counts: dict[str, int] = {}
        for name in names:
            counts[name] = counts.get(name, 0) + 1
        return sorted(counts, key=lambda name: (-counts[name], name))[0]
    kept = [maintainer for maintainer in maintainers if maintainer]
    if not kept:
        return number
    counts = {}
    for maintainer in kept:
        counts[maintainer] = counts.get(maintainer, 0) + 1
    maintainer = sorted(counts, key=lambda name: (-counts[name], name))[0]
    return f"{maintainer} {number}"


def fetch_turrutebasen(directory: Path) -> Path:
    """National Turrutebasen foot routes as one GeoJSON FeatureCollection.

    Geonorge's download order for the whole country, GPX in WGS84. Each foot
    ``rte`` becomes one feature: route number, route name, maintainer.
    """
    payload = {
        "orderLines": [
            {
                "metadataUuid": TURRUTE_UUID,
                "areas": [{"code": "0000", "name": "Hele landet", "type": "landsdekkende"}],
                "formats": [{"name": "GPX "}],
                "projections": [
                    {
                        "code": "4326",
                        "name": "WGS84 Geografisk",
                        "codespace": "http://www.opengis.net/def/crs/EPSG/0/4326",
                    }
                ],
            }
        ]
    }
    request = Request(
        TURRUTE_ORDER,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "dayhike"},
        method="POST",
    )
    with urlopen(request, timeout=120) as response:
        order = json.load(response)
    files = [item for item in order.get("files") or [] if item.get("downloadUrl")]
    if not files:
        raise OSError(f"{TURRUTE_ORDER}: no file to download")
    download = Request(files[0]["downloadUrl"], headers={"User-Agent": "dayhike"})
    with urlopen(download, timeout=600) as response:
        blob = response.read()
    features = list(_gpx_foot_features(blob))
    path = directory / "no-turrutebasen.geojson"
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    tmp.replace(path)
    return path


_GPX = "{http://www.topografix.com/GPX/1/1}"


def _gpx_foot_features(blob: bytes) -> Iterator[dict]:
    """Foot ``rte`` elements of a zipped GPX, as GeoJSON features in lon/lat."""
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        gpx_name = next(name for name in archive.namelist() if name.lower().endswith(".gpx"))
        with archive.open(gpx_name) as handle:
            for _, element in ET.iterparse(handle, events=("end",)):
                if element.tag != f"{_GPX}rte":
                    continue
                kind = (element.findtext(f"{_GPX}type") or "").strip()
                number = (element.findtext(f"{_GPX}name") or "").strip()
                if kind == "Fotrute" and number:
                    line = [
                        [float(point.get("lon")), float(point.get("lat"))]
                        for point in element.findall(f"{_GPX}rtept")
                        if point.get("lat") is not None and point.get("lon") is not None
                    ]
                    if len(line) >= 2:
                        yield {
                            "type": "Feature",
                            "properties": {
                                "rutenummer": number,
                                "rutenavn": (element.findtext(f"{_GPX}desc") or "").strip(),
                                "maintainer": (element.findtext(f"{_GPX}src") or "").strip(),
                                "type": "Fotrute",
                            },
                            "geometry": {"type": "LineString", "coordinates": line},
                        }
                element.clear()


EXTERNAL_SOURCES = {
    "ie-sport-ireland": ExternalSource("ie-sport-ireland", fetch_sport_ireland, read_sport_ireland),
    "fi-lipas": ExternalSource("fi-lipas", fetch_lipas, read_lipas),
    "no-turrutebasen": ExternalSource("no-turrutebasen", fetch_turrutebasen, read_turrutebasen),
}


def _source(source_id: str) -> ExternalSource:
    if source_id not in EXTERNAL_SOURCES:
        raise ValueError(f"no external source {source_id}; known: {', '.join(sorted(EXTERNAL_SOURCES))}")
    return EXTERNAL_SOURCES[source_id]
