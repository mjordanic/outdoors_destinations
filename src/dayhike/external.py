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

import contextlib
import hashlib
import io
import json
import math
import tomllib
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dayhike.catalog import (
    GAP_JOIN_M,
    KIND_EXTERNAL,
    MIN_LENGTH_M,
    NAME_ORIGIN_SYNTHETIC,
    DayHike,
    _dayhike,
    _haversine_m,
    _linestring_length_m,
)
from dayhike.sources import require_used

EXTERNAL_ID_MIN = 2**39  # every external id is at least this; OSM relation ids are about 2.2e7
EXTERNAL_ID_MAX = 2**40  # and below every built id (BUILT_ID_MIN)
EXTERNAL_DIR = Path("data/external")
NAME_ORIGIN_STAGE = "stage"  # as a built DayStage's (ADR-0038)

Point = tuple[float, float]  # (lat, lon)


@dataclass(frozen=True)
class ExternalHike:
    """One record of an external source: its id there (``source_ref``), its
    own name (None when the record has none), its line from its start and the
    record's page. ``fallback`` names a record with no name of its own and no
    place near it (ADR-0062); ``name_origin`` is how ``name`` was found. A
    DayStage of a long record (``stages.py``) has ``stage`` too: where it
    starts and ends along the record's line."""

    ref: str
    name: str | None
    points: tuple[Point, ...]
    url: str | None = None
    fallback: str | None = None
    stage: str | None = None
    name_origin: str = KIND_EXTERNAL


@dataclass(frozen=True)
class ExternalSource:
    """A sources register id, the short name an unnamed record is shown with,
    how to cache its data in a directory (returns the file) and how to read the
    cached file."""

    id: str
    short_name: str
    fetch: Callable[[Path], Path]
    read: Callable[[Path], Iterator[ExternalHike]]


def external_id(source: str, ref: str, stage: str | None = None) -> int:
    """The Catalog id of the external hike ``ref`` of ``source``, or of its
    DayStage ``stage`` (the stage's start and end along the record's line, so
    the same walk keeps its id whatever the other stages and records are)."""
    key = f"{source}\n{ref}" if stage is None else f"{source}\n{ref}\nstage {stage}"
    digest = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big")
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
    try:
        return source.fetch(directory)
    except (ValueError, OSError):  # a source skipped for a reason (France) or not downloaded: leave no empty directory
        with contextlib.suppress(OSError):
            directory.rmdir()
        raise


def read_external(source_id: str, root: Path | str = EXTERNAL_DIR) -> list[ExternalHike]:
    """The hikes of the external source ``source_id`` from its cache; refused
    when the register does not list it as used."""
    require_used({source_id})
    path = cache_path(source_id, root)
    if not path.is_file():
        raise FileNotFoundError(f"No cached data for {source_id} at {path}: run `dayhike fetch-external --source {source_id}`")
    return list(_source(source_id).read(path))


def external_label(hike: ExternalHike, place: str | None, source_id: str) -> tuple[str, str]:
    """The label of an external hike and its NameOrigin: its own name, else
    "Hike near {place}" (the nearest place, as for an OSM relation, ADR-0017),
    else the source's ``fallback``, else, synthetic as an unnamed OSM
    relation's, "Unnamed hike ({source short name} {ref})"."""
    if hike.name:
        return hike.name, KIND_EXTERNAL
    if place:
        return f"Hike near {place}", KIND_EXTERNAL
    if hike.fallback:
        return hike.fallback, KIND_EXTERNAL
    return f"Unnamed hike ({_source(source_id).short_name} {hike.ref})", NAME_ORIGIN_SYNTHETIC


