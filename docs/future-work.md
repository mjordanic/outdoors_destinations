# Future work

Deliberately deferred. Nothing here is being built in the Catalog-and-ranker
milestone. This file exists so the research behind these decisions is not redone.

---

## 1. Website UI

### Why it's parked

Phase 1 is a local Catalog and CLI Ranking. A webpage would freeze the HTTP
shape before ingest, AmenityFacts, and k-NN are honest.

### What would have to be true to start

- `rank` against a fixture Catalog returns a stable ordered list of DayHikes.
- Seed names resolve or fail loudly (no silent empty Ranking).
- Origin + max DriveTime is an optional filter with a mocked Valhalla in tests.

Then: Origin on a map, Seed picker from the Catalog (not free text into a void),
result cards, geometries, optional isochrone overlay, OSM attribution.

---

## 2. Per-Hiker ratings and a supervised ranker

### Why it's parked

There are no likes yet. Collaborative filtering is impossible with one Hiker.
A vacant `labels` table with `hiker_id` is enough so the website does not force
a rewrite.

### What would have to be true to start

- A Hiker can rate DayHikes they know (1–5 or like/dislike) in the UI.
- Enough ratings that a pairwise or pointwise model beats k-NN-to-Seeds on a
  holdout the Hiker cares about. Until then, Seeds remain the query.

---

## 3. Hut weekends and multi-day itineraries

### Why it's parked

A DayHike is one named relation in a length band. Hut-to-hut is a sequence of
DayHikes (or stages) plus overnight Hut choice. OSM superroutes are not that
product; they are often 200 km traces.

### What would have to be true to start

- Huts are first-class nodes with reliable `tourism=alpine_hut` coverage in the
  extract.
- A stage graph (Hut to Hut along hiking relations) is defined, with a day's
  length cap. That is a new Catalog unit, not a filter on DayHike.

---

## 4. Via ferrata, climbing, MTB

### Why it's parked

Different OSM keys (`via_ferrata_scale`, `sport=climbing`, `mtb:scale`),
different danger models, different Seeds. Mixing them into DayHike FeatureVectors
would make similarity meaningless.

### What would have to be true to start

- A separate Catalog type per sport, or an explicit sport dimension that never
  mixes in the same Ranking.

---

## 5. Public transit as access

### Why it's parked

No unified GTFS across HR/SI/BA/ME/Alps. Stitching feeds is a project. Valhalla
can ingest GTFS, but coverage will be patchy.

### What would have to be true to start

- A documented set of GTFS feeds for the Origin regions that matter, rebuilt on
  the same cadence as the OSM snapshot.

---

## 6. Live weather, SolarAspect, and seasonal snow

### Why the rest is parked

LandCover and ClimateNormal are FeatureVector material (ADR-0010). A weekend
forecast is not: it would make "like Seed X" depend on the day of the query.
SolarAspect is still the notebook prototype. Seasonal snow has no honest
per-DayHike layer. LandCover snow is permanent ice on the land-cover map, not
January snowpack.

### What would have to be true to start

- A forecast shown on a detail view, or applied after Ranking the way DriveTime
  is. Never a FeatureVector column.
- SolarAspect promoted from `notebooks/04_rank_feature_lab.ipynb` the same way
  elevation left the notebook.
- A snow layer that describes the season the Hiker asked about.

---

## 7. Photos and prose

### Why it's parked

AllTrails/Komoot/Wikiloc reviews are not a legal source. LLM blurbs of the same
tags as the FeatureVector add no Ranking signal.

### What would have to be true to start

- Wikimedia Commons or Mapillary with license filters, or hand-written notes the
  Hiker types themselves. OSM `description=*` may be shown, not embedded as if it
  were a review.

---

## 8. Isochrone map overlay

### Why it's parked

Isochrones answer "paint the reachable blob." They do not rank DayHikes. Four-hour
driving polygons are expensive. ADR-0004 keeps DriveTime on the matrix API.

### What would have to be true to start

- The UI exists, and Valhalla `/isochrone` is an optional GeoJSON layer, never a
  substitute for Trailhead DriveTime.
