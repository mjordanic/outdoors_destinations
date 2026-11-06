---
status: accepted (amends ADR-0053 and ADR-0054)
---

# What the page does after visitor check 4

Six simulated visitors used the review site built from develop bb3588e (rebuild 4, 258,305 hikes). They
found seven bugs and asked for clearer search rows and messages. This records the decisions the fixes took.
The scores and the hikes are unchanged; multi-day routes are ADR-0056.

**Famous places come first in the hike search (amends ADR-0053).** "Mont Blanc" listed hills in Normandy,
"Gardasee" Norwegian peaks, "Lac Léman" nothing, "Bodensee" Austrian tarns and "Ratitovec" a look-alike 45 km
away. The Landmarks lacked the places meant: no hike passes within 2 km of the summit of Mont Blanc or of a
big lake's GeoNames point, and Ratitovec is a GeoNames ridge no OSM Peak names. Now:

- The GeoNames mountains are Landmarks too: `MT`, `PK` and `PKS` as peaks, `RDGE` as a ridge. A peak or ridge keeps its GeoNames height, else the elevation model's at its point.
- A GeoNames Landmark with no hike within 2 km (`LANDMARK_NEAR_M`) is on the hikes passing within 10 km
  (`LANDMARK_FAR_M`): a big lake's shore, a summit no trail reaches. One with none there is still left out.
- A GeoNames peak within 1.5 km (`LANDMARK_MERGE_M`) of an OSM Peak named as one of its names is that Peak:
  its names in other languages join the Peak's (Triglav, Monte Bianco), not a second Landmark.
- Each Landmark counts its names (`landmarks.names`). Among Landmarks matched as well (exact, alias,
  last words, start), the one with the most names comes first, then the highest, then the most hikes. A
  famous place has a name in many languages: Lake Constance has 81 in GeoNames (folded), the tarns named
  Bodensee 1; Mont Blanc 68, the hills 1. An alias is matched like the name ("Gardasee", "Lake Garda" and "Lago di Garda"
  all find Lago di Garda), so a respelling ("Vardåsen") is tried only when nothing is named so.

On the review export `export_landmarks` now keeps 48,330 lakes (17,976 before), 11,579 passes (6,028),
257,341 peaks (209,755), and 6,255 ridges. It took 3.6 min on one core here, peaking at 1.8 GB
(ADR-0053: 5 min and 1.6 GB), and writes 128 MB (111 MB). A
Landmark query takes 2-30 ms, up to 260 ms for two letters on a cold cache. An export from before opens
as before: without the `names` column the order is the old one. The review site needs `dayhike export-places`
run again beside its export to get the new Landmarks (no other rebuild).

**Search rows say where.** Five hikes named "Zirbenweg" were told apart only by their coordinates. Each hike
row (search, Landmark lists) and a hike's panel now say the town nearest its Trailhead and that town's country
("near Igls, AT"), and each Landmark its nearest other town and country ("lake, near Gargnano, IT"). The
server finds them in `places.sqlite` (towns of 1,000 people or more within 30 km); an export without it says
none. No export change is needed.

**An address typed or gone back to shows what it asks.** Editing the address bar to other settings, or Back
and Forward between such addresses, changed the settings but kept the old list unmarked; Back to the home
address kept the hike and its list. Now a list whose settings the address no longer matches is marked "The
query changed since this ranking", and the home address shows the page as it opens: no hike, no list, the
settings at their defaults. A link without a setting still leaves that setting as it is (ADR-0054).

**"Link to this hike" is the Share link.** Its address was made when the hike was shown, so it lacked a
weight moved or Advanced opened later. It now follows every setting, as the address bar does.

**A busy server says so beside the button, with Try again.** The 25 s wait then "busy; try again in a
moment" was not slow work: the Ranking took 0.3-1.5 s on retry. One Ranking runs at a time per instance
(ADR-0054), first come first served, and the six visitors shared it. Behind three loop searches (about 3 s each)
a 0.3 s wish waited 8.5 s here; behind drive-time searches (up to 31 s each on 1 vCPU) a request passes
`RANK_WAIT_S` (25 s) and gets the 503. The queue stays: it is what keeps a small instance's memory, and a
real visitor seldom meets another. The 503 now says "The server is busy with other visitors' searches: yours
waited 25 s for its turn. Try again in a moment." with `Retry-After: 5`, and the page shows a failed Find in
the line under Find, and a failed Show 10 more beside that button, each with a **Try again** button (for a
503, a 409 or no answer at all). Show 10 more says its progress there too.

**Messages where a phone's Hiker looks.** A setting changed after a search is said under Find too ("Your
settings changed since the list below: press Find similar again to update it."). A hike chosen on the map is
said under the map ("Chosen: …"). Share on a phone says "Opening your phone's share menu…" at once, beside
the button, as the menu can take a moment.

**A map click offers every hike passing under it.** A click picked the nearest line unless another ran
within 3 px of it, so a hike sharing a trail drawn a few px apart (the Zirbenweg under Almenweg 1600 and
Adlerweg 13, each simplified on its own) or near a crossing was not reachable. Now every hike whose line runs
through the click's 10 px tolerance, away from its own ends, is offered in "N hikes run here; choose one",
shorter first. Where two hikes only meet end to end, the line clicked still wins.

**The route designer stays put and takes junctions.** Its hint, readout and "Connect here" buttons were above
the map: each click changed their height and moved the map under the pointer. They are under the map now.
A junction's dot was clickable only before the start, and a line hovered is drawn on top of it; now a click
within 8 px (`JUNCTION_PX`) of a dot is for the junction, and after the start it walks the shortest way there
over the loaded segments, as one click for Undo. The Start dot walks back to the start.

**The pair table fits.** Its value columns did not wrap, so "Check any pair" cut its Score column off on a
narrow screen. Values wrap now; on a phone the first column takes its width from its words, the score bars
are narrower, and a "?"'s tap area stays inside its cell.

**Considered Options**: a per-address queue or more Rankings at once (more memory than one small instance
holds); retrying a 503 by itself (a real visitor would wait twice without a word); GeoNames mountains only for
the far ones (Triglav would list its OSM Peak and a GeoNames one); population or area to rank Landmarks
(GeoNames has no area, and population only for towns); resetting every setting a link does not name (breaks
ADR-0054's limit kept for another hike's link). Rejected.
