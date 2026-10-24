from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dayhike.catalog import Catalog, Origin, Ranking, build_catalog, filter_by_drive_time
from dayhike.dem import Bounds, DEFAULT_DEM_BOUNDS, DEFAULT_DEM_DIR, fetch_fabdem, sample_dem
from dayhike.landcover import DEFAULT_LANDCOVER_DIR, fetch_worldcover, sample_landcover
from dayhike.climate import DEFAULT_CLIMATE_DIR, fetch_worldclim, sample_climate
from dayhike.extracts import fetch_geofabrik_extracts
from dayhike.similarity import (
    MEASURE_NAMES,
    StaleSimilarity,
    build_similarity,
    calibrate_similarity,
    rank_dayhikes,
)
from dayhike.valhalla import DEFAULT_VALHALLA_URL, ValhallaMatrixClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dayhike")
    subparsers = parser.add_subparsers(dest="command", required=True)
    ingest = subparsers.add_parser(
        "ingest", help="Ingest OSM XML or PBF snapshot(s) into a Catalog"
    )
    ingest.add_argument(
        "osm",
        nargs="+",
        type=Path,
        help="OSM XML or PBF path(s), or a directory of those files",
    )
    ingest.add_argument(
        "--catalog",
        type=Path,
        default=Path("data/catalog.sqlite"),
        help="SQLite Catalog path (default: data/catalog.sqlite)",
    )
    ingest.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore an existing ingest work file and start from the snapshot",
    )
    ingest.add_argument(
        "--list",
        action="store_true",
        dest="list_dayhikes",
        help="Print kept DayHikes as id, name, length_m after ingest",
    )
    fetch = subparsers.add_parser(
        "fetch-extracts",
        help="Download Geofabrik PBFs into data/osm (does not ingest)",
    )
    fetch.add_argument(
        "--dest",
        type=Path,
        default=Path("data/osm"),
        help="Directory for downloaded PBFs (default: data/osm)",
    )
    fetch_dem = subparsers.add_parser(
        "fetch-dem",
        help="Download FABDEM GeoTIFFs covering Alps+Dinarides into data/dem",
    )
    fetch_dem.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_DEM_DIR,
        help="Directory for GeoTIFFs (default: data/dem)",
    )
    fetch_dem.add_argument(
        "--bounds",
        type=_parse_bounds,
        default=DEFAULT_DEM_BOUNDS,
        help="south,west,north,east in WGS84 (default: PRD extract bbox)",
    )
    fetch_lc = subparsers.add_parser(
        "fetch-landcover",
        help="Download ESA WorldCover GeoTIFFs covering Alps+Dinarides into data/landcover",
    )
    fetch_lc.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_LANDCOVER_DIR,
        help="Directory for GeoTIFFs (default: data/landcover)",
    )
    fetch_lc.add_argument(
        "--bounds",
        type=_parse_bounds,
        default=DEFAULT_DEM_BOUNDS,
        help="south,west,north,east in WGS84 (default: PRD extract bbox)",
    )
    fetch_cl = subparsers.add_parser(
        "fetch-climate",
        help="Download WorldClim monthly GeoTIFFs covering Alps+Dinarides into data/climate",
    )
    fetch_cl.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_CLIMATE_DIR,
        help="Directory for GeoTIFFs (default: data/climate)",
    )
    fetch_cl.add_argument(
        "--bounds",
        type=_parse_bounds,
        default=DEFAULT_DEM_BOUNDS,
        help="south,west,north,east in WGS84 (default: PRD extract bbox)",
    )
    sample = subparsers.add_parser(
        "sample-dem",
        help="Sample local FABDEM onto GeometryStore vertices and Catalog elevation",
    )
    sample.add_argument(
        "--catalog",
        type=Path,
        default=Path("data/catalog.sqlite"),
        help="SQLite Catalog path (default: data/catalog.sqlite)",
    )
    sample.add_argument(
        "--dem",
        type=Path,
        default=DEFAULT_DEM_DIR,
        help="Directory of FABDEM GeoTIFFs (default: data/dem)",
    )
    sample_lc = subparsers.add_parser(
        "sample-landcover",
        help="Sample local LandCover rasters onto GeometryStore vertices and Catalog fractions",
    )
    sample_lc.add_argument(
        "--catalog",
        type=Path,
        default=Path("data/catalog.sqlite"),
        help="SQLite Catalog path (default: data/catalog.sqlite)",
    )
    sample_lc.add_argument(
        "--landcover",
        type=Path,
        default=DEFAULT_LANDCOVER_DIR,
        help="Directory of LandCover GeoTIFFs (default: data/landcover)",
    )
    sample_cl = subparsers.add_parser(
        "sample-climate",
        help="Sample local climate rasters onto GeometryStore vertices and Catalog means",
    )
    sample_cl.add_argument(
        "--catalog",
        type=Path,
        default=Path("data/catalog.sqlite"),
        help="SQLite Catalog path (default: data/catalog.sqlite)",
    )
    sample_cl.add_argument(
        "--climate",
        type=Path,
        default=DEFAULT_CLIMATE_DIR,
        help="Directory of WorldClim GeoTIFFs (default: data/climate)",
    )
    similarity = subparsers.add_parser(
        "build-similarity",
        help="Build the similarity facts and Calibration next to a Catalog",
    )
    similarity.add_argument(
        "--catalog",
        type=Path,
        default=Path("data/catalog.sqlite"),
        help="SQLite Catalog path (default: data/catalog.sqlite)",
    )
    similarity.add_argument(
        "--dem",
        type=Path,
        default=DEFAULT_DEM_DIR,
        help="Directory of FABDEM GeoTIFFs for SolarAspect (default: data/dem)",
    )
    similarity.add_argument(
        "--calibrate-only",
        action="store_true",
        help="Keep the stored facts and only recompute the Calibration",
    )
    rank = subparsers.add_parser("rank", help="Rank DayHikes by similarity to Seeds")
    rank.add_argument("--catalog", required=True, type=Path)
    rank.add_argument(
        "--origin",
        type=_parse_origin,
        help="Origin as latitude,longitude (requires --max-hours)",
    )
    rank.add_argument(
        "--max-hours",
        type=float,
        help="Maximum DriveTime from Origin in hours (requires --origin)",
    )
    rank.add_argument(
        "--valhalla-url",
        default=DEFAULT_VALHALLA_URL,
        help="Valhalla base URL for /sources_to_targets",
    )
    rank.add_argument(
        "--month",
        type=int,
        default=None,
        help="QueryMonth 1-12; omit to use the June-August climate regime",
    )
    rank.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Print only the best this many DayHikes (all are still scored)",
    )
    rank.add_argument(
        "--weight",
        action="append",
        type=_parse_weight,
        metavar="MEASURE=WEIGHT",
        help="Weight 0-100 of one Measure, repeatable; Measures not named are "
        "excluded. Omit to weigh every Measure equally. Measures: "
        + ", ".join(MEASURE_NAMES),
    )
    rank.add_argument("seeds", nargs="+", help="Seed DayHike names")
    args = parser.parse_args(argv)
    if args.command == "ingest":
        return _ingest(
            args.osm, args.catalog, fresh=args.fresh, list_dayhikes=args.list_dayhikes
        )
    if args.command == "fetch-extracts":
        return _fetch_extracts(args.dest)
    if args.command == "fetch-dem":
        return _fetch_dem(args.dest, args.bounds)
    if args.command == "fetch-landcover":
        return _fetch_landcover(args.dest, args.bounds)
    if args.command == "fetch-climate":
        return _fetch_climate(args.dest, args.bounds)
    if args.command == "sample-dem":
        return _sample_dem(args.catalog, args.dem)
    if args.command == "sample-landcover":
        return _sample_landcover(args.catalog, args.landcover)
    if args.command == "sample-climate":
        return _sample_climate(args.catalog, args.climate)
    if args.command == "build-similarity":
        return _build_similarity(args.catalog, args.dem, calibrate_only=args.calibrate_only)
    if (args.origin is None) != (args.max_hours is None):
        rank.error("--origin and --max-hours must be given together")
    try:
        return _rank(
            args.catalog,
            args.seeds,
            args.origin,
            args.max_hours,
            args.valhalla_url,
            args.month,
            dict(args.weight) if args.weight else None,
            args.limit,
        )
    except ValueError as exc:
        rank.error(str(exc))


