# PRD: Catalog and ranker

Status: ready-for-agent

Vocabulary in this document is defined in `GLOSSARY.md`. Terms are used in their
glossary sense — DayHike, Trailhead, Catalog, Origin, DriveTime, Hut, Difficulty,
AmenityFact, Seed, FeatureVector, Ranking, Hiker — and not loosely. Respects
ADR-0001 (OSM snapshot), ADR-0002 (DayHike is a named route relation in a length
band), ADR-0003 (k-NN in scaled intrinsic features), ADR-0004 (local Valhalla
matrix for DriveTime).

## Problem Statement

A Hiker planning a day outing in Croatia, Slovenia, Bosnia, Montenegro, and the
Alps has too many OSM traces and no way to ask "more like the hikes I already
did," subject to how far they will drive today.

Public trail sites keep reviews behind terms of service. A live Overpass query
over that geography will not finish. Drive-time isochrones on the public
openrouteservice cap at one hour. A list of filters (length, Difficulty) is not
the same as "like those Seeds." And DriveTime is not a fact about a DayHike; it
depends on today's Origin.

The Hiker needs a Catalog that lives on their disk, a Ranking by similarity to
named Seeds, and an optional DriveTime filter — from Python, before any webpage.

## Solution

A local Python package (uv) that:

1. Ingests OSM PBF snapshots into a Catalog of DayHikes (named `route=hiking`
   relations in a ~2–25 km band) with Trailheads and AmenityFacts (Huts,
   parking, restaurant/cafe, drinking water).
2. Fits a scaler on FeatureVectors and ranks the Catalog by maximum similarity
   to one or more Seeds the Hiker names.
3. Optionally drops DayHikes whose DriveTime from Origin exceeds a max, using a
   local Valhalla matrix. Isochrones are not used for Ranking.

All bulky files stay under `data/` (gitignored). Tests use a committed OSM
fixture, not Geofabrik. There is no web UI in this milestone.

## User Stories

**Building the Catalog**

1. As a Hiker, I want OSM extracts on disk turned into a Catalog, so that Ranking
   does not depend on Overpass being up.
2. As a Hiker, I want only named hiking relations in a day-length band in the
   Catalog, so that I am not ranking unnamed forest fragments or 200 km
   superroutes.
3. As a Hiker, I want each DayHike to have a Trailhead, so that amenities and
   DriveTime have a point to measure from.
4. As a Hiker, I want Difficulty taken from `sac_scale` on member ways, so that
   grade is an OSM fact rather than a guess.
5. As a Hiker, I want length from the relation geometry, so that the length band
   is enforced rather than hoped for.
6. As a Hiker, I want a counts report after ingest (kept vs dropped, and why), so
   that I can see whether an extract is dense enough to bother Ranking.
7. As a Hiker, I want to ingest Croatia, Slovenia, Bosnia and Herzegovina,
   Montenegro, and Alpine extracts with the same rules, so that one Catalog
   covers the geography I actually drive.
8. As a developer, I want ingest to be deterministic given the same PBF, so that
   a change in Ranking came from a change I made.

**Amenities as features, not as the Catalog**

9. As a Hiker, I want Hut count and distance near the Trailhead recorded, so that
   "hut nearby" can participate in similarity.
10. As a Hiker, I want parking near the Trailhead recorded, so that car access is
    visible in the FeatureVector.
11. As a Hiker, I want restaurant and cafe counts near the Trailhead recorded, so
    that easy logistics after the hike can participate in similarity without
    making restaurants the Catalog key.
12. As a Hiker, I want drinking water near the Trailhead recorded, so that a dry
    karst start differs from a village start.
13. As a Hiker, I want a DayHike with no restaurants to still be in the Catalog,
    so that alpine dirt-lot starts are not deleted.

**FeatureVectors and Ranking**

14. As a Hiker, I want a FeatureVector of intrinsic fields only (length,
    elevation stats when present, Difficulty, AmenityFacts), so that similarity
    means "enjoyable like the Seeds," not "near Zagreb."
