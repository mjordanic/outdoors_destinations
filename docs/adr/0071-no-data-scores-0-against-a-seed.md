---
status: accepted (amends ADR-0013; extends ADR-0043 and ADR-0052 to a Seed hike)
---

# A hike with no data for a Measure the Seed has scores 0 on it

ADR-0052 scores a DayHike 0 on a Measure a Wish asks a value of and the hike has no data for, and kept a Ranking from a Seed hike as it was: a Measure either hike lacks dropped out of the pair's mean (ADR-0013). Visitors then found the same bug from a Seed. On the combined Europe and US build, 230 DayHikes (all OSM, on Svalbard and in Georgia) have no elevation, and three of them no land cover or climate either. With the default weights such a hike was compared on Length alone, or Length and Lake or sea, and ranked first for hikes it is not like: "Nordenskiöldfjellet from Longyearbyen" was #1 for Triglav at 0.967 with most of its cells "–", and other Svalbard hikes ranked high for Whitney and Skyline. Testers asked to drop such hikes from the results or to mark them "not comparable".

Now a DayHike with no data for a Measure the Seed has data for scores a raw 0 on it, as far apart as values go: a fact column NaN, such as no climb, no land cover or no climate there, or no ProfileShape. A Wish has data for the Measures it asks a value of, so this is one rule for both, and ADR-0043's missing Difficulty grade is a case of it. The hike stays in the list, below the hikes that have the data and are otherwise as alike. Its score explanation says "No data for this hike: counts as 0 against the hike you like". A Measure the Seed has no data for still drops out, so a Ranking from a hike on Svalbard compares it on what it has.

**What it gives on the combined build** (default weights, with the way back):

| | Before | After |
|---|---|---|
| Nordenskiöldfjellet for the Triglav Seed (931036949605038) | #1, 0.967 | #775,038 of 887,346, 0.161 |
| Hikes with no elevation in the top 10s of 30 random measured Seeds | 9 | 0 |
| Top-10 slots kept, those 30 Seeds | | 97 % (the 9 above replaced) |
| Ranking time, median of those 30 Seeds | 1,084 ms | 1,109 ms |

**Considered Options**:
- *Leave a hike out below a minimum number of measured Measures.* It needs a threshold: a hike just above it still outranks hikes with all the data on the Measures it has, and one below it disappears though it is a real hike, only unmeasured (ADR-0052 rejected the same).
- *Mark the row "not comparable".* The hike still ranks first; the mark only says it should not.
- *List every hike with the data before every hike without.* The order would no longer follow the score, and the draw would need its own rule (ADR-0043, ADR-0052).

Rejected.

**Consequences**: `ranked_scores` scores 0 wherever `unmeasured` (which replaces `wish_unmeasured`) finds the Seed has data and the DayHike has none. A Ranking from a Seed with all the data its candidates have is unchanged. The Calibration's random pairs still leave missing data out, so the stored export needs no rebuild. The "–" climb on such a hike is the missing elevation itself, an ingest gap this does not fill.
