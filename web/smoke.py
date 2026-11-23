"""Smoke-test the web demo on a real serving export: known hikes, timings, memory.

    uv run --extra web python web/smoke.py --data data/web-serving [--valhalla-url URL]
    uv run python web/smoke.py --url http://127.0.0.1:8080   # a server already running
    uv run --extra web python web/smoke.py --us --data US_BUILD/web-serving   # the one-state US build

With --data it starts `dayhike serve` on a free port, checks it, reports the
server's resident memory, and stops it. Exits non-zero on the first failure.
It checks the known hikes of each region the server has hikes in (/api/info
"regions": Europe, the United States, or both), and that the page says what the
server covers. --us checks the us-rhode-island build (tests/test_real_data_us.py) instead.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Build:
    min_dayhikes: int
    known: list[tuple[str, str]]  # (search text, exact DayHike name expected among the hits); the first is the Seed
    origins: dict[str, tuple[float, float]]
    hours: tuple[float, float]  # drive-time limits
    min_results: int | None = None  # rows a Ranking answers with at least; None: a full page (/api/info "results")


# The known hikes of each region are in Slovenia and Switzerland (Europe), California, Colorado and Utah (the
# United States), and the drives from cities near them: an export of fewer hikes than the whole region
# (a showcase of a few countries and states) passes while it keeps those.
EUROPE = Build(
    min_dayhikes=50_000,
    known=[
        ("Pot kralja Triglava", "Pot kralja Triglava"),
        ("Vršič - Razor", "Vršič - Razor"),
        ("koca pri savici", "Koča pri Savici - Dom na Komni"),
        ("Matterhorn-Trail", "Matterhorn-Trail"),
    ],
    origins={"Ljubljana": (46.0569, 14.5058), "Munich": (48.137, 11.575)},
    hours=(2, 4),
)
US_48 = Build(
    min_dayhikes=10_000,
    known=[
        ("Mount Whitney Trail", "Mount Whitney Trail"),
        ("mt whitney trail", "Mount Whitney Trail"),
        ("Longs Peak Trail", "Longs Peak Trail"),
        ("delicate arch", "Delicate Arch Trail"),
    ],
    origins={"Denver": (39.7392, -104.9903)},
    hours=(2, 4),
)
BY_REGION = {"europe": EUROPE, "us": US_48}  # the checks of each region of /api/info "regions"
US = Build(
    min_dayhikes=20,
    known=[
        ("cliff walk", "Cliff Walk"),
        ("North South Trail", "North South Trail"),
        ("benson mountain", "Benson Mountain Trail"),
    ],
    origins={"Providence": (41.824, -71.4128)},
    hours=(1, 2),
    min_results=5,  # its 23 hikes group into fewer rows than a page
)


def call(base: str, path: str, body: dict | None = None) -> tuple[dict, float]:
    request = urllib.request.Request(
        base + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="GET" if body is None else "POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response), (time.monotonic() - started) * 1000


def rss_mb(pid: int) -> float:
    """Resident memory of a process and its children, MB."""
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,rss="], capture_output=True, text=True).stdout
    rows = [tuple(map(int, line.split())) for line in out.splitlines() if line.strip()]
    family, total = {pid}, 0
    for _ in range(3):
        family |= {p for p, parent, _ in rows if parent in family}
    total = sum(rss for p, _, rss in rows if p in family)
    return total / 1024


def check(condition: bool, message: str) -> None:
    print(("ok   " if condition else "FAIL ") + message, flush=True)
    if not condition:
        raise SystemExit(1)


def smoke(base: str, drive: bool, pid: int | None, builds: list[Build] | None = None) -> None:
    """Check the server at ``base``: the known hikes of ``builds``, else of each region it has hikes in."""
    info, ms = call(base, "/api/info")
    regions = [region["name"] for region in info["regions"]]
    builds = builds or [BY_REGION[name] for name in regions]
    check(
        info["dayhikes"] > sum(build.min_dayhikes for build in builds),
        f"info: {info['dayhikes']} DayHikes in {', '.join(regions)}, drive_time={info['drive_time']} ({ms:.0f} ms)",
    )
    if pid:
        print(f"     server RSS after start: {rss_mb(pid):.0f} MB")
    covered = info["covered"]
    # Only a site of hikes in Europe alone says Europe: one with US hikes names its regions.
    check((covered == "Europe") == (regions == ["europe"]), f"covers {covered!r}, with hikes in {', '.join(regions)}")
    with urllib.request.urlopen(base + "/", timeout=120) as response:
        page = response.read().decode()
    check(f"hikes across {covered}." in page, f"page: hikes across {covered}")
    for build in builds:
        check_build(base, info, drive, build)
    if pid:
        print(f"     server RSS at the end: {rss_mb(pid):.0f} MB")


def check_build(base: str, info: dict, drive: bool, build: Build) -> None:
    """Search, detail, map view and Rankings of the known hikes of one build, and its drives."""
    seeds = {}
    for query, name in build.known:
        hits, ms = call(base, "/api/search?" + urllib.parse.urlencode({"q": query}))
        found = [hit for hit in hits["results"] if hit["name"] == name]
        check(bool(found), f"search {query!r} finds {name!r} ({len(hits['results'])} hits, {ms:.0f} ms)")
        seeds[name] = found[0]["id"]
    for name, dayhike_id in seeds.items():
        hike, ms = call(base, f"/api/hikes/{dayhike_id}")
        ele = hike["elevation"]["metres"] if hike["elevation"] else []
        check(
            bool(hike["line"]) and len(ele) > 2,
            f"detail {name}: {len(hike['points'])} points, {min(ele)}-{max(ele)} m ({ms:.0f} ms)",
        )
        lat, lon = hike["trailhead"]
        # A dense view lists no hikes, so zoom in until it holds few enough.
        for zoom in range(8):
            half = 0.05 / 2**zoom
            box = {"south": lat - half, "west": lon - 2 * half, "north": lat + half, "east": lon + 2 * half}
            in_view, ms = call(base, "/api/hikes?" + urllib.parse.urlencode(box))
            if not in_view["too_many"]:
                break
        check(
            dayhike_id in [found["id"] for found in in_view["results"]],
            f"map view around {name} (±{half:.4f}°): {len(in_view['results'])} hikes, "
            f"too_many={in_view['too_many']} ({ms:.0f} ms)",
        )
    seed = seeds[build.known[0][1]]
    for label, body in [
        ("all 26 Measures", {"seed": seed}),
        ("effort + altitude", {"seed": seed, "weights": {"length": 100, "elevation_gain": 100, "highest": 60}}),
        ("July, huts off", {"seed": seed, "month": 7, "excluded": ["hut_along"]}),
    ]:
        ranked, ms = call(base, "/api/rank", body)
        top = ranked["results"][0]
        check(
            (build.min_results or info["results"]) <= len(ranked["results"]) <= info["draw"]["pool"]
            and 0 <= top["score"] <= 1,
            f"rank {label}: top {top['name']!r} score {top['score']:.3f} ({ms:.0f} ms)",
        )
    if drive:
        for city, (lat, lon) in build.origins.items():
            for hours in build.hours:
                body = {"seed": seed, "origin": {"lat": lat, "lon": lon}, "max_hours": hours}
                ranked, ms = call(base, "/api/rank", body)
                worst = max((r["drive_hours"] for r in ranked["results"]), default=0)
                check(
                    worst <= hours and ranked["reachable"] > 0,
                    f"rank within {hours} h of {city}: {ranked['reachable']} reachable, "
                    f"top {ranked['results'][0]['name']!r} ({ms:.0f} ms)",
                )


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", help="A running server; skips starting one")
    parser.add_argument("--data", type=Path, help="Serving export to start `dayhike serve` on")
    parser.add_argument("--valhalla-url", help="Highways-only Valhalla for the drive-time checks")
    parser.add_argument("--us", action="store_true", help="Check the one-state US build, not the regions the server has")
    args = parser.parse_args()
    builds = [US] if args.us else None
    if args.url:
        smoke(args.url.rstrip("/"), drive=call(args.url, "/api/info")[0]["drive_time"], pid=None, builds=builds)
        return 0
    if args.data is None:
        parser.error("give --url or --data")
    port = free_port()
    command = [sys.executable, "-m", "dayhike", "serve", "--data", str(args.data), "--port", str(port)]
    if args.valhalla_url:
        command += ["--valhalla-url", args.valhalla_url]
    started = time.monotonic()
    server = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        while True:
            try:
                call(base, "/api/health")
                break
            except OSError:
                if server.poll() is not None or time.monotonic() - started > 60:
                    raise SystemExit("server did not start")
                time.sleep(0.1)
        print(f"     server ready in {time.monotonic() - started:.1f} s")
        smoke(base, drive=args.valhalla_url is not None, pid=server.pid, builds=builds)
    finally:
        server.terminate()
        server.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
