---
status: accepted (builds on ADR-0038; keeps ADR-0033's node-network rule)
---

# NetworkHikes

Around Briançon, and in the Benelux and German *knooppunt* networks, trails are mapped as a node network: one relation per signpost-to-signpost leg (`network:type=node_network`). A leg is no outing, so ingest drops it (ADR-0033), and nothing turned chains of legs into hikes: the classic Briançon walks existed only as fragments. Near Briançon 544 of the 603 French relations are legs.

**Ingest builds NetworkHikes, built DayHikes (ADR-0038) chained from node-network legs.** `network_source` is the first entry in `BUILT_HIKE_SOURCES`. It takes every `network:type=node_network` relation ingest read (`route=hiking` or `foot`, any length, legs under 1 km too) and runs `network_hikes`:

- **Leg graph.** Each leg's main walk; leg ends within the 50 m gap join are one signpost.
- **Starts.** A signpost within 500 m of parking or 300 m of a place starts chains. Signposts within 1 km of a start's first one start with it, so the cap holds per place and not per signpost.
- **Chains.** From a start, every line that passes no signpost twice and every loop back to its first signpost, at least two legs, **3–25 km** (the captain's choice: day-sized; multi-day comes from relations and DayStages). The search tries at most 20,000 legs per start.
- **Selection, per start.** Loops first, then lines that end within 200 m of a named Peak, Hut, Viewpoint or lake, then other lines; within each, nearer 10 km (by ratio) first. A chain is kept only if more than half of it (`VERSION_DIFFERS`) lies off every chain kept before it there, and at most **4** per start (the captain said about 3–5). None is a **Twin** (`similarity`'s rule and sampling, not a copy: 80 % both ways within 30 m) of a better chain or of a relation DayHike that starts within 50 km of a leg. Twins alone do not stop a dense network's detour variants that share 60 %: the distinctness rule and the cap do.
- **Names.** "Loop from {start} via {destination}" or "{start} – {destination}". A signpost is named by the end names its legs share, else by a named guidepost (`information=guidepost`) within 50 m; a start without one takes the nearest place. NameOrigin `network`.

**Points read for this alone.** Ingest's filter list gains `wr/water=lake,reservoir,pond` and `n/information=guidepost`. A named `natural=water` of those kinds is a lake point (its first node); a named guidepost names signposts. Both reach only `network_source` (`OsmSnapshot.destinations`, `.guideposts`): they are no amenity, no named point for relation ends and not in the GeometryStore, so every relation DayHike is unchanged. Named Peaks, Huts and Viewpoints, already read, are destinations too.

**Consequences.**

- **Briançon** (the 2026-09-17 cut, develop before against after): Catalog hikes with a Trailhead within 5/10/20 km go from 1/7/111 to 22/60/330. 360 NetworkHikes join 370 relation DayHikes; 21 start within 5 km, among them Croix de Toulouse, the Prorel and the walks through Puy-Saint-Pierre; Col de Granon starts 7.5 km out. No start keeps more than 4, and no NetworkHike is a Twin of another Catalog hike. 89 go to or via a lake. On the first page for a 10 km Wish within 20 km, 5 of 10 rows are NetworkHikes, but only 1 row starts within 10 km: the Ranking orders by similarity, not distance.
- **Cost.** Ingest runs the chain search after the relations. Lakes are the costly read: Europe's `water=lake,reservoir,pond` objects carry 51.4 M nodes, a 333 MB extract (+29 % on the 1.14 GB catalog extract), for 186k named lake points; ingest of that extract alone took 18 min (on a busy machine), against about 3 h for a Europe ingest. Nothing of it reaches the export, the image or the container's memory. Guideposts add 0.55 M nodes (9 MB). Dropping lakes is one filter line.
- **Europe** (an extract of the 2026-09-17 snapshot with only the node-network relations, parking, places and destinations): 121,945 legs give **55,201 NetworkHikes** (23,720 loops, 31,481 lines; 6.0/15.0/23.5 km at the 10th/50th/90th percentile) from 29,512 starts, most in the Netherlands and Belgium. That is an upper bound: without the other relations no Twin of a relation DayHike is removed. It is over the 50k the coverage plan set as a checkpoint; the captain chose to report it and not cut coverage.
- **Flood check** (a Belgian and Dutch Limburg *knooppunt* cut, 5.2–5.9 E, 50.8–51.3 N): 1,684 NetworkHikes from 993 starts beside 769 relation DayHikes, 1.7 per start, none over 4. Of the 1,036 pairs that share a start, none lies 60 % on the other both ways (median 17 %, most 55 %), and no NetworkHike is a Twin of another Catalog hike. Chains there run 3–40 legs.
- With more NetworkHikes in an area, a relation DayHike there faces more neighbours in a Ranking; the GroupingRadius (ADR-0032) still lists one row per area.

**Considered Options.**

- Relax the node-network rule or the 1 km minimum (rejected: 26,000 leg fragments Europe-wide, each no outing).
- SuggestedLoops alone (ADR-0020, ADR-0036): composed at query time, so not on the map, in search or in a Catalog-only Ranking, and only loops.
- Twins alone, no cap (rejected: a dense network's detour variants share about 60 % and are no Twins; the cap and the distinctness rule bound the rows per start, which a Europe-wide loop precompute, rejected in ADR-0020, did not).
- Chains up to about 60 km (rejected by the captain: more rows and near-copies; long tours come from relations and DayStages).
- Lakes left out (rejected: near Briançon a quarter of NetworkHikes go to a lake, and the cost is ingest time only).
