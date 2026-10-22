# 01: One main-walk assembly (prefactor)

**What to build:** Today a DayHike's line is built from the one-pass chain in
three separate places: choosing endpoints before the access pass, keeping or
dropping a relation by length, and writing GeometryStore (where the in-chain
flags and `unconnected_m` are computed again). Put them behind one assembly
step that takes a relation, its ways, node coordinates and the access points,
and returns everything those three places need: the ordered vertices, the
main walk's length, a status per member listing and the Leftover metres. All
three use it. Behaviour does not change: the assembly still runs the ADR-0009
chain for now.

This makes tickets 02–04 change one place instead of three. See PRD
"Implementation Decisions → One main-walk assembly" and user story 27.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Endpoint choice, the keep/drop decision, DayHike construction and the
      GeometryStore write all use the one assembly; no other code glues
      member ways for ingest.
- [ ] The assembly takes the access points as input (unused for now) so
      ticket 04 does not change its callers again.
- [ ] Catalog and GeometryStore built from every committed OSM fixture are
      unchanged (same `length_m`, Trailheads, vertices, `in_chain`,
      `unconnected_m`).
- [ ] The full test suite passes with no test edited.

Prior art: `test_geometry_store_skips_unconnected_member_ways`, the
streaming-ingest tests, `test_build_catalog`. Glossary: DayHikeGeometry,
Leftover, GeometryStore.
