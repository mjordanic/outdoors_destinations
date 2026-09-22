# 03: Sample ClimateNormal and rank with an optional QueryMonth

**What to build:** The Hiker samples local monthly climate rasters onto
DayHikeGeometry. Each covered vertex stores twelve months of temperature,
precipitation, and solar radiation in the GeometryStore. The Catalog stores
hike-level monthly means and the June–August regime: June–August mean
temperature, mean monthly precipitation, and June–August solar radiation.
Ranking without a month uses that regime. Ranking with a month uses that
month's three numbers, scaled on that month's slice, not on the regime.
Length, elevation, Difficulty, AmenityFacts, and LandCover keep a single
scale. The Seed carries the climate. Asking for a month before climate has
been sampled fails loudly. Omitting the month on an unsampled Catalog still
ranks. DriveTime stays a filter after Ranking.

**Status:** ready-for-agent

## Blocked by

- 01-sample-landcover

**Complexity:** high

**Prior art:** FABDEM sampling, `rank_dayhikes`, ADR-0003, ADR-0010, PRD
stories 13–24. `CONTEXT.md` ClimateNormal, QueryMonth, FeatureVector, Ranking,
Seed. WorldClim units: °C, mm, kJ m⁻² day⁻¹.

- [x] Covered vertices gain twelve GeometryStore climate rows; uncovered
      vertices gain none
- [x] The Catalog stores hike-level means for months 1–12 and the three regime
      columns; a hike with no samples stays, with those values NULL
- [x] Sampling adds missing tables and columns in place and refits the scaler,
      including one climate scale slice per month
- [x] Ranking with no month uses the regime; ranking with month 7 uses July
      and does not use the June–August temperature
- [x] A month outside 1–12 fails; a month with no sampled climate rows fails
      with a clear message; omitting the month does not fail for that reason
- [x] A warm sunny Seed in July ranks nearer a DayHike with similar July
      climate than one with a different July, and LandCover still separates a
      forested hike from a barer hotter one
- [x] DriveTime filtering still happens after Ranking and is not a
      FeatureVector column
- [x] Tests use a synthetic raster on the committed fixture and do not
      download WorldClim
