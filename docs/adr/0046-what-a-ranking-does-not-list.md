---
status: accepted (amends ADR-0032)
---

# A Ranking says what it does not list

A row stands for an area: every hike starting within the GroupingRadius of its start, and of no
better row's, is one of its versions or is left out (ADR-0020). "Show 10 more" leaves out every hike
in the areas shown (ADR-0032), so no later page lists those either. A Hiker who sees one row cannot
know that dozens of hikes start there, some nearly as good. On the rebuild 2 export (264,386
DayHikes, 30 random Seeds, default weights), the 10 areas of page 1 held a median of 1,152 hikes
that are not listed Europe-wide, and 78 with a 50 km DistanceLimit. The captain asked for three
counts.

**Per row, the good matches that start there.** Each row has `more_here`: the DayHikes ranked that
start in its area, are not listed, and score at most 0.05 below the row (`GOOD_MATCH`). A hike near
two rows counts for the better one, as the grouping takes it. Counting every hike in the area would
be honest but inflated Europe-wide, often a hundred or more per row; the floor keeps the count to
hikes a Hiker would want (the captain's choice). Versions dropped by the cap or as walking the same
trails count here too. The page reads "3 versions · 12 more good matches start here". The tooltip says
why they are not listed.

**Under the table, the hikes in other areas.** The answer has `other_areas`: the DayHikes ranked that
start in no row's area. These are the hikes later pages can still list. It equals the hikes ranked
less those listed and those counted in the rows' areas, including the ones below the floor. The page
reads "24,113 more hikes start in other areas" next to "Show 10 more". It does not count areas: that
needs the whole grouping, seconds Europe-wide.

**With loops, what the search did.** The answer has `loop_search`, null when no loop search ran:
`starts`, the start Junctions searched from; `found`, the loops found and measured; `listed`, the
loops in the rows and their versions; and `stopped`, whether the search stopped at its 5 s limit
(ADR-0036), in the search or while measuring. `find_loops` returns a `LoopSearch` record with its
loops, starts and stop, and the rest was already known to `_loops`. The page adds it to the note
above the table: "Loops: searched from 31 starts near the best hikes, 14 found, 3 listed (the others
rank lower, or walk mostly the same trails as a listed hike or the hike you like). The search stopped
at its time limit, so it may have missed some loops."

**Cost.** The area of each hike ranked is found once per row: a latitude band first, then the
great-circle distance of the hikes in it. Measured in-process through `POST /api/rank` on the rebuild 2
export, 8-core Mac under load, 30 random Seeds, two runs each:

| | before, median / p90 | after, median / p90 | counting alone, median / p90 |
|---|---|---|---|
| Europe-wide | 309-315 / 330-339 ms | 317-319 / 349-390 ms | 12-14 / 17-23 ms |
| 50 km DistanceLimit | 32-36 / 43-52 ms | 34-38 / 44-62 ms | 0.4 / 0.7-0.9 ms |

Per row, `more_here` was 2 at the median and 39 at p90 Europe-wide (at most 119), and 0 and 6 with
the 50 km limit.

**Considered Options**:
- *Every hike in the area, with no floor.* Honest, but too large to mean much Europe-wide.
- *A count of the remaining areas.* It needs a full grouping pass.
- *The Twins left out, and the NetworkHike caps.* The first is about the same for every Ranking,
  and the second is a build-time choice the Hiker has no use for.

Rejected.

**Consequences**: `ServingData.rank` returns a `Ranking`, a list of the rows as before with
`other_areas` and `loop_search`; each row has `more_here`. `POST /api/rank` answers with all three.
`find_loops` returns a `LoopSearch`, its loops in `loops`.
