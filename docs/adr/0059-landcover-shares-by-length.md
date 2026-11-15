---
status: accepted (amends ADR-0010: LandCover shares weigh the line by length, not each vertex once)
---

# LandCover shares by length along the line

ADR-0010 made LandCover a mean along DayHikeGeometry with each GeometryStore vertex counted once. OSM vertices are dense on bends (switchbacks, villages) and sparse on straight stretches (fields, roads), so bendy parts weighed more than their length, and the middle of a long step between two vertices was never read ([future work §9](../future-work.md#9-landcover-along-the-line-not-per-vertex)).

**The rule.** Shares are by length, in haversine metres:

- A step between two vertices of at most 40 m (`ALONG_MIN_M`) goes half to each of its two vertices, as the class `sample-landcover` stores beside each vertex's latitude and longitude.
- A longer step is also read along its length: WorldCover is read at the middles of equal pieces at most 20 m long (`ALONG_STEP_M`), and each read stands for its piece. Its two vertices get none of it.
- A share is the length of that WorldCover class over the length with any class. A vertex or read without a class is left out, and the others share the line. A long step whose reads all lack a class goes half to each of its vertices. When every classified vertex sits on one spot, they count once each.

`sample-landcover` stores the classes read along each long step in GeometryStore `vertices.landcover_along`, on the step's first vertex (one byte per read; NULL for a short step). `sample-trailgraph` stores those of each Segment in `segments.landcover_along`, step by step, so the reads of a step are found again from the Segment's own vertices. `_landcover_fractions` counts both. The Catalog fractions, the facts with the way back and a ComposedHike's facts all use it, so the three agree. A walk carries the reads of each step, also when it walks a Segment backwards or retraces itself. The straight step of a way back across a gap of at most 50 m is not read along.

**What it gives** (DayHikes with a trailhead in notebook 05's region, 42.3–46.9° N, 13.3–19.5° E; three samples of 400 of its 4,581; the truth is WorldCover read every 10 m along each segment; error is the share of the walk put in the wrong class; the stored Catalog fractions after the product's `sample_landcover` on a copy of those hikes):

| Sample | Vertex count: mean / p95 / worst | Vertices by length: mean / p95 / worst | This rule: mean / p95 / worst |
| --- | --- | --- | --- |
| 1 | 3.5 % / 10.0 % / 16.9 % | 1.64 % / 4.55 % / 24.8 % | 1.23 % / 3.23 % / 8.1 % |
| 2 | 3.5 % / 10.1 % / 18.4 % | 1.83 % / 5.79 % / 20.6 % | 1.42 % / 4.45 % / 10.2 % |
| 3 | 3.6 % / 10.2 % / 23.5 % | 1.73 % / 5.58 % / 12.1 % | 1.25 % / 2.99 % / 11.2 % |

**Why the vertices alone are not enough.** Weighting the vertices by length (the middle column, the first version of this ADR) halved the error but left it at 1.6–1.8 %. The stored vertex classes equal a fresh read of WorldCover at each vertex (0 of 350,319 differ). Capping how much of a long step each end vertex may claim makes the error worse (at most 50 m per side: 1.65–1.85 %). Starting the 10 m truth reads at each step's first vertex, not halfway along each 10 m piece, gives 1.58 % on sample 1, so the method of measuring does not explain it either. What was left is the class changing between two vertices where nothing is read. The earlier study's 1.3 % for the vertices by length came from an older build of the region. Its script was not kept, so that number cannot be checked again.

**Why 40 m and 20 m.** On the same samples ([future work §9](../future-work.md#9-landcover-along-the-line-not-per-vertex) has the table), the steps over 40 m read every 20 m are the cheapest option tried that stays under 1.5 % on all three samples. Reading the steps over 50 m every 25 m gives 1.50 % on sample 2. Even reading every step every 10 m leaves about 1 %, because a vertex and the truth's point beside it can fall in different 10 m cells.

**Cost.** On the 1,099 hikes of the three samples (320,636 vertices), `sample-landcover` reads 112,820 more points, 1.35 times as many. With warm tile caches its time did not change measurably: 6.4 s and 8.3 s reading only at vertices against 6.4 s and 7.5 s with the reads along. The GeometryStore grew by 0.3 %. The Europe TrailGraph of 2026-10-28 (1,018,708 Segments, 55,122,520 vertices) needs 32,354,389 reads, so `segments.landcover_along` adds about 32 MB (plus SQLite record overhead) to the serving TrailGraph of 3.29 GB, about 1 %. It is read from disk per walk, like the other per-vertex samples, so the server needs no more memory.

**Storage.** The facts version changes (`landcover-along-steps` with both lengths), so stored similarity facts are refused until `dayhike build-similarity` runs. The Catalog fractions and `vertices.landcover_along` change when `dayhike sample-landcover` runs again, and the TrailGraph's reads when `dayhike sample-trailgraph` runs again. Both also remove the similarity facts. `build-similarity` refuses a GeometryStore whose land cover was sampled before `vertices.landcover_along` existed. When the similarity facts hold land cover, `export-serving` refuses a TrailGraph without `segments.landcover_along` or with a Segment where it is NULL; `sample-trailgraph` adds that column only when WorldCover tiles are there. A TrailGraph without that column is still walked, with every step going to its two vertices. None of this has run on the Europe Catalog yet: the site shows the old shares until `sample-landcover`, `sample-trailgraph` and `build-similarity` run and a new serving export is made. Notebook 05 reads the full Europe similarity store, so its comparison has not been re-run either.

**Considered Options**: keep counting each vertex once (twice the error, for no saving); weigh only the vertices by length (1.6–1.8 %, above the 1.5 % target); read every step every 25 m (1.13–1.37 %, 2.3 times the points); read the steps over 20 m every 10 m (1.01–1.18 %, 2.4 times the points). Rejected.
