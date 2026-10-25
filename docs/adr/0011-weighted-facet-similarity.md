---
status: superseded by ADR-0013 (proposed; never promoted out of the notebooks)
---

# Similarity is a Hiker-weighted mean of FacetScores

The Hiker wants to say what "similar" means for one query (huts matter, vegetation does not). A single cosine over a scaled FeatureVector cannot take that. Similarity of two DayHikes is therefore Σ wᶠ·FacetScoreᶠ / Σ wᶠ over the Facets both DayHikes can be scored on, with wᶠ the FacetWeights. Each FacetScore is in [0, 1] and is the plain mean of its measures. A Facet that cannot be scored on either DayHike (no LandCover outside the sampled bbox, no tags) is dropped for that pair and the remaining weights renormalize; the share of weight that was scored is reported as coverage. With several Seeds the score is the maximum combined score to any Seed, as in ADR-0003. DriveTime stays a filter after Ranking.

This phase lives in notebooks only. `rank_dayhikes` stays cosine until the design is approved and promoted.

**Considered Options**: keep cosine and let the Hiker scale z-scored columns (weights on unbounded columns are not readable and do not sum to a score in [0, 1]); geometric mean or minimum across Facets (one missing hut sinks an otherwise perfect match); score a missing Facet 0 or 0.5 (punishes or invents data); per-Facet maximum across Seeds (stitches one candidate's score from several Seeds); Jaccard for presence measures (two hut-less DayHikes become unlike). Rejected.

**Consequences**: along-route facts (amenities near DayHikeGeometry, surface mix, ProfileShape) come from GeometryStore. Ranking does not read GeometryStore (ADR-0006), so promotion has to store those facts on the Catalog.
