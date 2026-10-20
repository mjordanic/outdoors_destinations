# 08: All-Facet ranking demo and verification summary

**What to build:** With all eight Facets in place, the Hiker sees the whole
design work end to end: one Seed ranked against the region under two
FacetWeights sets (Facilities-heavy and Altitude-heavy) gives visibly
different top fives, each shown on a map with per-Facet scores, the combined
score and coverage. Two Seeds show the maximum rule. The notebook reports how
often each Facet is missing in the region and ends with one verification
summary table built from every `verify` line, plus a short list of the design
choices the Hiker should review (constants changed from the PRD, the Profile
method kept).

**Status:** ready-for-agent

## Blocked by

- 02: Profile Facet (ProfileShape, lock-step vs DTW)
- 03: Landscape Facet and the Corridor
- 04: Water Facet (water count and DryStretch)
- 05: Facilities Facet
- 06: Trail Facet (SurfaceMix and TarmacApproach)
- 07: Weather Facet (ClimateNormal slice and SolarAspect)

**Complexity:** standard

**Model:** opus

**Effort:** high

**Prior art:** PRD "Combining", "Notebooks"; PRD stories 8, 10, 11, 51, 52.
ADR-0011, ADR-0003 (multi-Seed max). Notebook 04's "Map the Seed and its
neighbours" cell. Notebook 03's max-not-mean demonstration. Tests:
`test_demonstration_notebooks`.

## Acceptance criteria

- [ ] 05a combined section: FacetWeights set A (Facilities 50, Altitude 30, Landscape 20, the rest 0) and set B (Altitude-heavy, stated in the notebook) for the same Seed; top 5 of each with all eight FacetScores, combined score and coverage; one map per set with the Seed highlighted. The two top fives differ, and a markdown cell explains why in terms of the weights.
- [ ] Multi-Seed cell: two dissimilar Seeds; each candidate's score equals its higher combined score against either Seed, printed so it can be checked.
- [ ] Coverage report: share of region DayHikes missing each Facet (and each measure where useful).
- [ ] Verification summary: a table built from all `verify` lines (Facet, check, score, PASS/FAIL), with a count of passes and failures, and a markdown list of failures with a one-line reading of each.
- [ ] A "for review" markdown cell lists every constant that differs from the PRD starting constants and the Profile method kept, each with its reason.
- [ ] README notebook walkthrough rows for 05 and 05a describe what each notebook now shows.
- [ ] Both notebooks still run on the fixture and the full test suite passes.