15. As a Hiker, I want DriveTime excluded from the FeatureVector, so that moving
    Origin does not pretend to change what a DayHike is.
16. As a Hiker, I want a scaler fit on the Catalog FeatureVectors, so that metres
    and sac_scale grades do not dominate each other by raw range.
17. As a Hiker, I want to name one Seed I already did and get a Ranking of other
    DayHikes, so that I can plan something in that vein.
18. As a Hiker, I want to name several Seeds and have each candidate scored by
    its maximum similarity to any Seed, so that a coastal stroll and a Julian
    ridge do not average into a mushy middle.
19. As a Hiker, I want Seed names resolved against the Catalog, so that I am
    ranking from real DayHikes rather than from a hallucinated title.
20. As a Hiker, I want an unknown Seed name to fail loudly, so that I do not get
    an empty or random Ranking and think it worked.
21. As a Hiker, I want an ambiguous Seed name (several Catalog matches) to fail
    loudly with the candidates, so that I can pick the intended DayHike.
22. As a Hiker, I want the Seed itself omitted or ranked with a documented rule
    (dropped from results), so that "more like X" is not just X.
23. As a Hiker, I want similarity scores in the Ranking, so that I can see why
    something was near the top.
24. As a developer, I want Ranking to be a function over a Catalog plus Seeds,
    so that tests do not need a network or Valhalla.

**Origin and DriveTime**

25. As a Hiker, I want to pass an Origin and a maximum DriveTime and have the
    Ranking filtered, so that today's departure point limits the list.
26. As a Hiker, I want Ranking without Origin to still work, so that I can
    inspect similarity before routing tiles exist.
27. As a Hiker, I want DriveTime to come from local Valhalla matrix calls, so
    that I am not capped at one-hour public isochrones.
28. As a Hiker, I want unreachable Trailheads dropped rather than given a fake
    DriveTime, so that I do not plan a hike the router cannot reach.
29. As a developer, I want DriveTime tests to mock the matrix HTTP API, so that
    CI does not build Valhalla tiles.

**Local data and licensing**

30. As a Hiker, I want bulky PBFs and Valhalla tiles gitignored under `data/`, so
    that the git repo stays small.
31. As a Hiker, I want OSM attribution recorded with the Catalog, so that ODbL
    use is honest.
32. As a developer, I want a tiny committed OSM fixture, so that ingest and
    Ranking tests do not download Geofabrik.

**Later website (schema only)**

33. As a developer, I want a vacant labels table keyed by hiker and DayHike, so
    that website ratings do not require a Catalog rewrite.
34. As a Hiker, I do not want a webpage in this milestone, so that phase 1 stays
    scripts and a database.

## Implementation Decisions

- **Two public seams, plus one optional filter.** `build_catalog` turns OSM files
  into a Catalog on disk. `rank_dayhikes` takes a Catalog and Seeds and returns a
  Ranking. `filter_by_drive_time` takes a Ranking, Origin, and max hours and
  returns a Ranking. Amenities, FeatureVectors, and the scaler live behind
  `build_catalog`. Name resolution lives behind `rank_dayhikes`. No third
  "recommender service."
- **CLI wraps those seams.** Ingest writes `data/catalog.sqlite` (gitignored).
  Rank reads it, prints DayHike id, name, score, and optional DriveTime.
- **Length band.** Inclusive 2 km to 25 km of relation geometry. Shorter or
  longer relations are counted as dropped in the ingest report. Superroutes
  (`network=iwn` / long `nwn` without day-length child relations) are dropped.
- **Trailhead.** First and last nodes of the assembled LineString; use the one
  closer to a highway/parking if both exist in a small radius, otherwise the
  first node. Record lat/lon.
- **Difficulty.** Maximum `sac_scale` on member ways, stored as the OSM string
  and as an ordinal for the FeatureVector. Missing scale is a distinct value,
  not T1.
- **Amenity radius.** 1 km great-circle from Trailhead for count and nearest
  distance of Hut (`tourism=alpine_hut` or `tourism=wilderness_hut`), parking
  (`amenity=parking`), restaurant/cafe (`amenity=restaurant` or `amenity=cafe`),
  drinking water (`amenity=drinking_water`). Zero counts are valid.
