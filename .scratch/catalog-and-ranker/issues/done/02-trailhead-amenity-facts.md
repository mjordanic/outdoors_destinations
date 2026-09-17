# 02: AmenityFacts around each Trailhead

**What to build:** After a Catalog exists, each Trailhead carries AmenityFacts:
count and nearest distance within 1 km for Huts, parking, restaurant/cafe, and
drinking water. A DayHike whose Trailhead has zero restaurants stays in the
Catalog. Amenities are features of the Trailhead, not a second Catalog key.

**Status:** ready-for-agent

## Blocked by

- 01-ingest-fixture-catalog

**Prior art:** Ticket 01's fixture Catalog and `build_catalog` seam. Extend the
same fixture with a Hut, cafe, parking, and water near one Trailhead and not
the other. PRD stories 9–13. `CONTEXT.md` AmenityFact, Hut, Trailhead.

- [x] The ingest fixture places amenities so one in-band DayHike has nearby
      Hut, cafe, parking, and water, and the other has none of them in 1 km
- [x] `build_catalog` writes AmenityFacts: counts and nearest distances for
      Hut, parking, restaurant/cafe, drinking water
- [x] Zero counts are stored, not omitted, and that DayHike remains in the
      Catalog
- [x] Restaurant/cafe does not create or delete DayHikes
- [x] Tests go through `build_catalog`, not by querying OSM internals
