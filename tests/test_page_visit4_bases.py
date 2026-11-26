"""Find a base after the fourth visitor check (round 4: holiday H, phone P and breaker B testers).

Each test names the triage item it covers (3-16) and the testers' findings it comes from.
The Bases fixture is ``test_page_bases``'s: Keswick and Bethesda reach the same four hikes, Chamonix half of them.
Needs Chromium for Playwright: ``uv run playwright install chromium``.
"""

import time

import pytest

from test_page import Api, open_page
from test_page import _close_what_a_test_opened, browser  # noqa: F401 (fixtures)
from test_page_bases import _few_hikes, exports, server  # noqa: F401 (fixtures)
from test_page_bases import SETTLE_MS, asked, choose_base, keswick_in_60, open_base, open_bases, rank
from test_page_bases_round2 import hike_choices, phone, town_choices
from test_page_bases_visit import settled
from test_web_bases import KESWICK


def slow_rankings(monkeypatch: pytest.MonkeyPatch, seconds: float) -> None:
    """Every BaseRanking takes ``seconds`` longer, as a 60-min one on a busy server does."""
    from dayhike import bases

    real = bases.rank_bases

    def slow(*args, **kwargs):
        time.sleep(seconds)
        return real(*args, **kwargs)

    monkeypatch.setattr(bases, "rank_bases", slow)


def click_map(page, lat: float, lon: float) -> None:
    point = page.evaluate("([lat, lon]) => bases.map.latLngToContainerPoint([lat, lon])", [lat, lon])
    box = page.locator("#base-map").bounding_box()
    page.mouse.click(box["x"] + point["x"], box["y"] + point["y"])


# 8 (H3, P5, B5, B6): "Colwyn Bay figures for Bethesda": the link named one town and opened another, with its people.


def test_a_row_is_named_after_the_town_it_is_scored_for_and_opens(browser, server, monkeypatch) -> None:
    monkeypatch.setattr("dayhike.bases.AREA_REACHES", 100)  # Bethesda and the bigger Chamonix in one area
    page = open_bases(browser, server)
    keswick_in_60(page)

    table = page.locator("#base-results tbody")
    assert "figures for" not in table.inner_text()
    first = table.locator("tr").first
    assert first.locator(".base-name").inner_text() == "Bethesda"  # the best, scored as itself
    assert "4,735 people" in first.inner_text()  # Bethesda's own
    assert "Chamonix-Mont-Blanc" in first.locator(".area").inner_text()  # the bigger town nearby, listed under it
    assert page.evaluate("() => bases.markers[1].getTooltip().getContent()").startswith("1. Bethesda ·")  # its map dot too

    open_base(page, "Bethesda")

    assert "Bethesda" in page.locator("#base-hikes h2").inner_text()


def test_hamlets_are_left_out_by_a_default_town_size_of_at_least_1000_shown_over_the_list(browser, server) -> None:
    page = open_bases(browser, server)
    choose_base(page, "Kes", "Keswick")

    body = asked(page, lambda: rank(page))

    assert body["traits"] == {"size": {"at_least": 1_000, "up_to": 50_000}}
    chips = page.locator("#base-chips").inner_text()
    assert "Town size at least 1,000 people (the default)" in chips
    assert "Town size up to 50,000 people (the default)" in chips
    assert "size=" not in page.evaluate("() => location.hash")  # the default needs no word in the address

    town_choices(page)  # and it can be changed
    box = page.locator("#base-size-at_least")
    assert box.input_value() == "1000"
    body = asked(page, lambda: box.fill(""))
    assert body["traits"] == {"size": {"up_to": 50_000}}
    settled(page)
    assert "at least 1,000" not in page.locator("#base-chips").inner_text()
    assert "&size=-50000" in page.evaluate("() => location.hash")


# 3 (H2, B11): about 5 quick changes in 1.5 s ran into "Too many requests; wait a minute", and the list vanished.


def test_quick_changes_while_a_list_is_found_send_only_the_latest(browser, server, monkeypatch) -> None:
    slow_rankings(monkeypatch, 1.2)
    page = open_bases(browser, server)
    keswick_in_60(page)
    statuses: list[int] = []
    page.on("response", lambda response: statuses.append(response.status) if "/api/bases/rank" in response.url else None)

    for reach in ("15", "30", "60", "15"):
        page.get_by_role("radio", name=f"about {reach} min").check()
        page.wait_for_timeout(700)  # past the settle wait: each change asks while the last still runs
    settled(page)
    page.locator("#base-note").get_by_text("within about 15 min").wait_for(timeout=SETTLE_MS)

    assert 409 not in statuses  # none sent while this tab's last still ran, so none asked again and again
    assert len(statuses) <= 3


