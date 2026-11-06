# A DayHike is a route=hiking relation in a 1–20 km band, labeled for Seeds

**Status:** length band and superroute rule superseded by ADR-0033 (no upper cap; only pure containers dropped); `route=foot` added by ADR-0034. The label rule stands.

The Catalog unit is an OSM `route=hiking` relation whose geometry length is 1–20 km inclusive. Superroutes (`network=iwn`, or `network=nwn` with child relations or over the length band) are still dropped. Unnamed `highway=path` ways are still not DayHikes.

A missing OSM `name` no longer drops the relation. The Seed label is OSM `name`, else `ref`, else `relation:{id}`. NameOrigin records which of those three was used so synthetic labels can be filtered out later. `resolve_seed` matches the label.

**Considered Options**: keep dropping blank `name` (ADR-0002); store empty names that cannot be Seeds. Rejected because the Hiker wants those outings in the Catalog and still needs a Seed spelling. A 40 km then 25 km upper band was tried for feature-lab coverage and then cut to 20 km so the Catalog stays a day outing.