def external_dayhike(source_id: str, hike: ExternalHike, snapshot, amenity_index) -> DayHike:
    """The DayHike of an external hike (its ``name`` is its label, see
    ``external_label``): its own line, no OSM way, no difficulty (the source's
    grading is not SAC), amenity facts from OpenStreetMap at its start. A
    DayStage of one has NameOrigin ``stage``, and the record of its route."""
    return _dayhike(
        external_id(source_id, hike.ref, hike.stage),
        hike.name,
        NAME_ORIGIN_STAGE if hike.stage else hike.name_origin,
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


def _joined_and_rest(parts: Sequence[Sequence[Point]]) -> tuple[list[Point], list[int]]:
    """The line ``joined_line`` builds, and the indices of the parts it leaves over."""
    order = sorted(
        (index for index, part in enumerate(parts) if len(part) >= 2),
        key=lambda index: _linestring_length_m(parts[index]),
        reverse=True,
    )
    if not order:
        return [], []
    line, rest = list(parts[order[0]]), order[1:]
    joined = True
    while joined and rest:
        joined = False
        for index in rest:
            piece = list(parts[index])
            for candidate in (piece, piece[::-1]):
                if _haversine_m(*line[-1], *candidate[0]) <= GAP_JOIN_M:
                    line = line + candidate[1:]
                elif _haversine_m(*candidate[-1], *line[0]) <= GAP_JOIN_M:
                    line = candidate[:-1] + line
                else:
                    continue
                rest.remove(index)
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
        if properties.get("Activity") != "Walking" or not geometry:
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
                name=(properties.get("Name") or "").strip() or None,
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
        if properties.get("status") != "active" or properties.get("type-code") not in LIPAS_TYPES or lipas_id is None:
            continue
        lines = _joined_components(_geometry_parts(feature.get("geometry")))
        if not lines:
            continue
        url = LIPAS_RECORD.format(lipas_id)
        if len(lines) == 1:
            yield ExternalHike(ref=str(lipas_id), name=name or None, points=tuple(lines[0]), url=url)
            continue
        ordered = sorted(lines, key=lambda line: (line[0][0], line[0][1]))
        for index, line in enumerate(ordered, start=1):
            yield ExternalHike(ref=f"{lipas_id}/{index}", name=name or None, points=tuple(line), url=url)


def _lipas_feature(item: dict) -> dict | None:
    """One Lipas API record as a GeoJSON feature, or none when it is not a walk."""
    type_code = (item.get("type") or {}).get("type-code")
    name = str(item.get("name") or "").strip()
    lipas_id = item.get("lipas-id")
    if item.get("status") != "active" or type_code not in LIPAS_TYPES or lipas_id is None:
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
    return [line for line, _ in _joined_component_parts(parts)]


def _joined_component_parts(parts: Sequence[Sequence[Point]]) -> list[tuple[list[Point], list[int]]]:
    """The lines of ``_joined_components``, each with the indices of the parts in it."""
    pieces = [index for index, part in enumerate(parts) if len(part) >= 2]
    parent = {index: index for index in pieces}

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def ends_meet(left: Sequence[Point], right: Sequence[Point]) -> bool:
        return any(_haversine_m(*left[end], *right[other]) <= GAP_JOIN_M for end in (0, -1) for other in (0, -1))

    for position, i in enumerate(pieces):
        for j in pieces[position + 1 :]:
            if ends_meet(parts[i], parts[j]):
                parent[find(j)] = find(i)
    groups: dict[int, list[int]] = {}
    for index in pieces:
        groups.setdefault(find(index), []).append(index)
    lines = []
    for group in groups.values():
        line, rest = _joined_and_rest([parts[index] for index in group])
        left_over = {group[position] for position in rest}
        lines.append((line, [index for index in group if index not in left_over]))
        lines.extend((list(parts[index]), [index]) for index in sorted(left_over))
    return lines


# Turrutebasen (register id no-turrutebasen), CC BY 4.0. Credit Kartverket.
# Foot routes only. A national GPX `<rte>` is one segment: `<name>` is the
# route number, `<desc>` the route name, `<src>` the maintainer, `<type>` the
# kind. Segments of one maintainer and route number join as Lipas parts do.
# "Ukjent" is an empty name. The 1 km floor and the DayStages of a long route are
# ingest's (ADR-0062).
_MISSING_ROUTE_NAME = "ukjent"
TURRUTE_UUID = "d1422d17-6d95-4ef1-96ab-8af31744dd63"
TURRUTE_ORDER = "https://nedlasting.geonorge.no/api/order"


def read_turrutebasen(path: Path) -> Iterator[ExternalHike]:
    """Foot routes of a cached Turrutebasen file, segments of one maintainer and
    ``rutenummer`` chained.

    Parts whose ends meet within ``GAP_JOIN_M`` are one walk, and ``source_ref``
    is the route number. Parts that do not join it, branches included, are
    separate pieces: ``source_ref`` is ``{rutenummer}/{n}``, numbered from 1 by
    each piece's start (latitude, then longitude). A route number used by more
    than one maintainer is ``{maintainer}/{rutenummer}`` (and ``/{n}``) for each
    maintainer, the bare number for a blank one. Each piece's name is its own
    segments' ``rutenavn`` (``Ukjent`` counts as none), else None, and ingest
    names it after the nearest place; its ``fallback`` is the maintainer and the
    route number, else the route number. Ski, bike and other route
    types are left out. There is no per-route page.
    """
    grouped: dict[tuple[str, str], list[tuple[list[Point], int]]] = {}
    segments: list[tuple[str, str]] = []
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
        maintainer = str(properties.get("maintainer") or "").strip()
        grouped.setdefault((maintainer, number), []).extend((part, len(segments)) for part in parts)
        segments.append((str(properties.get("rutenavn") or "").strip(), maintainer))
    maintainers_of: dict[str, int] = {}
    for _, number in grouped:
        maintainers_of[number] = maintainers_of.get(number, 0) + 1
    for (maintainer, number), tagged in grouped.items():
        base = f"{maintainer}/{number}" if maintainers_of[number] > 1 and maintainer else number
        pieces = []
        for line, indices in _joined_component_parts([part for part, _ in tagged]):
            if len(line) < 2:
                continue
            owners = {tagged[index][1] for index in indices}
            pieces.append((line, *_turrute_names([segments[owner] for owner in sorted(owners)], number)))
        if len(pieces) == 1:
            line, name, fallback = pieces[0]
            yield ExternalHike(ref=base, name=name, points=tuple(line), fallback=fallback)
            continue
        ordered = sorted(pieces, key=lambda piece: (piece[0][0][0], piece[0][0][1]))
        for index, (line, name, fallback) in enumerate(ordered, start=1):
            yield ExternalHike(ref=f"{base}/{index}", name=name, points=tuple(line), fallback=fallback)


def _turrute_names(segments: Sequence[tuple[str, str]], number: str) -> tuple[str | None, str]:
    """The route's own name (``rutenavn``, None when it has none) and its
    fallback label: ``{maintainer} {rutenummer}``, else the number."""
    names = [name for name, _ in segments if name and name.casefold() != _MISSING_ROUTE_NAME]
    maintainers = [maintainer for _, maintainer in segments if maintainer]
    fallback = f"{_most_common(maintainers)} {number}" if maintainers else number
    return (_most_common(names) if names else None), fallback


def _most_common(values: Sequence[str]) -> str:
    """The most frequent value, ties going to the alphabetical one."""
    return min(Counter(values).items(), key=lambda item: (-item[1], item[0]))[0]


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
    features = list(_gpx_foot_features(blob, files[0]["downloadUrl"]))
    path = directory / "no-turrutebasen.geojson"
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    tmp.replace(path)
    return path


_GPX = "{http://www.topografix.com/GPX/1/1}"


def _gpx_foot_features(blob: bytes, url: str) -> Iterator[dict]:
    """Foot ``rte`` elements of a zipped GPX, as GeoJSON features in lon/lat."""
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        gpx_name = next((name for name in archive.namelist() if name.lower().endswith(".gpx")), None)
        if gpx_name is None:
            raise OSError(f"{url}: no .gpx file in the archive")
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


# France: the schema_randonnee datasets of data.gouv.fr (https://github.com/PnX-SI/schema_randonnee,
# which Geotrek exports follow), one register id each, listed in data/fr_randonnee.toml. A
# dataset is used only when its published licence passes the gate (FR_LICENCES: Licence Ouverte
# 2.0 and ODbL, which the project already offers) and its file follows the schema. Credit
# the producer by the register name.
FR_DATASETS_PATH = Path(__file__).parent / "data" / "fr_randonnee.toml"
FR_LICENCES = {"lov2": "Licence Ouverte 2.0", "odc-odbl": "ODbL 1.0"}  # data.gouv.fr licence code: register licence
FR_SCHEMA_KEYS = ("id_local", "nom_itineraire", "pratique")
_ON_FOOT = {"a pied", "pédestre", "pedestre"}  # `pratique` values that are walks


@dataclass(frozen=True)
class FrenchDataset:
    """A schema_randonnee dataset: its register id, the short name an unnamed
    record is shown with, its data.gouv.fr page, its GeoJSON file and the
    licence code data.gouv.fr publishes for it."""

    id: str
    short: str
    page: str
    file: str
    licence: str


def french_datasets() -> list[FrenchDataset]:
    """The datasets of ``data/fr_randonnee.toml``, in file order."""
    tables = tomllib.loads(FR_DATASETS_PATH.read_text(encoding="utf-8"))["dataset"]
    return [FrenchDataset(**table) for table in tables]


def schema_problem(features: Sequence[dict]) -> str | None:
    """Why a file's records do not follow schema_randonnee, or None: it needs
    records that all carry ``id_local`` (distinct, as it is the record's id),
    ``nom_itineraire`` and ``pratique``."""
    if not features:
        return "not schema_randonnee: no records"
    properties = [feature.get("properties") or {} for feature in features]
    missing = [key for key in FR_SCHEMA_KEYS if any(key not in record for record in properties)]
    if missing:
        return f"not schema_randonnee: records lack {', '.join(missing)}"
    ids = [str(record["id_local"] or "").strip() for record in properties]
    if not all(ids) or len(set(ids)) != len(ids):
        return f"not schema_randonnee: id_local repeats or is blank ({len(ids)} records, {len(set(ids))} distinct ids)"
    return None


def fetch_schema_randonnee(dataset: FrenchDataset, directory: Path) -> Path:
    """One French dataset as a slim GeoJSON FeatureCollection: only the
    properties the adapter reads, lines without heights. A dataset whose
    licence the gate does not accept is not downloaded, and one whose file does
    not follow the schema is not cached; both raise ``ValueError`` with the reason."""
    if dataset.licence not in FR_LICENCES:
        raise ValueError(
            f"{dataset.id}: published under licence {dataset.licence}, not one of {', '.join(sorted(FR_LICENCES))}"
        )
    request = Request(dataset.file, headers={"User-Agent": "dayhike"})
    with urlopen(request, timeout=600) as response:
        features = json.load(response).get("features") or []
    problem = schema_problem(features)
    if problem:
        raise ValueError(f"{dataset.id}: {problem}")
    slim = []
    for feature in features:
        parts = _geometry_parts(feature.get("geometry"))
        if not parts:
            continue
        properties = feature["properties"]
        slim.append(
            {
                "type": "Feature",
                "properties": {
                    "id_local": str(properties["id_local"]).strip(),
                    "nom_itineraire": properties["nom_itineraire"],
                    "pratique": properties["pratique"],
                    "url": properties.get("url"),
                },
                "geometry": (
                    {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in parts[0]]}
                    if len(parts) == 1
                    else {"type": "MultiLineString", "coordinates": [[[lon, lat] for lat, lon in part] for part in parts]}
                ),
            }
        )
    path = directory / f"{dataset.id}.geojson"
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps({"type": "FeatureCollection", "features": slim}))
    tmp.replace(path)
    return path


