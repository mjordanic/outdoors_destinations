"""Cut a finished serving export down to Europe, the United States or a few areas (ADR-0082).

``cut_serving`` reads a serving export (``dayhike.serving``), read-only, and writes a complete
export of only the hikes it keeps, without the Catalog or the pipeline:

- ``europe`` keeps the hikes starting east of ``REGION_SPLIT_LON`` and ``us`` those west of it,
  the RegionChoice's split (ADR-0074). Everything else of the export is cut on the same line:
  TrailGraph Segments, Junctions, amenities, climate cells and water tiles, and the highway tiles.
  The cut refuses an export where a Segment or a highway tile crosses the line, so nothing is lost
  on either side.
- A cut file (``CUTS_DIR``, or any TOML file) keeps the hikes starting in its areas: each
  ``[[area]]`` has a ``label`` and ``continent`` (what the pages call it, as a region's), and keeps
  the hikes starting in its ``bounds`` box, its ``polygon`` (``[lat, lon]`` corners) and its
  ``countries`` (ISO 3166 codes, by ``countries.npy``), each that is given. A region file
  (``dayhike/data/regions``) is read as one area of its ``bounds``. The TrailGraph is kept within
  ``GRAPH_MARGIN_M`` of the kept hikes' lines, as far as a SuggestedLoop from their Trailheads can
  walk, with every climate cell and water tile a kept Segment reaches; the highway tiles and the
  places within ``PLACE_MARGIN_DEG`` of each area's Trailheads, so a place the place search finds
  can be driven from.

Every Ranking of a cut equals the uncut export's with the RegionChoice of its side ticked: the
facts, profiles and way back are the uncut export's rows, the Calibration is kept as it is (a score
stays the share of random pairs of the uncut export less alike, ADR-0026), and each hike's group
of Twins keeps its kept members. Hike ids and Segment ids are kept, so a shared link to a kept hike
or a ComposedHike on the kept side still opens.

The cut rebuilds what is derived from the hikes kept: the name search's index (``export_name_index``),
the way-back network (``TrailGraph.write_network``), ``places.sqlite`` and ``landmarks.sqlite`` (``export_places``,
``export_landmarks``, from the GeoNames dump), and ``meta.json``: the hike count, the regions (from
the uncut export's, of the side kept, or the cut file's areas), the credits of the sources the kept
hikes come from, and a ``data_tag`` ending in the cut's name (``-eu``, ``-us``, ``-demo``), the
tag of its data image (docs/deploy.md §1).

Every file is written new, never opened for writing where it is: an output linked to the input
(``cp -al``) leaves the input as it was.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import sqlite3
import struct
import tarfile
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import shapely

from dayhike.catalog import EARTH_RADIUS_M
from dayhike.dem import DEM_ATTRIBUTION_KEYS, USGS_3DEP, Bounds
from dayhike.loops import LOOP_MAX_M, LOOP_START_M
from dayhike.regions import _box
from dayhike.serving import (
    PLACE_MARGIN_DEG,
    REGION_SPLIT_LON,
    NAME_INDEX,
    REGIONS,
    ServingData,
    ServingExportError,
    export_landmarks,
    export_name_index,
    export_places,
)

Progress = Callable[[str], None]

CUTS_DIR = Path(__file__).parent / "data" / "cuts"
# A SuggestedLoop starts within LOOP_START_M of a Trailhead and walks at most LOOP_MAX_M, so it stays
# this close to the hikes it is searched from (loops.py).
GRAPH_MARGIN_M = LOOP_START_M + LOOP_MAX_M / 2
# The side a cut keeps: its data_tag suffix, and the continent its regions are in.
SIDES = {"europe": ("eu", "Europe"), "us": ("us", "North America")}
TILE_DEG = {0: 4.0, 1: 1.0, 2: 0.25}  # Valhalla's tile size per hierarchy level
WATER_TILE_DEG = 3  # the TrailGraph's water tiles (TrailGraph._beside_water)
_INDEX_ENTRY = struct.Struct("<QII")  # index.bin: tile data offset, GraphId (level | index << 3), size
_SIMILARITY_BY_HIKE = {  # the axis of each similarity array that is one per DayHike
    "ids.npy": 0, "twins.npy": 0, "profile.npy": 0, "profile_back.npy": 0,
    "facts.npy": 1, "facts_back.npy": 1, "way_back.npy": 1,
}  # fmt: skip
_SIMILARITY_KEPT = ("quantiles", "values", "rarity", "quantiles_back", "values_back", "rarity_back")


class CutError(ServingExportError):
    """The cut asked for does not fit the export."""


@dataclass(frozen=True)
class Area:
    """Where a cut file keeps the hikes starting: in its ``bounds``, its ``polygon`` and its ``countries``,
    each given; ``label`` and ``continent`` as a region's (``dayhike.regions``)."""

    label: str
    continent: str | None = None
    bounds: Bounds | None = None
    polygon: tuple[tuple[float, float], ...] = ()  # (lat, lon) corners
    countries: tuple[str, ...] = ()


