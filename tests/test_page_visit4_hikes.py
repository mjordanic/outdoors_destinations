"""Find a hike after the fourth visitor check (round 4, 2026-11-26).

Each test names the triage item it covers (1-7 were bugs, 8-16 confusing) and the testers' findings
(H: holiday, P: phone, B: breaker). The place search's answers are stubbed where a name must fit several
places; every other request goes to ``test_page``'s app.
Needs Chromium for Playwright: ``uv run playwright install chromium``.
"""

from test_page import RIDGE_WALK_ID, SEARCHED, Api, _find, click_map, drawn_hike, map_dots, open_page, START
from test_page import _close_what_a_test_opened, browser, data, server  # noqa: F401 (fixtures)

VALLEY_PATH_ID = 1002


def back(page) -> None:
    page.go_back()
    page.wait_for_timeout(200)  # the page follows the address


def drive_from(page, typed: str) -> None:
    page.get_by_label("Only hikes near a starting place").check()
    page.get_by_label("From", exact=True).fill(typed)
    page.get_by_role("button", name="Find place").click()


def places(server: str, page, found: list[dict], guess: bool = False) -> None:
    """/api/place answers ``found`` (name, country, population; covered unless said)."""
    results = [{"lat": 45.3 + i / 10, "lon": 15.2, "covered": True, **place} for i, place in enumerate(found)]
    page.route(f"{server}/api/place?*", lambda route: route.fulfill(json={"results": results, "guess": guess}))


# 1 (P1, P27a): the address kept the liked hike after a similar one was opened from the list.


def test_a_hike_opened_from_the_list_is_named_by_the_address(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)
    opened = page.evaluate("state.results[0].id")

    page.locator("#results-table tbody tr").first.click()
    page.locator("#selected").wait_for()

    assert page.evaluate("location.hash") == f"#hike={RIDGE_WALK_ID}&list&open={opened}"
    back(page)  # Back still closes the row
    assert page.locator("#selected").is_hidden()
    assert page.evaluate("location.hash") == f"#hike={RIDGE_WALK_ID}&list"


def test_back_from_a_row_opened_over_a_list_of_other_settings_only_closes_it(browser, server) -> None:
    api = Api({})
    page = open_page(browser, server, api, hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)
    page.locator("#month").select_option("1")  # the list is now of other settings
    page.locator("#results.stale").wait_for()
    page.locator("#results-table tbody tr").first.click()
    page.locator("#selected").wait_for()

    back(page)

    assert page.locator("#selected").is_hidden()
    assert page.locator("#results").is_visible()
    assert page.locator("#results.stale").count() == 1
    assert page.locator("#month").input_value() == "1"
    assert len(api.ranked) == 1


def test_an_address_naming_an_opened_hike_opens_it_over_the_list(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}&list&open={VALLEY_PATH_ID}")

    page.locator("#selected h2", has_text="Valley Path").wait_for()

    assert page.locator("#results").is_visible()
    assert "Chosen: Ridge Walk" in page.locator("#liked-name").inner_text()
    assert page.evaluate("location.hash").endswith(f"&list&open={VALLEY_PATH_ID}")


# 2 (B1-B3): "Find place" took a small Frankfurt and one of many Springfields silently, and once a place
# was chosen, Find place again took it again without asking.


