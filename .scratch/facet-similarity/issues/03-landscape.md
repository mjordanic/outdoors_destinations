# 03: Landscape Facet and the Corridor

**What to build:** The Hiker compares what a DayHike looks like: its LandCover
mix (with the leftover share), whether it reaches a Peak, whether a Viewpoint
is along it, and how many Peaks are around it. This ticket introduces the
Corridor rule (an amenity point within 100 m of DayHikeGeometry, measured to
the line, not only to vertices) that the Water and Facilities Facets reuse.

**Status:** ready-for-agent

## Blocked by

- 01: Facet scoring core with Effort and Altitude, end to end

**Complexity:** standard

**Model:** sonnet

**Effort:** high

**Prior art:** PRD "Extraction rules" (Corridor, Peak summited, Peaks around),
"Starting constants" (Landscape rows); PRD stories 27–30. `CONTEXT.md`
Corridor, LandCover, Peak, Viewpoint. Notebook 04's `corridor_hut_count`
experiment (BallTree over hut points). Tests: `test_amenity_facts`,
`test_trailhead_and_amenities` (haversine BallTree distances), the fixture
GeometryStore (it has one Peak and one Viewpoint). Ticket 01's measure
functions and fact-helper shape.

## Acceptance criteria

- [ ] A Corridor helper returns, per DayHike, the amenity points of a kind within a radius of DayHikeGeometry, measured to the line (for example by densifying the vertex chain before a haversine BallTree query) so a point beside the middle of a long segment is found.
- [ ] Unit tests: a point at 90 m is in and at 110 m is out of a 100 m Corridor; a point 90 m from the midpoint of a 300 m segment is in; the fixture's Peak and Viewpoint land where the fixture geometry puts them.
- [ ] Landscape facts per DayHike: six LandCover shares (the five Catalog fractions plus the leftover share = 1 − their sum, clipped at 0; missing when the Catalog fractions are missing), Peak summited (a Peak within 50 m), Viewpoint along (a Viewpoint in the 100 m Corridor), Peaks around (Peaks within 2 km).
- [ ] Landscape FacetScore = mean of LandCover mix, Peak-summited presence, Viewpoint-along presence, and Peaks-around ratio (floor 1).
- [ ] 05 Landscape section with a short explanation, a LandCover mix figure and counts of summited/Viewpoint hikes in the region; facts added to the table.
- [ ] 05a Landscape section: distributions and constants, similar and dissimilar real pairs (for example forest vs forest, forest vs open karst or alpine) with the expected verdict first, a map, and `verify` lines; synthetic identity/symmetry/reversed lines.
- [ ] Both notebooks still run on the fixture and the full test suite passes.
