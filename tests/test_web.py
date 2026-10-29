from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dayhike import build_catalog, build_similarity, web
from dayhike.serving import ServingData, export_serving
from dayhike.similarity import CORE_MEASURES, FACILITIES_FLAG_COLUMNS, MEASURE_NAMES
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


def test_measures_say_how_a_wish_value_is_typed_in(client: TestClient) -> None:
    measures = {m["name"]: m for m in client.get("/api/measures").json()["measures"]}

    assert {name for name, m in measures.items() if m["wish"] is None} == {"profile", "landcover", "surface"}
    assert measures["hut_along"]["wish"] == {
        "kind": "yes/no", "unit": None, "min": None, "max": None, "step": None, "default": True,
    }  # fmt: skip
    length = measures["length"]["wish"]
    assert (length["kind"], length["unit"]) == ("number", "m")
    assert measures["temperature"]["wish"]["unit"] == "°C"
    for name, measure in measures.items():
        wish = measure["wish"]
        if wish is not None and wish["kind"] == "number":
            assert wish["min"] <= wish["default"] <= wish["max"] and 0 < wish["step"] < wish["max"] - wish["min"], name
    # A wished value ranks: the default of every Measure that takes one is accepted.
    defaults = {name: m["wish"]["default"] for name, m in measures.items() if m["wish"] is not None}
    assert client.post("/api/rank", json={"wish": defaults, "return_to_start": False}).status_code == 200


ELEVATION_MEASURES = ("elevation_gain", "highest", "lowest", "profile", "southness", "climb_grade", "steepest_km")
CLIMATE_MEASURES = ("temperature", "precipitation", "solar_radiation")
LANDCOVER_MEASURES = ("landcover", "waterside")


def test_every_measure_names_the_data_its_fact_comes_from(client: TestClient) -> None:
    sources = {m["name"]: m["source"] for m in client.get("/api/measures").json()["measures"]}

    for name, source in sources.items():
        if name in ELEVATION_MEASURES:
            assert "FABDEM" in source, name
        elif name in CLIMATE_MEASURES:
            assert "CHELSA" in source, name
        elif name in LANDCOVER_MEASURES:
            assert "WorldCover" in source, name
        else:
            assert "OpenStreetMap" in source, name


def test_every_osm_flag_says_two_hikes_without_it_are_not_compared(client: TestClient) -> None:
    descriptions = {m["name"]: m["description"] for m in client.get("/api/measures").json()["measures"]}

    for name in (*FACILITIES_FLAG_COLUMNS, "peak_summited", "viewpoint_along"):
        assert "two hikes that both lack it are not compared on it" in descriptions[name], name
    assert "not compared" not in descriptions["round_trip"]


def test_the_score_explanation_says_what_a_score_means_and_how_it_is_made(client: TestClient) -> None:
    help_ = client.get("/api/measures").json()["score_help"]
    text = " ".join(section["text"] for section in help_["sections"])

    assert help_["overall"] and help_["measure"]
    assert all(section["title"] and section["text"] for section in help_["sections"])
    assert "100,000 random pairs" in text
    # The rarity rank (ADR-0026): the share of random pairs less alike, a rare match counting for more.
    assert "Share of random hike pairs less alike" in help_["measure"]
    assert "0.9 means only 1 pair in 10" in help_["measure"]
    assert "about 0.44" in help_["overall"]
    assert "0.16 on waterside" in text and "84 %" in text  # two dry hikes: identical, but it tells little
    assert "not compared" in text and "0.95" in text  # a shared absence; both passing a hut
    assert "What does 0.1 mean" in " ".join(section["title"] for section in help_["sections"])
    assert "Kattegat" in text and "0.10" in text
    assert "count half" not in text and "score 1.0" not in text  # the rules ADR-0026 replaced
    for source in ("OpenStreetMap", "FABDEM", "WorldCover", "CHELSA"):
        assert source in text
    assert "weights" in text


def test_search_and_detail(client: TestClient) -> None:
    hits = client.get("/api/search", params={"q": "ridge"}).json()["results"]
    hike = client.get(f"/api/hikes/{hits[0]['id']}").json()

    assert [hit["id"] for hit in hits] == [RIDGE_WALK_ID]
    assert hike["name"] == "Ridge Walk"
    assert hike["line"] and hike["elevation"]["metres"] and hike["points"]
    assert client.get("/api/hikes/999").status_code == 404


def test_hikes_in_a_map_box(client: TestClient) -> None:
    box = {"south": 45.05, "west": 14.99, "north": 45.06, "east": 15.01}

    body = client.get("/api/hikes", params=box).json()

    assert [hike["id"] for hike in body["results"]] == [RIDGE_WALK_ID]
    assert body["too_many"] is False
    assert set(body["results"][0]) == {"id", "name", "walk_m", "line"}


