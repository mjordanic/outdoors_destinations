# PRD: Main walk, round 2 (lightweight fixes after the Europe rollout)

**Status:** ready-for-agent

## Problem Statement

The first Europe rollout of the main walk (ADR-0012, `.scratch/main-walk/`)
fixed most chain errors: DayHikes went from 155,844 to 161,599, length off by
more than 25% fell from 13.3% to 3.0%, and no way is walked twice. But four of
the fourteen real-data checks fail, and two problems the checks do not catch
remain:

- 1,395 line DayHikes stall: a small cycle hangs off the line by one way, the
  odd junctions cannot be cut down to two, and the walk stops early. The rest
  of the route becomes `shortcut` Leftovers (Hermannshöhen Etappe 02 walks
  4.0 of 19.8 km). 435 of them only got into the Catalog because the stall
  cut them into the 1–20 km band.
- 5,379 routes close by a gap of 50 m or less inside one piece. The assembly
  reads them as lines, so their Trailhead lands at one end, not near parking.
  They cause almost all of the loop-start failures (4.81% against a 2% limit).
- The drivable-road access scan reads only the first box of its extract
  polygon, so road access points exist in one small area of Austria. In
  practice access points are parking almost everywhere already.
- Two DayHikes left the Catalog through a stem kept near parking, and the
  check that explains why DayHikes left assembled them without access points.
- Three notebook 05/05a example pairs use DayHikes that left as over 20 km.
- The step check flags two nodes with the same coordinates as a 101 m step.

The goal is a demo-ready Catalog with light fixes: no new stored data, no new
Leftover reason, and less code where possible.

## Solution

- Access points are parking only. The drivable-road scan, its osmium extract
  and its endpoint pass are removed. Ingest needs only the hiking extract.
- Two dead-end tips of the main piece 50 m or less apart are joined with a
  gap, so a route closed by a small gap can be a loop and start near parking.
- A line that cannot be walked once because a cycle hangs off it by one way
  sets aside the smaller side of that way as a `dead_end` Leftover.
- A stem counts toward the length band. The check that explains why DayHikes
  left uses parking as access points, like ingest.
- Notebook 05/05a pairs are re-picked from DayHikes still in the Catalog.
- The step check treats two vertices with the same coordinates as one point.
- A sweep removes code, docs, tests and notebook text made stale by the main
  walk and parking-only access, or records that nothing needed removing.
- A full rollout re-ingests Europe, re-samples, rebuilds facts and runs every
  real-data check, compared against the original baseline (155,844).

## User Stories

1. As a hiker, I want a line route with a small loop hanging off it to be
   walked end to end, so that its length is not cut to a fraction.
2. As a hiker, I want a route whose start and end are a few metres apart to
   be treated as a loop, so that it starts near parking.
3. As a hiker, I want every Trailhead chosen from parking, so that I start
   where I can leave a car.
4. As a hiker, I want a line with no parking within 5 km of either end to
   start at its member-order end, so that it still has a Trailhead.
5. As a data maintainer, I want ingest to stop scanning drivable roads, so
   that it is faster and has one less source of bugs.
6. As a data maintainer, I want ingest to work from the hiking extract alone,
   so that I do not need the full Europe file for access points.
7. As a data maintainer, I want parking near any part of a walk to count, not
   only parking near precomputed endpoints, so that the access pass needs no
   endpoint pre-pass.
8. As a data maintainer, I want a cycle hanging off a line set aside with the
   existing `dead_end` reason, so that no new Leftover reason is added.
9. As a data maintainer, I want the dead-end tip join to look only at
   dead-end tips of the main piece, so that a spur tip near the line does not
   make a fake cycle.
10. As a data maintainer, I want the tip join to count its straight gap in
    the length, like the existing gap join, so that length stays the length
    walked.
11. As a data maintainer, I want no stalled line walks after the rollout, so
    that `shortcut` Leftovers are real shortcuts.
12. As a data maintainer, I want the 435 DayHikes that joined only through a
    stall to be judged on their real main walk, so that the band is honest.
13. As a data maintainer, I want a stem to count toward the band, so that the
    stored length equals the walked length.
14. As a data maintainer, I want the "why did this DayHike leave" check to use
    the same access points as ingest, so that it agrees with ingest.
15. As a data maintainer, I want the step check to pass when two consecutive
    vertices share coordinates, so that it reports only real jumps.
16. As a data maintainer, I want the loop-start check to pass under 2%, so that
    loops start near parking.
17. As a demo viewer, I want notebook 05/05a pairs to be DayHikes in the
    Catalog, so that the notebooks run.
18. As a data maintainer, I want code that only served the road access scan
    removed, so that nobody maintains dead code.
19. As a data maintainer, I want docs, README and notebooks to stop describing
    road access points, the one-pass chain or ADR-0009 as current, so that
    they match the code.
20. As a data maintainer, I want tests for removed behaviour removed and tests
    for kept behaviour kept, so that the suite stays honest.
21. As a data maintainer, I want the sweep to list what it checked and what it
    removed, even if nothing, so that I know it was done.
22. As a data maintainer, I want no new GeometryStore or Catalog columns, so
    that the data does not grow.
23. As a data maintainer, I want before/after DayHike counts against the
    original 155,844 baseline, so that I see the total effect of both rounds.
