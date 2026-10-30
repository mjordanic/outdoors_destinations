---
status: accepted
---

# "Find place" searches GeoNames towns in the serving export, not Nominatim

The page's "Find place" asked public Nominatim. Its usage policy allows at most one request a second summed over every visitor and no autocomplete, so a spike of clicks could get the site's Referer throttled or blocked, and every typed place left the site for OSMF. The serving export now carries `places.sqlite`: the GeoNames `cities1000` towns (1,000 people or more, CC BY 4.0) within `PLACE_MARGIN_DEG` (2°) of the Trailheads' box, so a start a drive away from the nearest hike is still found. Each place keeps its name, country, population and position, and every name it has (name, ASCII name, alternate names) folded like hike search. `GET /api/place?q=` matches a folded prefix of any of those names: the place named exactly the query first, then a place with it as an alternate name (Wien is Vienna), then the most populous. Without the second rule an exact alternate name of a big city beat the town itself (Krk found Kraków, Porto found Bordeaux).

`export-serving --geonames` writes the file; `export-places` writes it beside an export already built, since it reads only `trailheads.npy`. It is optional: the export format is unchanged, `/api/info` says `place_search`, and the page asks Nominatim only when the export has no place file.

**Considered Options**: keep Nominatim (its rate limit and the visitor's query leaving the site); a hosted geocoder (an account, a key and a bill); the OSM `place=*` nodes from the Catalog's extracts (no population to rank by, and a second ingest path); all of GeoNames (the world, when only Europe has hikes). Rejected.

**Consequences**: the place list is as fresh as the export and the GeoNames download it used; a place under 1,000 people is not found (type "lat, lon" or use the browser's location). GeoNames is credited in `/api/info` `attribution` and on the Data and credits page when the file is present. "Roma" finds Roma in Romania before Rome, whose name in GeoNames is "Rome".
