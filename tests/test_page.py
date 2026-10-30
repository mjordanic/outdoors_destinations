"""The page in a headless browser, over the real app on the test catalog.

The wish half of the API (wishable Measures in ``/api/measures``, a ``wish`` in
``/api/rank``) is stubbed at the network: the page is tested against that contract.
Needs Chromium for Playwright: ``uv run playwright install chromium``.
"""

import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from dayhike import build_catalog, build_similarity
from dayhike.serving import ServingData, export_serving
from dayhike.web import create_app

sync_api = pytest.importorskip("playwright.sync_api")

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
RIDGE_WALK_ID = 1001
YES_NO = {"kind": "yes/no", "unit": None, "min": None, "max": None, "step": None, "default": True}
# The "wish" input /api/measures gives the Measures a Hiker can wish for (ADR-0024); every other is null.
WISHABLE = {
    "elevation_gain": {"kind": "number", "unit": "m", "min": 0, "max": 3000, "step": 50, "default": 600},
    "highest": {"kind": "number", "unit": "m", "min": 0, "max": 5000, "step": 50, "default": 1500},
    "hut_along": YES_NO,
    "waterside": {"kind": "number", "unit": "share", "min": 0, "max": 1, "step": 0.05, "default": 0.5},
    "round_trip": YES_NO,
}


class Matrix:
    """DriveTime of 1 h to every Trailhead."""

    def sources_to_targets(self, origin, trailheads):
        return [3600.0 for _ in trailheads]


@pytest.fixture(scope="module")
def data(tmp_path_factory: pytest.TempPathFactory) -> ServingData:
    directory = tmp_path_factory.mktemp("page")
    path = directory / "catalog.sqlite"
    build_catalog(FIXTURE, path)
    build_similarity(path, directory / "dem")
    cities = Path(__file__).parent / "fixtures" / "cities1000.txt"
    return ServingData(export_serving(path, directory / "web-serving", geonames_path=cities))


@pytest.fixture(scope="module")
def server(data: ServingData) -> Iterator[str]:
    yield from serve(create_app(data, lambda max_hours: Matrix()))


@pytest.fixture(scope="module")
def counted_server(data: ServingData) -> Iterator[str]:
    """The app with GoatCounter and Ko-fi set, so every page loads the real count.js."""
    yield from serve(create_app(data, goatcounter="gofindmeahike", kofi="gofindmeahike"))


def serve(app) -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    running = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    while not running.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    running.should_exit = True
    thread.join()


@pytest.fixture(scope="module")
def browser() -> Iterator["sync_api.Browser"]:
    with sync_api.sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch()
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for Playwright (uv run playwright install chromium): {error}")
        yield launched
        launched.close()


class Api:
    """The page's requests to the wish contract; every other request goes to the real app."""

    def __init__(self, wishable: dict) -> None:
        self.wishable = wishable
        self.ranked: list[dict] = []

    def measures(self, route) -> None:
        body = route.fetch().json()
        for measure in body["measures"]:
            measure["wish"] = self.wishable.get(measure["name"])
        route.fulfill(json=body)

    def rank(self, route) -> None:
        asked = json.loads(route.request.post_data)
        self.ranked.append(asked)
        if "wish" not in asked:
            route.continue_()
            return
        # The real Ranking from a Seed has the shape a wish Ranking answers with.
        seeded = {key: value for key, value in asked.items() if key != "wish"}
        body = route.fetch(post_data=json.dumps({**seeded, "seed": RIDGE_WALK_ID})).json()
        route.fulfill(json={**body, "seed": None, "wish": asked["wish"]})


def open_page(browser, server: str, api: Api, *, width: int = 1280, hash: str = ""):
    page = browser.new_page(viewport={"width": width, "height": 900})
    page.set_default_timeout(5000)
    page.route(lambda url: not url.startswith(server), lambda route: route.abort())  # no tiles
    page.route(f"{server}/api/measures", api.measures)
    page.route(f"{server}/api/rank", api.rank)
    page.goto(f"{server}/{hash}")
    page.wait_for_selector(".weight", state="attached")
    return page


