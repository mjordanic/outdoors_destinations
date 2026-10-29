# PRD: LandCover and ClimateNormal

Status: ready-for-agent

Vocabulary in this document is defined in `GLOSSARY.md`. Terms are used in their
glossary sense — DayHike, DayHikeGeometry, GeometryStore, Catalog, FeatureVector,
LandCover, ClimateNormal, QueryMonth, Seed, Ranking, DriveTime, DEM, SolarAspect —
and not loosely. Respects ADR-0001 (OSM snapshot), ADR-0003 (k-NN to Seeds),
ADR-0008 (local rasters sampled after ingest), and ADR-0010 (QueryMonth selects
the climate slice inside similarity).

## Problem Statement

A Hiker looking for DayHikes like a Seed they already know cannot tell a
forested hike from a bare one, or a wet shady July from a dry sunny one.
Length, elevation, Difficulty, and AmenityFacts do not carry that. Two
DayHikes can match on those and still feel unlike each other on the ground.

The Hiker wants that difference inside Ranking, not only on a detail page.
They want to optionally name a month, so July climate is what gets compared,
and to leave the month out and still compare a warm-season regime. They want
the underlying samples kept in the GeometryStore, the way elevation samples
already are, so the line can be inspected. They want the commands, the
tables, and a worked example written the same way elevation and Ranking
already are.

## Solution

After ingest, the Hiker downloads two local raster sets and samples them onto
DayHikeGeometry.

LandCover is five fractions along the hike: tree, shrub, grass, bare, and
permanent snow. ClimateNormal is monthly mean temperature, precipitation, and
solar radiation. Both are averaged with each GeometryStore vertex counted
once. The raw per-vertex values are stored in the GeometryStore. The
fractions and the hike-level monthly means are stored in the Catalog.
Ranking reads only the Catalog.

An optional QueryMonth fills three score slots from that month. With no
QueryMonth, those slots are the June–August regime: June–August mean
temperature, mean monthly precipitation, and June–August solar radiation.
The Seed carries the climate. DriveTime stays a filter after Ranking.

Until the rasters are sampled, the new slots are missing and Ranking behaves
as it does today. Ingest does not download rasters.

The repo README explains the new commands in the same shape as the FABDEM
elevation section: when to run them, what they download, what they write,
a shell example, a Python example, and what Ranking then does. The notebook
walkthrough grows matching examples that run offline on the committed OSM
fixture.

## User Stories

**Sampling onto the hike**

1. As a Hiker, I want LandCover and ClimateNormal sampled from local rasters
   after ingest, so that building a Catalog does not wait on a raster
   download.
2. As a Hiker, I want each GeometryStore vertex to count once in the average,
   so that a hike which starts in forest and finishes on rock is neither
   "all forest" nor "all rock".
3. As a Hiker, I want vertices outside the downloaded tiles left unset, so
   that a partial download does not invent a class or a climate.
4. As a Hiker, I want a DayHike with no covered vertices to stay in the
   Catalog, so that sampling never deletes hikes.
5. As a Hiker, I want re-ingest to rebuild the GeometryStore from OSM, so
   that I know samples must be run again afterwards, the same way DEM
   heights must.
6. As a Hiker, I want to sample an already-built Catalog in place, so that
   I do not re-ingest a large extract just to add these columns.

**LandCover**

7. As a Hiker, I want the fraction of tree, shrub, grass, bare, and permanent
   snow on each DayHike, so that a shaded forest ranks differently from open
   karst.
8. As a Hiker, I want crop, built-up, water, wetland, and moss to remain in
   the leftover share, so that those classes do not add score columns.
9. As a Hiker, I want LandCover fractions to stay the same whichever month I
   pick, so that a forest hike is still a forest hike in January.
10. As a Hiker, I want permanent snow kept distinct from seasonal snowpack,
    so that I do not read the land-cover map as a January snow forecast.
