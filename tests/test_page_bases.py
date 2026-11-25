"""The "Find a base" mode (ADR-0084) in a headless browser, over the real app on the fixture export with Bases.

The export and its ``bases/`` folder are those of ``test_web_bases``: Keswick and Bethesda reach the same four
hikes, Chamonix half of them and Moab the other half; Moab is never mild.
Needs Chromium for Playwright: ``uv run playwright install chromium``.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from dayhike import build_catalog, build_similarity
from dayhike.bases import REACHES
from dayhike.serving import ServingData, export_serving
from dayhike.web import create_app
from test_page import Api, browser, open_page, serve  # noqa: F401 (browser: a fixture)
from test_page import _close_what_a_test_opened  # noqa: F401 (an autouse fixture)
from test_web_bases import BETHESDA, KESWICK, _with_bases

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"
# A list superseded while the server still runs it makes the next wait its turn (409, asked again every 1.5 s):
# under a loaded test run that can outlast the default 5 s.
SETTLE_MS = 20_000


@pytest.fixture(scope="module")
def exports(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    directory = tmp_path_factory.mktemp("page-bases")
    path = directory / "catalog.sqlite"
    build_catalog(FIXTURE, path)
    build_similarity(path, directory / "dem")
    plain = export_serving(path, directory / "web-serving")
    return plain, _with_bases(plain, directory / "with-bases")


@pytest.fixture(scope="module")
def server(exports: tuple[Path, Path]) -> Iterator[str]:
    yield from serve(create_app(ServingData(exports[1]), rank_rate=1_000, base_rank_rate=1_000, api_rate=100_000))


@pytest.fixture(scope="module")
def plain_server(exports: tuple[Path, Path]) -> Iterator[str]:
    yield from serve(create_app(ServingData(exports[0]), rank_rate=1_000, api_rate=100_000))


@pytest.fixture(autouse=True)
def _few_hikes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixture export has four hikes: as in ``test_web_bases``, a Base needs one HikeGroup, not ten, one
    is enough to compare on, none is left out as in the Hiker's region, and scores run from 0."""
    monkeypatch.setattr("dayhike.bases.MIN_HIKE_GROUPS", 1)
    monkeypatch.setattr("dayhike.bases.ENOUGH_GROUPS", 1)
    monkeypatch.setattr("dayhike.bases.OWN_REGION_SHARE", 1.0)
    monkeypatch.setattr("dayhike.bases.SCORE_FLOOR", dict.fromkeys(REACHES, 0.0))


def edited(change):
    """A route handler: the server's answer, changed by ``change`` (its body, in place); one refused as it is."""

    def handle(route) -> None:
        response = route.fetch()
        if response.status != 200:  # e.g. a 409 while the list it replaces still runs
            route.fulfill(response=response)
            return
        body = response.json()
        change(body)
        route.fulfill(json=body)

    return handle


def open_bases(browser, server: str, **options):
    """The page in "Find a base" mode."""
    page = open_page(browser, server, Api({}), **options)
    page.get_by_role("button", name="Find a base").click()
    page.locator("#base-panel").wait_for()
    return page


def choose_base(page, typed: str, name: str) -> None:
    page.get_by_label("A town you stayed in").fill(typed)
    page.get_by_role("option", name=name).click()
    page.locator("#base-chosen").get_by_text(name).wait_for()


def rank(page) -> list[str]:
    """Presses Find bases: the names the list shows, best first."""
    page.wait_for_function("() => !document.querySelector('#base-find').disabled", timeout=SETTLE_MS)  # a town chosen asks at once
    page.get_by_role("button", name="Find bases").click()
    page.locator("#base-results tbody tr").first.wait_for()
    page.wait_for_function("() => !document.querySelector('#base-find').disabled && !bases.settling", timeout=SETTLE_MS)  # the last list asked for
    return page.locator("#base-results tbody .base-name").all_inner_texts()


def asked(page, action) -> dict:
    """The body of the BaseRanking ``action`` asks for."""
    with page.expect_request("**/api/bases/rank") as request:
        action()
    return json.loads(request.value.post_data)


def test_the_mode_is_offered_only_by_a_site_with_bases(browser, server, plain_server) -> None:
    plain = open_page(browser, plain_server, Api({}))

    assert plain.get_by_role("button", name="Find a base").is_hidden()

    page = open_bases(browser, server)

    assert page.locator("#seed-pick").is_hidden()
    assert page.locator("#liked").is_hidden()
    page.get_by_role("button", name="Leave Find a base").click()
    assert page.locator("#base-panel").is_hidden()
    assert page.locator("#seed-pick").is_visible()
    assert page.locator("#liked").is_visible()