# South Tyrol hiking trails (register id it-odh-suedtirol). The Open Data Hub tourism
# API carries these trails as `civis.geoserver` records with licence "Closed", and
# its CC0 hiking records have no track, so the data is read from its owner, the
# Autonomous Province of Bolzano's open data portal: the dataset
# `percorsi-escursionistici` (layer Routes-HikingTrails, WFS), licence cc-zero. The
# portal states one licence per dataset and none per record, so the licence gate is
# the dataset's: `fetch_suedtirol` refuses a dataset the portal no longer lists as
# cc-zero. A feature is one trail segment: `ID`, `CODE` (trail number, repeated all
# over the province), `NAME` (the trail's name, often missing; "." is the province's
# blank). WFS JSON keeps four decimals (about 10 m).
SUEDTIROL_PORTAL = "https://data.civis.bz.it/api/3/action/package_show?id=percorsi-escursionistici"
SUEDTIROL_PAGE_URL = "https://data.civis.bz.it/dataset/percorsi-escursionistici"
SUEDTIROL_WFS = "https://geoservices1.civis.bz.it/geoserver/p_bz-TransportNetwork/ows"
SUEDTIROL_LAYER = "p_bz-TransportNetwork:Routes-HikingTrails"
SUEDTIROL_LICENCE = "cc-zero"
SUEDTIROL_PAGE = 200  # features per request; the server is slow with big ones
_SUEDTIROL_BLANK = "."


