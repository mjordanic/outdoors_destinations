from __future__ import annotations

import argparse
import os
import socket
import sys
import time
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
from dayhike.destination_hikes import DEST_WORKERS_ENV
from dayhike.dem import DEM_SOURCES, Bounds, DEFAULT_DEM_DIR, fetch_dem, sample_dem
from dayhike.landcover import DEFAULT_LANDCOVER_DIR, fetch_worldcover, sample_landcover
from dayhike.parallel import WORKERS_ENV
from dayhike.climate import DEFAULT_CLIMATE_DIR, fetch_chelsa, sample_climate
from dayhike.commons import DEFAULT_COMMONS_DIR, count_commons, fetch_commons
from dayhike.extracts import fetch_geofabrik_extracts
from dayhike.regions import (
    DEFAULT_MAP_REGION,
    DEFAULT_REGION,
    Region,
    RegionError,
    bounds_around,
    extracts_of,
    labels_text,
    load_region,
    region_names,
    view_around,
)
from dayhike.highways import HighwayDriveTimeClient, HighwaySnaps
from dayhike.ingest import PATH_LEVELS
from dayhike.steps import STEP_READS, StepLedger, file_key, files_key
from dayhike.similarity import (
    CORE_MEASURES,
    MEASURE_NAMES,
    StaleSimilarity,
    build_similarity,
    calibrate_similarity,
    rank_dayhikes,
)
from dayhike.valhalla import DEFAULT_VALHALLA_URL, MAX_MATRIX_DISTANCE_M, VALHALLA_TIMEOUT_S, ValhallaMatrixClient