def test_a_town_searched_for_lists_the_bases_alike_best_first(browser, server) -> None:
    page = open_bases(browser, server)
    assert page.get_by_role("button", name="Find bases").is_disabled()

    choose_base(page, "Kes", "Keswick")
    page.get_by_role("radio", name="about 60 min").check()
    body = asked(page, lambda: rank(page))

    assert body == {"seed": KESWICK, "reach": 60, "traits": {"size": {"up_to": 50_000}}}  # the default
    rows = page.locator("#base-results tbody tr")
    assert rows.first.locator(".base-name").inner_text() == "Bethesda"
    assert "GB" in rows.first.inner_text()
    assert "Keswick" not in page.locator("#base-results tbody").inner_text()  # the Hiker's own Base
    assert "Bases with hikes like Keswick's" in page.locator("#base-results h2").inner_text()


def test_more_letters_typed_drop_the_old_suggestions(browser, server) -> None:
    page = open_bases(browser, server)
    box = page.get_by_label("A town you stayed in")
    box.fill("Kes")
    page.get_by_role("option", name="Keswick").wait_for()

    box.press("z")
    box.press("ArrowDown")
    box.press("Enter")

    assert page.get_by_role("option", name="Keswick").is_hidden()
    assert "No town chosen yet" in page.locator("#base-chosen").inner_text()


def test_a_shorter_reach_asks_for_it(browser, server) -> None:
    page = open_bases(browser, server)
    choose_base(page, "Kes", "Keswick")

    assert page.get_by_role("radio", name="about 30 min").is_checked()  # the default
    assert asked(page, lambda: rank(page))["reach"] == 30
    assert asked(page, lambda: page.get_by_role("radio", name="about 15 min").check())["reach"] == 15


def test_a_reach_changed_while_the_first_ranking_runs_wins(browser, server) -> None:
    page = open_bases(browser, server)
    held = []

    def hold_the_first(route) -> None:
        if held:
            route.continue_()
        else:
            held.append(route)

    page.route("**/api/bases/rank", hold_the_first)
    choose_base(page, "Kes", "Keswick")  # asks for the list at once, at 30 min: held
    page.wait_for_function("() => bases.asked === 1")
    reach = asked(page, lambda: page.get_by_role("radio", name="about 15 min").check())["reach"]
    page.locator("#base-results tbody tr").first.wait_for()
    held[0].fulfill(json=held[0].fetch().json())
    page.wait_for_timeout(300)

    assert reach == 15
    assert "about 15 min" in page.locator("#base-note").inner_text()


def test_every_row_explains_its_score(browser, server) -> None:
    page = open_bases(browser, server)
    choose_base(page, "Kes", "Keswick")
    page.get_by_role("radio", name="about 60 min").check()
    rank(page)

    best = page.locator("#base-results tbody tr").first
    assert best.locator(".score").inner_text().startswith("1.00")
    assert "hikes alike 1.00" in best.locator(".why").inner_text()
    best.get_by_role("button", name="What the base score means").click()
    box = page.locator("#score-help")
    assert box.is_visible()
    assert "Base score" in box.locator("h3").inner_text()
    assert "40 %" in box.inner_text()  # the mix's share of the hike score
    page.keyboard.press("Escape")
    assert box.is_hidden()


def test_a_trait_that_took_off_the_score_is_said_in_the_row(browser, server) -> None:
    page = open_bases(browser, server)

    def with_cuts(body: dict) -> None:
        body["results"][0].update(score=0.8, hike_score=0.9, cuts={"size": 0.11, "stays": 0.0})

    page.route("**/api/bases/rank", edited(with_cuts))
    choose_base(page, "Kes", "Keswick")
    rank(page)

    why = page.locator("#base-results tbody tr").first.locator(".why").inner_text()
    assert "hikes alike 0.90" in why
    assert "town size −11 %" in why
    assert "places to stay" not in why  # nothing taken off


def test_the_months_strip_marks_the_mild_months(browser, server) -> None:
    page = open_bases(browser, server)
    choose_base(page, "Kes", "Keswick")
    page.get_by_role("radio", name="about 60 min").check()
    rank(page)

    rows = page.locator("#base-results tbody tr")
    bethesda = rows.filter(has_text="Bethesda").locator(".months li")
    moab = rows.filter(has_text="Moab").locator(".months li")
    assert bethesda.count() == moab.count() == 12
    assert rows.filter(has_text="Bethesda").locator(".months li.mild").count() == 12
    assert rows.filter(has_text="Moab").locator(".months li.mild").count() == 0
    assert "25" in moab.first.get_attribute("title")  # the month's temperature


