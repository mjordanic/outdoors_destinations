---
status: accepted (amends ADR-0026)
---

# A near pair meets the values counted the same under a floor by rarity

ADR-0026 ranks a pair among random pairs: a random pair is at least as alike when it scores a higher raw score, or the same raw score below 1, or a raw 1 on a value at least as rare. A ratio Measure counts every value at or under its floor as one, so all of them score raw 1 against each other. On Climbing (floor 100 m) 34 % of DayHikes climb 100 m or less, and 11.6 % of the random pairs with the way back (13.8 % one way) are two such hikes. A visitor test found the result: 166 m against 167 m and 599 m against 600 m scored 0.88, the same as two flat walks of 11 m and 65 m, because a raw 0.994 is below every raw 1. Only exactly equal climbs reached 1.0. Peaks around (floor 1), Climb grade (3 %) and Steepest km (50 m) were capped the same way at 0.83 to 0.91.

**The rule.** On a `ratio` Measure, a random pair at raw 1 counts as at least as alike as a pair below raw 1 only when its shared value is at least as rare as the stretch between that pair's two values. The rarity is ADR-0026's: the share of the Catalog's DayHikes whose value lies between the two values, after the floor. So a pair below raw 1 scores the share of random pairs that score lower, plus the share at raw 1 whose shared value is commoner than its stretch. A pair at raw 1 scores as before. Closeness still comes first among pairs below raw 1.

The ratio Measures are Length, Climbing, Peaks around, Precipitation, Dry stretch, Climb grade and Steepest km. Tolerance Measures keep ADR-0026's rule: their dead band and cap were a captain decision there (Lake or sea's Kattegat pair).

**What it gives on the Europe Catalog** (rebuild-4 export, with the way back):

- Climbing: 167 m against 166 m 0.998, 600 m against 599 m 0.999, 600 m against 570 m 0.980. Two climbs both 100 m or less stay at 0.884. 600 m against 300 m rises from 0.61 to 0.73: the climbs between 300 and 600 m are rarer than the flat walks.
- Default weights keep 99 % of the top-10 slots on 40 random Seeds (60 % on the most changed one).
- A typical random pair scores about 0.43 (0.42 before), 0.75 beats 99 random pairs in 100 (0.74), and the best match per Seed about 0.84 (0.82).

**Words.** The compare notes keep their wording ("More alike than N% of random hike pairs"). "Counted as the same" names the limit: "both 100 m or less", "less than 15 % of the walk apart", "both 3 or more". The "?" of each Measure where many DayHikes share the zone (Climbing, Peaks around, Climb grade, Steepest km, Lake or sea) says so in one sentence: values in it count as the same, so many hikes share that it says little, and two hikes that match closely on a less common value are the closer match. Length, Precipitation and Dry stretch have almost no DayHike under their floor, and Water sources' "3 or more" scores 0.96, above two hikes with one source each (0.92), so their "?" says nothing more.

**Storage.** The random pairs, their rarity and the values are as stored. The calibration version carries the new rule, so a stored Calibration is refused until `dayhike build-similarity --calibrate-only` is run again, followed by a serving export.

**Considered Options**: keeping ADR-0026 as it is (near-equal climbs stay at 0.88); the same rule for every continuous Measure, Lake or sea's dead band included (undoes the Kattegat decision; 96.75 % of default top-10 slots kept). Rejected (captain, option B of the decision request).
