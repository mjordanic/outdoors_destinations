---
status: accepted (amends ADR-0016)
---

# A dead band for WaterSide, and the compare score said in words

WaterSide is zero-inflated: 83 % of DayHikes have a share of 0, so 69 % of random pairs score raw 1.0. With ties counting half, "both dry" calibrated to 0.65, and any difference, however small, fell below that whole block: a gap of 0.02 scored 0.29, Triglav (0) against Jalovec (0.107) 0.19. WaterSide was the largest drag on such pairs, the problem ADR-0016 fixed for the amenity flags.

**Dead band.** A `tolerance` Measure may have a `dead_band`: gaps under it count as equal (raw 1.0), wider gaps keep `1 - gap / D`. WaterSide's is 0.15. Half of the DayHikes that touch water at all touch it for less than 0.15 of their walk (the median of the non-zero shares is 0.149): a cove, a bridge, a lake glimpsed for about 700 m of a 5 km walk. Under 0.15 a share says "barely beside water", not how much. With the band, 84 % of random pairs tie at 1.0, so a pair within it calibrates to 0.58 and a wider gap keeps its old score (0.15 apart 0.15, 0.5 apart 0.03). Triglav to Jalovec rises from 0.748 to 0.784. The Measure is part of the Calibration version, so a stored Calibration is stale until `build-similarity --calibrate-only` re-fits it (about 5 s; the facts stay).

On the blind-judge set of ADR-0016 (170 rated pairs, scored one way on the in-place facts), agreement moved from Spearman 0.415 to 0.406 per Seed (pooled 0.458 to 0.440), and bands of 0.05 to 0.3 all land within 0.02 of no band. That is noise for 17 Seeds, and the judges' cards had no WaterSide fact, so the set cannot pick the band; the distribution does.

**The score in words.** The compare table keeps the calibrated score (higher is more alike) and adds small text under it: "Difference smaller than 52% of random hike pairs" for 0.52, the same number as the score. The share counts only random pairs that score lower, so with ties it is below the score and the text names the tied share ("Difference smaller than 16% of random hike pairs; 84% score the same"); it never claims more pairs are further apart than are. `mix` and `profile` Measures say "More alike than", yes/no flags "Alike, as are 80% of random hike pairs" or "Differ, as do 20%". The help texts no longer say "alike within D": D only shapes the raw score, which the Calibration ranks against random pairs.

**Considered Options**: leave WaterSide uncalibrated and average its raw score (the one Measure off the common scale); drop it from the core Measures (loses the only coast and lake signal); show the raw score instead of the calibrated one (the weights average the calibrated one); the "bigger than 1 - s" form of the text (the captain chose the form with the score's own number). Rejected.