def test_hikes_in_a_map_box_holding_too_many_lists_none(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "MAP_HIKES", 3)

    body = client.get("/api/hikes", params={"south": 44, "west": 14, "north": 46, "east": 16}).json()

    assert body == {"results": [], "too_many": True}


@pytest.mark.parametrize(
    ("params", "status"),
    [
        ({"south": 45.1, "west": 15, "north": 45.0, "east": 15.1}, 400),
        ({"south": 45.0, "west": 15.1, "north": 45.1, "east": 15.0}, 400),
        ({"south": 45.0, "west": 15, "north": 95, "east": 15.1}, 422),
        ({"south": 45.0, "west": 15, "north": 45.1}, 422),
    ],
)
def test_hikes_in_a_map_box_refuse_bad_boxes(client: TestClient, params: dict, status: int) -> None:
    assert client.get("/api/hikes", params=params).status_code == status


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


def test_a_wish_ranks_hikes_by_the_values_typed_in(client: TestClient) -> None:
    # With the way back the Ridge Walk is 20 km with a Hut; the other three are 10 km without one.
    wish = {"length": 20_000, "hut_along": True}

    body = client.post("/api/rank", json={"wish": wish, "weights": {"length": 100, "hut_along": 50}}).json()

    assert body["seed"] is None and body["composed"] is None and body["wish"] == wish
    dayhikes = [r for r in body["results"] if r["id"] is not None]
    assert dayhikes[0]["id"] == RIDGE_WALK_ID  # no hike is left out as the Seed
    assert dayhikes[0]["measures"]["length"]["raw"] == pytest.approx(1.0)
    assert dayhikes[0]["measures"]["hut_along"]["note"].startswith("Identical; ")
    assert {r["id"] for r in dayhikes} == {1001, 1002, 1003, 1007}
    assert dayhikes[1]["measures"]["length"]["raw"] == pytest.approx(0.5)


def test_only_the_measures_a_wish_has_a_value_for_weigh(client: TestClient) -> None:
    wish = {"length": 10_000, "hut_along": False}

    weighted = client.post("/api/rank", json={"wish": wish, "weights": {"length": 60, "elevation_gain": 100}}).json()
    unweighted = client.post("/api/rank", json={"wish": wish}).json()
    no_value_weighted = client.post("/api/rank", json={"wish": wish, "weights": {"elevation_gain": 100}})

    assert weighted["weights"] == {"length": 60}
    assert unweighted["weights"] == {"length": 100, "hut_along": 100}
    assert no_value_weighted.status_code == 400


def test_a_wish_for_no_hut_ranks_the_hikes_without_one_first(client: TestClient) -> None:
    # A Wish's "no" is the Hiker's answer, not a missing map entry: a hike without a Hut matches it.
    body = client.post("/api/rank", json={"wish": {"hut_along": False}}).json()

    dayhikes = [r for r in body["results"] if r["id"] is not None]
    assert {r["id"] for r in dayhikes} == {1001, 1002, 1003, 1007}
    assert dayhikes[-1]["id"] == RIDGE_WALK_ID and dayhikes[-1]["score"] == 0.0
    assert all(r["score"] > 0 for r in dayhikes[:-1])
    assert dayhikes[0]["measures"]["hut_along"]["note"].startswith("Identical; ")


def test_a_wish_left_with_no_weighted_measure_says_why(client: TestClient) -> None:
    round_trip = client.post("/api/rank", json={"wish": {"round_trip": True}})
    excluded = client.post("/api/rank", json={"wish": {"length": 10_000}, "excluded": ["length"]})
    both = client.post(
        "/api/rank", json={"wish": {"round_trip": True, "hut_along": True}, "excluded": ["hut_along"]}
    )

    assert round_trip.status_code == 400 and "Round trip is left out with Return to start" in round_trip.json()["detail"]
    assert excluded.status_code == 400 and "excluded: Length" in excluded.json()["detail"]
    assert both.status_code == 400
    assert "Round trip" in both.json()["detail"] and "excluded: Hut along" in both.json()["detail"]
    assert client.post("/api/rank", json={"wish": {"round_trip": True}, "return_to_start": False}).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"seed": RIDGE_WALK_ID, "wish": {"length": 10_000}},
        {"seed": RIDGE_WALK_ID, "composed": {"route": "1:104.11"}},
        {"wish": {}},
        {"wish": {"nonsense": 1}},
        {"wish": {"profile": 0.5}},  # a ProfileShape is no single value
        {"wish": {"landcover": 0.5}},
        {"wish": {"hut_along": 1}},
        {"wish": {"length": True}},
        {"wish": {"length": "far"}},
    ],
)
def test_rank_refuses_anything_but_one_seed_or_a_wish_of_single_values(client: TestClient, body: dict) -> None:
    assert client.post("/api/rank", json=body).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        '{"wish": {"length": NaN}}',
        '{"wish": {"length": Infinity}}',
        '{"wish": {"length": 1e400}}',
        '{"seed": 1001, "origin": {"lat": 45, "lon": 15}, "max_hours": NaN}',
    ],
)
def test_rank_refuses_a_number_that_is_not_finite(client: TestClient, body: str) -> None:
    response = client.post("/api/rank", content=body, headers={"content-type": "application/json"})

    assert response.status_code == 422
    assert response.json()["detail"]


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


