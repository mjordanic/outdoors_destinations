import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from dayhike import Origin, build_catalog
from dayhike.catalog import AmenityFact, AmenityFacts, Trailhead
from dayhike.cli import main
from dayhike.highways import HighwayDriveTimeClient, HighwaySnaps

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"


def _serve(routes):
    """Fake Valhalla: routes maps a path to body -> (status, payload)."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            status, payload = routes[self.path](body)
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _locate(snap):
    """snap(lat, lon, search_cutoff) -> (lat, lon) | None."""

    def respond(body):
        found = []
        for location in body["locations"]:
            point = snap(location["lat"], location["lon"], location["search_cutoff"])
            found.append(
                {"edges": [{"correlated_lat": point[0], "correlated_lon": point[1]}] if point else []}
            )
        return 200, found

    return respond


def _square_isochrone(body):
    """A square around the source, 0.01 degrees of half-side per minute."""
    features = []
    for contour in body["contours"]:
        r = contour["time"] * 0.01
        ring = [[-r, -r], [r, -r], [r, r], [-r, r], [-r, -r]]  # lon, lat
        features.append(
            {
                "properties": {"contour": contour["time"]},
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        )
    return 200, {"features": features}


def _trailhead(latitude: float, longitude: float) -> Trailhead:
    fact = AmenityFact(count=0, nearest_m=None)
    return Trailhead(latitude, longitude, AmenityFacts(fact, fact, fact, fact))


def _stop(*servers) -> None:
    for server in servers:
        server.shutdown()
        server.server_close()


def test_snaps_look_farther_for_remote_places_and_are_kept_on_disk(tmp_path: Path) -> None:
    cutoffs: list[int] = []

    def snap(lat, lon, cutoff):
        cutoffs.append(cutoff)
        if lat == 45.0:
            return (45.0, 15.1)  # 0.1 degrees of longitude, about 7.8 km
        if lat == 46.0 and cutoff > 35_000:
            return (46.0, 16.0)
        return None

    server, url = _serve({"/locate": _locate(snap)})
    path = tmp_path / "snaps.npz"
    try:
        snaps = HighwaySnaps(url, path)
        found = snaps.of([(45.0, 15.0), (46.0, 16.0), (47.0, 17.0)])
        calls = len(cutoffs)
        again = HighwaySnaps(url, path).of([(45.0, 15.0), (47.0, 17.0)])
    finally:
        _stop(server)

    assert found[0][:2] == (45.0, 15.1) and found[0][2] == pytest.approx(7_860, abs=60)
    assert found[1][:2] == (46.0, 16.0) and found[1][2] == 0
    assert found[2] is None
    assert 35_000 in cutoffs and 100_000 in cutoffs
    assert again == [found[0], None]
    assert len(cutoffs) == calls  # the second HighwaySnaps read the file, not Valhalla


def test_client_bands_by_isochrone_and_asks_the_matrix_only_near_the_limit() -> None:
    asked: list[list[dict]] = []

    def matrix(body):
        asked.append(body["targets"])
        times = {0.5: 3_000.0, 0.7: 3_500.0}
        return 200, {
            "sources_to_targets": [[{"time": times[t["lat"]]} for t in body["targets"]]]
        }

    server, url = _serve(
        {
            "/locate": _locate(lambda lat, lon, cutoff: None if lat == 0.2 else (lat, lon)),
            "/isochrone": _square_isochrone,
            "/sources_to_targets": matrix,
        }
    )
    try:
        client = HighwayDriveTimeClient(1.0, HighwaySnaps(url))
        times = client.sources_to_targets(
            Origin(latitude=0.0, longitude=0.0),
            [
                _trailhead(0.05, 0.0),  # inside the 20 minute contour
                _trailhead(0.5, 0.0),  # inside 60, outside 40: straddles the limit
                _trailhead(0.7, 0.0),  # outside 60, inside the ring around it
                _trailhead(0.9, 0.0),  # beyond the ring
                _trailhead(0.2, 0.0),  # no highway in reach
            ],
        )
    finally:
        _stop(server)

    assert times == [600.0, 3_000.0, 3_500.0, None, None]  # 600 = middle of the first band
    assert [[t["lat"] for t in targets] for targets in asked] == [[0.5, 0.7]]
    assert client.unreachable == [(0.2, 0.0)]


def test_client_adds_straight_line_approach_for_origin_and_trailhead() -> None:
    server, url = _serve(
        {
            # Every place snaps 0.1 degrees of longitude (about 11.1 km at the equator) west.
            "/locate": _locate(lambda lat, lon, cutoff: (lat, lon - 0.1)),
            "/isochrone": _square_isochrone,
            "/sources_to_targets": lambda body: (200, {"sources_to_targets": [[{"time": 1.0}]]}),
        }
    )
    try:
        client = HighwayDriveTimeClient(1.0, HighwaySnaps(url))
        (seconds,) = client.sources_to_targets(
            Origin(latitude=0.0, longitude=0.0), [_trailhead(0.05, 0.0)]
        )
    finally:
        _stop(server)

    # Two approaches of 11.1 km at 35 km/h (1,142 s each) leave too little of the hour
    # for the band to decide, so the matrix's 1 s is added to them.
    assert seconds == pytest.approx(1 + 2 * 1_142, abs=10)


def test_client_refuses_an_origin_with_no_highway_in_reach() -> None:
    server, url = _serve({"/locate": _locate(lambda lat, lon, cutoff: None)})
    try:
        with pytest.raises(ValueError, match="no highway"):
            HighwayDriveTimeClient(1.0, HighwaySnaps(url)).sources_to_targets(
                Origin(latitude=0.0, longitude=0.0), [_trailhead(0.05, 0.0)]
            )
    finally:
        _stop(server)


def test_cli_highways_estimates_and_names_dayhikes_with_no_highway(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    path = tmp_path / "catalog.sqlite"
    catalog = build_catalog(FIXTURE, path)
    stranded = catalog.dayhikes[0]

    def fake_sources(self, origin, trailheads):
        self.unreachable = [(stranded.trailhead.latitude, stranded.trailhead.longitude)]
        return [
            None if t == stranded.trailhead else 1_800.0 for t in trailheads
        ]

    monkeypatch.setattr(
        "dayhike.cli.HighwayDriveTimeClient.sources_to_targets", fake_sources
    )

    code = main(
        ["nearby", "--catalog", str(path), "--origin", "45.8,15.97", "--max-hours", "2", "--highways"]
    )
    captured = capsys.readouterr()

    assert code == 0
    assert captured.out.strip()
    assert all(line.split("\t")[2] == "0.5" for line in captured.out.strip().splitlines())
    assert f"no highway in reach\t{stranded.id}\t{stranded.name}" in captured.err
