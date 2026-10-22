# 05: SurfaceMix and TarmacApproach follow the walk

**What to build:** The Trail Facet's SurfaceMix and TarmacApproach read the
walk from GeometryStore `ways.walk_order` and `ways.walked_m` instead of
gluing in-chain ways again on their own.

- SurfaceMix weights each walked way by its walked metres, so a way only
  partly walked counts only its walked part.
- TarmacApproach counts paved ways from the walk's start (the Trailhead) in
  walk order. For a loop it keeps the shorter of the two directions, as today.

DryStretch and ElevationProfile need no change: they read vertex order, which
now starts at the Trailhead (ticket 03). User story 20, and story 25 for
`walk_order` and `walked_m` (ticket 03 adds the columns).

**Blocked by:** 03 (Main walk on the way graph: pieces, gaps, lines)

**Status:** ready-for-agent

- [ ] SurfaceMix and TarmacApproach no longer glue member ways themselves.
- [ ] A test with a way walked for only part of its length shows SurfaceMix
      counting only the walked metres.
- [ ] A test with Leftovers that are paved shows TarmacApproach and SurfaceMix
      unchanged by them.
- [ ] The existing SurfaceMix and TarmacApproach tests pass, with their way
      frames given `walk_order` and `walked_m`.
- [ ] Notebook 05 and 05a demonstration tests pass on the fixture.

Prior art: `test_surface_mix_is_length_fractions_over_in_chain_classified_ways`,
`test_surface_mix_is_missing_when_no_in_chain_way_is_classified`, the
TarmacApproach tests in the similarity tests, `test_demonstration_notebooks`.
Glossary: SurfaceMix, TarmacApproach, DryStretch.