def test_the_map_marks_the_hikers_base_and_every_row(browser, server) -> None:
    page = open_bases(browser, server)
    choose_base(page, "Kes", "Keswick")
    page.get_by_role("radio", name="about 60 min").check()
    names = rank(page)

    marked = page.evaluate("() => bases.markers.map((m) => m.getTooltip().getContent())")
    assert len(marked) == len(names) + 1
    assert "Keswick" in marked[0]
    assert [next(n for n in names if n in text) for text in marked[1:]] == names


def test_a_pin_on_the_map_chooses_the_nearest_base_within_5_km(browser, server) -> None:
    page = open_bases(browser, server)

    def pin(lat: float, lon: float) -> None:
        point = page.evaluate("([lat, lon]) => bases.map.latLngToContainerPoint([lat, lon])", [lat, lon])
        box = page.locator("#base-map").bounding_box()
        page.mouse.click(box["x"] + point["x"], box["y"] + point["y"])

    page.evaluate("() => bases.map.setView([47.0, 7.0], 12)")
    pin(47.01, 7.0)  # about 1 km from Bethesda
    page.locator("#base-chosen").get_by_text("Bethesda").wait_for()
    assert asked(page, lambda: rank(page))["seed"] == BETHESDA
    page.wait_for_function("() => !document.querySelector('#base-find').disabled")  # the list is in: the map is fitted to it

    page.evaluate("() => bases.map.setView([47.5, 7.0], 12)")
    pin(47.5, 7.0)  # about 55 km from Bethesda and Chamonix
    page.locator("#base-pin-hint").get_by_text("No base within 5 km").wait_for()
    assert "Bethesda" in page.locator("#base-chosen").inner_text()  # still the one chosen


def test_too_few_hikes_to_compare_is_said_and_nothing_is_listed(browser, server) -> None:
    page = open_bases(browser, server)
    page.route("**/api/bases/rank", edited(lambda body: body.update(too_few=True, results=[])))
    choose_base(page, "Kes", "Keswick")
    page.get_by_role("button", name="Find bases").click()

    page.locator("#base-results").get_by_text("are needed to compare towns").wait_for()
    assert page.locator("#base-results tbody tr").count() == 0


def open_base(page, name: str) -> dict:
    """Opens the row of ``name``: the body of the one Base it asks for, once its hikes are shown."""
    with page.expect_request("**/api/bases/detail") as request:
        page.locator("#base-results tbody").get_by_role("button", name=name).click()
    page.locator("#base-hike-list li").first.wait_for()
    return json.loads(request.value.post_data)


def keswick_in_60(page) -> None:
    choose_base(page, "Kes", "Keswick")
    page.get_by_role("radio", name="about 60 min").check()
    rank(page)


def test_an_opened_base_lists_its_passing_hikes_nearest_first_and_maps_them(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)

    assert open_base(page, "Bethesda") == {"base": BETHESDA, "reach": 60}
    assert "Bethesda" in page.locator("#base-hikes h2").inner_text()
    minutes = page.locator("#base-hike-list .minutes").all_inner_texts()
    assert minutes == ["about 5 min", "about 10 min", "about 20 min", "about 50 min"]  # REACHED, nearest first
    assert page.locator("#base-results").is_hidden()
    marked = page.evaluate("() => bases.hikeMarkers.map((m) => [m.getLatLng().lat, m.getLatLng().lng])")
    assert len(marked) == 4
    assert all(abs(lat - 47.0) < 3 for lat, _ in marked)  # at the Trailheads, [lat, lon] read the right way round

    page.get_by_role("button", name="Back to the list").click()
    assert page.locator("#base-results").is_visible()
    assert page.locator("#base-hikes").is_hidden()
    assert len(page.evaluate("() => bases.markers")) == 1 + page.locator("#base-results tbody tr").count()


