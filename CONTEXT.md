# DayHike catalog

A local Catalog of DayHikes built from OpenStreetMap, ranked by similarity to Seed DayHikes the hiker already knows. Drive time is a query-time filter, not a property of a hike.

## Language

**DayHike**:
A day-length hiking outing stored in the Catalog. It is an OSM `route=hiking` relation that survived the length band and superroute rules. Its label is OSM `name`, else `ref`, else `relation:{id}`.
_Avoid_: destination, trail, route, path, recommendation

**NameOrigin**:
Whether a DayHike label came from OSM `name`, OSM `ref`, or a synthetic `relation:{id}` assigned at ingest. Synthetic labels can be dropped in postprocessing.
_Avoid_: unnamed as a drop reason, title, display name

**Trailhead**:
The point used to start the DayHike, taken from the relation's geometry (first/last node or tagged access point). Drive time and nearby amenities are measured from here.
_Avoid_: start, parking (parking is an amenity near a Trailhead)

**Catalog**:
The local database of DayHikes, Trailheads, AmenityFacts, and FeatureVectors. Built from an OSM snapshot, not queried live. It does not hold DayHikeGeometry or raw amenity points.
_Avoid_: dataset, index, graph (the routing graph is Valhalla's, not the Catalog)

**DayHikeGeometry**:
The ordered lon/lat vertices of a DayHike, assembled from OSM member ways that share endpoints (a member that does not touch the chain is omitted, not jumped). Used to invent FeatureVector columns; Ranking does not read it.
_Avoid_: path, route, trace, LineString

**GeometryStore**:
The sidecar SQLite written at ingest next to a Catalog. It holds DayHikeGeometry, member-way and relation tags, raw amenity points, and the per-vertex LandCover and ClimateNormal samples. Ranking does not read it.
_Avoid_: experimental database, work file, Catalog, lab

**Origin**:
The hiker's departure point for this query. It is not stored on the DayHike.
_Avoid_: home, location, user location

**DriveTime**:
Travel time by car from Origin to a Trailhead, computed at query time by the local routing engine.
_Avoid_: isochrone, drive estimate as a stored column, distance (distance may exist alongside time but is not the filter)

**Hut**:
An OSM alpine or mountain hut near a Trailhead or along the DayHike geometry.
_Avoid_: hotel, shelter (unless tagged as such and counted separately)

**Difficulty**:
The technical grade of a DayHike, derived from OSM `sac_scale` on member ways (the strictest grade present).
_Avoid_: hardness, danger, exposure (those are not this field; alpine fall hazard stays inside Difficulty)

**DEM**:
A local elevation raster sampled onto DayHikeGeometry after ingest. Until that sample runs, elevation stats come from OSM `ele` on member nodes.
_Avoid_: live SRTM, elevation API, exposure

**ElevationProfile**:
Metres of elevation change along a DayHike from its first point, in the hike's stored direction. The metres stay in metres.
_Avoid_: altitude, normalized profile, unit-scaled shape

**SolarAspect**:
A terrain-derived description of which way DayHikeGeometry faces relative to the sun, sampled from a DEM. It is not Difficulty, not ClimateNormal, and not alpine fall hazard. Shade from tree cover is LandCover.
_Avoid_: exposure, sunny, shady, weather

**LandCover**:
The mix of surface along DayHikeGeometry, as fractions of tree, shrub, grass, bare, and permanent snow, used in the FeatureVector. Each GeometryStore vertex counts once. Fractions do not change with QueryMonth. Snow here is ice on the land-cover map, not the seasonal snowpack.
_Avoid_: NDVI, vegetation image, satellite picture, greenness, snow depth

**ClimateNormal**:
The long-term monthly temperature, precipitation, and solar radiation of a DayHike, averaged along DayHikeGeometry. Ranking scores three numbers from it. It is not a forecast and not SolarAspect.
_Avoid_: live weather, weekend forecast, aspect, sunny-day count

**QueryMonth**:
The optional month a Hiker sets on a Ranking. It selects which ClimateNormal month fills the three score slots: that month's temperature, precipitation, and solar radiation. When absent, the slots are June–August mean temperature, mean monthly precipitation, and June–August solar radiation.
_Avoid_: season filter, forecast date, current month as a silent default

**AmenityFact**:
A count or distance from a Trailhead to an OSM amenity used as a Feature (restaurant/cafe, parking, drinking water, Hut). Viewpoint, Peak, and Shelter are not AmenityFacts.
_Avoid_: POI, restaurant as a Catalog key

**Viewpoint**:
An OSM `tourism=viewpoint` stored as a raw amenity point in GeometryStore.
_Avoid_: lookout as an AmenityFact

**Peak**:
An OSM `natural=peak` stored as a raw amenity point in GeometryStore.
_Avoid_: summit as an AmenityFact

**Shelter**:
An OSM `amenity=shelter` (lean-to / weather shelter) stored as a raw amenity point in GeometryStore. It is not a Hut.
_Avoid_: hut, wilderness_hut

**Seed**:
A DayHike the hiker names as "like the outing I want." Ranking is similarity to Seeds, not a trained taste model.
_Avoid_: label, like, rating, example (example is too vague)

**FeatureVector**:
The numeric description of a DayHike used for similarity (length, elevation stats, Difficulty, AmenityFacts, LandCover, and three ClimateNormal slots). Which climate slots are used follows the QueryMonth. It does not include DriveTime.
_Avoid_: embedding, description, tags

**Ranking**:
An ordered list of Catalog DayHikes with a similarity score to the Seed set. Score is the maximum similarity to any Seed. An optional QueryMonth selects the ClimateNormal slice in that score. The Seed carries the climate being matched.
_Avoid_: recommendation, unsupervised clustering, prediction, climate target

**Hiker**:
The person querying the Catalog. Phase 1 is a single local user; a future website may have many Hikers with ratings.
_Avoid_: user, customer, tourist
