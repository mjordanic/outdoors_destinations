---
status: accepted (amends ADR-0012 Round 2, ADR-0017, ADR-0039 and ADR-0040)
---

# A one-way line starts at its lower end

The captain found *Planinarska staza Medvednica 5* (OSM relation 1538330) walked the wrong way. It started on the hill
at 471 m and ended in the streets of Borčec at 276 m. Its `from`/`to` tags and its name give no direction, and both
ends have parking within 5 km, so the nearer car park chose the start: 128 m from the top against 639 m from the
bottom (ADR-0012 Round 2). Height played no part. The neighbouring Medvednica trails that start in the valley did so
by luck: their lower car park happened to be a few tens of metres nearer. In the Europe rebuild 3, 23,584 of 183,064
one-way lines (12.9 %) started more than 100 m higher than they ended (od-hike-direction report).

**The rule.** A one-way line (two odd nodes; loops and ADR-0012's loop starts are unchanged) starts:

1. at the end its OSM `from`/`to` **tags** name (ADR-0017); a tagged descent, such as a hut-to-valley Alta Via stage, keeps its direction;
2. else, when its ends differ by more than **100 m** (`LOWER_START_M`), at its **lower end**, unless nothing reaches the lower end while something reaches the upper one;
3. else at the end its dashed name ("Dolič - Triglav") names (ADR-0017), then at the end with the nearer parking within 5 km, then at its member-order end (ADR-0012 Round 2).

So a height difference over 100 m now outranks the dashed name, the nearer parking and member order. Dashed names
are weaker than tags: "Gaflei - Planken" often lists two places, not a direction.

**Reached.** An end is reached when parking or an OSM place node (city, town, village, hamlet) lies within **300 m**
of it (`END_ACCESS_M`), or a road of the line's own ways passes through it (`ACCESS_ROADS`: trunk to unclassified,
residential, service, living_street, pedestrian, road). This is what ingest already holds: the parking and place
points and the member ways' tags. WorldCover built-up land was left out: it would need the landcover tiles at ingest
and a raster read per end, and a suburb such as Borčec is reached by its streets anyway. The exception keeps a line
whose low end is a forest junction starting at its parked top: *Medvednica 15* stays at Sljeme (973 m, parking), its
low end at 610 m having no road, place or parking within 300 m.

**Heights.** Ingest reads FABDEM at the two ends (`dem.PointHeights`, the cell `sample-dem` reads), from the tiles
`dayhike ingest --dem DIR` names. Without `--dem`, or where a tile is missing, height orients no line and the old
rules decide. The heights are the same whenever a walk is assembled again, so the Catalog and the GeometryStore agree.

**DayStages and NetworkHikes** follow the same height rule on their own two ends; the tour's or the legs' `from`/`to`
tags do not settle a stage's or a chain's direction. A flipped stage keeps its number in the tour and swaps its name's
ends ("stage 2: Refuge du Col – Refuge du Lac"). A flipped NetworkHike line swaps its name ("Charlie – Alpha"; "Hike
from X" becomes "Hike to X") and may now start at its destination, not a start signpost; it then ends at the signpost
or car park it was built from (ADR-0039). The report estimated 15,589 flips on rebuild 3: 8,215 relations, 5,099
stages, 2,275 NetworkHikes.

**Climb and descent swap exactly.** A flipped hike should show its climb and descent swapped. They did not:
`climb_and_descent` dropped DEM spikes and started its 10 m dead-band from the first vertex, so the same walk counted
differently each way (DayHike 17958871: 175.8 / 262.6 m one way, 311.7 / 230.2 m the other; Medvednica 5: 45 / 232
against 239 / 52). Now each walking direction also counts the last metres it moved under 10 m, so its climb less its
descent is the end's height less the start's, and climb and descent are the means of the walk counted forward and
counted back. Walking the other way swaps them exactly, and a retraced walk (Return to start) climbs exactly what it
descends. `elevation_gain`, the larger of the two, is direction-free, so the notebook 05 "reversed" check holds.

**Considered Options**: start low whatever the tags say (it overrides 5,053 genuine OSM directions, e.g. "Vom
Gletscher zum Wein" from the Dachstein cable car); start at the end with a settlement or road first, height as a
tie-break (38,132 flips, 3,541 of them to a higher start, noisy for flat hikes); trust member order (ADR-0017 found
it no direction); a smaller parking radius (Medvednica 5 has parking within 700 m at both ends). Rejected. Not done
here: aerialway stations as access points (a lift-top descent is flipped to start in the valley unless tagged), a
"reorient" pass on an existing Catalog (a full rebuild picks the rule up), and loops' start rule.
