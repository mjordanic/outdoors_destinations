---
status: accepted (amends ADR-0054 and ADR-0055)
---

# A link sets every setting, and a wish's starting place stays with the wish

The final visitor test found two ways the page searched with settings the visitor had not set.

**A link opened in a tab sets every setting.** ADR-0054 let a link without a setting leave that setting as
it was, so a limit set in a tab stayed for another hike's link. A friend's link pasted into an open tab then
kept the tab's month, weights and starting place, while a reload of the same link opened with the defaults:
one link, two searches. Now a hike, composed-hike or wish link opened in a tab (typed, pasted, or gone back
or forward to) first sets every setting an address can name to its default, as the home address does
(ADR-0055), and then fills those the link names. A link opens the same search in any tab and on reload. The
units are a display choice, not a setting of the search, and stay as they are.

**A liked hike starts with "Only hikes near a starting place" off.** Leaving a wish for a liked hike (Back to
a hike you liked, or a hike picked from the search box or the map) kept the wish's place filter on, and the
list said so only in small print. The place belonged to the hike described, not the one liked, so it is now
turned off when the page leaves the wish; the place typed stays in its box for ticking again. A hike link
that names a starting place still opens with it on.

**Considered Options**:

- *Keep the place filter and show it plainly.* The weights already change with the mode (each mode keeps
  its own), so a filter that carried over was the odd one; turning it off matches them.
- *Reset only the settings the reports named (month and weights).* The starting place and grouping radius
  carried over the same way.