# The most highway snaps `serve` keeps beyond the export's (SuggestedLoop starts a Ranking asked of Valhalla):
# about 20 MB of memory.
SERVE_SNAPS_KEPT = 50_000


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
        "--pass-cache",
        type=Path,
        default=None,
        metavar="DIR",
        help="Keep the work file the passes over the snapshot leave in DIR, and start from it when "
        "the same snapshot files, paths levels and code come again (--fresh reads the snapshot again); "
        "it holds one snapshot, about the size of the work file (default: none)",
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
    ingest.add_argument(
        "--dem",
        type=Path,
        default=None,
        help="Directory of FABDEM (or Copernicus GLO-30) GeoTIFFs: a line whose ends differ by more than "
        "100 m starts at its lower end (ADR-0048). Without it, height orients no line",
    )
    ingest.add_argument(
        "--previous",
        type=Path,
        default=None,
        help="Catalog of an earlier build (its GeometryStore beside it): a line whose ends differ by "
        "80 to 120 m starts where that build started it, so a few metres of height cannot turn it round (ADR-0048)",
    )
    ingest.add_argument(
        "--external",
        action="append",
        default=[],
        metavar="SOURCE",
        help="Add the hikes of an external source cached by fetch-external, e.g. ie-sport-ireland, "
        "fi-lipas, no-turrutebasen, fr-ecrins-randonnees or it-odh-suedtirol (ADR-0062); repeatable",
    )
    ingest.add_argument(
        "--external-dir",
        type=Path,
        default=None,
        help="Directory of the external sources' caches (default: data/external)",
    )
    ingest.add_argument(
        "--paths",
        action="append",
        default=[],
        metavar="[INPUT=]LEVEL",
        help="Paths in no hiking relation read into the TrailGraph and the snapshot: graded (sac_scale) "
        "paths only; also named paths, footways and bridleways, which become named-trail hikes (trails, "
        "a US build, ADR-0066); also named tracks (named); or also unnamed paths (unnamed) (ADR-0065, "
        "default: graded). INPUT=LEVEL sets one input file or directory's level, repeatable: "
        "a Europe and US build reads data/osm/us=trails (ADR-0069)",
    )
    _add_workers(ingest)
    ingest.add_argument(
        "--dest-workers",
        type=_parse_workers,
        default=None,
        metavar="N",
        help="Worker processes for the destination hikes' tile search, which runs before the snapshot "
        f"loads, the same result with any number (default: ${DEST_WORKERS_ENV}, else the --workers count)",
    )
    _add_resume(ingest)
    fetch_ext = subparsers.add_parser(
        "fetch-external",
        help="Download an external hike source into data/external/<source> (does not ingest)",
    )
    fetch_ext.add_argument(
        "--source",
        required=True,
        help="Sources register id, e.g. ie-sport-ireland, fi-lipas (fi is that Finland source), "
        "no-turrutebasen (no is that Norway source), fr-ecrins-randonnees or it-odh-suedtirol; fr is every French "
        "dataset whose licence and file pass the gate (ADR-0062)",
    )
    fetch_ext.add_argument(
        "--dest",
        type=Path,
        default=None,
        help="Directory of the external sources' caches (default: data/external)",
    )
    fetch = subparsers.add_parser(
        "fetch-extracts",
        help="Download Geofabrik PBFs into data/osm (does not ingest)",
    )
    _add_regions(fetch, DEFAULT_REGION, "whose extracts to download")
    fetch.add_argument(
        "--dest",
        type=Path,
        default=Path("data/osm"),
        help="Directory for downloaded PBFs (default: data/osm)",
    )
    fetch_dem = subparsers.add_parser(
        "fetch-dem",
        help="Download FABDEM (or Copernicus GLO-30) GeoTIFFs covering the regions (default Alps+Dinarides) into data/dem",
    )
    fetch_dem.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_DEM_DIR,
        help="Directory for GeoTIFFs (default: data/dem)",
    )
    _add_bounds(fetch_dem)
    fetch_dem.add_argument(
        "--source",
        choices=list(DEM_SOURCES),
        default="fabdem",
        help="Elevation map: fabdem, bare earth, non-commercial (default); or copernicus, "
        "GLO-30 with tree tops and roofs, commercial use allowed; or 3dep, USGS 3DEP 1 arc-second, "
        "bare earth, US only, public domain. Keep one of fabdem and copernicus per directory; "
        "3dep tiles beside them are read where they are",
    )
    fetch_lc = subparsers.add_parser(
        "fetch-landcover",
        help="Download ESA WorldCover GeoTIFFs covering the regions (default Alps+Dinarides) into data/landcover",
    )
    fetch_lc.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_LANDCOVER_DIR,
        help="Directory for GeoTIFFs (default: data/landcover)",
    )
    _add_bounds(fetch_lc)
    fetch_cl = subparsers.add_parser(
        "fetch-climate",
        help="Download CHELSA monthly GeoTIFFs covering the regions (default Alps+Dinarides) into data/climate",
    )
    fetch_cl.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_CLIMATE_DIR,
        help="Directory for GeoTIFFs (default: data/climate)",
    )
    _add_bounds(fetch_cl)
    sample = subparsers.add_parser(
        "sample-dem",
        help="Sample local FABDEM (or Copernicus GLO-30) onto GeometryStore vertices and Catalog elevation",
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
        help="Directory of FABDEM or Copernicus GLO-30 GeoTIFFs, and USGS 3DEP ones for the US (default: data/dem)",
    )
    sample.add_argument(
        "--landcover",
        type=Path,
        default=DEFAULT_LANDCOVER_DIR,
        help="WorldCover GeoTIFFs; Copernicus GLO-30 needs them for the ground under trees (default: data/landcover)",
    )
    _add_workers(sample)
    _add_resume(sample)
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
    _add_workers(sample_lc)
    _add_resume(sample_lc)
    fetch_cm = subparsers.add_parser(
        "fetch-commons",
        help="Download the Wikimedia Commons geo_tags, page and image dumps (about 27 GB) into data/commons",
    )
    fetch_cm.add_argument(
        "--dest", type=Path, default=DEFAULT_COMMONS_DIR, help="Directory for the dumps (default: data/commons)"
    )
    count_cm = subparsers.add_parser(
        "count-commons",
        help="Count distinct Commons uploaders within 150 m of each DayHike into the Catalog (shown, never scored)",
    )
    count_cm.add_argument(
        "--catalog",
        type=Path,
        default=Path("data/catalog.sqlite"),
        help="SQLite Catalog path (default: data/catalog.sqlite)",
    )
    count_cm.add_argument(
        "--commons", type=Path, default=DEFAULT_COMMONS_DIR, help="Directory of the Commons dumps (default: data/commons)"
    )
    count_cm.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="Keep the parsed Commons points in DIR, and read them from there while the dumps and "
        "the Catalog's bounds are the same (default: parse the dumps every run)",
    )
    _add_workers(count_cm)
    _add_resume(count_cm)
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
    _add_resume(sample_cl)
    sample_tg = subparsers.add_parser(
        "sample-trailgraph",
        help="Sample local DEM, LandCover and climate rasters onto the TrailGraph's vertices",
    )
    sample_tg.add_argument(
        "--catalog",
        type=Path,
        default=Path("data/catalog.sqlite"),
        help="SQLite Catalog path (default: data/catalog.sqlite)",
    )
    sample_tg.add_argument("--dem", type=Path, default=DEFAULT_DEM_DIR, help="FABDEM or Copernicus GLO-30 GeoTIFFs (default: data/dem)")
    sample_tg.add_argument(
        "--landcover", type=Path, default=DEFAULT_LANDCOVER_DIR, help="WorldCover GeoTIFFs (default: data/landcover)"
    )
    sample_tg.add_argument(
        "--climate", type=Path, default=DEFAULT_CLIMATE_DIR, help="CHELSA GeoTIFFs (default: data/climate)"
    )
    _add_workers(sample_tg)
    _add_resume(sample_tg)
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
        help="Directory of FABDEM or Copernicus GLO-30 GeoTIFFs for SolarAspect (default: data/dem)",
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
    _add_workers(similarity)
    _add_resume(similarity)
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
        help="Valhalla serves highways-only tiles: estimate DriveTime (see docs/technical.md, 'Highway tiles')",
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
    rank.add_argument(
        "--one-way",
        action="store_true",
        help="Compare every hike one way; by default every hike, the Seeds included, "
        "is measured with its way back to its start",
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
        "--one-way",
        action="store_true",
        help="Compare every hike one way; by default every hike, the Seed included, "
        "is measured with its way back to its start",
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
        help="Valhalla serves highways-only tiles: estimate DriveTime (see docs/technical.md, 'Highway tiles')",
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
    export.add_argument(
        "--geonames",
        type=Path,
        help="GeoNames allCountries.zip for the page's place search; downloaded there when missing",
    )
    export.add_argument(
        "--countries",
        type=Path,
        help="Natural Earth 10m admin-0 countries GeoJSON: the country each hike starts in, for its emergency number",
    )
    export.add_argument(
        "--borders",
        type=Path,
        help="Natural Earth 10m admin-0 land boundary lines GeoJSON, with --countries: a hike crossing or starting "
        "within 2 km of a border also shows the other country's emergency number",
    )
    export.add_argument(
        "--osm-borders",
        type=Path,
        action="append",
        default=[],
        help="OpenStreetMap national boundaries (osmium tags-filter <extract> r/admin_level=2), repeatable, with "
        "--countries: a hike starting within 2 km of a border takes the country OSM puts it in and also shows the one it had",
    )
    export.add_argument(
        "--region",
        action="append",
        default=[],
        help=f"A region the Catalog covers: one of {', '.join(region_names())}, or a region .toml file; "
        "repeatable. The site's pages name them (with only regions in Europe, they say Europe); without it, they "
        "name where the hikes start (Europe, the United States). Refused when the hikes start outside every region given",
    )
    export.add_argument(
        "--external-dir",
        type=Path,
        default=None,
        help="Directory of the external sources' caches (default: data/external): an OpenStreetMap hike that "
        "lies on one of their records names it, and an external hike links its record's page as the source gives it now",
    )
    _add_workers(export)
    _add_resume(export)
    countries = subparsers.add_parser(
        "export-countries",
        help="Add the country each hike starts in (for its emergency number) beside an existing serving export",
    )
    countries.add_argument("--out", type=Path, default=Path("data/web-serving"))
    countries.add_argument(
        "--countries",
        type=Path,
        help="Natural Earth 10m admin-0 countries GeoJSON; without it, the country of the place nearest each Trailhead",
    )
    countries.add_argument(
        "--borders",
        type=Path,
        help="Natural Earth 10m admin-0 land boundary lines GeoJSON, with --countries: a hike crossing or starting "
        "within 2 km of a border also shows the other country's emergency number",
    )
    countries.add_argument(
        "--osm-borders",
        type=Path,
        action="append",
        default=[],
        help="OpenStreetMap national boundaries (osmium tags-filter <extract> r/admin_level=2), repeatable, with "
        "--countries: a hike starting within 2 km of a border takes the country OSM puts it in and also shows the one it had",
    )
    names = subparsers.add_parser(
        "export-name-index",
        help="Add the name search's index (names.npz) beside an existing serving export, so a server starts "
        "without reading every hike name (export-serving writes it too)",
    )
    names.add_argument("--out", type=Path, default=Path("data/web-serving"))
    places = subparsers.add_parser(
        "export-places", help="Add GeoNames place search and the hike search's Landmarks beside an existing serving export"
    )
    places.add_argument(
        "--geonames",
        type=Path,
        default=Path("data/geonames/allCountries.zip"),
        help="GeoNames allCountries.zip; downloaded there when missing",
    )
    places.add_argument("--out", type=Path, default=Path("data/web-serving"))
    cut = subparsers.add_parser(
        "cut-serving",
        help="Cut a finished serving export down to Europe, the US or a cut file's areas, as an export of its own",
    )
    cut.add_argument("--from", dest="source", type=Path, default=Path("data/web-serving"), help="The export to cut (read only)")
    cut.add_argument(
        "--keep",
        required=True,
        help="europe or us (the hikes east or west of 40° W), a cut file of dayhike/data/cuts by name (demo), "
        "or a cut or region .toml file",
    )
    cut.add_argument("--out", required=True, type=Path, help="Where to write the cut export")
    cut.add_argument(
        "--geonames",
        type=Path,
        default=Path("data/geonames/allCountries.zip"),
        help="The GeoNames allCountries.zip the export was made with: the cut's place search and Landmarks",
    )
    photos = subparsers.add_parser(
        "export-photos",
        help="Add up to two free Wikimedia Commons photos of each hike's named POIs to a serving export",
    )
    photos.add_argument("--catalog", type=Path, default=Path("data/catalog.sqlite"))
    photos.add_argument("--out", type=Path, default=Path("data/web-serving"))
    photos.add_argument(
        "--cache",
        type=Path,
        default=Path("data/commons-cache.sqlite"),
        help="Wikimedia API responses; a later run fetches only what this lacks",
    )
    photos.add_argument(
        "--warm-only",
        action="store_true",
        help="Only fill --cache with the answers for POIs on a hike, from the Catalog alone (run it right after ingest, "
        "beside the CPU steps); --out is not read",
    )
    osm_download = subparsers.add_parser(
        "export-osm-download",
        help="Write the ODbL download of the OpenStreetMap part of a serving export's hikes (hikes-osm.sqlite and a README)",
    )
    osm_download.add_argument("--data", type=Path, default=Path("data/web-serving"), help="Serving export directory")
    osm_download.add_argument("--out", type=Path, default=Path("data/osm-download"))
    osm_download.add_argument(
        "--data-date",
        help="Date (YYYY-MM-DD) of the OpenStreetMap data the export was built from; default: the day the export was written",
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
    serve.add_argument(
        "--goatcounter", help="GoatCounter code: count visits at https://CODE.goatcounter.com; omit for no analytics"
    )
    serve.add_argument(
        "--cors-origin",
        action="append",
        default=[],
        help="Another origin (https://host) whose page may call the API, e.g. a loading shell; repeatable",
    )
    serve.add_argument(
        "--site-url", help="The site's public origin (https://host): shared links then get a preview image"
    )
    serve.add_argument(
        "--tile-url",
        help="Raster base-map tile URL template with {z}/{x}/{y}; omit for tile.openstreetmap.org",
    )
    serve.add_argument(
        "--tile-attribution", help="The tile provider's credit (HTML), shown beside the OpenStreetMap credit"
    )
    serve.add_argument("--tile-max-zoom", type=int, help="The deepest zoom the tile provider serves (default 19)")
    serve.add_argument(
        "--pmtiles-url",
        help="A vector PMTiles base map (Protomaps basemap): the maps draw it with MapLibre GL JS, raster tiles the fallback",
    )
    serve.add_argument(
        "--download-url",
        help="Where the ODbL download (hikes-osm.sqlite, `dayhike export-osm-download`) is hosted: the credits page links it; empty or unset, no link",
    )
    coverage = subparsers.add_parser(
        "coverage-map", help="Draw the SVG map of the regions (default Europe) with the covered countries shaded"
    )
    _add_regions(coverage, DEFAULT_MAP_REGION, "to draw; several are drawn in one Equal Earth map")
    coverage.add_argument("--world", action="store_true", help="Draw the whole world instead of the regions")
    coverage.add_argument("--catalog", type=Path, default=Path("data/catalog.sqlite"))
    coverage.add_argument(
        "--countries", type=Path, required=True, help="Natural Earth admin-0 countries GeoJSON"
    )
    coverage.add_argument(
        "--out", type=Path, default=Path("src/dayhike/web/static/coverage.svg")
    )
    args = parser.parse_args(argv)
    if hasattr(args, "region_default"):
        try:
            args.region = [load_region(name) for name in args.region or [args.region_default]]
        except RegionError as exc:
            subparsers.choices[args.command].error(str(exc))
    if getattr(args, "workers", None) is not None:
        os.environ[WORKERS_ENV] = str(args.workers)  # the stage's code reads it (dayhike.parallel)
    if getattr(args, "dest_workers", None) is not None:
        os.environ[DEST_WORKERS_ENV] = str(args.dest_workers)
    if args.command in STEP_READS and not _STEP_RUNNING:
        return _run_step(args, argv)
    if args.command == "export-serving":
        try:
            regions = [load_region(name) for name in args.region]
        except RegionError as exc:
            export.error(str(exc))
        return _export_serving(
            args.catalog, args.out, args.valhalla_tiles, args.snaps, args.valhalla_url, args.geonames, args.countries,
            args.borders, regions, args.external_dir, args.osm_borders,
        )
    if args.command == "export-countries":
        from dayhike.serving import ServingExportError, export_countries

        try:
            print(export_countries(args.out, args.countries, args.borders, args.osm_borders))
        except ServingExportError as exc:
            print(exc, file=sys.stderr)
            return 1
        return 0
    if args.command == "export-name-index":
        from dayhike.serving import export_name_index

        print(export_name_index(args.out))
        return 0
    if args.command == "export-places":
        from dayhike.serving import export_landmarks, export_places, fetch_geonames

        geonames = fetch_geonames(args.geonames)
        print(export_places(geonames, args.out))
        print(export_landmarks(args.out, geonames))
        return 0
    if args.command == "cut-serving":
        from dayhike.cut import CutError, cut_serving, load_keep
        from dayhike.serving import ServingExportError

        try:
            keep = load_keep(args.keep)
        except CutError as exc:
            cut.error(str(exc))
        has_places = (args.source / "places.sqlite").is_file()
        try:
            print(
                cut_serving(
                    args.source, args.out, keep, geonames_path=args.geonames if has_places else None, progress=_progress
                )
            )
        except (StaleSimilarity, ServingExportError) as exc:
            print(exc, file=sys.stderr)
            return 1
        return 0
    if args.command == "export-photos":
        from dayhike.photos import export_photos, warm_photos

        if args.warm_only:
            try:
                warmed = warm_photos(args.catalog, args.cache, progress=lambda line: print(line, flush=True))
            except FileNotFoundError as exc:
                print(exc, file=sys.stderr)
                return 1
            print(f"{warmed} POIs warmed")
            return 0
        try:
            count = export_photos(
                args.catalog, args.out, args.cache, progress=lambda line: print(line, flush=True)
            )
        except FileNotFoundError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"{count} DayHikes with a photo")
        return 0
    if args.command == "export-osm-download":
        from dayhike.osm_download import export_osm_download

        print(export_osm_download(args.data, args.out, args.data_date) / "hikes-osm.sqlite")
        return 0
    if args.command == "serve":
        return _serve(
            args.data,
            args.host,
            args.port,
            args.valhalla_url,
            args.forwarded_hops,
            args.goatcounter,
            tuple(args.cors_origin),
            args.site_url,
            args.tile_url,
            args.tile_attribution,
            args.tile_max_zoom,
            args.pmtiles_url,
            args.download_url,
        )
    if args.command == "coverage-map":
        from dayhike.coverage import WORLD_VIEW, coverage_map

        if args.world:
            frame = {"view": WORLD_VIEW, "centre": None, "label": "the world"}
        elif len(args.region) == 1:
            (region,) = args.region
            frame = {"view": _wsen(region.view), "centre": region.centre, "label": region.label}
        else:
            label = labels_text([region.label for region in args.region])
            frame = {"view": _wsen(view_around(args.region)), "centre": None, "label": label}
        for country, count in coverage_map(args.catalog, args.countries, args.out, **frame):
            print(f"{country}\t{count}")
        return 0
    if args.command == "ingest":
        paths, paths_of = "graded", {}
        for value in args.paths:
            given, _, level = value.rpartition("=")
            if level not in PATH_LEVELS:
                ingest.error(f"--paths {value}: the level must be one of {', '.join(PATH_LEVELS)}")
            if given:
                paths_of[Path(given)] = level
            else:
                paths = level
        return _ingest(
            args.osm,
            args.catalog,
            fresh=args.fresh,
            list_dayhikes=args.list_dayhikes,
            snap_tolerance_m=args.snap_tolerance,
            dem_dir=args.dem,
            previous=args.previous,
            external=args.external,
            external_dir=args.external_dir,
            paths=paths,
            paths_of=paths_of,
            pass_cache=args.pass_cache,
        )
    if args.command == "sample-trailgraph":
        try:
            return _sample_trailgraph(args.catalog, args.dem, args.landcover, args.climate)
        except ValueError as exc:
            sample_tg.error(str(exc))
    if args.command == "compose":
        try:
            return _compose(
                args.data,
                args.route,
                args.name,
                dict(args.weight) if args.weight else None,
                args.month,
                args.limit,
                return_to_start=not args.one_way,
            )
        except ValueError as exc:
            compose.error(str(exc))
    if args.command == "fetch-external":
        from dayhike.external import EXTERNAL_DIR, fetch_external, fetch_french_datasets

        root = EXTERNAL_DIR if args.dest is None else args.dest
        if args.source == "fr":
            paths, skipped = fetch_french_datasets(root)
            for path in paths:
                print(path)
            for reason in skipped:
                print(f"skipped {reason}", file=sys.stderr)
            return 0
        try:
            print(fetch_external(args.source, root))
        except ValueError as exc:
            fetch_ext.error(str(exc))
        return 0
    if args.command == "fetch-extracts":
        return _fetch_extracts(args.dest, extracts_of(args.region))
    # Tiles cover each region's box; CHELSA's one file per variable is clipped to the box around them all.
    if args.command == "fetch-dem":
        return _fetch_dem(args.dest, [args.bounds] if args.bounds else [r.bounds for r in args.region], args.source)
    if args.command == "fetch-landcover":
        return _fetch_landcover(args.dest, [args.bounds] if args.bounds else [r.bounds for r in args.region])
    if args.command == "fetch-climate":
        return _fetch_climate(args.dest, args.bounds or bounds_around(args.region))
    if args.command == "sample-dem":
        try:
            return _sample_dem(args.catalog, args.dem, args.landcover)
        except ValueError as exc:
            sample.error(str(exc))
    if args.command == "sample-landcover":
        return _sample_landcover(args.catalog, args.landcover)
    if args.command == "fetch-commons":
        for path in fetch_commons(args.dest, progress=_progress):
            print(path)
        return 0
    if args.command == "count-commons":
        print(count_commons(args.catalog, args.commons, progress=_progress, cache_dir=args.cache_dir))
        return 0
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
        except (ValueError, FileNotFoundError) as exc:
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
            return_to_start=not args.one_way,
        )
    except (ValueError, FileNotFoundError) as exc:
        rank.error(str(exc))