def fetch_suedtirol(directory: Path) -> Path:
    """The hiking trail segments of the province's open dataset as one GeoJSON
    FeatureCollection in WGS84, fetched page by page, once the portal lists the
    dataset as cc-zero."""
    request = Request(SUEDTIROL_PORTAL, headers={"User-Agent": "dayhike"})
    with urlopen(request, timeout=120) as response:
        licence = (json.load(response).get("result") or {}).get("license_id")
    if licence != SUEDTIROL_LICENCE:
        raise OSError(f"{SUEDTIROL_PORTAL}: licence is {licence!r}, not {SUEDTIROL_LICENCE}; nothing was fetched")
    features: list[dict] = []
    while True:
        query = urlencode(
            {
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeNames": SUEDTIROL_LAYER,
                "outputFormat": "application/json",
                "srsName": "EPSG:4326",
                "sortBy": "ID",
                "count": SUEDTIROL_PAGE,
                "startIndex": len(features),
            }
        )
        request = Request(f"{SUEDTIROL_WFS}?{query}", headers={"User-Agent": "dayhike"})
        with urlopen(request, timeout=120) as response:
            batch = json.load(response)["features"]
        features += [
            {
                "type": "Feature",
                "properties": {key: (feature.get("properties") or {}).get(key) for key in ("ID", "CODE", "NAME")},
                "geometry": feature.get("geometry"),
            }
            for feature in batch
        ]
        if len(batch) < SUEDTIROL_PAGE:
            break
    path = directory / "it-odh-suedtirol.geojson"
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    tmp.replace(path)
    return path


