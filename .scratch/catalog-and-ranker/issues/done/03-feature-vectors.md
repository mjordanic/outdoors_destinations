# 03: FeatureVectors on each DayHike

**What to build:** Each Catalog DayHike has a FeatureVector of intrinsic fields
only: length, elevation stats when OSM `ele` is present (missing is distinct
from zero), Difficulty ordinal, and AmenityFacts. DriveTime is not a column.
No scaler and no Ranking yet.

**Status:** ready-for-agent

## Blocked by

- 02-trailhead-amenity-facts

**Prior art:** AmenityFacts from ticket 02, DayHike length and Difficulty from
ticket 01. PRD stories 14–15. ADR-0003 (intrinsic features). `CONTEXT.md`
FeatureVector.

- [x] `build_catalog` persists a FeatureVector per DayHike
- [x] The vector includes length, Difficulty ordinal, Hut/parking/restaurant-
      cafe/water counts and nearest distances
- [x] Missing elevation is a distinct missing value, not zero; when `ele` exists
      on the fixture geometry, min/max or gain is present
- [x] No DriveTime, Origin, or isochrone field exists on the FeatureVector
- [x] Tests assert FeatureVector values from the fixture by hand, through
      `build_catalog`
