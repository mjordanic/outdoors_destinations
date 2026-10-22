# 02: Member roles and each way once

**What to build:** Ingest keeps each relation member's OSM role. Members with
role `alternative`, `alternate`, `variant`, `variation`, `detour`, `excursion`
or `connection` are Leftovers with reason `role`. `approach`, `forward`,
`backward`, empty and any other role are walked. A way listed more than once
is walked at most once. Its later listings are Leftovers with reason `repeat`,
so a there-and-back trail stores its one-way line. GeometryStore `ways` gains
the `leftover` column (NULL when walked). For now every other skipped way gets
reason `disconnected`, because it was skipped by the existing chain.

A Dom Paklenica – Jurline style relation (one way listed four times) gets the
length of that way once. User stories 2, 3, 15, 16, 17, 23, 35; ADR-0012 rules
"each distinct way counts once" and "roles".

**Blocked by:** 01 (One main-walk assembly)

**Status:** ready-for-agent

- [ ] Every OSM reader that builds relations keeps member roles, including the
      ingest work file's relation members. Adding the roles must not break
      code that reads members as (type, id): keep roles beside the members,
      not inside each member tuple.
- [ ] An ingest work file written before this change does not resume; ingest
      starts it again (the resume fingerprint or schema check rejects it).
- [ ] Role Leftovers: a fixture relation with `alternative`, `excursion` and
      `connection` members stores those ways with `in_chain = 0` and
      `leftover = 'role'`, and they are not in vertices or `length_m`.
- [ ] `approach` and `forward`/`backward` members are walked.
- [ ] Repeat listing: a fixture relation listing one way four times has
      `length_m` equal to that way's length. The first listing is walked and
      the others have `leftover = 'repeat'`.
- [ ] The a-b-c-d-e-a-b-e-b member list (loop, then a-b again, b-e, e-b) no
      longer counts a-b twice. Full handling of the b-e side trip is ticket 04.
- [ ] `test_geometry_store_skips_unconnected_member_ways` asserts the new
      `leftover` column.

Prior art: `test_a_spring_tagged_as_drinking_water_stays_drinking_water`
(inline fixture edit), the streaming-ingest resume tests, `reversed_ways.osm`.
Glossary: Leftover, DayHike (length counted once).