def fetch_french_datasets(root: Path | str = EXTERNAL_DIR) -> tuple[list[Path], list[str]]:
    """Cache every French dataset the gate accepts (``fetch-external --source fr``):
    the files written, and the reason for each dataset skipped (it names the dataset)."""
    paths: list[Path] = []
    skipped: list[str] = []
    for dataset in french_datasets():
        try:
            paths.append(fetch_external(dataset.id, root))
        except ValueError as exc:
            skipped.append(str(exc))
        except OSError as exc:
            skipped.append(f"{dataset.id}: download failed: {exc}")
    return paths, skipped


def read_schema_randonnee(path: Path) -> Iterator[ExternalHike]:
    """The walks (``pratique`` on foot) of a cached French dataset: the record
    ``id_local`` is the ``source_ref``, ``nom_itineraire`` the name (None when
    blank), ``url`` the record's page. Parts that meet within ``GAP_JOIN_M``
    are one line, the rest left out. A twin of an OSM relation is ingest's to drop
    (the Twin rule): ``id_osm`` is not read."""
    for feature in json.loads(Path(path).read_text())["features"]:
        properties = feature.get("properties") or {}
        if str(properties.get("pratique") or "").strip().casefold() not in _ON_FOOT:
            continue
        line = joined_line(_geometry_parts(feature.get("geometry")))
        if len(line) >= 2:
            yield ExternalHike(
                ref=str(properties["id_local"]),
                name=str(properties.get("nom_itineraire") or "").strip() or None,
                points=tuple(line),
                url=properties.get("url") or None,
            )


