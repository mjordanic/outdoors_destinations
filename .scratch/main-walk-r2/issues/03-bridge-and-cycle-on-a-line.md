# 03: A cycle hanging off a line is a dead end (no stalled walks)

**What to build:** A line with a small cycle hanging off it by one way is
walked end to end. Today its odd junctions cannot be cut down to two, so the
walk falls back to member order and stops early. The rest becomes `shortcut`
Leftovers. In round 1 that was 1,395 Catalog DayHikes: Hermannshöhen Etappe 02
walks 4.0 of 19.8 km. 435 of them got into the Catalog only because the stall
cut them into the 1–20 km band.

A line can still have more than two odd nodes after dead ends and shortcuts
are set aside, with no removable shortcut left. Then find a way piece whose
removal splits the piece (a bridge). The smaller side by length, the bridge
included, becomes `dead_end`. The usual dead-end and shortcut steps then run
again. Repeat until the line can be walked once. If several bridges qualify,
record the choice made in the commit body.

The member-order greedy walk stays only as a last resort. It should not
trigger on the real data. Loops are unchanged. No new Leftover reason and no
new column.

User stories 1, 8, 11 and 12 (fixture side), 22; ADR-0012.

**Blocked by:** 01 (Parking-only access)

**Status:** ready-for-agent

- [ ] Fixture tests through `build_catalog`, each asserting `length_m`,
      Trailhead, vertex order, `in_chain`, `leftover` and `walk_order`:
  - [ ] a line with a small cycle hanging off a middle junction by one way:
        the line is walked from one real end to the other. The cycle's ways
        and the bridge are `dead_end`, there is no `shortcut`, and `length_m`
        is the line's length.
  - [ ] the same shape with the line over 20 km: dropped by the length band
        (the band reads the whole line, not a stalled part of it)
- [ ] A parallel variant on a line is still a `shortcut`: shortcuts are tried
      before a bridge.
- [ ] No step between consecutive ways in any fixture DayHike is over 50 m.
- [ ] The existing main-walk fixture tests still pass.

Prior art:
`test_a_small_variant_loop_on_a_line_is_a_shortcut_and_both_real_ends_stay`,
`test_a_short_spur_listed_in_the_middle_is_a_dead_end`,
`test_the_length_band_reads_the_main_walk`. Round-1 report: "Not caught by the
checks → Stalled line walks". The stalled ids are in the round-1
`rollout_ids.md`. Glossary: DayHikeGeometry, Leftover, Trailhead.
