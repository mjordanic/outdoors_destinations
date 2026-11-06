"""The page in a headless browser, over the real app on the test catalog.

The wish half of the API (wishable Measures in ``/api/measures``, a ``wish`` in
``/api/rank``) is stubbed at the network: the page is tested against that contract.
Needs Chromium for Playwright: ``uv run playwright install chromium``.
"""

import itertools
import json
import math
import re
import socket
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
import uvicorn

from dayhike import build_catalog, build_similarity, web
from dayhike.serving import ServingData, decode_polyline, draw_noise, drawn, encode_polyline, export_serving
from dayhike.web import create_app
from test_built_hikes import ridge_stage
from test_loops import MEADOW_PATH, loops_export

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
    cities = Path(__file__).parent / "fixtures" / "geonames.txt"
    return ServingData(export_serving(path, directory / "web-serving", geonames_path=cities))


@pytest.fixture(scope="module")
def server(data: ServingData) -> Iterator[str]:
    # Every test here asks from one address: the budgets per minute (RANK_RATE, API_RATE) are not under test.
    yield from serve(create_app(data, lambda max_hours: Matrix(), rank_rate=1_000, api_rate=100_000))


@pytest.fixture(scope="module")
def counted_server(data: ServingData) -> Iterator[str]:
    """The app with GoatCounter set, so every page loads the real count.js."""
    yield from serve(create_app(data, goatcounter="gofindmeahike"))


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
        # A Wish scores only the Measures it asks a value of: the others have no score and no note.
        for hike in (hike for row in body["results"] for hike in (row, *row["versions"])):
            for name, scored in hike.get("measures", {}).items():
                if name not in asked["wish"]:
                    scored.update(score=None, raw=None, note=None)
        route.fulfill(json={**body, "seed": None, "wish": asked["wish"]})


def open_page(
    browser,
    server: str,
    api: Api,
    *,
    width: int = 1280,
    hash: str = "",
    init: str = "",
    draw: int | None = None,
    touch: bool = False,
):
    """``draw``: the draw number each Find takes, in place of a random one; ``touch``: a phone's touch screen."""
    page = browser.new_page(viewport={"width": width, "height": 900}, has_touch=touch, is_mobile=touch)
    if init:
        page.add_init_script(init)
    page.set_default_timeout(5000)
    if draw is not None:
        page.add_init_script(f"crypto.getRandomValues = (numbers) => numbers.fill({draw});")
    page.route(lambda url: not url.startswith(server), lambda route: route.abort())  # no tiles
    page.route(f"{server}/api/measures", api.measures)
    page.route(f"{server}/api/rank", api.rank)
    page.goto(f"{server}/{hash}")
    page.wait_for_selector(".weight", state="attached")
    return page


SHOWN = "rows => rows.map(r => r.dataset.measure)"


def open_advanced(page) -> None:
    """Opens Advanced search, or folds it away again when open."""
    page.get_by_text("Advanced search", exact=True).click()
BASIC = ["length", "elevation_gain", "highest", "landcover", "temperature", "waterside"]


def test_a_hiker_sees_a_few_plain_measures_and_the_rest_wait_behind_advanced_search(browser, server) -> None:
    page = open_page(browser, server, Api({}))

    assert page.locator(".weight:visible").evaluate_all(SHOWN) == BASIC
    assert page.get_by_label("Climbing weight, 0 to 100").input_value() == "100"
    assert not page.get_by_label("Summit weight, 0 to 100").is_visible()
    assert not page.get_by_label("Lowest point weight, 0 to 100").is_visible()

    open_advanced(page)

    assert page.get_by_label("Lowest point weight, 0 to 100").is_visible()
    assert page.get_by_label("Summit weight, 0 to 100").is_visible()
    assert page.locator("#advanced legend").all_text_contents() == [
        "Effort and steepness", "Altitude", "Scenery", "Facilities", "Trail", "Water", "Weather",
    ]  # fmt: skip
    assert len(page.locator(".weight:visible").evaluate_all(SHOWN)) == 27


def test_every_main_feature_counts_by_default(browser, server) -> None:
    page = open_page(browser, server, Api({}))

    basic = page.locator("#weights .weight input[type=range]")
    assert basic.count() == len(BASIC)
    assert set(basic.evaluate_all("inputs => inputs.map(i => i.value)")) == {"100"}
    assert page.locator("#weights .weight.excluded").count() == 0


def test_each_weight_is_a_slider_from_0_to_100_in_steps_of_10_with_its_value_shown(browser, server) -> None:
    page = open_page(browser, server, Api({}))
    open_advanced(page)

    sliders = page.locator(".weight input:not(.wish)")
    assert set(sliders.evaluate_all("inputs => inputs.map(i => [i.type, i.min, i.max, i.step].join())")) == {"range,0,100,10"}
    assert page.locator(".weight input[type=checkbox]:not(.wish)").count() == 0  # no "off" tick
    assert page.locator('.weight[data-measure="elevation_gain"] output').inner_text() == "100"

    climbing = page.get_by_label("Climbing weight, 0 to 100")
    climbing.focus()
    climbing.press("ArrowLeft")
    assert climbing.input_value() == "90"
    assert page.locator('.weight[data-measure="elevation_gain"] output').inner_text() == "90"


