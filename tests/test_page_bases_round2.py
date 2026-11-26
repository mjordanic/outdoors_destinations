"""What the second visitor check of "Find a base" found (ADR-0086), in a headless browser over the fixture export.

The fixture is ``test_page_bases``'s: Keswick and Bethesda reach the same four hikes, Chamonix half of them
and Moab the other half. Each test names the finding it keeps fixed: the breaker's (B), phone visitor's (P),
holiday visitor's (H), novice's (N) and analyst's (A) numbers in their round-2 reports.
Needs Chromium for Playwright: ``uv run playwright install chromium``.
"""

from test_page import open_page, Api
from test_page import _close_what_a_test_opened, browser  # noqa: F401 (fixtures)
from test_page_bases import _few_hikes, exports, server  # noqa: F401 (fixtures)
from test_page_bases import SETTLE_MS, choose_base, edited, keswick_in_60, open_base, open_bases
from test_web_bases import BETHESDA, KESWICK


def settled(page) -> None:
    page.wait_for_function("() => !document.querySelector('#base-find').disabled && !bases.settling", timeout=SETTLE_MS)


def hike_choices(page) -> None:
    page.get_by_text("Which hikes count").click()


def town_choices(page) -> None:
    page.get_by_text("What the town is like").click()


def opened_link(browser, server: str, hash: str, **options):
    page = open_page(browser, server, Api({}), hash=hash, **options)
    page.locator("#base-panel").wait_for()
    return page


# B20: a range the wrong way round is left out of the address, which keeps its town.


