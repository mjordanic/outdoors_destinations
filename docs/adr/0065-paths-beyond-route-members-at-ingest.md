---
status: accepted (amends ADR-0037)
---

# Paths beyond route members at ingest

Ingest reads the member ways of hiking relations and, since ADR-0037, graded paths. Other paths that no relation lists never reach the snapshot or the TrailGraph. So an outing that is not one relation ("Peak X from village Y", #124) cannot become a hike, and neither can a trail that is mapped as same-name `highway=path` ways with no relation, as most US trails are (#125). Both need those paths. The risk is memory: a full Europe ingest already peaks at about 8 GB RSS, plus swap, while it builds the TrailGraph.

**The option.** `dayhike ingest --paths LEVEL` (`build_catalog(paths=...)`, `PATH_LEVELS`). Each level adds to the one before:

- `graded` (the default): graded paths only, as in ADR-0037.
- `named`: also every way with a `name` and `highway=path|footway|bridleway|track` that no hiking relation lists, except `footway=sidewalk|crossing`.
- `unnamed`: also every `highway=path`, named or not.

Beyond `graded`, the catalog extract adds the filter `w/highway=path,footway,bridleway,track`. osmium cannot also require a name, so the extract keeps every such way and the ways pass picks the ones it needs. Changing the level changes the filter list, so the next run cuts the extract again. These paths go to the work-file tables that held only graded paths, renamed `path_ways` and `path_nodes` (`INGEST_WORK_SCHEMA` 10). They join the TrailGraph just as graded paths do: no relation id, split, snapped and linked like any other way. Beyond `graded`, the snapshot that the DayHike assembly and the built-hike sources read also loads those tables (graded paths too), so #124 and #125 can walk them. No relation lists them, so the relation DayHikes do not change. The level is part of the work-file fingerprint, so a work file of another level is never resumed.

**Measured** on four Geofabrik extracts: Slovenia (2026-09-16), Bavaria and Finland (2026-11-15) and Colorado (2026-11-13), on the 16 GB Mac. Each run is `dayhike ingest --fresh` on an extract cut beforehand, with the default worker count (one). The peak RSS is that of the Python process. Each level's cut with osmium takes 4–10 s and peaks at 1.8–2.5 GB in osmium, whatever the level. Wall times are rough: another worker's builds kept the load average near 30.

| Region | Level | Path ways in no relation | Snapshot ways / nodes | TrailGraph ways / nodes | Segments | Trail km | Peak RSS | Wall time |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Slovenia | graded | 5,373 | 39,882 / 765,614 | 45,255 / 892,499 | 19,564 | 14,539 | 988 MB | 95 s |
| | named | 10,692 | 50,574 / 980,143 | 50,574 / 980,143 | 27,020 | 15,956 | 1,069 MB | 121 s |
| | unnamed | 49,035 | 88,917 / 1,505,582 | 88,917 / 1,505,582 | 94,085 | 24,500 | 1,183 MB | 118 s |
| Bavaria | graded | 20,814 | 267,566 / 2,305,016 | 288,380 / 2,641,319 | 101,771 | 66,800 | 2,688 MB | 235 s |
| | named | 54,779 | 322,345 / 2,924,159 | 322,345 / 2,924,159 | 143,690 | 75,304 | 2,809 MB | 388 s |
| | unnamed | 319,417 | 586,983 / 4,482,942 | 586,983 / 4,482,942 | 575,790 | 109,203 | 4,081 MB | 421 s |
| Finland | graded | 5,228 | 26,877 / 525,888 | 32,105 / 615,808 | 11,934 | 12,283 | 608 MB | 164 s |
| | named | 44,816 | 71,693 / 1,601,053 | 71,693 / 1,601,053 | 53,165 | 45,726 | 1,214 MB | 319 s |
| | unnamed | 331,785 | 358,662 / 4,309,656 | 358,662 / 4,309,656 | 503,143 | 105,762 | 2,481 MB | 397 s |
| Colorado | graded | 2,226 | 5,163 / 272,292 | 7,389 / 443,505 | 3,801 | 7,770 | 393 MB | 36 s |
| | named | 31,090 | 36,253 / 2,052,385 | 36,253 / 2,052,385 | 45,358 | 52,856 | 1,575 MB | 144 s |
| | unnamed | 75,240 | 80,403 / 2,687,352 | 80,403 / 2,687,352 | 121,885 | 62,503 | 1,917 MB | 153 s |

At `graded`, the snapshot leaves graded paths out, as before, so its numbers are the hiking tables only. In every region the Catalog's DayHikes (id, name, length) are identical at all three levels. The peak always comes at the end of the TrailGraph build (Junctions and Segments). Loading the snapshot peaks lower: in Bavaria at 1,244 MB at `graded` and 1,237 MB at `named`.

**What `named` adds**, in km of ways in no hiking relation:

| Region | Named path | Named track | Named footway | Named bridleway | (Unnamed path) |
|---|---:|---:|---:|---:|---:|
| Slovenia | 526 | 807 | 85 | 0 | 8,546 |
| Bavaria | 1,763 | 5,880 | 858 | 6 | 33,909 |
| Finland | 3,050 | 29,651 | 736 | 16 | 60,053 |
| Colorado | 17,140 | 26,903 | 1,013 | 43 | 9,649 |

Named tracks are most of what `named` adds: 89 % in Finland, 60 % in Colorado and 69 % in Bavaria. In OSM a track is a road for farm or forest vehicles, not a trail. #125 asks for named `path|footway|bridleway` only.

**Europe, extrapolated.** No full Europe ingest was run. Today the TrailGraph holds about 1.4 M km (1.25 M km of member ways and 0.145 M km of graded paths, ADR-0037) on about 72 M nodes (62 M in the work file and 9.7 M on graded paths only). In the three European regions, `named` adds 0.070 (Slovenia), 0.121 (Bavaria) and 0.099 (Finland) km of way per km² of the region, at 62, 33 and 29 TrailGraph nodes per added km. Spread over the roughly 7 M km² of the Europe extract (Geofabrik's, which leaves out Russia), each region's figures give 0.5–0.85 M km and 20–30 M nodes, +28–42 % of the TrailGraph's nodes. Scaling today's 8 GB peak in proportion gives about 10–11 GB. The marginal cost each region measured, 925 (Slovenia), 428 (Bavaria) and 615 (Finland) bytes of peak per added TrailGraph node, would add 12–28 GB. Without named tracks the density falls to 0.030 (Slovenia), 0.037 (Bavaria) and 0.011 (Finland) km/km²: about 0.08–0.26 M km and 2–13 M nodes, +3–18 % of nodes, about 8.2–9.4 GB in proportion. `unnamed` multiplies the TrailGraph's Segments by 4–40 and is out of reach for Europe.

**Decision.** The default stays `graded`. `named` does not fit a full Europe ingest on the 16 GB Mac with any margin: today's run already swaps, and even the proportional estimate puts it above 10 GB. Per region it is cheap: 0.1–1.2 GB above `graded` and at most 2.8 GB (Bavaria) in the measured extracts, so the prototypes of #124 and #125 use `--paths named` on regional extracts. A Europe build with more paths waits for those prototypes to decide which paths they need. If they need named paths, footways and bridleways but not tracks, the Europe cost falls to the "without named tracks" estimate. If they need tracks too, the TrailGraph build has to take less memory first. `unnamed` stays for measurement only.

**Considered Options**: making `named` the default now (the Europe estimate is 10–11 GB or more, past what the Mac holds without heavy swap); a separate on/off switch for each highway type (no prototype has asked for one yet; one level covers what #124 and #125 need first); keeping the extra paths out of the snapshot, as graded paths are kept out (#124 and #125 build hikes from the snapshot, so they would see none of them); a full Europe count to replace the extrapolation (it reads the 35 GB Europe PBF, which is not a run to do beside another worker's builds; worth doing before a Europe build with `named`).

**Consequences**: no rebuild is needed while the default is `graded`. Work files of schema 9 are not resumed. A Catalog built with `--paths named` has the same DayHikes. Only its TrailGraph is larger, so ComposedHikes and SuggestedLoops may walk named paths and tracks that no relation marks.