def _ingest(
    osm_paths: list[Path],
    catalog_path: Path,
    *,
    fresh: bool = False,
    list_dayhikes: bool = False,
    snap_tolerance_m: float = SNAP_TOLERANCE_M,
    dem_dir: Path | None = None,
    previous: Path | None = None,
    external: list[str] | tuple[str, ...] = (),
    external_dir: Path | None = None,
    paths: str = "graded",
    paths_of: dict[Path, str] | None = None,
    pass_cache: Path | None = None,
) -> int:
    catalog = build_catalog(
        osm_paths,
        catalog_path,
        progress=_progress,
        fresh=fresh,
        snap_tolerance_m=snap_tolerance_m,
        dem_dir=dem_dir,
        previous=previous,
        external=external,
        external_dir=external_dir,
        paths=paths,
        paths_of=paths_of,
        pass_cache=pass_cache,
    )
    print(catalog.path)
    print(catalog.ingest_report)
    if list_dayhikes:
        for hike in catalog.dayhikes:
            print(f"{hike.id}\t{hike.name}\t{hike.length_m}")
    return 0


def _fetch_extracts(dest: Path, urls: list[str]) -> int:
    for path in fetch_geofabrik_extracts(dest, urls=urls):
        print(path)
    return 0


def _fetch_dem(dest: Path, boxes: list[Bounds], source: str) -> int:
    paths = [
        path
        for bounds in boxes
        for path in fetch_dem(
            dest,
            bounds,
            source=DEM_SOURCES[source],
            progress=_progress,
        )
    ]
    for path in dict.fromkeys(paths):
        print(path)
    return 0


