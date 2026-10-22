# 04: Loops: shortcuts, stems, start near access

**What to build:** The assembly separates loops from lines and handles loops.

- **Loop or line.** Set aside dead ends, then shortcuts, until one cycle
  remains. The piece is a loop when that cycle is at least 50% of the piece's
  length, and otherwise a line (ticket 03's rules).
- **Shortcuts.** In a loop, the shortest path between two odd junctions that
  lies on a cycle becomes a Leftover (`shortcut`), until the loop closes. A
  figure-eight (all junctions even) stays whole.
- **Stems.** A loop's dead ends are Leftovers (`dead_end`), except at most one
  stem:
  - a dead end whose far end is within 200 m of an access point (parking or a
    road you can drive on), nearest first; or else
  - the only dead end, when it is 1 km or longer.

  A stem's far end is the Trailhead.
- **Loop start.** Without a stem, the loop starts at its vertex nearest an
  access point within the existing 5 km Trailhead radius, else where member
  order starts it. Direction follows member order.
- **Access points.** Ingest looks for access points around the ends of every
  dead end in the main piece and around points along each loop no further
  apart than the access search radius, then runs the assembly with them.
- **Nothing dropped for access.** An access point never decides whether a
  DayHike is kept. `TRAILHEAD_ACCESS_RADIUS_M` stays 5 km.

User stories 4, 5, 12, 13, 14, 28 (loop shapes), 29, 36; ADR-0012 "Loop",
"Stems"; PRD "Access points and the Trailhead".

**Blocked by:** 03 (Main walk on the way graph: pieces, gaps, lines)

**Status:** ready-for-agent

- [ ] Fixture tests, each asserting `length_m`, Trailhead, vertex order,
      `leftover` and `unconnected_m`:
  - [ ] plain loop, with parking 50 m from a vertex that is not the first
        member's start: the walk starts at that vertex
  - [ ] loop with a shortcut (b–e): the shortcut is `shortcut`, and the
        length is the loop's
  - [ ] the a-b-c-d-e-a-b-e-b member list: the main walk is a-b-c-d-e-a once
  - [ ] loop with a 300 m side trip to a point with no access point: the side
        trip is `dead_end`
  - [ ] lollipop whose 2 km stem ends at parking: the stem is walked and its
        end is the Trailhead
  - [ ] lollipop whose 1.5 km stem has no access point and is the only dead
        end: the stem is walked
  - [ ] loop with two dead ends, neither near an access point: both are
        `dead_end`
  - [ ] figure-eight: walked whole
  - [ ] linear route with a small variant loop (cycle under 50% of the
        piece): stays a line, both ends kept
  - [ ] a remote loop with no access point within 5 km: kept as a DayHike
        and starting where member order starts it
- [ ] Access points are found near a loop vertex far from the old chain's
      first vertex (the access pass covers the new candidate points).
- [ ] Earlier fixture tests still pass.

Prior art: `trailhead_at_parking.osm`, `trailhead_at_highway.osm`, the
Trailhead and amenity tests, `test_geometry_store_skips_unconnected_member_ways`.
Glossary: Trailhead, Leftover, DayHikeGeometry.