def test_pair_scores_any_two_hikes_like_a_ranking_would(client: TestClient) -> None:
    weights = {"length": 100, "hut_along": 40}
    ranked = client.post("/api/rank", json={"seed": RIDGE_WALK_ID, "weights": weights}).json()["results"]
    other = ranked[-1]

    body = client.post("/api/pair", json={"a": RIDGE_WALK_ID, "b": other["id"], "weights": weights}).json()

    assert (body["a"], body["b"]) == (RIDGE_WALK_ID, other["id"])
    assert body["score"] == pytest.approx(other["score"])
    assert body["measures"] == other["measures"]
    assert body["weights"] == weights


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"a": RIDGE_WALK_ID, "b": 999}, 404),
        ({"a": RIDGE_WALK_ID, "b": 1002, "weights": {"length": 0}}, 400),
        ({"a": RIDGE_WALK_ID, "b": 1002, "excluded": ["nonsense"]}, 400),
        ({"a": RIDGE_WALK_ID, "b": 1002, "month": 0}, 422),
        ({"a": RIDGE_WALK_ID, "b": 1002, "month": 7}, 400),
        ({"a": RIDGE_WALK_ID}, 422),
    ],
)
def test_pair_refuses_bad_queries(client: TestClient, body: dict, status: int) -> None:
    assert client.post("/api/pair", json=body).status_code == status


def test_info_gives_the_trailhead_box_for_the_swapped_coordinates_warning(client: TestClient) -> None:
    south, west, north, east = client.get("/api/info").json()["coverage"]

    assert south <= 45.0 <= north and west <= 15.0 <= east
    assert south < north and west <= east


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

    assert matrix.calls == 2  # the Trailheads, then the starts of SuggestedLoops (ADR-0020)
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
    assert matrix.calls == 2


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
    for path in (
        "/api/health",
        "/api/info",
        "/api/measures",
        "/api/search?q=ridge",
        f"/api/hikes/{RIDGE_WALK_ID}",
        "/api/hikes?south=45&west=15&north=45.1&east=15.1",
    ):
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


def test_round_trip_is_left_out_while_every_hike_returns_to_its_start(client: TestClient) -> None:
    weights = {"length": 100, "round_trip": 100}
    for path, seed in (("/api/rank", {"seed": RIDGE_WALK_ID}), ("/api/pair", {"a": RIDGE_WALK_ID, "b": 1003})):
        back = client.post(path, json={**seed, "weights": weights}).json()
        one_way = client.post(path, json={**seed, "weights": weights, "return_to_start": False}).json()
        assert back["weights"] == {"length": 100}
        assert one_way["weights"] == weights
        only = client.post(path, json={**seed, "weights": {"round_trip": 100}})
        assert only.status_code == 400 and "Round trip" in only.json()["detail"]


def test_a_hike_with_no_name_in_osm_is_shown_as_unnamed(client: TestClient) -> None:
    shown = "Unnamed hike (OSM relation 1003)"

    assert client.get("/api/hikes/1003").json()["name"] == shown
    assert [hit["name"] for hit in client.get("/api/search", params={"q": "1003"}).json()["results"]] == [shown]
    box = client.get("/api/hikes", params={"south": 44, "west": 14, "north": 46, "east": 16}).json()["results"]
    assert shown in [hike["name"] for hike in box]


def test_a_search_hit_says_whether_the_hike_ends_at_its_start(client: TestClient) -> None:
    hits = client.get("/api/search", params={"q": "al"}).json()["results"] + client.get("/api/search", params={"q": "ps"}).json()["results"]

    assert hits
    for hit in hits:  # the search lists every hike one way
        one_way = client.get(f"/api/hikes/{hit['id']}", params={"return_to_start": False}).json()
        assert hit["round_trip"] == one_way["facts"]["round_trip"]
        assert hit["length_m"] == one_way["facts"]["length_m"]
