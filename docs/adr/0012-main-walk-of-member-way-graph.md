---
status: accepted (supersedes ADR-0009)
---

# DayHikeGeometry is the main walk of the member-way graph

ADR-0009 glued member ways in one pass, only at the chain's two ends. That
removed straight-line jumps but measured badly on the full Catalog (155,844
DayHikes): 20% had a `length_m` more than 5% off their distinct member length.
A way listed out of order was never retried (14,158 DayHikes, 75,851 km lost).
A spur listed mid-order cut off the rest of the route. A stray first member
won over the real route (2,747). A way listed twice was counted twice (11,594
DayHikes, 8,393 km). The length band ran on that broken chain, so 3,414
superroute fragments got in (a 938 km Camino stored as 7.6 km). A loop
started wherever its first listed way began.

**Now:** member ways form a graph, and DayHikeGeometry is its main walk:

- Each distinct way counts once, however often it is listed. A there-and-back
  trail stores its one-way line.
- Ways with role `alternative` (and `alternate`, `variant`, `variation`,
  `detour`), `excursion` or `connection` are Leftovers. `approach`,
  `forward`/`backward` and all other roles are walked.
- Pieces whose endpoints are 50 m or less apart are joined, repeatedly. The
  straight gap is drawn and counted. The longest piece by length is the main
  piece; the rest are Leftovers.
- Loop or line: after shortcuts are dropped, a piece whose biggest cycle is at
  least 50% of its length is a loop.
  - Loop: shortcuts (the shortest path between two odd junctions on a cycle)
    are dropped until it closes. Dead ends are Leftovers, except at most one
    stem: one whose far end is within 200 m of an access point (parking or a
    road you can drive on), or else the only dead end when it is 1 km or
    longer. The 5 km Trailhead radius is far too wide for this test: almost
    every spur would pass. The stem's far end is
    the Trailhead. Without a stem, the loop starts at its vertex nearest an
    access point.
  - Line: extra dead ends are dropped shortest first until two remain, and
    variants (the shortest path on a cycle) until it can be walked once.
  - A path is dropped only if the piece stays connected.
- The walk visits every remaining way once, starts at the Trailhead and
  follows member order where it has a choice (loop direction).
- Consecutive ways share a node or are a joined gap of 50 m or less. No other
  jump exists.
- The length band and superroute rule use the main walk's length.

GeometryStore `ways.in_chain` is 1 when any part of the way is walked;
`ways.leftover` holds the reason otherwise (`role`, `repeat`, `disconnected`,
`dead_end`, `shortcut`). `dayhikes.unconnected_m` is member length minus
walked length, in metres.

**Considered Options**: keep the one-pass chain and retry skipped ways (fixes
ordering only, not spurs, repeats or loops); walk every way once (Euler, keeps
side trips and shortcuts in the length); Chinese Postman (walks extras out and
back); always the longest cycle (drops walk-ins and linear routes); a fixed
metre cutoff for spurs (no single value fits 1–20 km hikes); never join gaps
(42% of extra pieces are 50 m or less from the main piece, about 17,500 km of
trail). Ferry and bus ways stay counted as length, and that is left for later.
