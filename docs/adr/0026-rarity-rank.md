---
status: accepted (supersedes ADR-0023; amends ADR-0013, ADR-0016, ADR-0021, ADR-0022)
---

# Rarity rank: a score is the share of random pairs less alike

The Calibration ranked a raw score among random pairs: the share scoring lower, ties counting half (ADR-0013), and since ADR-0023 every equal pair scored 1.0. That is already close to an information quantity. It is Goodall's (1966) probabilistic similarity, one minus the chance that a random pair is at least as alike, and −log₂(1 − s) is the surprise of the match in bits. But it could not tell sharing a rare value from sharing a common one. After ADR-0023 two hikes that both pass a mountain hut, and two that both pass none, both scored 1.0. The captain asked for a score that weighs how rare a match is, and that says what a low number means on two hikes that look alike.

**The rule.** For each Measure, a pair's score is the share of the stored random pairs compared on that Measure that are strictly less alike than this pair. A random pair is at least as alike when it scores a higher raw score, or the same raw score below 1, or a raw 1 on a value at least as rare. Ties never count half, so every number reads as "only 1 − s of random hike pairs are this alike": 0.9 is 1 pair in 10, 0.5 an ordinary match, 0 nothing in common.

- **Closeness comes first.** The raw score, the Measure's constants, its floor, cap and dead band are as before.
- **Among equal pairs, the rarer shared value is the closer match** (Goodall's rule). Rarity is the share of the Catalog's DayHikes whose value lies between the two values, both included, after the floor, the cap, and putting a WaterSide share under the dead band to 0 (`measure_rarity`). For a yes/no flag it is the share of DayHikes with that value. `mix` and `profile` have no single value and almost no ties, so their equal pairs are never rarer than another.
- **A shared absence on an OSM flag is not compared** (Gower's 1971 rule for presence/absence characters). For `peak_summited`, `viewpoint_along`, `hut_along`, `restaurant_cafe_along`, `shelter_along`, `restaurant_cafe_trailhead` and `parking_trailhead` (`Measure.presence`), a pair where neither has the feature mapped drops that Measure, and the other weights renormalize, as a missing fact does. "Neither has a hut" may only mean nobody mapped one. The random pairs of these Measures are the ones where either has one. `round_trip` stays symmetric: both values are real geometry.
- **WaterSide's dead band decides what counts as one value.** Every share under 0.15 is the same value for rarity, so two dry hikes and a dry hike against one that touches water for 0.1 of its walk score alike.
- **A Wish's "no" is compared.** A Wish is no map: a yes/no it gives is the Hiker's answer (`OutsideSeed.stated`). A DayHike without the feature matches a "no" and is scored like any compared pair, and one with it scores 0. Without this a Wish of only "no hut" would list only the hikes with a hut, each at 0, since nothing would be compared on the others.

The weights, the weighted mean over the Measures compared, and the 100,000 random pairs (seed 20261024, one way and with the way back) stay. Adding surprisal across Measures instead of averaging was rejected: it kept 30 % of the default top 10, as one very rare match outweighs everything else.

**What it gives on the Europe Catalog** (from the scout report's re-fit):

- Two Triglav routes that both pass a hut score 0.95 on it, both reaching a summit 0.92. A hut mismatch scores 0. Two lowland walks with no hut are not compared on it.
- Two dry hikes score 0.16 on WaterSide: 84 % of random pairs are at least as alike there. The Kattegat coast walks, 56 % and 30 % beside water, keep 0.10 on it, and 0.85 overall.
- Highest points 400 m or more apart score 0 (0.30 before): no random pair is less alike there.
- Default weights keep 95 % of the top-10 slots. For waterside Seeds the share of the top 10 really beside water (share ≥ 0.15) rises from 79 % to 95 %.
- The numbers fall: a typical random pair scores about 0.44 (0.53), the best result per Seed about 0.85 (0.93), and with the default weights 0.75 beats 99 random pairs in 100. ADR-0016's "0.5 is about a typical pair" now reads "about 0.44".

**Storage.** Per mode the Calibration keeps, beside the 1,024 raw quantiles per key, 1,024 quantiles of the rarity of the random pairs at raw 1 (`rarity.npy`) and 1,024 quantiles of every fact column over the DayHikes (`values.npy`), which `measure_rarity` reads. At raw 1 the score is the share scoring lower plus the share at raw 1 whose value is commoner. The facts stay; the calibration version carries the new rule, so a stored Calibration is refused until `dayhike build-similarity --calibrate-only` is run again, followed by a serving export.

**The compare notes** say the number in the same words:

| Case | Note |
|---|---|
| Neither has an OSM flag's feature mapped | "Neither has one mapped: not compared" |
| Identical, or counted as the same | "Identical; P% of random hike pairs are this alike", or "Counted as the same (within the dead band); P% …", with P = 100 − N; "fewer than 1%" below 1, and ", so it counts little" from 50 |
| A real difference | "More alike than N% of random hike pairs" |
| Score 0 with a tied block | "Nothing in common on this measure, like T% of random hike pairs" |
| Score 0 otherwise | "Less alike than almost all random hike pairs" |

On the seven OSM flags the pairs are "random hike pairs where either has one". "(ties count half)" is gone (ADR-0021, ADR-0022).

**Decisions** (captain, all option A of the scout report):

1. Rarity rank, strict, over ties counting half (mid-p: both no hut 0.58 though 83 % of random pairs are as alike; it cannot be combined with not comparing shared absences, as a hut mismatch would score 0.48) and over keeping equal values at 1.0 (ADR-0023).
2. A shared absence on the seven OSM flags is not compared, over scoring it by rarity (both no parking 0.92 above both having parking 0.41, and a no-hut Seed with huts weighted at 100 let 7 % hut hikes into its top 10).
3. WaterSide by rarity with the dead band as one value, over not comparing two dry hikes (waterside Seeds' top 10 drop to 68 % really waterside), a relative band, or ordering by the share of hikes between the two values (wet top 10s within 0.25 of the Seed fall from 98 % to 64 %).

**Considered Options**: ordering closeness by how many hikes lie between the two values (Lin 1998, mass-based dissimilarity: two alpine walks 500 m apart on highest point score 0.90, against the judges' 400 m tolerance of ADR-0016; default top 10s keep 62 %); the combinations above. Rejected.