@dataclass(frozen=True)
class Keep:
    """What a cut keeps: one side of ``REGION_SPLIT_LON`` (``side``) or its ``areas``; ``name`` ends its data_tag."""

    name: str
    side: str | None = None
    areas: tuple[Area, ...] = ()


def load_keep(name_or_path: str | Path) -> Keep:
    """``europe``, ``us``, a cut file of ``CUTS_DIR`` by name (``demo``), or a cut or region TOML file by path."""
    text = str(name_or_path)
    if text in SIDES:
        return Keep(name=SIDES[text][0], side=text)
    path = Path(name_or_path)
    if path.suffix != ".toml":
        path = CUTS_DIR / f"{text}.toml"
        if not path.is_file():
            known = ", ".join(sorted(p.stem for p in CUTS_DIR.glob("*.toml")))
            raise CutError(f"no cut {text!r}: europe, us, one of {known}, or a .toml file")
    try:
        data = tomllib.loads(path.read_text())
        tables = data["area"] if "area" in data else [data]
        areas = tuple(_area(table) for table in tables)
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError, ValueError) as exc:
        raise CutError(f"{path}: {exc}") from exc
    if not areas:
        raise CutError(f"{path}: no [[area]]")
    return Keep(name=path.stem, areas=areas)


def _area(table: dict) -> Area:
    area = Area(
        label=str(table["label"]),
        continent=None if table.get("continent") is None else str(table["continent"]),
        bounds=None if "bounds" not in table else _box(table["bounds"]),
        polygon=tuple((float(lat), float(lon)) for lat, lon in table.get("polygon", ())),
        countries=tuple(str(code) for code in table.get("countries", ())),
    )
    if area.bounds is None and not area.polygon and not area.countries:
        raise ValueError(f"area {area.label!r} has no bounds, polygon or countries")
    if area.polygon and len(area.polygon) < 3:
        raise ValueError(f"area {area.label!r}: a polygon has at least 3 corners")
    return area


