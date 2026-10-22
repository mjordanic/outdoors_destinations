# 06: Opt-in real-data checks

**What to build:** Checks on the real Catalog and GeometryStore under `data/`,
run only with `DAYHIKE_REAL_DATA=1` like the existing real-data tests, that
prove a full ingest followed the main-walk rules:

- No step between consecutive ways over 50 m. A step means the last vertex of
  one way and the first vertex of the next in `walk_order`.
- No way walked twice in one DayHike.
- Every Catalog `length_m` is within 1–20 km.
- Every Leftover has a reason, and every walked way has a walk order.
- A loop that passes within 100 m of a GeometryStore parking point starts
  within 300 m of one. This is the measure behind the PRD's 22.5% "before"
  figure. GeometryStore holds parking but not the drivable-road access nodes,
  so the check uses parking only. Report the share that fails, and fail the
  check above 2% (road access points nearer than the parking explain a
  few).
- Against `data/baseline/catalog.sqlite`:
  - list the DayHike ids that left and joined the Catalog, grouping those that
    left by main-walk reason (over 20 km, under 1 km, superroute)
  - assert that none left for any other reason, in particular not for lack of
    an access point
  - skip when there is no baseline

The WaterSource re-ingest checks that assert "unchanged against the baseline"
no longer hold after this change and are removed.

User stories 30, 36, 37; PRD "Testing Decisions → Seam 2".

**Blocked by:** 04 (Loops: shortcuts, stems, start near access)

**Status:** ready-for-agent

- [ ] Checks are skipped without `DAYHIKE_REAL_DATA=1`, and the baseline
      comparison is skipped without `data/baseline/`.
- [ ] Each check fails with a message naming a few offending DayHike ids.
- [ ] The left/joined lists with reasons are printed (or written to a file
      named in the test docstring) so ticket 08 can copy them into the report.
- [ ] The removed WaterSource baseline-equality checks are gone, and the module
      docstring says which re-ingest its baseline belongs to.
- [ ] The checks pass on a Catalog built from the committed fixture when the
      environment points them at it (`DAYHIKE_REAL_CATALOG`), or that limit is
      stated in the docstring.

Prior art: the real-data test module (its `needs_baseline` mark and `_read`
helper), `DAYHIKE_REAL_CATALOG`. Glossary: Leftover, Trailhead, DayHike.