def _ingest(
    osm_paths: list[Path],
    catalog_path: Path,
    *,
    fresh: bool = False,
    list_dayhikes: bool = False,
) -> int:
    catalog = build_catalog(
        osm_paths,
        catalog_path,
        progress=lambda message: print(message, file=sys.stderr, flush=True),
        fresh=fresh,
    )
    print(catalog.path)
    print(catalog.ingest_report)
    if list_dayhikes:
        for hike in catalog.dayhikes:
            print(f"{hike.id}\t{hike.name}\t{hike.length_m}")
    return 0


def _fetch_extracts(dest: Path) -> int:
    for path in fetch_geofabrik_extracts(dest):
        print(path)
    return 0


def _fetch_dem(dest: Path, bounds: Bounds) -> int:
    paths = fetch_fabdem(
        dest,
        bounds,
        progress=lambda message: print(message, file=sys.stderr, flush=True),
    )
    for path in paths:
        print(path)
    return 0


def _fetch_landcover(dest: Path, bounds: Bounds) -> int:
    paths = fetch_worldcover(
        dest,
        bounds,
        progress=lambda message: print(message, file=sys.stderr, flush=True),
    )
    for path in paths:
        print(path)
    return 0


def _fetch_climate(dest: Path, bounds: Bounds) -> int:
    paths = fetch_worldclim(
        dest,
        bounds,
        progress=lambda message: print(message, file=sys.stderr, flush=True),
    )
    for path in paths:
        print(path)
    return 0