def test_a_weight_slid_to_0_is_off_struck_through_and_left_out(browser, server) -> None:
    api = Api({})
    page = open_page(browser, server, api, hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    climbing = page.locator('.weight[data-measure="elevation_gain"]')

    page.get_by_label("Climbing weight, 0 to 100").fill("0")

    assert "excluded" in climbing.get_attribute("class")
    assert climbing.locator(".name").evaluate("e => getComputedStyle(e).textDecorationLine") == "line-through"
    assert climbing.locator("output").inner_text() == "off"
    page.get_by_role("button", name="Find similar").click()
    page.locator("#results-table tbody tr").first.wait_for()
    assert not api.ranked[-1]["weights"].get("elevation_gain")

    page.get_by_label("Climbing weight, 0 to 100").fill("30")
    assert "excluded" not in climbing.get_attribute("class")
    assert climbing.locator("output").inner_text() == "30"


GROUP_IDS = {"basic", "effort", "altitude", "scenery", "facilities", "trail", "water", "weather"}


def test_the_facts_of_a_hike_are_headed_by_plain_group_names_never_raw_ids(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.get_by_role("button", name="Find similar").click()
    page.locator("#results-table tbody tr").first.click()

    page.locator('#selected table.facts th[scope="rowgroup"]').first.wait_for()
    headings = page.locator('#selected table.facts th[scope="rowgroup"]').all_text_contents()
    assert headings == [
        "Main features", "Effort and steepness", "Altitude", "Scenery", "Facilities", "Trail", "Water", "Weather",
    ]  # fmt: skip
    shown = page.locator('#selected table.facts th[scope="rowgroup"], #weights-panel legend').all_text_contents()
    assert not GROUP_IDS & {text.strip() for text in shown}


def test_looking_into_advanced_search_leaves_results_current_until_an_advanced_weight_counts(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.get_by_role("button", name="Find similar").click()
    page.locator("#results-table tbody tr").first.wait_for()
    results = page.locator("#results")

    open_advanced(page)
    open_advanced(page)
    page.evaluate("() => new Promise((done) => setTimeout(done, 50))")  # the toggle events have run
    assert "stale" not in (results.get_attribute("class") or "")

    open_advanced(page)
    page.get_by_label("Lowest point weight, 0 to 100").fill("50")
    page.get_by_role("button", name="Find similar").click()
    page.wait_for_function("() => !document.querySelector('#results').classList.contains('stale')")
    open_advanced(page)
    page.wait_for_function("() => document.querySelector('#results').classList.contains('stale')")


def test_measures_behind_a_closed_advanced_search_do_not_count(browser, server) -> None:
    api = Api({})
    page = open_page(browser, server, api, hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    open_advanced(page)
    page.get_by_label("Lowest point weight, 0 to 100").fill("50")
    open_advanced(page)

    page.get_by_role("button", name="Find similar").click()
    page.locator("#results-table tbody tr").first.wait_for()

    counted = {name for name, weight in api.ranked[-1]["weights"].items() if weight > 0}
    assert counted == {"length", "elevation_gain", "highest", "landcover", "temperature", "waterside"}

    open_advanced(page)
    page.get_by_role("button", name="Find similar").click()
    page.wait_for_function("() => !document.querySelector('#results').classList.contains('stale')")

    assert api.ranked[-1]["weights"]["lowest"] == 50


def test_a_wish_shows_the_basic_measures_and_asks_advanced_ones_only_while_open(browser, server) -> None:
    api = Api(WISHABLE)
    page = open_page(browser, server, api)
    page.get_by_role("button", name="No particular hike in mind?").click()

    assert page.locator(".weight:visible").evaluate_all(SHOWN) == ["elevation_gain", "highest", "waterside"]
    open_advanced(page)
    page.get_by_label("Hut along you want").click()
    page.get_by_label("Climbing you want, in m").fill("900")
    open_advanced(page)

    page.wait_for_url("**/#wish=elevation_gain:900:100")
    with page.expect_response(f"{server}/api/rank"):
        page.get_by_role("button", name="Find hikes").click()
    assert api.ranked[-1]["wish"] == {"elevation_gain": 900}

    shared = open_page(browser, server, Api(WISHABLE), hash="#wish=hut_along:0:100")

    assert shared.get_by_label("Hut along you want").is_visible()
    assert not shared.get_by_label("Hut along you want").is_checked()


def test_a_hiker_with_no_hike_in_mind_gets_a_wish_form_of_the_wishable_measures(browser, server) -> None:
    page = open_page(browser, server, Api(WISHABLE))

    page.get_by_role("button", name="No particular hike in mind?").click()
    open_advanced(page)

    rows = page.locator(".weight:visible")
    assert sorted(rows.evaluate_all("rows => rows.map(r => r.dataset.measure)")) == sorted(WISHABLE)
    gain = page.get_by_label("Climbing you want, in m")
    assert gain.get_attribute("type") == "number"
    assert (gain.get_attribute("min"), gain.get_attribute("max"), gain.get_attribute("step")) == ("0", "3000", "50")
    # Empty until asked, so no value looks asked while its weight is off; the default is a hint.
    assert gain.input_value() == "" and gain.get_attribute("placeholder") == "600"
    hut = page.get_by_label("Hut along you want")
    assert hut.get_attribute("type") == "checkbox" and hut.is_checked()
    assert page.get_by_label("Climbing weight, 0 to 100").is_visible()
    assert not page.locator("#search").is_visible()

    page.get_by_role("button", name="Back to a hike you liked").click()

    assert page.locator("#search").is_visible()
    assert page.locator(".weight:visible").count() > len(WISHABLE)
    assert not gain.is_visible()


def test_finding_hikes_for_a_wish_asks_the_ranking_for_it_and_lists_the_results(browser, server) -> None:
    api = Api(WISHABLE)
    page = open_page(browser, server, api)
    page.get_by_role("button", name="No particular hike in mind?").click()
    page.get_by_label("Climbing you want, in m").fill("900")
    page.get_by_label("Climbing weight, 0 to 100").fill("80")
    open_advanced(page)
    page.get_by_label("Hut along weight, 0 to 100").fill("40")
    page.get_by_label("Hut along you want").uncheck()
    page.get_by_label("Highest point weight, 0 to 100").fill("0")
    page.get_by_label("Lake or sea weight, 0 to 100").fill("0")
    page.get_by_label("Only hikes near a starting place").check()
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
    page.get_by_label("Climbing you want, in m").fill("900")
    page.get_by_label("Climbing weight, 0 to 100").fill("80")
    open_advanced(page)
    page.get_by_label("Hut along weight, 0 to 100").fill("40")
    page.get_by_label("Lake or sea weight, 0 to 100").fill("0")
    page.get_by_role("button", name="Find hikes").click()

    page.locator("#results-table tbody tr").first.click()

    facts = page.locator("#selected table.facts")
    facts.wait_for()
    assert facts.locator("thead th").all_inner_texts() == ["Measure", "This hike", "You want", "Score ?"]
    gain = facts.locator("tr", has=page.locator("td", has_text="Climbing"))
    assert gain.locator("td").nth(2).inner_text() == "900 m"
    waterside = facts.locator("tr", has=page.locator("td", has_text="Lake or sea"))
    assert waterside.locator("td").nth(2).inner_text() == "left out"


def test_clicking_the_hut_box_as_the_guide_says_asks_for_a_hut(browser, server) -> None:
    # docs/user-guide.md: "click the "yes" box of Hut along ... the click turns it on".
    api = Api(WISHABLE)
    page = open_page(browser, server, api)
    page.get_by_role("button", name="No particular hike in mind?").click()
    page.get_by_label("Climbing you want, in m").fill("800")
    open_advanced(page)
    hut = page.get_by_label("Hut along you want")
    assert hut.is_checked() and page.get_by_label("Hut along weight, 0 to 100").input_value() == "0"

    hut.click()
    with page.expect_response(f"{server}/api/rank"):
        page.get_by_role("button", name="Find hikes").click()

    assert api.ranked[-1]["wish"] == {"elevation_gain": 800, "hut_along": True}
    assert api.ranked[-1]["weights"]["hut_along"] == 100


def test_a_measure_the_wish_does_not_ask_is_left_out_not_no_data(browser, server) -> None:
    page = open_page(browser, server, Api(WISHABLE))
    page.get_by_role("button", name="No particular hike in mind?").click()
    open_advanced(page)
    page.get_by_label("Hut along you want").click()
    page.get_by_role("button", name="Find hikes").click()
    row = page.locator("#results-table tbody tr").first
    row.wait_for()

    titles = row.locator("td.cell.none").evaluate_all("cells => cells.map(c => c.title)")
    assert titles and set(titles) == {"not in your wish"}
    row.click()
    facts = page.locator("#selected table.facts")
    facts.wait_for()
    length = facts.locator("tr", has=page.locator("td", has_text="Length"))
    assert length.locator("td").nth(3).inner_text() == "left out"
    assert "no data" not in facts.locator("td.score").all_inner_texts()


def test_a_share_is_wished_in_percent_and_asked_as_a_share(browser, server) -> None:
    percent = {**WISHABLE["waterside"], "display": {"unit": "%", "per": 0.01}}
    api = Api({**WISHABLE, "waterside": percent})
    page = open_page(browser, server, api)
    page.get_by_role("button", name="No particular hike in mind?").click()
    waterside = page.get_by_label("Lake or sea you want, in %")
    assert waterside.get_attribute("placeholder") == "50"  # the default, in %
    assert (waterside.get_attribute("min"), waterside.get_attribute("max"), waterside.get_attribute("step")) == ("0", "100", "5")

    waterside.fill("30")
    with page.expect_response(f"{server}/api/rank"):
        page.get_by_role("button", name="Find hikes").click()

    assert api.ranked[-1]["wish"] == {"waterside": 0.3}


def test_a_wish_survives_a_reload_and_opens_from_its_link(browser, server) -> None:
    page = open_page(browser, server, Api(WISHABLE))
    page.get_by_role("button", name="No particular hike in mind?").click()
    page.get_by_label("Climbing you want, in m").fill("900")
    page.get_by_label("Climbing weight, 0 to 100").fill("80")
    open_advanced(page)
    page.get_by_label("Hut along weight, 0 to 100").fill("40")
    page.get_by_label("Hut along you want").uncheck()
    page.get_by_label("Highest point weight, 0 to 100").fill("0")
    page.get_by_label("Return to start").uncheck()

    page.reload()
    page.wait_for_selector(".weight", state="attached")

    assert page.get_by_label("Climbing you want, in m").input_value() == "900"
    assert page.get_by_label("Climbing weight, 0 to 100").input_value() == "80"
    assert not page.get_by_label("Hut along you want").is_checked()
    assert page.get_by_label("Hut along weight, 0 to 100").input_value() == "40"
    assert page.get_by_label("Highest point weight, 0 to 100").input_value() == "0"
    assert not page.get_by_label("Return to start").is_checked()
    assert not page.locator("#search").is_visible()

    shared = open_page(browser, server, Api(WISHABLE), hash="#wish=highest:2200:100")

    assert shared.get_by_label("Highest point you want, in m").input_value() == "2200"
    assert shared.get_by_label("Highest point weight, 0 to 100").input_value() == "100"
    assert shared.get_by_label("Climbing weight, 0 to 100").input_value() == "0"
    assert shared.get_by_role("button", name="Find hikes").is_enabled()


KM_LENGTH = {
    "kind": "number", "unit": "m", "min": 1000, "max": 2_000_000, "step": 500, "default": 10_000,
    "display": {"unit": "km", "per": 1000},
}  # fmt: skip


def test_a_multi_day_length_is_wished_in_km_with_a_hint(browser, server) -> None:
    # Lengths are typed in km, up to 2,000 km, and sent in metres (ADR-0035).
    api = Api({**WISHABLE, "length": KM_LENGTH})
    page = open_page(browser, server, api)
    page.get_by_role("button", name="No particular hike in mind?").click()
    length = page.get_by_label("Length you want, in km")

    assert (length.get_attribute("min"), length.get_attribute("max"), length.get_attribute("step")) == ("1", "2000", "0.5")
    assert length.input_value() == "" and length.get_attribute("placeholder") == "10"
    assert page.get_by_text("Over about 25 km is usually more than a day").is_visible()

    length.fill("66")
    page.get_by_label("Length weight, 0 to 100").fill("100")
    page.get_by_role("button", name="Find hikes").click()
    page.locator("#results-table tbody tr").first.wait_for()

    assert api.ranked[-1]["wish"] == {"length": 66_000}
    page.wait_for_url("**/#wish=length:66000:100")  # the address keeps metres, as links made before km do
    page.locator("#results-table tbody tr").first.click()
    facts = page.locator("#selected table.facts")
    facts.wait_for()
    assert facts.locator("tr", has=page.locator("td", has_text="Length")).locator("td").nth(2).inner_text() == "66 km"

    shared = open_page(browser, server, api, hash="#wish=length:12500:100")
    assert shared.get_by_label("Length you want, in km").input_value() == "12.5"


def test_the_site_says_it_lists_day_hikes_and_multi_day_tours(browser, server) -> None:
    page = browser.new_page()
    page.route(lambda url: not url.startswith(server), lambda route: route.abort())  # no tiles
    page.goto(f"{server}/about.html")
    for told in (
        "Day hikes and multi-day tours",
        "hikes joined from signpost networks",
        "day stages of long tours",
    ):
        assert page.get_by_text(told).first.is_visible(), told

    page.goto(f"{server}/")
    assert "day hikes" not in page.locator(".tagline").inner_text()
    assert "day hike" not in page.locator('meta[name="description"]').get_attribute("content")


def test_a_wish_address_with_a_small_nonzero_weight_keeps_its_measure_on(browser, server) -> None:
    api = Api(WISHABLE)
    page = open_page(browser, server, api, hash="#wish=highest:2000:3")
    highest = page.locator('.weight[data-measure="highest"]')

    assert page.get_by_label("Highest point weight, 0 to 100").input_value() == "10"
    assert highest.locator(".name").evaluate("e => getComputedStyle(e).textDecorationLine") != "line-through"
    with page.expect_response(f"{server}/api/rank"):
        page.get_by_role("button", name="Find hikes").click()
    assert api.ranked[-1]["wish"] == {"highest": 2000}


def test_a_server_without_wishes_offers_no_wish_form(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash="#wish=highest:2200:100")

    assert not page.get_by_role("button", name="No particular hike in mind?").is_visible()
    assert page.locator("#search").is_visible()


def test_the_wish_form_fits_a_phone(browser, server) -> None:
    page = open_page(browser, server, Api(WISHABLE), width=375)

    page.get_by_role("button", name="No particular hike in mind?").click()

    assert page.get_by_label("Climbing you want, in m").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= 375


def test_a_round_trip_wish_is_off_while_every_hike_returns_to_its_start(browser, server) -> None:
    page = open_page(browser, server, Api(WISHABLE))
    page.get_by_role("button", name="No particular hike in mind?").click()
    open_advanced(page)
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
    page.get_by_label("Climbing weight, 0 to 100").fill("70")
    page.get_by_role("button", name="No particular hike in mind?").click()

    weights = page.locator(".weight:visible input[aria-label$='weight, 0 to 100']")
    assert set(weights.evaluate_all("inputs => inputs.map(i => i.type + i.value)")) == {"range0"}
    assert page.locator(".weight:visible:not(.excluded)").count() == 0  # all off, struck through
    page.get_by_label("Highest point you want, in m").fill("2000")
    open_advanced(page)
    hut = page.get_by_label("Hut along you want")
    hut.click()  # the box shows "yes" while off: the first click asks that "yes"
    assert hut.is_checked()
    assert page.get_by_label("Highest point weight, 0 to 100").input_value() == "100"
    assert page.get_by_label("Hut along weight, 0 to 100").input_value() == "100"
    hut.click()  # a second click asks "no"
    assert not hut.is_checked()
    assert page.get_by_label("Hut along weight, 0 to 100").input_value() == "100"
    assert page.locator(".weight:visible:not(.excluded)").evaluate_all(SHOWN) == ["highest", "hut_along"]

    page.get_by_role("button", name="Back to a hike you liked").click()

    assert page.get_by_label("Climbing weight, 0 to 100").input_value() == "70"
    assert page.get_by_label("Length weight, 0 to 100").input_value() == "100"
    assert page.locator("#weights .weight.excluded").count() == 0


def test_resetting_a_wish_asks_nothing_again(browser, server) -> None:
    page = open_page(browser, server, Api(WISHABLE))
    page.get_by_role("button", name="No particular hike in mind?").click()
    page.get_by_label("Highest point you want, in m").fill("2000")

    page.get_by_role("button", name="Reset weights").click()

    assert page.get_by_label("Highest point weight, 0 to 100").input_value() == "0"
    assert page.get_by_label("Highest point you want, in m").input_value() == ""


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
    open_advanced(page)
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
    assert page.get_by_role("link", name="HikesAlike").get_attribute("href") == "/"

    page.locator(".top .mark").click()
    page.wait_for_selector(".weight", state="attached")

    assert page.url == f"{server}/"
    assert page.title() == "HikesAlike"
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
    assert "starting points" not in page.locator("#results-note").inner_text()

    page.get_by_label("Length weight, 0 to 100").fill("10")  # the shown Ranking's query is the one asked again
    with page.expect_response(f"{server}/api/rank"):
        suggest.click()
    page.wait_for_function("() => document.querySelector('#results-note').textContent.includes('loops composed')")

    assert api.ranked[-1] == {**api.ranked[-2], "loops": True}
    # Searched from a few starts only (ADR-0045), so the page says the loops are not all there are.
    assert "not every loop there is and can change when the catalog is rebuilt" in page.locator("#results-note").inner_text()
    assert page.locator("#results-table tbody tr").count() > 0
    assert page.locator("#results-stale").is_visible()  # still not the query on the left
    assert not suggest.is_visible()


def test_suggest_loops_stops_asking_for_the_rows_shown_once_a_page_of_them_fails(browser, loops_server, monkeypatch) -> None:
    monkeypatch.setattr("dayhike.serving.GROUP_M", 0.0)
    monkeypatch.setattr(web, "RESULTS", 2)
    page = open_page(browser, loops_server, Api({}), hash=f"#hike={MEADOW_PATH}&strict")
    page.get_by_text("Chosen: Meadow Path").wait_for()
    _find(page)
    page.get_by_role("button", name="Show 2 more").click()
    page.wait_for_function("() => state.results.length === 4")
    refused = []

    def too_many(route) -> None:
        if "after" not in json.loads(route.request.post_data):
            route.fallback()
            return
        refused.append(route.request)
        route.fulfill(status=429, json={"detail": "Too many searches from your address; wait a minute."})

    page.route(f"{loops_server}/api/rank", too_many)
    page.get_by_role("button", name="Suggest loops near these hikes (takes a few seconds)").click()

    page.wait_for_function("() => state.answer.loops")
    page.wait_for_timeout(500)
    assert len(refused) == 1
    assert page.evaluate("state.results.length") == 2


def _listed(page) -> list[str]:
    return page.locator("#results-table tbody tr td.name").all_inner_texts()


def test_show_more_appends_the_next_rows_under_the_table(browser, server, monkeypatch) -> None:
    monkeypatch.setattr(web, "RESULTS", 1)  # three areas hold the other hikes: three pages of one row
    api = Api({})
    page = open_page(browser, server, api, hash=f"#hike={RIDGE_WALK_ID}&strict")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.get_by_role("button", name="Find similar").click()
    page.locator("#results-table tbody tr").first.wait_for()
    more = page.get_by_role("button", name="Show 1 more")
    first = _listed(page)
    assert len(first) == 1 and "after" not in api.ranked[-1]

    page.get_by_label("Length weight, 0 to 100").fill("10")  # the shown Ranking's query is the one continued
    more.click()
    page.locator("#results-table tbody tr").nth(1).wait_for()

    lat, lon = page.evaluate("state.results[0].trailhead")
    # Loops are searched for the first rows only (ADR-0054).
    assert api.ranked[-1] == {**api.ranked[0], "loops": False, "after": [{"lat": lat, "lon": lon}]}
    assert _listed(page)[0] == first[0] and len(set(_listed(page))) == 2
    assert page.locator("#results-table tbody tr td:first-child").all_inner_texts() == ["1", "2"]
    with page.expect_response(f"{server}/api/rank"):
        more.click()
    page.locator("#results-table tbody tr").nth(2).wait_for()
    assert len(set(_listed(page))) == 3
    assert page.locator("#results-more").is_hidden()  # the last answer held the last area


def _names(page) -> list[str]:
    return page.locator("#results-table tbody tr:not(.version) .hike-name").all_inner_texts()


class Pool:
    """The real answers to /api/rank, every row's score set by ``scores`` (best first), kept as asked."""

    def __init__(self, scores=None) -> None:
        self.scores = scores
        self.asked: list[dict] = []
        self.answers: list[list[dict]] = []

    def __call__(self, route) -> None:
        self.asked.append(json.loads(route.request.post_data))
        body = route.fetch().json()
        if self.scores is not None:
            for row, score in zip(body["results"], self.scores):
                row["score"] = score
        self.answers.append(body["results"])
        route.fulfill(json=body)

    def drawn(self, draw: int, count: int, answer: int = -1) -> list[int]:
        rows = self.answers[answer]
        return drawn([r["score"] for r in rows], [str(r["id"] or r["route"]["text"]) for r in rows], draw, count)

    def names(self, indices, answer: int = -1) -> list[str]:
        return [self.answers[answer][i]["name"] for i in indices]


def _find(page) -> None:
    page.get_by_role("button", name="Find similar").click()
    page.locator("#results-table tbody tr").first.wait_for()


def test_the_page_draws_as_the_server_does(browser, server) -> None:
    page = open_page(browser, server, Api({}))
    keys = ["1002", "19787033", "n1w2n3", "Loop near Peyrou"]

    for draw in (0, 7, 4_294_967_295):
        for key in keys:
            assert page.evaluate("([d, k]) => drawNoise(d, k)", [draw, key]) == pytest.approx(draw_noise(draw, key))
        scores = [0.81, 0.8, 0.8, 0.79]
        assert page.evaluate("([s, k, d]) => drawn(s, k, d, 2, 0.005)", [scores, keys, draw]) == drawn(scores, keys, draw, 2)


def test_the_page_lists_hikes_drawn_by_score_from_the_answer_and_marks_those_strict_order_would_not(
    browser, server, monkeypatch
) -> None:
    monkeypatch.setattr(web, "RESULTS", 2)  # 2 rows drawn from the 3 areas
    pool = Pool([0.5, 0.5, 0.5])
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}", draw=11)
    page.route(f"{server}/api/rank", pool)
    page.get_by_text("Chosen: Ridge Walk").wait_for()

    _find(page)

    assert pool.asked[-1]["draw"] == page.evaluate("state.draw") == 11  # the server draws loop starts with it
    shown = pool.drawn(11, 2)
    assert _names(page) == pool.names(shown)
    marks = page.locator("#results-table tbody tr td:first-child").evaluate_all(
        "cells => cells.map(c => Boolean(c.querySelector('.picked')))")  # fmt: skip
    assert marks == [i >= 2 for i in shown]
    note = page.locator("#results-note").inner_text()
    assert "picked from the best 3 areas, closer matches more often" in note
    assert "The best 3 areas match equally well" in note  # ties: the order among them says nothing
    assert page.get_by_role("button", name="Redraw").is_enabled()
    assert not page.get_by_label("Strict order").is_checked()


def test_strict_order_lists_the_ranking_as_it_is_without_asking_again(browser, server, monkeypatch) -> None:
    monkeypatch.setattr(web, "RESULTS", 2)
    pool = Pool([0.5, 0.5, 0.5])
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}", draw=11)
    page.route(f"{server}/api/rank", pool)
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)
    drawn_names = _names(page)

    page.get_by_label("Strict order").check()

    assert _names(page) == pool.names([0, 1]) and len(pool.asked) == 1
    assert page.get_by_role("button", name="Redraw").is_disabled()
    assert "picked" not in page.locator("#results-note").inner_text()
    assert page.locator("#results-table .picked").count() == 0
    assert page.evaluate("location.hash") == f"#hike={RIDGE_WALK_ID}&strict"

    page.get_by_label("Strict order").uncheck()

    assert _names(page) == drawn_names and len(pool.asked) == 1  # the same draw again
    assert page.evaluate("location.hash") == f"#hike={RIDGE_WALK_ID}"
    page.get_by_label("Strict order").check()
    _find(page)
    assert "draw" not in pool.asked[-1]  # loop starts as ranked too


def test_redraw_draws_page_one_again_from_the_answer_and_drops_later_pages(browser, server, monkeypatch) -> None:
    monkeypatch.setattr(web, "RESULTS", 1)
    pool = Pool([0.5, 0.5, 0.5])
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}", draw=11)
    page.route(f"{server}/api/rank", pool)
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)
    first = _names(page)
    page.get_by_role("button", name="Show 1 more").click()
    page.locator("#results-table tbody tr").nth(1).wait_for()
    asked = len(pool.asked)

    page.get_by_role("button", name="Redraw").click()

    draw = page.evaluate("state.draw")
    assert 11 < draw <= 11 + 5 and len(pool.asked) == asked  # drawn again here, a few times at most
    assert _names(page) == pool.names(pool.drawn(draw, 1, answer=0), answer=0) != first
    assert page.get_by_role("button", name="Show 1 more").is_visible()
    page.get_by_role("button", name="Suggest loops near these hikes (takes a few seconds)").click()
    page.wait_for_function("() => document.querySelector('#results-note').textContent.includes('loops composed')")
    assert pool.asked[-1]["draw"] == draw and pool.asked[-1]["loops"]  # the draw goes on with loops


