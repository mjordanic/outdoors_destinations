# 02: Climate sampler opens each of the 36 monthly rasters once per run

**What to build:** `sample-climate` opens each of the 36 WorldClim rasters
(tavg, prec, srad × 12 months) once at the start of a run, reuses the open
datasets for every vertex chunk, and closes them all when the run ends, whether
it finishes, raises, or is interrupted. The Hiker sees the same `vertex_climate`
rows, the same Catalog `temperature_c`, `precipitation_mm`, `solar_radiation`,
`climate_months`, scalers and month scalers, so QueryMonth ranking is unchanged.
The command, flags, stdout count and stderr progress lines stay as they are.
Only the number of raster opens changes: it no longer grows with the number of
vertex chunks.

The point-in-bounds test and per-point sampling stay as they are; only the
lifetime of the opened raster changes. The all-36-or-skip rule per vertex is
kept: a vertex missing any one value stays uncovered and never writes a partial
year. Raster-declared nodata stays uncovered. The incomplete-raster-set error
is still raised before any sampling starts.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

**Complexity:** medium

**Prior art:** PRD `.scratch/sampler-raster-reuse/PRD.md` stories 2, 4, 6,
8–14, 16, 17, 20. `CONTEXT.md` ClimateNormal, QueryMonth, GeometryStore,
Catalog. ADR-0005, ADR-0008, ADR-0010. Tests in `test_climate` that write
synthetic constant month GeoTIFFs (`_write_climate_tifs`), the CLI subprocess
tests for `sample-climate`, and
`test_sample_climate_rejects_incomplete_raster_set`. Reuse those helpers. The
vertex chunk size is a module constant (`_VERTEX_CHUNK`), so a test can shrink
it. Follow the same open-once shape as ticket 01 if it has landed, but do not
wait on it.

- [ ] Each of the 36 climate rasters is opened at most once per `sample_climate` run
- [ ] All opened datasets are closed on success, on a raised error, and on
      `KeyboardInterrupt`; the GeometryStore journal mode is still reset afterwards
- [ ] New test: shrink the chunk size so the fixture splits into several chunks,
      wrap the raster-open call with a counter, and assert each month file is
      opened at most once. The test also checks that `vertex_climate` and the
      Catalog climate columns equal a single-chunk run
- [ ] New test: a raster that fails to read part way through a run still leaves
      every dataset opened so far closed
- [ ] All existing climate tests pass without changed expectations (empty
      directory returns 0, incomplete-set error, zero-sample warning unchanged)
- [ ] No change to `sample-dem`, ingest, Catalog or GeometryStore schema, chunk
      size, commands or flags; no new dependency