- **Elevation.** If OSM `ele` exists on nodes, min/max/gain along the geometry
  go into the FeatureVector. A DEM download is not required for this milestone;
  missing elevation is a distinct missing value, not zero.
- **Scaler.** StandardScaler fit on Catalog rows that have complete required
  numeric fields; persist scaler params in the same SQLite file. Optional PCA is
  out of this milestone unless it is a one-flag sklearn step with a test — default
  off.
- **Similarity.** Cosine similarity on scaled FeatureVectors. Ranking score =
  max over Seeds. Seeds excluded from the result list.
- **SQLite.** Tables at least: `dayhikes`, `trailheads`, `amenity_facts`,
  `feature_vectors`, `scaler`, `labels` (empty: `hiker_id`, `dayhike_id`,
  `score`, `created_at`). No ORM unless sqlite3 becomes painful at the seam.
- **Valhalla.** Optional Docker compose under `data/valhalla` documented, not
  required to import the package. Matrix chunking if Trailhead count exceeds a
  small batch size (e.g. 50). Tests never start Docker.
- **Package name.** `dayhike`. Tests via `uv run pytest`.
- **Extract list (ingest script, not tests).** Geofabrik: croatia, slovenia,
  bosnia-herzegovina, montenegro, austria, switzerland, italy/nord-est,
  germany/bayern. France alpine extracts only if a follow-up shows the filtered
  relation count is worth it; not a blocker for the fixture-backed tickets.

## Testing Decisions

**A good test asserts Catalog or Ranking behaviour, not osmium internals.** Tests
go through `build_catalog`, `rank_dayhikes`, and `filter_by_drive_time`. A test
that inspects SQL table names as the behaviour is too low; a test that only
mocks sklearn internals is too high.

**The ingest fixture is the oracle.** A committed OSM XML (or tiny PBF) under
`tests/fixtures/` contains: two named hiking relations inside the length band
(different Difficulty and amenities), one unnamed relation, one named relation
outside the band, one long-distance network relation, a Hut, a cafe, parking,
and water near one Trailhead and not the other. Hand-computed: which ids survive,
AmenityFact counts, and which DayHike is nearer to which Seed.

**Ranking tests use that Catalog.** Seed by exact name → expected neighbor order.
Unknown name → error. Two matching names → ambiguous error. Two Seeds of
different character → score is max, not mean (a candidate similar to only one
Seed outranks a midpoint). Seed not in the result list.

**DriveTime tests stub the matrix client.** Return seconds for some Trailheads,
null for others; filter keeps finite times under max hours. No tiles in CI.

**Determinism.** Same fixture bytes → same DayHike ids and scores across two
`build_catalog` + `rank_dayhikes` runs.

There is no prior art in this repo; this is the first feature.

## Out of Scope

The webpage, auth, and per-Hiker rating UI. Isochrone GeoJSON. Open-Meteo.
Scraped reviews and LLM descriptions. Public transit. Snow and sun GIS. DEM
sampling. Multi-day and Hut-to-Hut itineraries. Via ferrata, climbing, MTB.
Collaborative filtering. Fitting a supervised ranker. Live Overpass. Public
ORS isochrones. Planet or full-Europe PBF.

See `docs/future-work.md`.

## Further Notes

**Seeds that are not in the Catalog cannot be queries.** That is ADR-0002, not a
bug to paper over with fuzzy Wikipedia search.

**`data/catalog.sqlite` is gitignored.** Tests must not depend on a developer
having run Geofabrik ingest. The fixture Catalog is built in test setup into a
temp path.

**implement-issues** must run on a feature branch, not `main`, with a clean tree
except the report file. Isolation at `cap=3` without a GitHub remote is
`worktree` (see the implement-issues skill). Do not start those agents from this
PRD; this document plus the issues under `.scratch/catalog-and-ranker/issues/`
are the spec they will read later.
