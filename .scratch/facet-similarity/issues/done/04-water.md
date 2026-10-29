# 04: Water Facet (water count and DryStretch)

**What to build:** The Hiker compares how much water a DayHike has along the
way: how many drinking-water points are in the Corridor (capped at three) and
the DryStretch, the longest distance walked without passing one. The notebook
states that natural springs are mostly missing from the GeometryStore, so
mountain hikes look drier than they are.

**Status:** done

## Blocked by

- 03: Landscape Facet and the Corridor

**Complexity:** standard

**Model:** sonnet

**Effort:** high

**Prior art:** PRD "Extraction rules" (water points, DryStretch), "Starting
constants" (Water rows); PRD stories 35–37. `GLOSSARY.md` Corridor,
DryStretch. Ticket 03's Corridor helper. Fixture GeometryStore (one
drinking-water point). Ticket 01's measure functions and fact-helper shape.

## Acceptance criteria

- [ ] Water facts per DayHike: count of `drinking_water` points in the 100 m Corridor, and DryStretch in metres (each Corridor water point projected to its position along the DayHike; stretches from the first vertex to the first point, between points, and from the last point to the last vertex; the longest wins; no water gives the DayHike's length).
- [ ] Unit tests: DryStretch with zero, one (at the start, in the middle) and several water points; two points at the same position; the count includes only Corridor points.
- [ ] Water FacetScore = mean of capped-count tolerance (cap 3, D = 3) and DryStretch ratio (floor 1 km).
- [ ] 05 Water section with the springs data-gap note, the region's water-count and DryStretch distributions, and one map of a DayHike with its water points.
- [ ] 05a Water section: constants, similar and dissimilar real pairs (for example well-watered vs well-watered, well-watered vs dry karst) with the expected verdict first, a map, and `verify` lines; synthetic identity/symmetry/reversed lines.
- [ ] Both notebooks still run on the fixture and the full test suite passes.
