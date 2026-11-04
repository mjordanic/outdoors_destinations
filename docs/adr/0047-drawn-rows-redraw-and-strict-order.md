---
status: accepted (amends ADR-0020, ADR-0032, ADR-0045 and ADR-0046)
---

# The rows listed are drawn by score, with Redraw and Strict order

A Ranking listed its best 10 areas, the same 10 on every visit. Europe-wide the 10th row typically scores
0.017 below the 1st, and rows 4 to 30 are often within 0.001 to 0.005 of each other. An invisible
difference decided which of them a Hiker ever saw. The captain asked for sampled rows by default,
drawn by score, a Redraw button to draw again, and a Strict order button for the ranking as it is.
They also asked for all 10 rows to be drawn, not only the lower ones.

**A draw.** The page lists 10 rows drawn from a pool of the Ranking's best 30. The draw picks them
without replacement, and the chance of picking a row rises as exp(score / T), with T = 0.005
(`DRAW_T`). The rows are then listed in score order. The draw is Gumbel-top-k: each row gets a key,
score / T plus Gumbel noise, and the 10 largest keys are taken. The noise is a hash of the draw
number and the row's key (its DayHike id, or a SuggestedLoop's route text), so a draw keeps no random
state and is the same for the same draw number. It is FNV-1a 32 then MurmurHash3's finalizer, mapped
to a uniform u and to −log(−log u). `serving.drawn` and `drawNoise`/`drawn` in `app.js` compute it
alike, and a test holds them to the same answer. The page takes a draw number from
`crypto.getRandomValues` per Find and adds one per Redraw.

The pool is in the browser, so the server stays deterministic. `POST /api/rank` answers with 30 rows
(`web.POOL`) where it answered with 10, and `/api/info` gives `draw: {pool, t}`. The page draws its 10.
Each Find then costs about 0.1 s and 18 KB gzip more Europe-wide (measured at 1 CPU on develop:
0.94 s against 0.80 s median, 27 KB against 8.9 KB gzip). A Redraw costs the server nothing.

**Redraw** draws page 1 again from the same pool, at once, without a request. With loops on it keeps
the loops already found. If the new page lists the same hikes, it draws again, at most 5 times. If no
draw lists another hike, it keeps the page and says "No other close matches here; widen the distance
or turn on Strict order". That happens when the pool holds no more than 10 rows, or when every other
row scores far below them. With a 50 km DistanceLimit about 1 Redraw in 10 would otherwise list the
same page.

**Strict order** is a tick box. While it is ticked, each page lists the first 10 rows of its pool:
today's Ranking, its ties broken by the raw score as before. Redraw is off. Ticking it, unticking it
and Redraw each change page 1 without a request and drop the pages "Show 10 more" added, since those
were drawn after another page 1. A link to a hike or a wish ends in `&strict`, like `&oneway`, while
it is ticked. A link never carries the draw number, so whoever opens it gets a draw of their own.

**Show 10 more** asks with `after`, the starts of every row shown, as before (ADR-0032). The server
answers with the next 30 rows, and the page draws the next 10 from them with the same draw number. In
Strict order this pages exactly as before. Areas still go on with none shown twice: the leaders of a
grouping are pairwise at least the GroupingRadius apart, so `after` removes exactly the areas shown,
and the areas drawn past stay in later pools. The button hides once an answer holds no row the page
did not list. The number of hikes in other areas (ADR-0046) adds every DayHike in the areas of the
last answer's rows the page did not list: each row now has `in_area`, the DayHikes ranked that start
in its area, listed or not. `more_here` is counted, as before, for each of the 30 rows.

**Loop starts are drawn too.** The request carries the draw number. A loop search then takes its 20
Trailheads (`LOOP_TRAILHEADS`) by the same draw, over the DayHike ids of the best 60
(`LOOP_DRAW_RANKS`) at T 0.005, and passes them on in rank order. They still take their starts round
by round (ADR-0045). In Strict order the request carries no draw number, and the search takes the
best 20, as before. "Suggest loops near these hikes" keeps the shown draw number. So does "Show 10
more". `LOOP_STARTS`, `LOOP_BUDGET_S` and the rest of the search are unchanged.

**What the page says.** The note above the table reads "These 10 are picked from the best 30 areas,
closer matches more often: Redraw for others, or tick Strict order for the exact ranking." A row that
Strict order would not list on its page, one drawn from below the 10th, has a light "picked" mark in
the # column. Every row is drawn, so marking every one would say nothing. When the best rows of the
pool score exactly the same, the note says "The best 4 areas match equally well." Calibrated Wish
scores sit on a 1/1024 grid, so this is common for a Wish. Strict order removes the first sentence and
the marks. It keeps the tie sentence, since the tie holds in either order.

**Measured offline** on the rebuild 2 export, in-process with `serving.drawn`. The Rankings were 30
random Seeds with default weights, the same Seeds with a 50 km DistanceLimit around their own
Trailhead, and 20 Wishes. Each Ranking was drawn 400 times.