11. As a Hiker, I want the land-cover class stored on each vertex in the
    GeometryStore, so that I can see where along the line the forest ends.
12. As a Hiker, I want fractions in the Catalog, so that Ranking can use them
    without reading the GeometryStore.

**ClimateNormal and QueryMonth**

13. As a Hiker, I want each vertex to store twelve months of temperature,
    precipitation, and solar radiation in the GeometryStore, so that I can
    see how climate changes along the hike.
14. As a Hiker, I want hike-level monthly means in the Catalog, so that
    Ranking can use a month without reading the GeometryStore.
15. As a Hiker, I want to pass a month into Ranking, so that similarity uses
    that month's temperature, precipitation, and solar radiation.
16. As a Hiker, I want to omit the month, so that similarity uses June–August
    mean temperature, mean monthly precipitation, and June–August solar
    radiation.
17. As a Hiker, I want the other months stored but not scored, so that twelve
    temperatures do not drown length and Difficulty.
18. As a Hiker, I want a warm sunny Seed to rank near other warm sunny
    DayHikes in that month, so that climate is part of "like this hike".
19. As a Hiker, I want a forested Seed to stay nearer other forested
    DayHikes than an open hotter ridge, so that LandCover still matters when
    the month is set.
20. As a Hiker, I want solar radiation as the sunshine number, so that I am
    not depending on a sunny-day threshold the rasters do not contain.
21. As a Hiker, I want a request for a month before climate has been sampled
    to fail loudly, so that I do not think July was applied when it was not.
22. As a Hiker, I want Ranking without a month to succeed on an unsampled
    Catalog, so that the new slots do not break the Ranking I already have.
23. As a Hiker, I want length, elevation, Difficulty, AmenityFacts, and
    LandCover scaled once, and the three climate slots scaled on the slice
    in use, so that July temperatures are not scaled as if they were the
    June–August regime.
24. As a Hiker, I want DriveTime to remain a filter after Ranking, so that
    the drive from Origin is still not a property of the hike.

**Fetching the rasters**

25. As a Hiker, I want a command that downloads ESA WorldCover 2021 v200
    map tiles for the Alps and Dinarides bbox, so that I am not classifying
    Sentinel scenes myself.
26. As a Hiker, I want a command that downloads WorldClim 2.1 30 arc-second
    monthly mean temperature, precipitation, and solar radiation, so that
    climate is a local snapshot like the DEM.
27. As a Hiker, I want tiles that are already on disk to be skipped, so that
    I can re-run a fetch after it was interrupted.
28. As a Hiker, I want the same default bbox the DEM fetch uses, and a way
    to pass a different one, so that a wider Catalog can be covered.
29. As a Hiker, I want those rasters under gitignored data directories, so
    that they are not committed.

**Explaining it**

30. As a Hiker, I want the README command order to include the new fetch and
    sample steps after elevation, so that I know they run after ingest.
31. As a Hiker, I want a README section in the same shape as the FABDEM
    section (when, what is downloaded, what is written, shell, Python,
    what Ranking does), so that I do not have to read the ADR to use this.
32. As a Hiker, I want the GeometryStore tables in the README to include the
    per-vertex land-cover class and the per-vertex monthly climate, so that
    the sidecar documentation stays true.
33. As a Hiker, I want a rank example with a month and without one, so that
    the optional QueryMonth is visible next to the existing rank example.
34. As a Hiker, I want the public Python API list to name the new fetch,
    sample, and month arguments, so that the API section matches the CLI.
35. As a Hiker, I want attribution for WorldCover and WorldClim next to the
    existing OSM attribution, so that the rasters' licenses are stated.
36. As a Hiker, I want the notebook walkthrough to show the new GeometryStore
    rows and a Ranking with and without a month, so that I can see the
    behavior on the committed fixture without downloading WorldCover or
    WorldClim.
37. As a developer, I want those notebook examples to execute in the existing
    notebook test run, so that a broken example fails CI the way the
    current walkthrough does.

