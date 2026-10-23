# 04: Real-data checks agree with ingest

**What to build:** The opt-in real-data checks (`DAYHIKE_REAL_DATA=1`) stop
reporting the two false failures from round 1:

- **Step check.** A stored step whose first vertex has the same coordinates
  as the next walked way's node is on that way. Round 1 flagged 19839380 and
  19885873, each with a 101 m step. There, nodes 13305837749 (end of way
  433058087) and 4319747989 (start of way 1450809737) share coordinates. The
  assembly joins them with a 0 m gap and the vertex builder drops the
  repeated point, so the check, which compares node ids, saw a jump.
- **"Why did this DayHike leave" helper.** It assembles with parking as
  access points, the same as ingest after ticket 01: all parking in the
  hiking snapshot. The `osmium getid` extract holds no parking, so read it
  from the GeometryStore `amenities`. In round 1, 1920172 and 16868807 came
  out as "kept by the main-walk rules" without access points. With parking,
  a stem near parking pushes them over 20 km. Stems count toward the band
  (ADR-0012 "Round 2").
- **Loop-start check.** It keeps its 2% limit and its loop test (first and
  last vertex within 50 m). Its comment about drivable-road access points
  goes: access is parking only.
- The module docstring still names the 2026-10-21 baseline (155,844
  DayHikes). It also says round 2 compares against that baseline.

User stories 14, 15, 26. Story 13 holds by property: the band reads the main
walk with its stem, as `test_a_lollipop_stem_ending_at_parking_is_walked_from_the_parking`
shows. This ticket makes the helper agree with it.

**Blocked by:** 01 (Parking-only access)

**Status:** ready-for-agent

- [ ] The checks are skipped without `DAYHIKE_REAL_DATA=1`, and the baseline
      comparison is skipped without `data/baseline/`.
- [ ] Point the step check, via `DAYHIKE_REAL_CATALOG`, at a Catalog built
      from a small fixture whose two ways meet at two different nodes with
      the same coordinates. It passes. Without the fix, it fails.
- [ ] Run on the round-1 data still under `data/`, the helper gives
      "over 20 km" for 1920172 and 16868807.
- [ ] The main-walk checks still pass on a Catalog built from
      `tests/fixtures/catalog.osm`, or the docstring says why not.
- [ ] `uv run pytest` passes (the real-data checks are skipped).

Prior art: the real-data test module (`walk_measures`, `_main_walk_reasons`,
`needs_baseline`, `DAYHIKE_REAL_CATALOG`). Round-1 report: "Real-data check
failures", failures 2 and 4. Glossary: Trailhead, DayHike, GeometryStore.
