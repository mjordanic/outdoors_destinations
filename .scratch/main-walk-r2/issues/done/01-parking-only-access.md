# 01: Parking-only access

**What to build:** Every access point is a parking point from the hiking
snapshot. Parking already comes from the hiking extract's amenity filter. One
access tree, built once from all of it, serves line Trailheads, the 200 m stem
test and loop starts. The 5 km Trailhead radius is unchanged. A line with no
parking within 5 km of either end starts at its member-order end.

Removed, with the tests that only covered them:

- the drivable-road node and way scan
- the access polygon writer and its bounding boxes, the osmium access extract
  and its `*.access*` sidecar files
- the endpoint pre-pass that assembles every relation to choose endpoints
  before the access scan, and the assembly's access search points that fed it
  (with the helper that spaces points along a loop)
- loading road access points from the work file, and "parking near endpoints"
- drivable-road access points in the snapshot, and the drivable-highway set,
  once nothing uses them

The work file drops its access tables and access progress passes. Its schema
version is bumped so an old work file restarts. osmium stays for the hiking
tags-filter extract.

The ingest report, `build_catalog`, `run_ingest` and the GeometryStore writer
keep their outward shape where callers depend on it. A return value or
parameter left without a use is removed with its callers, including the
real-data test helper that calls `run_ingest`. Ingest progress no longer
reports an access scan, and the stage list in `build_catalog`'s docstring
matches.

Keep this ticket to code, docstrings and tests. README, ADR, CONTEXT and
notebook text belong to ticket 05 (stale sweep).

User stories 3, 4, 5, 6, 7, and 18 and 20 for the road scan; ADR-0012
"Round 2".

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Fixture tests through `build_catalog`:
  - [ ] a line with parking near its last member's end: the Trailhead is that
        end and the vertices start there
  - [ ] the same line with no parking within 5 km of either end: the
        Trailhead is the member-order end
  - [ ] a drivable highway near a line's last end, with no parking near it,
        does not make that end the Trailhead
  - [ ] a loop still starts at its vertex nearest parking, and a stem whose
        far end is within 200 m of parking is still walked from there
- [ ] After `build_catalog`, no `*.access*` file is left beside the Catalog,
      and a PBF ingest calls osmium only for the hiking tags-filter.
- [ ] A work file with the old schema version is not resumed.
- [ ] Tests of the road scan, the access extract, its boxes and its sidecars
      are removed. The drivable-highway Trailhead fixture goes too unless
      another test reads it.
- [ ] `uv run pytest` passes.

Prior art: `test_trailhead_is_the_endpoint_nearer_parking`,
`test_a_line_whose_trailhead_is_the_last_members_end_starts_there`,
`test_a_loop_starts_at_its_vertex_nearest_parking_in_member_direction`,
`test_a_lollipop_stem_ending_at_parking_is_walked_from_the_parking`,
`test_a_work_file_without_member_roles_is_not_resumed`,
`test_build_catalog_reports_ingest_stages`, `trailhead_at_parking.osm`.
Round-1 report: "The access extract covers one place only". Glossary:
Trailhead (an access point is parking), DayHike, GeometryStore.
