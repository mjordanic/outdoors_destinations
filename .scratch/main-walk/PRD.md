# PRD: DayHikeGeometry is the main walk

Status: ready-for-agent

Vocabulary in this document is defined in `CONTEXT.md` and used in its glossary
sense: DayHike, DayHikeGeometry, Leftover, Trailhead, GeometryStore, Catalog,
DEM, ElevationProfile, ProfileShape, LandCover, ClimateNormal, DryStretch,
SurfaceMix, TarmacApproach, Hiker. Implements ADR-0012 (DayHikeGeometry is the
main walk of the member-way graph), which supersedes ADR-0009. Respects
ADR-0006 (geometry lives in GeometryStore beside the Catalog, Ranking does not
read it) and ADR-0007 (a DayHike is a `route=hiking` relation in a 1–20 km
band).

## Problem Statement

A DayHike's line is built by gluing its OSM member ways in listed order, once,
and only at the two ends of what has been glued so far. OSM member order is not
a walk, so on the full Catalog (155,844 DayHikes) the Hiker gets wrong hikes:

- About one DayHike in five has a length more than 5% off the length of its
  distinct member ways, and one in eight is more than 25% off.
- A way listed out of order is never tried again, so the rest of the route is
  lost (14,158 DayHikes, 75,851 km).
- A short side trip listed in the middle cuts off everything listed after it.
- A stray first member wins over the real route (2,747 DayHikes). Herkulesweg –
  Etappe 1 keeps 1.6 km of 24.9 km.
- A way listed several times is counted each time (11,594 DayHikes, 8,393 km
  extra). Dom Paklenica – Jurline lists one 3.9 km way four times and is stored
  as 15.5 km. A loop a-b-c-d-e-a with a side trip b-e-b listed after it becomes
  a-b-c-d-e-a-b-e-b.
- A side trip touching the end of a loop is glued on, so the loop no longer
  closes.
- The 1–20 km band checks the broken length, so 3,414 relations whose ways
  total more than 20 km are in the Catalog. A 938 km Camino de Santiago is
  stored as a 7.6 km DayHike.
- A loop starts wherever its first listed way begins. In a sample of 3,000
  loops, 22.5% start more than 300 m from parking while the loop passes within
  100 m of one.

Everything that reads the line in order inherits these errors: `length_m`,
ElevationProfile, ProfileShape, DryStretch, TarmacApproach, SurfaceMix,
LandCover, ClimateNormal and the Trailhead.

## Solution

Ingest builds each DayHike's DayHikeGeometry as its **main walk**: the route as
signed, each way at most once, starting at the Trailhead, with no jump between
ways. Member ways that are not part of it are Leftovers: they stay in
GeometryStore with a recorded reason, and their metres are reported, but they
are not in the line or the length.

The rules, from ADR-0012:

1. **Each distinct way counts once.** Repeat listings are Leftovers (`repeat`).
   A there-and-back trail stores its one-way line.
2. **Roles.** Members with role `alternative`, `alternate`, `variant`,
   `variation`, `detour`, `excursion` or `connection` are Leftovers (`role`).
   `approach`, `forward`, `backward`, empty and every other role are walked.
3. **Pieces and small gaps.** Ways that share a node are connected. A piece
   whose way end is 50 m or less from another piece's way end is joined to it,
   repeatedly, with the straight gap drawn and counted. The longest piece by
   length is the main piece. Every other piece is a Leftover (`disconnected`).
4. **Loop or line.** After its dead ends and shortcuts are set aside, a piece
   whose biggest cycle is at least 50% of its length is a loop. Otherwise it is
   a line.
5. **Loop.** Shortcuts (the shortest path between two odd junctions that lies
   on a cycle) are Leftovers (`shortcut`) until the cycle closes. Dead ends are
   Leftovers (`dead_end`), except at most one stem: a dead end whose far end is
   within 200 m of an access point (parking or a road you can drive on), or
   else the only dead end when it is 1 km or longer. With a stem, its far end
   is the Trailhead. Without one, the loop starts at its vertex nearest an
   access point, else where member order starts it. A figure-eight (all
   junctions even) stays whole.
6. **Line.** While more than two dead ends remain, the shortest is a Leftover
   (`dead_end`). Then variants (the shortest path between two odd junctions on
   a cycle) are Leftovers (`shortcut`) until the line can be walked once. The
   Trailhead is chosen between the two ends as today.
7. **Never split the piece.** A path is set aside only if the piece stays
   connected without it.