def _fetch_landcover(dest: Path, boxes: list[Bounds]) -> int:
    paths = [
        path
        for bounds in boxes
        for path in fetch_worldcover(
            dest,
            bounds,
            progress=_progress,
        )
    ]
    for path in dict.fromkeys(paths):
        print(path)
    return 0


def _fetch_climate(dest: Path, bounds: Bounds) -> int:
    paths = fetch_chelsa(
        dest,
        bounds,
        progress=_progress,
    )
    for path in paths:
        print(path)
    return 0


def _sample_dem(catalog_path: Path, raster_dir: Path, landcover_dir: Path) -> int:
    sampled = sample_dem(
        catalog_path,
        raster_dir,
        landcover_dir=landcover_dir,
        progress=_progress,
    )
    print(sampled)
    return 0


def _sample_landcover(catalog_path: Path, raster_dir: Path) -> int:
    sampled = sample_landcover(
        catalog_path,
        raster_dir,
        progress=_progress,
    )
    if sampled == 0:
        print(f"no vertices sampled: no usable landcover rasters cover them in {raster_dir}", file=sys.stderr)
    print(sampled)
    return 0


def _sample_climate(catalog_path: Path, raster_dir: Path) -> int:
    sampled = sample_climate(
        catalog_path,
        raster_dir,
        progress=_progress,
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
            progress=_progress,
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
    *,
    return_to_start: bool = True,
) -> int:
    from dayhike.serving import ServingData
    from dayhike.similarity import DEFAULT_WEIGHTS
    from dayhike.trailgraph import Route

    serving = ServingData(data)
    if serving.trailgraph is None:
        print(f"{data}: no TrailGraph in this export; ingest again, then export-serving", file=sys.stderr)
        return 1
    route = Route.parse(route_text)
    hike = serving.compose(route, name, return_to_start)
    facts = hike["facts"]
    back = hike["way_back"]
    print(
        f"{hike['name']}\t{facts['length_m']:.0f} m\tclimb {hike['climb_m']} m\tdescent {hike['descent_m']} m"
        f"\thighest {facts['highest_m']}\tround trip {bool(facts['round_trip'])}"
        + ("" if back is None else f"\tway back {back['m']:.0f} m ({back['kind']})")
    )
    for result in serving.rank(
        route,
        DEFAULT_WEIGHTS if weights is None else weights,
        month=month,
        limit=limit,
        return_to_start=return_to_start,
        loops=True,
    ):
        for indent, hike in (("", result), *(("  ", version) for version in result["versions"])):
            listed = hike["route"]["text"] if hike["id"] is None else hike["id"]  # a SuggestedLoop by its link
            print(f"{indent}{listed}\t{hike['name']}\t{hike['score']}")
    return 0


