# 04: Fetch WorldClim monthly normals for the Catalog bbox

**What to build:** The Hiker downloads WorldClim 2.1 30 arc-second monthly
mean temperature, precipitation, and solar radiation into `data/climate/`.
Files already on disk are skipped. The download does not open the Catalog.
`data/climate/` is gitignored. The files are the ones ClimateNormal sampling
reads. The default coverage matches the DEM bbox.

**Status:** ready-for-agent

## Blocked by

- 03-sample-climate-and-query-month

**Complexity:** standard

**Prior art:** `fetch_fabdem` skip-existing tests, ADR-0008, ADR-0010, PRD
stories 26–29. WorldClim 2.1 base zips `wc2.1_30s_tavg`, `wc2.1_30s_prec`,
and `wc2.1_30s_srad`.

- [ ] Fetch stores the twelve monthly GeoTIFFs for temperature, precipitation,
      and solar radiation
- [ ] A file already present is not downloaded again
- [ ] The command does not require a Catalog
- [ ] `data/climate/` is gitignored
- [ ] Tests lock the download locations and the skip rule and do not download
      the real normals