def test_a_refused_ranking_says_the_site_is_busy_and_keeps_the_list(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    settled(page)
    page.route("**/api/bases/rank", lambda route: route.fulfill(status=429, json={"detail": "Too many requests"}))

    page.get_by_role("radio", name="about 15 min").check()

    problem = page.locator("#base-problem")
    problem.get_by_text("busy").wait_for(timeout=SETTLE_MS)
    text = problem.inner_text()
    assert "try again in a moment" in text.lower()
    assert "Too many" not in text and "wait a minute" not in text
    assert page.locator("#base-results tbody tr").first.is_visible()


# 7 (B17): climb "from 800", then "up to 100": the address dropped the climb while the list still was for it.


def test_a_choice_made_wrong_leaves_the_address_naming_the_list_shown(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    hike_choices(page)
    page.locator("#base-climb-min").fill("800")
    settled(page)
    page.wait_for_function("() => location.hash.includes('climb_min_m=800')")
    before = page.evaluate("() => location.hash")

    page.locator("#base-climb-max").fill("100")
    page.locator(".base-range .range-error").first.get_by_text("is more than").wait_for()
    page.wait_for_timeout(1_200)  # past the typing wait

    assert page.evaluate("() => location.hash") == before
    assert "earlier choices" in page.locator("#base-problem").inner_text()


# 7 (B14): a link to a town of no number showed "Back to the list" and "Return to the list" together.


def test_a_link_to_no_town_opened_shows_one_button_back_to_the_list(browser, server) -> None:
    page = open_page(browser, server, Api({}), hash=f"#base={KESWICK}&reach=30&open=123")
    page.locator("#base-hikes-note.error, #base-hikes-note:has-text('could not')").first.wait_for(timeout=SETTLE_MS)

    assert page.locator(".base-hikes-close:visible").count() == 1


# 9 (H4, B19): the old 30-min list stayed on screen, not greyed, while the 60-min one was found.


def test_a_list_being_found_again_greys_the_old_one_and_says_what_comes(browser, server, monkeypatch) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    settled(page)
    slow_rankings(monkeypatch, 1.5)

    choose_base(page, "Beth", "Bethesda")  # a town chosen asks at once, with no wait to settle

    results = page.locator("#base-results")
    assert "stale" in results.get_attribute("class")
    updating = page.locator("#base-updating")
    assert updating.is_visible() and "Bethesda" in updating.inner_text() and "60 min" in updating.inner_text()
    page.wait_for_function("() => !document.querySelector('#base-results').classList.contains('stale')", timeout=SETTLE_MS)
    assert updating.is_hidden()


# 11 (H6, B10, P15): "Keswick GB" here, "Keswick, United Kingdom" in Find a hike, "Keswick, CA" read as California.


def test_countries_are_named_in_full_as_in_find_a_hike(browser, server) -> None:
    page = open_bases(browser, server)
    page.get_by_label("A town you stayed in").fill("Kes")
    option = page.get_by_role("option", name="Keswick")
    assert "United Kingdom" in option.inner_text()

    option.click()

    assert "United Kingdom" in page.locator("#base-chosen").inner_text()
    rank(page)
    assert "United Kingdom" in page.locator("#base-results tbody tr").first.inner_text()


# 12 (H9): a click on the map while looking at the list replaced the town.


def test_a_map_click_over_a_list_offers_the_town_and_keeps_yours_until_chosen(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)
    settled(page)
    page.evaluate("() => bases.map.setView([47.0, 7.0], 12, { animate: false })")

    click_map(page, 47.01, 7.0)  # about 1 km from Bethesda

    offer = page.locator("#base-pin-hint").get_by_role("button", name="Choose Bethesda")
    offer.wait_for()
    assert "Keswick" in page.locator("#base-chosen").inner_text()
    assert "Bethesda" in page.locator("#base-results tbody").inner_text()  # the list is still Keswick's

    offer.click()

    page.locator("#base-chosen").get_by_text("Bethesda").wait_for()


# 15 (P6, P9): the row's score said "same kinds of hike in the same shares 0.89, this town has 95 % of your kinds".


def test_a_rows_score_is_explained_in_plain_words(browser, server) -> None:
    page = open_bases(browser, server)
    keswick_in_60(page)

    why = page.locator("#base-results tbody tr .why").first.inner_text()

    assert "same kinds of hike in the same shares" not in why
    assert "of your kinds of hike are here" in why


# 16 (P24): "Find hikes like this" on a town's page left the hike search scrolled where the town's list was.


def test_find_hikes_like_this_opens_the_hike_search_at_its_top(browser, server) -> None:
    page = phone(browser, server)
    keswick_in_60(page)
    open_base(page, "Bethesda")
    page.evaluate("() => window.scrollTo(0, document.querySelector('#base-hike-list').getBoundingClientRect().top + scrollY - 100)")
    assert page.evaluate("() => scrollY") > 200

    page.locator("#base-hike-list a").first.click()

    page.wait_for_function("() => location.hash.startsWith('#hike=')")
    page.locator("#liked-name").get_by_text("Chosen:").wait_for(timeout=SETTLE_MS)
    page.wait_for_timeout(300)
    assert page.evaluate("() => scrollY") == 0