def _build_similarity(
    catalog_path: Path, dem_dir: Path, landcover_dir: Path, *, calibrate_only: bool
) -> int:
    try:
        if calibrate_only:
            print(calibrate_similarity(catalog_path, progress=_progress))
        else:
            print(
                build_similarity(
                    catalog_path, dem_dir, landcover_dir=landcover_dir, progress=_progress
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
    geonames: Path | None,
    countries: Path | None,
    borders: Path | None,
    regions: list[Region],
    external_dir: Path | None = None,
    osm_borders: tuple[Path, ...] | list[Path] = (),
) -> int:
    from dayhike.external import EXTERNAL_DIR
    from dayhike.serving import ServingExportError, export_serving, fetch_geonames

    try:
        print(
            export_serving(
                catalog_path,
                out,
                valhalla_dir=valhalla_tiles,
                snaps_path=snaps,
                valhalla_url=valhalla_url,
                geonames_path=geonames and fetch_geonames(geonames),
                countries_path=countries,
                borders_path=borders,
                osm_borders_paths=osm_borders,
                regions=regions,
                external_dir=EXTERNAL_DIR if external_dir is None else external_dir,
                progress=_progress,
            )
        )
    except (StaleSimilarity, ServingExportError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def _serve(
    data: Path,
    host: str,
    port: int,
    valhalla_url: str | None,
    forwarded_hops: int,
    goatcounter: str | None,
    cors_origins: tuple[str, ...],
    site_url: str | None = None,
    tile_url: str | None = None,
    tile_attribution: str | None = None,
    tile_max_zoom: int | None = None,
    pmtiles_url: str | None = None,
    download_url: str | None = None,
) -> int:
    try:
        import uvicorn

        from dayhike.web import BaseMap, create_app
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
        snaps = HighwaySnaps(valhalla_url, data / "snaps.npz", keep=SERVE_SNAPS_KEPT)
        snaps.path = None  # the export is read-only: keep new snaps in memory, the most recent SERVE_SNAPS_KEPT

        def drive_client(max_hours: float) -> HighwayDriveTimeClient:
            return HighwayDriveTimeClient(max_hours, snaps)

    tiles = {"url": tile_url, "attribution": tile_attribution, "max_zoom": tile_max_zoom, "pmtiles": pmtiles_url}
    try:
        app = create_app(
            serving,
            drive_client,
            forwarded_hops=forwarded_hops,
            goatcounter=goatcounter,
            cors_origins=cors_origins,
            site_url=site_url,
            basemap=BaseMap(**{key: value for key, value in tiles.items() if value is not None}),
            download_url=download_url,
        )
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    # A stalled Valhalla call would hold the one-Ranking lock past the request; time it
    # out instead so /api/rank answers 503 and frees the lock.
    socket.setdefaulttimeout(VALHALLA_TIMEOUT_S)
    uvicorn.run(app, host=host, port=port, log_level="info", proxy_headers=False)
    return 0


def _progress(message: str) -> None:
    # Each line says when it was written (UTC), so a long rebuild's log times every step.
    print(f"{time.strftime('%H:%M:%SZ', time.gmtime())} {message}", file=sys.stderr, flush=True)


_STEP_RUNNING: list[str] = []  # the step ``_run_step`` is running: ``main`` then runs its command


def _add_resume(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--resume",
        action="store_true",
        help="Skip this step when it finished before with the same inputs, code and earlier steps "
        "(its marker is beside the Catalog, in CATALOG.steps); else run it",
    )


# Arguments that do not decide what a step writes, or that name its own output.
_NOT_INPUTS = {
    "command", "catalog", "workers", "dest_workers", "resume", "fresh", "list_dayhikes", "out", "pass_cache", "cache_dir",
    "region_default",
}


def _input_key(value: object) -> object:
    if isinstance(value, Path):
        return files_key(value) if value.is_dir() else file_key(value) or str(value)
    if isinstance(value, (list, tuple)):
        return [_input_key(item) for item in value]
    return str(value)


def _step_inputs(args: argparse.Namespace) -> dict[str, object]:
    inputs = {"catalog_exists": args.catalog.is_file()}
    if args.command == "export-serving":
        inputs["exported"] = (args.out / "meta.json").is_file()
        inputs["out"] = str(args.out)
    inputs.update({name: _input_key(value) for name, value in sorted(vars(args).items()) if name not in _NOT_INPUTS})
    return inputs


def _run_step(args: argparse.Namespace, argv: list[str] | None) -> int:
    """Run a rebuild step, or with ``--resume`` skip it when its marker says it finished with
    these inputs; leave its marker when it succeeds (dayhike.steps)."""
    ledger = StepLedger(args.catalog)
    if args.resume and ledger.done(args.command, _step_inputs(args)):
        _progress(f"{args.command}: finished before with the same inputs, skipped (--resume)")
        return 0
    ledger.clear(args.command)
    _STEP_RUNNING.append(args.command)
    try:
        code = main(argv)
    finally:
        _STEP_RUNNING.pop()
    if code == 0:
        ledger.mark(args.command, _step_inputs(args))
    return code


def _add_workers(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--workers",
        type=_parse_workers,
        default=None,
        metavar="N",
        help=f"Worker processes to use, the same result with any number (default: ${WORKERS_ENV}, else 1)",
    )


def _parse_workers(value: str) -> int:
    try:
        count = int(value)
    except ValueError:
        count = 0
    if count < 1:
        raise argparse.ArgumentTypeError(f"expected a whole number of at least 1, got {value!r}")
    return count


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


def _add_regions(command: argparse.ArgumentParser, default: str, what: str) -> None:
    command.add_argument(
        "--region",
        action="append",
        help=f"A region {what}: one of {', '.join(region_names())}, or a region .toml file; "
        f"repeatable (default: {default})",
    )
    command.set_defaults(region_default=default)


def _add_bounds(command: argparse.ArgumentParser) -> None:
    """--region (the box around the regions' bounds) or --bounds; neither is the default region's box."""
    where = command.add_mutually_exclusive_group()
    where.add_argument(
        "--region",
        action="append",
        help=f"A region whose bounds to cover: one of {', '.join(region_names())}, or a region .toml file; "
        f"repeatable (default: {DEFAULT_REGION})",
    )
    where.add_argument(
        "--bounds", type=_parse_bounds, default=None, help="south,west,north,east in WGS84, instead of --region"
    )
    command.set_defaults(region_default=DEFAULT_REGION)


def _wsen(box: Bounds) -> tuple[float, float, float, float]:
    return (box.west, box.south, box.east, box.north)


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
    *,
    return_to_start: bool = True,
) -> int:
    catalog = Catalog.open(catalog_path)
    if origin is not None and max_hours is not None:
        ranking = _filter_by_drive_time(
            rank_dayhikes(catalog, seeds, weights, month=month, return_to_start=return_to_start),
            catalog_path,
            origin,
            max_hours,
            valhalla_url,
            highways,
        )
        ranking = Ranking(items=ranking.items[:limit])
    else:
        ranking = rank_dayhikes(
            catalog, seeds, weights, month=month, limit=limit, return_to_start=return_to_start
        )
    for item in ranking.items:
        line = f"{item.dayhike.id}\t{item.dayhike.name}\t{item.score}"
        if item.drive_time is not None:
            line += f"\t{item.drive_time.hours}"
        print(line)
    return 0