def read_suedtirol(path: Path) -> Iterator[ExternalHike]:
    """The trails of a cached South Tyrol file, segments of one trail number and
    name chained; a segment with neither is a walk of its own.

    Parts whose ends meet within ``GAP_JOIN_M`` are one walk, as for Lipas and
    Turrutebasen. ``source_ref`` is the lowest segment ``ID`` of a walk (the
    segment ``ID`` when it is one segment); a segment's parts that do not join
    give ``{ID}/{n}``, numbered from 1 by each piece's start (latitude, then
    longitude). A piece's name is its segments' ``NAME`` (the most common one),
    else None, and ingest names it after the nearest place; its ``fallback`` is
    "Trail {CODE}", else none. The page is the dataset's. Segments with no line
    are left out.
    """
    grouped: dict[tuple[str, str, int | None], list[tuple[list[Point], int]]] = {}
    for feature in json.loads(Path(path).read_text())["features"]:
        properties = feature.get("properties") or {}
        segment = properties.get("ID")
        parts = _geometry_parts(feature.get("geometry"))
        if segment is None or not parts:
            continue
        code, name = _suedtirol_text(properties.get("CODE")), _suedtirol_text(properties.get("NAME"))
        key = (code, name, None if code or name else int(segment))
        grouped.setdefault(key, []).extend((part, int(segment)) for part in parts)
    pieces: list[tuple[int, list[Point], str | None, str | None]] = []
    for (code, name, _), tagged in grouped.items():
        for line, indices in _joined_component_parts([part for part, _ in tagged]):
            if len(line) >= 2:
                lowest = min(tagged[index][1] for index in indices)
                pieces.append((lowest, line, name or None, f"Trail {code}" if code else None))
    for lowest, line, name, fallback in sorted(pieces, key=lambda piece: (piece[0], piece[1][0])):
        same = [piece for piece in pieces if piece[0] == lowest]
        ref = str(lowest) if len(same) == 1 else f"{lowest}/{sorted(p[1][0] for p in same).index(line[0]) + 1}"
        yield ExternalHike(ref=ref, name=name, points=tuple(line), url=SUEDTIROL_PAGE_URL, fallback=fallback)


def _suedtirol_text(value) -> str:
    """A ``CODE`` or ``NAME`` trimmed; the province's blank "." is empty."""
    text = str(value or "").strip()
    return "" if text == _SUEDTIROL_BLANK else text


EXTERNAL_SOURCES = {
    "ie-sport-ireland": ExternalSource("ie-sport-ireland", "Sport Ireland", fetch_sport_ireland, read_sport_ireland),
    "fi-lipas": ExternalSource("fi-lipas", "Lipas", fetch_lipas, read_lipas),
    "no-turrutebasen": ExternalSource("no-turrutebasen", "Turrutebasen", fetch_turrutebasen, read_turrutebasen),
    **{
        dataset.id: ExternalSource(dataset.id, dataset.short, partial(fetch_schema_randonnee, dataset), read_schema_randonnee)
        for dataset in french_datasets()
    },
    "it-odh-suedtirol": ExternalSource("it-odh-suedtirol", "South Tyrol", fetch_suedtirol, read_suedtirol),
}


def _source(source_id: str) -> ExternalSource:
    if source_id not in EXTERNAL_SOURCES:
        raise ValueError(f"no external source {source_id}; known: {', '.join(sorted(EXTERNAL_SOURCES))}")
    return EXTERNAL_SOURCES[source_id]
