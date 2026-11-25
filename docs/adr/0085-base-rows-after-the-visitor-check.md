---
status: accepted
---

# A Base row is named after its biggest town, cities and the Hiker's region are left out, and scores run from a random pair's

Five visitors tried "Find a base" (PR 261) on a local copy. The captain answered the triage with "Implement
all changes that the visitors recommend". For the ranking and naming that meant: name each row after a real
town and show the towns in the area (triage C8); keep city centres and districts out (C9); leave out the
Hiker's own region at every Reach, not just the nearest towns (C10); spread the scores so good and poor
matches separate (C11); give small samples less confident scores, and go easier on "fewer hikes" once a
town has enough for a week (C12); and drop the cross-continent beach rows at 15 min and the row in Mexico
(C13). The measurements below are on the real Europe and US build of ticket 13 (no rebuild), with the page's
default size filter (up to 50,000 people). Everything is worked out at request time from the `bases/`
folder and `places.sqlite` as they are. GeoNames' feature codes (PPLX, a section of a populated place) would
mark city districts directly, but `places.sqlite` does not keep them, and adding them means writing it
again.

**The hike score runs from a random pair's.** The 40 % mix, 60 % coverage score is unchanged. Before the
enough factor, it is mapped linearly from `SCORE_FLOOR` (0) up to 1. The floor is the median score of 20,000
random pairs of Bases at that Reach: 0.36, 0.40 and 0.48 at 15, 30 and 60 min. A Base at or under it is no
row. The old scores bunched at 0.76-0.95 because a random pair already scores 0.4-0.5. For Keswick at 30 min,
the analogues that visitors and the known pairs name (Betws-y-Coed, Llanberis, Fort William, Aviemore) went
from 0.51-0.93 to 0.75-0.88. The poor matches they named (Bradley Stoke, Seaham, Cramlington, Spalding) went
from 0.16-0.61 to 0.00-0.35. The two groups overlapped before and no longer do. At 60 min, Elkesley ("Donut
Pond", 0.76) is now 0.54 and Felpham (seaside, 0.63) is 0.29. Moab's 15-minute beach resorts (Sitges,
Peníscola, Protaras, 0.24) score 0 and are not listed. Gorafe, the Spanish badlands that a visitor called a
fair match, stays at 0.15.

**Enough for a week, and sure enough to compare.** A Base with under half the Hiker's HikeGroups is still
scored down by the square root of the ratio, but the ratio is now to the smaller of half the Hiker's and
`ENOUGH_GROUPS` (30). So Fort William (85) is no longer cut by 40 % against Keswick (478). With a hike
filter that leaves the Hiker few hikes, every row is now scored down by `sure`, the square root of the
Hiker's HikeGroups over 30 (at most 1). Before, 16 hikes gave confident 0.94 scores. In the known-pairs test,
Keswick's Fort William, Aviemore and Killarney were no row before and now rank 24th, 25th and 44th.
Grindelwald (for Chamonix) drops out of the best 6,000, where more small alpine towns now compete. The
analogue median moved from 99.9 to 99.8 (threshold 98).

**The Hiker's region is left out at every Reach.** Bases within three Reaches of the Hiker's in a straight
line are still left out. A Base is now also left out when more than half of its nearest HikeGroups (its
first band with any, whatever the Reach asked) are within 60 min of the Hiker's Base. For Keswick, Skelwith
Bridge, Kendal, Newland, Broughton in Furness, Sedbergh and Millthrop share 90-100 % of their nearest
hikes. Hellifield (Dales), Moffat and Snowdonia share 0 %. For Bohinj, Kranjska Gora shares 68 %, and Bovec
and Tarvisio share 0-8 %. Only the Bases that may be rows (the best 6,000) within 240 km are checked, and
only their nearest band is read. Reading every band of every Base near the seed raised peak memory by about
0.4 GB. That is what the known-pairs test sets off, with the straight-line rule.