## Implementation Decisions

- Ingest still does not download or sample rasters. A fresh GeometryStore
  is created with the new land-cover column empty and the per-vertex climate
  table empty. A fresh Catalog is created with the new fraction and climate
  columns empty (NULL) and with no monthly climate rows.
- Sampling an existing Catalog and GeometryStore adds any missing columns
  and tables in place, fills them, and refits the scaler. It does not add
  or drop DayHikes. Re-ingest replaces both SQLite files and clears samples.
- LandCover raster: ESA WorldCover 2021 v200 map layer, 3×3° Cloud-Optimized
  GeoTIFFs, EPSG:4326, from the public bucket, no credentials. Default
  bounds match the DEM fetch. Files already present are skipped. A missing
  tile is skipped. Stored class codes are the map values. Fractions use
  code 10 as tree, 20 as shrub, 30 as grass, 60 as bare, 70 as permanent
  snow. Any other sampled code counts in the denominator only.
- Climate raster: WorldClim 2.1 at 30 arc-seconds, the monthly GeoTIFFs for
  mean temperature (°C), precipitation (mm), and solar radiation
  (kJ m⁻² day⁻¹), from the three base zips `wc2.1_30s_tavg`,
  `wc2.1_30s_prec`, and `wc2.1_30s_srad`. Files already present are skipped.
- Rasters live in `data/landcover/` and `data/climate/`. Both directories
  are gitignored, same as `data/dem/`. The README "where files live" and
  "what is gitignored" lists name them.
- GeometryStore `vertices` gains `landcover` (integer class code, NULL when
  unset). GeometryStore gains `vertex_climate` with one row per sampled
  vertex per month: dayhike id, vertex sequence, month 1–12, temperature,
  precipitation, solar radiation. Uncovered vertices get no climate rows.
  `geometry_meta` gains attribution entries for WorldCover and WorldClim
  when those samples have been written, and keeps the OSM attribution.
- Catalog `feature_vectors` gains five LandCover fraction columns and three
  regime climate columns, all nullable. Catalog gains a monthly table of
  hike-level means: dayhike id, month 1–12, temperature, precipitation,
  solar radiation. Fractions and means use only vertices that have a
  sample. A hike with none leaves the Catalog values NULL.
- Regime columns, written by the climate sample: mean of the hike-level
  June, July, and August temperatures; mean of the twelve hike-level
  monthly precipitations; mean of the June, July, and August solar
  radiations.
- The main scaler covers every FeatureVector column except that the three
  climate columns in it are the regime. Twelve extra scaler slices store
  mean and scale for those three climate names only, one slice per month,
  fitted on that month's hike-level values. LandCover and the other
  columns are not refit per month.
- Ranking without a QueryMonth uses the regime climate columns and the main
  scaler. Ranking with a QueryMonth substitutes that month's hike-level
  means into the three climate slots and scales those three with that
  month's slice. Missing values stay missing and become neutral after
  scaling, as elevation already does.
- QueryMonth is an integer from 1 to 12. Any other value fails. Passing a
  month when the Catalog has no monthly climate rows fails with a message
  that climate has not been sampled. Omitting the month never fails for
  that reason.
- Public seams, matching the DEM pair plus the existing rank function:
  fetch and sample for land cover, fetch and sample for climate, and rank
  with an optional month. The CLI wraps them: `fetch-landcover`,
  `sample-landcover`, `fetch-climate`, `sample-climate`, and `rank --month`.
  Bounds and destination flags match the DEM fetch. Catalog path flags
  match `sample-dem`.
- README at the repo root is updated in the FABDEM manner, not as a new
  document. That means a step in "How data is created", rows in "Where
  files live", the GeometryStore table section, a dedicated section with
  when / pull / store / shell / Python, a rank example with and without
  `--month`, the Python API list, the notebook table, the attribution
  section, and the gitignore list. Supported write paths name the new
  sample commands beside ingest and `sample-dem`.
