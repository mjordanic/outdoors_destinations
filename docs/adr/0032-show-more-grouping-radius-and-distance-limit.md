---
status: accepted (amends ADR-0020 and ADR-0028)
---

# Ten rows at a time, a GroupingRadius that follows the search, and a DistanceLimit

Three changes to the results list, asked together by the captain.

**Ten rows, then "Show 10 more".** A Ranking still lists 10 rows. Under the table, a button "Show 10 more"
asks the next 10 areas and adds them under the rows shown. It asks the query of the table shown, as
the loops button does (ADR-0028), with `after`: the starts of the rows already shown. The server
ranks again and leaves out every hike that starts within the GroupingRadius of one of those starts,
then groups the rest as before. That is exactly how the rows shown would have gone on. The rule of
ADR-0020 takes a hike as a new row only when it starts outside the GroupingRadius of every better
row, so every hike ranked above the 11th row's leader is within the GroupingRadius of a row shown.
Page 2 leaves those out and starts at that 11th leader. No area is shown twice and none is skipped.
A row's versions still score above the hike that leads the first row of the next page, as ADR-0020
says for the 11th row. The button hides when an answer has fewer than 10 rows, or when 100 rows are
shown (`after` takes at most 90 starts).

Loops are still searched only on request. "Show 10 more" asks the shown table's query again, loops
included. Without loops, each page costs one Catalog Ranking. With loops, each page searches loops
near the best 30 DayHikes that start outside the areas shown, so the new rows get loops near them
too, and each page costs a loop search. The search's 5 s stop can cut a later search shorter than
an earlier one. Pages built from starts stay exact anyway, because a page never re-searches the
rows already shown. "Suggest loops near these hikes" still ranks the shown query again with loops
and shows its first 10 rows, so the table starts over from row 1.

**A GroupingRadius that follows the search.** A fixed 10 km hides many hikes when someone searches
close to home where hikes are dense. On the Europe export, with a 30 km DistanceLimit around each of
30 random Seeds' Trailheads, the 10 rows held a median of 14 versions behind them at 10 km, against
1 at 1.5 km, and 3 of the 30 lists held fewer than 10 rows at 10 km. The captain's rule: the
GroupingRadius is 5 % of the DistanceLimit, or of the DriveTime limit converted to km, the smaller
when both are set, at least 500 m. With no limit it stays 10 km.

A drive hour counts as 50 km (`GROUP_KMH`), for two reasons:
- The radius is measured in a straight line. 50 km/h in a straight line is about 65 km/h on roads
  that wind 1.3 times the straight line, which is about right for a drive that mixes motorway and
  valley roads.
- It makes the longest drive, 4 h, group at 200 km × 5 % = 10 km, the same as no limit. So a limit
  never groups more widely than no limit does.

The DistanceLimit goes up to 200 km for the same reason. The Hiker can set the GroupingRadius under
"More options", below the drive and distance limits, from 0.1 to 50 km. Empty, it follows the limits,
and the field's placeholder shows the value for the current limits. It is never 0: the next page is
found by leaving out hikes that start within the GroupingRadius of the rows shown, which needs an
area. 0.1 km already puts nearly every hike in a row of its own. The version rules of ADR-0020 do not
change: at most 3 versions, each more than half off the others, and Twins are listed once.
`/api/info` gives the rule (`grouping`: `km`, `share`, `min_km`, `kmh`) so the page shows the same
default the server takes. A Ranking echoes the `group_km` it used.

**A DistanceLimit.** Beside the DriveTime limit, "Max km, straight line" keeps only hikes that start
within that great-circle distance of the starting place. A DayHike starts at its Trailhead and a
SuggestedLoop at its start. The distance is not a road distance. It needs no routing engine, so it
works on a server without drive times, and alone it costs nothing measurable (12 ms median on the
Europe export at 30 km). With both limits a hike must be within both. Every row has `distance_km`
when a starting place is given, and the table shows it in a Distance column when the DistanceLimit
is on.

**Measured** on the Europe FABDEM export (135,588 DayHikes), 8-core Mac, one process, 30 random
Seeds, default weights, ReturnToStart on:
- Page 1 takes 0.14 s median. Page 2 takes 0.18 s and page 10 0.36 s: each page checks every
  Trailhead against 10 more starts.
- Pages 1 and 2 matched the first 20 rows of one Ranking asked for 20 for every Seed: the same
  leaders in the same order, and the same rows 11 to 20 with their versions.
- With loops, for 10 Seeds, page 1 took 0.64 s and page 2 0.61 s median (1.3 s and 1.2 s at most),
  and no hike or loop appeared on both pages.

**Considered Options**:
- *An offset that recomputes rows 1 to 20 and returns 11 to 20.* It needs the loop search again on
  every page, and that search can stop at its deadline with different loops, which moves the areas
  of page 1.
- *Listing 20 rows at once.* It costs every visit what only some want.
- *A tenth of the limit, at least 1 km* (the first proposal). The captain chose 5 % and 500 m, and a
  visible control.
- *Road distance from the routing engine.* The captain asked for straight-line distance. It is
  simpler and works without Valhalla.

Rejected.

**Consequences**: `POST /api/rank` takes `max_km` (up to 200, with `origin`), `group_km` (0.1 to 50)
and `after` (at most 90 places). An `origin` needs `max_hours`, `max_km` or both. It echoes
`max_hours`, `max_km`, `group_km` and the number of starts in `after`. `reachable` counts the hikes
within every limit given. `ServingData.rank` takes `near`, `group_m` and `after`. The CLI is
unchanged, and its Rankings still group at 10 km. The page's third panel is now "Only hikes near a
starting place", with a tick box for each limit.
