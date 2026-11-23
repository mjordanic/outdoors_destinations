---
status: accepted (amends ADR-0012 Round 2, ADR-0017, ADR-0039 and ADR-0040; DayStages walk their tour's way since ADR-0072)
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
3. else, for a relation without both `from` and `to` tags, at the end its dashed name ("Dolič - Triglav") names (ADR-0017); then at the end with the nearer parking within 5 km, then at its member-order end (ADR-0012 Round 2).

So a height difference over 100 m now outranks the dashed name, the nearer parking and member order. Dashed names
are weaker than tags: "Gaflei - Planken" often lists two places, not a direction.

**Stable across rebuilds.** A line whose ends differ by about 100 m flips when another DEM or a newer tile moves its
heights by a few metres. The Copernicus against FABDEM comparison reversed 46 of 19,550 hikes (0.24 %), among them every
stage of "Jakobova pot – Prekmursko-Štajerska veja", with nothing but the elevation source changed. A wider threshold or
a band where height does not decide only moves the line: a hike near its edge flips just the same. Any rule that reads
this build's heights alone has an edge. So `dayhike ingest --previous CATALOG` names the build before (its GeometryStore
beside it; `previous_starts`), and a line whose end difference lies within **20 m** of the threshold
(`LOWER_START_BAND_M`: 80 to 120 m) starts where that build started it. The line is found by its two end points, so
relation lines, stages and NetworkHikes all keep their start, and a line the earlier build did not hold, or held at two
different starts, is decided as before. Beyond the band height decides again, so a line flips only when its difference
crosses the whole 40 m. Without `--previous` nothing changes: 100 m decides, as above.

*Slovenia* (one extract, 2,143 one-way lines): 152 (7.1 %) have ends 80 to 120 m apart. Between FABDEM and Copernicus the
end difference moves by a median 2.7 m, 90 % under 11 m, 99 % under 19 m, at most 39 m; for the 152, at most 25 m. Without
`--previous` 6 lines (0.28 %, all relations, all with differences crossing 100 m between the sources, 88 to 102 m
and the like) start at the other end. With `--previous` set to the FABDEM build, none do: all 2,143 starts equal the
FABDEM build's, and the 6 are the only lines that differ from the plain Copernicus build.

The build before is kept, not recomputed, so a rebuild from scratch (no `--previous`) can differ from a rebuild that
keeps an earlier orientation. A kept start also outranks a dashed name or a tag edit made since, for a line inside the
band; the next rebuild without `--previous` picks those up. Run the Europe rebuild with `--previous` set to the
last published Catalog.

**Reached.** An end is reached when parking or an OSM place node (city, town, village, hamlet) lies within **300 m**
of it (`END_ACCESS_M`), or a road of the line's own ways passes through it (`ACCESS_ROADS`: trunk to unclassified,
residential, service, living_street, pedestrian, road). This is what ingest already holds: the parking and place
points and the member ways' tags. WorldCover built-up land was left out: it would need the landcover tiles at ingest
and a raster read per end, and a suburb such as Borčec is reached by its streets anyway. The exception keeps a line
whose low end is a forest junction starting at its parked top: *Medvednica 15* stays at Sljeme (973 m, parking), its
low end at 610 m having no road, place or parking within 300 m.

**Heights.** Ingest reads the DEM at the two ends (`dem.PointHeights`, the cell `sample-dem` reads), from the tiles
`dayhike ingest --dem DIR` names: FABDEM, or Copernicus GLO-30 (raw cells, no canopy floor). It must read the same
source as `sample-dem`, `sample-trailgraph` and `build-similarity`: the orientation is a height-derived value too, so a
build on Copernicus runs ingest on the Copernicus directory, and `export-serving` refuses a build whose steps read
different sources (ADR-0030). Without `--dem`, or where a tile is missing, height orients no line and the old
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
it no direction); a smaller parking radius (Medvednica 5 has parking within 700 m at both ends); a band around 100 m,
say 80 to 120 m, inside which height decides nothing and the other rules apply (the edge moves to 120 m, where a hike
whose other rules disagree with its height flips just the same); a wider threshold (the same). Rejected. Not done
here: aerialway stations as access points (a lift-top descent is flipped to start in the valley unless tagged), a
"reorient" pass on an existing Catalog (a full rebuild picks the rule up), and loops' start rule.
