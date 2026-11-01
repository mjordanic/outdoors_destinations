---
status: accepted (amends ADR-0002, ADR-0006 and ADR-0014)
---

# Built DayHikes in the Catalog

Around Briançon most hikes are mapped as node-network legs, one relation per signpost-to-signpost leg, and the long tours there are multi-day relations. Neither is a day's outing as one relation. NetworkHikes (legs chained into a day's walk) and DayStages (day-sized pieces of a long tour) fill that gap, and both are walks that no single OSM relation is. Until now a Catalog DayHike was exactly one relation and its id was the relation id.

**A built DayHike is a Catalog DayHike built from OSM data rather than read as one relation.** Ingest keeps the relation DayHikes first. Then each *built-hike source* in `BUILT_HIKE_SOURCES` gets the snapshot, the relation DayHikes and the main walk of any relation (`BuildContext`), and returns walks (`BuiltWalk`): ordered vertices from the Trailhead, the OSM ways walked, a label, a kind and the relations it is built from. From then on it is a DayHike like any other: GeometryStore, sampling, similarity, Twins, export, map, search, Ranking and the page take it with no special case.

- **Id.** A hash of the walk's Route (ADR-0018: start node, then each OSM way walked and the node where the walk leaves it), mapped into [2^40, 2^53). OSM relation ids are about 2.2 × 10^7, so the two never meet, and every id is a JavaScript-safe integer. A rebuild that leaves the walk unchanged gives the same id, so a `#hike=` link to it survives the rebuild. Two sources that build the same walk give one DayHike.
- **New fields** on every DayHike, Catalog `dayhikes` and export `hikes`: `kind` (`relation`, `network` or `stage`), `parts` (the OSM relation ids it is built from; empty for a relation) and `route` (its Route text; NULL for a relation). The NameOrigin of a built DayHike is its kind.
- **GeometryStore.** A built DayHike's rows are its walk as the source gave it: each way it walks once, in walk order, with the metres walked on it; no Leftover and no relation tags.
- **Page.** The detail of a built DayHike links the OSM relations it is built from in place of the single relation link, links to itself (`#hike=id`), and beside a liked hike offers "Edit on the map": its Route opens as a ComposedHike to change. `/api/hikes/{id}` and `/api/compose` both carry `built` (null unless built). Export format 5.

**Consequences.** The Catalog and export schemas gain three columns; a Catalog from before still opens and exports, as relations only. `kept` in the ingest report counts built DayHikes too. With no source registered the Catalog is the same, row for row, as before. Each source owns its own rules: its length band, its Twin check against the relation DayHikes (with the Twin rule of `similarity`), its cap per start.

**Considered Options.** Store NetworkHikes and DayStages as ComposedHikes (rejected: a ComposedHike is never stored, so it would not be on the map, in search or in a Catalog-only Ranking). Number built DayHikes in sequence, or by negative ids (rejected: the id changes with every rebuild, or the page reads it as a relation; a hash of the Route keeps links). Hash the vertices rather than the Route (rejected: the same walk then gets a new id when a node only moves).
