# 05: Show LandCover and QueryMonth in the notebook walkthrough

**What to build:** The existing notebook walkthrough shows the new facts on
the committed OSM fixture, without downloading WorldCover or WorldClim. The
geometry notebook shows per-vertex land-cover classes and twelve climate rows
per covered vertex. The ranking notebook shows one Ranking with no month and
one with July, including that the July temperature in the score is not the
warm-season temperature, and that LandCover moves a forested neighbour ahead
of a barer one for a forested Seed. The catalog notebook's schema description
names the new fraction columns, regime columns, and monthly means. The
numbered notebook order does not gain a new file. The rank-feature lab does
not present these columns as experiments.

**Status:** ready-for-agent

## Blocked by

- 01-sample-landcover
- 03-sample-climate-and-query-month

**Complexity:** standard

**Prior art:** The notebook runner that executes every walkthrough notebook
against the committed fixture and checks README links. PRD stories 36–37.
Notebooks `02_catalog`, `03_ranking`, and `04a_geometry`.

- [x] The geometry notebook prints a non-zero count of land-cover classes and
      of per-vertex climate rows after sampling a tiny synthetic raster
- [x] The ranking notebook prints a no-month Ranking and a July Ranking, and
      the July temperature differs from the warm-season temperature
- [x] The ranking notebook shows a forested Seed ranking a more forested
      DayHike above a barer one
- [x] The catalog notebook mentions the new Catalog fields
- [x] The walkthrough file list is unchanged
- [x] The notebook test run stays offline
