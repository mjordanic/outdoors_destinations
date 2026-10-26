---
status: superseded by ADR-0013
---

# Similarity is k-NN in scaled intrinsic features

Phase 1 does not train on likes. A StandardScaler (optional PCA) is fit on Catalog FeatureVectors. Given Seeds, Ranking uses maximum cosine/Euclidean similarity to any Seed in that scaled space. DriveTime is applied after Ranking. Fitting a supervised ranker waits for website ratings.

Calling this "unsupervised discovery" would be wrong: the Seed set is a query. The only unsupervised fit is the scaler.

**Considered Options**: LLM embeddings of generated blurbs (circular with the same OSM tags); collaborative filtering (one Hiker, no matrix); averaging Seed vectors (mush when Seeds disagree). Rejected.