8. **Walk.** Every remaining way is walked once, from the Trailhead, following
   member order wherever there is a choice (this sets a loop's direction).
   Consecutive ways share a node or are a joined gap of 50 m or less.
9. **Band.** The 1–20 km band and the superroute rule use the main walk's
   length.

## User Stories

1. As a Hiker, I want a DayHike's length to be the length of the route I would
   walk, so that a 7 km loop is not shown as 15 km.
2. As a Hiker, I want a way listed twice in OSM to count once, so that a
   mapping mistake does not double a hike's length.
3. As a Hiker, I want a there-and-back trail stored as its one-way line, so
   that all DayHikes are measured the same way whatever the mapper did.
4. As a Hiker, I want an optional side trip to a viewpoint left out of a loop's
   length, so that the loop is compared as the loop.
5. As a Hiker, I want a shortcut across a loop left out, so that the loop is
   not counted with an extra leg.
6. As a Hiker, I want a parallel variant on part of a linear route left out, so
   that the route is counted once, end to end.
7. As a Hiker, I want a linear route to keep both of its real ends, so that a
   route to a summit is not cut short because the summit has no parking.
8. As a Hiker, I want ways that OSM lists out of order still on the route, so
   that a DayHike is not a fragment of itself.
9. As a Hiker, I want a stray way listed first not to replace the real route,
   so that a DayHike is the outing its name promises.
10. As a Hiker, I want two route pieces that almost touch (50 m or less) treated
    as one, so that a small mapping gap does not throw away half the trail.
11. As a Hiker, I want no straight line through empty space between two ways,
    so that the drawn route and its length follow real trail.
12. As a Hiker, I want a loop to start where I can park or arrive, so that the
    Trailhead, DriveTime and nearby amenities refer to a real start.
13. As a Hiker, I want a lollipop's walk-in stem kept when it starts at parking
    or a road, so that the walk from the car is part of the hike.
14. As a Hiker, I want a long single stem kept even when no parking is mapped
    there, so that a 3 km walk-in is not lost to a missing OSM tag.
15. As a Hiker, I want OSM `alternative`, `excursion` and `connection` members
    left out, so that a route's mapped variants are not walked.
16. As a Hiker, I want OSM `approach` members walked, so that the walk-in a
    mapper marked is part of the DayHike.
17. As a Hiker, I want `forward`/`backward` members walked, so that a route
    going up one path and down another keeps both.
18. As a Hiker, I want long-distance routes kept out of the Catalog, so that a
    938 km pilgrimage does not show up as a 7.6 km DayHike.
19. As a Hiker, I want the ElevationProfile to start at the Trailhead, so that
    "climbs first, then flat" means from where I start.
20. As a Hiker, I want DryStretch, TarmacApproach and SurfaceMix measured along
    the main walk, so that Leftovers do not change them.
21. As a Hiker, I want LandCover and ClimateNormal sampled on the main walk
    only, so that a variant I will not walk does not shift them.
22. As a Hiker, I want Ranking to use the corrected FeatureVectors, so that
    similar-length and similar-gain hikes really are similar.
23. As the maintainer, I want each Leftover's reason stored in GeometryStore,
    so that I can check each rule on real data.
24. As the maintainer, I want `unconnected_m` to be the exact metres of
    Leftover trail, so that I can find DayHikes that lost a lot.
25. As the maintainer, I want to see how much of each way is walked and in what
    order, so that SurfaceMix and TarmacApproach do not glue ways again on
    their own.
26. As the maintainer, I want a way that is only partly walked marked as
    walked, with its walked metres stored, so that a spur leaving mid-way does
    not drop the whole way.
27. As the maintainer, I want the same rules used when choosing where to look
    for access points, when applying the length band and when writing
    GeometryStore, so that the three never disagree.
28. As the maintainer, I want unit tests for each route shape (linear, linear
    with a spur, linear with a variant, loop, loop with a shortcut, lollipop,
    loop with a side trip, figure-eight, a stray first piece, out-of-order
    listing, repeat listing, roles, a small gap, a large gap), so that a later
    change cannot quietly break one.
29. As the maintainer, I want the a-b-c-d-e-a-b-e-b case as a test, so that the
    original complaint stays fixed.
30. As the maintainer, I want an opt-in check on the real GeometryStore that no
    step between ways is over 50 m, no way is walked twice, every length is in
    the band and loops start near an access point when one is in reach, so
    that a full ingest is verified, not assumed.
31. As the maintainer, I want the before and after audit numbers recorded, so
    that the effect of the change is known.
32. As the maintainer, I want notebook 04a to show one DayHike per Leftover
    reason and the loop/line split, so that I can look at what the rules did.
33. As the maintainer, I want the share of pieces classed as loops reported, so
    that the 50% cycle threshold can be tuned with evidence.
34. As the maintainer, I want a clear rollout order (re-ingest, then DEM,
    LandCover and climate sampling, then similarity facts), so that nothing
    downstream reads the old line.
35. As the maintainer, I want a stale ingest work file to restart rather than
    resume with members that have no roles, so that roles are never silently
    missing.
36. As a Hiker, I want no DayHike dropped because no access point is near its
    Trailhead, so that remote trails stay in the Catalog.
37. As the maintainer, I want the number of DayHikes before and after the
    change, split by drop reason (kept, length band, superroute), and the
    DayHike ids that left or joined the Catalog, so that I can see who was
    lost and why.

## Implementation Decisions

- **Member roles are kept.** Every OSM reader that builds relations keeps each
  member's role next to its type and id, including the ingest work file's
  relation members. A work file written before this change cannot resume, and
  ingest starts it again.
- **One main-walk assembly** replaces the one-pass chain wherever the chain is
  used today: choosing endpoints before the access pass, the keep/drop
  decision, building the DayHike and writing GeometryStore. Given a relation
  (members with roles), its ways, node coordinates and the access points, it
  returns the ordered vertices from the Trailhead, the Trailhead point, the
  main walk's length, a status for every member listing (walked, or Leftover
  with a reason), each way's walked metres and walk order, and the Leftover
  metres.
