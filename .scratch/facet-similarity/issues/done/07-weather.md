# 07: Weather Facet (ClimateNormal slice and SolarAspect)

**What to build:** The Hiker compares the weather a DayHike is likely to have:
temperature, precipitation and solar radiation for the QueryMonth (the
June–August regime when no month is named), and SolarAspect, how south-facing
the slopes along the hike are. Notebook 05a has one `QUERY_MONTH` setting.
SolarAspect is sampled from the local FABDEM tiles with a corrected formula.

**Status:** done

## Blocked by

- 01: Facet scoring core with Effort and Altitude, end to end

**Complexity:** high

**Model:** opus

**Effort:** high

**Prior art:** PRD "Extraction rules" (SolarAspect, ClimateNormal),
"Starting constants" (Weather rows); PRD stories 31–34. ADR-0008 (local
FABDEM), ADR-0010 (QueryMonth slice). `CONTEXT.md` ClimateNormal, QueryMonth,
SolarAspect, DEM. Notebook 04's SolarAspect prototype (its formula scores west
as most south-facing; do not copy it). `sample_elevations` in the DEM module.
Tests: `test_dem` (tiny synthetic GeoTIFFs), `test_climate`. Ticket 01's
measure functions and fact-helper shape.

## Acceptance criteria

- [ ] Weather facts per DayHike: regime temperature, precipitation and solar radiation from Catalog `feature_vectors`; the twelve months from `climate_months`; SolarAspect southness.
- [ ] Southness: sample FABDEM at a subset of vertices plus small east and north offsets, take the downslope direction, score 1 due south, 0 due north, 0.5 east or west; points with slope below a few degrees are left out; no sloped points or no covering tile gives missing.
- [ ] Unit tests with synthetic ramp rasters: a south-facing ramp scores near 1, north near 0, east and west near 0.5, a flat raster gives missing.
- [ ] Weather FacetScore = mean of temperature tolerance (D = 10 °C), precipitation ratio (floor 10 mm), solar-radiation tolerance (D from the region's spread, stated), and southness tolerance (D = 1), using the slice that `QUERY_MONTH` selects.
- [ ] 05 Weather section: southness distribution in the region and a map of a few strongly north- and south-facing DayHikes.
- [ ] 05a Weather section: constants, similar and dissimilar real pairs (for example two shady north-facing alpine hikes vs a sunny coastal one) with the expected verdict first, and `verify` lines; one cell showing a pair's Weather score with no month and with `QUERY_MONTH = 1`; synthetic identity/symmetry/reversed lines.
- [ ] On the fixture (no DEM tile) southness is missing and the measure drops out without errors. Both notebooks still run on the fixture and the full test suite passes.