def test_a_range_the_wrong_way_round_is_left_out_of_the_address(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    hike_choices(page)
    page.locator("#base-climb-max").fill("500")
    settled(page)
    page.wait_for_function("() => location.hash.includes('climb_max_m=500')")
    before = page.evaluate("() => location.hash")

    page.locator("#base-climb-min").fill("2000")
    page.locator(".base-range .range-error").first.get_by_text("is more than").wait_for()
    page.wait_for_timeout(1_200)  # past the typing wait

    assert page.evaluate("() => location.hash") == before.replace("&climb_max_m=500", "")
    page.reload()
    page.locator("#base-results tbody tr").first.wait_for(timeout=SETTLE_MS)
    assert "Keswick" in page.locator("#base-chosen").inner_text()


def test_a_town_picked_after_a_range_the_wrong_way_round_is_in_the_address(browser, server) -> None:
    page = open_bases(browser, server)
    hike_choices(page)
    page.locator("#base-climb-min").fill("2000")
    page.locator("#base-climb-max").fill("500")
    page.locator(".base-range .range-error").first.get_by_text("is more than").wait_for()

    choose_base(page, "Kes", "Keswick")

    page.wait_for_function(f"() => location.hash.includes('base={KESWICK}&')")
    assert "climb_" not in page.evaluate("() => location.hash")
    page.reload()
    page.locator("#base-chosen").get_by_text("Keswick").wait_for()


def test_a_link_to_another_town_in_other_units_with_a_wrong_range_keeps_its_town(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    other = page.evaluate("() => state.units === 'metric' ? 'imperial' : 'metric'")

    page.evaluate(f"() => {{ location.hash = '#base={BETHESDA}&reach=60&climb_min_m=2000&climb_max_m=500&units={other}'; }}")
    page.locator("#base-chosen").get_by_text("Bethesda").wait_for()
    page.wait_for_timeout(500)

    assert f"base={BETHESDA}&" in page.evaluate("() => location.hash")


def test_a_link_with_a_range_the_wrong_way_round_still_names_its_town(browser, server) -> None:
    page = opened_link(browser, server, f"#base={KESWICK}&reach=60&climb_min_m=2000&climb_max_m=500")

    page.locator("#base-chosen").get_by_text("Keswick").wait_for()
    page.locator(".base-range .range-error").first.get_by_text("is more than").wait_for()


def test_a_town_size_the_wrong_way_round_is_said_beside_it_in_words(browser, server) -> None:
    """B21: "at least 60000.0 is more than up to 50000.0", far from the field and with no name."""
    page = open_bases(browser, server)
    keswick_in_60(page)
    town_choices(page)

    page.locator("#base-size-at_least").fill("60000")

    error = page.locator('.base-trait[data-trait="size"] .trait-error')
    error.get_by_text("Town size: “at least 60000” is more than “up to 50000”.").wait_for()
    assert ".0" not in page.locator("#base-problem").inner_text()


def test_a_negative_number_of_places_to_stay_is_said(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    town_choices(page)
    page.locator('select[name="base-stays"]').select_option("filter")

    page.locator("#base-stays-at_least").fill("-5")

    page.locator('.base-trait[data-trait="stays"] .trait-error').get_by_text("type a number of 0 or more").wait_for()


# B9: a shared link names its town while rankings are refused.


def test_a_shared_link_names_its_town_while_rankings_are_refused(browser, server) -> None:
    page = open_page(browser, server, Api({}))
    page.route("**/api/bases/rank", lambda route: route.fulfill(status=429, json={"detail": "Too many requests"}))
    page.goto(f"{server}/#base={KESWICK}&reach=60")

    page.locator("#base-problem").get_by_text("Too many requests").wait_for()
    page.locator("#base-chosen").get_by_text("Keswick").wait_for()
    assert "near" in page.locator("#base-chosen").inner_text()  # C1: the "near" part is kept after a reload


# H36, N11: a link to no town said only "No town chosen yet" in the town box, its reason far below.


def test_a_link_to_no_town_says_so_in_the_town_box(browser, server) -> None:
    page = open_page(browser, server, Api({}))
    page.goto(f"{server}/#base=999999999&reach=30")

    page.locator("#base-chosen").get_by_text("link may be old or mistyped").wait_for(timeout=SETTLE_MS)
    assert "No town chosen yet" not in page.locator("#base-chosen").inner_text()


# B12: a setting in a link that is no valid one keeps the default, and "ignore" is said.


def test_a_town_size_in_a_link_that_is_no_setting_keeps_the_default(browser, server) -> None:
    page = opened_link(browser, server, f"#base={KESWICK}&reach=60&size=zzz")
    page.locator("#base-results tbody tr").first.wait_for(timeout=SETTLE_MS)

    assert page.locator('select[name="base-size"]').input_value() == "filter"
    assert page.locator("#base-size-up_to").input_value() == "50000"


def test_a_town_size_ignored_is_said_in_the_lists_note(browser, server) -> None:
    page = opened_link(browser, server, f"#base={KESWICK}&reach=60&size=ignore")
    page.locator("#base-results tbody tr").first.wait_for(timeout=SETTLE_MS)

    assert "any town size" in page.locator("#base-note").inner_text()


# B11: the units are kept in the address, and a number typed in feet stays the number typed.


def test_the_units_are_kept_across_a_reload_with_the_number_typed(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    page.locator("#units").select_option("imperial")
    hike_choices(page)
    page.locator("#base-climb-max").fill("10000")  # the fixture's hikes climb more than 1,000 ft
    settled(page)
    page.wait_for_function("() => location.hash.includes('climb_max_m=3048') && location.hash.includes('units=imperial')")

    page.reload()
    page.locator("#base-results tbody tr").first.wait_for(timeout=SETTLE_MS)

    assert page.locator("#units").input_value() == "imperial"
    assert page.locator("#base-climb-max").input_value() == "10000"  # not 3048 m


def test_a_number_switched_to_feet_and_back_is_the_number_typed(browser, server) -> None:
    """A11: 500 m to feet and back became 499.9 m."""
    page = open_bases(browser, server)
    keswick_in_60(page)
    hike_choices(page)
    page.locator("#base-climb-min").fill("500")
    settled(page)

    page.locator("#units").select_option("imperial")
    page.locator("#units").select_option("metric")
    settled(page)

    assert page.locator("#base-climb-min").input_value() == "500"
    assert "climb_min_m=500&" in page.evaluate("() => location.hash") + "&"


# B4: a town filter no town passes says so.


def test_a_town_filter_no_town_passes_says_so_and_names_it(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    town_choices(page)
    page.locator('select[name="base-stays"]').select_option("filter")

    page.locator("#base-stays-at_least").fill("99999")

    note = page.locator("#base-note")
    note.get_by_text("No town passes your town choices").wait_for()
    assert "places to stay at least 99,999" in note.inner_text()
    assert "No town is alike" not in note.inner_text()


def test_no_row_for_another_reason_does_not_blame_the_town_choices(browser, server) -> None:
    """A strict hike filter, not the town size up to 50,000 always on, can leave no row (ADR-0086)."""
    page = open_bases(browser, server)
    page.route("**/api/bases/rank", edited(lambda body: body.update(results=[], traits_leave_none=False)))
    choose_base(page, "Kes", "Keswick")

    note = page.locator("#base-note")
    note.get_by_text("No town is alike").wait_for(timeout=SETTLE_MS)
    assert "No town passes your town choices" not in note.inner_text()


# B5: a guess at a name one typo away is offered as one, not taken on Enter.


def test_a_name_one_typo_away_is_asked_about_not_taken(browser, server) -> None:
    page = open_bases(browser, server)
    box = page.get_by_label("A town you stayed in")
    box.fill("Keswik")
    suggestions = page.locator("#base-suggestions")
    suggestions.get_by_text("Did you mean").wait_for(timeout=SETTLE_MS)

    box.press("Enter")
    page.wait_for_timeout(500)

    assert "Keswick" not in page.locator("#base-chosen").inner_text()
    page.get_by_text("Did you mean").first.wait_for()
    page.get_by_role("option", name="Keswick").click()
    page.locator("#base-chosen").get_by_text("Keswick").wait_for()


def test_find_bases_with_a_name_one_typo_away_asks_did_you_mean(browser, server) -> None:
    page = open_bases(browser, server)
    page.get_by_label("A town you stayed in").fill("Keswik")
    page.get_by_role("button", name="Find bases").click()

    asked = page.locator("#base-search-problem")
    asked.get_by_text("Did you mean").wait_for(timeout=SETTLE_MS)
    asked.get_by_role("button", name="Keswick").click()
    page.locator("#base-chosen").get_by_text("Keswick").wait_for()


# P14: a name not found is said under its box, not far below it.


def test_a_name_not_found_is_said_under_the_box(browser, server) -> None:
    page = open_bases(browser, server, width=390)
    page.get_by_label("A town you stayed in").fill("Qwxzzy")
    page.get_by_role("button", name="Find bases").click()

    said = page.locator("#base-search-problem")
    said.get_by_text("No town found named “Qwxzzy”").wait_for()
    box, text = page.locator("#base-search").bounding_box(), said.bounding_box()
    assert 0 < text["y"] - box["y"] < 200


# B13: several months mean all of them.


def test_several_mild_months_are_said_as_all_of_them(browser, server) -> None:
    page = opened_link(browser, server, f"#base={KESWICK}&reach=60&months=1,2")
    page.locator("#base-note").get_by_text("Your choices").wait_for(timeout=SETTLE_MS)

    assert "mild in all of Jan, Feb" in page.locator("#base-note").inner_text()


# B14: "among them" only when some are left out; A4, P19: the area's best town is named.


def test_a_rows_towns_all_listed_are_not_said_to_be_among_them_and_its_best_town_is_named(browser, server) -> None:
    page = open_bases(browser, server)

    def area(body: dict) -> None:
        body["results"][0].update(area_bases=2, area_towns=[{"id": 9, "name": "Dumfries", "population": 33_000}],
                                  best_town={"id": 9, "name": "Dumfries", "score": 0.93})

    page.route("**/api/bases/rank", edited(area))
    keswick_in_60(page)

    row = page.locator("#base-results tbody tr").first
    assert row.locator(".area").inner_text() == "and 1 nearby town with much the same hikes: Dumfries (the best of them, 0.93)"


# H14, N3: the explanation adds up to the score shown.


def test_a_cut_explanation_ends_with_the_score_it_comes_to(browser, server) -> None:
    page = open_bases(browser, server)

    def fewer(body: dict) -> None:
        body["results"][0].update(score=0.51, hike_score=0.51, enough=0.84, sure=1.0, hike_groups=2)

    page.route("**/api/bases/rank", edited(fewer))
    keswick_in_60(page)

    why = page.locator("#base-results tbody tr").first.locator(".why").inner_text()
    assert why.endswith("= 0.51")


# A13: "Only 1 of your hikes … counts".


def test_one_hike_is_said_in_the_singular(browser, server) -> None:
    page = open_bases(browser, server)

    def one(body: dict) -> None:
        body.update(too_few=True, results=[])
        body["seed"]["hike_groups"] = 1

    page.route("**/api/bases/rank", edited(one))
    choose_base(page, "Kes", "Keswick")

    page.locator("#base-note").get_by_text("Only 1 of your hikes").wait_for()
    assert "of Keswick counts:" in page.locator("#base-note").inner_text()


# A9: a "like mine" trait the visitor's own town has no data on is said to do nothing.


def test_like_mine_on_a_trait_your_town_has_no_data_on_says_so(browser, server) -> None:
    page = open_bases(browser, server)
    page.route("**/api/bases/rank", edited(lambda body: body["seed"].update(population=None)))
    choose_base(page, "Kes", "Keswick")
    town_choices(page)

    page.locator('select[name="base-size"]').select_option("like_mine")

    page.locator("#base-note").get_by_text("Keswick has no data on town size").wait_for()


# P18, A12: a short list says why without blaming the drive.


def test_a_short_list_says_why_without_blaming_the_drive(browser, server) -> None:
    page = opened_link(browser, server, f"#base={KESWICK}&reach=30")
    page.locator("#base-note").get_by_text("areas").wait_for(timeout=SETTLE_MS)

    note = page.locator("#base-note").inner_text()
    assert "towns close together share a row" in note
    assert "longer drive" not in note


# N15: a filter with nothing typed says what to type.


def test_a_town_filter_with_nothing_typed_says_what_to_type(browser, server) -> None:
    page = open_bases(browser, server)
    town_choices(page)

    page.locator('select[name="base-eats"]').select_option("filter")

    assert page.locator('.base-trait[data-trait="eats"] .filter-none').is_visible()
    page.locator("#base-eats-at_least").fill("5")
    assert page.locator('.base-trait[data-trait="eats"] .filter-none').is_hidden()


# B6, P29: a tap on the map of a town's hikes does not change the visitor's town.


def test_a_map_click_on_a_towns_page_keeps_the_visitors_town(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    open_base(page, "Bethesda")
    asked_nearest = []
    page.on("request", lambda request: asked_nearest.append(request) if "/api/bases/nearest" in request.url else None)

    page.locator("#base-map").click(position={"x": 30, "y": 30})
    page.wait_for_timeout(300)

    assert asked_nearest == []
    assert "Keswick" in page.locator("#base-chosen").inner_text()
    assert "A numbered dot" not in page.locator("#base-pin-hint").inner_text()


# B8: towns opened quickly one after another are one step of the history.


def test_towns_opened_one_after_another_quickly_are_one_step_back(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    steps = page.evaluate("() => history.length")

    page.evaluate("() => { bases.open(bases.rows[0]); bases.open(bases.rows[1]); }")
    page.locator("#base-hike-list li").first.wait_for(timeout=SETTLE_MS)

    assert page.evaluate("() => history.length") == steps + 1
    page.go_back()
    page.locator("#base-results tbody tr").first.wait_for(timeout=SETTLE_MS)


# H39, A2: a town's hikes say their facts one way, and the link is "Find hikes like this".


def test_a_towns_hikes_say_one_way_and_link_to_hikes_like_them(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    open_base(page, "Bethesda")

    first = page.locator("#base-hike-list li").first
    assert "one way" in first.locator(".hike-facts").inner_text()
    assert first.get_by_role("link", name="Find hikes like this").is_visible()


# The phone visitor's findings, at 390 px.


def phone(browser, server: str, hash: str = ""):
    page = open_page(browser, server, Api({}), width=390, touch=True, hash=hash)
    if not hash:
        page.get_by_role("link", name="Start with a town").click()
    page.locator("#base-panel").wait_for()
    return page


def in_view(page, selector: str) -> bool:
    return page.evaluate(f"() => {{ const r = document.querySelector('{selector}').getBoundingClientRect(); return r.top < innerHeight && r.bottom > 0; }}")


def test_a_town_picked_on_a_phone_scrolls_to_its_list(browser, server) -> None:
    """P2: the list loaded about 1,230 px below the form and the page did not move."""
    page = phone(browser, server)
    choose_base(page, "Kes", "Keswick")
    page.locator("#base-results tbody tr").first.wait_for(timeout=SETTLE_MS)

    page.wait_for_function("() => document.querySelector('#base-results').getBoundingClientRect().top < innerHeight / 2")


def test_a_rows_first_line_is_as_tall_as_its_text_on_a_phone(browser, server) -> None:
    """P3: the 44 px name button made each row's first line tall, with a gap under it."""
    page = phone(browser, server)
    choose_base(page, "Kes", "Keswick")
    row = page.locator("#base-results tbody tr").first
    row.wait_for(timeout=SETTLE_MS)

    name, figures = row.locator(".base-name").bounding_box(), row.locator(".figure-line").bounding_box()
    assert name["height"] >= 44  # still as big to tap
    assert figures["y"] - name["y"] < 34
    score, why = row.locator(".score").bounding_box(), row.locator(".why").bounding_box()
    assert why["y"] - score["y"] < 34


def test_the_score_help_on_a_phone_keeps_its_close_button_in_view(browser, server) -> None:
    """P6: the × covered the first line, and was out of view once the help was scrolled."""
    page = phone(browser, server)
    choose_base(page, "Kes", "Keswick")
    page.locator("#base-results tbody tr").first.wait_for(timeout=SETTLE_MS)
    page.locator("#base-results tbody tr").first.get_by_role("button", name="What the base score means").click()
    box = page.locator("#score-help")
    box.wait_for()

    close, title = box.get_by_role("button", name="Close").bounding_box(), box.locator("h3").bounding_box()
    assert close["x"] >= title["x"] + title["width"] or close["y"] + close["height"] <= title["y"] + 4
    box.evaluate("(element) => { element.scrollTop = element.scrollHeight; }")
    close, frame = box.get_by_role("button", name="Close").bounding_box(), box.bounding_box()
    assert frame["y"] - 1 <= close["y"] <= frame["y"] + frame["height"]


def test_the_month_boxes_sit_in_two_rows_on_a_phone(browser, server) -> None:
    """P23: "mild in" on its own line and the 12 months in 3 untidy rows."""
    page = phone(browser, server)
    town_choices(page)
    page.locator('select[name="base-months"]').select_option("filter")

    tops = page.evaluate("() => [...document.querySelectorAll('input[name=\"base-months\"]')].map((box) => Math.round(box.getBoundingClientRect().top))")
    assert len(set(tops)) <= 2
    right = page.evaluate("() => Math.max(...[...document.querySelectorAll('input[name=\"base-months\"]')].map((box) => box.closest('label').getBoundingClientRect().right))")
    assert right <= page.locator("#base-panel").bounding_box()["x"] + page.locator("#base-panel").bounding_box()["width"]


def test_a_tapped_month_says_its_temperature(browser, server) -> None:
    """P24: the temperatures were only in tooltips, which a touch screen does not show."""
    page = phone(browser, server)
    choose_base(page, "Kes", "Keswick")
    row = page.locator("#base-results tbody tr").first
    row.wait_for(timeout=SETTLE_MS)

    row.locator(".months li").first.click()

    row.locator(".months-said").get_by_text("Jan:").wait_for()


def test_a_shared_towns_page_opens_on_its_hikes_and_says_the_choices(browser, server) -> None:
    """P28: a shared town page opened on the form, 1,700 px above the town, and did not say its choices."""
    page = phone(browser, server, hash=f"#base={KESWICK}&reach=60&open={BETHESDA}&length_max_km=50")
    page.locator("#base-hike-list li").first.wait_for(timeout=SETTLE_MS)

    page.wait_for_function("() => document.querySelector('#base-hikes-title').getBoundingClientRect().top < innerHeight")
    assert "Your choices: length up to 50 km" in page.locator("#base-hikes-note").inner_text()


def test_back_from_a_hike_returns_to_the_towns_page_where_it_was(browser, server) -> None:
    """P31: Back from a hike opened from a town's page came back at its top."""
    page = phone(browser, server)
    keswick_in_60(page)
    open_base(page, "Bethesda")
    page.evaluate("() => window.scrollTo(0, document.querySelector('#base-hike-list').getBoundingClientRect().top + scrollY - 100)")
    where = page.evaluate("() => scrollY")
    page.locator("#base-hike-list a").first.click()
    page.wait_for_function("() => location.hash.startsWith('#hike=')")

    page.go_back()

    page.locator("#base-hike-list li").first.wait_for(timeout=SETTLE_MS)
    page.wait_for_function(f"() => Math.abs(scrollY - {where}) < 5")


def test_a_list_being_found_is_said_in_view_on_a_phone(browser, server) -> None:
    """P35: "Finding bases…" showed below the open choices, out of view of the drive-time buttons."""
    page = phone(browser, server)
    keswick_in_60(page)
    settled(page)
    page.route("**/api/bases/rank", lambda route: None)  # held: the list is being found
    hike_choices(page)
    town_choices(page)  # both open, as the visitor had them: the status sits far below the drive times
    page.locator("#base-reaches").scroll_into_view_if_needed()

    page.get_by_role("radio", name="about 15 min").check()

    page.locator("#base-status").get_by_text("Finding bases").wait_for()
    assert in_view(page, "#base-status")


def test_a_hike_dot_on_a_towns_map_is_numbered_and_shows_its_hike_in_the_list(browser, server) -> None:
    """B18, P30: the hike dots were plain, and a tap showed a tooltip only."""
    page = open_bases(browser, server)
    keswick_in_60(page)
    open_base(page, "Bethesda")

    page.evaluate("() => bases.hikeMarkers[1].fire('click')")

    item = page.locator("#base-hike-list li").nth(1)
    page.wait_for_function("() => document.querySelectorAll('#base-hike-list li')[1].classList.contains('picked')")
    assert page.evaluate("() => bases.hikeMarkers[1].getTooltip().getContent()").startswith("2. ")
    assert item.is_visible()


# H17, N5: climb from 2,000 up to 500 on a list with too few hikes was said only as "climb from 2,000 m".


def test_a_range_the_wrong_way_round_on_a_list_of_too_few_hikes_is_said(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    hike_choices(page)
    page.locator("#base-climb-min").fill("2000")
    page.locator("#base-note").get_by_text("of your hikes within about 60 min").wait_for(timeout=SETTLE_MS)

    page.locator("#base-climb-max").fill("500")

    page.locator(".base-range .range-error").first.get_by_text("“from 2000” is more than “up to 500”").wait_for()
    page.locator("#base-problem").get_by_text("“from 2000” is more than “up to 500”").wait_for(timeout=SETTLE_MS)
    assert "climb_max_m" not in page.evaluate("() => location.hash")
