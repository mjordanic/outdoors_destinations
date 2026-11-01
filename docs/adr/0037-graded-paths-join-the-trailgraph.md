---
status: accepted (amends ADR-0018)
---

# Graded paths join the TrailGraph

Around Briançon the local trails are mapped as signpost-to-signpost legs and most paths belong to no relation. Only 50 of the 463 km of paths and tracks within 5 km of the town were in the TrailGraph, so composing and SuggestedLoops found almost nothing there: 2 loops of about 6 km, none of 10 or 15 km. Many of the missing paths carry a `sac_scale` grade, which mappers give to hiking paths.

**What joins.** Every `highway=path` way whose `sac_scale` is one of the six grades (`hiking` to `difficult_alpine_hiking`) and that no `route=hiking` or `route=foot` relation lists becomes part of the TrailGraph, with no relation id. It is split, snapped, linked and merged like any member way, so a graded path that meets a marked trail at a shared node meets it at a Junction. Tracks and footways with a `sac_scale`, paths without one, and a `sac_scale` that is no grade stay out. The ADR-0018 option "unmarked paths too" was rejected because it doubles the graph and invites walks no one maintains; a grade is a narrower signal than "any path", although it is a difficulty grade, not a waymark.

**Ingest.** The catalog extract's filter list gains `w/sac_scale`, so the next ingest of a full PBF cuts the extract again. In the ways pass, a graded path that is no hiking member way goes to its own work-file table, `graded_ways`, and the nodes only graded paths use go to `graded_nodes`. The DayHike assembly and the GeometryStore read only the hiking tables, so the Catalog is unchanged and their memory is too; only `write_trailgraph` reads both. The work-file schema is 8, so an older work file is not resumed.

**Naming.** A trail names a walk only when the walk follows it for at least 30 % of its length (`TRAIL_NAME_SHARE`), so 150 m of GR 5 does not name 7 km of graded paths; of those, the two walked longest name it, as before. A walk on graded paths only, or with no trail that long, has no trail names. The proposed name falls back to "Your hike from" the named hiking place nearest its start, and a SuggestedLoop is named "Loop from/near" such a place as before (ADR-0022). The composer calls a Segment with no route name an "Unnamed trail" and the detail says "Drawn from trail segments", not "marked trail segments".

**Measured on the Hautes-Alpes cut** (6.25–7.05 E, 44.70–45.10 N, smart extract from the 2026-09-17 Europe snapshot; develop against this change, same pipeline):

| | Before | After |
|---|---:|---:|
| Catalog DayHikes | 324 | 324 (byte-identical rows) |
| TrailGraph trail km, whole cut | 4,275 | 5,594 (+31 %) |
| Trail km within 5 / 10 / 20 km of Briançon | 63 / 233 / 1,119 | 130 / 416 / 1,754 |
| Segments / Junctions | 2,551 / 1,999 | 6,534 / 5,463 |
| Serving `trailgraph.sqlite` | 15.4 MB | 22.2 MB (×1.44) |
| Distinct loops `find_loops` closes from every Junction within 10 km, 6 / 10 / 15 km | 7 / 7 / 12 | 101 / 163 / 180 |
| The same from every Junction within 5 km | 2 / 2 / 1 | 6 / 6 / 17 |
| `find_loops` from four fixed starts in and around the town, 6 / 10 / 15 km | 2 / 0 / 0 | 2 / 0 / 0 |
| `ingest` peak RSS (with the TrailGraph) | 987 MB | 1,160 MB |

The four fixed starts gain nothing: the graded paths there end on unmarked paths and tracks that stay out, so no 10 or 15 km loop passes through those Junctions. The loops are 2–10 km out. Seeding SuggestedLoops from the Origin, not only from the best DayHikes' Trailheads, is what reaches them.

**Europe estimate** (`osmium tags-filter europe-latest.osm.pbf w/sac_scale`, 6 min, then one pyosmium pass): 364,101 graded paths, **145,235 km**, in no hiking relation, beside 117,087 km of graded paths that are already member ways. The TrailGraph holds 1.25 M km today, so that is +11.6 % of its length (Alps box 67,777 km of it; Hautes-Alpes 1,277 km, which matches the cut). They use 9.7 M nodes no hiking way uses. Graded paths cost more bytes per km than the marked network (5.2 KB/km against 3.6 KB/km on the cut: shorter ways, more Segments), so the 3.29 GB export TrailGraph is projected at about **3.8 GB** (3.67 GB by length alone). Segments grow by about 3 per km of graded path, from 1.02 M to about 1.46 M. The whole-graph load behind ComposedHike way-backs (`TrailGraph._network`) now reads its rows in chunks of 100,000 into arrays instead of one `fetchall` of tuples. Its transient peak per Segment drops from about 390–430 bytes to 260 (measured on the cut), so the Europe load stays near 380 MB with graded paths, against about 440 MB today. The arrays it keeps grow from about 44 MB to 63 MB.

**Considered Options**: every path (`highway=path|footway|track`), which near Briançon would add 413 km within 5 km against 67 km of graded paths and lists tracks no hiker would pick; graded paths only in mountain regions, such as an Alps box (half the km, but the captain wants every hike everywhere); `trailblazed`/`osmc:symbol` on ways (only 207 of the 12,304 ways within 20 km of Briançon carry a waymark tag themselves; the waymarks sit on relations); graded paths as Catalog DayHikes (they are pieces, not outings). Rejected.

**Consequences**: one full rebuild (ingest, `sample-trailgraph`, `build-similarity`, `export-serving`) brings them in; the WayBack and the facts of the way back can change where a graded path is shorter. A SuggestedLoop or a ComposedHike may now walk paths that no relation marks; its `sac_scale` shows their grade. `LOOP_READ` caps the Segments one loop search reads, and the denser graph reaches it in a smaller area.
