# 03: Main walk on the way graph: pieces, gaps, lines

**What to build:** Replace the one-pass chain inside the assembly with a walk
over the graph of distinct member ways, split at every node two member ways
share (or one way repeats):

- Out-of-order ways are walked.
- Pieces whose way ends are 50 m or less apart are joined, repeatedly. The
  straight gap is drawn and counted.
- The longest piece by length is the main piece. Other pieces are Leftovers
  (`disconnected`).
- Lines: while more than two dead ends remain, the shortest becomes a Leftover
  (`dead_end`). Then the shortest path between two odd junctions that lies on
  a cycle becomes a Leftover (`shortcut`), until the line can be walked once.
- A path is set aside only if the piece stays connected.
- The walk visits each remaining way once, follows member order where it has a
  choice, and starts at the Trailhead (the existing end rule for lines). Its
  vertices are stored in that order.
- The length band and the superroute rule use the main walk's length.

Loop handling (loop/line split, stems, loop start) is ticket 04. Until then a
piece with no dead ends is walked whole, with no shortcuts dropped.

GeometryStore `ways` gains `walked_m` (metres of the way on the walk) and
`walk_order` (position along the walk, NULL for Leftovers). `in_chain` means
`walked_m > 0`, so a way only partly walked counts as walked.
`dayhikes.unconnected_m` is the distinct member length minus the walked length,
gap joins not counted.

User stories 1, 6, 7, 8, 9, 10, 11, 18, 19, 24, 25, 26, 28 (line shapes);
ADR-0012.

**Blocked by:** 02 (Member roles and each way once)

**Status:** ready-for-agent

- [ ] Fixture tests, one per shape, each asserting `length_m`, Trailhead,
      vertex order from the Trailhead, `in_chain`, `leftover`, `walked_m`,
      `walk_order` and `unconnected_m`:
  - [ ] linear in member order
  - [ ] linear listed out of order (1, 3, 2)
  - [ ] linear with a short spur listed in the middle
  - [ ] linear with a parallel variant section, keeping both real ends
  - [ ] stray piece listed first
  - [ ] two pieces 30 m apart (joined)
  - [ ] two pieces 500 m apart (the shorter one `disconnected`)
  - [ ] a spur leaving from the middle of a way (that way is walked, with
        `walked_m` below its length)
- [ ] No step between consecutive ways in any fixture DayHike is over 50 m.
- [ ] A relation whose main walk is over 20 km is dropped by the length band,
      and one whose broken chain was under 1 km but whose main walk is in the
      band is kept.
- [ ] Vertices of a linear DayHike whose Trailhead is at the last member's end
      start at the Trailhead.
- [ ] Endpoint choice before the access pass uses the main walk's ends.
- [ ] The tests from 01 and 02 still pass, updated only where this ticket
      changes what they check (the reason for a skipped way).

Prior art: `test_geometry_store_skips_unconnected_member_ways`,
`reversed_ways.osm`, `test_build_catalog`. Glossary: DayHikeGeometry, Leftover,
Trailhead, ElevationProfile (from the Trailhead).
