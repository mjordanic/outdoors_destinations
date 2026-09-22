# 01: Sample LandCover onto the GeometryStore and into Ranking

**What to build:** After a Catalog exists, the Hiker samples a local land-cover
raster onto DayHikeGeometry. Each covered vertex stores its class in the
GeometryStore. The Catalog stores fractions of tree, shrub, grass, bare, and
permanent snow. Crop, built-up, water, wetland, and moss stay in the leftover
share. Ranking then prefers a forested neighbour of a forested Seed over a
barer one. Vertices outside the raster stay unset, and a DayHike with no
covered vertices stays in the Catalog with missing fractions. Sampling an
existing Catalog adds the column in place and refits the scaler. It does not
download tiles and does not add or drop DayHikes.

**Status:** ready-for-agent

## Blocked by

- None (can start immediately)

**Complexity:** high

**Prior art:** FABDEM sampling (`sample_dem`, DEM tests with a tiny GeoTIFF),
ADR-0008, ADR-0010, PRD stories 1–12. `CONTEXT.md` LandCover, GeometryStore,
FeatureVector, Ranking. WorldCover class codes 10, 20, 30, 60, and 70.

- [x] A fresh ingest creates an empty land-cover class on each vertex and empty
      fraction columns; ingest does not download a raster
- [x] Sampling a tiny raster writes the class onto covered GeometryStore
      vertices and leaves uncovered vertices unset
- [x] Catalog fractions use only sampled vertices; other class codes count in
      the denominator only; a hike with no samples keeps NULL fractions and
      stays in the Catalog
- [x] Sampling an already-built Catalog adds the missing column in place and
      refits the scaler
- [x] With a forested Seed, a more forested DayHike outranks a barer one that
      otherwise matches
- [x] Fractions do not change when a month is later selected
- [x] Tests use a synthetic raster on the committed OSM fixture and do not
      download WorldCover
