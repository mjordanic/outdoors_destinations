from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dayhike import build_catalog, build_similarity, web
from dayhike.serving import ServingData, export_serving
from dayhike.similarity import CORE_MEASURES, FACILITIES_FLAG_COLUMNS, MEASURE_NAMES
from dayhike.web import RESULTS, create_app

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
RIDGE_WALK_ID = 1001
SITE_PAGES = ("/", "/index.html", "/about.html", "/support.html", "/credits.html")


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
    assert body["loops"] is False  # SuggestedLoops only on request (ADR-0028)


def test_a_wish_ranks_hikes_by_the_values_typed_in(client: TestClient) -> None:
    # With the way back the Ridge Walk is 20 km with a Hut; the other three are 10 km without one.
    wish = {"length": 20_000, "hut_along": True}

    body = client.post("/api/rank", json={"wish": wish, "weights": {"length": 100, "hut_along": 50}}).json()

    assert body["seed"] is None and body["composed"] is None and body["wish"] == wish
    dayhikes = [r for r in body["results"] if r["id"] is not None]
    assert dayhikes[0]["id"] == RIDGE_WALK_ID  # no hike is left out as the Seed
    assert dayhikes[0]["measures"]["length"]["raw"] == pytest.approx(1.0)
    note = dayhikes[0]["measures"]["hut_along"]["note"]
    # A Wish's "yes" is scored as before: against random pairs where either has a Hut.
    assert note.startswith("Identical; ") and "of random hike pairs where either has one are this alike" in note
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
    # A Wish's "no" is the Hiker's answer, not a missing map entry: a hike without a Hut matches it,
    # scored against every random pair, those where neither has a Hut included.
    body = client.post("/api/rank", json={"wish": {"hut_along": False}}).json()

    dayhikes = [r for r in body["results"] if r["id"] is not None]
    assert {r["id"] for r in dayhikes} == {1001, 1002, 1003, 1007}
    assert dayhikes[-1]["id"] == RIDGE_WALK_ID and dayhikes[-1]["score"] == 0.0
    assert all(r["score"] > 0 for r in dayhikes[:-1])
    note = dayhikes[0]["measures"]["hut_along"]["note"]
    assert note.startswith("Identical; ") and "of random hike pairs are this alike" in note


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
        ({"seed": RIDGE_WALK_ID, "loops": "sometimes"}, 422),
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

    assert matrix.calls == 1  # the Trailheads; the starts of SuggestedLoops only on request (ADR-0028)
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


API_READS = (
    "/api/info",
    "/api/measures",
    "/api/search?q=ridge",
    f"/api/hikes/{RIDGE_WALK_ID}",
    "/api/hikes?south=45&west=15&north=45.1&east=15.1",
)


def test_api_reads_are_cached_for_an_hour_and_revalidate_by_etag(client: TestClient) -> None:
    for path in API_READS:
        response = client.get(path)
        assert response.headers["cache-control"] == "public, max-age=3600", path
        etag = response.headers["etag"]

        again = client.get(path, headers={"If-None-Match": etag})
        assert again.status_code == 304, path
        assert again.content == b""
        assert again.headers["etag"] == etag
        assert client.get(path, headers={"If-None-Match": 'W/"other"'}).status_code == 200


def test_a_redeploy_with_new_code_or_drive_time_settings_changes_the_etag(
    data: ServingData, matrix: Matrix, monkeypatch: pytest.MonkeyPatch
) -> None:
    def etag() -> str:
        return TestClient(create_app(data, lambda max_hours: matrix)).get("/api/info").headers["etag"]

    before = etag()
    assert TestClient(create_app(data)).get("/api/info").headers["etag"] != before

    monkeypatch.setattr(web, "BUILD", "another build")
    assert etag() != before

    monkeypatch.undo()
    monkeypatch.setattr(web, "MAX_DRIVE_HOURS", 3.0)
    assert etag() != before


def test_refusals_rankings_and_health_are_not_cached(client: TestClient) -> None:
    for response in (
        client.get("/api/hikes/999999"),
        client.get("/api/hikes?south=46&west=15&north=45&east=16"),
        client.get("/api/search"),
        client.post("/api/rank", json={"seed": RIDGE_WALK_ID}),
        client.get("/api/health"),
    ):
        assert "etag" not in response.headers, response.url
        assert "max-age" not in response.headers.get("cache-control", ""), response.url


def test_the_page_revalidates_every_time_so_a_redeploy_shows_at_once(client: TestClient) -> None:
    for path in (*SITE_PAGES, "/app.js", "/style.css"):
        response = client.get(path)
        assert response.headers["cache-control"] == "no-cache", path

        again = client.get(path, headers={"If-None-Match": response.headers["etag"]})
        assert again.status_code == 304, path


