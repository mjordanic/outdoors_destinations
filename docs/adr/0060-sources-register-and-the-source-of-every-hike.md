---
status: accepted (amends ADR-0014 and ADR-0038)
---

# A sources register, and the Source of every hike

Every DayHike comes from OpenStreetMap today, so nothing recorded where a hike came from: attribution lived only per file (`catalog_meta`, the GeometryStore's `*_attribution` keys). Hikes from open official registries (Ireland, Finland, Norway and others), heights from more than one DEM and an ODbL-only download all need each hike to say where it came from, under which licence and how to credit it. Licence research also kept re-checking the same candidates, because the verdicts lived in issue comments.

**The register.** `docs/sources.csv` has one row per data source the project uses, plans, is checking, watches or rejected: `id`, `name`, `kind`, `coverage`, `url`, `licence`, `licence_url`, `odbl_compatible`, `commercial_ok`, `status`, `reason`, `checked` (the date the licence was last read) and `issue`. A CSV renders as a searchable table on GitHub and diffs cleanly in a pull request; a database file would be opaque in review. `tests/test_sources.py` checks every row has its fields and allowed values, and that every `used` row is named in `DATA-LICENSES.md`. A new source starts as a register row in a pull request (`CONTRIBUTING.md`).

**The Source of a hike.** The Catalog's `dayhikes` and the serving export's `hikes` get two columns:

- `source`: a register id, `osm` for every hike today.
- `source_ref`: the record's own id there. An OSM relation DayHike is `relation/{id}`. A built DayHike (ADR-0038) keeps `osm` and points at what it was built from: a DayStage at its tour, `relation/{tour id}`; a NetworkHike, which chains several legs, at the signpost its Route starts from, `node/{id}`.

Licence, credit text and URL are looked up from the register by `source`, not copied into every row. Ingest refuses a `source` the register does not list as `used`. `/api/hikes/{id}` returns `source` as `{id, ref, url}`, the url the record's OpenStreetMap page for an `osm` hike. A ComposedHike is drawn from OSM trail segments and is no record of its own: `osm` with no `ref`. The hike panel adds a source line only for a hike not from OSM; an OSM hike's head already links its relation.

**Existing Catalogs and exports.** A Catalog or export from before this has no `source` columns. Both are all OpenStreetMap, so they read as `osm` with the `source_ref` derived from the kind, parts and Route as above: no rebuild is needed. The export format stays 5. A fresh `dayhike export-serving` writes the columns; a fresh ingest writes them into the Catalog.

**Not done here.** A per-hike `dem_source` is added only when a build mixes DEMs; until then the file-level elevation attribution stays (ADR-0030). A per-record link for a source other than OSM comes with that source's adapter.

**Considered Options**

- **The licence and credit text in every row.** Rejected: one licence change would mean a rebuild, and every row would repeat the same text.
- **A database file or a Python table for the register.** Rejected: opaque or awkward to review; the CSV is readable on GitHub and by `csv` in the code.
- **A NetworkHike pointing at all its legs.** Rejected for now: `parts` already lists them, and one stable reference per hike is enough to find it again.
