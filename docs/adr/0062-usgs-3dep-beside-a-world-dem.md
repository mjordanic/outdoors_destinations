---
status: accepted (amends ADR-0030 and ADR-0060)
---

# USGS 3DEP is read beside a world DEM, and each hike records its DEM

FABDEM (ADR-0008) and Copernicus GLO-30 (ADR-0030) cover the world. Copernicus already works for the United States with no code, but it is a surface model, so under forest it reads the treetops. USGS 3DEP 1 arc-second is a bare-earth terrain model of the contiguous US and Alaska. It is in the U.S. public domain, and USGS asks for the credit "Credit: U.S. Geological Survey". A worldwide build therefore reads different DEMs in different regions. ADR-0060 left a per-hike `dem_source` for the first build that mixes DEMs, and this is that build.

**The source.** `fetch-dem --source 3dep` downloads the 1° GeoTIFFs of a box from the USGS staged-products bucket (`prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1/TIFF/current/`, HTTPS, no account), about 50 MB a tile. Each tile sits in its own folder and is named by its northwest corner: `n40w106/USGS_1_n40w106.tif` holds 39–40 °N, 106–105 °W. A tile the bucket lacks (sea, outside the US) answers 404 and is skipped. A tile is 3,612 × 3,612 cells: 1° plus 6 cells past every edge. Its grid is NAD83 (EPSG:4269), about 1 m off WGS 84, and is read as it is. Its nodata value is −999999, and a cell with that value reads as uncovered.

**Selectable per region.** One `--dem` directory may hold 3DEP tiles beside the tiles of one world source (FABDEM or Copernicus GLO-30). FABDEM and Copernicus tiles in one directory are still refused. Every DEM step (`sample-dem`, `sample-trailgraph`, `build-similarity`, and ingest's orienting of one-way lines) reads the tile of a point's own 1° cell: the 3DEP tile where the directory has one, otherwise the world source's. The overlap is never read across. A point just south of 40 °N reads its own tile even though the tile to its north covers it too, and a point on 40 °N reads the tile it is the southwest corner of. So a region's DEM is chosen by fetching that region's tiles from that source into the build's `--dem` directory. Once regions are configuration (#132), a region can name its DEM source. 3DEP is bare earth, so it gets no canopy floor. Copernicus points keep theirs, and Copernicus tiles still need the WorldCover tiles.

**The DEM of each hike.** `sample-dem` writes the register ids of the DEMs a DayHike's heights were read from into the Catalog's `dayhikes.dem_source`: `fabdem`, `copernicus-glo30` or `usgs-3dep`. A hike that crosses the edge of 3DEP coverage gets both ids, comma-separated in `DEM_SOURCES` order (`fabdem,usgs-3dep`). A fresh Catalog has the column. `sample-dem` adds it to an older one, which is NULL until sampled. A 3DEP `sample-dem` writes `usgs_3dep_attribution` into `geometry_meta` while its directory holds 3DEP tiles, as Copernicus does.

**The credits follow the DEMs used.** When the Catalog records DEMs, the export's attribution lists exactly those DEMs: FABDEM only when some hike read FABDEM, and Copernicus or 3DEP only when some hike read it. A Catalog that records none (sampled before this change) keeps ADR-0030's rule. The credits page, the home page footer line and the terms credit 3DEP after the world DEM's credit, or alone when no hike read a world DEM. The Measure sources and the score help name both maps ("FABDEM or USGS 3DEP elevation along the line."). A GPX download credits the DEMs its hike was read from.

**Europe stays as it was.** Only an export whose hikes read more than one DEM gets a `dem_source` column in `hikes.sqlite`. There, `/api/hikes/{id}` returns the hike's ids, and only hikes read from Copernicus have their drawn profile smoothed (ADR-0030). In a one-DEM export, `/api/hikes/{id}` returns `dem_source: null`. Its files are byte for byte what they were, checked on the test fixtures for FABDEM and for Copernicus.

**Considered Options**

- **A second `--dem` directory per step, or a list of them.** Rejected. Every DEM step would need the flag, and reading the tiles by name in one directory gives the same choice with no new flag.
- **One DEM per hike: the first source that covers all its vertices.** Rejected for now. The steps read points, not hikes (TrailGraph Segments and the SolarAspect probes have no hike), so the per-tile rule is the only one all of them can follow. A hike that crosses the US border is rare, and it records both DEMs.
- **The `dem_source` column in every export.** Rejected. It would change every Europe export, which reads one DEM, for no gain. ADR-0060 already planned the column for a build that mixes DEMs only.
