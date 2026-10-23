# 02: Dead-end tip join: gap-closed routes are loops

**What to build:** A route closed by a small gap inside one piece is a loop,
so it starts near parking. Round 1 read 5,379 such routes as lines, and they
caused almost all loop-start failures (4.81% against a 2% limit).

After pieces are joined and the main piece is chosen, two dead-end tips
(degree-one nodes) of the main piece 50 m or less apart are joined with a
straight gap, closest pair first, each tip at most once. The gap is drawn and
counts in `length_m`, like the gap join between pieces. The loop/line
decision then runs as before, so such a route starts at its stem or at its
vertex nearest parking.

Only dead-end tips of the main piece are joined: never a tip to a vertex in
the middle of a way, never a tip of a Leftover. No new GeometryStore or
Catalog column. A gap is not a way, so it has no `ways` row.

User stories 2, 9, 10, 16 (fixture side), 22; ADR-0012.

**Blocked by:** 01 (Parking-only access)

**Status:** ready-for-agent

- [ ] Fixture tests through `build_catalog`, each asserting `length_m`,
      Trailhead, vertex order and Leftover reasons:
  - [ ] a route whose two way ends in one piece are 30 m apart, with parking
        by a vertex mid-route: a loop that starts at the vertex nearest
        parking. Its first and last vertex are the same point, `length_m`
        includes the 30 m gap, and there is no Leftover.
  - [ ] a dead-end tip of the main piece 30 m from a vertex in the middle of
        a way of the main piece: no gap is drawn, no loop
  - [ ] a dead-end tip of the main piece 30 m from the tip of a way that is
        not on the main piece (for example a `role` Leftover): no gap is
        drawn, no loop
  - [ ] three tips within 50 m of each other: the closest pair is joined, and
        the third stays a tip
- [ ] No step between consecutive ways in any fixture DayHike is over 50 m.
- [ ] No new column in the Catalog or GeometryStore.
- [ ] The existing main-walk fixture tests still pass.

Prior art: `test_pieces_30_m_apart_are_joined_by_their_gap`,
`test_a_loop_starts_at_its_vertex_nearest_parking_in_member_direction`,
`test_a_loop_without_access_starts_where_member_order_starts_it`, the gap join
between pieces in the assembly. Round-1 report: "Real-data check failures",
failure 3. Glossary: DayHikeGeometry, Trailhead, Leftover.
