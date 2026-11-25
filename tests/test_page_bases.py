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
from dayhike.serving import ServingData, export_serving
from dayhike.web import create_app
from test_page import Api, browser, open_page, serve  # noqa: F401 (browser: a fixture)
from test_page import _close_what_a_test_opened  # noqa: F401 (an autouse fixture)
from test_web_bases import BETHESDA, KESWICK, _with_bases

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.osm"


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
    yield from serve(create_app(ServingData(exports[1]), rank_rate=1_000, api_rate=100_000))


@pytest.fixture(scope="module")
def plain_server(exports: tuple[Path, Path]) -> Iterator[str]:
    yield from serve(create_app(ServingData(exports[0]), rank_rate=1_000, api_rate=100_000))


@pytest.fixture(autouse=True)
def _few_hikes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixture export has four hikes: a Base needs one HikeGroup, not ten."""
    monkeypatch.setattr("dayhike.bases.MIN_HIKE_GROUPS", 1)


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
    page.get_by_role("button", name="Find bases").click()
    page.locator("#base-results tbody tr").first.wait_for()
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
    page.get_by_role("button", name="Back to finding hikes").click()
    assert page.locator("#base-panel").is_hidden()
    assert page.locator("#seed-pick").is_visible()
    assert page.locator("#liked").is_visible()


def test_a_town_searched_for_lists_the_bases_alike_best_first(browser, server) -> None:
    page = open_bases(browser, server)
    assert page.get_by_role("button", name="Find bases").is_disabled()

    choose_base(page, "Kes", "Keswick")
    page.get_by_role("radio", name="about 60 min").check()
    body = asked(page, lambda: rank(page))

    assert body == {"seed": KESWICK, "reach": 60}
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
    choose_base(page, "Kes", "Keswick")
    page.get_by_role("radio", name="about 60 min").check()
    page.get_by_role("button", name="Find bases").click()
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
    assert "hikes 1.00" in best.locator(".why").inner_text()
    best.get_by_role("button", name="What the base score means").click()
    box = page.locator("#score-help")
    assert box.is_visible()
    assert "Base score" in box.locator("h3").inner_text()
    assert "40 %" in box.inner_text()  # the mix's share of the hike score
    page.keyboard.press("Escape")
    assert box.is_hidden()


def test_a_trait_that_took_off_the_score_is_said_in_the_row(browser, server) -> None:
    page = open_bases(browser, server)

    def with_cuts(route) -> None:
        body = route.fetch().json()
        body["results"][0].update(score=0.8, hike_score=0.9, cuts={"size": 0.11, "stays": 0.0})
        route.fulfill(json=body)

    page.route("**/api/bases/rank", with_cuts)
    choose_base(page, "Kes", "Keswick")
    rank(page)

    why = page.locator("#base-results tbody tr").first.locator(".why").inner_text()
    assert "hikes 0.90" in why
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

    page.evaluate("() => bases.map.setView([47.5, 7.0], 12)")
    pin(47.5, 7.0)  # about 55 km from Bethesda and Chamonix
    page.locator("#base-pin-hint").get_by_text("No base within 5 km").wait_for()
    assert "Bethesda" in page.locator("#base-chosen").inner_text()  # still the one chosen


def test_too_few_hikes_to_compare_is_said_and_nothing_is_listed(browser, server) -> None:
    page = open_bases(browser, server)
    page.route("**/api/bases/rank", lambda route: route.fulfill(json={**route.fetch().json(), "too_few": True, "results": []}))
    choose_base(page, "Kes", "Keswick")
    page.get_by_role("button", name="Find bases").click()

    page.locator("#base-results").get_by_text("Too few of your hikes").wait_for()
    assert page.locator("#base-results tbody tr").count() == 0
