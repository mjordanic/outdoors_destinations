# PRD: Facet similarity (notebook design phase)

Status: ready-for-agent

Vocabulary in this document is defined in `GLOSSARY.md` and used in its glossary
sense: DayHike, DayHikeGeometry, GeometryStore, Catalog, Trailhead, Seed,
Ranking, Hiker, Difficulty, ElevationProfile, ProfileShape, LandCover,
ClimateNormal, QueryMonth, SolarAspect, Hut, Viewpoint, Peak, Shelter,
AmenityFact, Facet, FacetScore, FacetWeights, Corridor, DryStretch,
SurfaceMix, TarmacApproach. Follows ADR-0011 (proposed: similarity is a
Hiker-weighted mean of FacetScores). Production Ranking keeps ADR-0003 (max
cosine) in this phase. Respects ADR-0006 (Ranking does not read GeometryStore;
these notebooks do), ADR-0008 (local FABDEM) and ADR-0010 (QueryMonth selects
the climate slice).

## Problem Statement

A Hiker who names a Seed gets neighbours from one cosine over a scaled
FeatureVector. They cannot say what "similar" means for this query: "I care
about huts and how high it goes, not about the trees." The cosine score is not
in a readable range, mixes every column with fixed weight, and cannot be
explained per theme. Several things the Hiker cares about are not in the
FeatureVector at all: water along the way, Huts and restaurants that are on
the route rather than within 20 km of the Trailhead, the shape of the climb,
the trail surface, how long the walk on tarmac is before the trail starts,
which way the slopes face, and whether the hike reaches a summit or a
Viewpoint.

The Hiker wants to design the replacement in a controlled place first: a
readable notebook over a real region, where every measure can be looked at,
checked against hikes whose character is known, and argued with, before any
of it becomes a script or a Catalog column.

## Solution

