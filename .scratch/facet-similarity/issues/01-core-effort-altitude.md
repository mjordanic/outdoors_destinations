# 01: Facet scoring core with Effort and Altitude, end to end

**What to build:** The Hiker opens two new notebooks over the Slovenia + Croatia
region and compares DayHikes on Effort and Altitude. Notebook 05 loads the
region from the GeometryStore the way 04a does (with Catalog summaries merged
on), extracts the Effort and Altitude facts per DayHike, and writes the
similarity facts table next to the Catalog. Notebook 05a reads that table,
prints each measure's distribution and the constants chosen from it, computes
Effort and Altitude FacetScores, combines them with FacetWeights into a score
with coverage, ranks the region for one Seed, and verifies each Facet on a
similar and a dissimilar pair of real named DayHikes. This ticket also lays
down the shared math every later Facet uses, and the notebook shape later
tickets extend with one section per Facet.

**Status:** ready-for-agent

## Blocked by

- None (can start immediately)

**Complexity:** high

**Model:** opus

**Effort:** high

**Prior art:** PRD sections "Combining", "Measure kinds", "Starting constants",
"Region and loading", "Similarity facts table", "Notebooks"; PRD stories 1–22,
46–50, 53–58. ADR-0011 (proposed), ADR-0003 (multi-Seed max), ADR-0006.
`CONTEXT.md` Facet, FacetScore, FacetWeights, Difficulty. Notebook 04a
(GeometryStore as GeoPandas, fixture fallback under `PYTEST_CURRENT_TEST`,
`show_fig`-style figure display). Tests: `test_geometry_store`
(fixture-built GeometryStore), `test_rank_dayhikes`,
`test_demonstration_notebooks` (walkthrough list, README mention, fixture
execution).

## Acceptance criteria

- [ ] A new package module `dayhike.similarity` has plain functions for the four measure kinds (ratio with floor, tolerance with D, mix, presence), FacetScore (mean of non-missing measures), combined score with coverage (normalized FacetWeights, zero weights ignored, missing Facets dropped and renormalized), and the multi-Seed maximum. Each returns a value in [0, 1] or None for missing input.
- [ ] Unit tests cover: identity gives 1, symmetry, bounds, ratio floors (both under the floor give 1), missing input gives None, FacetScore ignores missing measures, combined score renormalizes and reports coverage, all-missing gives a missing score, multi-Seed takes the max.
- [ ] A region loader in the same module returns the 04a frames (vertices, amenities, dayhikes, ways as GeoDataFrames; Catalog `feature_vectors`, `trailheads` and Difficulty merged on) for DayHikes whose Trailhead lies in a lat/lon box, filtering rows with SQL rather than reading whole tables. Tested on the fixture GeometryStore.
- [ ] Per-Facet fact helpers return a frame indexed by DayHike id. This ticket adds Effort (length, elevation gain, Difficulty rank) and Altitude (highest and lowest point) facts. Missing stays missing.
- [ ] `notebooks/05_similarity_facts.ipynb`: region load (box lat 42.3–46.9, lon 13.3–19.5 on the local Catalog; the fixture otherwise), a short Effort section and an Altitude section with a distribution plot each, then the facts table written as `{catalog}.similarity_facts.pkl`. A `*.similarity_facts.pkl` gitignore rule exists and `git check-ignore` confirms it.
- [ ] `notebooks/05a_similarity.ipynb`: reads the facts table when present, otherwise builds it from the same loader and fact helpers (fixture, pytest). Prints the region distributions for each Effort/Altitude measure with the constants used (PRD starting constants unless the data argues otherwise, with the reason written in markdown). Computes both FacetScores.
- [ ] Verification for Effort and for Altitude: one similar and one dissimilar pair of real `osm_name` DayHikes in the region, the expected verdict and reason written in markdown before the score cell, the pair shown with its facts (and a small map), and a `verify\t<facet>\t<check>\t<score>\tPASS|FAIL` line (PASS = similar beats dissimilar; soft targets ≥ 0.7 and ≤ 0.4 printed alongside).
- [ ] Synthetic checks print `verify` lines for identity, symmetry and reversed direction on each Facet; on the fixture these are the only verification lines and they PASS.
- [ ] A small ranking cell: one Seed against the region with FacetWeights over Effort and Altitude, top 10 with per-Facet scores, the combined score and coverage.
- [ ] Both notebooks are in the walkthrough list in `test_demonstration_notebooks` and in the README notebook walkthrough table; the full test suite passes.
- [ ] Notebook code stays plain: short cells, functions called directly, no classes or pipelines; each rule explained in a markdown cell above the code.
