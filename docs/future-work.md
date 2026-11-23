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

A DayHike is a relation of at least 1 km, or a built hike; since ADR-0033 that
includes multi-day tours and long sections, with no upper cap, and since
ADR-0040 a tour over 25 km is also cut into DayStages that end at a hut, else a
place, else parking. Hut-to-hut is a sequence of
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

No unified GTFS across Europe and the US. Stitching feeds is a project. Valhalla
can ingest GTFS, but coverage will be patchy.

### What would have to be true to start

- A documented set of GTFS feeds for the Origin regions that matter, rebuilt on
  the same cadence as the OSM snapshot.

---

## 6. Live weather, SolarAspect, and seasonal snow

### Why the rest is parked

LandCover and ClimateNormal are FeatureVector material (ADR-0010). A weekend
forecast is not: it would make "like Seed X" depend on the day of the query.
SolarAspect has since been built as a Measure (`southness`, from the DEM; see
the Measures table in [technical.md](technical.md#measures)). Seasonal snow has no honest
per-DayHike layer. LandCover snow is permanent ice on the land-cover map, not
January snowpack.

### What would have to be true to start

- A forecast shown on a detail view, or applied after Ranking the way DriveTime
  is. Never a FeatureVector column.
- A snow layer that describes the season the Hiker asked about.

---

## 7. Photos and prose

**Built since (photos only):** a hike shows up to two free Wikimedia Commons
photos (CC0, CC BY, CC BY-SA) with author and licence, added by
`dayhike export-photos`; see [DATA-LICENSES.md](../DATA-LICENSES.md). Looking
photos up when a hike is opened rather than at build time is open (#189). Prose is
still parked. What follows is the original reasoning.

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

**Built since:** ADR-0059. The vertices weigh by length (the second row of the
first table), and steps over 40 m are read every 20 m at most (the row "Segments
over 40 m, every 20 m" of the second table): on three new samples of 400 hikes
the walk misassigned falls from 3.5–3.6 % (vertex count) and 1.6–1.8 % (vertices
by length) to 1.23–1.42 %, for 1.35 times the points read. The rest of this
section is the study that led there.

### Why it's parked

LandCover fractions count each GeometryStore vertex once. OSM vertices are dense
on bends (forest switchbacks, villages) and sparse on straight stretches
(fields, roads), so bendy parts weigh more than their length, and the middle of
a long segment is never read. Measured on the notebook 05 region (400 of its
4,408 DayHikes; truth is WorldCover read every 10 m along each segment):

| Method | Share of the walk misassigned: mean / p95 / worst | Points read |
| --- | --- | --- |
| Vertex count (before ADR-0059) | 2.7% / 8.0% / 14.4% | 1× vertices |
| Vertex weighted by half its two segments | 1.3% / 3.9% / 11.7% | 1× (no new reads) |
| Read every 25 m along segments | 1.2% / 4.0% / 7.9% | 1.3× |

Only 6.4% of the walked length lies in segments over 100 m, so uneven vertex
density, not long gaps, is most of the error. The `landcover` Measure inherits
it one to one. That is small next to the gap between real similar
and dissimilar pairs, so it waits.

Reading along segments, measured again after ADR-0059 on the same three samples
of 400 (the samples ADR-0059 uses, truth as above, now 5.3–5.6 % of the walked
length in segments over 100 m). Each read stands for an equal part of its
segment. Shorter segments keep half to each end vertex:

| Reads added | Mean error (3 samples) | p95 | Worst | Points read |
| --- | --- | --- | --- | --- |
| None (ADR-0059) | 1.64 / 1.83 / 1.73 % | 4.6–5.8 % | 24.8 % | 1× |
| Segments over 100 m, every 25 m | 1.42 / 1.68 / 1.55 % | 3.9–5.4 % | 12.1 % | 1.05–1.06× |
| Segments over 50 m, every 25 m | 1.28 / 1.50 / 1.34 % | 3.4–4.7 % | 10.9 % | 1.19–1.22× |
| Segments over 40 m, every 20 m (built, ADR-0059) | 1.23 / 1.42 / 1.25 % | 3.0–4.5 % | 11.2 % | 1.32–1.37× |
| Segments over 30 m, every 15 m | 1.14 / 1.34 / 1.21 % | 3.1–4.4 % | 11.2 % | 1.61–1.68× |
| Segments over 20 m, every 10 m | 1.01 / 1.18 / 1.04 % | 2.9–4.2 % | 11.2 % | 2.32–2.44× |

The first row that stays under 1.5 % on every sample is segments over 40 m read
every 20 m, for about a third more points read. Even 10 m reads leave about 1 %,
because a vertex and the point the truth reads beside it can fall in
different 10 m WorldCover cells. No re-weighting of the vertex classes gets
there. A cap on how much of a long segment each end vertex may claim made the
error worse at every cap tried (100, 50, 25, 15 and 10 m).

Same pattern, not yet measured: ClimateNormal is a per-vertex mean (1 km grid,
small effect expected), and elevation gain is read only at vertices, so a climb
inside a long segment is missed.

### What would have to be true to start

- Length-weighted shares from the vertex `landcover`, with notebook 05's
  verification still passing (built: ADR-0059).
- Then for production: the LandCover entry in `GLOSSARY.md` says weighted by
  length instead of "each GeometryStore vertex counts once", and `sample-landcover`
  and `build-similarity` are rerun to refresh the Catalog fractions and the
  similarity facts (built: ADR-0059). Ranking changes with it.
- Reading along segments (built: ADR-0059 reads steps over 40 m every 20 m,
  kept in `vertices.landcover_along` and `segments.landcover_along`).
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

Routes cut into a cliff side got far too much: Levada da Ribeira da Janela
(Madeira, 357 m height range, nearly flat in reality) had 1,557 m. There the DEM
is wrong by 20–40 m over many vertices in a row, which neither the spike filter
nor the 10 m dead-band removes; a wider median filter that fixes it also cuts
real mountain gain by about 10 %. A path along a steep lake shore is the same:
Rhodannenberg – Vorauen on Klöntalersee (83 m height range) reads the lake's
flattened surface and the slope above it in turn, 408 m one way before the run
rule and 248 m after it.

Since ADR-0062 the gain smooths only the noisy stretches: where the kept heights
stray 2.5 m or more on average from their 90 m moving average over 200 m either
side, they are that moving average. On 26 OSM relations along a bisse, Suone,
canal or steep lake shore the median error against their tags falls from 16 %
to 10 %; the median random DayHike does not change. Rhodannenberg – Vorauen now
climbs 177 m and Marlinger Waalweg 314 m (475 m before). Levada da Ribeira da
Janela still climbs 1,077 m: on long, sparsely noded stretches the DEM is wrong
by more than a moving average can mend. In Liechtenstein, where FABDEM already
counts 14 % less than the OSM tags, the median error rises from 15 % to 18 %.
These were measured on FABDEM; the last build reads Copernicus GLO-30 and was
not measured again.

### What would have to be true to start

- A better DEM in steep valleys and on Madeira: lidar where a country publishes
  it openly, read for those areas (issue #138's second direction, closed). For
  the US, `fetch-dem --source 3dep` reads USGS 3DEP, but the build still reads
  Copernicus GLO-30 there (#188).

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

---

## 15. A popularity signal from open data

### Why it's parked

HikesAlike knows what a hike is like, not whether people know it. Issue #156
measured candidate signals from open sources only (Wikidata and Wikimedia
pageviews, CC0; Wikimedia Commons geotags; OpenStreetMap, ODbL) for one region,
Slovenia, against two hand-made lists: 30 well-known day hikes (Triglav from
Aljažev dom and Dolič, the Seven Lakes valley from Komarča and Planina Blato,
Vintgar gorge, Stol, Mangart, Krn, Okrešelj, Velika planina, Jalovec, Golica,
Snežnik, Nanos, Slavnik, Uršlja gora, Kum, the Pohorje lakes, Storžič,
Juliana Trail stage 1, Mala Osojnica above Bled, Rakov Škocjan, Škocjan Caves
and others) and 30 obscure ones (village and forest walks picked by name from a
seeded random sample of the region's hikes, before any signal was looked at).
The hikes are the 2,007 that start inside Slovenia in a fresh Slovenia-only
ingest of the 2026-09-17 Geofabrik extract with today's rules (1,642 relation
hikes, 365 DayStages).

"Separation" is the chance a well-known hike scores above an obscure one (ties
count half): 0.5 is no better than a coin, 1.0 is perfect. "Coverage" is the
share of the region's hikes with a non-zero value.

| Signal | Separation | Well-known / obscure non-zero | Coverage |
| --- | ---: | ---: | ---: |
| Commons: distinct uploaders of geotagged files within 150 m of the line | **0.90** | 29 / 15 | not measured region-wide |
| Commons: the same per km | 0.78 | 29 / 15 | not measured region-wide |
| OSM: hiking relations sharing each member way (mean) | **0.82** | 30 / 30 | 100 % |
| Named points' page views, 12 months, sl/en/de/it/hr/hu articles (sum) | 0.70 | 14 / 3 | 23 % (points with an article in those six languages; views were read for the 60 only) |
| Named points' Wikipedia languages (max sitelinks) | 0.68 | 14 / 4 | 28 % |
| OSM: guideposts within 50 m, per 10 km | 0.60 | 30 / 22 | 81 % |
| OSM: route network level (iwn > nwn > rwn > lwn) | 0.48 | 27 / 30 | 96 % |
| OSM: `osmc:symbol` present | 0.43 | 24 / 28 | 85 % |
| OSM: `wikidata=*` on the hike's own relation | 0.50 | 0 / 0 | 2 % (DayStages of tagged long routes only) |

- **Commons** separates best. At 3 or more distinct uploaders it picks 23 of
  the 30 well-known hikes and 3 of the 30 obscure ones; at 5 or more, 13 and
  none. Part of that is length (the well-known median is 5.4 km, the obscure
  3.4 km): per km it drops to 0.78. Files on round coordinates (3 decimals or
  fewer) were dropped as a place, not a photo spot; counting uploaders, not
  files, removes bulk uploads. Only the count is kept, never a name.
- **Route sharing** costs nothing (it is in the Catalog's own OSM data) and
  separates well, but it marks trail hubs near huts and valley bases as much as
  fame.
- **Wikipedia** covers a hike only through an OSM point within 150 m that
  has `wikidata=*` (a named peak, hut, viewpoint, waterfall, gorge or cave): 28
  % of hikes. Lakes are areas, not nodes, so the Seven Lakes hikes score 0.
  No Slovenian day-hike relation has `wikidata=*`; only long-distance routes
  do. The pageviews API throttles anonymous clients hard (HTTP 429 at about one
  request a second), so page views were read for the 60 listed hikes only.
- **OSM tags** do not separate here: Slovenia tags nearly every hike `lwn` with
  the standard red-and-white `osmc:symbol`, famous or not.

**Gap list (Wikidata).** 49 Wikidata items in Slovenia that are trails: the
subclasses of hiking trail (`Q2143825`) and trail (`Q628179`, `Q1966910`)
with `P17 = Q215` or a coordinate inside the country, plus the members of
sl.wikipedia's trail categories (Naravoslovne učne poti v Sloveniji,
Pohodništvo v Sloveniji, Pohodne poti, Slovenska planinska pot), which find
items Wikidata gives no class.

| Status | Items |
| --- | ---: |
| A hike, by the item's OSM relation (`P402`, or the relation's `wikidata=*`) | 4 |
| A hike, by name and place, or by hand | 13 |
| A long route whose sections or DayStages are hikes | 9 |
| An OSM relation, but no hike starts in Slovenia | 3 |
| No hike | 20 |

Of the 20 with no hike, 15 are short local trails, most of them educational
(učne poti), with no OSM relation and no path of that name, which is OSM mapping work; one has 10
ways named "Pot ob Rakah" (#125, which built named-trail hikes since, ADR-0066, but
only for the US part of the build; Europe has none until #193); two are
Destinations (the Kokra canyon, the Pekel gorge at Borovnica, where a hike ends
but none walks the gorge) (#124, which built DestinationHikes since, ADR-0067, to
peaks, huts, viewpoints and lakes only, so a gorge is still not one);
two are multi-day routes with no OSM relation (the Walk of Peace, Via Dinarica
Blue).

**Gap list (Destinations).** Of the 461 OSM points in Slovenia with
`wikidata=*` that are a peak, cave, waterfall, saddle, hut or gorge (huts are
mostly mapped as areas, so only 7 are points), 251 have a hike within 150 m and 123 have none within 500 m: 92 peaks, 14 caves, 12
waterfalls, 4 saddles and a gorge. Slap Boka (930 m from the nearest hike, 9
Wikipedia languages) and the Veliki Kozjak waterfall (544 m) are the best
known; Slap Savica (233 m) and Peričnik (159 m) are just off a hike's line.
These are #124's Destinations; the DestinationHikes built since (ADR-0067) reach
peaks, not caves, waterfalls, saddles or gorges. Not counted again.

**Recommendation: none now; a badge later, if a second region agrees.** No
signal is good enough to change a score: the only strong one, Commons, is not
measured beyond the 60 hikes, and ranking by fame would push down the obscure
hikes HikesAlike is good at finding. A "well-known" badge (3 or more distinct
Commons uploaders within 150 m) is the candidate, with route sharing as a
free second opinion. A tie-break or a filter would need the same data and more
trust in it than one region gives. The Wikidata gap lists fed #124 and #125,
both built since (ADR-0067, ADR-0066).

### Second region: Colorado (#170)

Colorado was picked over the Écrins because its mapping habits differ most
from Slovenia's: US mappers draw each trail as a named path and use route
relations mainly for long trails, and there are no waymark symbols or dense
networks of local routes. The hikes are the 284 that start inside the state
box (37° to 41° N, 102.04° to 109.05° W) in a fresh Colorado-only ingest of
the 2026-11-13 Geofabrik extract with today's rules (157 relation hikes, 127
DayStages, most of them stages of the American Discovery Trail, the Denver
Orbital Trail and other long routes). The lists were fixed before any signal
was computed. The well-known list has 25 hikes, not 30: 12 matches, by name or
start, from a list of 50 Colorado classics written from general knowledge
(Emerald Lake, Glacier Gorge, Longs Peak, Wild Basin, Barr Trail, the
Manitou Incline, Maroon Lake to Crater Lake, Blue Lakes, Lost Lake from
Hessie, Waterton Canyon), plus 13 Catalog hikes known by name and place
(Flat Top Mountain, Fern Lake, Mount Sherman, Ouray Perimeter, Jud Wiebe,
Petrified Forest Loop at Florissant, North Table, Crag Crest and others). The
12 matches cover 15 of the 50 classics (Glacier Gorge reaches Mills Lake and
Alberta Falls, Longs Peak Trail the Chasm Lake and Keyhole routes); the other
35, Hanging Lake and Quandary Peak among them, have no hike, as US trails are
mostly named paths in no relation (#153). The 30 obscure hikes are the first 30 of a
seeded random order of the remaining hikes, leaving out Colorado Trail, CDT
and Collegiate stages and a few middling names. The same method gives this
table:

| Signal | Separation | Well-known / obscure non-zero | Coverage |
| --- | ---: | ---: | ---: |
| Commons: distinct uploaders of geotagged files within 150 m of the line | 0.64 | 19 / 20 | not measured region-wide |
| Commons: the same per km | 0.73 | 19 / 20 | not measured region-wide |
| OSM: hiking relations sharing each member way (mean) | 0.49 | 25 / 30 | 100 % |
| OSM: guideposts within 50 m, per 10 km | 0.69 | 13 / 5 | 28 % |
| OSM: route network level (iwn > nwn > rwn > lwn) | 0.27 | 15 / 23 | 78 % |
| OSM: `osmc:symbol` present | 0.50 | 0 / 0 | 0 % |
| OSM: `wikidata=*` on the hike's own relation | 0.54 | 3 / 1 | 11 % |
| Named points' Wikipedia languages (max) | 0.48 | 2 / 4 | 12 % |
| Named points' page views, 12 months, en/es/de/fr articles (sum) | 0.48 | 2 / 4 | 12 % (points with an article in those languages) |

- **Commons does not reach 0.85 here.** Two things pull it down. Long
  stages that pass through towns and along roads collect photos of those towns:
  against the 21 obscure stages it separates at 0.56, and 11 of them reach 3
  uploaders. Against the 9 obscure local trails it separates at 0.82, and only
  one reaches 3 uploaders. Commons is also sparse in the Colorado mountains:
  6 well-known hikes have no geotagged file within 150 m (the Crags, Mount
  Cutler, North Table, Crag Crest, the Telluride Via Ferrata, Lost Lake). At 3
  or more uploaders it picks 16 of the 25 well-known hikes and 12 of the 30
  obscure ones. The obscure median is 13.8 km, the well-known 5.4 km, so per km
  does better (0.73), and among hikes of 12 km or less it is 0.82.
- **Route sharing fails.** Colorado's relations rarely overlap (the median is
  1.0 on both lists), so the Slovenian signal came from Europe's dense networks
  of waymarked routes, not from fame.
- **Network level is reversed.** The American Discovery Trail is tagged `nwn`,
  so obscure plains stages outrank every park trail.
- **Wikipedia** covers 12 % of hikes. Only 25 of the state's 518 Wikidata-linked
  OSM points (peaks, waterfalls, huts, viewpoints, churches and others) lie
  within 150 m of a hike.

### Recommendation after two regions

Build the index on Commons alone, and show it as what it is: a count ("N
Commons contributors photographed near this line"), not a "well-known"
badge. A fixed threshold of 3 uploaders is a fair claim in Slovenia (23 of 30
well-known hikes, 3 of 30 obscure), but in Colorado it gives the badge to 11 of
21 obscure long-route stages and leaves it off 9 of 25 classics. Drop route
sharing as a second opinion. Ranking stays unchanged. Two changes could lift
Commons, and neither is measured yet: counting per km, or leaving out files
inside towns. A US hike population worth testing again needed the
named-trail hikes of #153 first, since 35 of the 50 Colorado classics were not
hikes then; named-trail hikes (ADR-0066) and DestinationHikes (ADR-0067) are
built since, in the US part of the build (ADR-0069), and the Colorado lists have
not been measured again.

### What would have to be true to start

- Done in #170: the same lists for a second region with different mapping
  habits. Commons did not stay at 0.85 or better (Colorado 0.64, 0.82 against
  local trails only), so it ships as a count, not a badge.
- Commons counts for every hike from the dumps, not the API: the 2026-11
  `commonswiki-latest-geo_tags.sql.gz` is 0.8 GB, but the uploader is in the
  18.8 GB `image` table, so a build step that streams both and keeps one count
  per hike. Calling the API per hike would take about 20 requests a hike,
  millions for Europe, and the API throttles.
- An ADR for the index: what it says, how it is shown, what a hike with no
  data shows, and that it never changes a score or the order of a Ranking.

**Built in #170 (ADR-0064):** `dayhike count-commons` counts Commons
contributors for every DayHike from the `geo_tags`, `page` and `image` dumps
and the panel shows "N Commons contributors". What stays open here: the count
per km or without files inside towns, and a sort or filter of a Ranking by the
count. The count already decides which of several places named the query leads
the hike search (ADR-0079).
