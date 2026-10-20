# 06: Trail Facet (SurfaceMix and TarmacApproach)

**What to build:** The Hiker compares what they walk on: the SurfaceMix
(paved, gravel, natural, rock, by length) and the TarmacApproach, how far they
walk on paved surface from the Trailhead before the DayHike leaves it. Ways
without a surface tag are classified from their highway kind, so the 40% of
untagged ways still count.

**Status:** done

## Blocked by

- 01: Facet scoring core with Effort and Altitude, end to end

**Complexity:** standard

**Model:** sonnet

**Effort:** high

**Prior art:** PRD "Extraction rules" (SurfaceMix classes and fallbacks,
TarmacApproach), "Starting constants" (Trail rows); PRD stories 43–45.
`CONTEXT.md` SurfaceMix, TarmacApproach, Trailhead. Notebook 04a's way-tag
counts and length-weighted trail character (`way_frame`). Fixture OSM files
under the tests fixtures folder (the main fixture has only `highway=path`
ways, so a small new fixture is needed). Tests: `test_geometry_store`,
`test_pbf_ingest`. Ticket 01's measure functions and fact-helper shape.

## Acceptance criteria

- [ ] A surface classifier maps a member way's tags to paved, gravel, natural, rock, or unknown, following the PRD lists and the highway/tracktype fallback; unknown ways are left out of the denominator.
- [ ] SurfaceMix facts per DayHike: four length fractions over in-chain member ways; missing when no in-chain way is classified.
- [ ] TarmacApproach per DayHike: from the chain end nearest the Catalog Trailhead, the length of consecutive paved ways until the first non-paved way; an all-paved DayHike gives its length.
- [ ] Unit tests: each surface list and each highway/tracktype fallback; an unlisted surface falls back to highway; SurfaceMix sums to 1; TarmacApproach from either chain end, zero when the first way is unpaved, full length when all paved. A small new OSM fixture (or in-memory way rows) provides asphalt, track with grades, path and road ways.
- [ ] Trail FacetScore = mean of SurfaceMix mix and TarmacApproach tolerance (D = 2 km).
- [ ] 05 Trail section: how many ways were classified by tag vs by fallback, the region's SurfaceMix and TarmacApproach distributions, and one DayHike's ways coloured by class.
- [ ] 05a Trail section: constants, similar and dissimilar real pairs (for example forest-path vs forest-path, forest-path vs asphalt promenade) with the expected verdict first, a map, and `verify` lines; synthetic identity/symmetry/reversed lines.
- [ ] Both notebooks still run on the fixture and the full test suite passes.
