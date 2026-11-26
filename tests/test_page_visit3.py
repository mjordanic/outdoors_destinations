"""The page after the third visitor check of both tools (front page, Find a hike, Find a base).

Each test names the triage item it covers: B1-B8 were bugs, C9-C17 confusing.
The Bases fixture is ``test_page_bases``'s; the place search's is ``test_page``'s (``geonames.txt``).
Needs Chromium for Playwright: ``uv run playwright install chromium``.
"""

from test_page import RIDGE_WALK_ID, WISHABLE, Api, open_page
from test_page import _close_what_a_test_opened, browser, data  # noqa: F401 (fixtures)
from test_page import server as places_server  # noqa: F401 (a fixture)
from test_page_bases import _few_hikes, exports, server  # noqa: F401 (fixtures)
from test_page_bases import SETTLE_MS, choose_base, edited, keswick_in_60, open_bases
from test_web_bases import KESWICK


def choose_hike(page, typed: str, name: str) -> None:
    page.get_by_label("1. A hike you liked").fill(typed)
    page.locator("#suggestions").get_by_role("option", name=name).first.click()
    page.locator("#liked-name").get_by_text(f"Chosen: {name}").wait_for()


def find(page) -> None:
    page.locator("#find").click()
    page.locator("#results-table tbody tr").first.wait_for()
    page.wait_for_function("() => !document.querySelector('#find').disabled")


def back(page) -> None:
    page.go_back()
    page.wait_for_timeout(200)  # the page follows the address


def start_with_a_hike(browser, server):
    page = open_page(browser, server, Api({}))
    page.get_by_role("link", name="Start with a hike").click()
    page.locator("#seed-pick").wait_for()
    return page


# B1: Back after picking a hike, finding hikes or opening a row stepped out of the tool or emptied the search.


def test_each_step_of_the_hike_search_is_a_step_back(browser, server) -> None:
    page = start_with_a_hike(browser, server)
    choose_hike(page, "Ridge", "Ridge Walk")
    find(page)
    page.locator("#results-table tbody tr").first.click()
    page.locator("#selected").wait_for()

    back(page)  # the row
    assert page.locator("#selected").is_hidden()
    assert page.locator("#results").is_visible()
    back(page)  # the list
    assert page.locator("#results").is_hidden()
    assert "Chosen: Ridge Walk" in page.locator("#liked-name").inner_text()
    back(page)  # the hike
    assert page.locator("#liked-name").inner_text() == "No hike chosen yet."
    assert page.evaluate("() => location.hash") == "#hikes"
    back(page)  # the tool
    assert page.locator("#landing").is_visible()


def test_forward_shows_the_list_again_without_asking_the_server(browser, server) -> None:
    api = Api({})
    page = open_page(browser, server, api, hash="#hikes")
    choose_hike(page, "Ridge", "Ridge Walk")
    find(page)
    back(page)

    page.go_forward()
    page.locator("#results").wait_for()
    assert len(api.ranked) == 1


# B2: a shared hike link opened with no list; the address said nothing of it.


def test_the_address_of_a_list_found_opens_it_again(browser, server) -> None:
    page = start_with_a_hike(browser, server)
    choose_hike(page, "Ridge", "Ridge Walk")
    find(page)
    address = page.evaluate("() => location.hash")
    assert address.startswith(f"#hike={RIDGE_WALK_ID}") and "&list" in address

    shared = open_page(browser, server, Api({}), hash=address)
    shared.locator("#results-table tbody tr").first.wait_for()
    assert "Ridge Walk" in shared.locator("#liked-name").inner_text()