@dataclass(frozen=True)
class _Reach:
    """Where a cut keeps what lies around its hikes: one side of ``REGION_SPLIT_LON``, or ``boxes``.
    A box is kept where it touches the reach, a point where it is in it."""

    side: str | None = None
    boxes: tuple[Bounds, ...] = ()

    def points(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        if self.side is not None:
            return (lon < REGION_SPLIT_LON) == (self.side == "us")
        found = np.zeros(np.shape(lat), dtype=bool)
        for b in self.boxes:
            found |= (b.south <= lat) & (lat <= b.north) & (b.west <= lon) & (lon <= b.east)
        return found

    def touching(self, south: np.ndarray, north: np.ndarray, west: np.ndarray, east: np.ndarray) -> np.ndarray:
        if self.side is not None:
            return west < REGION_SPLIT_LON if self.side == "us" else east >= REGION_SPLIT_LON
        found = np.zeros(np.shape(south), dtype=bool)
        for b in self.boxes:
            found |= (south <= b.north) & (north >= b.south) & (west <= b.east) & (east >= b.west)
        return found

    def point_sql(self, lat: str, lon: str) -> str:
        if self.side is not None:
            return f"({lon}) {'<' if self.side == 'us' else '>='} {REGION_SPLIT_LON!r}"
        return " OR ".join(
            f"(({lat}) BETWEEN {b.south!r} AND {b.north!r} AND ({lon}) BETWEEN {b.west!r} AND {b.east!r})"
            for b in self.boxes
        )

    def touching_sql(self, south: str, north: str, west: str, east: str) -> str:
        if self.side is not None:
            return f"{west} < {REGION_SPLIT_LON!r}" if self.side == "us" else f"{east} >= {REGION_SPLIT_LON!r}"
        return " OR ".join(
            f"({south} <= {b.north!r} AND {north} >= {b.south!r} AND {west} <= {b.east!r} AND {east} >= {b.west!r})"
            for b in self.boxes
        )


def cut_serving(
    source_dir: Path | str,
    out_dir: Path | str,
    keep: Keep,
    *,
    geonames_path: Path | str | None = None,
    progress: Progress | None = None,
) -> Path:
    """Write the export of ``source_dir``'s hikes that ``keep`` keeps into ``out_dir`` (see the module doc).

    ``geonames_path``, the GeoNames dump ``export-serving`` read, rebuilds ``places.sqlite`` and the
    GeoNames Landmarks of ``landmarks.sqlite``; an export with places needs it."""
    started = time.monotonic()
    source, out = Path(source_dir), Path(out_dir)
    if out.resolve() == source.resolve():
        raise CutError(f"{out}: the cut is written beside the export, not over it")
    data = ServingData(source)  # refuses an incomplete export or one of another format
    if (source / "places.sqlite").is_file() and geonames_path is None:
        raise CutError(f"{source} has place search: pass the GeoNames dump it was exported with (--geonames)")
    if geonames_path is not None and not Path(geonames_path).is_file():
        raise CutError(f"{geonames_path}: no GeoNames dump there")
    kept, areas = _kept_rows(data, keep)
    report = progress or (lambda line: None)
    report(f"cut: keeping {len(kept)} of {len(data.store.ids)} DayHikes")
    out.mkdir(parents=True, exist_ok=True)
    _fresh(out / "meta.json")
    ids = data.store.ids[kept]
    _cut_similarity(source / "similarity", out / "similarity", kept)
    _save(out / "trailheads.npy", np.asarray(data.trailheads)[kept])
    for name in ("countries.npy", "borders.npy"):
        if (source / name).is_file():
            _save(out / name, np.load(source / name)[kept])
        else:
            _fresh(out / name)
    report("cut: hikes.sqlite")
    keep_hikes = "id IN (SELECT id FROM temp.keep)"
    _cut_sqlite(
        source / "hikes.sqlite",
        out / "hikes.sqlite",
        {"hikes": keep_hikes, "hike_boxes": keep_hikes},
        lambda db: _temp_ids(db, "keep", ids),
    )
    _fresh(out / NAME_INDEX)
    if (source / NAME_INDEX).is_file():  # the name search's index (ADR-0081), of the hikes kept
        report(f"cut: {NAME_INDEX}")
        export_name_index(out)
    reach_graph, reach_tiles = _reaches(data, keep, kept, areas)
    compose = False
    if (source / "trailgraph.sqlite").is_file() and data.meta.get("compose"):
        report("cut: trailgraph.sqlite")
        _cut_trailgraph(source / "trailgraph.sqlite", out / "trailgraph.sqlite", reach_graph, keep.side is not None)
        report("cut: the way-back network")
        _write_network(out / "trailgraph.sqlite")
        compose = True
    else:
        _fresh(out / "trailgraph.sqlite")
        shutil.rmtree(out / "trailgraph.network", ignore_errors=True)
    if (source / "valhalla" / "valhalla_tiles.tar").is_file():
        report("cut: highway tiles")
        (out / "valhalla").mkdir(exist_ok=True)
        tiles = _cut_tiles(source / "valhalla" / "valhalla_tiles.tar", out / "valhalla" / "valhalla_tiles.tar", reach_tiles)
        report(f"cut: {tiles} highway tiles")
        _copy(source / "valhalla" / "valhalla.json", out / "valhalla" / "valhalla.json")
    else:
        shutil.rmtree(out / "valhalla", ignore_errors=True)
    if (source / "snaps.npz").is_file():
        _cut_snaps(source / "snaps.npz", out / "snaps.npz", np.asarray(data.trailheads)[kept])
    else:
        _fresh(out / "snaps.npz")
    if (source / "places.sqlite").is_file():
        report("cut: places.sqlite")
        _fresh(out / "places.sqlite")
        export_places(geonames_path, out)
        if keep.side is None:
            _drop_places_outside(out / "places.sqlite", reach_tiles)
    else:
        _fresh(out / "places.sqlite")
    if (source / "landmarks.sqlite").is_file():
        report("cut: landmarks.sqlite")
        _fresh(out / "landmarks.sqlite")
        export_landmarks(out, geonames_path)
    else:
        _fresh(out / "landmarks.sqlite")
    meta = _cut_meta(data.meta, keep, areas, out, ids, compose)
    meta["export_seconds"] = round(time.monotonic() - started, 1)
    (out / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    return out


def _kept_rows(data: ServingData, keep: Keep) -> tuple[np.ndarray, list[tuple[Area, np.ndarray]]]:
    """The rows (``trailheads.npy`` order) ``keep`` keeps, and per area the rows starting in it."""
    lat, lon = np.asarray(data.trailheads[:, 0]), np.asarray(data.trailheads[:, 1])
    if keep.side is not None:
        rows = data.region_rows([keep.side])
        if not len(rows):
            raise CutError(f"{data.directory}: no hike starts in {REGIONS[keep.side][1]}")
        return rows, []
    if data.countries is None and any(area.countries for area in keep.areas):
        raise CutError(f"{data.directory}: no countries.npy, so no area can keep hikes by country")
    areas, found = [], np.zeros(len(lat), dtype=bool)
    for area in keep.areas:
        inside = np.ones(len(lat), dtype=bool)
        if area.bounds is not None:
            b = area.bounds
            inside &= (b.south <= lat) & (lat <= b.north) & (b.west <= lon) & (lon <= b.east)
        if area.polygon:
            inside &= shapely.contains_xy(shapely.Polygon([(x, y) for y, x in area.polygon]), lon, lat)
        if area.countries:
            inside &= np.isin(data.countries, area.countries)
        if not inside.any():
            raise CutError(f"{data.directory}: no hike starts in {area.label}")
        areas.append((area, np.flatnonzero(inside)))
        found |= inside
    return np.flatnonzero(found), areas


def _reaches(
    data: ServingData, keep: Keep, kept: np.ndarray, areas: list[tuple[Area, np.ndarray]]
) -> tuple[_Reach, _Reach]:
    """Where the TrailGraph and the highway tiles are kept: the side, or per area the box
    around its hikes' lines grown by ``GRAPH_MARGIN_M``, and around its Trailheads grown by
    ``PLACE_MARGIN_DEG``."""
    if keep.side is not None:
        return _Reach(side=keep.side), _Reach(side=keep.side)
    lines = {}
    with sqlite3.connect(data._db_uri, uri=True) as db:
        for row in db.execute("SELECT id, south, north, west, east FROM hike_boxes"):
            lines[row[0]] = row[1:]
    graph, tiles = [], []
    for _, rows in areas:
        lat, lon = np.asarray(data.trailheads[rows, 0]), np.asarray(data.trailheads[rows, 1])
        boxes = np.array([lines[i] for i in data.store.ids[rows].tolist() if i in lines]).reshape(-1, 4)
        south, north = float(np.concatenate([lat, boxes[:, 0]]).min()), float(np.concatenate([lat, boxes[:, 1]]).max())
        west, east = float(np.concatenate([lon, boxes[:, 2]]).min()), float(np.concatenate([lon, boxes[:, 3]]).max())
        grow_lat = math.degrees(GRAPH_MARGIN_M / EARTH_RADIUS_M)
        grow_lon = grow_lat / max(math.cos(math.radians(max(abs(south), abs(north)))), 0.01)
        graph.append(Bounds(south=south - grow_lat, west=west - grow_lon, north=north + grow_lat, east=east + grow_lon))
        tiles.append(
            Bounds(
                south=float(lat.min()) - PLACE_MARGIN_DEG,
                west=float(lon.min()) - PLACE_MARGIN_DEG,
                north=float(lat.max()) + PLACE_MARGIN_DEG,
                east=float(lon.max()) + PLACE_MARGIN_DEG,
            )
        )
    return _Reach(boxes=tuple(graph)), _Reach(boxes=tuple(tiles))


def _drop_places_outside(path: Path, reach: _Reach) -> None:
    """Drop the places of ``places.sqlite`` (written new by ``export_places``) outside ``reach``."""
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute(f"DELETE FROM places WHERE NOT ({reach.point_sql('lat', 'lon')})")
        db.execute("DELETE FROM place_names WHERE place NOT IN (SELECT id FROM places)")
        db.execute("VACUUM")
    finally:
        db.close()


def kept_twins(twins: np.ndarray, kept: np.ndarray) -> np.ndarray:
    """``twins`` (each row's first row of its group of Twins) for the rows ``kept`` (sorted), in their
    new numbering: each kept row's group is its kept members, led by the first of them."""
    groups = twins[kept]
    _, first = np.unique(groups, return_index=True)  # kept is sorted, so the first kept member of each group
    lead = dict(zip(groups[first].tolist(), first.tolist()))
    return np.array([lead[group] for group in groups.tolist()], dtype=twins.dtype)


def _cut_similarity(source: Path, target: Path, kept: np.ndarray) -> None:
    """The similarity directory of the rows ``kept``: their facts, profiles, Twins and way back,
    and the uncut Calibration, so every score stays as it was."""
    target.mkdir(exist_ok=True)
    for name, axis in _SIMILARITY_BY_HIKE.items():
        array = np.load(source / name, mmap_mode="r")
        if name == "twins.npy":
            _save(target / name, kept_twins(np.asarray(array), kept))
        else:
            _save(target / name, np.take(array, kept, axis=axis))
    for name in _SIMILARITY_KEPT:
        _copy(source / f"{name}.npy", target / f"{name}.npy")
    meta = json.loads((source / "meta.json").read_text())
    meta["dayhikes"] = len(kept)
    _fresh(target / "meta.json")
    (target / "meta.json").write_text(json.dumps(meta, indent=1))


def _cut_trailgraph(source: Path, target: Path, reach: _Reach, side: bool) -> None:
    """The TrailGraph within ``reach``: the Segments touching it and their Junctions, the Junctions,
    amenities and climate cells in it and the water tiles touching it. Cut at a side, a Segment
    crossing ``REGION_SPLIT_LON`` is refused: it would be on neither side. Cut to boxes, the climate
    cells a kept Segment names and the water tiles its box touches are kept too, so a walk along a
    Segment crossing a box's edge measures as in the uncut export."""
    uri = f"file:{source}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as db:
        if side:
            crossing = db.execute(
                "SELECT count(*) FROM segment_boxes WHERE west < ? AND east >= ?", (REGION_SPLIT_LON, REGION_SPLIT_LON)
            ).fetchone()[0]
            if crossing:
                raise CutError(f"{source}: {crossing} Segments cross {-REGION_SPLIT_LON:g}° W; the TrailGraph cannot be cut there")
        grid = dict(db.execute("SELECT key, value FROM trailgraph_meta")).get("climate_grid")
    if grid is not None:
        (a, _, c, _, e, f), _, width = json.loads(grid)
        # A cell's centre, as TrailGraph._write_climate_cells places it: transform @ (col + 0.5, row + 0.5).
        climate = reach.point_sql(f"{f!r} + {e!r} * (cell / {width} + 0.5)", f"{c!r} + {a!r} * (cell % {width} + 0.5)")
        if not side:
            climate += " OR cell IN (SELECT cell FROM temp.climate_keep)"
    else:
        climate = None
    water = reach.touching_sql("south", f"south + {WATER_TILE_DEG}", "west", f"west + {WATER_TILE_DEG}")
    if not side:
        water = f"({water}) OR (south, west) IN (SELECT south, west FROM temp.water_keep)"

    def setup(db: sqlite3.Connection) -> None:
        db.execute("CREATE TEMP TABLE segment_keep (id INTEGER PRIMARY KEY)")
        db.execute(
            "INSERT INTO temp.segment_keep "
            f"SELECT id FROM src.segment_boxes WHERE {reach.touching_sql('south', 'north', 'west', 'east')}"
        )
        # The Segments are most of the file: their ends and relations are read once, in id order (the
        # order they are stored in), not in the R*Tree's.
        db.execute(
            "CREATE TEMP TABLE segment_ends AS SELECT s.id, s.a, s.b, s.relation_ids "
            "FROM temp.segment_keep k CROSS JOIN src.segments s ON s.id = k.id"
        )
        db.execute(
            "CREATE TEMP TABLE junction_keep AS "
            f"SELECT id FROM src.junctions WHERE {reach.point_sql('latitude', 'longitude')} "
            "UNION SELECT a FROM temp.segment_ends UNION SELECT b FROM temp.segment_ends"
        )
        db.execute(
            "CREATE TEMP TABLE amenity_keep AS "
            f"SELECT id FROM src.amenities WHERE {reach.point_sql('latitude', 'longitude')}"
        )
        if not side:
            _keep_segment_cells(db)

    segment = "id IN (SELECT id FROM temp.segment_keep)"
    amenity = "id IN (SELECT id FROM temp.amenity_keep)"
    _cut_sqlite(
        source,
        target,
        {
            "trailgraph_meta": None,
            "junctions": "id IN (SELECT id FROM temp.junction_keep)",
            "segments": segment,
            "segment_ways": "segment_id IN (SELECT id FROM temp.segment_keep)",
            "segment_boxes": segment,
            "relations": "id IN (SELECT DISTINCT r.value FROM temp.segment_ends s, json_each(s.relation_ids) r)",
            "amenities": amenity,
            "amenity_boxes": amenity,
            "climate_cells": climate,
            "water_tiles": water,
            "water_cells": f"(south, west) IN (SELECT south, west FROM src.water_tiles WHERE {water})",
        },
        setup,
    )


def _keep_segment_cells(db: sqlite3.Connection) -> None:
    """``temp.climate_keep``: the climate cells the kept Segments name (``climate_cell``, per vertex, -1
    none); ``temp.water_keep``: the water tiles their boxes touch."""
    cells, tiles = [], set()
    rows = db.execute(
        "SELECT s.climate_cell, b.south, b.north, b.west, b.east FROM temp.segment_keep k "
        "CROSS JOIN src.segments s ON s.id = k.id CROSS JOIN src.segment_boxes b ON b.id = k.id"
    )
    for blob, south, north, west, east in rows:
        if blob:
            cells.append(np.frombuffer(blob, dtype="<i4"))
        for lat in range(math.floor(south / WATER_TILE_DEG), math.floor(north / WATER_TILE_DEG) + 1):
            for lon in range(math.floor(west / WATER_TILE_DEG), math.floor(east / WATER_TILE_DEG) + 1):
                tiles.add((lat * WATER_TILE_DEG, lon * WATER_TILE_DEG))
    named = np.unique(np.concatenate(cells)) if cells else np.empty(0, dtype="<i4")
    db.execute("CREATE TEMP TABLE climate_keep (cell INTEGER PRIMARY KEY)")
    db.executemany("INSERT INTO temp.climate_keep VALUES (?)", ((c,) for c in named[named >= 0].tolist()))
    db.execute("CREATE TEMP TABLE water_keep (south INTEGER, west INTEGER, PRIMARY KEY (south, west))")
    db.executemany("INSERT INTO temp.water_keep VALUES (?, ?)", sorted(tiles))


def _write_network(path: Path) -> None:
    from dayhike.trailgraph import TrailGraph

    TrailGraph(path).write_network()  # removes the old network directory first: its files are unlinked, not rewritten


def _cut_sqlite(
    source: Path, target: Path, where: dict[str, str | None], setup: Callable[[sqlite3.Connection], None]
) -> None:
    """A new SQLite file of ``source``'s tables, each holding its rows matching ``where`` (None: all of
    them); ``setup`` first makes the temp tables the conditions read, with ``source`` attached as ``src``.
    Every table must have a condition, so a table added later is not copied whole unnoticed."""
    partial = target.with_name(f"{target.name}.partial")
    partial.unlink(missing_ok=True)
    db = sqlite3.connect(f"file:{partial}", uri=True)  # a URI connection, so the source attaches read-only
    try:
        db.executescript("PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF; PRAGMA cache_size = -262144;")
        db.execute("ATTACH DATABASE ? AS src", (f"file:{source}?mode=ro&immutable=1",))
        db.executescript(f"PRAGMA src.cache_size = -262144; PRAGMA src.mmap_size = {source.stat().st_size};")
        schema = db.execute("SELECT type, name, sql FROM src.sqlite_master WHERE sql IS NOT NULL ORDER BY rowid").fetchall()
        virtual = [name for kind, name, sql in schema if kind == "table" and sql.upper().startswith("CREATE VIRTUAL")]
        tables = [
            (name, sql)
            for kind, name, sql in schema
            if kind == "table" and not any(name.startswith(f"{v}_") for v in virtual)
        ]
        unknown = {name for name, _ in tables} - set(where)
        if unknown:
            raise CutError(f"{source}: no rule to cut table(s) {', '.join(sorted(unknown))}")
        setup(db)
        for name, sql in tables:
            db.execute(sql)
            condition = where[name]
            db.execute(f"INSERT INTO main.{name} SELECT * FROM src.{name}" + ("" if condition is None else f" WHERE {condition}"))
        for kind, name, sql in schema:
            if kind == "index":
                db.execute(sql)
        db.commit()
        db.execute("DETACH DATABASE src")
    finally:
        db.close()
    os.replace(partial, target)


def _temp_ids(db: sqlite3.Connection, name: str, ids: np.ndarray) -> None:
    db.execute(f"CREATE TEMP TABLE {name} (id INTEGER PRIMARY KEY)")
    db.executemany(f"INSERT INTO temp.{name} VALUES (?)", ((i,) for i in ids.tolist()))


def tile_box(name: str) -> tuple[int, int, Bounds]:
    """A Valhalla tile file's hierarchy level, tile index and box (``2/000/791/234.gph``)."""
    level, *digits = name.removesuffix(".gph").split("/")
    level, index = int(level), int("".join(digits))
    size = TILE_DEG[level]
    columns = round(360 / size)
    south, west = -90 + (index // columns) * size, -180 + (index % columns) * size
    return level, index, Bounds(south=south, west=west, north=south + size, east=west + size)


def _cut_tiles(source: Path, target: Path, reach: _Reach) -> int:
    """A new tile extract of the tiles of ``source`` touching ``reach``, with its ``index.bin``
    (first, as ``valhalla_build_extract`` writes it); the number of tiles kept. Cut at a side, a tile
    crossing ``REGION_SPLIT_LON`` is refused."""
    partial = target.with_name(f"{target.name}.partial")
    partial.unlink(missing_ok=True)
    with tarfile.open(source) as tiles:
        kept = []
        for member in tiles:
            if not member.name.endswith(".gph"):
                continue
            _, _, box = tile_box(member.name)
            # A tile is the half-open box [west, east): one ending at the split is wholly west of it.
            east = math.nextafter(box.east, -math.inf)
            if reach.side is not None and box.west < REGION_SPLIT_LON <= east:
                raise CutError(f"{source}: tile {member.name} crosses {-REGION_SPLIT_LON:g}° W")
            if reach.touching(np.array(box.south), np.array(box.north), np.array(box.west), np.array(east)):
                kept.append(member)
        with tarfile.open(partial, "w", format=tarfile.USTAR_FORMAT) as out:
            index = tarfile.TarInfo("index.bin")
            index.size, index.mtime, index.mode = _INDEX_ENTRY.size * len(kept), int(time.time()), 0o644
            out.addfile(index, _Zeros(index.size))
            for member in kept:
                info = tarfile.TarInfo(member.name)
                info.size, info.mtime, info.mode = member.size, int(member.mtime), member.mode
                out.addfile(info, tiles.extractfile(member))
    with tarfile.open(partial) as written:
        members = list(written)
    entries = []
    for member in members[1:]:
        level, index, _ = tile_box(member.name)
        entries.append(_INDEX_ENTRY.pack(member.offset_data, level | index << 3, member.size))
    with partial.open("r+b") as file:
        file.seek(members[0].offset_data)
        file.write(b"".join(entries))
    os.replace(partial, target)
    return len(kept)


class _Zeros:
    """A file of ``size`` zero bytes, for ``TarFile.addfile``."""

    def __init__(self, size: int) -> None:
        self.left = size

    def read(self, size: int = -1) -> bytes:
        size = self.left if size < 0 else min(size, self.left)
        self.left -= size
        return bytes(size)


def _cut_snaps(source: Path, target: Path, trailheads: np.ndarray) -> None:
    """The highway snaps of the Trailheads kept."""
    with np.load(source) as snaps:
        arrays = {name: snaps[name] for name in snaps.files}
    places = set(map(tuple, trailheads.tolist()))
    keep = np.array([place in places for place in zip(arrays["lat"].tolist(), arrays["lon"].tolist())], dtype=bool)
    _fresh(target)
    np.savez_compressed(target, **{name: array[keep] for name, array in arrays.items()})


def _cut_meta(
    meta: dict, keep: Keep, areas: list[tuple[Area, np.ndarray]], out: Path, ids: np.ndarray, compose: bool
) -> dict:
    """The uncut ``meta.json`` for the hikes kept: their count, regions and sources, and the tag ending in the cut's name."""
    with sqlite3.connect(f"file:{out / 'hikes.sqlite'}?mode=ro", uri=True) as db:
        used = {source for (source,) in db.execute("SELECT DISTINCT source FROM hikes")}
    sources = {key: value for key, value in meta.get("sources", {}).items() if key in used}
    dropped = set(meta.get("sources", {})) - set(sources)
    # USGS 3DEP maps only the United States: a cut without a hike there reads none of it.
    if not (np.asarray(np.load(out / "trailheads.npy"))[:, 1] < REGION_SPLIT_LON).any():
        dropped |= {key for key, register_id in DEM_ATTRIBUTION_KEYS.items() if register_id == USGS_3DEP.register_id}
    if keep.side is not None:
        continent = SIDES[keep.side][1]
        regions = [
            region
            for region in meta.get("regions", [])
            if (region.get("continent") == "North America") == (keep.side == "us")
        ] or [{"label": REGIONS[keep.side][1], "continent": continent}]
    else:
        regions = [{"label": area.label, "continent": area.continent} for area, _ in areas]
    return {
        **meta,
        "dayhikes": len(ids),
        "data_tag": f"{meta['data_tag']}-{keep.name}",
        "attribution": {key: value for key, value in meta["attribution"].items() if key not in dropped},
        "sources": sources,
        "drive_time": (out / "valhalla" / "valhalla.json").is_file() and (out / "snaps.npz").is_file(),
        "compose": compose,
        "regions": regions,
    }


def _fresh(path: Path) -> None:
    """Unlink ``path`` before it is written: a file linked to the uncut export is never written through."""
    path.unlink(missing_ok=True)


def _save(path: Path, array: np.ndarray) -> None:
    _fresh(path)
    np.save(path, np.ascontiguousarray(array))


def _copy(source: Path, target: Path) -> None:
    _fresh(target)
    shutil.copyfile(source, target)
