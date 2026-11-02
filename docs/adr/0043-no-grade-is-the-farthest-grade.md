---
status: accepted (amends ADR-0013)
---

# A hike with no grade scores 0 on Difficulty against a graded Seed

ADR-0013 drops a Measure a hike has no value for and renormalizes the rest. For Difficulty that let an ungraded hike outrank graded ones whenever Difficulty was weighted: a Wish for T4 ranked the ungraded first stage of the Via Domitia above real T4 hikes near Montgenèvre, because it was scored on length and climbing alone. Now, in a Ranking and in the scores explained beside it, a DayHike without a Difficulty grade scores a raw 0 on Difficulty (as far apart as grades go) when the Seed or Wish has a grade; it stays in the list, below the hikes with the grade asked for that are otherwise as alike. A Seed without a grade still compares no grade, and every other missing Measure still drops out.

**Considered Options**: rank every graded hike before every ungraded one whatever the other Measures say (the order would no longer follow the score); score no grade as a typical random pair (it would still outrank a hike with the grade asked for and a slightly worse length); leave ungraded hikes out of a Difficulty search (they are often fine, just untagged in OSM). Rejected.

**Consequences**: a search that does not weight Difficulty ranks exactly as before. The Calibration's random pairs still leave a missing grade out, so the stored export needs no rebuild. An ungraded hike's Difficulty row reads 0 in a hike's score explanation, also when Difficulty is not weighted.
