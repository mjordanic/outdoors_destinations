---
status: accepted (amends ADR-0016, ADR-0020)
---

# A Ranking from a Wish

A Hiker with no hike in mind had no way to ask for one: every Ranking started from a Seed, a Catalog DayHike or a ComposedHike, and compared its facts with every DayHike's. The Hiker can now type in the values they want for the Measures they care about, a Wish, and rank from it.

**A Wish is a Seed's facts typed in.** Each value stands in the fact column of its Measure (yes/no as 1 or 0); every other fact is missing. The Ranking is otherwise the same: the same Measures, Calibration, MeasureWeights, DriveTime filter, grouping into rows and result shape. A Measure the Wish has no value for scores nothing, so it weighs 0 whatever weight it is given. Without `weights` every Measure with a value weighs 100, not the core Measures of ADR-0016: those were chosen for comparing whole hikes, and most of them would score nothing. A monthly Measure's value fills every month's column, so it is read as the QueryMonth's value when one is set and as the June-August (or yearly) mean when not. With Return to start every DayHike is measured with its WayBack as before, so a wished length or gain is of the whole walk out and back.

**Only a Measure of one value takes one.** A ratio, tolerance or yes/no Measure compares one number: those 24 take a value. The ProfileShape is a curve, and LandCover and SurfaceMix are mixes of shares; typing a guess at one would be inventing a hike, so they take none. `GET /api/measures` gives each Measure its input (number or yes/no, unit, range, step, default, or none), so the page draws the form without knowing the Measures. The ranges and defaults are fixed in `WISH_INPUTS`, around what the Europe Catalog's DayHikes have; the API takes any finite number.

**Nothing is left out as the Seed.** A Wish is no hike, so it has no Twins and walks no line: every DayHike may be listed. SuggestedLoops follow a Wish's length (and its gain, highest and lowest point where it has them) as they follow a Seed's; without a length none are searched. No loop is left out for walking the Seed again.

**Considered Options**: the default (core) weights for a Wish without `weights` (most score nothing; rejected); input ranges from the Catalog's own spread (they move with every rebuild and region; rejected for fixed ones); a Wish value for LandCover or SurfaceMix as the share of one class (a mix is several values; left for later).