- Notebook examples extend the existing walkthrough rather than adding a
  new notebook file, so the numbered order stays
  `01`, `02`, `03`, `04`, `04a`. `04a_geometry` shows per-vertex land-cover
  classes and `vertex_climate` rows on the committed fixture. `03_ranking`
  shows one Ranking with no month and one with month 7, including that the
  July temperature used in the score is not the warm-season temperature,
  and that LandCover moves a forested neighbour ahead of a barer one for a
  forested Seed. `02_catalog` schema prose mentions the new Catalog columns
  and the monthly table so it does not describe a stale schema. The
  rank-feature lab does not present LandCover or ClimateNormal as
  experimental columns; they are production.
- Notebook examples do not download WorldCover or WorldClim. They build a
  tiny synthetic raster that covers the fixture vertices, sample that, and
  print stable lines the notebook test already knows how to read. Real
  fetch commands are documented in the README only.
- WorldCover attribution text and the WorldClim citation (Fick and Hijmans
  2017) are written in the README attribution section and into the
  GeometryStore meta keys described above.

## Testing Decisions

The behavioral seam is the same public surface as FABDEM elevation: call
fetch URL helpers without downloading, call sample on a Catalog built from
the committed OSM fixture plus a tiny synthetic GeoTIFF, then read the
GeometryStore and the Catalog, then call rank with and without a month.
Tests assert those observable results (class stored per vertex, twelve
climate rows per covered vertex, fractions, regime versus July, rank order,
loud failure when a month is asked for before climate is sampled). They do
not assert private helper structure.

Prior art is the DEM tests that write a constant GeoTIFF and expect
`vertices.ele` plus Catalog elevation, and the rank tests that resolve a
Seed and check order. Fetch tests follow the DEM tests that lock the tile
URL and skip files already on disk, and do not require the network.

Notebook and README coverage stays on the existing notebook runner: every
notebook executes against the committed fixture, and the README test checks
that the walkthrough names are linked. New cells must keep that run green
and add assertions for the stable lines from the LandCover and QueryMonth
examples. No test downloads WorldCover or WorldClim.

A good test checks what a Hiker can observe after a command: rows in the
GeometryStore, columns in the Catalog, and the Ranking. It does not pin
rasterio call sequences.

## Out of Scope

- Implementing this PRD in the same change as writing it.
- A climate target that is not the Seed's climate.
- Live or weekend weather, Open-Meteo, and a sunny-day count.
- Seasonal snowpack. Permanent snow on the land-cover map is in scope.
- SolarAspect as a FeatureVector column.
- CHELSA, NDVI, or classifying Sentinel scenes in this repo.
- Putting all twelve months into the cosine score.
- A website, ratings, or a supervised ranker.
- Changing DriveTime, the length band, or which OSM relations are DayHikes.

## Issues

1. `01-sample-landcover` — stories 1–12
2. `02-fetch-worldcover` — stories 25, 27–29 for the land-cover rasters
3. `03-sample-climate-and-query-month` — stories 13–24
4. `04-fetch-worldclim` — stories 26–29 for the climate rasters
5. `05-notebook-examples` — stories 36–37
6. `06-readme-landcover-climate` — stories 30–35

## Further Notes

ADR-0010 is the decision this PRD implements. Future-work §6 still parks
forecasts, SolarAspect, and seasonal snow.

WorldClim 2.1 normals are 1970–2000. Precipitation in the Alps is the known
weak spot versus a terrain-downscaled product. Swapping the climate raster
later must keep the same three Catalog slots and the same GeometryStore
monthly rows.

Each GeometryStore vertex counts once, including tightly spaced switchbacks.
That matches how elevation walks the same vertices. It is not a
length-weighted mean.

WorldCover is one map for 2021, not a monthly canopy. Shade in summer shows
up because July temperature and tree fraction are both in the score, not
because the fraction changes in July.
