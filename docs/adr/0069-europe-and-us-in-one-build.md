---
status: accepted (amends ADR-0065 and ADR-0066: the paths level is per input; the deploy's memory set from the data image since ADR-0081; the sites deployed are cut from the Europe and US export since ADR-0082)
---

# Europe and the US in one build, Europe alone still a build

The 48-state US preview (2026-11-17) built 61,355 US hikes and sized hosting: `dayhike serve` opens one export, so Europe and the US on one site take one combined build; that service needs 1 vCPU / 4 GiB (about $5/month at moderate traffic, a ceiling of about $171/month at 2 instances), while Europe alone runs on today's 2 GiB. The captain: "I want to deploy together. But i want to be able to deploy only europe if i want. So building only europe should be possible."

Regions were already configuration (TOML files): `fetch-extracts`, `fetch-dem`, `fetch-landcover` and `fetch-climate` take `--region` several times, and `export-serving --region` names the regions in `meta.json`, so the pages say "across Europe and the contiguous United States". One thing could not be chosen per region: the **paths level** of ingest. Europe is built at `graded`; the US needs `trails`, the level that builds named-trail hikes (ADR-0066). One level for a combined build would either leave the US without three quarters of its hikes or add named trails all over Europe, a Europe the captain has not asked to change.

**The selection mechanism is the inputs and their levels.** No new "build profile" exists; three builds are three ways to call the same commands:

- **Europe only** (the default, unchanged): `dayhike ingest data/osm/europe` at `graded`, `export-serving` without `--region` or with `--region europe`. Same filters, same snapshot, same DayHikes as before; the service stays at 2 GiB.
- **US only**: `dayhike ingest data/osm/us --paths trails`, `export-serving --region us-48`.
- **Europe and US**: `dayhike ingest data/osm/europe data/osm/us --paths data/osm/us=trails`, `export-serving --region europe --region us-48`. One Catalog, one export, one data image; the service needs 4 GiB.

`--paths` now also takes `INPUT=LEVEL` (repeatable; `build_catalog(paths_of={input: level})`): that input, a file or a directory of files, is read at that level and every other at `--paths LEVEL` (default `graded`). Ingest keeps the level per file: its catalog extract filter, which ways it reads, and which of the paths in no relation enter the snapshot. Only the paths of files read beyond `graded` enter the snapshot (`snapshot_path_ways`, `snapshot_path_nodes`; `INGEST_WORK_SCHEMA` 12), so Europe's graded paths stay out of it as in a Europe build and no named trail is built from them, while every file's paths still join the TrailGraph as before. A level for something that is not an input is refused. The rest of the build is per Catalog, not per region: DEM, land cover and climate cover every region's box, `sample-*`, `build-similarity` and `export-serving` read the one Catalog, and every hike name is unique across both regions (ADR-0068).

`us-48` is a packaged region: "the contiguous United States", Geofabrik's four groups that hold the 48 states and DC (`us-midwest`, `us-northeast`, `us-south`, `us-west`; `us-pacific`, Alaska and Hawaii, is left out), the preview's box. Its four extracts overlap at their edges, which ingest already handles (one row per OSM id).

`docs/deploy.md` gives the three builds and their memory. The deploy workflow is unchanged: it deploys whichever data image `DAYHIKE_DATA_IMAGE` names, and memory stays as the service was set, so moving to a combined data image is one `gcloud run services update --memory 4Gi` with it, and back to Europe alone one with `2Gi`.

**Considered Options**: two services, one per region (two cold starts and two deploys; the preview recommends one); merging two Catalogs or two exports after their builds (similarity, Twins and the TrailGraph are per Catalog: a merge tool would redo them); one paths level for the whole build (changes Europe or starves the US); a `paths` key in the region TOML read by ingest (ingest reads files, not regions, and a region's files may be clipped or renamed, as the preview's 48-state clip was). Rejected.
