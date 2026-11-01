"""The regional harness: build a serving export of one region, and measure it around an Origin.

    uv run python tests/rebuild/regional.py build --pbf PBF [--bbox W,S,E,N] --data DATA --out OUT
    uv run python tests/rebuild/regional.py measure --export OUT/web-serving [--origin LAT,LON] [--json FILE]

``build`` runs this checkout's pipeline on one region: with ``--bbox`` it first cuts PBF
(a full snapshot such as europe-latest.osm.pbf, not its .catalog extract, which lacks what
newer filters read) with ``osmium extract -s smart``, so relations come out complete; else
PBF is such a cut already. The cut is linked into OUT/osm, so ingest's catalog extract,
cut with this checkout's filter list, lands there and a shared cut is never written. Then
``ingest --fresh``, ``sample-dem``, ``sample-landcover``, ``sample-climate``,
``sample-trailgraph`` and ``build-similarity`` on the rasters in DATA/dem, DATA/landcover
and DATA/climate (only read), and ``export-serving`` without Valhalla, into
OUT/web-serving. Each step logs to OUT/logs/<step>.log; OUT/build.json holds its seconds.

``measure`` prints the Briançon table of the coverage plan for one export (``measure``
below); with ``--origin`` it measures there, the loops searched from the Origin alone.
Compare two builds, before and after a change, on the same cut: only counts compare, as
each region has its own Calibration.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from dayhike.catalog import Origin, straight_line_distances_m
from dayhike.loops import LoopTarget, find_loops
from dayhike.serving import LOOP_MEASURED, ServingData, decode_polyline
from dayhike.web import create_app

LOOP_SEARCH_S = 60.0  # a find_loops search from the starts stops after this
BRIANCON_BBOX = "6.25,44.70,7.05,45.10"  # west,south,east,north: Hautes-Alpes around Briançon


@dataclass(frozen=True)
class Measurement:
    """Where and how to measure: the Origin (lat, lon), the radii, km, hikes are counted
    within, the loop lengths, km, searched from the starts (lat, lon) and asked of a
    Ranking within ``loop_max_km`` (its loops counted within ``loop_within_km``), and
    the first page's Wish length and distance limit, km."""

    origin: tuple[float, float]
    radii_km: tuple[float, ...] = (5.0, 10.0, 20.0)
    loop_lengths_km: tuple[float, ...] = (6.0, 10.0, 15.0)
    starts: tuple[tuple[float, float], ...] = ()
    loop_max_km: float = 15.0
    loop_within_km: float = 10.0
    page_length_km: float = 10.0
    page_max_km: float = 20.0


BRIANCON = Measurement(
    origin=(44.8986, 6.6436),
    # The Briançon coverage report's loop starts, from OSM nodes: the lower town (Sainte-Catherine,
    # 401577080), the Cité Vauban (the town node, 26692531), Puy-Saint-Pierre (1872878676) and
    # Villar-Saint-Pancrace (1711875186).
    starts=((44.89564, 6.63546), (44.8984, 6.64363), (44.89295, 6.61783), (44.87452, 6.62786)),
)


