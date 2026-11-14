# Future work

Deliberately deferred ideas, and the measurements behind them, so the research
is not redone. It was written during the Catalog-and-ranker milestone; some
items have since been built, and say so.

---

## 1. Website UI

**Built since:** the web demo (ADR-0014, `src/dayhike/web/`) is live; see
[Web demo](technical.md#web-demo). What follows is the original reasoning.

### Why it was parked

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

A DayHike is one relation of at least 1 km; since ADR-0033 that includes
multi-day tours and long sections, with no upper cap. Hut-to-hut is a sequence of
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
SolarAspect has since been built as a Measure (`southness`, from FABDEM; see
the Measures table in [technical.md](technical.md#measures)). Seasonal snow has no honest
per-DayHike layer. LandCover snow is permanent ice on the land-cover map, not
January snowpack.

### What would have to be true to start

- A forecast shown on a detail view, or applied after Ranking the way DriveTime
  is. Never a FeatureVector column.
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

**Built since:** the length-weighted shares (the second row below) are ADR-0059.
On three new samples of 400 hikes they misassign a mean of 1.6–1.8 % of the walk,
against 3.5–3.6 % for the vertex count. Reading along segments stays parked.

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
- Then for production: the LandCover entry in `GLOSSARY.md` says weighted by
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
- Whether the Catalog's 1 km minimum and the `length` Measure read that walked length
  or keep the one-way length.

ADR-0019 takes up the comparison: with ReturnToStart (on by default) every hike
is measured with its WayBack, the shortest walk back to its Trailhead. The
Catalog keeps the one-way length and the 1 km minimum (no upper cap, ADR-0033); a line left as is near a
bus stop (a shuttle) is still open.

---

## 12. Elevation gain on cliff-side and gorge routes

### Why it's parked

Elevation gain (the `elevation_gain_m` fact, the web page's climb and descent,
and the Catalog's `feature_vectors.elevation_gain_m`) drops DEM spikes (a
vertex, or a run of vertices up to 60 m long, all 10 m or more above or all
below both sides at a 25 % or steeper grade in and out), then counts a climb or
descent only once it moves 10 m. Each drop tests the new last kept vertex
again, so it can drop several in a row, and a real short dip or hump between
steep sides (500 m, 400 m, 500 m, 100 m apart) is dropped too. The numbers below were measured with one-vertex
spikes only; the run rule leaves the median DayHike unchanged and moves 9 % by
more than 5 % (a sample of 1,348). Against
2,973 OSM `ascent` tags the median error is 12 %, and 5 % of DayHikes are over
1.5 times the tag (8 % before the spike filter). Checked against published
gains: Ben Nevis Mountain Path 1,357 m (about 1,350 m), BergeSeen Trail D5
1,693 m (OSM 1,692 m), a 15 km Dutch coast path 28 m (21 m height range).

Routes cut into a cliff side still get far too much: Levada da Ribeira da
Janela (Madeira, 357 m height range, nearly flat in reality) has 1,557 m. There
the DEM is wrong by 20–40 m over many vertices in a row, which neither the
spike filter nor the 10 m dead-band removes; a wider median filter that fixes
it also cuts real mountain gain by about 10 %. A path along a steep lake shore
is the same: Rhodannenberg – Vorauen on Klöntalersee (83 m height range) reads
the lake's flattened surface and the slope above it in turn, 408 m one way
before the run rule and 248 m after it; a 90 m moving average would give 162 m
but trims real climbs by about 10 % (7 % where the height range is over 500 m).

### What would have to be true to start

- A better DEM in steep valleys (lidar where a country publishes it), or a
  rule that recognises a canal or cliff-side path (OSM `waterway=canal` along
  the route, a levada name) and smooths only there.

---

## 13. Suggested lines (A to B)

### Why it's parked

ADR-0020 suggests loops composed from the TrailGraph. Composed lines, which
start and end at different Junctions, are 10^3 to 10^5 times more numerous
than loops on the marked network, and with ReturnToStart a line's WayBack is
usually its retrace, so most would compare like a shorter out-and-back.

### What would have to be true to start

- A beam that ends anywhere the length band allows and scores the walk with
  its WayBack as it grows, within the same per-request budget as loops.
- A rule for which of the near-copies of one line to keep, since a line
  can end at any Junction along its last Segments.

---

## 14. Sharper Lake or sea between barely-wet and dry hikes

### Why it's parked

Since ADR-0058 a dry hike scores about 0.78 on Lake or sea against a hike 20 %
beside water, so a waterside Seed's top 10 can fill with dry hikes. Measured on
notebook 05's region (4,581 DayHikes with a trailhead in 42.3–46.9° N,
13.3–19.5° E, 163 of them beside water, the Calibration re-fitted on random
pairs of these hikes, the way back, default weights, 100 waterside Seeds,
40 random Seeds):

| Option | Top 10 beside water (Seeds 0.15–0.25 / 0.25–0.4 / ≥ 0.4) | Seeds with a dry hike in their top 10 | Compared random pairs at raw 0 | Top-10 places kept, 40 random Seeds |
| --- | --- | --- | --- | --- |
| ADR-0058 as shipped | 51 % (16 / 56 / 79 %) | 84 | 30 % | 100 % |
| A waterside-and-dry gap counts 1.5× | 54 % (17 / 54 / 88 %) | 78 | 47 % | 98.8 % |
| … counts 2× | 60 % (19 / 68 / 90 %) | 72 | 65 % | 97.7 % |
| … counts 3× | 72 % (39 / 84 / 94 %) | 57 | 88 % | 96.5 % |
| Such a pair takes the gap of the shares as measured | 47 % (15 / 45 / 78 %) | 87 | 29 % | 99.5 % |
| As shipped, Lake or sea weighed 2× the other core Measures | 71 % (29 / 87 / 97 %) | 47 | 30 % | — |
| … weighed 3× | 79 % (43 / 93 / 100 %) | 36 | 30 % | — |

The first dry hike in a top 10 is at a median rank of 2 or 3 in every option.
The region has few waterside hikes (3.6 %), so its shares are lower than the
Europe Catalog's 82.6 %; the options are compared with each other.

Most compared random pairs are a dry hike against a waterside one, so the
Calibration ranks any such pair high whatever its raw score: stretching the gap
changes the calibrated score only once it reaches raw 0 (a 0.2-and-dry pair
stays about 0.8 at 1.5× and 2×, and drops to 0 at 3×). That gain comes from
piling pairs on raw 0, the jump in the curve ADR-0058 removed (88 % of compared
pairs at raw 0 at 3×). Taking the measured gap makes it worse. Every option
leaves every other Measure's Calibration byte for byte the same. Raising Lake
or sea's weight, which a Hiker can already do, gives more than any rule change
and keeps the curve.

### What would have to be true to start

- Hikers saying waterside Seeds' lists feel too dry, with the weight lever not
  enough. Then a separate random-pair population for waterside-and-dry pairs,
  rather than a sharper raw score, would be the place to look.