- **Graph.** Distinct ways (by OSM id, after role filtering) are split at
  every node that two member ways share, or that one way repeats. Joined gaps
  are extra straight edges. A dead end is the path from a degree-1 node to the
  first junction of degree 3 or more.
- **Loop or line** is decided per main piece: set aside dead ends, then
  shortcuts, until what remains is one cycle; the piece is a loop when that
  cycle is at least 50% of the piece's length.
- **Access points and the Trailhead.** The access-point set is unchanged
  (parking plus nodes of roads you can drive on), and so is the 5 km Trailhead
  radius. An access point never decides whether a DayHike is kept: the length
  band and the superroute rule stay the only drop reasons. A missing access
  point can only set a short stem aside or leave a loop's start at member
  order. The stem test uses a new, separate 200 m radius. The loop start uses the nearest access point within the
  existing 5 km Trailhead radius. Line ends keep today's Trailhead rule. Ingest
  looks for access points around points that do not depend on access points:
  the ends of every dead end in the main piece, and points along each loop no
  further apart than the access search radius. Assembly then runs with the
  access points.
- **Stored direction.** The vertices of every DayHike start at its Trailhead.
  ElevationProfile, ProfileShape and DryStretch need no change: they already
  read stored order.
- **Length band.** The band and the superroute rule read the main walk's
  length, gap joins included. The ingest report keeps its drop counters.
- **GeometryStore schema.** `ways` keeps one row per member listing and gains:
  - `leftover` TEXT: NULL when walked, else `role`, `repeat`, `disconnected`,
    `dead_end` or `shortcut`.
  - `walked_m` REAL: metres of this way on the main walk (0 for Leftovers).
  - `walk_order` INTEGER: position of the way along the walk from the
    Trailhead (NULL for Leftovers).

  `in_chain` stays and means `walked_m > 0`. `dayhikes.unconnected_m` is the
  distinct member length minus the walked length (gap joins not counted).
  `vertices` holds only walked vertices, from the Trailhead. A repeat listing
  is a Leftover even when its way is walked by an earlier listing.
- **SurfaceMix and TarmacApproach** read `walk_order` and `walked_m` instead of
  gluing in-chain ways again. TarmacApproach counts from the walk's start; for
  a loop it keeps the shorter of the two directions, as today.
- **Rollout.** Copy the current `data/catalog.sqlite` and its similarity facts
  to `data/baseline/` (gitignored, local only). The 19 GB GeometryStore is not
  copied: the disk has about 68 GB free, a re-ingest writes a new one beside its
  work file, and the before numbers are already in this PRD. Full
  re-ingest from `data/osm/europe-latest.catalog.osm.pbf`, then `sample-dem`,
  `sample-landcover` and `sample-climate`, then rebuild the similarity facts
  with notebook 05. The WaterSource re-ingest checks in the real-data tests
  that assert "unchanged against the baseline" no longer hold and are replaced
  by the main-walk checks.
