---
status: accepted (amends ADR-0019, ADR-0020, ADR-0021)
---

# What the page says, after two testers used it as hikers

Two testers used the page like hikers (liking a hike, reading the suggestions; selecting and designing a hike on the map). They found no wrong number, but about 30 places where the page surprised them. These are the rules that changed.

**Round trip is left out with Return to start.** With the way back every hike ends at its start, so the `round_trip` Measure agrees on every pair and scores 0.5 whatever is compared (ADR-0019). A Hiker who weighted it got noise. While ReturnToStart is on, the API sets its weight to 0 (a query weighting nothing else is refused with a message saying why), the page greys out its weight with a note, and the results and compare tables leave it out. One way, nothing changed.

**The compare note says when nothing differs.** ADR-0021's "Difference smaller than N% of random hike pairs" stays wherever the two values differ. When the two values are identical, the note says "Identical": ties with many random pairs had made "both dry" read as a middling 58%. When they differ but the Measure counts them as equal (raw score 1), it says why: "Counted as the same (within the dead band)" for WaterSide, "(both 3 or more)" for a capped value, "(both at or under the floor)" for a ratio. When the score rounds to 0, it says "Difference bigger than almost all random hike pairs" instead of "smaller than 0%". The calibrated score itself is unchanged.

**Names a hiker would steer by.** A ComposedHike's proposed name and a SuggestedLoop's name no longer come from a restaurant, cafe or car park ("Thames Path from Lockdown Bakehouse"); huts, peaks, viewpoints, shelters and water sources still name them. A SuggestedLoop with no such place within 2 km is named "Loop on" the two trails it walks longest, not by its start's coordinates: ADR-0020 rejected trail names because they may borrow a Catalog DayHike's name, but a coordinate (often in another country than the Seed) told the Hiker less. The two trails of a name are in name order, so a longer walk on the same trails keeps its name. A DayHike whose label is the synthetic `relation:{id}` is shown as "Unnamed hike (OSM relation {id})"; its stored label is unchanged.

**One length per hike.** Where a hike is measured with a way back, the page says "3.3 km one way (6.6 km with the way back)", the length as drawn and the length compared; search hits are listed one way ("one way" or "loop"), and the composer's running total says it is one way and that the way back is added when the hike is used.

**Considered Options**: dropping Round trip from the Measures (it still tells loops from lines one way); flooring the note at 1% (says a difference the score does not have); a note for ties only ("same as N% of random pairs", still reads as a difference for identical values); renaming catalog DayHikes at export (needs a rebuild). Rejected.
