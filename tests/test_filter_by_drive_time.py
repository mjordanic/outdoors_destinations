import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from dayhike import (
    Origin,
    RankedDayHike,
    Ranking,
    build_catalog,
    filter_by_drive_time,
    rank_dayhikes,
)
from dayhike.catalog import AmenityFact, AmenityFacts, Trailhead
from dayhike.cli import main
from dayhike.valhalla import ValhallaMatrixClient

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"


class StubMatrix:
    def __init__(self, times_s: tuple[float | None, ...]) -> None:
        self._times_s = times_s

    def sources_to_targets(self, origin: Origin, trailheads) -> tuple[float | None, ...]:
        assert origin.latitude == 45.8
        assert origin.longitude == 15.97
        assert len(trailheads) == len(self._times_s)
        return self._times_s


def test_filter_keeps_finite_drive_time_under_max_hours_and_preserves_order(
    tmp_path: Path,
) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    ridge, valley, ranking = _ranking_of(catalog)

    filtered = filter_by_drive_time(
        ranking,
        Origin(latitude=45.8, longitude=15.97),
        max_hours=2,
        matrix_client=StubMatrix((1_800.0, 5_400.0)),
    )

    assert [item.dayhike.id for item in filtered.items] == [ridge.id, valley.id]
    assert [item.score for item in filtered.items] == [0.9, 0.4]
    assert [item.drive_time.hours for item in filtered.items] == [0.5, 1.5]


def _ranking_of(catalog):
    ridge = next(hike for hike in catalog.dayhikes if hike.id == 1001)
    valley = next(hike for hike in catalog.dayhikes if hike.id == 1002)
    ranking = Ranking(
        items=(
            RankedDayHike(dayhike=ridge, score=0.9),
            RankedDayHike(dayhike=valley, score=0.4),
        )
    )
    return ridge, valley, ranking


