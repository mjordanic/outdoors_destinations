# 08: Demonstration notebooks

**What to build:** Committed Jupyter notebooks under `notebooks/` that run
against the fixture Catalog (no Geofabrik, no live Valhalla). They show ingest
counts, FeatureVectors, Ranking by Seed (including max-not-mean), unknown Seed
failure, and a stubbed DriveTime filter. A limitations section states unnamed
paths never become DayHikes, DriveTime needs local Valhalla for real origins,
and ratings/UI are future work.

**Status:** ready-for-agent

## Blocked by

- 07-readme-and-api-docs

**Prior art:** Fixture from ticket 01, `rank_dayhikes` from 05, DriveTime stub
from 06. README from 07 points at these notebooks.

- [x] At least one notebook under `notebooks/` executes with `uv run` / the
      project kernel against the committed OSM fixture
- [x] The notebook shows ingest keep/drop counts and the kept DayHike names
- [x] The notebook shows Ranking for one Seed and for two dissimilar Seeds
- [x] The notebook demonstrates unknown Seed failure
- [x] The notebook shows DriveTime filtering with a stub client, and states
      that real routing needs local Valhalla tiles
- [x] Limitations are written in the notebook: Catalog unit is named
      `route=hiking` relations in a length band; no scraped reviews; no webpage
      ratings yet
