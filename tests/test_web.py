from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dayhike import build_catalog, build_similarity
from dayhike.serving import ServingData, export_serving
from dayhike.similarity import CORE_MEASURES, MEASURE_NAMES
from dayhike.web import RESULTS, create_app

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
RIDGE_WALK_ID = 1001


@pytest.fixture(scope="module")
def data(tmp_path_factory: pytest.TempPathFactory) -> ServingData:
    directory = tmp_path_factory.mktemp("web")
    path = directory / "catalog.sqlite"
    build_catalog(FIXTURE, path)
    build_similarity(path, directory / "dem")
    return ServingData(export_serving(path, directory / "web-serving"))


class Matrix:
    """DriveTime of 1 h to every Trailhead but the one at 45.3,15.2 (unreachable)."""

    def __init__(self) -> None:
        self.calls = 0

    def sources_to_targets(self, origin, trailheads):
        self.calls += 1
        return [None if (t.latitude, t.longitude) == (45.3, 15.2) else 3600.0 for t in trailheads]


@pytest.fixture
def matrix() -> Matrix:
    return Matrix()


@pytest.fixture
def client(data: ServingData, matrix: Matrix) -> TestClient:
    return TestClient(create_app(data, lambda max_hours: matrix))


def test_info_and_measures_describe_every_measure_in_groups(client: TestClient) -> None:
    info = client.get("/api/info").json()
    body = client.get("/api/measures").json()

    assert info["dayhikes"] == 4
    assert info["drive_time"] is True
    assert [m["name"] for m in body["measures"]] == list(MEASURE_NAMES)
    assert {m["group"] for m in body["measures"]} == set(body["groups"])
    assert all(m["label"] and m["description"] for m in body["measures"])
    assert {m["name"] for m in body["measures"] if m["default_weight"] > 0} == set(CORE_MEASURES)


def test_search_and_detail(client: TestClient) -> None:
    hits = client.get("/api/search", params={"q": "ridge"}).json()["results"]
    hike = client.get(f"/api/hikes/{hits[0]['id']}").json()

    assert [hit["id"] for hit in hits] == [RIDGE_WALK_ID]
    assert hike["name"] == "Ridge Walk"
    assert hike["line"] and hike["elevation"]["metres"] and hike["points"]
    assert client.get("/api/hikes/999").status_code == 404


def test_rank_returns_scored_results_with_every_measure(client: TestClient) -> None:
    response = client.post("/api/rank", json={"seed": RIDGE_WALK_ID})

    body = response.json()
    assert response.status_code == 200
    assert 0 < len(body["results"]) <= RESULTS
    assert RIDGE_WALK_ID not in [r["id"] for r in body["results"]]
    scores = [r["score"] for r in body["results"]]
    assert scores == sorted(scores, reverse=True)
    assert set(body["results"][0]["measures"]) == set(MEASURE_NAMES)
    assert body["reachable"] is None and body["results"][0]["drive_hours"] is None
    assert set(body["weights"]) == set(CORE_MEASURES)


def test_an_excluded_measure_counts_as_weight_zero(client: TestClient) -> None:
    only_length = client.post(
        "/api/rank", json={"seed": RIDGE_WALK_ID, "weights": {"length": 100}}
    ).json()
    excluded = client.post(
        "/api/rank",
        json={"seed": RIDGE_WALK_ID, "weights": {"length": 100, "hut_along": 80}, "excluded": ["hut_along"]},
    ).json()

    assert excluded["weights"] == {"length": 100}
    assert [r["id"] for r in excluded["results"]] == [r["id"] for r in only_length["results"]]
    assert [r["score"] for r in excluded["results"]] == [r["score"] for r in only_length["results"]]


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"seed": RIDGE_WALK_ID, "weights": {"length": 101}}, 400),
        ({"seed": RIDGE_WALK_ID, "weights": {"length": 0}}, 400),
        ({"seed": RIDGE_WALK_ID, "weights": {"nonsense": 50}}, 400),
        ({"seed": RIDGE_WALK_ID, "excluded": ["nonsense"]}, 400),
        ({"seed": RIDGE_WALK_ID, "month": 13}, 422),
        ({"seed": 999}, 404),
        ({"seed": RIDGE_WALK_ID, "origin": {"lat": 45, "lon": 15}}, 400),
        ({"seed": RIDGE_WALK_ID, "origin": {"lat": 45, "lon": 15}, "max_hours": 5}, 422),
    ],
)
def test_rank_refuses_bad_queries(client: TestClient, body: dict, status: int) -> None:
    assert client.post("/api/rank", json=body).status_code == status