def test_filter_drops_drive_time_above_max_hours(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    ridge, valley, ranking = _ranking_of(catalog)

    filtered = filter_by_drive_time(
        ranking,
        Origin(latitude=45.8, longitude=15.97),
        max_hours=2,
        matrix_client=StubMatrix((1_800.0, 9_000.0)),
    )

    assert [item.dayhike.id for item in filtered.items] == [ridge.id]
    assert filtered.items[0].drive_time.hours == 0.5
    assert valley.id not in {item.dayhike.id for item in filtered.items}


def test_filter_keeps_later_items_when_an_earlier_one_is_over_max(
    tmp_path: Path,
) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    ridge, valley, ranking = _ranking_of(catalog)

    filtered = filter_by_drive_time(
        ranking,
        Origin(latitude=45.8, longitude=15.97),
        max_hours=2,
        matrix_client=StubMatrix((9_000.0, 1_800.0)),
    )

    assert [item.dayhike.id for item in filtered.items] == [valley.id]
    assert filtered.items[0].drive_time.hours == 0.5
    assert ridge.id not in {item.dayhike.id for item in filtered.items}


def test_filter_drops_null_matrix_entries_without_a_fake_time(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    ridge, valley, ranking = _ranking_of(catalog)

    filtered = filter_by_drive_time(
        ranking,
        Origin(latitude=45.8, longitude=15.97),
        max_hours=2,
        matrix_client=StubMatrix((1_800.0, None)),
    )

    assert [item.dayhike.id for item in filtered.items] == [ridge.id]
    assert filtered.items[0].drive_time.hours == 0.5
    assert valley.id not in {item.dayhike.id for item in filtered.items}
    assert all(item.drive_time is not None for item in filtered.items)


def test_rank_without_origin_does_not_require_valhalla(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")

    ranking = rank_dayhikes(catalog, ["Ridge Walk"])

    assert ranking.items
    assert all(item.drive_time is None for item in ranking.items)


def test_cli_without_origin_prints_id_name_score_only(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, path)

    result = subprocess.run(
        [sys.executable, "-m", "dayhike", "rank", "--catalog", str(path), "Ridge Walk"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    columns = result.stdout.strip().splitlines()[0].split("\t")
    assert len(columns) == 3


def test_cli_prints_drive_time_when_filtering(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, path)

    def fake_sources(self, origin, trailheads):
        return [1_800.0] * len(trailheads)

    monkeypatch.setattr(
        "dayhike.cli.ValhallaMatrixClient.sources_to_targets",
        fake_sources,
    )

    code = main(
        [
            "rank",
            "--catalog",
            str(path),
            "--origin",
            "45.8,15.97",
            "--max-hours",
            "2",
            "Ridge Walk",
        ]
    )
    out = capsys.readouterr().out.strip().splitlines()

    assert code == 0
    valley_line = next(line for line in out if line.split("\t")[1] == "Valley Path")
    columns = valley_line.split("\t")
    assert len(columns) == 4
    assert columns[3] == "0.5"


def test_cli_nearby_lists_dayhikes_within_max_hours_nearest_first(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    path = tmp_path / "catalog.sqlite"
    build_catalog(FIXTURE, path)
    times_by_latitude = {45.0: 3_600.0, 45.2: 1_800.0, 45.3: 9_000.0, 45.6: 5_400.0}

    def fake_sources(self, origin, trailheads):
        return [times_by_latitude[t.latitude] for t in trailheads]

    monkeypatch.setattr(
        "dayhike.cli.ValhallaMatrixClient.sources_to_targets", fake_sources
    )

    code = main(["nearby", "--catalog", str(path), "--origin", "45.25,15.15", "--max-hours", "2"])
    lines = [line.split("\t") for line in capsys.readouterr().out.strip().splitlines()]

    assert code == 0
    assert [float(columns[2]) for columns in lines] == [0.5, 1.0, 1.5]


def test_valhalla_client_reads_seconds_and_null_from_sources_to_targets(
    tmp_path: Path,
) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    recorded: dict[str, list] = {"paths": [], "bodies": []}
    ridge_latitude = next(
        hike.trailhead.latitude for hike in catalog.dayhikes if hike.id == 1001
    )

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            recorded["paths"].append(self.path)
            recorded["bodies"].append(body)
            time = 1_800.0 if body["targets"][0]["lat"] == ridge_latitude else None
            payload = {"sources_to_targets": [[{"time": time}]]}
            raw = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        client = ValhallaMatrixClient(f"http://127.0.0.1:{port}", batch_size=1)
        times = client.sources_to_targets(
            Origin(latitude=45.8, longitude=15.97),
            [
                hike.trailhead
                for hike in catalog.dayhikes
                if hike.id in {1001, 1002}
            ],
        )
    finally:
        server.shutdown()
        server.server_close()

    assert times == [1_800.0, None]
    assert recorded["paths"] == ["/sources_to_targets", "/sources_to_targets"]
    assert recorded["bodies"][0]["sources"] == [{"lat": 45.8, "lon": 15.97}]
    assert recorded["bodies"][0]["costing"] == "auto"
    assert "targets" in recorded["bodies"][0]


class RecordingMatrix:
    def __init__(self) -> None:
        self.targets: list = []

    def sources_to_targets(self, origin: Origin, trailheads) -> list[float | None]:
        self.targets.extend(trailheads)
        return [60.0] * len(trailheads)


def test_filter_never_sends_trailheads_beyond_max_speed_reach_to_the_matrix(
    tmp_path: Path,
) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    ridge, valley, ranking = _ranking_of(catalog)
    matrix = RecordingMatrix()

    # Valley Path starts about 14 km from Ridge Walk; 140 km/h for 0.05 h is 7 km.
    filtered = filter_by_drive_time(
        ranking,
        Origin(latitude=ridge.trailhead.latitude, longitude=ridge.trailhead.longitude),
        max_hours=0.05,
        matrix_client=matrix,
    )

    assert matrix.targets == [ridge.trailhead]
    assert [item.dayhike.id for item in filtered.items] == [ridge.id]


def test_filter_skips_the_matrix_when_nothing_is_within_reach(tmp_path: Path) -> None:
    catalog = build_catalog(FIXTURE, tmp_path / "catalog.sqlite")
    _, _, ranking = _ranking_of(catalog)
    matrix = RecordingMatrix()

    filtered = filter_by_drive_time(
        ranking, Origin(latitude=0.0, longitude=0.0), max_hours=4, matrix_client=matrix
    )

    assert filtered.items == ()
    assert matrix.targets == []


def _serve_matrix(respond):
    """Fake Valhalla: respond(body) -> (status, payload). Returns (server, bodies)."""
    bodies: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            bodies.append(body)
            status, payload = respond(body)
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
    return server, bodies


def _trailhead(latitude: float, longitude: float):
    fact = AmenityFact(count=0, nearest_m=None)
    return Trailhead(latitude, longitude, AmenityFacts(fact, fact, fact, fact))


def test_valhalla_client_sends_a_shared_trailhead_once_nearest_first() -> None:
    server, bodies = _serve_matrix(
        lambda body: (
            200,
            {
                "sources_to_targets": [
                    [{"time": t["lat"] * 100} for t in body["targets"]]
                ]
            },
        )
    )
    try:
        client = ValhallaMatrixClient(f"http://127.0.0.1:{server.server_address[1]}")
        near, far = _trailhead(45.1, 15.0), _trailhead(46.0, 15.0)
        times = client.sources_to_targets(
            Origin(latitude=45.0, longitude=15.0), [far, near, far]
        )
    finally:
        server.shutdown()
        server.server_close()

    assert [t["lat"] for t in bodies[0]["targets"]] == [45.1, 46.0]
    assert times == [4_600.0, 4_510.0, 4_600.0]


def test_valhalla_client_marks_only_the_unsnappable_trailhead_unreachable() -> None:
    def respond(body):
        if any(t["lat"] == 46.0 for t in body["targets"]):
            return 400, {"error_code": 171, "error": "No suitable edges near location"}
        return 200, {"sources_to_targets": [[{"time": 60.0} for _ in body["targets"]]]}

    server, _ = _serve_matrix(respond)
    try:
        client = ValhallaMatrixClient(f"http://127.0.0.1:{server.server_address[1]}")
        times = client.sources_to_targets(
            Origin(latitude=45.0, longitude=15.0),
            [_trailhead(45.1, 15.0), _trailhead(46.0, 15.0), _trailhead(45.2, 15.0)],
        )
    finally:
        server.shutdown()
        server.server_close()

    assert times == [60.0, None, 60.0]
