# 02: Fetch ESA WorldCover tiles for the Catalog bbox

**What to build:** The Hiker downloads ESA WorldCover 2021 v200 map tiles into
`data/landcover/` for the same default bbox as the DEM fetch, or a bbox they
pass. Tiles already on disk are skipped. A missing tile is skipped. The
download does not open the Catalog or the GeometryStore. `data/landcover/` is
gitignored. The files are the ones LandCover sampling reads.

**Status:** ready-for-agent

## Blocked by

- 01-sample-landcover

**Complexity:** standard

**Prior art:** `fetch_fabdem` URL and skip-existing tests, ADR-0008, ADR-0010,
PRD stories 25 and 27–29. Public seam matches the DEM fetch (destination and
bounds). WorldCover 2021 v200 map COGs on the public bucket, no credentials.

- [x] Fetch writes 3×3° map GeoTIFFs for the default Alps and Dinarides bbox
      and accepts an override bbox
- [x] A file already present is not downloaded again
- [x] A tile the server does not have is skipped
- [x] The command does not require a Catalog
- [x] `data/landcover/` is gitignored
- [x] Tests lock the tile URL and the skip rule and do not download the real
      map
