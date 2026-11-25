"""What a visitor check of "Find a base" found (ADR-0084), in a headless browser over the fixture export.

The fixture is ``test_page_bases``'s: Keswick and Bethesda reach the same four hikes, Chamonix half of them
and Moab the other half. Each test names the finding it keeps fixed.
Needs Chromium for Playwright: ``uv run playwright install chromium``.
"""

import json

from test_page import Api, open_page
from test_page import _close_what_a_test_opened, browser  # noqa: F401 (fixtures)
from test_page_bases import _few_hikes, exports, server  # noqa: F401 (fixtures)
from test_page_bases import SETTLE_MS, asked, choose_base, edited, keswick_in_60, open_base, open_bases, rank
from test_web_bases import BETHESDA, KESWICK


def listed(page) -> list[str]:
    return page.locator("#base-results tbody .base-name").all_inner_texts()


def settled(page) -> None:
    """The last list or Base asked for is in."""
    page.wait_for_function("() => !document.querySelector('#base-find').disabled && !bases.settling", timeout=SETTLE_MS)


def requests_to(page, path: str) -> list:
    seen = []
    page.on("request", lambda request: seen.append(request) if path in request.url else None)
    return seen


# B1: Back undoes the visitor's last step instead of leaving the site.


def test_back_from_an_opened_town_returns_to_the_list(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    open_base(page, "Bethesda")

    page.go_back()

    page.locator("#base-results tbody tr").first.wait_for()
    assert page.locator("#base-hikes").is_hidden()
    assert page.evaluate("() => location.hash") == f"#base={KESWICK}&reach=60"


def test_a_units_switch_after_back_from_a_town_keeps_the_lists_map(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    rows = len(listed(page))
    open_base(page, "Bethesda")
    page.go_back()
    page.locator("#base-results tbody tr").first.wait_for()
    settled(page)

    page.locator("#units").select_option("imperial")

    assert page.locator("#base-hikes").is_hidden()
    assert page.locator(".base-marker").count() == rows


def test_back_after_a_changed_drive_time_shows_the_list_for_the_one_before(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    settled(page)
    page.get_by_role("radio", name="about 15 min").check()
    page.wait_for_function("() => document.querySelector('#base-note').innerText.includes('about 15 min')")

    page.go_back()

    page.wait_for_function("() => document.querySelector('#base-note').innerText.includes('about 60 min')")
    assert page.get_by_role("radio", name="about 60 min").is_checked()


def test_going_back_to_a_list_shown_before_asks_the_server_nothing(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    open_base(page, "Bethesda")
    asked_for = requests_to(page, "/api/bases/rank")

    page.go_back()
    page.locator("#base-results tbody tr").first.wait_for()

    assert asked_for == []


# B2: a refused request says so beside the list, marks the list it leaves, and ordinary use does not hit the limit.


def test_a_refused_ranking_is_said_above_the_list_which_is_marked_as_for_earlier_choices(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    settled(page)
    page.route("**/api/bases/rank", lambda route: route.fulfill(status=429, json={"detail": "Too many requests; wait a minute."}))

    page.get_by_role("radio", name="about 15 min").check()

    problem = page.locator("#base-problem")
    problem.get_by_text("Too many requests").wait_for()
    assert "earlier choices" in problem.inner_text()
    assert problem.get_by_role("button", name="Try again").is_visible()
    assert "stale" in page.locator("#base-results").get_attribute("class")
    page.unroute("**/api/bases/rank")
    problem.get_by_role("button", name="Try again").click()
    page.wait_for_function("() => document.querySelector('#base-problem').hidden")
    assert "stale" not in page.locator("#base-results").get_attribute("class")
    assert "about 15 min" in page.locator("#base-note").inner_text()


def test_a_refused_town_lookup_is_not_said_as_no_town_of_that_name(browser, server) -> None:
    page = open_bases(browser, server)
    page.route("**/api/bases/search*", lambda route: route.fulfill(status=429, json={"detail": "Too many requests"}))

    page.get_by_label("A town you stayed in").fill("Keswick")

    suggestions = page.locator("#base-suggestions")
    suggestions.get_by_text("Too many requests").wait_for()
    assert "No town" not in suggestions.inner_text()


def test_choices_changed_in_a_row_ask_for_one_list(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    settled(page)
    asked_for = requests_to(page, "/api/bases/rank")
    page.get_by_text("Which hikes count").click()

    page.get_by_label("Reaches a summit").check()
    page.locator("#base-round_trip").check()
    page.locator("#base-round_trip").uncheck()
    page.get_by_role("radio", name="about 30 min").check()
    settled(page)
    page.wait_for_timeout(300)

    assert len(asked_for) == 1
    assert json.loads(asked_for[0].post_data) == {**json.loads(asked_for[0].post_data), "reach": 30, "hike_filter": {"summit": True}}


# B3: a typed town is used, by Enter or by Find bases.


def test_enter_takes_the_first_suggestion_when_none_is_highlighted(browser, server) -> None:
    page = open_bases(browser, server)
    box = page.get_by_label("A town you stayed in")
    box.fill("Bethesda")
    page.get_by_role("option", name="Bethesda").wait_for()

    box.press("Enter")

    page.locator("#base-chosen").get_by_text("Bethesda").wait_for()


def test_find_bases_uses_the_town_typed_but_not_picked(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    settled(page)
    page.get_by_label("A town you stayed in").fill("Bethesda")

    body = asked(page, lambda: page.get_by_role("button", name="Find bases").click())

    assert body["seed"] == BETHESDA
    page.locator("#base-chosen").get_by_text("Bethesda").wait_for()


# B4: the list's count and a town's page agree on what they count.


def test_the_list_and_a_towns_page_say_what_their_counts_count(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)

    assert "4 hikes counted" in page.locator("#base-results tbody tr").first.inner_text()
    assert "start close together count once" in page.locator("#base-note").inner_text()
    open_base(page, "Bethesda")
    note = page.locator("#base-hikes-note").inner_text()
    assert "4 hikes, nearest first; 4 of them count for the score" in note


# B5: map markers carry the list's numbers, and a click opens that town.


def test_the_map_markers_carry_the_rows_numbers_and_open_the_town(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    names = listed(page)

    assert page.locator(".base-marker").all_inner_texts() == [str(i + 1) for i in range(len(names))]
    with page.expect_request("**/api/bases/detail") as request:
        page.locator(".base-marker").first.click()

    assert json.loads(request.value.post_data)["base"] == BETHESDA
    page.locator("#base-hike-list li").first.wait_for()
    assert "Keswick" in page.locator("#base-chosen").inner_text()  # still the visitor's own town


def test_the_map_opens_on_the_visitors_town_and_the_best_rows(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    settled(page)

    shown = page.evaluate("() => { const b = bases.map.getBounds(); return [b.contains([45, 7]), b.contains([47, 7])]; }")

    assert shown == [True, True]  # the visitor's town and the best row
    assert page.evaluate("() => bases.markers.length") == 4


# B6: the Units switch reaches the hike choices.


def test_miles_and_feet_label_the_ranges_and_are_asked_for_in_metres(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    settled(page)
    page.locator("#units").select_option("imperial")
    page.get_by_text("Which hikes count").click()

    assert page.locator('.base-range[data-stem="climb"] .unit').inner_text() == "ft"
    assert page.locator('.base-range[data-stem="length"] .unit').inner_text() == "mi"
    page.get_by_label("Length up to").fill("4")
    body = asked(page, lambda: page.get_by_label("Length up to").press("Tab"))

    assert body["hike_filter"] == {"length_max_km": 6.4}
    page.locator("#units").select_option("metric")
    assert page.get_by_label("Length up to").input_value() == "6.4"


def test_a_towns_hikes_say_their_length_in_the_units_shown(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    open_base(page, "Bethesda")

    facts = page.locator("#base-hike-list .hike-facts").first.inner_text()
    assert " km" in facts
    page.locator("#units").select_option("imperial")
    page.wait_for_function("() => document.querySelector('#base-hike-list .hike-facts').innerText.includes(' mi')")


# B7: a town opened from low in the list opens at its top; back to the list is where it was.


def test_a_town_opened_low_in_the_list_opens_at_its_top_and_the_list_comes_back_where_it_was(browser, server) -> None:
    page = open_bases(browser, server, width=1000)
    page.set_viewport_size({"width": 1000, "height": 400})
    keswick_in_60(page)
    settled(page)
    page.locator("#base-results tbody tr").last.scroll_into_view_if_needed()
    page.evaluate("() => window.scrollBy(0, 200)")
    scrolled = page.evaluate("() => window.scrollY")
    assert scrolled > 0

    page.locator("#base-results tbody tr").last.locator(".base-name").click()
    page.locator("#base-hike-list li").first.wait_for()

    assert page.evaluate("() => document.querySelector('#base-card').getBoundingClientRect().top") >= -1
    page.get_by_role("button", name="Back to the list").click()
    assert abs(page.evaluate("() => window.scrollY") - scrolled) <= 2


# B8: a name not found does not say the town has no hikes.


def test_a_name_not_found_is_said_as_not_found(browser, server) -> None:
    page = open_bases(browser, server)
    page.get_by_label("A town you stayed in").fill("Lundy")

    nothing = page.locator("#base-suggestions .nothing")
    nothing.wait_for()
    assert "No town found by that name" in nothing.inner_text()
    assert "has hikes" not in nothing.inner_text()


def test_a_name_typed_with_spaces_for_hyphens_is_offered(browser, server) -> None:
    page = open_bases(browser, server)
    page.get_by_label("A town you stayed in").fill("chamonix mont blanc")

    page.get_by_role("option", name="Chamonix-Mont-Blanc").wait_for()


# B9: too few hikes says how many, how many are needed, and what to change.


def test_too_few_hikes_says_how_many_and_points_at_the_hike_choices_at_the_longest_drive(browser, server) -> None:
    page = open_bases(browser, server)
    def too_few(body: dict) -> None:
        body.update(too_few=True, results=[])
        body["seed"]["hike_groups"] = 0

    page.route("**/api/bases/rank", edited(too_few))
    choose_base(page, "Kes", "Keswick")
    page.get_by_role("radio", name="about 60 min").check()
    page.get_by_text("Which hikes count").click()
    page.get_by_label("Reaches a summit").check()

    note = page.locator("#base-note")
    page.wait_for_function("() => document.querySelector('#base-note').innerText.includes('under your hike choices')", timeout=SETTLE_MS)
    text = note.inner_text()
    assert "None of your hikes within about 60 min of Keswick count" in text
    assert "at least 1 are needed" in text
    assert "loosen your hike choices" in text
    assert "longer drive" not in text  # 60 min is the longest
    assert "reaches a summit" in text


# B10: a broken link says so in plain words.


def test_a_link_to_no_town_says_the_link_may_be_old(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash="#base=99999999&reach=45")

    problem = page.locator("#base-problem")
    problem.get_by_text("link").wait_for()
    assert "Base at place" not in problem.inner_text()
    assert page.get_by_role("radio", name="about 30 min").is_checked()


# B11: a range the wrong way round or a failed look-up does not leave the old list looking current.


def test_a_range_the_wrong_way_round_is_said_beside_it_and_asks_nothing(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    settled(page)
    asked_for = requests_to(page, "/api/bases/rank")
    page.get_by_text("Which hikes count").click()

    page.get_by_label("Climb from").fill("2000")
    page.get_by_label("Climb up to").fill("500")
    page.get_by_label("Climb up to").press("Tab")
    page.wait_for_timeout(BASE_WAIT_MS)

    assert "more than" in page.locator('.base-range[data-stem="climb"] .range-error').inner_text()
    assert page.locator("#base-problem").is_visible()
    assert "stale" in page.locator("#base-results").get_attribute("class")
    assert asked_for == []


def test_a_range_the_wrong_way_round_on_a_towns_page_keeps_its_hikes_marked_and_asks_nothing(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    open_base(page, "Bethesda")
    hikes = page.locator("#base-hike-list li").count()
    asked_for = requests_to(page, "/api/bases/detail")
    page.get_by_text("Which hikes count").click()

    page.get_by_label("Climb from").fill("2000")
    page.get_by_label("Climb up to").fill("500")
    page.get_by_label("Climb up to").press("Tab")
    page.wait_for_timeout(BASE_WAIT_MS)

    assert asked_for == []
    assert page.locator("#base-hike-list li").count() == hikes
    assert "stale" in page.locator("#base-hikes").get_attribute("class")
    assert "more than" in page.locator('.base-range[data-stem="climb"] .range-error').inner_text()


def test_a_town_not_found_by_find_bases_marks_the_list_as_for_the_town_before(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    settled(page)
    page.get_by_label("A town you stayed in").fill("Lundy")
    page.get_by_role("button", name="Find bases").click()

    problem = page.locator("#base-problem")
    problem.get_by_text("No town found named").wait_for()
    assert "earlier choices" in problem.inner_text()
    assert "stale" in page.locator("#base-results").get_attribute("class")


BASE_WAIT_MS = 1_200  # longer than bases.js waits for choices to settle


# C1: towns of one name can be told apart.


def test_a_suggestion_and_the_chosen_town_say_the_big_town_it_is_near(browser, server) -> None:
    page = open_bases(browser, server)
    page.get_by_label("A town you stayed in").fill("Kes")

    option = page.get_by_role("option", name="Keswick")
    assert "near Chamonix-Mont-Blanc, 445 km" in option.inner_text()
    option.click()
    page.locator("#base-chosen").get_by_text("near Chamonix-Mont-Blanc").wait_for()


# C2: the explanation adds up to the score.


def test_fewer_hikes_is_said_as_a_cut_of_the_score_with_the_counts(browser, server) -> None:
    page = open_bases(browser, server)

    def fewer(body: dict) -> None:
        body["results"][0].update(score=0.81, hike_score=0.81, enough=0.9, hike_groups=2)

    page.route("**/api/bases/rank", edited(fewer))
    keswick_in_60(page)

    why = page.locator("#base-results tbody tr").first.locator(".why").inner_text()
    assert "hikes alike 0.90" in why
    assert "fewer hikes than Keswick (2 against 4) −10 %" in why


# C3: the visitor's own months, and what mild means.


def test_the_visitors_own_months_and_what_mild_means_are_shown(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)

    mine = page.locator("#base-mine")
    assert "Your town, Keswick" in mine.inner_text()
    assert mine.locator(".months li.mild").count() == 12
    assert "8.0 °C to 22.0 °C" in mine.inner_text()
    assert "4,658 people" in mine.inner_text()


# C4: a shared link shows which choices are on.


def test_a_shared_link_opens_the_sections_holding_its_choices_and_says_them(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#base={KESWICK}&reach=60&summit&size=like-mine-important")
    page.locator("#base-results tbody tr").first.wait_for()

    hikes, town = page.locator(".base-choices").all()
    assert hikes.get_attribute("open") is not None
    assert town.get_attribute("open") is not None
    note = page.locator("#base-note").inner_text()
    assert "Your choices: reaches a summit; town size like yours (important)" in note


def test_a_like_mine_link_is_read_and_the_address_says_it_in_words(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#base={KESWICK}&reach=60&eats=like-mine-important")
    page.locator("#base-results tbody tr").first.wait_for()

    assert page.get_by_label("How much restaurants, cafes and pubs matter").input_value() == "3"
    page.get_by_label("How much restaurants, cafes and pubs matter").select_option("1")
    page.wait_for_function("() => location.hash.includes('eats=like-mine-a-little')")


def test_a_like_mine_not_in_words_is_left_out(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#base={KESWICK}&reach=60&eats=like3")
    page.locator("#base-results tbody tr").first.wait_for()

    assert page.get_by_label("Restaurants, cafes and pubs", exact=True).input_value() == "ignore"
    assert "eats" not in page.evaluate("() => location.hash")


# C5: back to the list is at the top of a town's page; leaving the mode is said so.


def test_back_to_the_list_is_above_a_towns_hikes(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    open_base(page, "Bethesda")

    top = page.get_by_role("button", name="Back to the list")
    assert top.bounding_box()["y"] < page.locator("#base-hikes h2").bounding_box()["y"]
    assert page.get_by_role("button", name="Leave Find a base").is_visible()


# C6: a map click at a wide zoom still picks a town near it, and the town box says it.


def test_a_click_on_a_wide_map_picks_a_town_within_its_reach_and_fills_the_box(browser, server) -> None:
    page = open_bases(browser, server)
    page.evaluate("() => bases.map.setView([47.0, 7.0], 6, { animate: false })")
    point = page.evaluate("() => bases.map.latLngToContainerPoint([47.2, 7.0])")  # about 22 km from Bethesda
    box = page.locator("#base-map").bounding_box()

    page.mouse.click(box["x"] + point["x"], box["y"] + point["y"])

    page.locator("#base-chosen").get_by_text("Bethesda").wait_for()
    assert page.get_by_label("A town you stayed in").input_value() == "Bethesda"


def test_choosing_a_town_by_name_clears_what_the_map_said(browser, server) -> None:
    page = open_bases(browser, server)
    page.evaluate("() => bases.map.setView([47.0, 7.0], 12)")
    point = page.evaluate("() => bases.map.latLngToContainerPoint([47.01, 7.0])")
    box = page.locator("#base-map").bounding_box()
    page.mouse.click(box["x"] + point["x"], box["y"] + point["y"])
    page.locator("#base-pin-hint").get_by_text("Bethesda is the nearest base").wait_for()

    choose_base(page, "Kes", "Keswick")

    assert "Bethesda" not in page.locator("#base-pin-hint").inner_text()


# C7: an area's other towns are explained, and one person is one person.


def test_an_area_row_says_its_nearby_towns_and_one_person_is_singular(browser, server) -> None:
    page = open_bases(browser, server)

    def one(body: dict) -> None:
        body["results"][0].update(population=1, area_bases=3)

    page.route("**/api/bases/rank", edited(one))
    keswick_in_60(page)

    row = page.locator("#base-results tbody tr").first
    assert "1 person" in row.inner_text()
    assert "1 people" not in row.inner_text()
    area = row.locator(".area")
    assert area.inner_text() == "and 2 nearby towns with much the same hikes"
    assert "share a row" in area.get_attribute("title")


# C12, C14: what round trip and length mean, and why a list is short.


def test_the_hike_choices_say_what_they_measure_and_a_short_list_says_why(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    page.get_by_text("Which hikes count").click()

    choices = page.locator("#base-hike-filter").inner_text()
    assert "Length (one way, as mapped)" in choices
    assert "Round trip (ends where it starts" in choices
    assert "3 areas: with a longer drive more towns share their hikes" in page.locator("#base-note").inner_text()


# Wishes.


def test_a_towns_hikes_say_their_length_climb_and_summit(browser, server) -> None:
    page = open_bases(browser, server)

    def with_facts(body: dict) -> None:
        body["hikes"][0].update(length_m=6200, climb_m=540, highest_m=1100, summit=True, round_trip=True)

    page.route("**/api/bases/detail", edited(with_facts))
    keswick_in_60(page)
    open_base(page, "Bethesda")

    assert page.locator("#base-hike-list .hike-facts").first.inner_text() == "6.2 km · ↑ 540 m · top 1,100 m · summit · round trip"


def test_a_row_shows_the_places_to_stay_when_that_trait_is_chosen(browser, server) -> None:
    page = open_bases(browser, server)

    def counts(body: dict) -> None:
        body["results"][0].update(stays=12, eats=40)
        body["seed"].update(stays=30, eats=90)

    page.route("**/api/bases/rank", edited(counts))
    keswick_in_60(page)
    assert "places to stay" not in page.locator("#base-results tbody tr").first.inner_text()
    assert "30 places to stay · 90 to eat" in page.locator("#base-mine").inner_text()  # the visitor's own, always
    page.get_by_text("What the town is like").click()

    page.get_by_label("Places to stay", exact=True).select_option("like_mine")

    page.wait_for_function("() => document.querySelector('#base-results tbody tr').innerText.includes('12 places to stay')")


def test_more_than_a_hundred_rows_are_shown_a_hundred_at_a_time(browser, server) -> None:
    page = open_bases(browser, server)

    def many(body: dict) -> None:
        body["results"] = [{**body["results"][i % len(body["results"])], "id": 1000 + i} for i in range(150)]

    page.route("**/api/bases/rank", edited(many))
    keswick_in_60(page)
    assert page.locator("#base-results tbody tr").count() == 100

    page.get_by_role("button", name="Show 50 more").click()

    assert page.locator("#base-results tbody tr").count() == 150
    assert page.get_by_role("button", name="Show").is_hidden()


def test_the_range_boxes_say_any_when_empty(browser, server) -> None:
    page = open_bases(browser, server)
    page.get_by_text("Which hikes count").click()

    assert page.get_by_label("Climb from").get_attribute("placeholder") == "any"


def test_months_ticked_are_kept_when_like_mine_is_tried_and_left(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    page.get_by_text("What the town is like").click()
    months = page.get_by_label("Best months", exact=True)
    months.select_option("filter")
    assert page.locator(".months-none").is_visible()  # nothing ticked yet: it says what to do
    page.get_by_label("Jul", exact=True).check()
    page.get_by_label("Aug", exact=True).check()

    months.select_option("like_mine")
    body = asked(page, lambda: months.select_option("filter"))

    assert body["traits"]["months"] == {"mild_in": [7, 8]}


def test_a_hike_followed_from_a_town_has_a_way_back_to_it(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    open_base(page, "Bethesda")
    address = page.evaluate("() => location.hash")
    page.locator("#base-hike-list li").first.get_by_role("link").click()
    page.locator("#base-panel").wait_for(state="hidden")

    page.get_by_role("button", name="Back to Find a base").click()

    page.locator("#base-hike-list li").first.wait_for()
    assert page.evaluate("() => location.hash") == address


def test_a_weak_best_score_is_said(browser, server) -> None:
    page = open_bases(browser, server)

    def weak(body: dict) -> None:
        for row in body["results"]:
            row.update(score=0.39)

    page.route("**/api/bases/rank", edited(weak))
    keswick_in_60(page)

    assert "Even the best town is not much alike (0.39)" in page.locator("#base-note").inner_text()


def test_the_months_strip_stays_on_one_line_on_a_phone(browser, server) -> None:
    page = open_bases(browser, server, width=390)
    keswick_in_60(page)

    strip = page.locator("#base-results tbody tr").first.locator(".months")
    cells = strip.locator("li")
    assert cells.first.bounding_box()["y"] == cells.last.bounding_box()["y"]
    assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
    last = cells.last.bounding_box()
    assert last["x"] + last["width"] <= 390  # not cut off by the table's edge
    why = page.locator("#base-results tbody tr").first.locator(".why").bounding_box()
    assert why["width"] > 250  # the explanation reads across the row, not down a narrow column


def test_every_choice_has_a_name_for_the_browser(browser, server) -> None:
    page = open_bases(browser, server)

    assert page.evaluate("() => [...document.querySelectorAll('#base-panel input, #base-panel select')].filter((e) => !e.name && !e.id).length") == 0
