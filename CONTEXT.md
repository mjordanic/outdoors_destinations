# DayHike catalog

A local Catalog of DayHikes built from OpenStreetMap, ranked by similarity to Seed DayHikes the hiker already knows. Drive time is a query-time filter, not a property of a hike.

## Language

**DayHike**:
A named, day-length hiking outing stored in the Catalog. It is an OSM `route=hiking` relation that survived the length band and naming rules.
_Avoid_: destination, trail, route, path, recommendation

**Trailhead**:
The point used to start the DayHike, taken from the relation's geometry (first/last node or tagged access point). Drive time and nearby amenities are measured from here.
_Avoid_: start, parking (parking is an amenity near a Trailhead)

**Catalog**:
The local database of DayHikes, Trailheads, AmenityFacts, and FeatureVectors. Built from an OSM snapshot, not queried live.
_Avoid_: dataset, index, graph (the routing graph is Valhalla's, not the Catalog)

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
_Avoid_: hardness, danger, exposure (those are not this field)

**AmenityFact**:
A count or distance from a Trailhead to an OSM amenity used as a Feature (restaurant/cafe, parking, drinking water, Hut).
_Avoid_: POI, restaurant as a Catalog key

**Seed**:
A DayHike the hiker names as "like the outing I want." Ranking is similarity to Seeds, not a trained taste model.
_Avoid_: label, like, rating, example (example is too vague)

**FeatureVector**:
The numeric intrinsic description of a DayHike used for similarity (length, elevation stats, Difficulty, AmenityFacts). It does not include DriveTime.
_Avoid_: embedding, description, tags

**Ranking**:
An ordered list of Catalog DayHikes with a similarity score to the Seed set. Score is the maximum similarity to any Seed.
_Avoid_: recommendation, unsupervised clustering, prediction

**Hiker**:
The person querying the Catalog. Phase 1 is a single local user; a future website may have many Hikers with ratings.
_Avoid_: user, customer, tourist
