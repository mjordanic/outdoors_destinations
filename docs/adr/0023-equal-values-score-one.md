---
status: accepted (amends ADR-0013, ADR-0021, ADR-0022)
---

# Equal values score 1.0

The Calibration was the mid-rank of a raw score among random pairs: the share that score lower, ties counting half (ADR-0013). On a Measure where many random pairs tie at raw 1.0, two equal values could never reach 1.0. On the stored Calibration, both dry on WaterSide scored 0.58 (85 % of random pairs are equal there), no parking at either Trailhead 0.70, no water source on either 0.79, both round trips 0.68. ADR-0022 made the compare note say "Identical" for these, but the number beside it still read as a middling match, and it pulled down every pair that agreed on such a Measure.

**A raw score of 1 counts its ties in full.** When the two values are equal, or the Measure counts them as equal (inside WaterSide's dead band, both at a cap, both at or under a ratio floor, two yes/no flags that agree or are both absent), the calibrated score is the share of random pairs that score lower or the same, which is 1.0. Every other raw score keeps the mid-rank. That includes the block of random pairs at raw 0. On `highest` 59 % of random pairs are 400 m or more apart and score raw 0. Counting that block in full as well would lift a 2,800 m summit against a 600 m one from 0.30 to 0.59, above a typical pair. Disagreeing yes/no flags would double the same way (no parking against parking from 0.20 to 0.41). On the stored Calibration every real difference scores exactly as before. A typical random pair under the default weights moves from 0.50 to 0.53, mostly from WaterSide.

The compare notes are unchanged: "Identical" or "Counted as the same (…)" for a raw 1, "Difference smaller than N% of random hike pairs" (with "(ties count half)" where random pairs tie) for a real difference, "Difference bigger than almost all random hike pairs" near 0. The stored random-pair scores are the same as before, but the calibration version includes this rule. A Calibration made under the mid-rank is therefore refused until `dayhike build-similarity --calibrate-only` is run again.

**Considered Options**: the upper rank for every score, where ties always count in full (moves the raw-0 blocks as above); ties counting in full at raw 1 and not at all below it (drops far-apart `highest` pairs and disagreeing flags to 0); extra credit for sharing a rare value (not approved). Rejected.