def test_a_hiker_with_no_hike_in_mind_gets_a_wish_form_of_the_wishable_measures(browser, server) -> None:
    page = open_page(browser, server, Api(WISHABLE))

    page.get_by_role("button", name="No particular hike in mind?").click()

    rows = page.locator(".weight:visible")
    assert sorted(rows.evaluate_all("rows => rows.map(r => r.dataset.measure)")) == sorted(WISHABLE)
    gain = page.get_by_label("Elevation gain you want, in m")
    assert gain.get_attribute("type") == "number"
    assert (gain.get_attribute("min"), gain.get_attribute("max"), gain.get_attribute("step")) == ("0", "3000", "50")
    assert gain.input_value() == "600"
    hut = page.get_by_label("Hut along you want")
    assert hut.get_attribute("type") == "checkbox" and hut.is_checked()
    assert page.get_by_label("Elevation gain weight, 0 to 100").is_visible()
    assert not page.locator("#search").is_visible()

    page.get_by_role("button", name="Back to a hike you liked").click()

    assert page.locator("#search").is_visible()
    assert page.locator(".weight:visible").count() > len(WISHABLE)
    assert not gain.is_visible()


def test_finding_hikes_for_a_wish_asks_the_ranking_for_it_and_lists_the_results(browser, server) -> None:
    api = Api(WISHABLE)
    page = open_page(browser, server, api)
    page.get_by_role("button", name="No particular hike in mind?").click()
    page.get_by_label("Elevation gain you want, in m").fill("900")
    page.get_by_label("Elevation gain weight, 0 to 100").fill("80")
    page.get_by_label("Hut along you want").uncheck()
    page.get_by_label("Hut along weight, 0 to 100").fill("40")
    page.get_by_label("Leave Highest point out").check()
    page.get_by_label("Waterside weight, 0 to 100").fill("0")
    page.get_by_label("Only hikes within a drive").check()
    page.get_by_label("From").fill("45.1, 15.1")

    page.get_by_role("button", name="Find hikes").click()
    page.locator("#results-table tbody tr").first.wait_for()

    asked = api.ranked[-1]
    assert "seed" not in asked and "composed" not in asked
    assert asked["wish"] == {"elevation_gain": 900, "hut_along": False}
    assert asked["weights"] == {"elevation_gain": 80, "hut_along": 40}
    assert asked["origin"] == {"lat": 45.1, "lon": 15.1} and asked["max_hours"] == 2
    assert asked["return_to_start"] is True
    assert "hike you want" in page.locator("#results-note").inner_text()
    assert page.locator("#results-table tbody tr").count() > 0


def test_a_hike_found_for_a_wish_is_shown_against_what_the_hiker_wants(browser, server) -> None:
    page = open_page(browser, server, Api(WISHABLE))
    page.get_by_role("button", name="No particular hike in mind?").click()
    page.get_by_label("Elevation gain you want, in m").fill("900")
    page.get_by_label("Elevation gain weight, 0 to 100").fill("80")
    page.get_by_label("Hut along weight, 0 to 100").fill("40")
    page.get_by_label("Waterside weight, 0 to 100").fill("0")
    page.get_by_role("button", name="Find hikes").click()

    page.locator("#results-table tbody tr").first.click()

    facts = page.locator("#selected table.facts")
    facts.wait_for()
    assert facts.locator("thead th").all_inner_texts() == ["Measure", "This hike", "You want", "Score ?"]
    gain = facts.locator("tr", has=page.locator("td", has_text="Elevation gain"))
    assert gain.locator("td").nth(2).inner_text() == "900 m"
    waterside = facts.locator("tr", has=page.locator("td", has_text="Waterside"))
    assert waterside.locator("td").nth(2).inner_text() == "left out"


