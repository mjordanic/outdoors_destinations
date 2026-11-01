---
status: accepted (amends ADR-0016)
---

# A basic set of Measures, the rest behind Advanced search

The weights panel listed all 27 Measures in nine groups. Hikers met solar radiation, sun-facing slopes,
dry stretch and tarmac approach before they had picked a hike, and most of those Measures say little to
someone who is not a hiking-data enthusiast. The page now shows a short **basic set** in plain words.
Every other Measure waits behind a closed **Advanced search**, in named groups. Both the liked-hike
form and the Wish form work this way.

**The basic set.** Length, Climbing (`elevation_gain`), Highest point, Landscape (`landcover`),
Typical temperature (`temperature`) and Lake or sea (`waterside`). Each of them counts by default.
Landscape is a mix, which a Wish cannot ask for (ADR-0024), so the Wish form shows the other five.
They were chosen with a Spearman check of each Measure's fact over 40,000 of the 135,588 Catalog
DayHikes, so that no two say the same thing. Every pair has |rho| ≤ 0.48, except Climbing and Highest
point at +0.71. Highest point stays despite that, because Climbing alone lets results drift 305 m in
highest point (table below). Left out on purpose:
- Lowest point, +0.93 with Highest point.
- Difficulty: 62 % of DayHikes have no SAC grade.
- Climb grade and Steepest km: +0.79 and +0.92 with Climbing.
- Dry stretch: +0.94 with Length.
- Summit: it was in the basic set at first, at weight 0. A basic Measure that weighs nothing by
  default says it does not matter, which contradicts showing it first. The evidence below gives it no
  weight (Summit at 100 does not improve matches), so it moved to Scenery.

**The advanced groups, in order.**
- effort: Difficulty, Profile shape, Climb grade, Steepest km
- altitude: Lowest point
- scenery: Summit, Viewpoint along, Peaks around
- facilities: Hut along, Restaurant or cafe along, Shelter along, Restaurant at trailhead, Parking at trailhead
- trail: Surface, Tarmac approach, Round trip
- water: Water sources, Dry stretch
- weather: Precipitation, Solar radiation, Sun-facing slopes

The order runs from how hard and how high, through what is on the way, to climate. `MEASURE_GROUPS`
holds the order and each group's plain name, with `basic` first ("Main features" in the facts table).
Scoring still knows no groups (ADR-0013).

**Only what shows counts.** A Measure behind a closed Advanced search weighs 0, in both forms. What
the Hiker types there stays, and counts again once Advanced search is open. A Wish address naming an
advanced Measure opens it.

**New defaults.** Without weights, the basic set, `length`, `elevation_gain`, `highest`, `landcover`,
`temperature` and `waterside`, weighs 100. The others are off. This replaces ADR-0016's eleven core
Measures. Under "only what shows counts", the old default would have used Measures the Hiker cannot
see. Summit is off because ADR-0016's judges found the OSM flags did not separate good matches from
poor ones, and the evidence below agrees. The CLI and the API take the same default.

**Weights are sliders.** Each Measure's weight is a slider from 0 to 100 in steps of 10, its value
shown beside it. At 0 the Measure is off: its name is struck through and it is left out, as the old
"off" tick and a weight of 0 both did. A Wish address with a weight between steps opens at the nearest step,
but a nonzero weight opens at 10 or more, so an old link never turns a Measure off.

**Evidence.** For 300 random Seeds, with Return to start on, each candidate default ranked every DayHike.
Its top 10 was compared with the top 10 of ADR-0016's judge-tuned default:
- *quality*: the mean of the ADR-0016 score over the candidate's top 10, divided by the mean over the ADR-0016 top 10.
- *|dT|, |dHighest|, |dGain|*: the mean gap between the Seed and its top 10 in temperature, highest point and climb.

| Default ranking uses | quality | \|dT\| °C | \|dHighest\| m | \|dGain\| m |
|---|---|---|---|---|
| ADR-0016 (11 Measures) | 1.000 | 0.41 | 56 | 57 |
| Length, Climbing, Landscape, Lake or sea | 0.816 | 2.29 | 305 | 31 |
| + Highest point | 0.899 | 1.90 | 34 | 39 |
| + Lowest point instead | 0.900 | 1.85 | 86 | 41 |
| + Highest + Lowest | 0.910 | 1.86 | 34 | 43 |
| + Highest, Summit and Hut along at 100 | 0.895 | 1.88 | 40 | 48 |
| + Highest + Climb grade | 0.906 | 1.90 | 41 | 32 |
| + Highest + Profile shape | 0.927 | 1.87 | 47 | 44 |
| **+ Highest + Temperature (chosen)** | **0.941** | **0.27** | 46 | 49 |

Without an altitude Measure, results drift 305 m in highest point. Highest point alone keeps the terrain
but not the climate. Temperature keeps the climate and gives the best quality of any one added Measure.
Summit and Hut along at 100 do not help.

**Considered Options**:
- Keep ADR-0016's defaults with the hidden Measures still counting. Rankings would not change, but the
  page would score with Measures the Hiker cannot see.
- Weigh only the four first chosen (Length, Climbing, Landscape, Lake or sea). Quality 0.82, and results
  drift in altitude and climate.
- Lowest point instead of Highest point. Equal quality, but less plain, and it keeps highest points 86 m apart instead of 34 m.
- Mountain hut in the basic set. It does not improve matches, and it moved to facilities to make room for Temperature.

Rejected.

**Consequences**: the default ranking changes for the page, the API and the CLI. With the new default,
a typical random pair scores about 0.41 instead of 0.44, 0.72 beats 99 random pairs in 100 instead
of 0.75, and the best match for a hike usually scores about 0.82 instead of 0.85. `SCORE_HELP` says
so. Golden rankings name their weights explicitly, so they are unaffected. The weather Measure the
basic set shows is labelled "Typical temperature". It is still the June-August mean, or the chosen
month's.
