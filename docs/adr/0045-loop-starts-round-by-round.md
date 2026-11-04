---
status: accepted (amends ADR-0020's and ADR-0036's start Junctions)
---

# Loop starts are taken round by round over the best 20 Trailheads

ADR-0020 took the three nearest Junctions of each of the best 30 DayHikes' Trailheads in rank order, at most 40 starts in all. The cap was reached at about the 14th Trailhead, so a score change of about 1e-4 that reordered near-tied DayHikes could push a Trailhead's every start past the cap and drop the Seed's best loop. That happened to Seed 19787033 "Pot kralja Triglava" between Europe rebuilds 1 and 2: its loop near Source de la Goute Escure starts at the Trailhead ranked 13th in rebuild 1 and 15th in rebuild 2, and rebuild 2 listed no loop for it.

**Round by round over the best 20.** After the Origin's starts (ADR-0036, unchanged), each of the best 20 Trailheads (`LOOP_TRAILHEADS`) gets its nearest Junction, in rank order, then each its second nearest, until the 40 starts are taken: two each. `LOOP_STARTS`, `LOOP_SEEDS`, `LOOP_STARTS_PER_TRAILHEAD`, the radii, the expansion and read bounds and `LOOP_BUDGET_S` are unchanged.

`LOOP_SEEDS` still passes the best 30 Trailheads to the loop search, but only the first `LOOP_TRAILHEADS` give starts.

**Why 20.** Starts spread over more areas read more Segments, and on one vCPU that pushes searches into the 5 s budget. Measured on the rebuild 2 export, in-process in a 1-CPU, 2 GiB container, 52 Seeds sampled from both rebuilds, two warm runs each:

| Starts | Loops listed | Seeds with a loop | `_loops` p50 / p90 / max | Runs at the budget | Triglav |
| --- | --- | --- | --- | --- | --- |
| 3 each, rank order (before) | 103 | 40 | 1.35 / 3.61 / 4.94 s | 0 of 104 | no loop |
| round by round, best 15 | 104 | 40 | 1.63 / 4.05 / 5.45 s | 4 of 104 | no loop |
| round by round, best 20 | 107 | 42 | 1.73 / 4.49 / 7.67 s | 8 of 104 | 3 loops, incl. "Loop near Peyrou" (0.7974) |
| round by round, all 30 | 107 | 40 | 2.26 / 5.10 / 7.26 s | 14 of 104 | "Loop near Peyrou" (0.7974) |

Twenty is the fewest that brings the Triglav Seed's loop back, from a Junction 340 m from the old loop's start, at about half the budget hits of all 30. A search at the budget lists what it found by then, so those Seeds' loops vary from run to run: a second measurement of the committed code, one warm run each, listed 104 loops for 40 Seeds with 3 of 52 runs at the budget, and the Triglav Seed's 3 loops again.

**Considered Options**: all 30 Trailheads (twice the budget hits); the best 15 (Triglav still gets no loop); raising `LOOP_STARTS` (more time on one vCPU, and the ADR-0036 measurement again); merging Trailheads within `LOOP_START_M` before taking starts (still cut by rank at the cap). Rejected.

**Consequences**: loops are still searched from at most 40 starts near 20 Trailheads, so they are not every loop there is and can change when the Catalog is rebuilt; the page's ranking note and the README say so. A loop found from a 3rd-nearest Junction before can be lost, and more loops found can push a better-scoring one out of the `LOOP_MEASURED` best guesses.
