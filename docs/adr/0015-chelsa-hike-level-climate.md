---
status: accepted (supersedes the climate source and per-vertex climate of ADR-0010)
---

# Climate is CHELSA V2.1, sampled straight to hike-level means

ClimateNormal comes from CHELSA V2.1 1981–2010 monthly climatologies at 30 arc-seconds: mean temperature (`tas`), precipitation (`pr`) and surface downwelling shortwave radiation (`rsds`). CHELSA downscales with orographic predictors (wind, exposition, boundary layer), so precipitation in the mountains, where most DayHikes are, is better than WorldClim 2.1's, and its normals are a decade more recent. It is released under CC0 1.0 (EnviDat, doi:10.16904/envidat.228.v2.1), more permissive than WorldClim's non-commercial terms. The official EnviDat record for CHELSA V2.1 (https://www.envidat.ch/#/metadata/chelsa-climatologies), checked on 2026-10-26, states the licence as "Creative Commons Zero - No Rights Reserved (CC0 1.0)". `fetch-climate` stores each raster clipped and converted to the units the climate Measures already use (°C, mm per month, kJ m⁻² day⁻¹), so Measure constants did not change.

Climate is no longer stored per vertex. Only the hike-level monthly means in the Catalog are read (`climate_months` and the regime columns), and twelve rows per vertex made the Europe GeometryStore about 32 GB larger and sampling about 7 hours long, one point lookup per vertex per raster. `sample-climate` now reads one raster at a time into memory, indexes every vertex at once, and averages per DayHike with `numpy.bincount`. A vertex counts only when all 36 rasters cover it; a DayHike with no counted vertex has no climate. Each run replaces the climate of every DayHike. LandCover keeps its per-vertex class (ADR-0010).

**Considered Options**: keeping WorldClim and only vectorising the sampling (fast, but keeps the weaker mountain precipitation and the older period). Keeping `vertex_climate` for notebooks (nothing in Ranking reads it). Reading CHELSA windows over HTTP instead of downloading each global file (fewer bytes, but a new fetch path to test). Rejected.
