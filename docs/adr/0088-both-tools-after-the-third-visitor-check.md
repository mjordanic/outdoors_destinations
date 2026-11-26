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
Cumbria") are left out. A name one typo away is offered as "Did you mean" (with a country after the comma,
among the places there: "Innsbruk, Austria"); none, "No place named …". Find hikes with the choice still
offered says to choose one of the places, not that the place was not found.

**The landing keeps both tools as they were left.** The site's name, the mark and Home go to the landing in
the page, not by loading it again; its buttons and the switch, now on the landing and on every page of a site
with Bases, open each tool as it was left. Back to the landing no longer empties the hike search.

**One name for the hike search's button.** "Find hikes" in both modes, where it was "Find similar" for a
liked hike and "Find hikes" for one described.

**A row of Find a base is named after the biggest town of its area.** The area is still formed around its
best town (ADR-0086), and every figure of the row (its score and how it is made, its hikes counted, its
country, people, places to stay and eat, its months, the page it opens) is still that of the biggest town
about as alike as that best one, the town it is scored as. Only its name is that of the biggest town or
village of the area (100 people or more), with the others under it ("and 2 nearby towns …") and the best
town beside it, as before; when that is not the town it is scored as, its place line starts "figures for …"
that town, so a row never mixes two towns unsaid. Before, a bigger town scoring more than `NAME_WITHIN`
under the best was listed under a smaller one's name.

**Find a base:** Find bases is greyed out while the town box holds a name that is not the town chosen (choose
from the list, or Enter looks it up); each row says its hikes beside the visitor's ("12 hikes counted (yours:
40)"); the default town size filter is shown over the list, as are the other choices; the front page says
"the same kinds of hike, enough of them for a week", not "as many hikes", as a town with 30 kinds of hike or
more loses nothing to one with more (ADR-0085).

**Found as data causes, not changed:** places to stay are OpenStreetMap's hotels, guest houses, campsites and the
like within 2 km of the town's GeoNames point (`TOWN_RADIUS_M`, ADR-0086), so a resort whose beds lie farther
out, or are not mapped, counts few (Chamonix-Mont-Blanc 53, Moab 34); counting them again within a wider radius
(the points counted are kept) is a separate choice, a change of the data. Rows still named after a hamlet or
ghost town (Winfield, Creagan) are of an area with no town or village of 100 people known (ADR-0086); a row's
place line now says when its population is not known.

**Not reproduced:** a month filter of nearly a minute. Measured on 2026-11-26 against a local server over the
same Europe and US review data (`POST /api/bases/rank` from Keswick, GB): "mild in all of Jan, Oct" within
30 min took 0.69 s; "Jan, Feb, Oct" within 30 min 0.17 s; "Jan, Oct" with places to stay like mine within 60
min 2.27 s; the first request after a start (Moab, 15 min) 1.42 s. With two heavy 60-min rankings in flight,
holding both turns of the limit (Chamonix-Mont-Blanc with a climb limit and stays and eats like mine;
Zermatt with a length limit and size and stays like mine), a month-filtered ranking ("Jan, Mar, Oct", 30
min) sent 0.2 s after them answered 200 in 1.41 s, the two heavy ones in 1.62 s each. Four visitors shared
one server, one running 14 searches at once. Also not reproduced: "Find place" doing nothing on a
click (the visitors' browser tool clicked a button by an id from before typing; a click works, and the page
tests click it). The limit of two heavy searches at once is on: the 14 searches each took 1-2 s locally, so
each got its turn within the 12 s wait; a test holds two running and the third is told the server is busy.

**Considered Options**:

- *Run Find hikes on every hike link opened, as a Base's link does.* A drive-limited search can take 20 s,
  unasked for (ADR-0075); `&list` says the visitor had asked.
- *Suggest places while typing.* "Find place" is pressed once; a choice after it is enough and keeps the
  request count of the box as it was.
