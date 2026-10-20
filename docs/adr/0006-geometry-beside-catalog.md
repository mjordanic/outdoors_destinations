# DayHikeGeometry lives beside the Catalog, not in it

Feature construction needs DayHikeGeometry and unaggregated amenity points. Those do not belong in Catalog: Ranking still loads Catalog, and Catalog stays derived facts. Ingest always writes a GeometryStore at `{catalog}.geometry.sqlite` (for `data/catalog.sqlite` that is `data/catalog.geometry.sqlite`). The ingest work file remains temporary resume state and is still deleted after a successful Catalog write.

GeometryStore contains, for Catalog DayHikes only: relation tags, member ways in order with tags, node chain with lon/lat/OSM `ele`, NameOrigin, and every osmium-kept amenity point with OSM id, kind, coordinates, and tags (hut, parking, restaurant/cafe, drinking water, Viewpoint, Peak, Shelter, and the other WaterSources: spring, water tap, water point). Production AmenityFacts stay the original four kinds. GeometryStore does not clone ingest checkpoint tables.

**Considered Options**: keep the work file as the experimental database (ingest-shaped schema, deleted today); put polylines in Catalog (every `rank_dayhikes` pays for geometry); opt-in flag or a second command after the work file is gone. Rejected.