24. As a data maintainer, I want a short "Round 2" section in a report with
    counts, check results, stall count and loop share, so that it stays
    readable.
25. As a data maintainer, I want `data/baseline/` deleted after round 2 passes,
    so that disk is freed.
26. As a data maintainer, I want no DayHike to leave the Catalog for lack of
    parking, so that access never decides keep or drop.

## Implementation Decisions

**Parking-only access (ADR-0012 Round 2).**
- Access points are every parking point in the hiking snapshot (parking
  already comes from the hiking extract's amenity filter). They feed one
  access tree, built once, used for line Trailheads, the 200 m stem test and
  loop starts. The 5 km Trailhead radius is unchanged.
- Removed: the drivable-road node and way scan, the access polygon writer and
  its bounding boxes, the osmium access extract and its sidecar files, the
  endpoint pre-pass that chose endpoints before the access scan, loading
  road access points from the work file, and "parking near endpoints".
- The work file drops its access tables and access progress passes; its
  schema version is bumped so an old work file restarts.
- The ingest report, ingest signature and GeometryStore writer keep their
  outward shape where callers depend on it; any now-unused return value or
  parameter is removed with its callers.
- osmium stays for the hiking tags-filter extract.

**Dead-end tip join.** After pieces are joined and the main piece chosen, two
dead-end tips (degree-one nodes) of the main piece 50 m or less apart are
joined with a straight gap, closest pair first, each tip at most once. The gap
counts in length like other gaps. The loop/line decision then runs as before.

**Bridge-and-cycle branch.** When a line still has more than two odd nodes and
no removable shortcut exists, find a way whose removal splits the piece (a
bridge). Set aside the smaller side by length, the bridge included, as
`dead_end`, then continue the usual dead-end and shortcut steps. Repeat until
the line can be walked once. The member-order greedy fallback stays only as a
last resort and should not trigger on the real data.

**Band and stems.** Unchanged rule: the band uses the main walk's length,
stems included.

**Real-data checks.**
- The step check treats a stored step whose first vertex has the same
  coordinates as the way's node as on that way.
- The "why did this DayHike leave" helper assembles with parking as access
  points, the same as ingest.
- The loop-start check keeps its 2% limit and its loop test (first and last
  vertex within 50 m).

**Notebooks 05/05a.** Pairs are picked from DayHikes in the current Catalog,
as the WaterSource re-ingest did.

**Stale sweep.** A ticket reviews the whole codebase for parts made obsolete
by the main walk (ADR-0012) and parking-only access, at least: ingest and
catalog modules, CLI options, README ingest and access sections, docs and
ADR cross-references, future-work items now done or moot, GLOSSARY.md terms,
notebooks 04a/05/05a text, tests and fixtures, constants and helpers with no
caller left. It removes or rewrites what is stale and writes a short list of
what it checked and changed (possibly "nothing") in its commit body. It does
not refactor working code. Pre-existing dead code that these changes did not
make stale (such as the old in-memory OSM parser) is listed, not deleted.

**Rollout.**
- Keep `data/baseline/` (the 2026-10-21 Catalog, 155,844 DayHikes).
- Re-ingest Europe from the hiking extract, run the DEM, LandCover and
  climate samplers, rebuild similarity facts with notebook 05, run all
  real-data checks.
- Write `.scratch/main-walk-r2/implementation_report.md` with a short
  "Round 2" section: counts by drop reason against both the baseline and
  round 1 (161,599), check results, stalled walks, loop/line share, ingest
  time.
- Delete `data/baseline/` only when all checks pass.

## Testing Decisions

- Good tests check behaviour at the highest seam: what a Catalog and
  GeometryStore built from a small OSM fixture contain (length, Trailhead,
  vertex order, Leftover reasons, walk order), not internal helpers.
- Seam 1: `build_catalog` on small OSM XML fixtures, as in the round-1 main
  walk tests. New fixtures:
  - a line with a small cycle hanging off it by one way: walked end to end,
    the cycle and its bridge are `dead_end`
  - a route closed by a 30 m gap between two way ends of one piece: a loop
    that starts at its vertex nearest parking
  - two spur tips 30 m apart where only one is a main-piece dead end: no
    fake loop
  - a line with parking near its last member's end: Trailhead there; with no
    parking within 5 km: the member-order end
- Seam 1 also: ingest runs without any drivable-road data and leaves no access
  sidecar files. Tests for the removed road scan and access extract go.
- Seam 2: the opt-in real-data checks (`DAYHIKE_REAL_DATA=1`), updated as
  above; all fourteen must pass after the rollout.
- Prior art: the round-1 main-walk fixture tests, the streaming-ingest tests,
  the real-data test module and its baseline mark.

## Out of Scope

- Drivable roads, bus stops or other access points besides parking.
- Lollipop routes (stem plus loop) as loops in TarmacApproach or the loop
  check.
- Storing the loop/line decision or any new column.
- Ferry and bus ways in walked length.
- Speeding up the climate sampler.

## Further Notes

Round-1 figures this round should move: 1,395 stalled line walks (target 0),
5,379 gap-closed loops read as lines (most become loops), loop-start failures
4.81% (target under 2%), and four failing real-data checks (target none). Ingest
took 2 h 10 min with the road scan; it should get faster without it. The full
rollout with sampling took about 10 h last time, about 4 h of it climate.
