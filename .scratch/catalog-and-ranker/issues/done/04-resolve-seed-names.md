# 04: Resolve Seed names against the Catalog

**What to build:** The Hiker names a Seed. The Catalog returns exactly one
DayHike id, or fails. Unknown names fail loudly. Several matches fail loudly
with the candidate names. This is the name door for Ranking; it does not rank.

**Status:** ready-for-agent

## Blocked by

- 01-ingest-fixture-catalog

**Prior art:** Catalog names from ticket 01. PRD stories 19–21. ADR-0002 (a Seed
must be a Catalog DayHike). `GLOSSARY.md` Seed, DayHike.

- [x] Exact fixture DayHike name resolves to that DayHike's id
- [x] A name that matches nothing raises a clear error (not an empty list)
- [x] When two DayHikes would match the same query string, the error lists
      candidates instead of picking one
- [x] Resolution is a function over an already-built Catalog (no OSM I/O)
- [x] Case-folding is documented and tested (pick one rule: case-insensitive
      exact match after strip)
