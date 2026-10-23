# DayHike catalog

A local Catalog of DayHikes built from OpenStreetMap, ranked by similarity to Seed DayHikes the hiker already knows. Drive time is a query-time filter, not a property of a hike.

## Language

**DayHike**:
A day-length hiking outing stored in the Catalog. It is an OSM `route=hiking` relation that survived the length band and superroute rules. Its label is OSM `name`, else `ref`, else `relation:{id}`. Its length is the length of its DayHikeGeometry, each way once: a there-and-back trail counts one way.
_Avoid_: destination, trail, route, path, recommendation

**NameOrigin**:
Whether a DayHike label came from OSM `name`, OSM `ref`, or a synthetic `relation:{id}` assigned at ingest. Synthetic labels can be dropped in postprocessing.
_Avoid_: unnamed as a drop reason, title, display name

**Trailhead**:
The point where a DayHike's main walk starts, so it is the first vertex of DayHikeGeometry. A line starts at the end nearer an access point within 5 km, else at the end where member order starts it. A loop starts at the far end of the one dead end it keeps (its stem), else at its vertex nearest an access point within 5 km. An access point is a parking point and nothing else. Drive time and nearby amenities are measured from here.
_Avoid_: start, parking (parking is an amenity near a Trailhead)

**Catalog**:
The local database of DayHikes, Trailheads, AmenityFacts, and FeatureVectors. Built from an OSM snapshot, not queried live. It does not hold DayHikeGeometry or raw amenity points.
_Avoid_: dataset, index, graph (the routing graph is Valhalla's, not the Catalog)

**DayHikeGeometry**:
The ordered lon/lat vertices of a DayHike's main walk, starting at the Trailhead: the longest connected piece of its OSM member ways (ways a few tens of metres apart count as connected), each way at most once, without Leftovers. Consecutive ways meet; there is no jump between them. Used to invent FeatureVector columns; Ranking does not read it.
_Avoid_: path, route, trace, LineString, chain

**Leftover**:
A member way of a DayHike that is not on its main walk: a side trip, a variant, a stray piece that does not connect, or a repeat listing. It stays in GeometryStore but is not in DayHikeGeometry or the DayHike's length.
_Avoid_: stray, unconnected way, excursion, side trip, optional part

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
Metres of elevation change along a DayHike from its Trailhead, in walking direction. The metres stay in metres.
_Avoid_: altitude, normalized profile (that is ProfileShape), unit-scaled shape

**ProfileShape**:
An ElevationProfile scaled to 0–1 in both distance and height, so two DayHikes of different length and gain compare by form alone. How high a DayHike goes and how much it climbs are not in it.
_Avoid_: normalized ElevationProfile, elevation plot, profile

**ClimbGrade**:
How steep a DayHike climbs: its elevation gain over the length of the stretches that climb, in the walking direction with the larger climb, on elevation resampled every 50 m. Flat and falling stretches do not dilute it, so a RoundTrip and a line up the same slope have the same ClimbGrade.
_Avoid_: slope (that is the DEM slope in SolarAspect), gradient, incline, mean grade (gain over the whole length)

**SteepestKm**:
The largest height change, up or down, between two points 1 km apart along a DayHike, in metres: the hardest sustained stretch of the day. A DayHike shorter than 1 km uses its two ends.
_Avoid_: max grade, steepest section

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
A count or distance from a Trailhead to an OSM amenity used as a Feature (restaurant/cafe, parking, drinking water, Hut). Viewpoint, Peak, Shelter, and WaterSources other than drinking water are not AmenityFacts.
_Avoid_: POI, restaurant as a Catalog key

**Corridor**:
The 100 m band around DayHikeGeometry. An amenity point inside it is along the DayHike; an AmenityFact counts around the Trailhead instead.
_Avoid_: buffer, along-route radius, nearby

**DryStretch**:
The longest distance walked along a DayHike without passing a WaterSource in the Corridor, counting from the first and to the last vertex. A DayHike with no WaterSource has a DryStretch equal to its length.
_Avoid_: water gap, thirst distance

**SurfaceMix**:
The mix of trail surface along DayHikeGeometry, as length fractions of paved, gravel, natural, and rock. It comes from member-way surface tags, else from the way's highway kind. It is not LandCover.
_Avoid_: ground type, terrain, trail type

**RoundTrip**:
A DayHike whose main walk ends within 50 m of its Trailhead, so its length is the whole walk back to the car. A lollipop (a loop with a stem) ends where the stem meets the loop and is not a RoundTrip; nor is a line, whose length is one way whether it is walked point-to-point or out-and-back.
_Avoid_: loop (the main walk's loop includes lollipops), circular, closed route

**TarmacApproach**:
The distance walked on paved surface from the Trailhead before the DayHike first leaves it.
_Avoid_: distance from parking, road walk, access distance

**Viewpoint**:
An OSM `tourism=viewpoint` stored as a raw amenity point in GeometryStore.
_Avoid_: lookout as an AmenityFact

**Peak**:
An OSM `natural=peak` stored as a raw amenity point in GeometryStore.
_Avoid_: summit as an AmenityFact

**Shelter**:
An OSM `amenity=shelter` (lean-to / weather shelter) stored as a raw amenity point in GeometryStore. It is not a Hut.
_Avoid_: hut, wilderness_hut

**WaterSource**:
A point where a Hiker can refill water: an OSM drinking-water point, spring, water tap, or water point, stored as a raw amenity point in GeometryStore. One tagged as not drinkable is not a WaterSource. A spring or tap also tagged as drinking water is a drinking-water point.
_Avoid_: water point (one OSM kind), fountain, water amenity

**Seed**:
A DayHike the hiker names as "like the outing I want." Ranking is similarity to Seeds, not a trained taste model.
_Avoid_: label, like, rating, example (example is too vague)

**FeatureVector**:
The numeric description of a DayHike used for similarity (length, elevation stats, Difficulty, AmenityFacts, LandCover, and three ClimateNormal slots). Which climate slots are used follows the QueryMonth. It does not include DriveTime.
_Avoid_: embedding, description, tags

**Ranking**:
An ordered list of Catalog DayHikes with a similarity score to the Seed set. Score is the maximum similarity to any Seed. An optional QueryMonth selects the ClimateNormal slice in that score. The Seed carries the climate being matched.
_Avoid_: recommendation, unsupervised clustering, prediction, climate target

**Facet**:
One theme on which two DayHikes are compared and which the Hiker weights: Effort, Altitude, Profile, Landscape, Weather, Water, Facilities, Trail, RoundTrip, or Steepness. A Facet groups one or more measures.
_Avoid_: category, dimension, feature group, feature

**FacetScore**:
The similarity of two DayHikes on one Facet, from 0 (unlike) to 1 (alike). It is the plain mean of that Facet's measures.
_Avoid_: distance, sub-score, partial score

**FacetWeights**:
The Hiker's shares across Facets for one query, normalized to sum to 1. A Facet with weight 0 does not move the score.
_Avoid_: preferences, priorities, importance

**Hiker**:
The person querying the Catalog. Phase 1 is a single local user; a future website may have many Hikers with ratings.
_Avoid_: user, customer, tourist