def _sample_dem(catalog_path: Path, raster_dir: Path) -> int:
    sampled = sample_dem(
        catalog_path,
        raster_dir,
        progress=lambda message: print(message, file=sys.stderr, flush=True),
    )
    print(sampled)
    return 0


def _sample_landcover(catalog_path: Path, raster_dir: Path) -> int:
    sampled = sample_landcover(
        catalog_path,
        raster_dir,
        progress=lambda message: print(message, file=sys.stderr, flush=True),
    )
    if sampled == 0:
        print(f"no vertices sampled: no usable landcover rasters cover them in {raster_dir}", file=sys.stderr)
    print(sampled)
    return 0


def _sample_climate(catalog_path: Path, raster_dir: Path) -> int:
    sampled = sample_climate(
        catalog_path,
        raster_dir,
        progress=lambda message: print(message, file=sys.stderr, flush=True),
    )
    if sampled == 0:
        print(f"no vertices sampled: no usable climate rasters cover them in {raster_dir}", file=sys.stderr)
    print(sampled)
    return 0


def _build_similarity(catalog_path: Path, dem_dir: Path, *, calibrate_only: bool) -> int:
    def progress(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    try:
        if calibrate_only:
            print(calibrate_similarity(catalog_path, progress=progress))
        else:
            print(build_similarity(catalog_path, dem_dir, progress=progress))
    except StaleSimilarity as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def _parse_weight(value: str) -> tuple[str, float]:
    name, _, weight = value.partition("=")
    try:
        return name.strip(), float(weight)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected MEASURE=WEIGHT, got {value!r}") from None


def _parse_bounds(value: str) -> Bounds:
    south_s, west_s, north_s, east_s = value.split(",", maxsplit=3)
    return Bounds(
        south=float(south_s.strip()),
        west=float(west_s.strip()),
        north=float(north_s.strip()),
        east=float(east_s.strip()),
    )


def _parse_origin(value: str) -> Origin:
    latitude_s, longitude_s = value.split(",", maxsplit=1)
    return Origin(latitude=float(latitude_s.strip()), longitude=float(longitude_s.strip()))


def _rank(
    catalog_path: Path,
    seeds: list[str],
    origin: Origin | None,
    max_hours: float | None,
    valhalla_url: str,
    month: int | None,
    weights: dict[str, float] | None,
    limit: int | None,
) -> int:
    catalog = Catalog.open(catalog_path)
    if origin is not None and max_hours is not None:
        ranking = filter_by_drive_time(
            rank_dayhikes(catalog, seeds, weights, month=month),
            origin,
            max_hours,
            ValhallaMatrixClient(valhalla_url),
        )
        ranking = Ranking(items=ranking.items[:limit])
    else:
        ranking = rank_dayhikes(catalog, seeds, weights, month=month, limit=limit)
    for item in ranking.items:
        line = f"{item.dayhike.id}\t{item.dayhike.name}\t{item.score}"
        if item.drive_time is not None:
            line += f"\t{item.drive_time.hours}"
        print(line)
    return 0
