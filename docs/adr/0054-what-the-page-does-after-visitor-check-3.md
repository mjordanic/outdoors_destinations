---
status: accepted (amends ADR-0028, ADR-0032, ADR-0041 and ADR-0047; amended by ADR-0055; the help text's dry Lake-or-sea example and typical pair by ADR-0058)
---

# What the page does after visitor check 3

Six simulated visitors used the review site built from develop 332bd02 (rebuild 4, 258,305 hikes). They
found eleven bugs and several confusing spots. This records the decisions the fixes took. The scores and
the data are unchanged.

**One Ranking per browser tab, a few per address.** PR 99 let a client have one Ranking queued or
running, keyed by address. People behind one address (a home, an office, a phone network, and all six test
visitors on 127.0.0.1) then got "Your previous search is still running" for someone else's search. The
page now names its tab in `X-HikesAlike-Tab` (a random name per page load). The queue keys a client by
address and tab, and lets one address hold at most 4 (`PER_ADDRESS`) queued or running, so one address
still cannot fill the line. A missing or malformed name counts as none, the old key. The rate limits stay
per address. When the server answers 409 because the tab's own earlier search still runs (one the page
no longer waits for), the page says "Waiting for your earlier search to end…" and asks again every 1.5 s
for up to a minute. If it still cannot run, the list from before is hidden under the message, so it is
never read as the new search's answer.

**Links carry the search.** A hike link carried only `&oneway` and `&strict`; a wish link carried its
weights but no month or place. Both now carry the settings on the left: `&month=`, `&w=` (the weights moved
from their defaults, as `name:weight`), `&adv`, `&from=lat,lon`, `&place=`, `&hours=`, `&km=` and
`&group=`. A wish keeps its weights in `#wish=`. The address follows the settings as they change, so a
reload keeps them. Parts are read in any order and case; a part the page does not know is ignored, so a
hand-edited link still opens its hike. A link without a setting leaves that setting as it is, so old
links open as before, and a limit set in a tab stays for another hike's link. A link cut short or changed
(a hike id that is not a number, a composed route the server cannot parse) says "This link could not be
opened" beside the search box and in the hike card. Opening a link does not run the Ranking; the visitor
presses Find.

**Enter picks only a clear match.** On a phone Enter picked the first suggestion, which was often not the
hike meant; on a computer Enter before the list arrived did nothing. Now Enter, on both, waits for the list
of what is typed and picks a hike only when exactly one is named as typed (case, accents and punctuation
aside) or only one hike is found. The line beside the chosen hike says why. Otherwise the list stays open
and says how many hikes match.

**Loops for the first page only.** Each page searched loops anew near its own best hikes, so a loop found
for page 2 could outscore page 1 (rank 11 at 0.788 above rank 1 at 0.780). "Show 10 more" now asks with
`loops: false`. Suggest loops asks for as many rows as were shown, with "Show 10 more" asked again for the
rows past page 1, instead of falling back to 10. The loop note names the rows a loop is on ("#2, #5"), or,
with none listed, how the best loop found (`loop_search.best`) scores against the last row. This amends the
paging of ADR-0028 and ADR-0032. Redraw's weighting (ADR-0047) is unchanged; only the "picked" wording is
plainer: "not one of this page's 10 best areas, drawn in for variety from its best 30".

**A note when the matches are weak.** Under a tight limit the best rows scored 0.25 to 0.31, below the
0.43 a typical random pair scores, with no word of it. When a row listed scores below `TYPICAL_PAIR`
(0.43, served in `/api/info` and used by the score help), a note above the list says from which row down
the matches are weak; when even the first is, it says nothing within the limits is much like the hike. The
432 km Münchner Jakobsweg in a 0.5 h drive list is that case. The Catalog holds routes of any length by design (ADR-0033, ADR-0035; long
tours are also cut into day stages, ADR-0040), and it was listed only because nothing nearer scored better:
its rows scored 0.30 to 0.44. So it stays, and the note covers it. The help text's dry Lake-or-sea example
said 0.16 and 84 %; rebuild 4's Calibration scores two dry hikes 0.208 (79 % of random pairs as alike), and
the text now says 0.21 and 79 %.

**Edit on the map changes a copy.** Edit on the map made the loop or built hike the liked one and dropped
the list. Now the designer opens on it, zoomed in to at least the Segment zoom, while the liked hike and the
list stay until Use this hike. Undo takes back only the visitor's own clicks, never a Segment of the hike
opened. Clear starts over, and when the drawing is the liked hike itself it clears that hike too, so Find
never uses a walk no longer drawn. A built hike whose Route cuts a Segment opens the designer at its start
and says it cannot be drawn there.

**The map follows the hike.** "Select hike on map" opened on the box of every Trailhead, mostly sea. It now
opens on the liked hike (its line, at least at the zoom where hikes show), else the starting place, else
`core` in `/api/info`, the box of the middle 80 % of Trailheads. A hike picked by name while the map is open
moves it there. Where lines run together along one trail (each within 3 px of the nearest, away from its
ends), a click lists them and the hike nearest the click, the shorter first, instead of picking one. Hikes
that only meet at a point still pick the one clicked.

**Smaller fixes.** Find and a hike picked by name jump to the list or the hike's card at once (a smooth
scroll could be cut short and leave the list 3,800 px below on a phone). The score "?" box shows "More
below ↓ scroll" while its text goes on below its edge. On a phone, folded sections show an arrow and
"show", the text no longer says "on the left", the Share note floats over the map instead of pushing it
down, and a line says the result table scrolls sideways. The About page's wordmark said "160,000+ hikes";
its last line is cropped off the art (the paths otherwise unchanged, amending ADR-0041's note on the
wordmark) and the page writes it as text with the export's count, as the header does.

**Considered Options**:
- *Keep one Ranking per address and only change the message.* People sharing an address would still wait
  on strangers' searches.
- *A tab name with no per-address cap.* One address could queue any number of Rankings by inventing names.
- *Hike links that run the Ranking on open.* A link would cost a Ranking per click and draw its own rows
  anyway (ADR-0047); the visitor presses Find.
- *Searching loops on every page and re-sorting earlier pages.* Rows already read would move.
- *Dropping hikes over a day's length from the day-hike list.* It changes the Catalog's scope, which
  ADR-0033 set; the weak-match note says what the visitor needs to know.

Rejected.