- **Records.** The before/after audit numbers, the loop/line share and the
  DayHike count before and after go in
  `.scratch/main-walk/implementation_report.md` (committed). The count is kept,
  dropped for the length band and dropped as superroute, read from the ingest
  report of each run. The ids that left or joined come from comparing the
  re-ingested Catalog with `data/baseline/`. Each id that left is grouped by
  reason: over 20 km, under 1 km, or superroute. ADR-0012, the
  `CONTEXT.md` entries (DayHike, Trailhead, DayHikeGeometry, Leftover,
  ElevationProfile) and future-work section 10 are already written.

## Testing Decisions

- Good tests use only external behavior: build a Catalog from an OSM file and
  read the Catalog and GeometryStore. They do not call the assembly's helpers
  or look at graph internals.
- **Seam 1: building the Catalog from small OSM XML fixtures.** Each shape in
  user story 28, plus the a-b-c-d-e-a-b-e-b case, is a tiny `.osm` written by
  the test. Each test asserts Catalog `length_m` and Trailhead, GeometryStore
  `vertices` order (from the Trailhead), `ways.in_chain`, `ways.leftover`,
  `ways.walked_m`, `ways.walk_order` and `dayhikes.unconnected_m`. Fixtures are
  laid out at kilometre scale so each DayHike is inside the 1–20 km band;
  access points come from parking nodes and drivable highways in the fixture.
  Prior art: `test_geometry_store_skips_unconnected_member_ways` (its
  expectations change to the new columns and reasons),
  `test_a_spring_tagged_as_drinking_water_stays_drinking_water` (inline fixture
  edit), the `reversed_ways.osm` fixture and the streaming-ingest tests.
- **Seam 2: opt-in checks on the real data**, run with `DAYHIKE_REAL_DATA=1`
  like the existing real-data tests. They assert:
  - no step between consecutive ways over 50 m
  - no way walked twice in one DayHike
  - every `length_m` is in the 1–20 km band
  - a loop that passes within 100 m of a parking point starts within 300 m
    of one, for at least 98% of such loops (GeometryStore holds parking but
    not the drivable-road access nodes)
  - every Leftover has a reason
  - every walked way has a walk order
  - every DayHike in `data/baseline/` that is missing after the re-ingest has
    a main walk outside the band or is a superroute, and none is missing for
    lack of an access point

  Prior art: the real-data test module and its baseline comparisons.
- SurfaceMix and TarmacApproach tests use the new columns. Prior art:
  `test_surface_mix_is_length_fractions_over_in_chain_classified_ways`.
- The notebook demonstration test keeps passing on the fixture. Prior art:
  `test_demonstration_notebooks`.

## Out of Scope

- Ferry and bus ways counted as walked length (future-work section 10).
- Counting there-and-back trails twice, or Chinese-Postman walks.
- Dropping DayHikes because their Leftovers are large. They are kept, with
  `unconnected_m` showing how much was set aside.
- Using Leftovers anywhere in Ranking or the FeatureVector.
- Tuning the 50 m gap, 50% loop share, 200 m stem radius or 1 km stem length
  beyond the reported numbers. Retuning is a later decision.
- LandCover weighting along the line (future-work section 9).

## Further Notes

Before numbers (one-pass chain, measured on the GeometryStore of 2026-10-21
with way lengths from `europe-latest.catalog.osm.pbf`):

| Measure | Value |
| --- | --- |
| DayHikes | 155,844 |
| With any skipped way | 31,021 (19.9%) |
| `length_m` more than 5% / 25% off distinct member length | 31,824 (20.4%) / 20,712 (13.3%) |
| Recovered by retrying skipped ways | 14,158 DayHikes, 75,851 km |
| Main route on a smaller piece | 2,747 |
| Way listed and glued more than once | 11,594 DayHikes, 8,393 km |
| Relations totalling over 20 km in the Catalog | 3,414 |
| Single piece that can be walked once, all ways | 133,852 (85.9%) |
| Extra pieces 50 m or less from the main piece | 42% of 14,097 (about 17,500 km) |
| Dead ends 1 km or shorter (DayHikes with branches) | 57% of 45,368 |
| Loops starting over 300 m from parking with parking 100 m or less from the loop (3,000 sample) | 22.5% |
| Steps between vertices over 2 km | 16 DayHikes, all inside a single way (ferry, bus transfer) |
| Relations with any member role other than empty | about 3k |
| DayHikes whose longest piece is over 20 km (most that can leave the band) | 2,754 (1.8%) |

Relations dropped today as under 1 km on a broken chain are not in
GeometryStore, so how many join the Catalog is known only after the re-ingest.

The audit scripts used for these numbers were one-off. The real-data checks in
Seam 2 and the implementation report replace them.
