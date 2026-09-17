# 06: Filter a Ranking by DriveTime from Origin

**What to build:** After a Ranking exists, the Hiker may pass an Origin and a
maximum DriveTime. Trailheads whose Valhalla matrix time is missing or above the
max are dropped; the others keep their similarity order with DriveTime reported.
Without Origin, Ranking is unchanged. Isochrones are not used. Tests stub the
matrix HTTP client; Docker tiles are optional developer docs only.

**Status:** ready-for-agent

## Blocked by

- 05-rank-by-seed-similarity

**Prior art:** Ranking from ticket 05, ADR-0004, PRD stories 25–29. Public seam
`filter_by_drive_time`. `CONTEXT.md` Origin, DriveTime, Trailhead, Ranking.

- [x] `filter_by_drive_time` keeps DayHikes with finite DriveTime under max
      hours and preserves similarity order
- [x] Null/unreachable matrix entries are dropped, not given a fake time
- [x] Calling rank without Origin does not require Valhalla
- [x] Tests stub the matrix client (seconds vs null); they do not build tiles
      or start Docker
- [x] CLI accepts Origin coordinates and max hours and prints DriveTime when
      filtering
- [x] Optional compose/docs for local Valhalla mention `/sources_to_targets`,
      not isochrones as the filter
