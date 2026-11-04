---
status: accepted (amends ADR-0020's and ADR-0036's start Junctions)
---

# Loop starts are taken round by round over the Trailheads

ADR-0020 took the three nearest Junctions of each of the best 30 DayHikes' Trailheads in rank order, at most 40 starts in all. The cap was reached at about the 14th Trailhead, so a score change of about 1e-4 that reordered near-tied DayHikes could push a Trailhead's every start past the cap and drop the Seed's best loop. That happened to Seed 19787033 "Pot kralja Triglava" between Europe rebuilds 1 and 2: its loop near Source de la Goute Escure starts at the Trailhead ranked 13th in rebuild 1 and 15th in rebuild 2, and rebuild 2 listed no loop for it.

**Round by round.** After the Origin's starts (ADR-0036, unchanged), every Trailhead's nearest Junction is taken, in rank order, then every Trailhead's second nearest, then every third, until the 40 starts are taken. Every one of the 30 Trailheads now gets a start. `LOOP_STARTS`, `LOOP_SEEDS`, `LOOP_STARTS_PER_TRAILHEAD`, the radii, the expansion and read bounds and `LOOP_BUDGET_S` are unchanged.

Measured in-process on the rebuild 2 export (`ServingData.rank`, loops on, 52 Seeds sampled from both rebuilds, median of two warm runs per Seed, an 8-core laptop, not the 1-CPU container):

| | Loops listed | Seeds with a loop | `_loops` p50 / p90 / max | Runs past the 5 s budget |
| --- | --- | --- | --- | --- |
| before | 103 | 40 | 1.29 / 2.89 / 4.55 s | 0 of 104 |
| round by round | 112 | 41 | 1.18 / 3.43 / 5.17 s | 2 of 104 |

The Triglav Seed lists a loop again, "Loop near Peyrou" (0.7974, row 8), from a Junction 340 m from the old loop's start. Starts spread over more areas read more Segments, so the slowest searches take longer and a few now end at the budget with what they found.

**Considered Options**: raising `LOOP_STARTS` (more time on one vCPU, and the ADR-0036 measurement again); merging Trailheads within `LOOP_START_M` before taking starts (still cut by rank at the cap). Rejected.

**Consequences**: loops are still searched from at most 40 starts, so they are not every loop there is and can change when the Catalog is rebuilt; the page's ranking note and the README say so.
