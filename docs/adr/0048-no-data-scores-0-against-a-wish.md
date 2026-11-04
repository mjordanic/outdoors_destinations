---
status: accepted (amends ADR-0013, ADR-0024; extends ADR-0043)
---

# A hike with no data for a wished Measure scores 0 on it

ADR-0013 drops a Measure a hike has no value for and renormalizes the rest. In a Wish Ranking (ADR-0024) that put hikes with no data first: a Wish for 10 km and 600 m of climbing listed hikes in Georgia and Armenia, which have no elevation data, at 0.95 to 0.996 on length alone, above hikes with the asked length and climb. Visitors read that as a bug. Now, against a Wish, a DayHike with no data for a Measure the Wish asks a value of scores a raw 0 on it (as far apart as values go), as ADR-0043 already does for a missing Difficulty grade. It stays in the list, below the hikes that have the data and are otherwise as alike, and its score explanation says "No data for this hike: counts as 0 against the value you asked for".

The rows are still drawn from the Ranking's pool by score, and Strict order lists the pool in score order (ADR-0047), so both follow the lower score without a rule of their own.

**Considered Options**: list every hike with the data before every hike without, whatever the score (the order would no longer follow the score, and the draw would need its own rule; ADR-0043 rejected the same); score no data as a typical random pair (a hike with no climb data and the right length would still outrank one with a slightly worse climb); leave hikes without the data out (they are not wrong hikes, only unmeasured). Rejected. A Ranking from a Seed hike is unchanged: there a Measure either hike lacks still drops out, as before (only the Wish case was reported).

**Consequences**: a Wish that asks only Measures every hike has ranks exactly as before. The Calibration's random pairs still leave missing data out, so the stored export needs no rebuild.
