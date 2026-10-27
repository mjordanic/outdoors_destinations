from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

from dayhike.catalog import (
    MAX_CAR_SPEED_MS,
    SNAP_TOLERANCE_M,
    Catalog,
    Origin,
    RankedDayHike,
    Ranking,
    build_catalog,
    filter_by_drive_time,
)
from dayhike.dem import Bounds, DEFAULT_DEM_BOUNDS, DEFAULT_DEM_DIR, fetch_fabdem, sample_dem
from dayhike.landcover import DEFAULT_LANDCOVER_DIR, fetch_worldcover, sample_landcover
from dayhike.climate import DEFAULT_CLIMATE_DIR, fetch_chelsa, sample_climate
from dayhike.extracts import fetch_geofabrik_extracts
from dayhike.highways import HighwayDriveTimeClient, HighwaySnaps
from dayhike.similarity import (
    CORE_MEASURES,
    MEASURE_NAMES,
    StaleSimilarity,
    build_similarity,
    calibrate_similarity,
    rank_dayhikes,
)
from dayhike.valhalla import DEFAULT_VALHALLA_URL, MAX_MATRIX_DISTANCE_M, ValhallaMatrixClient


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
    ingest.add_argument(
        "--snap-tolerance",
        type=_parse_snap_tolerance,
        default=SNAP_TOLERANCE_M,
        metavar="METRES",
        help="TrailGraph: join a dead-end tip to another way this close, at most 50 "
        f"(default: {SNAP_TOLERANCE_M})",
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
        help="Download CHELSA monthly GeoTIFFs covering Alps+Dinarides into data/climate",
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
        help="Sample local climate rasters into Catalog hike-level monthly means",
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
        help="Directory of CHELSA GeoTIFFs (default: data/climate)",
    )
    sample_tg = subparsers.add_parser(
        "sample-trailgraph",
        help="Sample local FABDEM, LandCover and climate rasters onto the TrailGraph's vertices",
    )
    sample_tg.add_argument(
        "--catalog",
        type=Path,
        default=Path("data/catalog.sqlite"),
        help="SQLite Catalog path (default: data/catalog.sqlite)",
    )
    sample_tg.add_argument("--dem", type=Path, default=DEFAULT_DEM_DIR, help="FABDEM GeoTIFFs (default: data/dem)")
    sample_tg.add_argument(
        "--landcover", type=Path, default=DEFAULT_LANDCOVER_DIR, help="WorldCover GeoTIFFs (default: data/landcover)"
    )
    sample_tg.add_argument(
        "--climate", type=Path, default=DEFAULT_CLIMATE_DIR, help="CHELSA GeoTIFFs (default: data/climate)"
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
        "--landcover",
        type=Path,
        default=DEFAULT_LANDCOVER_DIR,
        help="Directory of WorldCover GeoTIFFs for WaterSide (default: data/landcover)",
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
        "--highways",
        action="store_true",
        help="Valhalla serves highways-only tiles: estimate DriveTime (see README)",
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
        "excluded. Omit to weigh the core Measures equally ("
        + ", ".join(CORE_MEASURES)
        + "). Measures: "
        + ", ".join(MEASURE_NAMES),
    )
    rank.add_argument("seeds", nargs="+", help="Seed DayHike names")
    compose = subparsers.add_parser(
        "compose",
        help="Compose a hike from TrailGraph Segments and rank DayHikes like it (reads a serving export)",
    )
    compose.add_argument("--data", type=Path, default=Path("data/web-serving"), help="Serving export directory")
    compose.add_argument("--name", default=None, help="Name of the hike (default: proposed from its trails)")
    compose.add_argument("--month", type=int, default=None, help="QueryMonth 1-12")
    compose.add_argument(
        "--limit", type=int, default=10, help="Print the best this many DayHikes like it (default: 10)"
    )
    compose.add_argument(
        "--weight",
        action="append",
        type=_parse_weight,
        metavar="MEASURE=WEIGHT",
        help="Weight 0-100 of one Measure, repeatable, as for rank",
    )
    compose.add_argument(
        "route", help="The hike as its shared link holds it: start:step,step,... (the text after #compose=)"
    )
    nearby = subparsers.add_parser(
        "nearby", help="List DayHikes within a max DriveTime of an Origin, nearest first"
    )
    nearby.add_argument("--catalog", required=True, type=Path)
    nearby.add_argument(
        "--origin", required=True, type=_parse_origin, help="Origin as latitude,longitude"
    )
    nearby.add_argument(
        "--max-hours", required=True, type=float, help="Maximum DriveTime from Origin in hours"
    )
    nearby.add_argument(
        "--valhalla-url",
        default=DEFAULT_VALHALLA_URL,
        help="Valhalla base URL for /sources_to_targets",
    )
    nearby.add_argument(
        "--highways",
        action="store_true",
        help="Valhalla serves highways-only tiles: estimate DriveTime (see README)",
    )
    export = subparsers.add_parser(
        "export-serving", help="Write the small read-only dataset the web demo serves"
    )
    export.add_argument("--catalog", type=Path, default=Path("data/catalog.sqlite"))
    export.add_argument("--out", type=Path, default=Path("data/web-serving"))
    export.add_argument(
        "--valhalla-tiles",
        type=Path,
        help="Directory with the highways-only valhalla_tiles.tar and its valhalla.json",
    )
    export.add_argument("--snaps", type=Path, help="Existing highway snap cache to start from")
    export.add_argument(
        "--valhalla-url", help="Running highways-only Valhalla: snap every Trailhead now"
    )
    serve = subparsers.add_parser("serve", help="Serve the web demo from a serving export")
    serve.add_argument("--data", type=Path, default=Path("data/web-serving"))
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument(
        "--valhalla-url",
        help="Highways-only Valhalla for the drive-time filter; omit to turn the filter off",
    )
    serve.add_argument(
        "--forwarded-hops",
        type=int,
        default=0,
        help="Proxies that append to X-Forwarded-For in front of the app (1 on Cloud Run)",
    )
    coverage = subparsers.add_parser(
        "coverage-map", help="Draw the SVG map of Europe with the covered countries shaded"
    )
    coverage.add_argument("--catalog", type=Path, default=Path("data/catalog.sqlite"))
    coverage.add_argument(
        "--countries", type=Path, required=True, help="Natural Earth admin-0 countries GeoJSON"
    )
    coverage.add_argument(
        "--out", type=Path, default=Path("src/dayhike/web/static/coverage.svg")
    )
    args = parser.parse_args(argv)
    if args.command == "export-serving":
        return _export_serving(args.catalog, args.out, args.valhalla_tiles, args.snaps, args.valhalla_url)
    if args.command == "serve":
        return _serve(args.data, args.host, args.port, args.valhalla_url, args.forwarded_hops)
    if args.command == "coverage-map":
        from dayhike.coverage import coverage_map

        for country, count in coverage_map(args.catalog, args.countries, args.out):
            print(f"{country}\t{count}")
        return 0
    if args.command == "ingest":
        return _ingest(
            args.osm,
            args.catalog,
            fresh=args.fresh,
            list_dayhikes=args.list_dayhikes,
            snap_tolerance_m=args.snap_tolerance,
        )
    if args.command == "sample-trailgraph":
        return _sample_trailgraph(args.catalog, args.dem, args.landcover, args.climate)
    if args.command == "compose":
        try:
            return _compose(
                args.data,
                args.route,
                args.name,
                dict(args.weight) if args.weight else None,
                args.month,
                args.limit,
            )
        except ValueError as exc:
            compose.error(str(exc))
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
        return _build_similarity(
            args.catalog, args.dem, args.landcover, calibrate_only=args.calibrate_only
        )
    if args.command == "nearby":
        try:
            return _nearby(
                args.catalog, args.origin, args.max_hours, args.valhalla_url, args.highways
            )
        except ValueError as exc:
            nearby.error(str(exc))
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
            args.highways,
        )
    except ValueError as exc:
        rank.error(str(exc))


