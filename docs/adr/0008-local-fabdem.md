# FABDEM is a local snapshot, sampled after ingest

Ingest still reads OSM `ele` only, so a Catalog can be built without rasters.
Elevation that ranking can actually use comes from FABDEM 1° GeoTIFFs stored
under `data/` (the same snapshot idea as ADR-0001). `fetch-dem` downloads tiles
for the PRD Alps+Dinarides bbox. `sample-dem` writes those heights onto
GeometryStore vertices and refreshes Catalog elevation min/max/gain plus the
scaler. Live elevation APIs and sampling inside ingest are rejected: the first
needs the network at query time, the second would block Catalog builds on a
multi-gigabyte raster fetch.

**Considered Options**: Copernicus GLO-30 (trees and buildings remain);
session-only notebook sampling (too slow to repeat on a real GeometryStore);
OSM `ele` only (too sparse). SolarAspect stays notebook-only until promoted.
