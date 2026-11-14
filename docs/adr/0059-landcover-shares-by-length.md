---
status: accepted (amends ADR-0010: LandCover shares weigh each vertex by length, not once)
---

# LandCover shares by length along the line

ADR-0010 made LandCover a mean along DayHikeGeometry with each GeometryStore vertex counted once. OSM vertices are dense on bends (switchbacks, villages) and sparse on straight stretches (fields, roads), so bendy parts weighed more than their length ([future work §9](../future-work.md#9-landcover-along-the-line-not-per-vertex)).

**The rule.** Each vertex stands for half of each of the two segments beside it (an end vertex half of its one segment), in haversine metres. A share is the length held by vertices of that WorldCover class over the length held by every vertex with a class. A vertex without a class is left out, and the others share the line. When every classified vertex sits on one spot, they count once each. The points read do not change: the class of each vertex is still the one `sample-landcover` stores beside its latitude and longitude, so there is no new raster pass and nothing new to store. Only the final count changes, in `_landcover_fractions`. The Catalog fractions, the facts with the way back and a ComposedHike's facts all use it, so the three agree.

**What it gives** (DayHikes with a trailhead in notebook 05's region, 42.3–46.9° N, 13.3–19.5° E; three samples of 400 of its 4,581; the truth is WorldCover read every 10 m along each segment; error is the share of the walk put in the wrong class):

| Sample | Vertex count: mean / p95 / worst | By length: mean / p95 / worst |
| --- | --- | --- |
| 1 | 3.5 % / 10.0 % / 16.9 % | 1.6 % / 4.5 % / 24.8 % |
| 2 | 3.5 % / 10.1 % / 18.4 % | 1.8 % / 5.8 % / 20.6 % |
| 3 | 3.6 % / 10.2 % / 23.5 % | 1.7 % / 5.6 % / 12.1 % |

The mean error halves, as the earlier study found (2.7 % to 1.3 % on an older build of the region), but it stays above that study's 1.3 %. The worst hike can get worse: one vertex alone in a long segment now carries half of that segment, where the class may change midway. Reading points along long segments (about 1.2 % in that study) is the expensive option and stays parked in §9.

**Issue #137 stays open.** Its acceptance asks for three things this change does not yet give:

- **The 1.5 % target was missed.** The mean error by length is 1.6–1.8 % on the three samples of 400 regional hikes above, not below 1.5 %.
- **Notebook 05's comparison was not re-run.** Notebook 05 reads the full Europe similarity store, and this round had no Europe-scale runs, so the table above comes from a regional sample and the notebook comparison is not reproduced on this branch.
- **The data reruns are pending.** `dayhike sample-landcover` and `dayhike build-similarity` have not been run on the Europe Catalog (see Storage).

**Storage.** The facts version changes (`landcover-by-length`), so stored similarity facts are refused until `dayhike build-similarity` is run. The Catalog fractions change only when `dayhike sample-landcover` is run again, which also removes the similarity facts. Neither has been run on the Europe Catalog yet, so the site shows the old shares until both run and a new serving export is made.

**Considered Options**: keep counting each vertex once (twice the error, for no saving); read WorldCover every 25 m along the line (a new raster pass and extra points to store, for little more gain). Rejected.
