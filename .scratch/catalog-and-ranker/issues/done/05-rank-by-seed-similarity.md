# 05: Rank DayHikes by maximum similarity to Seeds

**What to build:** The Hiker names one or more Seeds and gets a Ranking of other
DayHikes: score is cosine similarity in StandardScaler space, and with several
Seeds the score is the maximum similarity to any Seed. Seeds themselves are
omitted from the list. Unknown or ambiguous Seeds still fail via name
resolution. CLI prints id, name, score. No Origin, no Valhalla.

**Status:** ready-for-agent

## Blocked by

- 03-feature-vectors
- 04-resolve-seed-names

**Complexity:** high

**Prior art:** FeatureVectors (03), resolve (04), ADR-0003, PRD stories 16–18 and
22–24. Public seam `rank_dayhikes`. `CONTEXT.md` Ranking, Seed, FeatureVector.

- [x] `build_catalog` fits a StandardScaler on Catalog FeatureVectors and
      persists it in the same SQLite file
- [x] `rank_dayhikes` with one fixture Seed returns the other in-band DayHike
      with a similarity score, and does not include the Seed
- [x] Two Seeds: a candidate close to only one Seed outranks a midpoint —
      score is max, not mean
- [x] Ranking is deterministic on the fixture across two runs
- [x] CLI wraps `rank_dayhikes` and prints id, name, score
- [x] Tests do not use the network or Valhalla
