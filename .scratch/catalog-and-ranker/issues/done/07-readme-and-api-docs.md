# 07: README and public-API documentation

**What to build:** After the Catalog, Ranking, and DriveTime filter exist, a
newcomer can learn how to install, ingest the fixture, rank by Seeds, and
optionally filter by Origin from a README at the repo root. Public Python
seams (`build_catalog`, `rank_dayhikes`, `filter_by_drive_time`, `resolve_seed`)
have docstrings. Non-obvious OSM/Valhalla choices have short inline comments.
No new ranking behaviour.

**Status:** ready-for-agent

## Blocked by

- 06-filter-ranking-by-drivetime

**Prior art:** PRD Further Notes, ADRs 0001–0004, `GLOSSARY.md`. The CLI from
tickets 05–06 is the documented entry point.

- [x] README covers install (`uv sync`), ingest of the committed fixture, rank
      by Seed, optional Origin/max hours, ODbL attribution, and what is
      gitignored under `data/`
- [x] README states Seed names must exist in the Catalog and that DriveTime is
      not a FeatureVector column
- [x] Public seams have docstrings using glossary terms (DayHike, Seed,
      Ranking, Origin, DriveTime)
- [x] Valhalla compose/docs (from ticket 06) are linked from the README
