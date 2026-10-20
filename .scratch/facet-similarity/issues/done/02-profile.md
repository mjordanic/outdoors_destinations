# 02: Profile Facet (ProfileShape, lock-step vs DTW)

**What to build:** The Hiker compares the form of the elevation plot,
independent of how high a DayHike goes or how much it climbs. Notebook 05
turns each DayHike's ElevationProfile into a ProfileShape and plots a few
contrasting shapes. Notebook 05a scores Profile with a plain point-by-point
comparison and with banded dynamic time warping side by side, both taking the
better of the candidate walked forward or reversed, verifies the Facet on real
pairs, and records which method the design keeps and why.

**Status:** done

## Blocked by

- 01: Facet scoring core with Effort and Altitude, end to end

**Complexity:** high

**Model:** opus

**Effort:** high

**Prior art:** PRD "Measure kinds" (ProfileShape bullet), "Starting
constants" (Profile row), "Further Notes" (DTW cost); PRD stories 23–26.
`CONTEXT.md` ElevationProfile, ProfileShape. Notebook 04a's elevation-profile
section (metres of change from the first point, mean absolute difference
between two profiles). Ticket 01's measure functions and fact-helper shape.

## Acceptance criteria

- [ ] A ProfileShape function resamples an ElevationProfile to 64 points at equal steps of the cumulative-distance fraction 0→1, subtracts the minimum and divides by max(range, 50 m). Fewer than two vertices with elevation gives a missing ProfileShape.
- [ ] Lock-step Profile similarity = 1 − mean |Δ|, the better of forward and reversed. Banded DTW (Sakoe–Chiba band 10% of the points; cost = mean |Δ| along the warping path) gives 1 − cost, also forward/reversed.
- [ ] Unit tests: identity 1; a reversed profile scores 1; symmetry; bounds; a nearly flat profile (a few metres of noise) is not stretched and stays close to a flat one; DTW never scores below lock-step on the same pair; a one-climb shape scores higher against another one-climb shape than against a many-climbs shape.
- [ ] 05 Profile section: cumulative distance along vertices (haversine), ProfileShape per DayHike stored in the facts table, and a figure of a few contrasting shapes (single summit, traverse, rolling, flat).
- [ ] 05a Profile section: both methods computed on the verification pairs and shown in one table; similar and dissimilar real pairs with the expected verdict written first, their profiles plotted, and `verify` lines; synthetic identity/symmetry/reversed lines.
- [ ] A markdown cell states which method the design keeps (DTW only if it separates the pairs better) and the FacetScore uses it.
- [ ] Both notebooks still run on the fixture and the full test suite passes.
