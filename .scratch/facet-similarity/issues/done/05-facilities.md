# 05: Facilities Facet

**What to build:** The Hiker compares what a DayHike offers along the way and
at the Trailhead: a Hut, a restaurant or cafe, or a Shelter in the Corridor,
and a restaurant or cafe or parking within 500 m of the Trailhead. Two hikes
that both lack a Hut agree on that point.

**Status:** done

## Blocked by

- 03: Landscape Facet and the Corridor

**Complexity:** standard

**Model:** sonnet

**Effort:** high

**Prior art:** PRD "Extraction rules" (Corridor, at the Trailhead),
"Starting constants" (Facilities row); PRD stories 16, 38–42. `GLOSSARY.md`
Hut, Shelter, Trailhead, Corridor, AmenityFact (the 20 km Trailhead disk is
not used here). Ticket 03's Corridor helper. Tests: `test_trailhead_and_amenities`,
fixture GeometryStore (one Hut, cafe, parking, Shelter). Ticket 01's measure
functions and fact-helper shape.

## Acceptance criteria

- [ ] Facilities facts per DayHike: five flags (Hut along, restaurant_cafe along, Shelter along, restaurant_cafe at the Trailhead, parking at the Trailhead). Along = in the 100 m Corridor; at the Trailhead = within 500 m of the Catalog Trailhead.
- [ ] Unit tests: each flag on the fixture where its amenity sits; a point at 450 m vs 550 m from the Trailhead; a Shelter does not set the Hut flag.
- [ ] Facilities FacetScore = presence measure over the five flags.
- [ ] 05 Facilities section: share of region DayHikes with each flag and a short explanation.
- [ ] 05a Facilities section: similar and dissimilar real pairs (for example hut-to-hut vs hut-to-hut, hut hike vs no-facility hike) with the expected verdict first, a map, and `verify` lines; synthetic identity/symmetry/reversed lines.
- [ ] Both notebooks still run on the fixture and the full test suite passes.