| | Europe-wide | 50 km | Wish |
|---|---|---|---|
| best row shown | 98 % | 98 % | 42 % (16 of 20 tie at the top) |
| mean score lost per page, p50 / p90 (gate p90 ≤ 0.002) | 0.0018 / **0.0022** | 0.0012 / **0.0021** | 0.0000 / 0.0003 |
| lowest row shown below row 10, p50 / p90 (gate ≤ 0.015) | 0.0067 / 0.0125 | 0.0052 / 0.0130 | 0.0000 / 0.0007 |
| areas listed in 5 visits, median (Strict order 10) | 18.3 | 14.5 | 25.9 |
| Redraw lists the same page before it draws again | 0.7 % | 10.6 % | 0.4 % |

Pages 1 to 3 were also asked live, as the page asks them, with a pool of 30 and `after`: 8 Rankings
of each kind, 2 draws each, 144 pages in all. No area was shown twice, every page was in score order,
and every pool equalled the deterministic grouping less the rows shown.

The mean score lost misses its gate by 0.0002 at p90. The captain accepted the miss to keep T 0.005,
the pool of 30 and all 10 rows drawn. The alternatives measured were T 0.0045 (p90 0.0019 / 0.0019,
same page 1.3 % / 12.6 %) and a pool of 25 at T 0.005 (0.0018 / 0.0017). The table shows scores to
three decimals.

**Loop starts at 1 CPU / 2 GiB.** Measured in-process in a `docker run --cpus 1 --memory 2g` container
on the rebuild 2 export, while a Europe rebuild used up to 4 of the Mac's 8 cores. Each of the 52
Seeds of ADR-0045 and the Triglav Seed 19787033 was ranked with loops and a 30-row pool. For every
Seed, Strict order and draws 0, 1 and 2 ran one after the other, so host load drifted alike for all of
them. Each ran 3 times; times are the median of runs 2 and 3, and runs at the budget count over those
106. Strict order ran once more at the end, as a control.

| | loops listed | Seeds with a loop | `find_loops` p50 / p90 / max | `_loops` p50 / p90 / max | runs at the budget (Seeds) |
|---|---|---|---|---|---|
| Strict order (best 20) | 156 | 45 | 0.66 / 2.90 / 5.10 s | 1.95 / 5.38 / 6.44 s | 3 (2) |
| draw 0 | 147 | 46 | 0.75 / 2.93 / 5.02 s | 2.12 / 5.47 / 7.63 s | 6 (3) |
| draw 1 | 160 | 46 | 0.84 / 2.85 / 4.57 s | 2.33 / 5.24 / 6.83 s | 6 (4) |
| draw 2 | 140 | 45 | 0.88 / 2.74 / 5.03 s | 2.18 / 5.17 / 7.34 s | 6 (3) |
| Strict order, control run last | 151 | 45 | 0.68 / 2.73 / 4.70 s | 1.92 / 5.28 / 6.98 s | 3 (3) |

- **Timing.** The draws' p90 and maximum fall within Strict order's. Their median is 0.2 to 0.4 s
  higher: Trailheads drawn from the best 60 lie farther apart and read more Segments. They stopped at
  the budget in 6 runs of 106 each, against 3 for Strict order. ADR-0045 measured 8 of 104 for the
  same best 20.
- **Variety.** Over the 3 draws, 247 distinct loops are listed for 48 Seeds, against 156 for 45 Seeds
  in Strict order; 102 of them Strict order does not list.
- **Cost of one draw.** In one draw, 10 to 14 Seeds list a best loop below Strict order's, by 0.005 at
  the median, and in 1 Seed per draw that is no loop at all. Over the 3 draws, only 1 Seed never
  reaches Strict order's best.
- **Triglav.** The Triglav Seed's draws 1 and 2 list "Loop near Stainzer Kogel" (0.8101) in row 1,
  better than Strict order's best (0.8076).

**Considered Options**:
- *3 rows fixed and 7 drawn from rows 4-30, with a tie-aware head* (the recommendation). It keeps the
  top 3 on every page. The captain preferred to draw all 10. In Seed Rankings the two hardly differ,
  and in tied Wishes all 10 lets every equally good area take a turn at the top.
- *7 fixed and 3 drawn.* A calmer page, but a Redraw changes only about 2 rows.
- *A draw on the server, with a `shuffle` per click.* Each Redraw is a full Ranking: 0.8 s, or 3 to
  6 s with loops, held in the server's single Ranking slot and counted against 20 Rankings a minute.
- *T 0.002* (Redraw changes nothing on 13 to 34 % of clicks) and *T 0.01* (fails the lowest-row gate).
- *The draw number in shared links.* It reproduces one sampled table, but a link does not run the
  Ranking.
- *Redraw searching loops again.* It costs seconds per click. New loops come from "Suggest loops" or a
  new Find.

Rejected.

**Consequences**:
- The page no longer lists the same 10 rows on every visit. A Hiker or a support question that needs
  the exact table ticks Strict order, or opens a link with `&strict`.
- `POST /api/rank` takes `draw` (0 to 2³²−1) and echoes it, answers with up to 30 rows, and each row
  has `in_area`.
- `ServingData.rank` takes `draw`, which matters only to the loop search, and is otherwise unchanged.
  The CLI and `dayhike rank` stay deterministic.
- A Find's answer is about 3 times larger. Versions are decided in the 30-row grouping, so a row's
  versions can include a lower one that the 10-row grouping stopped before reaching.
- Loops suggested under one draw stay listed after ticking Strict order. The loop search runs only on
  request.
