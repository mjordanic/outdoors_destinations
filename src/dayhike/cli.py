from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dayhike.catalog import (
    Catalog,
    Origin,
    build_catalog,
    filter_by_drive_time,
    rank_dayhikes,
)
from dayhike.extracts import fetch_geofabrik_extracts
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
    rank.add_argument("seeds", nargs="+", help="Seed DayHike names")
    args = parser.parse_args(argv)
    if args.command == "ingest":
        return _ingest(
            args.osm, args.catalog, fresh=args.fresh, list_dayhikes=args.list_dayhikes
        )
    if args.command == "fetch-extracts":
        return _fetch_extracts(args.dest)
    if (args.origin is None) != (args.max_hours is None):
        rank.error("--origin and --max-hours must be given together")
    return _rank(args.catalog, args.seeds, args.origin, args.max_hours, args.valhalla_url)


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


def _parse_origin(value: str) -> Origin:
    latitude_s, longitude_s = value.split(",", maxsplit=1)
    return Origin(latitude=float(latitude_s.strip()), longitude=float(longitude_s.strip()))


def _rank(
    catalog_path: Path,
    seeds: list[str],
    origin: Origin | None,
    max_hours: float | None,
    valhalla_url: str,
) -> int:
    ranking = rank_dayhikes(Catalog.open(catalog_path), seeds)
    if origin is not None and max_hours is not None:
        ranking = filter_by_drive_time(
            ranking,
            origin,
            max_hours,
            ValhallaMatrixClient(valhalla_url),
        )
    for item in ranking.items:
        line = f"{item.dayhike.id}\t{item.dayhike.name}\t{item.score}"
        if item.drive_time is not None:
            line += f"\t{item.drive_time.hours}"
        print(line)
    return 0