Two new notebooks over a Slovenia + Croatia region of the real GeometryStore,
loaded the way notebook 04a loads it (vertices, amenities, dayhikes and ways
as GeoDataFrames, with the Catalog's sampled summaries merged on by id).

The first notebook turns each DayHike in the region into one row of
similarity facts, one Facet at a time, with plots and short explanations, and
writes that table next to the Catalog.

The second notebook compares DayHikes. Eight Facets each give a FacetScore in
[0, 1]:

| Facet | What it compares |
| --- | --- |
| Effort | length, elevation gain, Difficulty |
| Altitude | how high: highest and lowest point |
| Profile | ProfileShape: the form of the elevation plot |
| Landscape | LandCover mix, Peak summited, Viewpoint along, Peaks around |
| Weather | ClimateNormal for the QueryMonth slice, SolarAspect |
| Water | water points in the Corridor, DryStretch |
| Facilities | Hut / restaurant_cafe / Shelter along; restaurant_cafe and parking at the Trailhead |
| Trail | SurfaceMix, TarmacApproach |

The Hiker gives FacetWeights (e.g. Facilities 50, Altitude 30, Landscape 20,
Profile 0). The combined score is the weighted mean of the FacetScores that
both DayHikes can be scored on, plus a coverage figure that says how much of
the weight was actually scored. The notebook ranks the region against a Seed
with two different FacetWeights sets and shows the results differ the way the
weights say they should.

For every Facet, the notebook names a pair of DayHikes that should be similar
and a pair that should not, states the expected verdict before computing, and
prints PASS or FAIL. Identity, symmetry and reversed-direction checks run on
every Facet.

Pure measure functions and GeometryStore extraction helpers live in a small
package module with pytest tests. The notebooks call them plainly, one Facet
per section, with no classes or pipelines.

Production `rank_dayhikes`, the CLI, ingest and the Catalog schema do not
change. Once the Hiker approves the design, a later phase optimizes it and
runs it for the whole of Europe.

## User Stories

**Facets and weights**

1. As a Hiker, I want similarity split into a small number of named Facets, so that I can say which themes matter for this query.
2. As a Hiker, I want every FacetScore to be between 0 and 1, so that I can read and compare them directly.
3. As a Hiker, I want to give FacetWeights in any scale (50/30/20 or 5/3/2), so that I do not have to make them add up myself.
4. As a Hiker, I want a Facet with weight 0 to have no effect, so that I can switch a theme off.
5. As a Hiker, I want the combined score to be the weighted mean of FacetScores, so that one weak Facet lowers the score in proportion to its weight and does not zero it.
6. As a Hiker, I want each FacetScore to be the plain mean of its measures, so that I can see why a Facet scored what it did.
7. As a Hiker, I want a Facet that cannot be scored on either DayHike to drop out for that pair, so that a hike outside the sampled rasters is neither punished nor flattered with an invented value.
8. As a Hiker, I want a coverage figure next to every combined score, so that I know how much of my weight was actually compared.
9. As a Hiker, I want a measure that is missing inside a Facet (for example, Difficulty is not tagged) to drop out of that Facet's mean, so that the other measures still count.
10. As a Hiker with several Seeds, I want a candidate's score to be its best combined score against any one Seed, so that the result is not stitched together from different Seeds.
11. As a Hiker, I want to see the per-Facet scores next to the combined score in a ranking, so that I can tell which Facet put a hike near the top.

**Measure kinds**

12. As a Hiker, I want size quantities (length, gain, counts of Peaks, precipitation, DryStretch) compared as a ratio of the smaller to the larger, so that 15 vs 20 km is closer than 5 vs 10 km.
13. As a Hiker, I want tiny values below a floor treated as the floor, so that 10 m vs 30 m of gain counts as the same flat walk.
14. As a Hiker, I want level quantities (elevation, temperature, solar radiation, southness, Difficulty rank, capped water count, TarmacApproach) compared by their difference against a stated tolerance, so that 1800 m vs 2000 m is close and 300 m vs 2000 m is not.
15. As a Hiker, I want mixes (LandCover, SurfaceMix) compared as the share of the hike that matches, so that "half forest, half grass" vs "all forest" scores one half.
16. As a Hiker, I want yes/no facts compared by whether they agree, so that two DayHikes that both have no Hut count as alike on that point.
17. As a Hiker, I want every tolerance and floor written in the notebook with the data spread it was chosen from, so that I can challenge it.

**Effort**

18. As a Hiker, I want length compared, so that a 6 km walk does not match an 18 km day.
19. As a Hiker, I want elevation gain compared, so that a flat walk does not match a steep climb of the same length.
20. As a Hiker, I want Difficulty compared when both DayHikes have it, so that a T2 hike does not match an alpine T5 route.

**Altitude**

21. As a Hiker, I want the highest point compared, so that a lowland hill does not match a 2500 m ridge.
22. As a Hiker, I want the lowest point compared, so that a coastal climb from sea level does not match a hike that starts at a 1500 m pass.

**Profile**

23. As a Hiker, I want the form of the elevation plot compared without its absolute height or gain, so that "one long climb then down" matches another one-climb hike of a different size.
24. As a Hiker, I want a DayHike walked in the opposite direction to have the same Profile score, so that OSM's arbitrary way order does not matter.
25. As a Hiker, I want a nearly flat DayHike not to have its noise blown up into a dramatic shape, so that flat walks match other flat walks.
26. As a Hiker, I want plain point-by-point comparison and banded dynamic time warping shown side by side, so that I can decide whether a climb at 30% should match a climb at 50%.

**Landscape**

27. As a Hiker, I want the LandCover mix compared, including the leftover share, so that a forested hike matches forested hikes and open karst matches open karst.
28. As a Hiker, I want to know whether a DayHike reaches a Peak, so that summit hikes match summit hikes.
29. As a Hiker, I want to know whether a Viewpoint is along the DayHike, so that hikes with a view match each other.
30. As a Hiker, I want the number of Peaks around the DayHike compared, so that a hike among mountains does not match one in open hills.

**Weather**

31. As a Hiker, I want temperature, precipitation and solar radiation compared for the QueryMonth, so that a July comparison uses July climate.
32. As a Hiker, I want the June–August regime used when I name no QueryMonth, so that the comparison still has a season.
33. As a Hiker, I want SolarAspect (how south-facing the slopes along the hike are) compared, so that a shady north-face hike does not match a sun-baked south face.
34. As a Hiker, I want flat stretches left out of SolarAspect, so that a valley floor does not count as facing any way.

**Water**

35. As a Hiker, I want the number of drinking-water points in the Corridor compared, capped at three, so that "none", "one", "a couple" and "plenty" are the levels that matter.
36. As a Hiker, I want DryStretch compared, so that a hike with water every 3 km does not match one with a 12 km stretch without any.
37. As a Hiker, I want the notebook to say that natural springs are mostly missing from the GeometryStore, so that I read the Water Facet with that gap in mind.

**Facilities**

38. As a Hiker, I want to know whether a Hut is along the DayHike, so that hut hikes match hut hikes.
39. As a Hiker, I want to know whether a restaurant or cafe is along the DayHike, so that I can find hikes with a lunch stop.
40. As a Hiker, I want to know whether a Shelter is along the DayHike, so that hikes with somewhere to wait out rain match each other.
41. As a Hiker, I want to know whether there is a restaurant or cafe at the Trailhead, so that I can find hikes that end with a meal.
42. As a Hiker, I want to know whether there is parking at the Trailhead, so that I can find hikes I can drive to and park at.

**Trail**

43. As a Hiker, I want the SurfaceMix (paved, gravel, natural, rock) compared by length, so that a forest-path hike does not match a hike on asphalt.
44. As a Hiker, I want a way with no surface tag classified from its highway kind, so that the 40% of untagged ways still count.
45. As a Hiker, I want TarmacApproach compared, so that a hike that leaves the road at once does not match one with 3 km of road first.

**Verification**

46. As a Hiker, I want, for every Facet, a pair of real named DayHikes that should be similar and a pair that should not, so that I can see the Facet behaves as intended.
47. As a Hiker, I want the expected verdict and the reason stated before the score is computed, so that the check is honest.
48. As a Hiker, I want each pair shown on a map with its facts (and profiles where relevant), so that I can judge the verdict myself.
49. As a Hiker, I want a PASS/FAIL line per check, with the similar pair scoring higher than the dissimilar pair and soft targets of at least 0.7 and at most 0.4, so that I can see at a glance what worked.
50. As a Hiker, I want identity (a DayHike vs itself is 1), symmetry (A vs B equals B vs A) and reversed direction checked on every Facet, so that the basic math is right.
51. As a Hiker, I want an end-to-end ranking for one Seed under two FacetWeights sets (Facilities-heavy vs Altitude-heavy), with the top five of each on a map, so that I can see the weights change the answer.
52. As a Hiker, I want the notebook to report how often each Facet is missing in the region, so that I know where coverage is thin.

**Notebook and code**

53. As a Hiker, I want the notebooks to load the GeometryStore the way 04a does, filtered to the region, so that the analysis works on frames I already know.
54. As a Hiker, I want each Facet in its own short notebook section that calls a few plain functions, so that I can read the algorithm top to bottom.
55. As a Hiker, I want the similarity facts written to one table next to the Catalog, so that the comparison notebook does not recompute geometry work each time.
56. As a Hiker, I want both notebooks to run offline on the committed OSM fixture under the test suite, so that they do not rot.
57. As a Hiker, I want the measure functions covered by unit tests, so that the notebook rests on checked math.
58. As a Hiker, I want production Ranking and the Catalog left unchanged, so that this design can be thrown away or changed freely until I approve it.

## Implementation Decisions

**Phase boundary**

- Notebook-only design phase. `rank_dayhikes`, the CLI, ingest, the Catalog schema and the scaler are not touched. ADR-0011 stays `proposed`.
- New package module `dayhike.similarity` holds two kinds of plain functions: pure measure functions (numbers in, a similarity in [0, 1] or None out) and GeometryStore extraction helpers (arrays or frame rows in, per-DayHike facts out). No classes beyond what pandas/geopandas already give; no registry, config object or pipeline. Notebooks call these functions directly, one section per Facet.

**Region and loading**

- Region: DayHikes whose Trailhead lies in lat 42.3–46.9, lon 13.3–19.5 (Slovenia + Croatia box, 4,408 DayHikes at the time of writing, all with LandCover and ClimateNormal). It covers the Julian Alps, karst, Velebit, the coast and the Pannonian lowland.
- Loading follows notebook 04a: vertices, amenities, dayhikes and ways as GeoDataFrames in EPSG:4326, Catalog `feature_vectors` (and `climate_months`, `trailheads`, `dayhikes.difficulty`) merged on by id. Rows are filtered to region DayHike ids with SQL (the full GeometryStore is ~19 GB); amenities are filtered to the region box padded by a few kilometres.
- On the committed OSM fixture (when the local Catalog is missing or under pytest), both notebooks build the fixture Catalog the way 04a does and run every cell.

**Similarity facts table**

- Notebook 05 produces one row per region DayHike with every fact the measures need: length, elevation gain, Difficulty rank, highest and lowest point, ProfileShape, six LandCover fractions (the five Catalog fractions plus the leftover share), Peak summited, Viewpoint along, Peaks around, the regime and twelve monthly ClimateNormal values, SolarAspect southness, water count, DryStretch, the five Facilities flags, four SurfaceMix fractions and TarmacApproach. Missing values stay missing (None/NaN), never 0.
- The table is written as a pandas pickle next to the Catalog: `{catalog}.similarity_facts.pkl` (for the local Catalog, `data/catalog.similarity_facts.pkl`). It is regenerable and must be gitignored (add a `*.similarity_facts.pkl` rule; it is not ignored today). Notebook 05a reads it when present; when it is absent (fixture, pytest) 05a builds the facts itself with the same `dayhike.similarity` helpers.

**Combining**

- Combined score = Σ wᶠ·sᶠ / Σ wᶠ over Facets with a non-missing FacetScore and wᶠ > 0. Coverage = that weight sum / the sum of all positive weights. If no Facet can be scored, the combined score is missing.
- FacetScore = plain mean of the Facet's non-missing measures; missing if all are missing.
- Several Seeds: a candidate's score is the maximum combined score to any Seed.

**Measure kinds** (each returns a value in [0, 1], or None when either input is missing)

- Ratio: min(a′, b′) / max(a′, b′), where a′ = max(a, floor). Both at or below the floor gives 1.
- Tolerance: max(0, 1 − |a − b| / D).
- Mix: Σ minᵢ(pᵢ, qᵢ) over fractions that sum to 1 (include the leftover share).
- Presence: share of yes/no flags that agree (both-absent is agreement).
- ProfileShape: resample the ElevationProfile to 64 points at equal steps of the cumulative-distance fraction 0→1; subtract the minimum and divide by max(range, 50 m), so height is in [0, 1] and nearly flat hikes stay flat. Profile similarity = 1 − mean |Δ|, taken as the better of the candidate forward and reversed. Banded DTW (Sakoe–Chiba band 10% of 64 points; cost = mean |Δ| along the warping path, so 1 − cost is in [0, 1]) is computed alongside, also forward/reversed. The notebook compares both on the verification pairs and keeps DTW only if it separates them better; the choice is written in the notebook.

**Starting constants** (05a prints each measure's raw distribution in the region and may adjust a constant, stating why)

| Facet | Measure | Kind | Constant |
| --- | --- | --- | --- |
| Effort | length | ratio | floor 1 km |
| Effort | elevation gain | ratio | floor 100 m |
| Effort | Difficulty rank (SAC 1–6) | tolerance | D = 3 |
| Altitude | highest point | tolerance | D = 1000 m |
| Altitude | lowest point | tolerance | D = 1000 m |
| Profile | ProfileShape | 1 − mean abs diff (or DTW) | 64 points, range floor 50 m, band 10% |
| Landscape | LandCover mix (6 shares) | mix | — |
| Landscape | Peak summited | presence | — |
| Landscape | Viewpoint along | presence | — |
| Landscape | Peaks around | ratio | floor 1 |
| Weather | temperature | tolerance | D = 10 °C |
| Weather | precipitation | ratio | floor 10 mm |
| Weather | solar radiation | tolerance | D from the region's spread |
| Weather | SolarAspect southness | tolerance | D = 1 |
| Water | water count, capped at 3 | tolerance | D = 3 |
| Water | DryStretch | ratio | floor 1 km |
| Facilities | five flags | presence | — |
| Trail | SurfaceMix | mix | — |
| Trail | TarmacApproach | tolerance | D = 2 km |

**Extraction rules**

- Corridor: an amenity point is along the DayHike when it lies within 100 m of DayHikeGeometry. Distance is measured to the line, not only to vertices (vertex spacing reaches 120 m), for example by densifying the vertex chain before a haversine BallTree query. Peak summited: a Peak within 50 m. Peaks around: Peaks within 2 km. At the Trailhead: within 500 m of the Catalog Trailhead. Production AmenityFacts (20 km Trailhead disk) are not used.
- Water points are GeometryStore amenities of kind `drinking_water`. Natural springs are only present when also tagged as drinking water; the notebook states this gap.
- DryStretch: project each Corridor water point to its position along the DayHike; stretches run from the first vertex to the first water point, between water points, and from the last water point to the last vertex; DryStretch is the longest. No water: DryStretch = length.
- SurfaceMix classes from member ways in the chain (`in_chain = 1`), weighted by way length:
  - paved: asphalt, concrete (and concrete:*), paving_stones, sett, cobblestone, paved
  - gravel: compacted, gravel, fine_gravel, unpaved, pebblestone
  - natural: ground, dirt, earth, grass, mud, sand, woodchips
  - rock: rock, bare_rock, stone
  - no or unlisted surface: road highways (motorway down to residential, service, living_street, pedestrian, cycleway, unclassified) → paved; track by tracktype (grade1 → paved, grade2–3 → gravel, grade4–5 → natural, none → gravel); path, footway, steps, bridleway → natural; via_ferrata → rock; anything else is left out of the denominator.
- TarmacApproach: start at the chain end nearest the Trailhead and sum the length of consecutive paved ways until the first non-paved way. An all-paved DayHike has TarmacApproach equal to its length.
- SolarAspect southness: sample FABDEM at a subset of vertices plus small east and north offsets, take the downslope direction, and score 1 for due south, 0 for due north, 0.5 for east or west. Points with slope below a few degrees are left out; a DayHike with no sloped points has missing southness. The notebook-04 prototype's formula, `(1 + cos(aspect − π)) / 2` on the downslope angle, scores west-facing slopes 1 and south-facing 0.5. Do not copy it; test the new formula on a synthetic south-facing ramp. Without a covering DEM tile (fixture, pytest), southness is missing.
- ClimateNormal: the regime comes from Catalog `feature_vectors`; months come from `climate_months`. 05a has one `QUERY_MONTH` setting (None = regime).

**Notebooks**

- `notebooks/05_similarity_facts.ipynb`: region load, then one section per Facet that extracts its facts, plots a few distributions or examples, and explains the rule; ends by writing the facts table.
- `notebooks/05a_similarity.ipynb`: load facts, measure distributions and constants, one section per Facet with its FacetScore, verification pairs and PASS/FAIL lines; then the combined score, coverage, the two-FacetWeights ranking demo with maps, the multi-Seed max, and a verification summary table.
- Verification pairs use `osm_name` DayHikes from the region. The notebook prints machine-readable lines (`verify\t<facet>\t<check>\t<score>\tPASS|FAIL`) so results can be grepped. On the fixture, only the synthetic checks (identity, symmetry, reversed) run, and they must PASS.
- The README's notebook walkthrough lists both new notebooks (the walkthrough test requires each listed notebook to be mentioned).

## Testing Decisions

- Good tests check external behaviour at a public seam: given inputs, the similarity or fact that comes out. They do not check private helpers or intermediate arrays.
- Seam 1, pure measures (`dayhike.similarity`): unit tests for each kind and for combining. Identity gives 1; results are symmetric and stay in [0, 1]; ratio floors behave as stated; a missing input gives None; FacetScore ignores missing measures; the combined score renormalizes over scored Facets, ignores zero weights and reports coverage; the multi-Seed score is the maximum; ProfileShape of a reversed profile scores 1; a flat profile is not stretched; banded DTW never scores below the lock-step comparison on the same pair.
- Seam 2, extraction helpers: tests on a GeometryStore built from the committed OSM fixture (it has a Hut, cafe, parking, drinking water, Shelter, Peak and Viewpoint) and, where the fixture lacks a case (paved ways with surface tags, a road approach, tracktype grades), a small new OSM fixture or in-memory rows. Cover: Corridor in/out at the 100 m edge, including a point near the middle of a long segment; DryStretch with zero, one and several water points; SurfaceMix tag and highway fallbacks; TarmacApproach from either chain end; southness on synthetic north- and south-facing ramp rasters.
- Seam 3, notebooks: the existing notebook-execution test runs every notebook on the fixture and asserts the numbered walkthrough list. Add both new notebooks to that list; their synthetic checks print PASS on the fixture.
- Real-region verification (named pairs, ranking demo) is notebook output only; `data/` is not in CI.
- Prior art: `test_geometry_store` (fixture-built GeometryStore), `test_amenity_facts` and `test_trailhead_and_amenities` (amenity distances), `test_dem` and `test_landcover` (tiny synthetic GeoTIFFs), `test_rank_dayhikes` (ranking behaviour), `test_demonstration_notebooks` (notebook execution and walkthrough list).

## Out of Scope

- Changing production Ranking, the CLI, ingest, the Catalog schema or the scaler.
- New OSM kinds: shops, toilets, natural springs (needs an ingest filter change and a re-ingest).
- Reviews or ratings.
- Running outside the region or for the whole of Europe, and any performance work beyond what the region needs.
- DriveTime (still a filter after Ranking).
- Fixing the notebook-04 SolarAspect prototype.

## Further Notes

- After approval, the next phase promotes the facts to the Catalog (Ranking cannot read GeometryStore, ADR-0006), optimizes extraction and scoring for the whole of Europe, and accepts ADR-0011.
- DTW cost is not the blocker: on 64 points with a 10% band it is about 800 cells per pair; a Seed against the region is milliseconds and against 155k DayHikes a few seconds when vectorized.

## Amendments

- 2026-10-21, WaterSources. After review the springs gap was closed, which supersedes user story 37, the Water extraction rule above and "natural springs" under Out of Scope. Ingest keeps `natural=spring`, `man_made=water_tap` and `amenity=water_point` (not when tagged `drinking_water=no`; `amenity=drinking_water` keeps priority, so the Catalog AmenityFacts did not change), Europe was re-ingested and re-sampled, and the Water Facet counts all four WaterSource kinds (`WATER_SOURCE_KINDS`). Region DayHikes with no WaterSource fell from 71% to 60%. Two 05a Water verification pairs were re-picked because their dry hikes now pass springs. `tests/test_real_data.py` (opt-in) checks the re-ingest against the files from before it. Glossary: WaterSource.