def _ingest(
    osm_paths: list[Path],
    catalog_path: Path,
    *,
    fresh: bool = False,
    list_dayhikes: bool = False,
    snap_tolerance_m: float = SNAP_TOLERANCE_M,
) -> int:
    catalog = build_catalog(
        osm_paths,
        catalog_path,
        progress=lambda message: print(message, file=sys.stderr, flush=True),
        fresh=fresh,
        snap_tolerance_m=snap_tolerance_m,
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
    paths = fetch_chelsa(
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


def _sample_trailgraph(catalog_path: Path, dem_dir: Path, landcover_dir: Path, climate_dir: Path) -> int:
    from dayhike.trailgraph import sample_trailgraph

    print(
        sample_trailgraph(
            catalog_path,
            dem_dir=dem_dir,
            landcover_dir=landcover_dir,
            climate_dir=climate_dir,
            progress=lambda message: print(message, file=sys.stderr, flush=True),
        )
    )
    return 0


def _compose(
    data: Path,
    route_text: str,
    name: str | None,
    weights: dict[str, float] | None,
    month: int | None,
    limit: int,
) -> int:
    from dayhike.serving import ServingData
    from dayhike.similarity import DEFAULT_WEIGHTS
    from dayhike.trailgraph import Route

    serving = ServingData(data)
    if serving.trailgraph is None:
        print(f"{data}: no TrailGraph in this export; ingest again, then export-serving", file=sys.stderr)
        return 1
    route = Route.parse(route_text)
    hike = serving.compose(route, name)
    facts = hike["facts"]
    print(
        f"{hike['name']}\t{hike['walk_m']:.0f} m\tclimb {hike['climb_m']} m\tdescent {hike['descent_m']} m"
        f"\thighest {facts['highest_m']}\tround trip {bool(facts['round_trip'])}"
    )
    for result in serving.rank(route, DEFAULT_WEIGHTS if weights is None else weights, month=month, limit=limit):
        print(f"{result['id']}\t{result['name']}\t{result['score']}")
    return 0


def _build_similarity(
    catalog_path: Path, dem_dir: Path, landcover_dir: Path, *, calibrate_only: bool
) -> int:
    def progress(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    try:
        if calibrate_only:
            print(calibrate_similarity(catalog_path, progress=progress))
        else:
            print(
                build_similarity(
                    catalog_path, dem_dir, landcover_dir=landcover_dir, progress=progress
                )
            )
    except StaleSimilarity as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def _export_serving(
    catalog_path: Path,
    out: Path,
    valhalla_tiles: Path | None,
    snaps: Path | None,
    valhalla_url: str | None,
) -> int:
    from dayhike.serving import ServingExportError, export_serving

    def progress(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    try:
        print(
            export_serving(
                catalog_path,
                out,
                valhalla_dir=valhalla_tiles,
                snaps_path=snaps,
                valhalla_url=valhalla_url,
                progress=progress,
            )
        )
    except (StaleSimilarity, ServingExportError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def _serve(data: Path, host: str, port: int, valhalla_url: str | None, forwarded_hops: int) -> int:
    try:
        import uvicorn

        from dayhike.web import VALHALLA_TIMEOUT_S, create_app
    except ImportError:
        print("the web demo needs its extra: uv sync --extra web", file=sys.stderr)
        return 1
    from dayhike.serving import ServingData

    serving = ServingData(data)
    drive_client = None
    if valhalla_url is not None:
        if not (data / "snaps.npz").is_file():
            print(f"{data}: no snaps.npz; export with --valhalla-url first", file=sys.stderr)
            return 1
        snaps = HighwaySnaps(valhalla_url, data / "snaps.npz")
        snaps.path = None  # the export is read-only: keep new snaps in memory

        def drive_client(max_hours: float) -> HighwayDriveTimeClient:
            return HighwayDriveTimeClient(max_hours, snaps)

    app = create_app(serving, drive_client, forwarded_hops=forwarded_hops)
    # A stalled Valhalla call would hold the one-Ranking lock past the request; time it
    # out instead so /api/rank answers 503 and frees the lock.
    socket.setdefaulttimeout(VALHALLA_TIMEOUT_S)
    uvicorn.run(app, host=host, port=port, log_level="info", proxy_headers=False)
    return 0


def _parse_snap_tolerance(value: str) -> float:
    from dayhike.trailgraph import check_snap_tolerance

    try:
        metres = float(value)
        check_snap_tolerance(metres)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    return metres


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


def _filter_by_drive_time(
    ranking: Ranking,
    catalog_path: Path,
    origin: Origin,
    max_hours: float,
    valhalla_url: str,
    highways: bool,
) -> Ranking:
    if not highways:
        max_matrix_hours = MAX_MATRIX_DISTANCE_M / MAX_CAR_SPEED_MS / 3600
        if max_hours > max_matrix_hours:
            raise ValueError(f"--max-hours answers up to {max_matrix_hours:.1f} hours without --highways")
        return filter_by_drive_time(ranking, origin, max_hours, ValhallaMatrixClient(valhalla_url))
    snaps = HighwaySnaps(valhalla_url, catalog_path.with_suffix(".snaps.npz"))
    client = HighwayDriveTimeClient(max_hours, snaps)
    filtered = filter_by_drive_time(ranking, origin, max_hours, client)
    # Name what has no highway in reach rather than dropping it without a word.
    unreachable = set(client.unreachable)
    for item in ranking.items:
        place = (item.dayhike.trailhead.latitude, item.dayhike.trailhead.longitude)
        if place in unreachable:
            print(f"no highway in reach\t{item.dayhike.id}\t{item.dayhike.name}", file=sys.stderr)
    return filtered


def _nearby(
    catalog_path: Path,
    origin: Origin,
    max_hours: float,
    valhalla_url: str,
    highways: bool,
) -> int:
    catalog = Catalog.open(catalog_path)
    everything = Ranking(items=tuple(RankedDayHike(hike, 0.0) for hike in catalog.dayhikes))
    reachable = _filter_by_drive_time(
        everything, catalog_path, origin, max_hours, valhalla_url, highways
    )
    for item in sorted(reachable.items, key=lambda item: item.drive_time.hours):
        print(f"{item.dayhike.id}\t{item.dayhike.name}\t{item.drive_time.hours}")
    return 0


def _rank(
    catalog_path: Path,
    seeds: list[str],
    origin: Origin | None,
    max_hours: float | None,
    valhalla_url: str,
    month: int | None,
    weights: dict[str, float] | None,
    limit: int | None,
    highways: bool = False,
) -> int:
    catalog = Catalog.open(catalog_path)
    if origin is not None and max_hours is not None:
        ranking = _filter_by_drive_time(
            rank_dayhikes(catalog, seeds, weights, month=month),
            catalog_path,
            origin,
            max_hours,
            valhalla_url,
            highways,
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
