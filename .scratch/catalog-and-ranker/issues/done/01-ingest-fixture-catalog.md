# 01: Ingest a fixture OSM file into a Catalog of DayHikes

**What to build:** The Hiker (via a developer running the package) hands over a
small OSM file and gets a Catalog on disk: named DayHikes inside the length
band, each with a Trailhead, Difficulty from member-way `sac_scale`, geometry
length, and an ingest counts report (kept vs dropped and why). Unnamed
relations, relations outside 2–25 km, and iwn/nwn superroutes do not become
DayHikes. A vacant `labels` table exists so later ratings do not rewrite the
Catalog. OSM attribution is stored with the Catalog.

This is the tracer bullet for `build_catalog`. Ranking, amenities, and Valhalla
are later tickets. The committed fixture under `tests/fixtures/` is the oracle;
Geofabrik downloads are not required to pass tests. A CLI ingest command may
accept real PBFs onto gitignored `data/catalog.sqlite`.

**Status:** ready-for-agent

## Blocked by

- None (can start immediately)

**Complexity:** high

**Prior art:** No code prior art — first feature. Read the PRD Implementation
and Testing Decisions for `build_catalog`, ADR-0001, ADR-0002, and `GLOSSARY.md`.
Use DayHike, Trailhead, Catalog, Difficulty, Hiker as names in the public API.

- [x] `uv` project named `dayhike` runs `uv run pytest`
- [x] A committed OSM fixture contains two named in-band hiking relations, one
      unnamed, one named out-of-band, and one long-distance network relation
- [x] `build_catalog` on that fixture persists exactly the two in-band named
      DayHikes and no others
- [x] Each kept DayHike has a Trailhead lat/lon and a length inside 2–25 km
- [x] Difficulty is the maximum `sac_scale` on member ways, or a distinct
      missing value — not silently T1
- [x] The ingest report counts kept vs dropped by reason (unnamed, length,
      network/superroute)
- [x] A second `build_catalog` on the same bytes yields the same DayHike
      identities
- [x] SQLite includes an empty `labels` table with `hiker_id`, `dayhike_id`,
      `score`, `created_at`
- [x] OSM attribution is stored with the Catalog
- [x] Tests never read `data/` or the network; Catalog paths are temp dirs
- [x] `data/catalog.sqlite` remains gitignored for real extracts