def test_a_shorter_reach_opens_the_base_again_with_it(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    open_base(page, "Bethesda")

    with page.expect_request("**/api/bases/detail") as request:
        page.get_by_role("radio", name="about 15 min").check()
    page.locator("#base-hike-list li").nth(1).wait_for()
    page.wait_for_function("() => document.querySelectorAll('#base-hike-list li').length === 2")

    assert json.loads(request.value.post_data)["reach"] == 15
    assert page.locator("#base-hike-list .minutes").all_inner_texts() == ["about 5 min", "about 10 min"]


def test_a_hike_of_an_opened_base_links_into_hikes_like_it(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    open_base(page, "Bethesda")
    first = page.locator("#base-hike-list li").first
    name = first.locator(".hike-name").inner_text()
    link = first.get_by_role("link", name="Open this hike to find ones like it")

    assert (link.get_attribute("href") or "").startswith("#hike=")
    link.click()

    page.locator("#liked-name").get_by_text(name).wait_for()
    assert page.locator("#base-panel").is_hidden()
    assert page.locator("#seed-pick").is_visible()
    assert "base" not in page.evaluate("() => location.hash")


def test_the_address_carries_the_town_the_reach_and_the_opened_base(browser, server) -> None:
    page = open_bases(browser, server)
    assert page.evaluate("() => location.hash") == "#base&reach=30"
    keswick_in_60(page)
    assert page.evaluate("() => location.hash") == f"#base={KESWICK}&reach=60"
    open_base(page, "Bethesda")
    address = page.evaluate("() => location.hash")
    assert address == f"#base={KESWICK}&reach=60&open={BETHESDA}"

    shared = open_page(browser, server, Api({}), hash=address)

    shared.locator("#base-hike-list li").first.wait_for()
    assert shared.locator("#base-panel").is_visible()
    assert "Keswick" in shared.locator("#base-chosen").inner_text()
    assert shared.get_by_label("A town you stayed in").input_value() == "Keswick"
    assert shared.get_by_role("radio", name="about 60 min").is_checked()
    assert "Bethesda" in shared.locator("#base-hikes h2").inner_text()
    assert shared.locator("#base-hike-list li").count() == 4

    shared.get_by_role("button", name="Back to the list").click()
    assert shared.evaluate("() => location.hash") == f"#base={KESWICK}&reach=60"
    assert shared.locator("#base-results tbody tr").count() > 0


def test_a_base_link_opened_in_a_tab_already_open_sets_every_setting(browser, server) -> None:
    page = open_bases(browser, server)
    choose_base(page, "Beth", "Bethesda")
    page.get_by_role("radio", name="about 15 min").check()

    page.evaluate(f"() => {{ location.hash = '#base={KESWICK}&reach=60'; }}")

    page.locator("#base-chosen").get_by_text("Keswick").wait_for()
    page.locator("#base-results tbody tr").first.wait_for()
    assert page.get_by_role("radio", name="about 60 min").is_checked()
    assert page.locator("#base-hikes").is_hidden()

    page.evaluate("() => { location.hash = '#base'; }")  # a link with no town: the mode as it opens
    page.wait_for_function("() => document.querySelector('#base-results').hidden")
    assert "No town chosen yet" in page.locator("#base-chosen").inner_text()
    assert page.get_by_role("radio", name="about 30 min").is_checked()


def test_leaving_find_a_base_leaves_the_base_address(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)

    page.get_by_role("button", name="Leave Find a base").click()

    assert "base" not in page.evaluate("() => location.hash")

def test_the_choices_start_with_size_up_to_50000_and_every_other_trait_ignored(browser, server) -> None:
    page = open_bases(browser, server)
    page.get_by_text("Which hikes count").click()
    page.get_by_text("What the town is like").click()

    assert page.get_by_label("Town size", exact=True).input_value() == "filter"
    assert page.get_by_label("Town size up to").input_value() == "50000"
    for trait in ("Places to stay", "Restaurants, cafes and pubs", "Best months"):
        assert page.get_by_label(trait, exact=True).input_value() == "ignore"
    assert page.get_by_label("Climb up to").input_value() == ""
    assert not page.get_by_label("Reaches a summit").is_checked()


def test_a_hike_filter_changes_the_request_and_survives_a_shared_link(browser, server) -> None:
    page = open_bases(browser, server)
    choose_base(page, "Kes", "Keswick")
    page.get_by_role("radio", name="about 60 min").check()
    rank(page)
    page.get_by_text("Which hikes count").click()
    page.get_by_label("Length up to").fill("6")
    assert asked(page, lambda: page.get_by_label("Length up to").press("Enter"))["hike_filter"] == {"length_max_km": 6}
    body = asked(page, lambda: page.get_by_label("Reaches a summit").check())

    assert body["hike_filter"] == {"length_max_km": 6, "summit": True}
    link = page.url

    shared = open_page(browser, server, Api({}), hash="#" + link.split("#", 1)[1])
    shared.locator("#base-results").wait_for()  # the link asks for the list as it opens
    assert shared.locator("#base-panel").is_visible()
    assert "Keswick" in shared.locator("#base-chosen").inner_text()
    assert shared.get_by_role("radio", name="about 60 min").is_checked()
    assert shared.get_by_label("Length up to").input_value() == "6"
    assert shared.get_by_label("Reaches a summit").is_checked()
    assert asked(shared, lambda: shared.get_by_role("button", name="Find bases").click()) == {**body, "reach": 60}


def test_a_trait_setting_changes_the_list_and_survives_a_shared_link(browser, server) -> None:
    page = open_bases(browser, server)
    choose_base(page, "Kes", "Keswick")
    page.get_by_role("radio", name="about 60 min").check()
    assert "Moab" in rank(page)
    page.get_by_text("What the town is like").click()

    body = asked(page, lambda: page.get_by_label("Town size up to").fill("5000") or page.get_by_label("Town size up to").press("Enter"))
    assert body["traits"] == {"size": {"up_to": 5000}}
    page.wait_for_function("() => !document.querySelector('#base-results tbody').innerText.includes('Moab')")  # 5,235 people

    page.get_by_label("Restaurants, cafes and pubs", exact=True).select_option("like_mine")
    page.get_by_label("How much restaurants, cafes and pubs matter").select_option("3")
    page.get_by_label("Best months", exact=True).select_option("filter")
    page.get_by_label("Jul", exact=True).check()
    body = asked(page, lambda: page.get_by_label("Aug", exact=True).check())
    assert body["traits"] == {"size": {"up_to": 5000}, "eats": {"like_mine": 3}, "months": {"mild_in": [7, 8]}}

    page.get_by_label("Town size", exact=True).select_option("ignore")
    page.get_by_label("Places to stay", exact=True).select_option("filter")
    page.get_by_label("Places to stay at least").fill("3")
    body = asked(page, lambda: page.get_by_label("Places to stay at least").press("Enter"))
    traits = {"stays": {"at_least": 3}, "eats": {"like_mine": 3}, "months": {"mild_in": [7, 8]}}
    assert body["traits"] == traits

    page.wait_for_function("() => !document.querySelector('#base-find').disabled")  # its last list is in
    shared = open_page(browser, server, Api({}), hash="#" + page.url.split("#", 1)[1])
    shared.locator("#base-results").wait_for()  # the link asks for the list as it opens
    assert asked(shared, lambda: shared.get_by_role("button", name="Find bases").click()) == body
    assert shared.get_by_label("Town size", exact=True).input_value() == "ignore"
    assert shared.get_by_label("How much restaurants, cafes and pubs matter").input_value() == "3"
    assert shared.get_by_label("Aug", exact=True).is_checked()
    assert not shared.get_by_label("Jun", exact=True).is_checked()

    encoded = open_page(browser, server, Api({}), hash=f"#base={KESWICK}&reach=60&months=7%2C8&length_max_km=%36")
    encoded.locator("#base-results").wait_for()
    assert encoded.get_by_label("Jul", exact=True).is_checked()
    assert encoded.get_by_label("Aug", exact=True).is_checked()
    assert not encoded.get_by_label("Jun", exact=True).is_checked()
    assert encoded.get_by_label("Length up to").input_value() == "6"


def test_an_opened_base_shows_the_hikes_that_pass_and_a_link_keeps_the_choices(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    page.get_by_text("Which hikes count").click()
    page.get_by_label("Length up to").fill("6")
    page.get_by_label("Length up to").press("Enter")

    assert open_base(page, "Bethesda") == {"base": BETHESDA, "reach": 60, "hike_filter": {"length_max_km": 6}}
    assert page.locator("#base-hike-list li").count() == 3  # hike 0 is 10 km long
    page.get_by_text("What the town is like").click()
    page.get_by_label("Town size", exact=True).select_option("ignore")
    address = page.evaluate("() => location.hash")
    assert address == f"#base={KESWICK}&reach=60&open={BETHESDA}&length_max_km=6&size=ignore"

    shared = open_page(browser, server, Api({}), hash=address)
    shared.locator("#base-hike-list li").first.wait_for()
    assert shared.locator("#base-hike-list li").count() == 3
    assert shared.get_by_label("Town size", exact=True).input_value() == "ignore"

    shared.evaluate(f"() => {{ location.hash = '#base={KESWICK}&reach=60'; }}")  # a link names every setting
    shared.wait_for_function("() => document.querySelector('#base-length-max').value === ''")
    assert shared.get_by_label("Town size", exact=True).input_value() == "filter"
    assert shared.get_by_label("Town size up to").input_value() == "50000"
