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

---

## 9. LandCover along the line, not per vertex

### Why it's parked

LandCover fractions count each GeometryStore vertex once. OSM vertices are dense
on bends (forest switchbacks, villages) and sparse on straight stretches
(fields, roads), so bendy parts weigh more than their length, and the middle of
a long segment is never read. Measured on the notebook 05 region (400 of its
4,408 DayHikes; truth is WorldCover read every 10 m along each segment):

| Method | Share of the walk misassigned: mean / p95 / worst | Points read |
| --- | --- | --- |
| Vertex count (today) | 2.7% / 8.0% / 14.4% | 1× vertices |
| Vertex weighted by half its two segments | 1.3% / 3.9% / 11.7% | 1× (no new reads) |
| Read every 25 m along segments | 1.2% / 4.0% / 7.9% | 1.3× |

Only 6.4% of the walked length lies in segments over 100 m, so uneven vertex
density, not long gaps, is most of the error. The `landcover` Measure inherits
it one to one. That is small next to the gap between real similar
and dissimilar pairs, so it waits.

Same pattern, not yet measured: ClimateNormal is a per-vertex mean (1 km grid,
small effect expected), and elevation gain is read only at vertices, so a climb
inside a long segment is missed.

### What would have to be true to start

- Length-weighted shares from the vertex `landcover`, with notebook 05's
  verification still passing.
- Then for production: the LandCover entry in `CONTEXT.md` says weighted by
  length instead of "each GeometryStore vertex counts once", and `sample-landcover`
  and `build-similarity` are rerun to refresh the Catalog fractions and the
  similarity facts. Ranking changes with it.
- Reading along segments only if review shows the worst cases matter; it needs
  a new raster pass and somewhere to keep the extra points.
- Elevation gain measured the same way before deciding whether it needs the
  same treatment.

---

## 10. Ferry and bus ways in walked length

### Why it's parked

Some hiking relations include a `route=ferry` way or a bus transfer along a
road, and DayHikeGeometry counts it as walked length. On the full Catalog only
about 16 DayHikes have such a step over 2 km (ADR-0012 audit).

### What would have to be true to start

- A rule for which member ways are not walked (ferry, `highway` of a bus-only
  road, tagged transfer), and whether they stay in the line but not in
  `length_m`.

---

## 11. Walking back on a line

### Why it's parked

A DayHike's length is its main walk, each way once. For a RoundTrip that is the
whole outing. For a line (87.5% of the Slovenia + Croatia region) and a lollipop
(4.1%) it is not: the Hiker walks back (about twice the length on an
out-and-back, the stem again on a lollipop) or takes a shuttle. OSM does not
say which, and relations that list their ways twice are counted once (the
second listing is a `repeat` Leftover). The `length` Measure compares one-way
lengths, so a 5 km line and a 5 km RoundTrip look equally long. The
`round_trip` Measure tells them apart but does not change `length`.

### What would have to be true to start

- A rule for the length a Hiker walks: twice a line, the loop plus twice the
  stem for a lollipop, or a line left as is when it ends near a bus stop or
  parking.
- Whether the Catalog length band (1–20 km) and the `length` Measure read that walked length
  or keep the one-way length.

ADR-0019 takes up the comparison: with ReturnToStart (on by default) every hike
is measured with its WayBack, the shortest walk back to its Trailhead. The
Catalog keeps the one-way length and the 1–20 km band; a line left as is near a
bus stop (a shuttle) is still open.

---

## 12. Elevation gain on cliff-side and gorge routes

### Why it's parked

Elevation gain (the `elevation_gain_m` fact, the web page's climb and descent,
and the Catalog's `feature_vectors.elevation_gain_m`) drops one-vertex DEM
spikes (10 m or more above or below both neighbours at a 25 % or steeper grade
each side), then counts a climb or descent only once it moves 10 m. Against
2,973 OSM `ascent` tags the median error is 12 %, and 5 % of DayHikes are over
1.5 times the tag (8 % before the spike filter). Checked against published
gains: Ben Nevis Mountain Path 1,357 m (about 1,350 m), BergeSeen Trail D5
1,693 m (OSM 1,692 m), a 15 km Dutch coast path 28 m (21 m height range).

Routes cut into a cliff side still get far too much: Levada da Ribeira da
Janela (Madeira, 357 m height range, nearly flat in reality) has 1,557 m. There
the DEM is wrong by 20–40 m over many vertices in a row, which neither the
spike filter nor the 10 m dead-band removes; a wider median filter that fixes
it also cuts real mountain gain by about 10 %.

### What would have to be true to start

- A better DEM in steep valleys (lidar where a country publishes it), or a
  rule that recognises a canal or cliff-side path (OSM `waterway=canal` along
  the route, a levada name) and smooths only there.