def test_redraw_says_so_when_no_other_hike_is_close_enough_to_be_drawn(browser, server, monkeypatch) -> None:
    monkeypatch.setattr(web, "RESULTS", 1)
    pool = Pool([0.9, 0.5, 0.1])  # 80 T apart: the best is drawn every time
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}", draw=11)
    page.route(f"{server}/api/rank", pool)
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)

    page.get_by_role("button", name="Redraw").click()

    assert _names(page) == pool.names([0])
    assert page.locator("#status").inner_text() == "No other close matches here; widen the distance or turn on Strict order."


def test_each_next_page_is_drawn_from_the_areas_not_shown_with_the_same_draw_number(
    browser, server, monkeypatch
) -> None:
    monkeypatch.setattr(web, "RESULTS", 1)
    pool = Pool([0.5, 0.5, 0.5])
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}", draw=11)
    page.route(f"{server}/api/rank", pool)
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)
    for shown in (1, 2):
        page.get_by_role("button", name="Show 1 more").click()
        page.locator("#results-table tbody tr").nth(shown).wait_for()

    names = _names(page)
    starts = page.evaluate("state.results.map(r => ({lat: r.trailhead[0], lon: r.trailhead[1]}))")
    assert len(set(names)) == 3  # no area twice, none skipped
    assert [asked.get("after", []) for asked in pool.asked] == [starts[:0], starts[:1], starts[:2]]
    assert {asked["draw"] for asked in pool.asked} == {11}
    assert names == [pool.names(pool.drawn(11, 1, answer=k), answer=k)[0] for k in range(3)]
    assert page.locator("#results-more").is_hidden()


def test_a_strict_link_opens_in_strict_order_and_the_address_follows_the_tick(browser, server) -> None:
    pool = Pool()
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}&oneway&strict")
    page.route(f"{server}/api/rank", pool)
    page.get_by_text("Chosen: Ridge Walk").wait_for()

    assert page.get_by_label("Strict order").is_checked() and not page.locator("#return-on").is_checked()
    _find(page)
    assert "draw" not in pool.asked[-1] and pool.asked[-1]["return_to_start"] is False
    page.get_by_label("Strict order").uncheck()
    assert page.evaluate("location.hash") == f"#hike={RIDGE_WALK_ID}&oneway"
    page.get_by_label("Strict order").check()
    assert page.evaluate("location.hash") == f"#hike={RIDGE_WALK_ID}&oneway&strict"
    page.goto(f"{server}/#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    assert not page.get_by_label("Strict order").is_checked()  # a link without it draws


def test_the_page_says_what_the_ranking_does_not_list(browser, server) -> None:
    def counted(route) -> None:
        body = route.fetch().json()
        body["results"][0]["more_here"] = 12
        body["other_areas"] = 24_113
        body["loop_search"] = {"starts": 31, "found": 14, "listed": 3, "stopped": True}
        route.fulfill(json=body)

    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.route(f"{server}/api/rank", counted)
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.get_by_role("button", name="Find similar").click()
    page.locator("#results-table tbody tr").first.wait_for()

    count = page.locator("#results-table tbody tr").first.locator(".more-here")
    assert count.inner_text() == "12 more good matches start here"
    assert "later pages do not show them" in count.get_attribute("title")
    assert page.locator("#other-areas").inner_text() == "24,113 more hikes start in other areas."
    note = page.locator("#results-loops-note").inner_text()
    assert "Loops: searched from 31 starts near the best hikes, 14 found, none listed." in note
    assert "stopped at its time limit" in note


def test_the_loop_note_counts_the_loops_in_the_rows_shown(browser, server, monkeypatch) -> None:
    monkeypatch.setattr(web, "RESULTS", 1)  # three areas: page 1 shows one row of a three-row pool

    def loops(route) -> None:
        body = route.fetch().json()
        for row in body["results"]:
            row["route"] = {"text": f"loop {row['id']}"}
            row["versions"] = [dict(row, versions=[], route={"text": f"version {row['id']}"})]
        body["loop_search"] = {"starts": 20, "found": 4, "listed": 6, "stopped": False}
        route.fulfill(json=body)

    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}&strict")
    page.route(f"{server}/api/rank", loops)
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)
    note = page.locator("#results-loops-note")
    # Two loops in one row: the count before the row numbers is the count of rows.
    fewer = "Loops: searched from 20 starts near the best hikes, 4 found, 2 listed in 1 row: #1 (marked composed); the others rank lower"
    assert fewer in note.inner_text()

    with page.expect_response(f"{server}/api/rank"):
        page.get_by_role("button", name="Show 1 more").click()
    page.locator("#results-table tbody tr:not(.version)").nth(1).wait_for()
    assert "4 found, 4 listed in 2 rows: #1, #2 (marked composed)." in note.inner_text()

    page.get_by_label("Strict order").uncheck()  # page 1 again, drawn, later pages dropped
    assert fewer in note.inner_text()
    assert "rank lower, are not drawn on this page (Redraw or tick Strict order), or walk" in note.inner_text()
    page.get_by_role("button", name="Redraw").click()
    assert fewer in note.inner_text()


# Whether every line of an element lies inside its cell, and a pointer on its first line finds it (its tooltip shows).
SHOWN_WHOLE = """e => {
  const cell = e.closest("td").getBoundingClientRect();
  const lines = [...e.getClientRects()];
  const hit = document.elementFromPoint((lines[0].left + lines[0].right) / 2, (lines[0].top + lines[0].bottom) / 2);
  return lines.every((r) => r.left >= cell.left && r.right <= cell.right) && e.contains(hit);
}"""


@pytest.mark.parametrize("width", [1280, 390])
def test_the_versions_and_more_here_line_is_not_cut_off(browser, server, width) -> None:
    def counted(route) -> None:
        body = route.fetch().json()
        first = body["results"][0]
        first["name"] = "A very long hike name that does not fit in the hike column of the table"
        first["more_here"] = 12
        first["versions"] = first["versions"] or [dict(first, versions=[])] * 2
        route.fulfill(json=body)

    page = open_page(browser, server, Api({}), width=width, hash=f"#hike={RIDGE_WALK_ID}")
    page.route(f"{server}/api/rank", counted)
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.get_by_role("button", name="Find similar").click()
    row = page.locator("#results-table tbody tr").first
    row.wait_for()

    count = row.locator(".more-here")
    assert count.inner_text() == "12 more good matches start here"
    count.scroll_into_view_if_needed()
    assert count.evaluate(SHOWN_WHOLE)
    assert row.locator(".versions").first.evaluate(SHOWN_WHOLE)
    name = row.locator(".hike-name")
    assert name.evaluate("e => getComputedStyle(e).textOverflow") == "ellipsis"
    assert name.evaluate("e => e.scrollWidth > e.clientWidth")  # the long name itself is still cut


def test_a_distance_limit_alone_or_with_the_drive_time_is_asked(browser, server) -> None:
    api = Api({})
    page = open_page(browser, server, api, hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.get_by_label("Only hikes near a starting place").check()
    page.get_by_label("From", exact=True).fill("45.2, 15.1")
    page.get_by_label("Max drive hours", exact=True).uncheck()
    page.get_by_label("Max km, straight line", exact=True).check()
    page.get_by_label("Max km value").fill("20")

    page.get_by_role("button", name="Find similar").click()
    page.locator("#results-table tbody tr").first.wait_for()

    asked = api.ranked[-1]
    assert asked["origin"] == {"lat": 45.2, "lon": 15.1} and asked["max_km"] == 20 and "max_hours" not in asked
    assert "Distance" in page.locator("#results-table thead").inner_text()
    assert "Drive" not in page.locator("#results-table thead").inner_text()
    assert page.locator("#results-table tbody tr").first.locator("td").nth(3).inner_text() == "0.0 km"

    page.get_by_label("Max drive hours", exact=True).check()
    page.get_by_role("button", name="Find similar").click()
    page.wait_for_function("() => document.querySelector('#results-table thead').textContent.includes('Drive')")
    assert api.ranked[-1]["max_km"] == 20 and api.ranked[-1]["max_hours"] == 2

    page.get_by_label("Max km, straight line", exact=True).uncheck()
    page.get_by_label("Max drive hours", exact=True).uncheck()
    page.get_by_role("button", name="Find similar").click()
    page.locator("#status").get_by_text("Tick Max drive hours, Max km or both.").wait_for()


def test_round_and_small_max_km_values_are_valid_in_the_box(browser, server) -> None:
    page = open_page(browser, server, Api({}))
    page.get_by_label("Only hikes near a starting place").check()
    box = page.get_by_label("Max km value")

    for km in ("1", "4", "30", "50", "200"):
        box.fill(km)
        assert box.evaluate("box => box.validity.valid"), km


def test_the_grouping_radius_waits_under_more_options_and_follows_the_limits(browser, server) -> None:
    api = Api({})
    page = open_page(browser, server, api, hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    radius = page.get_by_label("Group hikes starting within (km)")
    assert not radius.is_visible()

    page.get_by_text("More options", exact=True).click()

    assert radius.is_visible() and radius.input_value() == ""
    assert radius.get_attribute("placeholder") == "10"  # no limit: today's 10 km
    page.get_by_label("Only hikes near a starting place").check()
    assert radius.get_attribute("placeholder") == "5"  # 2 h at 50 km/h, 5 %
    page.get_by_label("Max km, straight line", exact=True).check()
    page.get_by_label("Max km value").fill("30")
    assert radius.get_attribute("placeholder") == "1.5"  # the smaller limit
    page.get_by_label("Max km value").fill("4")
    assert radius.get_attribute("placeholder") == "0.5"  # at least 500 m
    page.get_by_label("Only hikes near a starting place").uncheck()
    page.get_by_role("button", name="Find similar").click()
    page.locator("#results-table tbody tr").first.wait_for()
    assert "group_km" not in api.ranked[-1]  # the server follows the limits

    radius.fill("20")
    page.get_by_role("button", name="Find similar").click()
    page.wait_for_function("() => document.querySelector('#results-note').textContent.includes('within 20 km')")
    assert api.ranked[-1]["group_km"] == 20
    assert page.locator("#results-table tbody tr").count() == 2  # 1002 and 1003, 14 km apart, share a row


def test_a_row_whose_version_is_a_loop_is_marked_composed(browser, server) -> None:
    def loop_version(route) -> None:
        body = route.fetch().json()
        first = body["results"][0]
        first["versions"] = [dict(first, versions=[], id=None, name="Loop from the hut", route={"text": "loop"})]
        body["loop_search"] = {"starts": 5, "found": 3, "listed": 1, "best": first["score"], "stopped": False}
        route.fulfill(json=body)

    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}&strict")
    page.route(f"{server}/api/rank", loop_version)
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)

    row = page.locator("#results-table tbody tr").first
    assert row.locator(".composed-tag").count() == 0  # the row itself is a catalog hike
    assert row.locator(".composed-var").inner_text() == "composed var 2"
    assert "1 listed: #1 (marked composed)" in page.locator("#results-loops-note").inner_text()
    assert "Rows marked composed are loops" in page.locator("#results-note").inner_text()


