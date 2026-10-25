---
status: accepted; scoring amended by ADR-0013 (Measures and Calibration replace cosine and the per-month scalers)
---

# Query month selects the climate slice in similarity

LandCover and ClimateNormal enter the FeatureVector so a warm, sunny, forested Seed ranks near other DayHikes that are warm, sunny, and forested. Similarity stays maximum cosine similarity to any Seed (ADR-0003). The Hiker does not name a climate target. An optional QueryMonth chooses which month fills three score slots: temperature, precipitation, and solar radiation. With no QueryMonth those slots are the June–August regime (mean temperature, mean monthly precipitation, June–August solar radiation). The other months are stored on the DayHike and are not extra score columns. DriveTime stays a filter after Ranking.

Values are means along DayHikeGeometry, each GeometryStore vertex once, from local rasters sampled after ingest. The raw samples stay in the GeometryStore: a land-cover class on each vertex, and twelve months of temperature, precipitation, and solar radiation on each vertex. Ranking still does not read the GeometryStore. It reads Catalog fractions and hike-level monthly means. The same snapshot rule as ADR-0008: ingest still builds a Catalog with these slots empty, and a later sample fills them. Empty slots are missing values, so an unsampled Catalog ranks as it does today.

Vegetation is ESA WorldCover 2021 v200 (already classified Sentinel-1/2). Fractions are tree, shrub, grass, bare, and permanent snow (class codes 10, 20, 30, 60, 70). Crop, built-up, water, wetland, and moss stay in the leftover share. Climate is WorldClim 2.1 at 30 arc-seconds: monthly mean temperature, precipitation, and solar radiation for 1970–2000. Both are fetched for the Alps+Dinarides bbox and sampled like FABDEM.

The three climate columns do not share one scaler across months. Length, elevation, Difficulty, AmenityFacts, and LandCover are scaled once. Temperature, precipitation, and solar radiation are scaled on the slice the query uses (the regime, or one of the twelve months).

**Considered Options**: Twelve month-columns in the cosine score (they drown length and difficulty under the scaler). Climate as a post-filter like DriveTime (the Hiker asked for it inside similarity). A warmth target independent of the Seed (a new query mode). Open-Meteo at query time (network, and it is a forecast or a single point). A sunny-day count (the rasters do not have one; solar radiation is the number). Processing Sentinel into NDVI (WorldCover is that map already). CHELSA for the first pass (better orographic rain in the Alps, harder to fetch; WorldClim is the set that already contains temperature, rain, and solar radiation as GeoTIFFs). Rejected.
