---
status: accepted (amends ADR-0021 and ADR-0026, decision 3; reverses that decision's rejection of not comparing two dry hikes)
---

# Two dry hikes are not compared on Lake or sea

Under ADR-0026, Lake or sea (WaterSide) ranked every random pair, and 80 % of random pairs scored raw 1, mostly two hikes that are never near water. Its calibration curve sat at 0.2 and then jumped to 1 at raw 1. No other Measure's curve looks like that. Two dry hikes scored a calibrated 0.21 (only the 21 % of pairs that differ were less alike), and two hikes both 30 % beside water scored 1.00. Most hikes are dry, so most Seeds and their best matches were dry. With the default weights a dry pair could not score above (5 + 0.21) / 6 ≈ 0.87. In the score distribution, dry Seeds' best matches piled up just under that ceiling, around 0.84, apart from wet Seeds' around 0.96. The captain asked for a curve shaped like the others, no cap at 0.21, and no spike.

**The rule.** Lake or sea becomes a presence Measure, as the seven OSM flags are (ADR-0026, Gower's 1971 rule for presence and absence):

- **A share under the dead band is dry, the same as none.** Before the gap is taken, a share under 0.15 is put to 0, so the raw score now says what the page already did: "Any share under 15 % counts as dry, the same as none". Before this, 0.12 against 0.20 was a gap under the band and scored raw 1. Now it is 0 against 0.20, raw 0.6. The dead band on the gap stays: 0.30 against 0.40 is raw 1.
- **Two dry hikes are not compared.** That most hikes are dry says nothing about these two. The Measure drops out of the pair's mean, and the cell shows a dash with "Both dry (less than 15 % of the walk beside water): not compared".
- **The random pairs are the ones where either hike is beside water**, 21.5 % of the 100,000. Calibration is unchanged: the share of those pairs strictly less alike, with the rarer shared value counting as the closer match among ties. Of the compared pairs 25 % score raw 0 and 2.7 % raw 1, and the curve rises between them as Length's or Climbing's does.
- **A Wish of a dry share is compared**, as a Wish's "no hut" is. A Wish is the Hiker's answer, not a missing value, so a dry hike matching it is scored against every random pair (`waterside+every-pair`) and gets about 0.21. Any other Wish share is scored like a hike's.

**What it gives on the Europe Catalog** (the rebuild-4 export re-fitted with `--calibrate-only`, with the way back, default weights):

| | Before | After |
|---|---|---|
| Random pairs at raw 1 / raw 0 | 80 % / 5 % | 2.7 % / 25 % |
| Two dry hikes | 0.21 | not compared |
| Both 30 % beside water | 1.00 | 1.00 |
| 0.5 and 0.55 | 1.00 | 0.99 |
| 0.3 and 0.5 | 0.16 | 0.78 |
| 0.2 and dry | 0.16 | 0.78 |
| 0.4 and dry | 0.07 | 0.35 |
| Kattegatstien and Kattegatkysten (0.56, 0.30) | 0.13 on it, 0.75 overall | 0.62, 0.83 |
| A typical random pair (mean of 30,000) | 0.43 | 0.47 |
| Beats 99 random pairs in 100 | 0.75 | 0.85 |
| Best match of 300 Seeds, median (dry Seeds / wet Seeds) | 0.84 (0.84 / 0.96) | 0.97 (0.97 / 0.96) |
| Default top-10 slots kept, 40 random Seeds | | 97.75 % |
| Top 10 really beside water (≥ 0.15), 100 wet Seeds | 99.6 % | 82.6 % |

Dolič–Triglav keeps its whole top 10 in the same order, Ferata dla Crusc first, now at 0.956 instead of 0.831. Kattegatstien keeps 9 of its 10, all beside water. Every other Measure's stored Calibration is byte for byte the same, and a test pins every other Measure's scores (`test_lake_or_sea_changes_no_other_measure`).

**The cost.** For a waterside Seed, a dry hike is now ranked among pairs where either is beside water. Most such pairs are a dry hike against a wetter one, so a dry hike 0.2 away is closer than three in four of them and scores 0.78 instead of 0.16. Waterside Seeds' top 10s are 82.6 % beside water instead of 99.6 %. ADR-0026 rejected this option for that reason (68 % then). Most of the dry hikes that get in are near Seeds that are barely beside water themselves, a third of the waterside hikes: for Seeds at 0.15 to 0.25, 57 % of the top 10 is beside water; for Seeds at 0.25 to 0.4, 97 %; from 0.4, 100 %. Putting a share under the band to 0 before the gap won back 8 points over not doing it (80 % to 88 % on another sample of 100 wet Seeds). Before that, a hike at 0.12 tied a Seed at 0.20 at raw 1. A Hiker who cares about water can raise Lake or sea's weight.

The best match of a Seed is still near the top of the scale, since the best of 258,305 hikes on five Measures usually matches them all well. The spike is now one group around 0.96 for dry and wet Seeds alike, not dry Seeds piled under a ceiling.

**Considered Options** (all with the dead band, measured on the same data):

- *Keep ADR-0026* (dry pairs 0.21): the curve and the ceiling stay.
- *Not compared, random pairs still all pairs*: dry pairs drop out, but a raw score below 1 still maps to 0 to 0.21, so the curve keeps its plateau.
- *Two dry hikes compared at raw 1 but ranked among pairs where either is beside water*: two dry hikes score 0.97, above most matches on water, and a typical pair rises to 0.53.
- *A dry hike against one beside water scores 0, like a hut mismatch*: wet Seeds' top 10s 87 % beside water, but 91 % of compared pairs score raw 0, a new jump in the curve.
- *Rank waterside pairs among pairs where both are beside water only, a mismatch 0*: top 10s 97 % beside water, but 44 % of those pairs tie at raw 1 (most waterside hikes are 0.15 to 0.3), and only 1,343 random pairs fit it.
- *Not compared, without putting a share under the band to 0*: as chosen, but top 10s 80 % beside water on the lab sample (88 % chosen).

Rejected.

**Storage.** The calibration version changes, and there is a new Calibration row, `waterside+every-pair`, so a stored Calibration is refused until `dayhike build-similarity --calibrate-only` (about 5 s; the facts stay) and then a serving export are run. The help texts' example numbers (a typical pair 0.47, 0.85 beating 99 in 100, a best match about 0.97, two hikes with no water source 0.65) were measured on that re-fit. The Kattegat example, which no longer scores low on Lake or sea, gives way to Ruta Teresiana and stage 8 of the Via Normanna: 0.15 on Length, 0.74 overall.
