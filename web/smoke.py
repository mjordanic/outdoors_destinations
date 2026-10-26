"""Smoke-test the web demo on a real serving export: known hikes, timings, memory.

    uv run --extra web python web/smoke.py --data data/web-serving [--valhalla-url URL]
    uv run python web/smoke.py --url http://127.0.0.1:8080   # a server already running

With --data it starts `dayhike serve` on a free port, checks it, reports the
server's resident memory, and stops it. Exits non-zero on the first failure.
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
from pathlib import Path

# (search text, exact DayHike name expected among the hits)
KNOWN = [
    ("Pot kralja Triglava", "Pot kralja Triglava"),
    ("Vršič - Razor", "Vršič - Razor"),
    ("koca pri savici", "Koča pri Savici - Dom na Komni"),
    ("Mosor-Kosa", "Mosor-Kosa"),
]
ORIGINS = {"Ljubljana": (46.0569, 14.5058), "Munich": (48.137, 11.575)}


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


def smoke(base: str, drive: bool, pid: int | None) -> None:
    info, ms = call(base, "/api/info")
    check(info["dayhikes"] > 100_000, f"info: {info['dayhikes']} DayHikes, drive_time={info['drive_time']} ({ms:.0f} ms)")
    if pid:
        print(f"     server RSS after start: {rss_mb(pid):.0f} MB")
    seeds = {}
    for query, name in KNOWN:
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
        box = {"south": lat - 0.05, "west": lon - 0.1, "north": lat + 0.05, "east": lon + 0.1}
        in_view, ms = call(base, "/api/hikes?" + urllib.parse.urlencode(box))
        check(
            dayhike_id in [found["id"] for found in in_view["results"]],
            f"map view around {name}: {len(in_view['results'])} hikes, more={in_view['more']} ({ms:.0f} ms)",
        )
    seed = seeds["Pot kralja Triglava"]
    for label, body in [
        ("all 26 Measures", {"seed": seed}),
        ("effort + altitude", {"seed": seed, "weights": {"length": 100, "elevation_gain": 100, "highest": 60}}),
        ("July, huts off", {"seed": seed, "month": 7, "excluded": ["hut_along"]}),
    ]:
        ranked, ms = call(base, "/api/rank", body)
        top = ranked["results"][0]
        check(
            len(ranked["results"]) == 10 and 0 <= top["score"] <= 1,
            f"rank {label}: top {top['name']!r} score {top['score']:.3f} ({ms:.0f} ms)",
        )
    if drive:
        for city, (lat, lon) in ORIGINS.items():
            for hours in (2, 4):
                body = {"seed": seed, "origin": {"lat": lat, "lon": lon}, "max_hours": hours}
                ranked, ms = call(base, "/api/rank", body)
                worst = max((r["drive_hours"] for r in ranked["results"]), default=0)
                check(
                    worst <= hours and ranked["reachable"] > 0,
                    f"rank within {hours} h of {city}: {ranked['reachable']} reachable, "
                    f"top {ranked['results'][0]['name']!r} ({ms:.0f} ms)",
                )
    if pid:
        print(f"     server RSS at the end: {rss_mb(pid):.0f} MB")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", help="A running server; skips starting one")
    parser.add_argument("--data", type=Path, help="Serving export to start `dayhike serve` on")
    parser.add_argument("--valhalla-url", help="Highways-only Valhalla for the drive-time checks")
    args = parser.parse_args()
    if args.url:
        smoke(args.url.rstrip("/"), drive=call(args.url, "/api/info")[0]["drive_time"], pid=None)
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
        smoke(base, drive=args.valhalla_url is not None, pid=server.pid)
    finally:
        server.terminate()
        server.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
