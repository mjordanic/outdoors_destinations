# A DayHike is a named hiking route relation in a length band

**Status:** superseded by ADR-0007

The Catalog unit was an OSM relation with `route=hiking`, a name, and geometry length in a day-hike band (about 2–25 km). International/national superroutes and unnamed `highway=path` ways are not DayHikes. Raw paths would flood the Catalog with fragments that are not outings a Hiker can name as a Seed.

**Consequences**: Seed resolution can only succeed for named relations in the Catalog. Local unmarked ridges will not appear until a later ingest rule is designed.