def test_a_wish_survives_a_reload_and_opens_from_its_link(browser, server) -> None:
    page = open_page(browser, server, Api(WISHABLE))
    page.get_by_role("button", name="No particular hike in mind?").click()
    page.get_by_label("Elevation gain you want, in m").fill("900")
    page.get_by_label("Elevation gain weight, 0 to 100").fill("80")
    page.get_by_label("Hut along you want").uncheck()
    page.get_by_label("Hut along weight, 0 to 100").fill("40")
    page.get_by_label("Highest point weight, 0 to 100").fill("0")
    page.get_by_label("Return to start").uncheck()

    page.reload()
    page.wait_for_selector(".weight", state="attached")

    assert page.get_by_label("Elevation gain you want, in m").input_value() == "900"
    assert page.get_by_label("Elevation gain weight, 0 to 100").input_value() == "80"
    assert not page.get_by_label("Hut along you want").is_checked()
    assert page.get_by_label("Hut along weight, 0 to 100").input_value() == "40"
    assert page.get_by_label("Highest point weight, 0 to 100").input_value() == "0"
    assert not page.get_by_label("Return to start").is_checked()
    assert not page.locator("#search").is_visible()

    shared = open_page(browser, server, Api(WISHABLE), hash="#wish=highest:2200:100")

    assert shared.get_by_label("Highest point you want, in m").input_value() == "2200"
    assert shared.get_by_label("Highest point weight, 0 to 100").input_value() == "100"
    assert shared.get_by_label("Elevation gain weight, 0 to 100").input_value() == "0"
    assert shared.get_by_role("button", name="Find hikes").is_enabled()


def test_a_server_without_wishes_offers_no_wish_form(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash="#wish=highest:2200:100")

    assert not page.get_by_role("button", name="No particular hike in mind?").is_visible()
    assert page.locator("#search").is_visible()


def test_the_wish_form_fits_a_phone(browser, server) -> None:
    page = open_page(browser, server, Api(WISHABLE), width=375)

    page.get_by_role("button", name="No particular hike in mind?").click()

    assert page.get_by_label("Elevation gain you want, in m").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= 375


def test_a_round_trip_wish_is_off_while_every_hike_returns_to_its_start(browser, server) -> None:
    page = open_page(browser, server, Api(WISHABLE))
    page.get_by_role("button", name="No particular hike in mind?").click()
    round_trip = page.get_by_label("Round trip you want")

    assert not round_trip.is_visible()
    assert page.get_by_text("Left out with Return to start").is_visible()

    page.get_by_label("Return to start").uncheck()

    assert round_trip.is_visible() and round_trip.is_enabled()


