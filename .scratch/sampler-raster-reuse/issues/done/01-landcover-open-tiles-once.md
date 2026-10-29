# 01: LandCover sampler opens each WorldCover tile once per run

**What to build:** `sample-landcover` opens every WorldCover tile in the raster
directory once at the start of a run, reuses the open datasets for every vertex
chunk, and closes them all when the run ends, whether it finishes, raises, or is
interrupted. The Hiker sees the same `vertices.landcover` codes, the same Catalog
fractions and scaler, the same command, flags, stdout count and stderr progress
lines. Only the number of raster opens changes: it no longer grows with the
number of vertex chunks.

The point-in-bounds test and per-point sampling stay as they are; only the
lifetime of the opened raster changes. First-tile-wins in sorted file order is
kept, and a point already given a class is not sampled again. WorldCover nodata
(0) and raster-declared nodata stay uncovered.

The public single-call helper `sample_landcover_classes(raster_dir, points)`
keeps its signature and behaviour: it opens the directory's tiles for that one
call. The sampler and the helper share one internal sampling path that takes
already-open datasets, so the two routes cannot drift.

**Blocked by:** None (can start immediately)

**Status:** done

**Complexity:** medium

**Prior art:** PRD `.scratch/sampler-raster-reuse/PRD.md` stories 1, 3, 5, 7,
9–17, 20. `GLOSSARY.md` LandCover, GeometryStore, Catalog. ADR-0005, ADR-0008,
ADR-0010. Tests in `test_landcover` that write synthetic class GeoTIFFs over the
fixture vertex bbox (`_write_class_tif`) and the CLI subprocess tests for
`sample-landcover`. Reuse those helpers. The vertex chunk size is a module
constant (`_VERTEX_CHUNK`), so a test can shrink it.

- [ ] Each WorldCover tile is opened at most once per `sample_landcover` run
- [ ] All opened datasets are closed on success, on a raised error, and on
      `KeyboardInterrupt`; the GeometryStore journal mode is still reset afterwards
- [ ] `sample_landcover_classes(raster_dir, points)` keeps its signature and
      results, and runs through the same internal sampling path as the sampler
- [ ] New test: shrink the chunk size so the fixture splits into several chunks,
      wrap the raster-open call with a counter, and assert each tile file is
      opened at most once. The test also checks that the written classes and
      Catalog fractions equal a single-chunk run, with overlapping tiles still
      first-tile-wins
- [ ] New test: a tile that fails to read part way through a run still leaves
      every dataset opened so far closed
- [ ] All existing landcover tests pass without changed expectations (empty
      directory returns 0, zero-sample warning, partial-fetch error unchanged)
- [ ] No change to `sample-dem`, ingest, Catalog or GeometryStore schema, chunk
      size, commands or flags; no new dependency
