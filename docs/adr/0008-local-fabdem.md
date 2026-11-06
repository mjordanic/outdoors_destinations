---
status: accepted (Copernicus GLO-30 an alternative beside FABDEM since ADR-0030; SolarAspect in production as the `southness` Measure)
---

# FABDEM is a local snapshot, sampled after ingest

Ingest still reads OSM `ele` only, so a Catalog can be built without rasters.
Elevation that ranking can actually use comes from FABDEM 1° GeoTIFFs stored
under `data/dem/` (the same snapshot idea as ADR-0001). `fetch-dem` downloads
tiles for the PRD Alps+Dinarides bbox. `sample-dem` writes those heights onto
GeometryStore vertices and refreshes Catalog elevation min/max/gain plus the
scaler. Live elevation APIs and sampling inside ingest are rejected: the first
needs the network at query time, the second would block Catalog builds on a
multi-gigabyte raster fetch.

**Considered Options**: Copernicus GLO-30 (trees and buildings remain; since
ADR-0030 it is an alternative beside FABDEM, which stays the default);
session-only notebook sampling (too slow to repeat on a real GeometryStore);
OSM `ele` only (too sparse). SolarAspect, notebook-only when this was decided,
is now the `southness` Measure, sampled from the same DEM.