def test_a_much_bigger_place_starting_with_the_name_is_offered_beside_the_one_named_so(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    places(server, page, [
        {"name": "Frankfurt", "country": "DE", "population": 900},
        {"name": "Frankfurt am Main", "country": "DE", "population": 650_000},
        {"name": "Frankfurt (Oder)", "country": "DE", "population": 57_000},
    ])  # fmt: skip

    drive_from(page, "Frankfurt")

    choices = page.locator("#origin-choices button")
    choices.first.wait_for()
    names = choices.evaluate_all("buttons => buttons.map(b => b.firstChild.textContent.trim())")
    assert names == ["Frankfurt am Main", "Frankfurt (Oder)", "Frankfurt"]
    assert page.locator("#origin-found").inner_text().startswith("Which place")


def test_every_place_of_one_name_in_one_country_is_offered_told_apart(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    places(server, page, [
        {"name": "Springfield", "country": "US", "population": 169_000, "near": "Joplin"},
        {"name": "Springfield", "country": "US", "population": 155_000, "near": "Hartford"},
        {"name": "Springfield", "country": "US", "population": 114_000, "near": "Decatur"},
    ])  # fmt: skip

    drive_from(page, "Springfield")

    choices = page.locator("#origin-choices button")
    choices.first.wait_for()
    assert choices.count() == 3
    assert "near Hartford" in choices.nth(1).inner_text()


def test_find_place_again_offers_the_choice_again(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    places(server, page, [
        {"name": "Keswick", "country": "GB", "population": 4_658},
        {"name": "Keswick", "country": "US", "population": 451},
    ])  # fmt: skip
    drive_from(page, "Keswick")
    page.locator("#origin-choices button").nth(1).click()
    page.get_by_text("Start: Keswick, United States").wait_for()

    page.get_by_role("button", name="Find place").click()

    page.locator("#origin-choices button").first.wait_for()
    assert page.locator("#origin-found").inner_text().startswith("Which place")


# 4 (H5): the weather help still spoke of Summer with October chosen.


def test_the_month_help_follows_the_month_chosen(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()

    page.get_by_label("Weather month").select_option("10")

    hint = page.locator("#month-hint").inner_text()
    assert "Summer" not in hint and "October" in hint
    page.get_by_label("Weather month").select_option("")
    assert page.locator("#month-hint").inner_text().startswith("With Summer")


# 5 (H8): the map's + / − buttons covered the "choose one" box.


def test_the_choose_one_box_on_the_hike_map_is_clear_of_the_zoom_buttons(browser, server) -> None:
    day = drawn_hike("Day hike", (45.0600, 6.5200), (45.0600, 6.5300))
    stage = {**drawn_hike("Long tour, stage 4", (45.0600, 6.5100), (45.0600, 6.5300)), "walk_m": 30_000}
    page = map_dots(browser, server, [stage, day])
    # The shared trail near the map's top left corner, where the zoom buttons are.
    page.evaluate("start => { const p = pick.map.latLngToContainerPoint(start); pick.map.panBy([p.x - 70, p.y - 120], { animate: false }); }", START)
    page.wait_for_function("() => pick.shown.length === 2")

    assert click_map(page, START, 0) == []
    page.locator(".pick-offer").wait_for()
    page.wait_for_timeout(400)  # the map pans the box into view

    zoom = page.locator(".leaflet-control-zoom").first.bounding_box()
    box = page.locator(".leaflet-popup-content-wrapper").bounding_box()
    apart = (box["x"] >= zoom["x"] + zoom["width"] or box["y"] >= zoom["y"] + zoom["height"]
             or box["x"] + box["width"] <= zoom["x"] or box["y"] + box["height"] <= zoom["y"])  # fmt: skip
    assert apart, (zoom, box)


# 6 (P3): on a phone the score table was 1,639 px wide and hike names broke mid-word.


def test_on_a_phone_the_score_table_keeps_names_whole_and_its_columns_narrow(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}", width=390, touch=True)
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)

    page.get_by_role("button", name="Show the score of every measure").click()

    name = page.locator("#results-table tbody td.name").first
    assert name.evaluate("e => getComputedStyle(e.querySelector('.hike-name')).overflowWrap") != "anywhere"
    assert name.evaluate("e => e.getBoundingClientRect().width") >= 110
    widths = page.locator("#results-table th.measure").evaluate_all("heads => heads.map(h => h.getBoundingClientRect().width)")
    assert max(widths) <= 40, widths


# 10 (H7): Enter in the hike search did nothing with several hikes found; the town box picks the top one.


def test_enter_in_the_hike_search_picks_the_top_suggestion(browser, server) -> None:
    page = open_page(browser, server, Api({}))
    page.route(f"{server}/api/search?*", lambda route: route.fulfill(json={"results": SEARCHED}))
    page.route(f"{server}/api/landmarks?*", lambda route: route.fulfill(json={"results": []}))
    search = page.locator("#search")
    search.fill("ridge")
    first = page.locator("#suggestions li[role=option]").first
    first.wait_for()
    top = first.evaluate("li => li.firstChild.textContent.trim()")

    search.press("Enter")

    page.get_by_text(f"Chosen: {top}").wait_for()
    assert "(Enter picked the top suggestion)" in page.locator("#liked-name").inner_text()


# 11 (H6, B10, P15): "Start: Keswick, CA" read as California; Canada, with no hikes, was offered first.


def test_places_say_their_country_in_full_and_those_without_hikes_come_last(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    places(server, page, [
        {"name": "Keswick", "country": "CA", "population": 21_000, "covered": False},
        {"name": "Keswick", "country": "GB", "population": 4_658},
    ])  # fmt: skip

    drive_from(page, "Keswick")

    choices = page.locator("#origin-choices button")
    choices.first.wait_for()
    assert "United Kingdom" in choices.nth(0).inner_text()
    assert "Canada" in choices.nth(1).inner_text() and "no hikes mapped" in choices.nth(1).inner_text()
    choices.nth(0).click()
    page.get_by_text("Start: Keswick, United Kingdom").wait_for()


# 15 (P4, P9, H17): the list's intro was four dense paragraphs, and the loop search's result read like a log.


def test_the_list_opens_with_one_plain_sentence_and_folds_how_to_read_it(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)

    lead = page.locator("#results-note").inner_text()
    assert lead.startswith("Like “Ridge Walk”") and "picked" not in lead and "var 2" not in lead
    how = page.locator("#results-how")
    assert how.evaluate("d => d.tagName") == "DETAILS" and not how.evaluate("d => d.open")
    assert how.locator("summary").inner_text() == "How to read this list"
    assert page.locator("#results-words").is_hidden()


def test_the_loop_search_says_what_it_found_in_one_sentence_by_its_button(browser, server) -> None:
    def searched(route) -> None:
        body = route.fetch().json()
        body["loop_search"] = {"starts": 22, "found": 2, "listed": 0, "best": -1.0, "stopped": True}
        body["loops"] = True
        route.fulfill(json=body)

    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.route(f"{server}/api/rank", searched)
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)

    says = page.locator("#results-loops-says")
    says.wait_for()
    assert says.inner_text() == "2 loops found near the best hikes, none as good as the hikes listed (the search stopped at its time limit)."
    assert page.locator("#results-loops-note").is_hidden()  # the details, folded


# 16 (P10, B18, H12, B13): small items.


def test_back_to_a_hike_you_liked_looks_like_a_button_not_a_box(browser, server) -> None:
    page = open_page(browser, server, Api({"elevation_gain": {"kind": "number", "unit": "m", "min": 0, "max": 3000, "step": 50, "default": 600}}))
    page.get_by_role("button", name="No particular hike in mind?").click()

    back_button = page.get_by_role("button", name="Back to a hike you liked")
    box = page.locator("#origin")
    assert back_button.evaluate("b => getComputedStyle(b).backgroundColor") != box.evaluate("b => getComputedStyle(b).backgroundColor")


def test_every_weight_at_zero_does_not_ask_to_press_a_greyed_out_find(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()
    _find(page)

    page.evaluate("""() => document.querySelectorAll('.weight input[type=range]').forEach((input) => {
      input.value = 0; input.dispatchEvent(new Event('input', { bubbles: true })); input.dispatchEvent(new Event('change', { bubbles: true }));
    })""")

    assert page.locator("#find").is_disabled()
    assert "Give at least one measure a weight above 0" in page.locator("#find-why").inner_text()
    for said in (page.locator("#status").inner_text(), page.locator("#results-stale").inner_text()):
        assert "press" not in said.lower(), said


def test_a_hike_measured_with_its_way_back_is_not_called_a_round_trip(browser, server) -> None:
    def out_and_back(route) -> None:
        hike = route.fetch().json()
        hike["way_back"] = {"kind": "retrace", "m": 1000}
        hike["facts"]["round_trip"] = 1
        route.fulfill(json=hike)

    page = open_page(browser, server, Api({}))
    page.route(f"{server}/api/hikes/*", out_and_back)
    page.goto(f"{server}/#hike={RIDGE_WALK_ID}")
    page.get_by_text("Chosen: Ridge Walk").wait_for()

    row = page.locator("#liked table.facts tr", has_text="Round trip")
    assert "one way" in row.inner_text() and "yes" not in row.inner_text().lower().split("round trip")[1]


def test_day_hikes_are_suggested_before_multi_day_routes(browser, server) -> None:
    found = [
        {"id": 7, "name": "John Muir Trail", "multi_day": True, "walk_m": 336_000, "length_m": 336_000, "climb_m": 14_000,
         "descent_m": 14_000, "elevation_gain_m": 14_000, "highest_m": 4421, "trailhead": [36.6, -118.3], "match": "words"},
        {"id": 8, "name": "Mount Whitney Trail", "length_m": 17_000, "climb_m": 1900, "descent_m": 0,
         "elevation_gain_m": 1900, "highest_m": 4421, "trailhead": [36.6, -118.2], "match": "words"},
    ]  # fmt: skip
    page = open_page(browser, server, Api({}))
    page.route(f"{server}/api/search?*", lambda route: route.fulfill(json={"results": found}))
    page.route(f"{server}/api/landmarks?*", lambda route: route.fulfill(json={"results": []}))

    page.locator("#search").fill("Whitney")

    first = page.locator("#suggestions li[role=option]").first
    first.wait_for()
    assert first.inner_text().startswith("Mount Whitney Trail")


# Not a bug here (P2, H1, B4): "not available on this server" read as a server error.


def test_the_drive_time_note_does_not_read_as_a_server_error(browser, server) -> None:
    page = open_page(browser, server, Api({}))

    note = page.locator("#drive-off").text_content()

    assert "server" not in note and "straight-line" in note