@pytest.mark.parametrize(
    ("search", "says"),
    [
        ({"found": 14, "best": -0.01}, ", 14 found, none listed: the best scores {best}, below #{n}'s {last}."),
        (
            {"found": 14, "best": -0.0001},
            ", 14 found, none listed: the best scores {last}, level with #{n}'s {last}; it walks mostly the same trails"
            " as a listed hike or the hike you like.",
        ),
        (
            {"found": 14, "best": 0.01},
            ", 14 found, none listed: the best scores {best}, above #{n}'s {last}; it walks mostly the same trails"
            " as a listed hike or the hike you like.",
        ),
        ({"found": 0, "best": None}, ", no loop found, so none is listed."),
    ],
)
def test_the_loop_note_says_plainly_when_no_loop_is_listed(browser, server, search, says) -> None:
    shown = {}

    def none_listed(route) -> None:
        body = route.fetch().json()
        last = body["results"][-1]["score"]
        last = round(last, 3) + 0.0002  # so a loop 0.0001 below it rounds to the same three decimals
        body["results"][-1]["score"] = last
        best = None if search["best"] is None else last + search["best"]
        body["loop_search"] = {"starts": 31, "found": search["found"], "listed": 0, "best": best, "stopped": False}
        shown.update(n=len(body["results"]), last=f"{last:.3f}", best=None if best is None else f"{best:.3f}")
        route.fulfill(json=body)

    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}&strict")
    page.route(f"{server}/api/rank", none_listed)
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)

    note = page.locator("#results-loops-note").inner_text()
    assert f"Loops: searched from 31 starts near the best hikes{says.format(**shown)}" in note


def test_a_loop_above_the_rows_drawn_is_said_not_drawn(browser, server, monkeypatch) -> None:
    monkeypatch.setattr(web, "RESULTS", 1)  # three areas: page 1 draws one row of a three-row pool

    def above(route) -> None:
        body = route.fetch().json()
        best = body["results"][0]["score"] + 0.01
        body["loop_search"] = {"starts": 31, "found": 14, "listed": 0, "best": best, "stopped": False}
        route.fulfill(json=body)

    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.route(f"{server}/api/rank", above)
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)

    note = page.locator("#results-loops-note").inner_text()
    assert "14 found, none listed: the best scores" in note
    assert "above #1's" in note
    assert "; it is not drawn on this page: Redraw or tick Strict order." in note


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
    for path in ("/", "/about.html", "/credits.html"):
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
    page.get_by_label("Only hikes near a starting place").check()

    page.get_by_label("From").fill("agram")
    page.get_by_role("button", name="Find place").click()
    page.get_by_text("Start: Zagreb, HR (45.8144, 15.9780)").wait_for()

    assert [url for url in asked if "place" in url] == [f"{server}/api/place?limit=1&q=agram"]