def test_api_json_and_the_page_are_gzipped_for_a_browser_that_takes_it(client: TestClient) -> None:
    for path in ("/api/measures", "/app.js", "/"):
        gzipped = client.get(path, headers={"Accept-Encoding": "gzip"})
        plain = client.get(path, headers={"Accept-Encoding": "identity"})

        assert gzipped.headers["content-encoding"] == "gzip", path
        assert gzipped.num_bytes_downloaded < len(plain.content) / 2, path
        assert gzipped.content == plain.content  # the client unzips it
        assert "content-encoding" not in plain.headers


def test_the_page_and_its_assets_are_served(client: TestClient) -> None:
    page = client.get("/")

    assert page.status_code == 200
    assert "Find similar" in page.text
    for asset in ("app.js", "style.css", "coverage.svg", "mark.svg"):
        assert client.get(f"/{asset}").status_code == 200


def test_every_site_page_has_the_home_link_and_the_footer_navigation(client: TestClient) -> None:
    for path in SITE_PAGES:
        page = client.get(path)
        assert page.status_code == 200, path
        assert page.headers["content-type"].startswith("text/html"), path
        assert '<a class="home" href="/">GoFindMeAHike</a>' in page.text, path
        for link in ('href="about.html"', 'href="support.html"', 'href="credits.html"'):
            assert link in page.text, (path, link)


def test_the_about_page_says_who_runs_the_site_and_where_its_code_is(client: TestClient) -> None:
    about = client.get("/about.html").text

    assert "Run by Mislav Jordanic, Zagreb, Croatia" in about
    assert '<a href="mailto:mislav.jordanic@gmail.com">mislav.jordanic@gmail.com</a>' in about
    assert "free and non-commercial" in about
    assert 'href="https://github.com/mjordanic/outdoors_destinations"' in about
    assert 'href="https://github.com/mjordanic/outdoors_destinations/blob/main/LICENSE">MIT licence</a>' in about


def test_the_support_page_links_no_tip_jar_until_a_ko_fi_handle_is_set(data: ServingData) -> None:
    unset = TestClient(create_app(data)).get("/support.html").text
    assert "ko-fi.com" not in unset
    assert "not set up yet" in unset
    assert "only cover hosting" in unset

    support = TestClient(create_app(data, kofi="gofindmeahike")).get("/support.html").text
    assert '<a href="https://ko-fi.com/gofindmeahike">' in support
    assert support.count("ko-fi.com") == 1  # a plain link: no widget script
    assert "not set up yet" not in support


def test_the_credits_page_credits_every_source_with_the_fabdem_dataset(client: TestClient) -> None:
    credits = client.get("/credits.html").text

    for credit in (
        "OpenStreetMap", "ODbL", "Geofabrik", "Nominatim", "ESA WorldCover", "CHELSA", "FABDEM", "Copernicus",
        "Natural Earth", "Valhalla", "Leaflet",
    ):
        assert credit in credits, credit
    assert 'href="https://doi.org/10.5523/bris.s5hqmjcdj8yo2ibzi9b4ew3sn"' in credits
    assert 'href="https://doi.org/10.1088/1748-9326/ac4d4f"' in credits


def test_the_home_page_footer_is_a_short_credit_line_to_the_full_credits(client: TestClient) -> None:
    home = client.get("/").text
    footer = home[home.index("<footer"):]

    for credit in ("OpenStreetMap", "ODbL", "FABDEM", "CC BY-NC-SA 4.0", 'href="credits.html">full credits</a>'):
        assert credit in footer, credit
    assert "Climatologies" not in home  # the full references are on the credits page


def test_no_page_loads_analytics_while_no_goatcounter_code_is_set(client: TestClient) -> None:
    for path in SITE_PAGES:
        page = client.get(path).text
        assert "data-goatcounter" not in page and "count.js" not in page, path


def test_every_page_counts_with_the_self_hosted_goatcounter_script_once_a_code_is_set(data: ServingData) -> None:
    client = TestClient(create_app(data, goatcounter="gofindmeahike"))
    tag = (
        '<script data-goatcounter="https://gofindmeahike.goatcounter.com/count"'
        ' async src="vendor/goatcounter/count.js"></script>'
    )

    for path in SITE_PAGES:
        assert client.get(path).text.count(tag) == 1, path
    assert client.get("/vendor/goatcounter/count.js").status_code == 200
    assert client.get("/vendor/goatcounter/LICENSE").status_code == 200


@pytest.mark.parametrize("setting", [{"goatcounter": 'x" onload="alert(1)'}, {"goatcounter": ""}, {"kofi": "a/b"}])
def test_a_goatcounter_code_or_ko_fi_handle_that_is_not_a_plain_name_is_refused(
    data: ServingData, setting: dict
) -> None:
    with pytest.raises(ValueError):
        create_app(data, **setting)


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