def test_drive_filter_keeps_only_reachable_hikes_with_their_hours(
    client: TestClient, matrix: Matrix
) -> None:
    body = client.post(
        "/api/rank",
        json={"seed": RIDGE_WALK_ID, "origin": {"lat": 45.1, "lon": 15.1}, "max_hours": 2},
    ).json()

    assert matrix.calls == 1
    assert body["reachable"] == 3  # the Seed's Trailhead counts; 1003 is unreachable
    assert sorted(r["id"] for r in body["results"]) == [1002, 1007]
    assert all(r["drive_hours"] == 1.0 for r in body["results"])


def test_a_stalled_drive_service_gives_503_and_frees_the_ranking(
    data: ServingData, matrix: Matrix
) -> None:
    class Stalled:
        def sources_to_targets(self, origin, trailheads):
            raise TimeoutError("timed out")

    clients = iter([Stalled(), matrix])
    client = TestClient(create_app(data, lambda max_hours: next(clients)))
    query = {"seed": RIDGE_WALK_ID, "origin": {"lat": 45.1, "lon": 15.1}, "max_hours": 2}

    assert client.post("/api/rank", json=query).status_code == 503
    response = client.post("/api/rank", json=query)
    assert response.status_code == 200
    assert matrix.calls == 1


def test_drive_filter_off_without_a_drive_client(data: ServingData) -> None:
    client = TestClient(create_app(data))

    assert client.get("/api/info").json()["drive_time"] is False
    response = client.post(
        "/api/rank", json={"seed": RIDGE_WALK_ID, "origin": {"lat": 45, "lon": 15}, "max_hours": 1}
    )
    assert response.status_code == 400


def test_rate_limit_is_per_client_and_spares_health(data: ServingData) -> None:
    client = TestClient(create_app(data, forwarded_hops=1, rank_rate=2, api_rate=3))
    first = {"X-Forwarded-For": "203.0.113.9, 198.51.100.1"}
    second = {"X-Forwarded-For": "198.51.100.2"}

    statuses = [client.get("/api/info", headers=first).status_code for _ in range(4)]
    ranks = [
        client.post("/api/rank", json={"seed": RIDGE_WALK_ID}, headers=first).status_code
        for _ in range(3)
    ]

    assert statuses == [200, 200, 200, 429]
    assert ranks == [200, 200, 429]
    assert client.get("/api/info", headers=second).status_code == 200
    assert client.get("/api/health", headers=first).status_code == 200


def test_api_reads_answer_head_like_get(client: TestClient) -> None:
    for path in ("/api/health", "/api/info", "/api/measures", "/api/search?q=ridge", f"/api/hikes/{RIDGE_WALK_ID}"):
        response = client.head(path)
        assert response.status_code == 200, path
        assert response.content == b""


def test_the_page_and_its_assets_are_served(client: TestClient) -> None:
    page = client.get("/")

    assert page.status_code == 200
    assert "Find similar" in page.text
    for asset in ("app.js", "style.css", "coverage.svg", "mark.svg"):
        assert client.get(f"/{asset}").status_code == 200
    for credit in ("OpenStreetMap", "ESA WorldCover", "CHELSA", "FABDEM", "Copernicus", "Natural Earth", "Valhalla", "Leaflet"):
        assert credit in page.text