def test_a_place_not_found_clears_the_previous_start_and_finding_hikes_says_why(browser, server) -> None:
    api = Api({})
    page = open_page(browser, server, api, hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.get_by_label("Only hikes near a starting place").check()
    page.get_by_label("From", exact=True).fill("agram")
    page.get_by_role("button", name="Find place").click()
    page.get_by_text("Start: Zagreb, HR (45.8144, 15.9780)").wait_for()

    # Set as autofill or a restored form sets it: no input event clears the start found before.
    page.eval_on_selector("#origin", "box => { box.value = 'Nevache'; }")
    page.get_by_role("button", name="Find place").click()
    page.get_by_text("Place not found; try a town name or lat, lon.").wait_for()
    page.get_by_role("button", name="Find similar").click()

    page.get_by_text("No start: Nevache was not found.").wait_for()
    assert api.ranked == []


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

    page.get_by_text("HikesAlike is paused for now").wait_for()
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

    page.get_by_text("HikesAlike is paused or busy").wait_for()
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
    for path in ("/", "/about.html", "/credits.html", "/privacy.html", "/terms.html"):
        page.goto(f"{counted_server}{path}")
        page.wait_for_load_state("load")
        page.wait_for_function("() => window.goatcounter && window.goatcounter.count")  # count.js has run
        assert page.evaluate("[document.cookie, localStorage.length, sessionStorage.length]") == ["", 0, 0], path

    assert context.cookies() == []
    context.close()


@pytest.fixture(scope="module")
def built_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """The app on the test catalog with Ridge Walk's second half built as a stage (ADR-0038)."""
    directory = tmp_path_factory.mktemp("built-page")
    path = directory / "catalog.sqlite"
    build_catalog(FIXTURE, path, sources=[ridge_stage])
    build_similarity(path, directory / "dem")
    data = ServingData(export_serving(path, directory / "web-serving"))
    yield from serve(create_app(data, lambda max_hours: Matrix(), rank_rate=1_000))


def test_a_built_hike_links_the_relations_it_is_built_from_and_itself(browser, built_server) -> None:
    with urllib.request.urlopen(f"{built_server}/api/search?q=stage%202") as response:
        (stage,) = json.load(response)["results"]
    page = open_page(browser, built_server, Api({}), hash=f"#hike={stage['id']}")
    page.get_by_text("Chosen: Ridge Walk, stage 2").wait_for()
    head = page.locator("#liked .detail-head")

    assert "Built from OSM relation 1001" in head.inner_text()
    assert head.get_by_role("link", name="1001").get_attribute("href") == "https://www.openstreetmap.org/relation/1001"
    link = head.get_by_role("link", name="Link to this hike")
    assert link.get_attribute("href") == f"#hike={stage['id']}"
    shared = open_page(browser, built_server, Api({}), hash=link.get_attribute("href"))
    shared.get_by_text("Chosen: Ridge Walk, stage 2").wait_for()
    assert shared.locator("#liked .role").text_content() == "Liked hike"


def test_a_built_hike_found_similar_can_be_edited_on_the_map(browser, built_server) -> None:
    page = open_page(browser, built_server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.get_by_role("button", name="Find similar").click()
    page.locator("#results-table tbody tr", has_text="stage 2").first.click()
    page.locator("#selected").get_by_role("button", name="Edit on the map").click()
    page.wait_for_function("() => compose.on && pick.map.getZoom() >= 13")  # zoomed in, so the segments load

    # The liked hike and the list stay until Use this hike measures a drawing (ADR-0054). This fixture's
    # stage cuts a Segment, so the designer says it cannot draw it, where it is.
    assert page.locator("#liked-name").inner_text().startswith("Chosen: Ridge Walk,")
    assert page.locator("#results").is_visible()
    assert "cannot be drawn here" in page.locator("#map-pick-hint").inner_text()
    centre = page.evaluate("[pick.map.getCenter().lat, pick.map.getCenter().lng]")
    assert centre == pytest.approx(page.evaluate("state.results.find(r => /stage 2/.test(r.name)).trailhead"), abs=1e-4)


START, CROSSING_A, CROSSING_B = (45.0600, 6.5250), (45.0590, 6.5240), (45.0610, 6.5260)


def drawn_hike(name: str, *points: tuple[float, float]) -> dict:
    lat, lon = zip(*points)
    return {"id": zlib.crc32(name.encode()), "name": name, "walk_m": 1000, "line": encode_polyline(np.array(lat), np.array(lon))}


def map_dots(browser, server: str, hikes: list[dict]):
    """The page's hike map at zoom 15 over START, with the given hikes in view (the /api/hikes read is stubbed)."""
    page = open_page(browser, server, Api({}))
    page.route(f"{server}/api/hikes?*", lambda route: route.fulfill(json={"results": hikes, "too_many": False}))
    page.locator("#map-pick-on").check()
    page.evaluate("start => pick.map.setView(start, 15)", START)
    page.wait_for_function("() => pick.layer.getLayers().length > 0")
    return page


DOTS = """() => {
  const dots = [];
  const walk = (layer) => layer instanceof L.CircleMarker ? dots.push(layer) : layer.eachLayer?.(walk);
  pick.layer.eachLayer(walk);
  const z = (name) => Number(getComputedStyle(pick.map.getPane(name)).zIndex);
  return dots.map((dot) => ({ fill: dot.options.fillColor, above: z(dot.options.pane) > z("overlayPane") }));
}"""
START_FILL, END_FILL = "#2f7d4f", "#1f2a24"


def test_the_start_dots_of_the_hikes_on_the_map_are_drawn_above_every_line(browser, server) -> None:
    # A line crossing the start of a loop is drawn after it, as the 22 lines across Laval's car park are.
    loop = drawn_hike("Loop", START, CROSSING_A, CROSSING_B, START)
    crossing = drawn_hike("Crossing", CROSSING_A, START, CROSSING_B)
    page = map_dots(browser, server, [loop, crossing])

    starts = [dot for dot in page.evaluate(DOTS) if dot["fill"] == START_FILL]

    assert len(starts) == 2
    assert all(dot["above"] for dot in starts)


# Two stages of one trail meeting at START: stage 1 comes from the west, stage 2 goes on east.
WEST, EAST = (45.0600, 6.5200), (45.0600, 6.5300)
def click_map(page, at: tuple[float, float], east_px: int) -> list[int]:
    """Clicks the hike map ``east_px`` pixels east of ``at``: the ids the page chose (``choose`` is spied on)."""
    page.evaluate("() => { window.chosen = []; window.choose = (id) => { window.chosen.push(id); }; }")
    point = page.evaluate("([lat, lon]) => pick.map.latLngToContainerPoint([lat, lon])", list(at))
    box = page.locator("#map-pick .pick-map").bounding_box()
    page.mouse.click(box["x"] + point["x"] + east_px, box["y"] + point["y"])
    return page.evaluate("window.chosen")



@pytest.mark.parametrize("drawn_first", ["stage 1", "stage 2"])
def test_a_click_on_a_line_where_two_hikes_meet_chooses_the_line_clicked(browser, server, drawn_first) -> None:
    one, two = drawn_hike("Stage 1", WEST, START), drawn_hike("Stage 2", START, EAST)
    page = map_dots(browser, server, [one, two] if drawn_first == "stage 1" else [two, one])

    assert click_map(page, START, -8) == [one["id"]]  # 8 px west of where they meet: on stage 1
    assert click_map(page, START, 8) == [two["id"]]
    assert click_map(page, START, 0) == [two["id"]]  # on the shared dot: the hike that starts there
    assert click_map(page, (45.0620, 6.5250), 0) == []  # off every line: nothing


def test_the_hike_under_the_pointer_is_the_one_a_click_would_choose(browser, server) -> None:
    one, two = drawn_hike("Stage 1", WEST, START), drawn_hike("Stage 2", START, EAST)
    page = map_dots(browser, server, [one, two])

    at = page.evaluate("([lat, lon]) => pick.map.latLngToContainerPoint([lat, lon]).add([-8, 0])", START)
    box = page.locator("#map-pick .pick-map").bounding_box()
    page.mouse.move(box["x"] + at["x"], box["y"] + at["y"])

    page.locator(".leaflet-tooltip", has_text="Stage 1").wait_for()
    assert page.evaluate("pick.hovered") == one["id"]


def test_a_hike_that_ends_where_it_starts_draws_its_start_dot_and_no_end_dot_over_it(browser, server) -> None:
    loop = drawn_hike("Loop", START, CROSSING_A, CROSSING_B, START)
    line = drawn_hike("Line", CROSSING_A, START, CROSSING_B)
    page = map_dots(browser, server, [loop, line])

    fills = [dot["fill"] for dot in page.evaluate(DOTS)]

    assert sorted(fills) == sorted([START_FILL, START_FILL, END_FILL])  # the loop's end dot is the one left out


GPX = "{http://www.topografix.com/GPX/1/1}"


def downloaded_gpx(page, card: str):
    """The GPX file the card's Download GPX button saves: its suggested name and its parsed root."""
    with page.expect_download() as caught:
        page.locator(card).get_by_role("button", name="Download GPX").click()
    return caught.value.suggested_filename, ET.parse(caught.value.path()).getroot()


def gpx_tracks(gpx) -> list[tuple[str, list[tuple[float, float]], list[float]]]:
    """Name, points and heights of each track of a GPX file."""
    return [
        (
            track.findtext(f"{GPX}name"),
            [(float(p.get("lat")), float(p.get("lon"))) for p in track.iter(f"{GPX}trkpt")],
            [float(p.findtext(f"{GPX}ele")) for p in track.iter(f"{GPX}trkpt")],
        )
        for track in gpx.iter(f"{GPX}trk")
    ]


def test_a_liked_hike_downloads_as_a_gpx_track_that_credits_openstreetmap_and_links_back(browser, server) -> None:
    with urllib.request.urlopen(f"{server}/api/hikes/{RIDGE_WALK_ID}?return_to_start=false") as response:
        hike = json.load(response)
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}&oneway")
    page.get_by_text("Chosen: Ridge Walk").wait_for()

    name, gpx = downloaded_gpx(page, "#liked")

    assert name == "ridge-walk.gpx"
    assert gpx.tag == f"{GPX}gpx" and gpx.get("version") == "1.1"
    meta = gpx.find(f"{GPX}metadata")
    assert meta.findtext(f"{GPX}name") == "Ridge Walk"
    assert meta.findtext(f"{GPX}desc").startswith("Route data © OpenStreetMap contributors, ODbL 1.0. Heights: FABDEM V1-2")
    assert "CC BY-NC-SA 4.0" in meta.findtext(f"{GPX}desc")  # the heights come from FABDEM, so its licence goes with them
    assert meta.find(f"{GPX}copyright").get("author") == "OpenStreetMap contributors"
    assert meta.findtext(f"{GPX}copyright/{GPX}license") == "https://opendatacommons.org/licenses/odbl/"
    assert meta.find(f"{GPX}link").get("href") == f"{server}/#hike={RIDGE_WALK_ID}&oneway"
    # One way: the hike alone, a height at every point, from its profile end to end.
    ((track, points, heights),) = gpx_tracks(gpx)
    assert track == "Ridge Walk"
    assert points == pytest.approx(decode_polyline(hike["line"]), abs=1e-9) and len(points) > 1
    assert heights[0] == hike["elevation"]["metres"][0] and heights[-1] == hike["elevation"]["metres"][-1]
    assert min(heights) >= min(hike["elevation"]["metres"]) and max(heights) <= max(hike["elevation"]["metres"])


def test_a_hike_measured_with_its_way_back_downloads_the_way_back_as_a_second_track(browser, server) -> None:
    with urllib.request.urlopen(f"{server}/api/hikes/{RIDGE_WALK_ID}") as response:
        hike = json.load(response)
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()

    _, gpx = downloaded_gpx(page, "#liked")

    (name, there, up), (back_name, back, down) = gpx_tracks(gpx)
    assert name == "Ridge Walk"
    assert back_name.startswith("Ridge Walk: way back to the start (")
    assert back[0] == there[-1] and back[-1] == there[0] and down[0] == up[-1] and down[-1] == up[0]
    walked = sum(metres_between(a, b) for a, b in itertools.pairwise(there + back[1:]))
    assert walked == pytest.approx(hike["facts"]["length_m"], rel=0.05)  # the length the page says


def metres_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def test_a_catalog_hike_and_a_hike_found_similar_each_link_to_themselves(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()

    assert page.locator("#liked").get_by_role("link", name="Link to this hike").get_attribute("href") == f"#hike={RIDGE_WALK_ID}"
    page.get_by_role("button", name="Find similar").click()
    page.locator("#results-table tbody tr").first.click()
    link = page.locator("#selected").get_by_role("link", name="Link to this hike")
    shared = open_page(browser, server, Api({}), hash=link.get_attribute("href"))
    shared.get_by_text(f"Chosen: {page.locator('#selected h2').text_content()}").wait_for()


SHARED = "window.shared = []; navigator.share = async (data) => { window.shared.push(data); };"
COPIED = """window.copied = []; delete Navigator.prototype.share; delete navigator.share;
Object.defineProperty(navigator, "clipboard", { value: { writeText: async (text) => { window.copied.push(text); } } });"""


def test_share_hands_the_hike_address_to_the_phone_share_sheet(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}", init=SHARED, touch=True)
    page.get_by_text("Chosen: Ridge Walk").wait_for()

    page.locator("#liked").get_by_role("button", name="Share").click()

    page.wait_for_function("() => window.shared.length")
    assert page.evaluate("window.shared") == [{"title": "Ridge Walk", "url": f"{server}/#hike={RIDGE_WALK_ID}"}]


def test_share_copies_the_hike_address_where_there_is_no_share_sheet(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}&oneway", init=COPIED)
    page.get_by_text("Chosen: Ridge Walk").wait_for()

    page.locator("#liked").get_by_role("button", name="Share").click()

    page.locator("#liked").get_by_role("button", name="Link copied").wait_for()
    assert page.evaluate("window.copied") == [f"{server}/#hike={RIDGE_WALK_ID}&oneway"]


@pytest.fixture(scope="module")
def loops_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """The app on the TrailGraph network where a Ranking from the Meadow Path suggests two loops (ADR-0020)."""
    data = loops_export(tmp_path_factory.mktemp("loops-page"))
    yield from serve(create_app(data, lambda max_hours: Matrix(), rank_rate=1_000))


def test_a_suggested_loop_shared_opens_the_same_route_for_a_friend(browser, loops_server, monkeypatch) -> None:
    monkeypatch.setattr("dayhike.serving.GROUP_M", 0.0)  # every hike in its own row, the loops too
    page = open_page(browser, loops_server, Api({}), hash=f"#hike={MEADOW_PATH}", init=SHARED, touch=True)
    page.get_by_text("Chosen: Meadow Path").wait_for()
    page.get_by_role("button", name="Find similar").click()
    page.locator("#results-table tbody tr").first.wait_for()
    page.get_by_role("button", name="Suggest loops near these hikes (takes a few seconds)").click()
    page.locator("#results-table tbody tr", has=page.locator(".composed-tag")).first.click()
    page.locator("#selected").get_by_role("button", name="Share").click()
    page.wait_for_function("() => window.shared.length")
    (shared,) = page.evaluate("window.shared")
    _, gpx = downloaded_gpx(page, "#selected")

    assert shared["url"].startswith(f"{loops_server}/#compose=")
    assert gpx.find(f"{GPX}metadata/{GPX}link").get("href") == shared["url"]
    friend = open_page(browser, loops_server, Api({}), hash=shared["url"].removeprefix(f"{loops_server}/"))
    friend.get_by_text(f"Chosen: {shared['title']}").wait_for()
    _, opened = downloaded_gpx(friend, "#liked")
    assert [p.attrib for p in opened.iter(f"{GPX}trkpt")] == [p.attrib for p in gpx.iter(f"{GPX}trkpt")]
    assert len(list(gpx.iter(f"{GPX}trkpt"))) > 1


def test_share_and_download_fit_a_phone(browser, server) -> None:
    page = open_page(browser, server, Api({}), width=390, hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()

    assert page.locator("#liked").get_by_role("button", name="Share").is_visible()
    assert page.locator("#liked").get_by_role("button", name="Download GPX").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= 390


# ---------- what the visitor test of rebuild 3 found ----------


def test_a_find_that_cannot_reach_the_server_says_so_beside_the_list_it_leaves(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)
    page.route(f"{server}/api/rank", lambda route: route.abort("connectionrefused"))

    page.get_by_role("button", name="Find similar").click()

    stale = page.locator("#results-stale")
    stale.get_by_text("This search failed: the server could not be reached.").wait_for()
    assert "The list below is from before; press Find similar to try again." in stale.inner_text()
    assert "error" in stale.get_attribute("class")
    assert "Failed to fetch" not in page.locator("body").inner_text()


def test_a_find_the_server_never_answers_ends_with_a_message(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.clock.install()
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.route(f"{server}/api/rank", lambda route: None)  # never answered

    page.get_by_role("button", name="Find similar").click()
    page.get_by_text("Ranking every hike…").wait_for()
    page.clock.fast_forward("01:01")

    page.get_by_text("The server did not answer within 60 s; try again in a minute.").wait_for()
    assert page.get_by_role("button", name="Find similar").is_enabled()


def test_the_list_names_the_starting_place_and_limit_it_keeps_for_another_hike(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.get_by_label("Only hikes near a starting place").check()
    page.get_by_label("From", exact=True).fill("agram")
    page.get_by_role("button", name="Find place").click()
    page.get_by_text("Start: Zagreb, HR (45.8144, 15.9780)").wait_for()
    _find(page)

    assert "within a 2 h drive of Zagreb, HR" in page.locator("#results-note").inner_text()

    # Another hike's link in the same tab keeps the limit: the list says so.
    other = page.evaluate("state.results[0].id")
    page.evaluate("id => { location.hash = `#hike=${id}`; }", other)
    page.wait_for_function("id => state.liked?.id === id", arg=other)
    _find(page)
    assert "within a 2 h drive of Zagreb, HR" in page.locator("#results-note").inner_text()


def test_a_reloaded_page_restores_no_starting_place_or_limit(browser, server) -> None:
    page = open_page(browser, server, Api({}))
    page.get_by_label("Only hikes near a starting place").check()
    page.get_by_label("From", exact=True).fill("agram")
    page.get_by_label("Max drive hours value").fill("3.5")

    page.reload()
    page.wait_for_selector(".weight", state="attached")

    assert not page.get_by_label("Only hikes near a starting place").is_checked()
    assert page.get_by_label("From", exact=True).input_value() == ""
    assert page.get_by_label("Max drive hours value").input_value() == "2"


@pytest.mark.parametrize("move", ["search", "map"])
def test_the_broken_link_message_goes_once_the_hiker_looks_for_another_hike(browser, server, move) -> None:
    page = open_page(browser, server, Api({}), hash="#hike=999999999")
    message = page.get_by_text("This link could not be opened: it doesn't match the current trail data, so it shows no hike.")
    message.wait_for()
    assert page.locator("#liked-name").inner_text() == "This link could not be opened; no hike chosen yet."

    if move == "search":
        page.locator("#search").fill("Ridge")
    else:
        page.locator("#map-pick-on").check()

    message.wait_for(state="detached")
    assert page.get_by_text("Search for a hike by name, or select one on the map.").is_visible()
    assert page.locator("#liked-name").inner_text() == "No hike chosen yet."


def test_the_picked_mark_and_a_greyed_redraw_say_what_they_mean_beside_the_list(browser, server, monkeypatch) -> None:
    monkeypatch.setattr(web, "RESULTS", 2)
    pool = Pool([0.5, 0.5, 0.5])
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}", draw=11)
    page.route(f"{server}/api/rank", pool)
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)
    picked = any(i >= 2 for i in pool.drawn(11, 2))
    explained = page.locator("#results-draw-note")

    assert explained.is_visible() == picked
    if picked:
        assert explained.inner_text() == (
            "picked: not one of this page's 2 best areas, drawn in for variety from its best 30. Strict order lists the 2 best in order."
        )

    page.get_by_label("Strict order").check()

    assert explained.inner_text() == "Redraw is off while Strict order is ticked: the list is the exact ranking."
    assert page.get_by_role("button", name="Redraw").get_attribute("title").startswith("Off in Strict order")


SHARE_REFUSED = COPIED.replace(
    "delete Navigator.prototype.share; delete navigator.share;",
    'navigator.share = async () => { throw new DOMException("No share sheet here", "NotAllowedError"); };',
)


def test_share_copies_the_link_when_the_share_sheet_fails(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}", init=SHARE_REFUSED, touch=True)
    page.get_by_text("Chosen: Ridge Walk").wait_for()

    page.locator("#liked").get_by_role("button", name="Share").click()

    page.locator("#liked").get_by_role("button", name="Link copied").wait_for()
    assert page.evaluate("window.copied") == [f"{server}/#hike={RIDGE_WALK_ID}"]


def test_one_way_and_round_trip_climbs_are_labelled(browser, server) -> None:
    page = open_page(browser, server, Api({}))
    page.locator("#search").fill("Ridge Walk")
    meta = page.locator("#suggestions li", has_text="Ridge Walk").first.locator(".meta").inner_text()
    assert re.search(r"km one way, ↑ [\d,]+ m ↓ [\d,]+ m", meta), meta

    page.locator("#suggestions li", has_text="Ridge Walk").first.dispatch_event("mousedown")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    badges = page.locator("#liked .badges li").all_inner_texts()
    assert any(re.fullmatch(r"↑ [\d,]+ m ↓ [\d,]+ m with the way back", b) for b in badges), badges

    page.locator("#return-on").uncheck()
    page.wait_for_function("() => state.liked && state.liked.way_back == null")
    badges = page.locator("#liked .badges li").all_inner_texts()
    assert any(re.fullmatch(r"↑ [\d,]+ m ↓ [\d,]+ m one way", b) for b in badges), badges


def test_a_loop_s_climb_is_not_labelled_one_way(browser, server) -> None:
    def as_loop(route) -> None:
        body = route.fetch().json()
        body["facts"]["round_trip"] = 1
        route.fulfill(json=body)

    page = browser.new_page()
    page.set_default_timeout(5000)
    page.route(lambda url: not url.startswith(server), lambda route: route.abort())
    page.route(f"{server}/api/hikes/{RIDGE_WALK_ID}?*", as_loop)
    page.goto(f"{server}/#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()

    page.locator("#return-on").uncheck()
    page.wait_for_function("() => state.liked && state.liked.way_back == null")

    badges = page.locator("#liked .badges li").all_inner_texts()
    assert any(re.fullmatch(r"↑ [\d,]+ m ↓ [\d,]+ m", b) for b in badges), badges
    assert "round trip" in badges


def test_search_by_name_works_without_abort_signal_any(browser, server) -> None:
    page = open_page(browser, server, Api({}), init="delete AbortSignal.any;")

    page.locator("#search").fill("Ridge Walk")

    page.locator("#suggestions li", has_text="Ridge Walk").first.wait_for()


def test_a_loop_edited_on_the_map_shows_the_climb_its_card_shows(browser, loops_server, monkeypatch) -> None:
    monkeypatch.setattr("dayhike.serving.GROUP_M", 0.0)
    page = open_page(browser, loops_server, Api({}), hash=f"#hike={MEADOW_PATH}")
    page.get_by_text("Chosen: Meadow Path").wait_for()
    _find(page)
    page.get_by_role("button", name="Suggest loops near these hikes (takes a few seconds)").click()
    page.locator("#results-table tbody tr", has=page.locator(".composed-tag")).first.click()
    page.locator("#selected").get_by_role("button", name="Edit on the map").click()
    page.wait_for_function("() => compose.on && compose.source?.route")

    readout = page.locator("#compose-readout")
    measured = page.evaluate("upDown(compose.source.climb_m, compose.source.descent_m)")
    readout.get_by_text(measured).wait_for()
    assert "as measured" in readout.inner_text() and "about" not in readout.inner_text()


# ---------- what the second visitor test of rebuild 4 found ----------


def test_the_grouping_radius_takes_its_own_defaults_and_tenths_as_valid(browser, server) -> None:
    page = open_page(browser, server, Api({}))
    page.get_by_text("More options", exact=True).click()
    box = page.get_by_label("Group hikes starting within (km)")

    for km in ("0.1", "0.5", "1", "1.5", "2.5", "5", "10", "20", "50"):
        box.fill(km)
        assert box.evaluate("box => box.validity.valid"), km


def test_a_wish_value_corrected_after_find_said_it_is_out_of_range_is_valid_again(browser, server) -> None:
    api = Api(WISHABLE)
    page = open_page(browser, server, api)
    page.get_by_role("button", name="No particular hike in mind?").click()
    gain = page.get_by_label("Climbing you want, in m")

    gain.fill("9000")
    page.get_by_role("button", name="Find hikes").click()
    page.get_by_text("Climbing: you want a value from 0 to 3000 m.").wait_for()
    assert not gain.evaluate("box => box.validity.valid")

    gain.fill("900")
    assert gain.evaluate("box => box.validity.valid")
    page.get_by_role("button", name="Find hikes").click()
    page.get_by_text("Done in").wait_for()
    assert api.ranked[-1]["wish"]["elevation_gain"] == 900
    assert gain.evaluate("box => box.validity.valid")


def test_a_place_found_after_the_hiker_typed_another_is_not_the_start(browser, server) -> None:
    api = Api({})
    page = open_page(browser, server, api, hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.get_by_label("Only hikes near a starting place").check()
    held = []
    page.route(f"{server}/api/place?*", lambda route: held.append(route))  # answered only once the Hiker typed another place
    page.get_by_label("From", exact=True).fill("agram")
    page.get_by_role("button", name="Find place").click()
    page.wait_for_function("() => document.querySelector('#origin-found').textContent === 'Searching…'")
    assert len(held) == 1

    page.get_by_label("From", exact=True).fill("Nevache")
    held[0].continue_()
    page.wait_for_timeout(300)

    assert page.locator("#origin-found").inner_text() == ""
    page.get_by_role("button", name="Find similar").click()
    page.get_by_text("Set a starting place.").wait_for()
    assert api.ranked == []


def test_find_place_with_an_empty_box_says_what_to_type(browser, server) -> None:
    page = open_page(browser, server, Api({}))
    page.get_by_label("Only hikes near a starting place").check()

    page.get_by_role("button", name="Find place").click()

    page.get_by_text("Type a town name or lat, lon first.").wait_for()


def test_a_starting_place_set_after_a_find_says_the_list_is_from_before(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.context.grant_permissions(["geolocation"])
    page.context.set_geolocation({"latitude": 45.2, "longitude": 15.2})
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.get_by_label("Only hikes near a starting place").check()
    page.get_by_label("From", exact=True).fill("45.1, 15.1")
    _find(page)
    assert not page.locator("#results-stale").is_visible()

    page.get_by_role("button", name="Use my location").click()  # sets the box with no input event

    page.get_by_text("Start: your location (45.2000, 15.2000)").wait_for()
    assert page.locator("#results-stale").is_visible()


def test_a_find_the_page_refuses_says_so_beside_the_list_it_leaves(browser, server) -> None:
    api = Api({})
    page = open_page(browser, server, api, hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)
    asked = len(api.ranked)
    for row in page.locator(".weight:visible input[type=range]").all():
        row.fill("0")

    page.get_by_role("button", name="Find similar").click()

    stale = page.locator("#results-stale")
    stale.get_by_text("This search was not run: Give at least one measure a weight above 0.").wait_for()
    assert "The list below is from before." in stale.inner_text()
    assert "error" in stale.get_attribute("class")
    assert "stale" in page.locator("#results").get_attribute("class")
    assert len(api.ranked) == asked


def test_each_suggested_hike_is_in_the_accessibility_tree_and_picks_itself(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.locator("#pair-search").fill("al")
    options = page.locator("#pair-suggestions li[role=option]")  # not the note above names only like the query
    options.nth(1).wait_for()

    # Focusable, as the snapshots of DevTools-based tools need to list an option at all.
    names = options.evaluate_all("lis => lis.map(li => li.firstChild.textContent)")
    tree = page.context.new_cdp_session(page).send("Accessibility.getFullAXTree")["nodes"]
    listed = [
        n for n in tree
        if n.get("role", {}).get("value") == "option" and any(n.get("name", {}).get("value", "").startswith(name) for name in names)
    ]
    focusable = [p["value"]["value"] for n in listed for p in n.get("properties", []) if p["name"] == "focusable"]
    assert len(focusable) == len(names) and all(focusable)

    second = options.nth(1).evaluate("li => li.firstChild.textContent.trim()")  # before a similar-name tag
    page.locator("#pair-suggestions").get_by_role("option").nth(1).click()
    page.locator("#selected").get_by_text(second).first.wait_for()
    assert page.locator("#pair-search").input_value() == second


def test_the_designer_says_a_route_ending_a_few_metres_from_its_start_is_back_there(browser, server) -> None:
    page = open_page(browser, server, Api({}))
    ending = """(metres) => {
      compose.junctions = new Map([[1, { latlng: [46, 14] }], [2, { latlng: [46.01, 14] }], [3, { latlng: [46 + metres / 111195, 14] }]]);
      compose.segments = new Map([
        [10, { id: 10, a: 1, b: 2, length: 1200, climbAB: 50, climbBA: 0 }],
        [11, { id: 11, a: 2, b: 3, length: 1150, climbAB: 0, climbBA: 50 }],
      ]);
      compose.start = 1;
      compose.route = [{ id: 10, step: "a" }, { id: 11, step: "b" }];
      compose.readout();
      return document.querySelector("#compose-readout").textContent;
    }"""

    near = page.evaluate(ending, 30)
    assert "one way" not in near and "ends 30 m from the start" in near
    assert "the way back to the start is added" not in near
    far = page.evaluate(ending, 80)
    assert "2.4 km one way" in far and "ends" not in far


def _edit_a_loop(page) -> int:
    """Finds loops for Meadow Path, edits the first on the map: the segments it opens with."""
    page.get_by_text("Chosen: Meadow Path").wait_for()
    _find(page)
    page.get_by_role("button", name="Suggest loops near these hikes (takes a few seconds)").click()
    page.locator("#results-table tbody tr", has=page.locator(".composed-tag")).first.click()
    page.locator("#selected").get_by_role("button", name="Edit on the map").click()
    page.wait_for_function("() => compose.on && compose.source?.route")
    # Its segments loaded (the hint says what to click next): a load still due would replace a later hint.
    page.locator("#map-pick-hint").get_by_text("Click a segment to walk on it").wait_for()
    return page.evaluate("compose.route.length")


def test_an_edited_loop_keeps_its_segments_from_undo_and_the_liked_hike_until_used(browser, loops_server, monkeypatch) -> None:
    monkeypatch.setattr("dayhike.serving.GROUP_M", 0.0)
    page = open_page(browser, loops_server, Api({}), hash=f"#hike={MEADOW_PATH}")
    drawn = _edit_a_loop(page)
    assert drawn > 1

    # Undo takes back only the Hiker's own clicks: none yet (ADR-0054).
    undo = page.get_by_role("button", name="Undo")
    assert undo.is_disabled()
    assert page.evaluate("compose.undo(), compose.route.length") == drawn
    assert "the segments of the hike you opened stay" in page.locator("#map-pick-hint").inner_text()
    assert page.evaluate("state.liked.name") == "Meadow Path" and page.locator("#results").is_visible()

    page.get_by_role("button", name="Use this hike").click()

    page.get_by_text(f"Your hike of {drawn} segment").wait_for()
    assert page.evaluate("state.liked.route.text === compose.text()")


def test_clear_on_the_liked_composed_hike_clears_it_and_on_an_edited_one_keeps_the_liked(browser, loops_server, monkeypatch) -> None:
    monkeypatch.setattr("dayhike.serving.GROUP_M", 0.0)
    page = open_page(browser, loops_server, Api({}), hash=f"#hike={MEADOW_PATH}")
    _edit_a_loop(page)

    page.get_by_role("button", name="Clear").click()  # an edited loop: the liked hike stays
    assert page.evaluate("state.liked.name") == "Meadow Path"
    page.locator("#compose-readout").get_by_text("No hike drawn yet.").wait_for()

    _edit_a_loop(page)
    page.get_by_role("button", name="Use this hike").click()
    page.wait_for_function("() => state.liked?.route")
    page.get_by_role("button", name="Clear").click()  # the liked hike is the drawing: it goes too

    assert page.evaluate("state.liked") is None
    assert page.get_by_role("button", name="Find similar").is_disabled()
    assert page.locator("#liked-name").inner_text() == "No hike chosen yet."
    assert page.evaluate("location.hash") == ""


# ---------- the wishes of the second visitor test ----------


def test_the_filters_of_a_list_show_above_it_and_each_clears_with_its_x(browser, server) -> None:
    api = Api({})
    page = open_page(browser, server, api, hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.get_by_label("Only hikes near a starting place").check()
    page.get_by_label("From", exact=True).fill("45.2, 15.1")
    page.get_by_label("Max drive hours", exact=True).uncheck()
    page.get_by_label("Max km, straight line", exact=True).check()
    page.get_by_label("Max km value").fill("50")
    page.locator("#more-options summary").click()
    page.locator("#group-km").fill("3")
    page.get_by_role("button", name="Find similar").click()
    page.locator("#results-table tbody tr").first.wait_for()

    chips = page.locator("#results-filters li")
    assert chips.all_inner_texts() == ["Within 50 km of 45.2, 15.1\n×", "Grouped within 3 km\n×"]

    asked = len(api.ranked)
    page.get_by_role("button", name="Clear Within 50 km of 45.2, 15.1").click()
    page.wait_for_function("() => document.querySelectorAll('#results-filters li').length === 1")
    assert len(api.ranked) == asked + 1 and "origin" not in api.ranked[-1] and api.ranked[-1]["group_km"] == 3
    assert not page.get_by_label("Only hikes near a starting place").is_checked()

    page.get_by_role("button", name="Clear Grouped within 3 km").click()
    page.wait_for_function("() => document.querySelector('#results-filters').hidden")
    assert "group_km" not in api.ranked[-1] and page.locator("#group-km").input_value() == ""


def test_a_one_way_list_says_so_above_it_and_its_x_measures_the_way_back_again(browser, server) -> None:
    api = Api({})
    page = open_page(browser, server, api, hash=f"#hike={RIDGE_WALK_ID}&oneway")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.get_by_role("button", name="Find similar").click()
    page.locator("#results-table tbody tr").first.wait_for()

    page.get_by_role("button", name="Clear One way, without the way back").click()

    page.wait_for_function("() => document.querySelector('#results-filters').hidden")
    assert page.locator("#return-on").is_checked() and api.ranked[-1]["return_to_start"] is True


def test_a_name_typed_with_a_typo_lists_similar_names_marked_so(browser, server) -> None:
    page = open_page(browser, server, Api({}))

    page.locator("#search").fill("Valey Path")

    page.get_by_text("“Valey Path” as typed. Names like it:").wait_for()
    option = page.get_by_role("option", name="Valley Path")
    assert "similar name" in option.inner_text()
    page.locator("#search").press("ArrowDown")
    page.locator("#search").press("Enter")
    page.get_by_text("Chosen: Valley Path").wait_for()


def test_a_value_typed_in_the_wish_form_switches_its_weight_on_and_a_weight_asks_the_shown_default(
    browser, server
) -> None:
    api = Api(WISHABLE)
    page = open_page(browser, server, api)
    page.get_by_role("button", name="No particular hike in mind?").click()

    page.get_by_label("Climbing you want, in m").fill("900")
    page.get_by_label("Highest point weight, 0 to 100").fill("50")

    assert page.get_by_label("Climbing weight, 0 to 100").input_value() == "100"
    assert page.get_by_label("Highest point you want, in m").input_value() == "1500"
    page.get_by_role("button", name="Find hikes").click()
    page.locator("#results").wait_for()
    assert api.ranked[-1]["wish"] == {"elevation_gain": 900, "highest": 1500}
    assert api.ranked[-1]["weights"] == {"elevation_gain": 100, "highest": 50}


def test_a_wish_weight_brought_back_after_a_reset_asks_the_shown_default(browser, server) -> None:
    api = Api(WISHABLE)
    page = open_page(browser, server, api)
    page.get_by_role("button", name="No particular hike in mind?").click()
    page.get_by_label("Climbing weight, 0 to 100").fill("50")
    page.get_by_role("button", name="Back to a hike you liked").click()
    page.get_by_role("button", name="Reset weights").click()

    page.get_by_role("button", name="No particular hike in mind?").click()

    assert page.get_by_label("Climbing you want, in m").input_value() == "600"
    page.get_by_role("button", name="Find hikes").click()
    page.locator("#results").wait_for()
    assert api.ranked[-1]["wish"] == {"elevation_gain": 600}
    assert api.ranked[-1]["weights"] == {"elevation_gain": 50}


def test_going_back_to_a_liked_hike_leaves_the_wish_values_empty(browser, server) -> None:
    page = open_page(browser, server, Api(WISHABLE))
    page.get_by_label("Length weight, 0 to 100").fill("70")
    page.get_by_role("button", name="No particular hike in mind?").click()
    page.get_by_role("button", name="Back to a hike you liked").click()

    page.get_by_role("button", name="No particular hike in mind?").click()

    assert set(page.locator("input.wish[type=number]").evaluate_all("inputs => inputs.map(i => i.value)")) == {""}


def test_a_wish_address_with_a_weight_and_no_value_asks_the_shown_default(browser, server) -> None:
    page = open_page(browser, server, Api(WISHABLE), hash="#wish=elevation_gain::100")

    assert page.get_by_label("Climbing weight, 0 to 100").input_value() == "100"
    assert page.get_by_label("Climbing you want, in m").input_value() == "600"


def test_share_says_beside_the_button_that_the_link_was_copied(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}", init=COPIED + SHARED)
    page.get_by_text("Chosen: Ridge Walk").wait_for()

    page.locator("#liked").get_by_role("button", name="Share").click()

    # A computer's share sheet may never answer: there the link is copied, and the page says so.
    page.locator("#liked .share-note").get_by_text("Link copied").wait_for()
    assert page.evaluate("window.shared") == [] and page.evaluate("window.copied") == [f"{server}/#hike={RIDGE_WALK_ID}"]


def test_share_shows_the_link_to_copy_where_it_cannot_be_copied(browser, server) -> None:
    refused = 'Object.defineProperty(navigator, "clipboard", { value: { writeText: async () => { throw new Error("no"); } } });'
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}", init=refused)
    page.get_by_text("Chosen: Ridge Walk").wait_for()

    page.locator("#liked").get_by_role("button", name="Share").click()

    assert page.get_by_label("Link to share").input_value() == f"{server}/#hike={RIDGE_WALK_ID}"


TAPPED = """() => [...document.querySelectorAll(
  'button, select, input:not([type=checkbox]):not([type=range]), summary, label.check, .leaflet-bar a')]
  .filter((e) => e.offsetParent && !e.matches('button.help'))
  .map((e) => [e.id || e.className || e.textContent.trim(), e.getBoundingClientRect().height])
  .filter(([, height]) => height < 44)"""


def test_every_control_on_a_phone_takes_a_tap_44_px_across(browser, server) -> None:
    page = open_page(browser, server, Api({}), width=390, hash=f"#hike={RIDGE_WALK_ID}", touch=True)
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.get_by_label("Select hike on map").check()
    page.locator(".pick-map .leaflet-control-zoom-in").wait_for()
    page.get_by_role("button", name="Find similar").click()
    page.locator("#results-table tbody tr").first.wait_for()

    assert page.evaluate(TAPPED) == []
    zoom = page.locator(".pick-map .leaflet-control-zoom-in").bounding_box()
    assert zoom["width"] >= 44 and zoom["height"] >= 44
    # The "?" beside a score stays small but takes a tap 44 px across.
    assert page.locator("button.help").first.evaluate("b => getComputedStyle(b, '::after').inset") == "-12px"
    assert page.evaluate("document.documentElement.scrollWidth") <= 390


def test_a_name_search_still_pending_when_the_hiker_leaves_the_box_does_not_open_the_list(browser, server) -> None:
    page = open_page(browser, server, Api({}))
    searched = []
    page.on("request", lambda request: searched.append(request.url) if "/api/search" in request.url else None)

    page.locator("#search").fill("Ridge")
    page.locator("#search").blur()  # before the search goes out
    page.wait_for_timeout(800)

    assert page.locator("#suggestions").is_hidden()
    assert page.locator("#search").get_attribute("aria-expanded") == "false"
    assert not searched


def test_a_new_suggestion_list_drops_the_option_the_keys_chose_before(browser, server) -> None:
    page = open_page(browser, server, Api({}))
    search = page.locator("#search")
    search.fill("Ridge")
    page.locator("#suggestions li[role=option]").first.wait_for()
    search.press("ArrowDown")
    assert search.get_attribute("aria-activedescendant") == "suggestions-0"
    assert search.get_attribute("aria-expanded") == "true"

    search.fill("Ridge W")
    page.wait_for_function("document.querySelector('#search').getAttribute('aria-activedescendant') === null")
    page.locator("#suggestions li[role=option]").first.wait_for()
    assert search.get_attribute("aria-activedescendant") is None  # no option is chosen in the new list


def test_a_result_that_loads_after_a_later_compared_hike_does_not_replace_its_card(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)
    held = []
    page.route(f"{server}/api/hikes/*", lambda route: held.append(route) if not held else route.continue_())

    page.locator("#results-table tbody tr").first.click()  # its hike is held back
    while not held:
        page.wait_for_timeout(50)
    page.locator("#pair-search").fill("Valley Path")
    page.locator("#pair-suggestions li", has_text="Valley Path").first.dispatch_event("mousedown")
    page.locator("#selected").get_by_text("Compared with the liked hike").wait_for()
    held[0].continue_()
    page.wait_for_timeout(500)

    assert page.locator("#selected").get_by_text("Compared with the liked hike").is_visible()
    assert page.locator("#selected h2, #selected h3").first.inner_text().startswith("Valley Path")


SHOWS = "([lat, lon]) => !!pick.map && pick.map.getZoom() >= PICK_ZOOM && pick.map.getBounds().contains([lat, lon])"


def test_search_lists_the_hikes_near_a_peak_apart_from_hike_names(browser, server) -> None:
    page = open_page(browser, server, Api({}))
    assert "peak or place" in page.locator("#search").get_attribute("placeholder")

    page.locator("#search").fill("Ridge Peak")

    head = page.locator("#suggestions li.landmark").first
    head.wait_for()
    assert head.inner_text().startswith("Hikes near Ridge Peak (peak)")
    hike = page.locator("#suggestions li.landmark + li[role=option]").first
    assert hike.locator(".meta").inner_text().startswith("reaches the top · ")
    hike.dispatch_event("mousedown")
    page.get_by_text("Chosen: Ridge Walk").wait_for()


def test_show_on_map_opens_the_hike_map_at_the_landmark(browser, server) -> None:
    page = open_page(browser, server, Api({}))
    page.locator("#search").fill("Ridge Lake")

    page.locator("#suggestions li.landmark").get_by_role("button", name="Show on map").dispatch_event("mousedown")

    assert page.locator("#map-pick-on").is_checked()
    page.wait_for_function(SHOWS, arg=[45.05, 15.01])
    page.get_by_text("Click a line to choose it").wait_for()


def test_a_landmark_or_start_place_found_moves_the_open_hike_map(browser, server) -> None:
    page = open_page(browser, server, Api({}))
    page.locator("#map-pick-on").check()

    page.locator("#search").fill("Vrh")
    page.wait_for_function(SHOWS, arg=[45.21, 15.11])

    page.locator("#search").fill("")
    page.locator("#drive-on").check()
    page.locator("#origin").fill("Ogulin")
    page.locator("#origin-find").click()
    page.get_by_text("Start: Ogulin").wait_for()
    page.wait_for_function(SHOWS, arg=[45.26611, 15.22861])


def test_a_start_place_found_with_the_map_closed_opens_it_there(browser, server) -> None:
    page = open_page(browser, server, Api({}))
    page.locator("#drive-on").check()
    page.locator("#origin").fill("Ogulin")
    page.locator("#origin-find").click()
    page.get_by_text("Start: Ogulin").wait_for()

    page.locator("#map-pick-on").check()

    page.wait_for_function(SHOWS, arg=[45.26611, 15.22861])


# ---------- what the visitor check of round 3 found (ADR-0054) ----------


def test_a_shared_hike_link_carries_the_search_settings_and_opens_them(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}", init=COPIED)
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.get_by_label("Weather month").select_option("1")
    page.get_by_label("Length weight, 0 to 100").fill("50")
    page.get_by_label("Only hikes near a starting place").check()
    page.get_by_label("From", exact=True).fill("agram")
    page.get_by_role("button", name="Find place").click()
    page.get_by_text("Start: Zagreb, HR (45.8144, 15.9780)").wait_for()
    page.get_by_label("Max km, straight line").check()
    page.get_by_label("Max km value").fill("200")
    page.get_by_label("Max km value").dispatch_event("change")

    page.locator("#liked").get_by_role("button", name="Share").click()
    page.locator("#liked").get_by_role("button", name="Link copied").wait_for()
    link = page.evaluate("window.copied[0]")
    assert link.startswith(f"{server}/#hike={RIDGE_WALK_ID}&month=1&w=length:50")
    assert "from=45.81444,15.97798&place=Zagreb%2C%20HR&hours=2&km=200" in link
    assert page.evaluate("location.hash") == link.removeprefix(server + "/")  # a reload opens it too

    friend = open_page(browser, server, Api({}), hash=link.removeprefix(server + "/"))
    friend.get_by_text("Chosen: Ridge Walk").wait_for()
    assert friend.get_by_label("Weather month").input_value() == "1"
    assert friend.get_by_label("Length weight, 0 to 100").input_value() == "50"
    assert friend.get_by_label("Only hikes near a starting place").is_checked()
    assert friend.get_by_label("From", exact=True).input_value() == "Zagreb, HR"
    assert friend.get_by_label("Max km value").input_value() == "200"
    assert friend.get_by_text("Start: Zagreb, HR (45.8144, 15.9780)").is_visible()


def test_a_wish_link_opens_its_month_and_starting_place_too(browser, server) -> None:
    hash = "#wish=elevation_gain:800:100&month=7&from=45.81444,15.97798&place=Zagreb%2C%20HR&km=60"
    page = open_page(browser, server, Api(WISHABLE), hash=hash)
    page.get_by_text("1. The hike you want").wait_for()

    assert page.get_by_label("Weather month").input_value() == "7"
    assert page.get_by_label("From", exact=True).input_value() == "Zagreb, HR"
    assert page.get_by_label("Max km value").input_value() == "60"
    assert page.evaluate("location.hash").startswith("#wish=elevation_gain:800:100&month=7&from=45.81444,15.97798")


@pytest.mark.parametrize("hash", [f"#hike={RIDGE_WALK_ID}&foo=bar", f"#foo=bar&hike={RIDGE_WALK_ID}", f"#HIKE={RIDGE_WALK_ID}%20"])
def test_a_hike_link_with_a_part_the_page_does_not_know_still_opens_the_hike(browser, server, hash) -> None:
    page = open_page(browser, server, Api({}), hash=hash)
    page.get_by_text("Chosen: Ridge Walk").wait_for()


@pytest.mark.parametrize("hash", ["#hike=abc", "#hike=-5&strict", f"#hike={RIDGE_WALK_ID},{RIDGE_WALK_ID}", "#compose=12:4.5,-"])
def test_a_link_cut_short_or_changed_says_plainly_it_could_not_be_opened(browser, loops_server, hash) -> None:
    page = open_page(browser, loops_server, Api({}), hash=hash)

    page.get_by_text("This link could not be opened: it looks cut short or changed, so it shows no hike.").wait_for()
    assert page.locator("#liked-name").inner_text() == "This link could not be opened; no hike chosen yet."


SEARCHED = [
    {"id": RIDGE_WALK_ID, "name": "Ridge Walk", "length_m": 5000, "round_trip": False, "climb_m": 400,
     "descent_m": 400, "elevation_gain_m": 400, "highest_m": 1800, "trailhead": [45.04, 15.0], "match": "words"},
    {"id": 2, "name": "Ridge Walk extension", "length_m": 9000, "round_trip": False, "climb_m": 600,
     "descent_m": 600, "elevation_gain_m": 600, "highest_m": 1900, "trailhead": [45.05, 15.0], "match": "words"},
]  # fmt: skip


@pytest.mark.parametrize("touch", [False, True])
def test_enter_picks_only_an_exact_name_and_else_leaves_the_list_open_saying_so(browser, server, touch) -> None:
    page = open_page(browser, server, Api({}), touch=touch, width=390 if touch else 1280)
    page.route(f"{server}/api/search?*", lambda route: route.fulfill(json={"results": SEARCHED}))
    page.route(f"{server}/api/landmarks?*", lambda route: route.fulfill(json={"results": []}))
    search = page.locator("#search")

    search.fill("ridge")
    search.press("Enter")  # pressed before the list is in: it is decided once it is

    page.get_by_text("2 hikes match; pick one (or use ↑ ↓, then Enter).").wait_for()
    assert page.locator("#liked-name").inner_text() == "No hike chosen yet."

    search.fill("ridge walk")
    search.press("Enter")

    page.get_by_text("Chosen: Ridge Walk").wait_for()
    assert "(Enter picked the hike with exactly that name)" in page.locator("#liked-name").inner_text()


def test_find_and_a_picked_hike_bring_their_card_into_view_on_a_phone(browser, server) -> None:
    page = open_page(browser, server, Api({}), width=390, touch=True)
    page.route(f"{server}/api/search?*", lambda route: route.fulfill(json={"results": SEARCHED[:1]}))
    page.locator("#search").fill("ridge walk")
    page.locator("#suggestions li[role=option]").first.click()
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.wait_for_function("() => Math.abs(document.querySelector('#liked').getBoundingClientRect().top) < 2")

    page.evaluate("scrollTo(0, 0)")
    _find(page)

    page.wait_for_function("() => Math.abs(document.querySelector('#results').getBoundingClientRect().top) < 2")


def test_a_find_refused_while_this_tab_s_last_search_runs_waits_for_it_and_names_the_tab(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    asked: list[str | None] = []

    def busy_once(route) -> None:
        asked.append(route.request.headers.get("x-hikesalike-tab"))
        if len(asked) == 1:
            route.fulfill(status=409, json={"detail": "Your previous search is still running; try again when it ends."})
        else:
            route.continue_()

    page.route(f"{server}/api/rank", busy_once)
    page.get_by_role("button", name="Find similar").click()
    page.get_by_text("Waiting for your earlier search to end…").wait_for()
    page.locator("#results-table tbody tr").first.wait_for(timeout=10_000)

    assert len(asked) == 2 and asked[0] and asked[0] == asked[1]


def test_a_list_left_under_still_running_goes(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.clock.install()
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)
    page.route(f"{server}/api/rank", lambda route: route.fulfill(
        status=409, json={"detail": "Your previous search is still running; try again when it ends."}))

    page.get_by_role("button", name="Find similar").click()
    page.get_by_text("Waiting for your earlier search to end…").wait_for()
    for _ in range(45):  # each wait is a timer, each ask a real request: step the clock past TIMEOUT_S
        page.clock.run_for(1_500)
        page.wait_for_timeout(20)

    page.locator("#results-stale").get_by_text("This search did not run").wait_for()
    assert not page.locator("#results-table").is_visible()


def test_a_list_whose_best_scores_below_a_typical_pair_says_the_matches_are_weak(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.route(f"{server}/api/rank", Pool([0.31, 0.29, 0.25]))
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)

    poor = page.locator("#results-poor")
    assert poor.is_visible()
    assert poor.inner_text().startswith("Weak matches: the best of these scores 0.310, below the 0.43 of a typical pair")

    page.unroute(f"{server}/api/rank")
    page.route(f"{server}/api/rank", Pool([0.8, 0.3, 0.2]))
    page.get_by_label("Strict order").check()
    _find(page)
    page.locator("#results-poor").get_by_text("From #2 down, these score below the 0.43").wait_for()

    page.unroute(f"{server}/api/rank")
    page.route(f"{server}/api/rank", Pool([0.8, 0.7, 0.6]))
    _find(page)
    page.wait_for_function("() => document.querySelector('#results-poor').hidden")


def test_a_click_where_two_hikes_run_along_one_trail_offers_both(browser, server) -> None:
    day = drawn_hike("Day hike", WEST, EAST)
    stage = {**drawn_hike("Long tour, stage 4", (45.0600, 6.5100), EAST), "walk_m": 30_000}
    page = map_dots(browser, server, [stage, day])

    assert click_map(page, START, 0) == []  # on the shared trail: nothing chosen yet, a choice offered
    offer = page.locator(".pick-offer")
    assert offer.locator("strong").inner_text() == "2 hikes run here; choose one:"
    assert offer.get_by_role("button").all_inner_texts() == ["Day hike (1.0 km)", "Long tour, stage 4 (30 km)"]
    offer.get_by_role("button", name="Day hike (1.0 km)").click()
    assert page.evaluate("window.chosen") == [day["id"]]


def test_a_click_on_the_end_of_a_hike_where_others_run_along_one_trail_offers_it_with_them(browser, server) -> None:
    day = drawn_hike("Day hike", START, EAST)  # starts on the trail the other two follow
    stage = {**drawn_hike("Long tour, stage 4", WEST, EAST), "walk_m": 30_000}
    other = {**drawn_hike("Other hike", (45.0600, 6.5100), EAST), "walk_m": 20_000}
    page = map_dots(browser, server, [stage, other, day])

    assert click_map(page, START, -3) == []  # on the day hike's start dot, just off its line
    offer = page.locator(".pick-offer")
    assert offer.get_by_role("button").all_inner_texts() == [
        "Day hike (1.0 km)", "Other hike (20 km)", "Long tour, stage 4 (30 km)"]
    offer.get_by_role("button", name="Day hike (1.0 km)").click()
    assert page.evaluate("window.chosen") == [day["id"]]


def test_the_hike_map_opens_where_most_hikes_are_then_on_the_liked_hike(browser, server) -> None:
    page = open_page(browser, server, Api({}))
    page.locator("#map-pick-on").check()
    south, west, north, east = page.evaluate("state.info.core")
    centre = page.evaluate("[pick.map.getCenter().lat, pick.map.getCenter().lng]")
    assert south <= centre[0] <= north and west <= centre[1] <= east
    page.locator("#map-pick-on").uncheck()

    page.goto(f"{server}/#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.locator("#map-pick-on").check()

    assert page.evaluate("pick.map.getZoom()") >= 12  # the hikes there show as lines
    assert page.evaluate("pick.map.getBounds().contains(state.liked.trailhead)")


def test_the_score_explanation_says_while_there_is_more_below(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)
    page.locator("#results-table thead .help").first.click()

    cue = page.locator("#score-help .help-more")
    assert cue.is_visible()
    page.evaluate("const box = document.querySelector('#score-help'); box.scrollTop = box.scrollHeight")
    page.wait_for_function("() => !document.querySelector('#score-help').classList.contains('more')")
    assert not cue.is_visible()


def test_on_a_phone_folds_show_they_open_the_table_says_it_scrolls_and_share_keeps_the_map(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}", width=390, touch=True, init=COPIED)
    page.get_by_text("Chosen: Ridge Walk").wait_for()

    fold = page.locator("#weights-panel > summary")
    assert page.evaluate("s => getComputedStyle(s, '::after').content", fold.element_handle()) == '"show"'
    assert page.evaluate("s => getComputedStyle(s, '::before').content", fold.element_handle()) == '"▸"'
    assert "on the left" not in page.locator("body").inner_text()

    top = "() => document.querySelector('#liked .map').getBoundingClientRect().top + scrollY"
    before = page.evaluate(top)
    page.locator("#liked").get_by_role("button", name="Share").click()
    page.locator("#liked .share-note").wait_for()
    assert page.evaluate(top) == before

    _find(page)
    assert page.get_by_text("Scroll the table sideways for every measure's score →").is_visible()


def test_suggest_loops_keeps_the_rows_shown_and_says_where_the_loops_are(browser, loops_server, monkeypatch) -> None:
    monkeypatch.setattr("dayhike.serving.GROUP_M", 0.0)
    monkeypatch.setattr(web, "RESULTS", 2)
    api = Api({})
    page = open_page(browser, loops_server, api, hash=f"#hike={MEADOW_PATH}&strict")
    page.get_by_text("Chosen: Meadow Path").wait_for()
    _find(page)
    page.get_by_role("button", name="Show 2 more").click()
    page.wait_for_function("() => state.results.length === 4")

    page.get_by_role("button", name="Suggest loops near these hikes (takes a few seconds)").click()

    page.wait_for_function("() => state.answer.loops && state.results.length === 4")
    assert [asked.get("loops") for asked in api.ranked[-2:]] == [True, False]  # later rows search no loops
    note = page.locator("#results-loops-note").inner_text()
    assert re.search(r"listed: #\d", note) or "none listed" in note


# ---------- the fourth visitor check (ADR-0055, ADR-0056) ----------


def test_an_address_edited_to_other_settings_marks_the_list_shown_as_from_before(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)
    assert not page.locator("#results-stale").is_visible()

    page.evaluate(f"location.hash = '#hike={RIDGE_WALK_ID}&w=length:50'")  # typed in the address bar

    page.locator("#results-stale").get_by_text("The query changed since this ranking").wait_for()
    assert page.locator('.weight[data-measure="length"] output').inner_text() == "50"
    page.go_back()  # the address the list was found with: the weights stay as set, so it stays marked
    page.wait_for_function("() => location.hash === '#hike=1001'")
    assert page.locator("#results-stale").is_visible()


def test_back_to_the_home_address_shows_the_home_page_with_no_hike_or_list(browser, server) -> None:
    page = open_page(browser, server, Api({}))
    page.evaluate(f"location.hash = '#hike={RIDGE_WALK_ID}&w=length:50&from=45.8,15.9&km=200'")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)

    page.go_back()

    page.wait_for_function("() => location.hash === '' && !state.liked")
    assert page.locator("#liked-name").inner_text() == "No hike chosen yet."
    assert not page.locator("#results").is_visible()
    assert page.locator('.weight[data-measure="length"] output').inner_text() == "100"  # as the page opens
    assert not page.get_by_label("Only hikes near a starting place").is_checked()
    assert page.get_by_role("button", name="Find similar").is_disabled()


def test_link_to_this_hike_carries_the_settings_as_they_are_now_as_share_does(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}", init=COPIED)
    page.get_by_text("Chosen: Ridge Walk").wait_for()

    page.locator('.weight[data-measure="highest"] input[type=range]').evaluate(
        "slider => { slider.value = 0; slider.dispatchEvent(new Event('input', { bubbles: true })); }"
    )
    open_advanced(page)

    link = page.locator("#liked").get_by_role("link", name="Link to this hike")
    page.wait_for_function("() => document.querySelector('#liked a.hike-link').getAttribute('href').includes('&adv')")
    page.locator("#liked").get_by_role("button", name="Share").click()
    page.wait_for_function("() => window.copied.length")
    assert link.get_attribute("href") == f"#hike={RIDGE_WALK_ID}&w=highest:0&adv"
    assert page.evaluate("window.copied") == [f"{server}/{link.get_attribute('href')}"]


BUSY = "The server is busy with other visitors' searches: yours waited 25 s for its turn. Try again in a moment."


def test_a_busy_server_is_said_beside_the_button_pressed_with_a_try_again(browser, server, monkeypatch) -> None:
    monkeypatch.setattr(web, "RESULTS", 1)
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}&strict", width=390, touch=True)
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    answers = iter([503, 200, 503, 200])  # Find busy, Try again; Show 1 more busy, Try again

    def rank(route) -> None:
        if next(answers) == 503:
            route.fulfill(status=503, json={"detail": BUSY})
        else:
            route.continue_()

    page.route(f"{server}/api/rank", rank)
    page.get_by_role("button", name="Find similar").click()

    status = page.locator("#status")
    status.get_by_text(BUSY).wait_for()
    status.get_by_role("button", name="Try again").click()
    page.locator("#results-table tbody tr").first.wait_for()

    page.get_by_role("button", name="Show 1 more").click()  # busy once more: said beside Show 1 more
    more = page.locator("#more-status")
    more.get_by_text(BUSY).wait_for()
    gap = page.evaluate("""() => document.querySelector('#more-status').getBoundingClientRect().top
      - document.querySelector('#show-more').getBoundingClientRect().top""")
    assert abs(gap) < 100
    more.get_by_role("button", name="Try again").click()
    page.wait_for_function("() => state.results.length === 2")
    assert more.inner_text().startswith("Done in")


def test_a_setting_changed_after_a_search_is_said_under_the_find_button(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}", width=390, touch=True)
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)

    page.locator("#weights-panel > summary").click()  # folded away on a phone
    page.locator("#month").select_option("1")

    page.locator("#status").get_by_text("Your settings changed since the list below: press Find similar again").wait_for()


def test_a_hike_chosen_on_the_map_is_said_under_the_map(browser, server) -> None:
    ridge = {**drawn_hike("Ridge Walk", WEST, EAST), "id": RIDGE_WALK_ID}
    page = map_dots(browser, server, [ridge])
    point = page.evaluate("([lat, lon]) => pick.map.latLngToContainerPoint([lat, lon])", list(START))
    box = page.locator("#map-pick .pick-map").bounding_box()

    page.mouse.click(box["x"] + point["x"], box["y"] + point["y"])

    page.locator("#map-pick-hint").get_by_text("Chosen: Ridge Walk,").wait_for()
    assert page.locator("#map-pick-hint").bounding_box()["y"] > box["y"]  # under the map


def test_share_on_a_phone_says_its_share_menu_is_opening(browser, server) -> None:
    waits = "navigator.share = () => new Promise(() => {});"  # a share sheet still open
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}", init=waits, touch=True, width=390)
    page.get_by_text("Chosen: Ridge Walk").wait_for()

    page.locator("#liked").get_by_role("button", name="Share").click()

    page.locator("#liked .share-note").get_by_text("Opening your phone's share menu…").wait_for()


def test_a_click_where_lines_pass_near_each_other_offers_every_one(browser, server) -> None:
    # A day hike along a trail and another 5 px north of it, as two copies of one path simplified apart are.
    day = drawn_hike("Day hike", WEST, EAST)
    page = map_dots(browser, server, [day])
    north = page.evaluate("([lat, lon]) => pick.map.containerPointToLatLng(pick.map.latLngToContainerPoint([lat, lon]).add([0, -5]))", list(WEST))
    beside = {**drawn_hike("Zirbenweg", (north["lat"], WEST[1]), (north["lat"], EAST[1])), "walk_m": 7_200}
    page.unroute(f"{server}/api/hikes?*")
    page.route(f"{server}/api/hikes?*", lambda route: route.fulfill(json={"results": [day, beside], "too_many": False}))
    page.evaluate("pick.load()")
    page.wait_for_function("() => pick.shown.length === 2")

    assert click_map(page, START, 0) == []  # on the day hike: both offered, not the nearest alone
    assert page.locator(".pick-offer").get_by_role("button").all_inner_texts() == ["Day hike (1.0 km)", "Zirbenweg (7.2 km)"]


def test_the_designer_walks_to_a_junction_clicked_and_its_messages_leave_the_map_in_place(browser, server) -> None:
    page = open_page(browser, server, Api({}))
    page.locator("#map-pick-on").check()
    page.evaluate("""() => {
      compose.on = true;
      compose.junctions = new Map([[1, { id: 1, latlng: [46, 14] }], [2, { id: 2, latlng: [46.01, 14] }], [3, { id: 3, latlng: [46.02, 14] }]]);
      compose.segments = new Map([
        [10, { id: 10, kind: "trail", a: 1, b: 2, length: 1100, climbAB: 50, climbBA: 0, routes: [], stepAB: "a", stepBA: "b",
          latlngs: [[46, 14], [46.01, 14]] }],
        [11, { id: 11, kind: "trail", a: 2, b: 3, length: 1100, climbAB: 0, climbBA: 50, routes: [], stepAB: "c", stepBA: "d",
          latlngs: [[46.01, 14], [46.02, 14]] }],
      ]);
      compose.layer = L.layerGroup().addTo(pick.map);
      compose.shown = [10, 11];
      pick.map.setView([46.01, 14], 14, { animate: false });
    }""")
    top = "() => document.querySelector('#map-pick .pick-map').getBoundingClientRect().top"
    before = page.evaluate(top)

    page.evaluate("compose.toJunction(1)")  # the start
    page.evaluate("compose.toJunction(3)")  # two segments on: both walked, as one click
    assert page.evaluate("compose.route.map(s => [s.id, s.auto])") == [[10, True], [11, False]]
    assert "Walked 2 segments to that junction" in page.locator("#map-pick-hint").inner_text()
    assert page.evaluate(top) == before  # the hint and readout grew under the map, not over it
    # A click on a line this near a junction's dot is for the junction: back to the start here.
    assert page.evaluate("compose.junctionAt(pick.map.layerPointToLatLng(pick.map.latLngToLayerPoint([46, 14]).add([3, 0])))") == 1
    page.evaluate("compose.undo()")
    assert page.evaluate("compose.route.length") == 0


def test_the_pair_table_shows_its_score_column_on_a_phone(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}", width=390, touch=True)
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    page.locator("#pair-search").fill("Valley Path")
    page.locator("#pair-suggestions li", has_text="Valley Path").first.dispatch_event("mousedown")
    page.locator("#selected").get_by_text("Compared with the liked hike").wait_for()

    assert page.evaluate("() => { const w = document.querySelector('#selected .table-wrap'); return w.scrollWidth - w.clientWidth; }") <= 1


def test_suggestions_say_where_a_hike_starts_and_mark_a_multi_day_route(browser, server) -> None:
    page = open_page(browser, server, Api({}))

    def multi_day(route) -> None:
        body = route.fetch().json()
        for hike in body["results"]:
            if hike["id"] == RIDGE_WALK_ID:
                hike.update(multi_day=True, walk_m=1_073_000)
        route.fulfill(json=body)

    page.route(f"{server}/api/search?*", multi_day)
    page.locator("#search").fill("walk")
    meta = page.locator("#suggestions li", has_text="Ridge Walk").first.locator(".meta")
    meta.wait_for()
    assert meta.inner_text().startswith("multi-day, 1,073 km")

    page.locator("#search").fill("valley path")
    page.locator("#suggestions li", has_text="Valley Path").first.locator(".meta").get_by_text("near Ogulin, HR").wait_for()
    page.locator("#search").fill("vrh")
    page.locator("#suggestions li.landmark", has_text="Hikes near Vrh (village, near Ogulin, HR)").wait_for()


def test_a_multi_day_route_is_marked_in_the_list_and_panel_and_drawn_only_after_the_day_hikes(browser, server) -> None:
    def multi_day(route) -> None:
        body = route.fetch().json()
        body["day_first"] = True
        body["results"][-1].update(multi_day=True, walk_m=1_073_000)
        route.fulfill(json=body)

    page = open_page(browser, server, Api({}), hash="#hike=1002")
    page.get_by_text("Chosen: Valley Path").wait_for()
    page.route(f"{server}/api/rank", multi_day)
    _find(page)

    assert page.locator("#results-table tbody tr").last.locator(".multi-day-tag").inner_text() == "multi-day, 1,073 km"
    assert page.evaluate("drawScore({ score: 0.9, multi_day: true })") == pytest.approx(-0.1)
    assert page.evaluate("drawScore({ score: 0.9, multi_day: false })") == 0.9
    page.route(f"{server}/api/hikes/1001*", lambda route: route.fulfill(json={**route.fetch().json(), "multi_day": True, "walk_m": 1_073_000}))
    page.evaluate(f"location.hash = '#hike={RIDGE_WALK_ID}'")
    page.locator("#liked .badges li").first.get_by_text("multi-day, 1,073 km").wait_for()