**Cities and places that are no town are left out.** A Base with 100,000 people or more living in the towns
of `places.sqlite` within 5 km (its own included) is in a city. The file keeps every town of 1,000 people or
more, but villages only near hikes, so only towns are counted. Every city part the visitors named passes the
line: Manchester City Centre 815,000, Stobcross 626,000, Orchard Square (Sheffield) 603,000, Eccles 298,000,
Uddingston 115,000. So do Istanbul's Anadoluhisarı and the cities themselves (Innsbruck, Bolzano,
Grenoble). The hiking towns stay well under it: Keswick 5,000, Glossop 29,000, Garmisch 30,000, Merano
55,000, Flagstaff 70,000. A Base whose name says it is no town is left out too: a mountain hut, a malga, an
inn, a hotel, an industrial estate, a college, a road-keeper's house, a condominium, a trailer or mobile
home park, apartments, a subdivision, or "(historical)" (`NOT_A_TOWN`). Real towns such as Bar-sur-Seine,
Braunau am Inn and San Martino Buon Albergo stay. West of the Atlantic, the hikes are the United States'
alone, so the 129 Canadian and 60 Mexican Bases there are left out: their Reach holds only the hikes across
the border. Together, 18,187 of the 195,220 Bases are never rows.

**A row is named after its biggest town.** Areas are formed as before, from the best Base down. A row is then
the Base of its area with the most people (then the most places to stay), among those within `NAME_WITHIN`
(0.05) of the area's best score. Its score, hike count and the town it opens are that Base's own. Rows are
ordered by those scores. The API also gives up to 5 of the area's other towns, biggest first
(`area_towns`). Keswick at 30 min now lists Blaenau-Ffestiniog, Caernarfon, Colwyn Bay, Brecon, Ballater,
Peebles and Machynlleth; before, it listed Maentwrog, Bwlch, Millthrop, Newland and Bowden Head. Zermatt at
60 min now lists St. Moritz, Tignes and Briançon; before, San Rocco, Feldner and Mandarfen.

**Considered Options**:
- *A floor at the random pairs' 75th percentile (0.55, 0.60, 0.65).* It spread the scores 2.5 times, not
  1.7. But Moab's best match (Kanab) read 0.50, Moab kept 18-23 rows, and with places to stay and eat
  "at least 20" it kept 2. Rejected for hiding fair matches. The median is also the plainer meaning: "as
  alike as two towns picked at random".
- *A power curve on top of the floor.* It spreads the top more, but adds a constant with no measured
  anchor. Not taken.
- *Making town size "at least 1,000 people" the default.* A visitor found that it turns the list into real
  towns. But every Base of unknown population fails it, and that is a third of the Bases: the towns that are
  Bases for their places to stay. Naming each row after its area's biggest town gives the same names
  without dropping them. The default stays "up to 50,000".
- *A fixed own-region distance (the 60-minute area, 84 km, at every Reach).* It would leave out Sedbergh
  and the Dales edge (50-90 km), which visitors called good. It would keep Bovec and Tarvisio, across a
  pass from Bohinj, only by luck of distance. Rejected for the hike-sharing rule.
- *GeoNames feature codes for city districts.* Exact for PPLX, but they mean writing `places.sqlite`
  again, and suburbs with their own GeoNames town (Eccles, Chadderton) are no PPLX. Not taken.

**Consequences**:
- Lists are shorter where few towns beat a random pair. With the page's default filter, Moab keeps 34, 46
  and 75 rows at 15, 30 and 60 min (100 before); the European seeds keep 100 at 15 and 30 min. At 60 min,
  Chamonix has 42 rows and Zermatt 56 (26 and 30 before).
- Scores read lower: a row's best is 0.85-0.90 in the Alps and Wales, 0.6-0.66 for Moab and Fort William at
  15 min.
- The API keeps every field, with `hike_score` and `enough` now as above. It adds `sure` and `area_towns` to
  each row.
- Warm ranking times are unchanged or shorter (fewer candidates): measured on an 8-core Mac under load,
  0.12-0.5 s for the six seeds at every Reach. Peak memory over the same rankings (three seeds, every Reach, with
  and without a climb filter and the default size filter) was 1.9-2.5 GB in four runs, against 2.2-2.4 GB
  for the old code: within the runs' spread. Reading every band of the Bases near the seed for the region
  check had cost 0.4 GB, hence the nearest band alone. Reading the names and towns of `places.sqlite` takes about 1.7 s on the Mac, so the server does
  it in a thread as it starts (`warm_bases`): the first ranking 5 s after a start took 1.5 s, against 2.4 s
  for the old code's first.
- The constants are not shown to the Hiker: `SCORE_FLOOR`, `ENOUGH_GROUPS`, `OWN_REGION_SHARE`,
  `URBAN_PEOPLE` and `URBAN_M`, `NAME_WITHIN`, `NOT_A_TOWN`. Neither 30 HikeGroups for a week nor 0.05 for a
  name was tuned beyond the six seeds.
