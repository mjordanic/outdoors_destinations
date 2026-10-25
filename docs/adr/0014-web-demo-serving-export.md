---
status: accepted
---

# The web demo answers from a serving export, in one container with Valhalla

The public demo must run in a small, scale-to-zero container (Cloud Run, 2 GiB), but the data behind it is 21 GB of GeometryStore and a 130 MB Catalog whose `Catalog.open` alone takes 4 s and 0.77 GB. So `dayhike export-serving` writes, once, a directory with only what the page reads: the similarity directory (ADR-0013) as is, Trailheads as a numpy array, and one SQLite file with each DayHike's name, its DayHikeGeometry simplified at 10 m, its ElevationProfile at 128 steps, and the amenity points the Measures count. The server memory-maps the arrays, reads the SQLite file per request, and searches names in one folded string held in memory; it never opens the Catalog. Ranking goes through `rank_rows`, the store-level core of `rank_dayhikes`, so the demo and the CLI rank identically.

DriveTime runs as in ADR-0013 (highway tiles): the export links the tile extract, writes a `valhalla.json` for the container, and snaps every Trailhead once, so no visitor pays for snapping. The drive filter is a pre-filter (`allowed_ids`) and is capped at 4 hours.

**Considered Options**: `Catalog.open` in the server (memory and a 4 s start on every cold start); a separate Valhalla service (a second always-billed or cold-starting container for a rarely used filter); PostGIS or another database server (a running service for read-only data); per-vertex geometry and climate in the export (gigabytes the page never draws). Rejected.

**Consequences**: the export must be rebuilt after the Catalog, the similarity facts or the tiles change; `meta.json` is written last and checked on open, and the copied similarity directory keeps its own version check. The served profiles and points are as fresh as the export.
