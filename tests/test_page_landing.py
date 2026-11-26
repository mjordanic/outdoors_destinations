"""The landing page of a site with Bases: two tools side by side, "Find a hike you like" and "Find a hiking
base you like", and a switch at the top of each to the other. A link into a tool skips the landing.

The fixture is ``test_page_bases``'s, with Bases, and its plain export without them.
Needs Chromium for Playwright: ``uv run playwright install chromium``.
"""

from test_page import RIDGE_WALK_ID, WISHABLE, Api, open_page
from test_page import _close_what_a_test_opened, browser  # noqa: F401 (fixtures)
from test_page_bases import _few_hikes, exports, plain_server, server  # noqa: F401 (fixtures)
from test_page_bases import choose_base, keswick_in_60
from test_web_bases import KESWICK

HIKE_TEXT = (
    "Guidebooks list hikes by region, but a great day out is about the kind of hike: how steep, how long, how wild,"
    " how high. HikesAlike starts from a hike you already know and finds others that feel the same, wherever they are."
)
BASE_TEXT = "A good hiking holiday depends on where you stay."


def landing(page) -> bool:
    return page.locator("#landing").is_visible()


def test_a_site_with_bases_opens_on_a_landing_of_both_tools(browser, server) -> None:
    page = open_page(browser, server, Api({}))

    assert landing(page)
    assert page.get_by_role("heading", name="Find a hike you like").is_visible()
    assert page.get_by_role("heading", name="Find a hiking base you like").is_visible()
    assert HIKE_TEXT in page.locator("#landing").inner_text()
    assert BASE_TEXT in page.locator("#landing").inner_text()
    assert page.locator("#seed-pick").is_hidden()
    assert page.locator("#base-panel").is_hidden()
    assert page.locator("#tool-switch").is_hidden()  # the landing is itself the choice


def test_the_landing_opens_find_a_base_and_back_returns_to_it(browser, server) -> None:
    page = open_page(browser, server, Api({}))

    page.get_by_role("link", name="Start with a town").click()

    page.locator("#base-panel").wait_for()
    assert not landing(page)
    assert page.evaluate("() => location.hash").startswith("#base")
    assert BASE_TEXT in page.locator("#base-intro").inner_text()  # the tool says what it does
    assert page.get_by_role("link", name="Find a base").get_attribute("aria-current") == "page"
    page.go_back()
    page.locator("#landing").wait_for()
    assert page.locator("#base-panel").is_hidden()


def test_the_landing_opens_find_a_hike(browser, server) -> None:
    page = open_page(browser, server, Api({}))

    page.get_by_role("link", name="Start with a hike").click()

    page.locator("#seed-pick").wait_for()
    assert not landing(page)
    assert page.evaluate("() => location.hash") == "#hikes"
    assert HIKE_TEXT in page.locator("#liked").inner_text()
    assert page.get_by_role("link", name="Find a hike").get_attribute("aria-current") == "page"
    page.reload()  # the hike search with nothing chosen keeps its own address
    page.locator("#seed-pick").wait_for()
    assert not landing(page)


def test_the_switch_at_the_top_goes_to_the_other_tool_each_as_it_was_left(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#hike={RIDGE_WALK_ID}")
    page.locator("#liked-name").get_by_text("Chosen:").wait_for()
    hike = page.evaluate("() => location.hash")

    page.locator("#tool-switch").get_by_role("link", name="Find a base").click()
    page.locator("#base-panel").wait_for()
    keswick_in_60(page)
    page.locator("#tool-switch").get_by_role("link", name="Find a hike").click()

    assert page.locator("#seed-pick").is_visible()
    assert page.locator("#base-panel").is_hidden()
    assert page.evaluate("() => location.hash") == hike  # the hike shown before
    page.locator("#tool-switch").get_by_role("link", name="Find a base").click()
    assert page.evaluate("() => location.hash") == f"#base={KESWICK}&reach=60"  # the town chosen before
    assert "Keswick" in page.locator("#base-chosen").inner_text()


def test_a_link_into_a_tool_skips_the_landing(browser, server) -> None:
    for hash in (f"#base={KESWICK}&reach=60", f"#hike={RIDGE_WALK_ID}", "#hikes"):
        page = open_page(browser, server, Api({}), hash=hash)

        assert not landing(page), hash
        assert page.locator("#tool-switch").is_visible(), hash


def test_the_hike_search_with_nothing_chosen_keeps_its_own_address(browser, server) -> None:
    page = open_page(browser, server, Api(WISHABLE), hash="#hikes")

    page.get_by_role("button", name="No particular hike in mind?").click()
    page.get_by_role("button", name="Back to a hike you liked").click()

    assert page.evaluate("() => location.hash") == "#hikes"


def test_the_town_intro_goes_once_a_town_is_chosen(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash="#base")
    assert page.locator("#base-intro").is_visible()

    choose_base(page, "Kes", "Keswick")

    assert page.locator("#base-intro").is_hidden()


def test_a_site_without_bases_opens_on_the_hike_search(browser, plain_server) -> None:
    page = open_page(browser, plain_server, Api({}))

    assert not landing(page)
    assert page.locator("#seed-pick").is_visible()
    assert page.locator("#tool-switch").is_hidden()


def test_the_landing_and_the_switch_fit_a_phone(browser, server) -> None:
    page = open_page(browser, server, Api({}), width=390, touch=True)

    assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
    page.get_by_role("link", name="Start with a town").click()
    page.locator("#base-panel").wait_for()
    assert page.locator("#tool-switch").is_visible()
    assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