def measure(export: Path | str, plan: Measurement) -> dict:
    """The coverage counts of a serving export around ``plan.origin``.

    ``relations``: per radius, the hikes with a vertex of their served line within it;
    ``trailheads``: per radius, the hikes whose Trailhead is within it;
    ``loops``: per loop length, ``found``, the loops ``find_loops`` draws from Junctions near
    ``plan.starts`` with that length and nothing else to follow, and ``listed``, the
    SuggestedLoops starting within ``plan.loop_within_km`` that ``/api/rank`` lists for a Wish
    of that length within ``plan.loop_max_km``, loops on;
    ``first_page``: ``loops_off`` and ``loops_on``, the ``/api/rank`` first page for a Wish of
    ``plan.page_length_km`` within ``plan.page_max_km``: its ``rows``, ``hikes`` (rows and
    their versions), ``rows_within`` (rows starting within each of the two smallest radii)
    and ``elapsed_ms``.
    """
    data = ServingData(export)
    origin = Origin(*plan.origin)
    # Only the lines whose box comes within the largest radius can pass within it.
    grow_lat = math.degrees(max(plan.radii_km) * 1000 / 6_371_000)
    grow_lon = grow_lat / math.cos(math.radians(plan.origin[0]))
    with sqlite3.connect(f"file:{data.directory / 'hikes.sqlite'}?mode=ro", uri=True) as db:
        lines = [
            line
            for (line,) in db.execute(
                "SELECT h.line FROM hike_boxes b JOIN hikes h ON h.id = b.id "
                "WHERE b.north >= ? AND b.south <= ? AND b.east >= ? AND b.west <= ?",
                (plan.origin[0] - grow_lat, plan.origin[0] + grow_lat, plan.origin[1] - grow_lon, plan.origin[1] + grow_lon),
            )
        ]
    nearest = np.array([straight_line_distances_m(origin, decode_polyline(line)).min() for line in lines])
    starts = straight_line_distances_m(origin, np.asarray(data.trailheads))
    client = TestClient(create_app(data))

    def ranking(length_km: float, max_km: float, loops: bool) -> dict:
        body = {
            "wish": {"length": length_km * 1000},
            "origin": {"lat": plan.origin[0], "lon": plan.origin[1]},
            "max_km": max_km,
            "loops": loops,
        }
        response = client.post("/api/rank", json=body)
        response.raise_for_status()
        return response.json()

    loops = {}
    for km in plan.loop_lengths_km:
        listed = _listed(ranking(km, plan.loop_max_km, True))
        loops[km] = {
            "found": _found_loops(data, km, plan.starts),
            "listed": sum(h["id"] is None and h["distance_km"] <= plan.loop_within_km for h in listed),
        }
    first_page = {}
    for key, on in (("loops_off", False), ("loops_on", True)):
        page = ranking(plan.page_length_km, plan.page_max_km, on)
        rows = page["results"]
        first_page[key] = {
            "rows": len(rows),
            "hikes": len(_listed(page)),
            "rows_within": {r: sum(row["distance_km"] <= r for row in rows) for r in plan.radii_km[:2]},
            "elapsed_ms": page["elapsed_ms"],
        }
    return {
        "relations": {r: int((nearest <= r * 1000).sum()) for r in plan.radii_km},
        "trailheads": {r: int((starts <= r * 1000).sum()) for r in plan.radii_km},
        "loops": loops,
        "first_page": first_page,
    }


def _listed(page: dict) -> list[dict]:
    return [hike for row in page["results"] for hike in (row, *row["versions"])]


def _found_loops(data: ServingData, km: float, starts: tuple[tuple[float, float], ...]) -> int:
    if data.trailgraph is None or not starts:
        return 0
    target = LoopTarget(km * 1000, math.nan, math.nan, math.nan, None)
    deadline = time.monotonic() + LOOP_SEARCH_S
    return len(find_loops(data.trailgraph, target, list(starts), limit=LOOP_MEASURED, deadline=deadline))


