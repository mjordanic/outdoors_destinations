---
status: superseded by ADR-0012
---

# Member ways that do not touch the chain are skipped, not jumped

DayHikeGeometry is a walk along OSM member ways. Ingest used to glue a member
that did not share an endpoint onto the current chain anyway (`chain + ids`).
That drew a straight line through empty space (often a kilometre or more),
counted it in Catalog `length_m`, and put the leftover snippet at the end of
`vertices`. Hiking relations often list a short extra way last (a side loop);
OSM member order is not a guaranteed walk.

**Now:** glue only when a member shares an endpoint with the current chain
(reversing the way if needed). Later members are still tried against that
chain. Skipped ways stay in GeometryStore `ways` with `in_chain = 0`. Their
real length is summed on GeometryStore `dayhikes.unconnected_m` (`0` when
every way glued). Catalog `length_m` and `vertices` use the connected chain
only. Leftover-way nodes that never appear on the chain are not in `vertices`.

**Cost:** the stitch is still one pass over member ways (four endpoint checks).
`unconnected_m` is haversine on skipped ways only. Both are noise next to OSM
I/O and writing ~40M vertices. Most relations have no leftovers.

**Not now:** attaching a leftover at an *interior* node, or extracting a
dominant cycle (junk listed first, then a loop that meets the junk in the
middle; 2-core / longest simple cycle / 50 m near-close / 70% coverage).
Typical leftovers are short, listed last, and should be discarded — skip is
enough. Cycle search on a ~20-way hike is milliseconds; it is only dangerous
on a dense mesh of cycles (Johnson-style enumeration). That is a cap problem
for later, not a reason to run it now.

**Considered Options**: Chinese Postman (would walk extras as out-and-back);
always prefer the longest cycle (drops walk-in / lake-circuit approaches);
plot-only “drop long edges” (Catalog length and DEM would still follow the
jump). Rejected for this change.