def test_changing_the_month_keeps_the_wish_form_in_front_of_a_liked_hike(browser, server) -> None:
    page = open_page(browser, server, Api(WISHABLE), hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.get_by_role("button", name="No particular hike in mind?").click()

    page.get_by_label("Weather month").select_option("Jul")

    assert not page.locator("#liked").is_visible()
    assert page.locator("#wish-card").is_visible()


def test_a_wish_asks_only_what_the_hiker_sets_and_leaves_the_liked_hike_weights_alone(browser, server) -> None:
    page = open_page(browser, server, Api(WISHABLE))
    page.get_by_label("Elevation gain weight, 0 to 100").fill("70")
    page.get_by_role("button", name="No particular hike in mind?").click()

    weights = page.locator(".weight:visible input[aria-label$='weight, 0 to 100']")
    assert set(weights.evaluate_all("inputs => inputs.map(i => i.value)")) == {"0"}
    page.get_by_label("Highest point you want, in m").fill("2000")
    page.get_by_label("Hut along you want").uncheck()
    assert page.get_by_label("Highest point weight, 0 to 100").input_value() == "100"
    assert page.get_by_label("Hut along weight, 0 to 100").input_value() == "100"

    page.get_by_role("button", name="Back to a hike you liked").click()

    assert page.get_by_label("Elevation gain weight, 0 to 100").input_value() == "70"
    assert page.get_by_label("Length weight, 0 to 100").input_value() == "100"


def test_resetting_a_wish_asks_nothing_again(browser, server) -> None:
    page = open_page(browser, server, Api(WISHABLE))
    page.get_by_role("button", name="No particular hike in mind?").click()
    page.get_by_label("Highest point you want, in m").fill("2000")

    page.get_by_role("button", name="Reset weights").click()

    assert page.get_by_label("Highest point weight, 0 to 100").input_value() == "0"
    assert page.get_by_label("Highest point you want, in m").input_value() == "1500"


def test_a_hike_still_loading_leaves_the_wish_form_open(browser, server) -> None:
    page = open_page(browser, server, Api(WISHABLE))
    held = []
    page.route(f"{server}/api/hikes/*", lambda route: held.append(route))
    page.evaluate(f"location.hash = '#hike={RIDGE_WALK_ID}'")
    page.wait_for_function("() => document.querySelector('#status').textContent.includes('Loading')")
    page.get_by_role("button", name="No particular hike in mind?").click()

    with page.expect_response(f"{server}/api/hikes/*"):
        held[0].continue_()

    assert page.locator("#wish-card").is_visible()
    assert not page.locator("#search").is_visible()
    assert page.locator("#status").inner_text() == ""


def test_a_liked_hike_is_measured_again_for_return_to_start_changed_while_wishing(browser, server) -> None:
    api = Api(WISHABLE)
    page = open_page(browser, server, api, hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.get_by_role("button", name="No particular hike in mind?").click()
    page.get_by_label("Return to start").uncheck()
    page.get_by_role("button", name="Back to a hike you liked").click()

    page.get_by_role("button", name="Find similar").click()
    page.locator("#results-table tbody tr").first.wait_for()

    assert api.ranked[-1]["return_to_start"] is False
    assert page.url.endswith("&oneway")


def test_a_feature_neither_hike_has_mapped_shows_a_dash_and_says_it_is_not_compared(browser, server) -> None:
    # The Valley Path (1002) and the hikes without a Hut: neither has one mapped (ADR-0026).
    page = open_page(browser, server, Api({}), hash="#hike=1002")
    page.get_by_text("Chosen: Valley Path").wait_for()
    page.get_by_label("Hut along weight, 0 to 100").fill("100")
    page.get_by_role("button", name="Find similar").click()

    not_compared = page.locator('td[title="Neither has one mapped: not compared"]')
    page.locator("#results-table").locator(not_compared).first.wait_for()
    assert page.locator("#results-table").locator(not_compared).first.inner_text() == "–"
    page.locator("#results-table tbody tr", has=not_compared).first.click()
    hut = page.locator("#selected table.facts tr", has=page.locator("td", has_text="Hut along"))
    assert hut.locator("td").last.inner_text().startswith("–")
    assert "Neither has one mapped: not compared" in hut.locator("td").last.inner_text()


def test_the_logo_takes_a_hiker_home_to_a_fresh_page(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash="#hike=1002")
    page.get_by_text("Chosen: Valley Path").wait_for()
    assert page.get_by_role("link", name="GoFindMeAHike").get_attribute("href") == "/"

    page.locator(".top .mark").click()
    page.wait_for_selector(".weight", state="attached")

    assert page.url == f"{server}/"
    assert page.title() == "GoFindMeAHike"
    assert page.locator("#liked-name").inner_text() == "No hike chosen yet."


def test_the_page_runs_leaflet_1_9_4_from_its_own_server_and_asks_unpkg_for_nothing(browser, server) -> None:
    page = browser.new_page()
    asked: list[str] = []
    page.on("request", lambda request: asked.append(request.url))
    page.route(lambda url: not url.startswith(server), lambda route: route.abort())  # no tiles
    page.goto(server)
    page.wait_for_load_state("load")

    assert page.evaluate("window.L && L.version") == "1.9.4"
    assert f"{server}/vendor/leaflet/leaflet.js" in asked
    assert f"{server}/vendor/leaflet/leaflet.css" in asked
    assert not [url for url in asked if "unpkg.com" in url]


def test_loops_are_suggested_only_when_the_hiker_asks_for_them(browser, server) -> None:
    api = Api({})
    page = open_page(browser, server, api, hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.get_by_role("button", name="Find similar").click()
    page.locator("#results-table tbody tr").first.wait_for()
    suggest = page.get_by_role("button", name="Suggest loops near these hikes (takes a few seconds)")

    assert "loops" not in api.ranked[-1]
    assert "loops composed" not in page.locator("#results-note").inner_text()

    page.get_by_label("Length weight, 0 to 100").fill("10")  # the shown Ranking's query is the one asked again
    with page.expect_response(f"{server}/api/rank"):
        suggest.click()
    page.wait_for_function("() => document.querySelector('#results-note').textContent.includes('loops composed')")

    assert api.ranked[-1] == {**api.ranked[-2], "loops": True}
    assert page.locator("#results-table tbody tr").count() > 0
    assert page.locator("#results-stale").is_visible()  # still not the query on the left
    assert not suggest.is_visible()


def test_a_server_that_cannot_compose_offers_no_loops(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.route(f"{server}/api/info", lambda route: route.fulfill(json={**route.fetch().json(), "compose": False}))
    page.reload()
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.get_by_role("button", name="Find similar").click()
    page.locator("#results-table tbody tr").first.wait_for()

    assert not page.get_by_role("button", name="Suggest loops near these hikes (takes a few seconds)").is_visible()


def test_no_page_asks_for_analytics_while_no_goatcounter_code_is_set(browser, server) -> None:
    page = browser.new_page()
    asked: list[str] = []
    page.on("request", lambda request: asked.append(request.url))
    page.route(lambda url: not url.startswith(server), lambda route: route.abort())  # no tiles
    for path in ("/", "/about.html", "/support.html", "/credits.html"):
        page.goto(f"{server}{path}")
        page.wait_for_load_state("load")
        assert page.evaluate("typeof window.goatcounter") == "undefined", path

    assert asked
    assert not [url for url in asked if "goatcounter" in url or "zgo.at" in url]


def test_finding_hikes_is_counted_as_an_event_when_analytics_are_on(browser, server) -> None:
    page = browser.new_page()
    page.add_init_script("window.goatcounter = { counted: [], count(vars) { this.counted.push(vars); } };")
    page.route(lambda url: not url.startswith(server), lambda route: route.abort())  # no tiles
    page.goto(f"{server}/#hike=1002")
    page.get_by_text("Chosen: Valley Path").wait_for()

    page.get_by_role("button", name="Find similar").click()
    page.locator("#results-table tbody tr").first.wait_for()

    assert page.evaluate("window.goatcounter.counted") == [{"path": "find", "event": True}]


def test_finding_a_place_asks_the_export_not_nominatim(browser, server) -> None:
    page = open_page(browser, server, Api(WISHABLE))
    asked = []
    page.on("request", lambda request: asked.append(request.url))
    page.get_by_label("Only hikes within a drive").check()

    page.get_by_label("From").fill("agram")
    page.get_by_role("button", name="Find place").click()
    page.get_by_text("Start: Zagreb, HR (45.8144, 15.9780)").wait_for()

    assert [url for url in asked if "place" in url] == [f"{server}/api/place?limit=1&q=agram"]


def test_the_credits_page_names_geonames_for_place_search_when_the_export_has_places(browser, server) -> None:
    page = browser.new_page()
    page.route(lambda url: not url.startswith(server), lambda route: route.abort())  # no tiles
    page.goto(f"{server}/credits.html")
    credit = page.locator("#place-credit")

    assert "GeoNames" in credit.inner_text()
    assert "Nominatim" not in credit.inner_text()
    assert credit.get_by_role("link", name="CC BY 4.0").get_attribute("href") == "https://creativecommons.org/licenses/by/4.0/"


def test_the_logo_and_loading_show_at_once_then_waking_the_server_until_it_answers(browser, server) -> None:
    page = browser.new_page()
    page.route(lambda url: not url.startswith(server), lambda route: route.abort())  # no tiles
    held = []
    page.route(f"{server}/api/info", lambda route: held.append(route))  # a server waking from zero
    page.goto(server)
    loading = page.locator("#loading")

    assert loading.is_visible()
    assert loading.locator("svg").is_visible()
    assert "Loading" in loading.inner_text()
    page.get_by_text("Waking the server").wait_for()

    held[0].continue_()
    loading.wait_for(state="hidden")
    page.wait_for_selector(".weight", state="attached")


def test_a_server_that_turns_the_page_away_says_the_site_is_paused(browser, server) -> None:
    page = browser.new_page()
    page.route(lambda url: not url.startswith(server), lambda route: route.abort())  # no tiles
    page.route(f"{server}/api/info", lambda route: route.fulfill(status=403, body="Forbidden"))
    page.goto(server)

    page.get_by_text("GoFindMeAHike is paused for now").wait_for()
    assert page.locator("#loading").is_visible()


def test_a_server_that_answers_too_many_requests_shows_its_own_message(browser, server) -> None:
    page = browser.new_page()
    page.route(lambda url: not url.startswith(server), lambda route: route.abort())  # no tiles
    page.route(f"{server}/api/info", lambda route: route.fulfill(status=429, json={"detail": "Rate limit exceeded"}))
    page.goto(server)

    page.locator("#loading-text.error").wait_for()
    assert page.locator("#loading-text").text_content() == "Too many requests; wait a minute and try again."


def test_an_api_on_another_origin_whose_answer_the_page_cannot_read_says_paused_or_busy(browser, server) -> None:
    shell_origin = server.replace("127.0.0.1", "localhost")  # another origin, which the API lets in no CORS for
    page = browser.new_context(permissions=["local-network-access"]).new_page()

    def shell(route) -> None:
        html = route.fetch().text().replace('<meta name="api-base" content="">', f'<meta name="api-base" content="{server}">')
        route.fulfill(body=html, content_type="text/html")

    page.route(lambda url: not url.startswith((server, shell_origin)), lambda route: route.abort())  # no tiles
    page.route(f"{shell_origin}/", shell)
    page.goto(shell_origin)

    page.get_by_text("GoFindMeAHike is paused or busy").wait_for()
    assert not page.get_by_text("its owner has switched the server off").is_visible()


def test_a_server_that_does_not_answer_says_it_is_down_and_offers_to_try_again(browser, server) -> None:
    page = browser.new_page()
    page.route(lambda url: not url.startswith(server), lambda route: route.abort())  # no tiles
    page.route(f"{server}/api/**", lambda route: route.abort("connectionrefused"))
    page.goto(server)

    page.get_by_text("The server is not answering").wait_for()
    page.unroute(f"{server}/api/**")
    page.get_by_role("button", name="Try again").click()

    page.locator("#loading").wait_for(state="hidden")
    page.wait_for_selector(".weight", state="attached")


def test_a_page_naming_another_api_base_asks_that_server(browser, server) -> None:
    api_base = "https://api.gofindmeahike.example"
    page = browser.new_page()
    asked: list[str] = []

    def proxy(route) -> None:  # the API on another origin, which lets the page's origin read it (CORS)
        asked.append(route.request.url)
        answer = route.fetch(url=route.request.url.replace(api_base, server))
        route.fulfill(response=answer, headers={**answer.headers, "access-control-allow-origin": server})

    def shell(route) -> None:
        html = route.fetch().text().replace('<meta name="api-base" content="">', f'<meta name="api-base" content="{api_base}/">')
        route.fulfill(body=html, content_type="text/html")

    page.route(lambda url: not url.startswith((server, api_base)), lambda route: route.abort())  # no tiles
    page.route(f"{api_base}/**", proxy)
    page.route(f"{server}/", shell)
    page.goto(server)
    page.wait_for_selector(".weight", state="attached")

    assert f"{api_base}/api/info" in asked and f"{api_base}/api/measures" in asked


def test_no_page_leaves_a_cookie_or_anything_in_browser_storage_even_with_analytics_on(
    browser, counted_server
) -> None:
    context = browser.new_context()
    page = context.new_page()
    page.route(lambda url: not url.startswith(counted_server), lambda route: route.abort())  # no tiles, no counting
    for path in ("/", "/about.html", "/support.html", "/credits.html", "/privacy.html", "/terms.html"):
        page.goto(f"{counted_server}{path}")
        page.wait_for_load_state("load")
        page.wait_for_function("() => window.goatcounter && window.goatcounter.count")  # count.js has run
        assert page.evaluate("[document.cookie, localStorage.length, sessionStorage.length]") == ["", 0, 0], path

    assert context.cookies() == []
    context.close()