def build(pbf: Path, bbox: str | None, data: Path, out: Path) -> dict[str, float]:
    """Build OUT/web-serving from one region (see the module doc); the seconds of each step."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "osm").mkdir(exist_ok=True)
    (out / "logs").mkdir(exist_ok=True)
    seconds: dict[str, float] = {}

    def step(name: str, command: list[str]) -> None:
        started = time.monotonic()
        with open(out / "logs" / f"{name}.log", "w") as log:
            print(f"{name} ...", file=sys.stderr, flush=True)
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        seconds[name] = round(time.monotonic() - started, 1)
        (out / "build.json").write_text(json.dumps(seconds, indent=1))

    region = out / "osm" / "region.osm.pbf"
    region.unlink(missing_ok=True)
    if bbox is None:
        region.symlink_to(pbf.resolve())
    else:
        step("cut", ["osmium", "extract", "-b", bbox, "-s", "smart", "-S", "types=route,superroute,multipolygon",
                     str(pbf), "-o", str(region), "--overwrite"])  # fmt: skip
    catalog = out / "catalog.sqlite"
    dayhike = [sys.executable, "-m", "dayhike"]
    dem, landcover, climate = (str(data / name) for name in ("dem", "landcover", "climate"))
    step("ingest", [*dayhike, "ingest", str(region), "--catalog", str(catalog), "--fresh"])
    step("sample-dem", [*dayhike, "sample-dem", "--catalog", str(catalog), "--dem", dem, "--landcover", landcover])
    step("sample-landcover", [*dayhike, "sample-landcover", "--catalog", str(catalog), "--landcover", landcover])
    step("sample-climate", [*dayhike, "sample-climate", "--catalog", str(catalog), "--climate", climate])
    step("sample-trailgraph", [*dayhike, "sample-trailgraph", "--catalog", str(catalog),
                               "--dem", dem, "--landcover", landcover, "--climate", climate])  # fmt: skip
    step("build-similarity", [*dayhike, "build-similarity", "--catalog", str(catalog), "--dem", dem, "--landcover", landcover])
    step("export-serving", [*dayhike, "export-serving", "--catalog", str(catalog), "--out", str(out / "web-serving")])
    return seconds


def table(measured: dict, plan: Measurement) -> str:
    """The measurements as a Markdown table."""

    def bands(values: dict) -> str:
        return " / ".join(str(values[key]) for key in values)

    def km(values) -> str:
        return "/".join(f"{value:g}" for value in values)

    loops = measured["loops"]
    rows = [
        (f"Hikes passing within {km(plan.radii_km)} km", bands(measured["relations"])),
        (f"Trailheads within {km(plan.radii_km)} km", bands(measured["trailheads"])),
        (f"Loops found from the {len(plan.starts)} starts, {km(loops)} km", bands({k: v["found"] for k, v in loops.items()})),
        (f"SuggestedLoops listed within {plan.loop_within_km:g} km, Wish {km(loops)} km, max_km {plan.loop_max_km:g}",
         bands({k: v["listed"] for k, v in loops.items()})),
    ]  # fmt: skip
    for key, page in measured["first_page"].items():
        rows.append((
            f"First page, Wish {plan.page_length_km:g} km, max_km {plan.page_max_km:g}, {key.replace('_', ' ')}: "
            f"rows, hikes, rows within {km(page['rows_within'])} km",
            f"{page['rows']}, {page['hikes']}, {bands(page['rows_within'])} ({page['elapsed_ms']} ms)",
        ))
    return "\n".join(["| Metric | Value |", "|---|---|", *(f"| {name} | {value} |" for name, value in rows)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    built = commands.add_parser("build", help="Build a serving export of one region")
    built.add_argument("--pbf", type=Path, required=True, help="A full OSM snapshot to cut, or a cut with no --bbox")
    built.add_argument("--bbox", help=f"west,south,east,north to cut (Briançon: {BRIANCON_BBOX})")
    built.add_argument("--data", type=Path, required=True, help="Directory holding dem/, landcover/ and climate/")
    built.add_argument("--out", type=Path, required=True)
    measured = commands.add_parser("measure", help="Print the Briançon table for a serving export")
    measured.add_argument("--export", type=Path, required=True)
    measured.add_argument("--origin", help="LAT,LON (default: Briançon, with the report's loop starts)")
    measured.add_argument("--json", type=Path, help="Also write the measurements here")
    args = parser.parse_args(argv)
    if args.command == "build":
        print(json.dumps(build(args.pbf, args.bbox, args.data, args.out), indent=1))
        return 0
    plan = BRIANCON
    if args.origin:
        lat, lon = (float(value) for value in args.origin.split(","))
        plan = replace(BRIANCON, origin=(lat, lon), starts=((lat, lon),))
    result = measure(args.export, plan)
    if args.json:
        args.json.write_text(json.dumps(result, indent=1))
    print(table(result, plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
