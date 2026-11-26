---
status: accepted
---

# A Base row is formed around its best town of 1,000 people, suburbs and next-to-nothing rows are left out, and Bases count day hikes only

Five more visitors tried "Find a base" (PR 261) after ADR-0085. The captain chose to fix every finding of
their reports. For the ranking and naming that meant: stop naming rows after hamlets, passes and huts
("Thanei · 16 people" over Silandro, "Pian Falzarego · 1 person" over Cortina d'Ampezzo, Gemmipass, Lair over
Pitlochry, Hungeren over Tromsø); keep Edinburgh's districts and Denver's suburbs out (Joppa Pans, Brunstane
and Musselburgh; Dakota Ridge, Stonegate and Parker leading Estes Park's list); stop listing rows that score
next to nothing (Moab's rows of 0.00-0.06); and stop counting multi-day routes as a town's hikes. The
measurements are on the real Europe and US build of ticket 13, as ADR-0085's, with no rebuild: everything is
worked out at request time from the `bases/` folder and `places.sqlite` as they are.

**Areas form around towns first.** ADR-0085 grouped Bases into areas around the best-scoring Base, whatever
it was, and named the row after the biggest Base within `NAME_WITHIN` (0.05) of that best score. When a
hamlet scored best by more than 0.05, it named the row, the town beside it only listed. Now the Bases are
taken in tiers: towns of `NAME_PEOPLE[0]` (1,000) people or more first, then villages of `NAME_PEOPLE[1]`
(100), then the rest (population unknown last), each tier best first. Areas form in that order, so a town
leads its area whenever it scores at all, and a village or hamlet only leads an area with no town in it. The
row is named after the biggest Base of the leader's tier within 0.05 of the leader's score, and shows that
Base's own score. When the best Base of the area is another one, the row says so ("Porthmadog (the best of
them, 0.91)"), and the API gives it as `best_town`. Zermatt at 15 min now names Cortina d'Ampezzo, Livigno,
Davos and Canazei rows; Thanei, Pian Falzarego and Gemmipass name none. Keswick at 30 min names Caernarfon
(not Gaerwen) and Pitlochry's area is no longer Lair's. A row's score can now be a little under the best
score in its area; that is the price of naming it after a town people can find.

**A city is also a sprawl, and a big town with nowhere to stay is a suburb.** ADR-0085 left out a Base with
100,000 people in the towns within 5 km. Edinburgh's districts passed, as GeoNames counts Edinburgh's 460,000
at one point more than 5 km from Joppa Pans. Now a Base is also in a city when 150,000 people live within
8 km (`SPRAWL_PEOPLE`, `SPRAWL_M`), or when it lies within a city of 100,000's own reach of its point: 5 km
times the square root of the city's people over 100,000 (Edinburgh's reach is 10.7 km). And a Base of
`DORMITORY[0]` (20,000) people with fewer than `DORMITORY[1]` (3) places to stay is left out: that is where
people live, not where visitors stay. It leaves out 1,279 of 164,424 listed Bases, among them Dakota Ridge
(33,892 people, 2 places to stay), Parker, Security-Widefield, Herriman, Ytrebygda (Bergen), Neath and
Northwich. Hiking towns of that size keep their places to stay in OSM: Kendal 11, Buxton 16, Bozeman 18,
Durango 18, Gap 11, Fort William 66, Tromsø 27. A Base whose places to stay are unknown is not left out.

**A row under 0.1 is none.** A Base scoring under `MIN_ROW_SCORE` (0.1) is hardly more alike than two towns
picked at random. Moab's 15 and 60-min lists lost their 0.00-0.06 rows; the page's note says why a list can
be short.

**Day hikes only.** A Base's hikes and its BaseProfile now leave out routes over `MULTI_DAY_M` (40 km one
way, ADR-0056), as the hike list's day hikes do. A town page no longer lists a 300 km long-distance trail as
"a hike".

**Names come from towns of real size.** The town search orders towns of 1,000 first, then villages of 100,
and in each an exact name before a prefix ("Cortina" is Cortina d'Ampezzo, "Interlaken" the Swiss one). A
row or search result says it is near another Base only when that Base is bigger and has 10,000 people, so
Munich is not "near" anything.

The analogue test of the known pairs still passes, with Keswick's Fort William, Aviemore and Killarney at the
same percentiles to within 0.3 and Chamonix's Courmayeur and Cortina d'Ampezzo at 100 and 99.2. Grindelwald
and Zermatt stay outside Chamonix's list at 30 min, as under ADR-0085.

Still as before, as data causes: GeoNames' main names (Y Gelli Gandryll for Hay-on-Wye, Sitten for Sion)
need a places rebuild; places to stay are counted within 2 km of a GeoNames point, so hotels a little farther
(Bohinj's at Ribčev Laz) are missed and neighbours share counts; most hikes near a town are walked there and
back (Keswick's 787 of 839 at 30 min), so few are round trips.
