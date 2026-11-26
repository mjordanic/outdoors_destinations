---
status: accepted (amends ADR-0055, ADR-0075 and ADR-0086)
---

# Both tools after the third visitor check: each step a step back, a list in its address, a choice of places

Four visitors tried the site with its landing of two tools, Find a hike and Find a base (PR 267). The captain
chose to fix every bug and confusing item of their triage. The decisions that change how the page behaves:

**Each step of the hike search is a step of the history, as in Find a base.** A hike picked from the search
box or the map, a list found and a result row opened each add a step, so Back closes the row, then hides the
list, then drops the hike, then leaves the tool. Before, a pick replaced the address and Back left the site.
A row opened over another is the same step, as a town opened over one is.

**The address names a list shown ("&list"), and opens it.** After Find hikes the address of the liked hike
(or the wish) ends in `&list`; a reload, Forward or a shared link to it shows the list, found again or kept
from before when it is of the same settings, as a Base's address opens its list. Share and "Link to this
hike" on the liked hike carry it once a list is shown. An address without it shows no list (ADR-0075 still
holds for one of other settings). The rows of a list are drawn (ADR-0047), so a friend gets the same best
rows, not always the same drawn ones.

**"Find place" offers a choice.** One place named exactly as typed is taken; several ("Keswick" in Cumbria,
Ontario and California) are offered to choose, with their countries, as the town box of Find a base offers
them. Words after a comma that name a country keep the places there; other words (a county: "Keswick,
Cumbria") are left out. A name one typo away is offered as "Did you mean"; none, "No place named …".

**The landing keeps both tools as they were left.** The site's name, the mark and Home go to the landing in
the page, not by loading it again; its buttons and the switch, now on the landing and on every page of a site
with Bases, open each tool as it was left. Back to the landing no longer empties the hike search.

**One name for the hike search's button.** "Find hikes" in both modes, where it was "Find similar" for a
liked hike and "Find hikes" for one described.

**Find a base:** Find bases is greyed out while the town box holds a name that is not the town chosen (choose
from the list, or Enter looks it up); each row says its hikes beside the visitor's ("12 hikes counted (yours:
40)"); the default town size filter is shown over the list, as are the other choices; the front page says
"the same kinds of hike, enough of them for a week", not "as many hikes", as a town with 30 kinds of hike or
more loses nothing to one with more (ADR-0085).

**Found as data causes, not changed:** places to stay are OpenStreetMap's hotels, guest houses, campsites and the
like within 2 km of the town's GeoNames point (`TOWN_RADIUS_M`, ADR-0086), so a resort whose beds lie farther
out, or are not mapped, counts few (Chamonix-Mont-Blanc 53, Moab 34); counting them again within a wider radius
(the points counted are kept) is a separate choice, a change of the data. Rows led by a hamlet or ghost town
(Winfield, Creagan) lead an area with no town of 100 people (ADR-0086); a row's place line now says when its
population is not known.

**Not reproduced:** a month filter of nearly a minute (the same choices take 0.2-2 s on a copy of the same
data; four visitors shared one server, one running 14 searches at once), and "Find place" doing nothing on a
click (the visitors' browser tool clicked a button by an id from before typing; a click works, and the page
tests click it). The limit of two heavy searches at once is on: the 14 searches each took 1-2 s locally, so
each got its turn within the 12 s wait; a test holds two running and the third is told the server is busy.

**Considered Options**:

- *Run Find hikes on every hike link opened, as a Base's link does.* A drive-limited search can take 20 s,
  unasked for (ADR-0075); `&list` says the visitor had asked.
- *Suggest places while typing.* "Find place" is pressed once; a choice after it is enough and keeps the
  request count of the box as it was.
