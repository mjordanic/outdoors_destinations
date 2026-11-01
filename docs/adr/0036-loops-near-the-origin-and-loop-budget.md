---
status: accepted (amends ADR-0020's start Junctions and ADR-0028's loop budget)
---

# Loops also start near the Origin; the loop budget stays 5 s

ADR-0020 searches for SuggestedLoops only from Junctions near the Trailheads of the best 30 DayHikes of
a Ranking. Where the Catalog holds no DayHike near the Hiker, no loop starts near them either. Briançon
is such a place: no Catalog DayHike starts within 5 km of it, and with loops on, no SuggestedLoop
started within 10 km of it, though the TrailGraph holds a 6 km loop at Puy-Saint-Pierre, 2 km from
the town (coverage report, 2026-11-01). The same report saw a live loops Ranking from Briançon list no
loop on its first, cold call, and wondered whether the 5 s loop budget is too short on one vCPU.

**Loops also start near the Origin.** When the Hiker gives an Origin, the loop search takes the 20
Junctions nearest it within 5 km where two trail Segments meet (`LOOP_ORIGIN_STARTS`,
`LOOP_ORIGIN_M`), and searches from them first, so the expansion budget and the 5 s go to them first.
The Junctions near the best DayHikes' Trailheads come after, within 2 km as before. All starts
together are still at most 40, so with an Origin the Trailheads keep at least 20. The Origin's
starts follow the same rules as the others: with the DriveTime filter, one out of reach is dropped,
and with a DistanceLimit, a loop starting beyond it is left out. `/api/rank` passes the Origin
whenever one is given, with `max_hours`, `max_km` or both; before, only `max_km` carried it. With no
Origin nothing changes.

Why 20 and 5 km. The three nearest Junctions, as for a Trailhead, are too few: at Briançon they lie in
the town centre, where no loop closes, and the Puy-Saint-Pierre loop starts 2 km out. Searching from
the Origin alone, on the Europe export in the 1-CPU container, warm, with a Wish length of 6, 10 and
15 km:

| Origin starts | Briançon: loops found (6 / 10 / 15 km) | Search time, 5 Origins |
| --- | --- | --- |
| 3 | 0 / 0 / 0 | 0.03–0.25 s |
| 6 | 2 / 0 / 0 | 0.01–0.28 s |
| 20 | 2 / 0 / 0 | 0.02–0.57 s |
| 40 | 2 / 2 / 1 (from 4.4 km out) | 0.03–1.15 s |

The 5 Origins were Briançon, Chamonix, Bled, Keswick and Freiburg. Twenty finds the Briançon loop.
Forty would find more but leave no starts for the Trailheads in dense places. 5 km covers a short drive
or walk from where the Hiker stands.

**The budget stays 5 s.** `LOOP_BUDGET_S` was measured in the container that `web/compose.yaml` runs:
1 CPU, 2 GiB, the Europe export (135,588 DayHikes, 3.3 GB TrailGraph) baked in. The request was
`/api/rank` with a Wish of 10 km, a 1 h DriveTime limit and loops on, from 10 Origins: Briançon,
Chamonix, Zermatt, Cortina, Innsbruck, Bled, Zakopane, Keswick, Freiburg and Sintra. Each cold
Ranking ran in a fresh container whose export files were first dropped from the page cache
(`POSIX_FADV_DONTNEED`), as on a new instance. The warm Ranking then asked the same again. A
reference run with a 60 s budget found loops from all 10 Origins (2–11 each).

| Budget | Runs | Cold: lists every reference loop | Cold p50 / p75 / max | Warm p50 / p75 / max |
| --- | --- | --- | --- | --- |
| 5 s | 20 (two rounds) | 19 of 20 | 5.3 / 7.4 / 15.7 s | 2.7 / 3.8 / 10.7 s |
| 8 s | 10 | 10 of 10 | 6.3 / 7.0 / 7.5 s | 2.9 / 3.3 / 4.2 s |
| 12 s | 10 | 10 of 10 | 5.3 / 7.7 / 9.8 s | 2.2 / 4.3 / 4.6 s |

The times are whole Rankings, drive times and the Catalog included. The rule was the smallest budget
that lists loops at p75 where the graph has them, at most 12 s. 5 s does: 19 of 20 cold Rankings
listed every loop the 60 s run found. The one miss was Freiburg's first cold call, 15.7 s in all. Its
second round took 7.4 s and listed all 11. The time went to cold reads, not to the search. A larger
budget barely moves the typical Ranking, because the search mostly ends before 5 s.

The cgroup peak, page cache included, was at most 1.05 GB of 2 GiB. The app's own (anon) memory was
at most 0.49 GB. No run was OOM-killed.

**No warm-up.** The cold time is page faults on the TrailGraph: a cold Ranking takes about twice a
warm one. The loop search reads `segments` rows (2.5 GB of the 3.3 GB file, coordinates and heights)
in the area of each start, so that table cannot be held warm in 2 GiB. Its R-tree (43 MB) and
`junctions` (31 MB) could, but they are not where the time goes. So no warm-up is built. This should
be measured again once graded paths make the TrailGraph denser (the shared rebuild).

**Considered Options**:
- The three nearest Junctions, as for a Trailhead. Rejected: at Briançon they find nothing.
- The 40 nearest. Rejected: more loops near Briançon (10 and 15 km), but in dense places the
  Trailheads would get no start.
- A start cap of their own for the Origin, on top of the 40. Rejected: it would not be needed while
  20 + 20 starts fit the budget.
- An 8 s budget. Rejected for now: it listed every loop in its 10 cold runs, but where 5 s already
  meets the rule, it would only lengthen the rare slow call by up to 3 s.
- A warm-up of the TrailGraph at start. Rejected, as above.

**Consequences**:
- A Ranking with an Origin and loops on can list a loop near the Hiker even where no Catalog DayHike
  starts nearby. That is what node-network areas like Briançon need until the Catalog covers them.
- Loops near the best DayHikes still come, from the remaining starts.
- The page's button still says "Suggest loops near these hikes", though with a start given the loops
  come from near it too. The page texts are left to the coverage texts change.
- ADR-0020's rejected option "starting from every Junction near the Seed" is untouched: these starts
  are near the Origin, where the Hiker is, not near the Seed.