def test_share_sends_the_list_with_the_liked_hike_only_once_it_is_found(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.locator("#liked-name").get_by_text("Chosen:").wait_for()
    link = page.locator("#liked a.hike-link")
    assert "&list" not in link.get_attribute("href")

    find(page)

    assert "&list" in link.get_attribute("href")


# B3: the place box took the top match silently (Keswick, Ontario), and said nothing of a typo.


def test_a_place_named_by_several_offers_a_choice_with_their_countries(browser, server) -> None:
    page = start_with_a_hike(browser, server)
    choose_hike(page, "Ridge", "Ridge Walk")
    page.get_by_label("Only hikes near a starting place").check()
    page.get_by_label("From", exact=True).fill("Ke")  # Keswick and Kessel
    page.get_by_role("button", name="Find place").click()

    choices = page.locator("#origin-choices")
    choices.get_by_role("button", name="Kessel").wait_for()
    assert "Germany" in choices.inner_text() and "United Kingdom" in choices.inner_text()
    assert page.locator("#origin-found").inner_text().startswith("Which place")
    # Found and waiting to be chosen, the place is not "not found".
    page.get_by_role("button", name="Find hikes").click()
    page.get_by_text("Choose one of the places under the box first.").wait_for()
    choices.get_by_role("button", name="Keswick").click()

    page.get_by_text("Start: Keswick, GB").wait_for()
    assert choices.is_hidden()


def test_a_place_typed_with_a_typo_asks_did_you_mean(browser, places_server) -> None:
    page = open_page(browser, places_server, Api({}))
    page.get_by_label("Only hikes near a starting place").check()
    page.get_by_label("From", exact=True).fill("Zagerb")
    page.get_by_role("button", name="Find place").click()

    page.get_by_text("No place named “Zagerb”. Did you mean:").wait_for()
    page.locator("#origin-choices").get_by_role("button", name="Zagreb").click()
    page.get_by_text("Start: Zagreb, HR").wait_for()


def test_a_place_not_found_says_so_under_the_box(browser, places_server) -> None:
    page = open_page(browser, places_server, Api({}))
    page.get_by_label("Only hikes near a starting place").check()
    page.get_by_label("From", exact=True).fill("Qqqqqq")
    page.get_by_role("button", name="Find place").click()

    page.get_by_text("No place named “Qqqqqq”. Check the spelling, or type lat, lon.").wait_for()


# B4: Find bases was clickable with text that was no town chosen, and said "Not updated: see the list." with none.


def test_find_bases_waits_for_a_town_chosen_from_the_list(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash="#base")
    choose_base(page, "Kes", "Keswick")
    page.wait_for_function("() => !document.querySelector('#base-find').disabled", timeout=SETTLE_MS)

    page.get_by_label("A town you stayed in").fill("zzzz")

    assert page.get_by_role("button", name="Find bases").is_disabled()
    assert page.locator("#base-search-problem").inner_text() == "Choose a town from the list, or press Enter to look it up."
    page.get_by_label("A town you stayed in").fill("Keswick")
    assert page.get_by_role("button", name="Find bases").is_enabled()


def test_a_link_to_no_town_says_nothing_of_a_list(browser, server) -> None:
    page = open_page(browser, server, Api({}))
    page.goto(f"{server}/#base=999999999&reach=30")
    page.locator("#base-problem").wait_for(timeout=SETTLE_MS)

    assert page.locator("#base-status").inner_text() == ""


# B5: the hike tool opened with step 1 scrolled out of view in its own scrolling panel.


def test_a_tool_or_mode_opens_with_its_panel_at_step_1(browser, server) -> None:
    page = open_page(browser, server, Api(WISHABLE), hash="#hikes", width=1280)
    page.set_viewport_size({"width": 1280, "height": 500})  # the panel scrolls on its own
    controls = page.locator(".controls")
    for open_it in (
        lambda: page.locator("#tool-switch").get_by_role("link", name="Find a base").click(),
        lambda: page.locator("#tool-switch").get_by_role("link", name="Find a hike").click(),
        lambda: page.get_by_role("button", name="No particular hike in mind?").click(),
    ):
        controls.evaluate("panel => { panel.scrollTop = 300; }")
        assert controls.evaluate("panel => panel.scrollTop") > 0  # step 1 out of view
        open_it()
        assert controls.evaluate("panel => panel.scrollTop") == 0


# B6: the hike map opened over the Atlantic, between Europe and the US.


def test_the_hike_map_opens_on_the_region_of_the_visitor(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash="#hikes")
    page.get_by_label("Select hike on map").check()

    center = page.evaluate("() => { const c = pick.map.getCenter(); return [c.lat, c.lng]; }")
    south, west, north, east = page.evaluate("() => state.info.regions[0].core")
    assert south <= center[0] <= north and west <= center[1] <= east


# C9, C10: the front page's words are for the front page, and its buttons are in view on a phone.


def test_the_landing_says_nothing_of_steps_or_of_what_appears_here(browser, server) -> None:
    page = open_page(browser, server, Api({}))
    text = page.locator("#landing").inner_text()

    assert "appear here" not in text and "step" not in text
    # In the tool, the steps are named, in order.
    page.get_by_role("link", name="Start with a town").click()
    intro = page.locator("#base-intro").inner_text()
    assert intro.index("step 2") < intro.index("steps 3 and 4")


def test_the_landing_buttons_are_on_a_phones_first_screen(browser, server) -> None:
    page = open_page(browser, server, Api({}), width=390, touch=True)

    for name in ("Start with a hike", "Start with a town"):
        box = page.get_by_role("link", name=name).bounding_box()
        assert box["y"] + box["height"] <= 900, name


# C11: the switch was missing on the front page; the footer said "Find hikes".


def test_the_landing_has_the_switch_and_the_footer_goes_home(browser, server) -> None:
    page = open_page(browser, server, Api({}))

    assert page.locator("#tool-switch").is_visible()
    assert page.locator("#tool-switch a[aria-current]").count() == 0
    assert page.locator("footer").get_by_role("link", name="Home").is_visible()
    page.locator("#tool-switch").get_by_role("link", name="Find a base").click()
    page.locator("#base-panel").wait_for()


# C12: the front page's buttons started a tool afresh, where the switch kept it as it was left.


def test_the_front_page_continues_each_tool_as_it_was_left(browser, server) -> None:
    page = start_with_a_hike(browser, server)
    choose_hike(page, "Ridge", "Ridge Walk")
    find(page)
    page.locator("#tool-switch").get_by_role("link", name="Find a base").click()
    keswick_in_60(page)

    page.locator("header h1 a.home").click()
    page.locator("#landing").wait_for()
    page.get_by_role("link", name="Start with a hike").click()
    assert "Chosen: Ridge Walk" in page.locator("#liked-name").inner_text()
    assert page.locator("#results").is_visible()
    page.locator("footer").get_by_role("link", name="Home").click()
    page.get_by_role("link", name="Start with a town").click()
    assert "Keswick" in page.locator("#base-chosen").inner_text()
    assert page.locator("#base-results").is_visible()


# C13: a row said how many hikes count, not against how many of the visitor's.


def test_each_base_row_says_its_hikes_beside_yours(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash="#base")
    keswick_in_60(page)

    assert "(yours: 4)" in page.locator("#base-results tbody tr").first.inner_text()
    assert "as many hikes" not in page.locator("#base-intro").evaluate("node => node.textContent")


# C15: the default town size filter was not said anywhere a list was shown.


def test_the_default_town_size_is_said_over_the_list(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#base={KESWICK}&reach=60")
    page.locator("#base-results tbody tr").first.wait_for(timeout=SETTLE_MS)

    assert page.locator("#base-chips").get_by_text("Town size up to 50,000 people (the default)").is_visible()


# C17: one button name in both hike modes, the pair's result in view, and Find hikes beside a phone's hike.


def test_both_hike_modes_name_the_button_alike(browser, server) -> None:
    page = open_page(browser, server, Api(WISHABLE), hash="#hikes")
    assert page.locator("#find").inner_text() == "Find hikes"

    page.get_by_role("button", name="No particular hike in mind?").click()

    assert page.locator("#find").inner_text() == "Find hikes"


def test_a_phone_finds_hikes_from_the_liked_hike_card(browser, server) -> None:
    api = Api({})
    page = open_page(browser, server, api, hash=f"#hike={RIDGE_WALK_ID}", width=390, touch=True)
    page.locator("#liked-name").get_by_text("Chosen:").wait_for()

    page.locator("#liked").get_by_role("button", name="Find hikes").click()

    page.locator("#results-table tbody tr").first.wait_for()
    assert len(api.ranked) == 1


def test_the_list_says_what_its_words_mean(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.locator("#liked-name").get_by_text("Chosen:").wait_for()
    find(page)

    words = page.locator("#results-words").inner_text()
    for word in ("versions", "picked", "Strict order", "var 2"):
        assert word in words, word


def test_the_base_score_help_opens_short(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#base={KESWICK}&reach=60", width=390, touch=True)
    page.locator("#base-results tbody tr").first.wait_for(timeout=SETTLE_MS)
    page.locator("#base-results tbody tr").first.locator(".help").click()

    box = page.locator("#score-help")
    box.wait_for()
    assert box.locator("details").count() >= 1
    assert box.evaluate("node => node.scrollHeight") <= 900


# C15 (ADR-0088): a row named after its area's biggest town says whose figures it shows.


def test_a_row_named_after_a_bigger_town_says_whose_figures_it_shows(browser, server) -> None:
    page = open_bases(browser, server)
    scored = []

    def named(body: dict) -> None:
        body["results"][0]["named"] = {"id": 9, "name": "Dumfries"}
        scored.append(body["results"][0])

    page.route("**/api/bases/rank", edited(named))
    keswick_in_60(page)

    row = page.locator("#base-results tbody tr").first
    first = scored[-1]
    assert row.locator(".base-name").inner_text() == "Dumfries"
    assert row.locator(".place").inner_text().startswith(f"figures for {first['name']} · ")
    assert row.locator(".figure-line").inner_text().startswith(f"{first['hike_groups']} hikes counted")
    page.locator(".base-marker").first.hover()
    tooltip = page.locator(".leaflet-tooltip").filter(has_text="1. ")
    assert tooltip.inner_text() == f"1. Dumfries (figures for {first['name']}) · {first['score']:.2f}"
