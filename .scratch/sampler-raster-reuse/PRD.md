# PRD: Open each raster once per sampling run

Status: ready-for-agent

Vocabulary in this document is defined in `GLOSSARY.md`: GeometryStore, Catalog,
DayHikeGeometry, LandCover, ClimateNormal, DEM. Respects ADR-0008 (local rasters
sampled after ingest), ADR-0010 (the climate slice stays in the Catalog), and
ADR-0005 (large extracts must stay workable). Origin: code review of the
`landcover-and-climate` branch, item 4.

## Problem Statement

`sample-landcover` and `sample-climate` walk the GeometryStore vertices in
chunks of 50,000. For every chunk they re-open every raster: all WorldCover
tiles for LandCover, all 36 WorldClim months (tavg, prec, srad × 12) for
ClimateNormal. They also test every point in the chunk against every raster's
bounds in Python.

On the committed fixture and the Croatia extract this is invisible. On a
continent-sized GeometryStore, with tens of millions of vertices, the same
rasters are opened hundreds of times each. The review estimated about 740
chunks × 36 opens for climate. WorldCover tiles are large COGs, so each reopen
also repeats header and overview setup. A Hiker running a Europe-wide
Catalog waits on file opens that carry no new information.

The cost has not been measured yet. The estimate comes from reading the code.

## Solution

A sampling run opens each raster once, keeps it open for the whole run, and
closes it at the end. The Hiker sees the same Catalog, the same
GeometryStore values, and the same commands. The run is faster, and the number
of raster opens no longer depends on the number of vertices.

If, after that change, a measurement on a real extract shows the per-point
sampling call is still the bottleneck, reading by window is a follow-up. It is
not part of this spec.

## User Stories

1. As a Hiker with a large Catalog, I want `sample-landcover` to open each WorldCover tile once, so that run time does not grow with the number of vertex chunks.
2. As a Hiker with a large Catalog, I want `sample-climate` to open each of the 36 monthly rasters once, so that a Europe-wide sample does not repeat 36 opens per chunk.
3. As a Hiker, I want `vertices.landcover` to hold exactly the same class codes as before, so that the speed-up does not change what I see in the GeometryStore.
4. As a Hiker, I want `vertex_climate` to hold exactly the same values as before, so that existing monthly means stay valid.
5. As a Hiker, I want Catalog `tree_fraction` … `snow_fraction` unchanged after the change, so that Ranking gives the same order as before.
6. As a Hiker, I want Catalog `temperature_c`, `precipitation_mm`, `solar_radiation`, `climate_months`, and the scalers unchanged, so that QueryMonth ranking stays the same.
7. As a Hiker, I want a vertex covered by two overlapping WorldCover tiles to keep the class from the first tile in sorted file order, so that results do not depend on how tiles are opened.
8. As a Hiker, I want a vertex missing any one of the 36 climate values to stay uncovered, so that a half-covered vertex never writes a partial year.
9. As a Hiker, I want WorldCover nodata (0) and any raster-declared nodata to stay uncovered, so that ocean and gaps are not counted as a class.
10. As a Hiker, I want the sampling commands to keep their names, flags, and defaults, so that my scripts and the README commands still work.
11. As a Hiker, I want `sample-landcover` and `sample-climate` to keep printing the sampled-vertex count and the stderr progress lines, so that I can still watch a long run.
12. As a Hiker, I want an empty raster directory to still return 0, and the incomplete-climate-set error and the zero-sample warning from the review fixes to stay, so that failure behaviour does not regress.
13. As a Hiker, I want a run that stops part way (error or Ctrl-C) to close every raster it opened, so that no file handles leak and the next run starts clean.
14. As a Hiker, I want a run to still reset the GeometryStore journal mode afterwards, so that other tools can open the file.
15. As a Hiker with a notebook, I want `sample_landcover_classes(raster_dir, points)` to keep working for a small point list, so that notebook cells that call it directly do not break.
16. As a maintainer, I want a test that counts raster opens over a multi-chunk run, so that a future change cannot quietly bring the per-chunk reopen back.
17. As a maintainer, I want the existing sampling tests to pass unchanged, so that I know behaviour is preserved without rewriting expectations.
18. As a maintainer, I want the README to say how many times each raster is opened (once per run), so that expectations about run time are written down next to the commands.
19. As a maintainer, I want a recorded before/after timing on a real extract, so that I know whether window reading is still needed.
20. As a maintainer, I want `sample-dem` left alone, so that this change stays scoped to the two samplers the review named.

## Implementation Decisions

- The two samplers (LandCover, ClimateNormal) each open their rasters once at
  the start of a run and hold them open until the run ends, closing them
  whether the run finishes or fails. The vertex chunk loop reuses those open
  datasets.
- The point-in-bounds test and per-point sampling stay as they are. Only the
  lifetime of the opened raster changes. This is the smallest change that
  removes the repeated opens and keeps the result bit-for-bit identical.
- LandCover keeps its first-tile-wins rule: tiles are tried in sorted file
  order, and a point already given a class is not sampled again.
- The public single-call helper that takes a directory and a point list keeps
  its signature and behaviour. It opens the directory's rasters for that one
  call. The samplers use a shared internal path that takes already-open
  datasets, so both routes run the same sampling logic and cannot drift.
- Climate keeps the all-36-or-skip rule per vertex, the missing-raster error,
  and the hike-level refresh into the Catalog. None of the Catalog or
  GeometryStore schema changes.
- No new dependency, no new command or flag, no change to ingest. Ranking still
  reads only the Catalog (ADR-0010).
- Vertex chunk size stays 50,000. Whether a different chunk size helps is not
  part of this change.
- A real timing is taken on the local extract before and after, and the
  numbers are written into the PRD's `## Comments` section, or the
  implementing issue's, so the result is on record.

## Testing Decisions

- A good test here checks what a caller sees: the values written to the
  GeometryStore and the Catalog, and the count of raster opens. It does not
  check which private helper was called.
- One seam: the public `sample_landcover` and `sample_climate` calls on a
  fixture Catalog with synthetic GeoTIFFs. All existing landcover and climate
  sampling tests already enter there, and keep passing unchanged as the
  behaviour-preserved check.
- One new test per sampler, on the same seam, that shrinks the vertex chunk
  size so the fixture splits into several chunks, wraps the raster-open call
  with a counter, and asserts each raster file is opened at most once for the
  run. The same test asserts the sampled values equal a single-chunk run.
- A test that an error part way through a run (a raster that fails to read)
  still closes the datasets already opened.
- Prior art: the sampler tests that write synthetic constant GeoTIFFs over the
  fixture vertex bbox, and the CLI tests that run `sample-landcover` and
  `sample-climate` through a subprocess. The raster-writing helpers there are
  reused.
- No test depends on wall-clock time. The timing is a manual measurement on the
  local extract, recorded in the comments.

## Out of Scope

- Reading rasters by window or block instead of per-point sampling. Revisit
  only if the recorded timing shows it is still needed.
- Sorting vertices by tile or by spatial order.
- Parallel sampling across processes or threads.
- `sample-dem` and `sample_elevations`, which already open one tile per bucket.
- The duplicated `_download`, `_ensure_column`, and `_rewrite_scaler` helpers
  in the landcover, climate, and dem modules.
- `fetch-climate` downloading three full global zips.
- Re-sampling that leaves old `vertex_climate` rows for vertices that lost
  coverage.
- Any change to what LandCover or ClimateNormal mean, or to the Catalog schema.

## Further Notes

- The 740 × 36 figure is from the review, not a measurement. The timing on the
  local `data/` extract compares baseline commit `316e27f` with the finished
  change (issue 04), so the gain can be stated honestly without blocking the
  code issues on a "before" run.
- Climate rasters are clipped to the fetch bbox, so each open is cheap. The
  larger saving is expected on WorldCover, whose tiles are 3° COGs and whose
  count grows with the Catalog bbox. If the timing shows climate gains little,
  say so rather than claim a number.
- Keeping up to about 36 climate files plus a few dozen WorldCover tiles open
  at once is well inside normal file-handle limits. A Catalog bbox much wider
  than Europe may need the limit checked.

## Comments

### Timing on the local extract (issue 04)

Measured 2026-10-15 on the local `data/` extract: 37,686,493 vertices in the
GeometryStore, 24 WorldCover tiles, 36 climate rasters (12 months x tavg, prec,
srad). "Before" is `316e27f` (baseline git worktree), "after" is `63ea739`
(develop HEAD with 01 and 02). Each run used a fresh copy-on-write copy of
`data/catalog.sqlite` and `data/catalog.geometry.sqlite`, and both sides read
the same `data/landcover` and `data/climate` directories. `data/` was not
modified. One run per cell, on an otherwise idle 8-core Mac.

| Command | Before `316e27f` | After `63ea739` |
| --- | --- | --- |
| `sample-landcover` (full run, 25,736,595 vertices covered, 756 progress chunks) | 750.6 s wall | 650.0 s wall |
| `sample-climate` (fixed 1,966 s window, not a full run) | 3,250,452 vertices sampled | 3,650,753 vertices sampled |

- **LandCover:** about 13% faster (100 s of 750 s). The before figure also
  includes a one-off `uv` environment build for the worktree (a minute or less,
  not separated out), so the true gain is somewhat smaller than 13%. User CPU
  time was 659.7 s before and 606.6 s after.
- **Climate:** a full run did not finish in reasonable time on either side.
  The after-run of the whole extract was still going after about 2 hours, and
  the before run extrapolates to hours as well (about 1,650 vertices/s, so more
  than 4 hours at 25.7M covered vertices). So both sides were cut at the same
  1,966 s window (the before run was stopped at 1,966 s; the after run
  was stopped at the same point). In that window the after side sampled about
  12% more vertices (3.65M vs 3.25M). That is a modest gain, in line with the
  expectation that clipped climate rasters are cheap to open. The run is
  dominated by the per-vertex cost: 36 `dataset.sample` calls per point and a
  Python bounds filter per raster per chunk.
- **Is window reading needed?** Opening rasters once helped LandCover by
  roughly a tenth and climate by roughly a tenth. Neither sampler is now
  limited by file opens. LandCover still takes about 11 minutes and climate
  would take hours on the full extract, so the remaining cost is per-point
  reads. Window reading (read a block of pixels per raster per chunk instead
  of one `sample` call per point) is the next lever and looks worth doing for
  climate in particular. It is not needed for LandCover to be usable. This
  should be a separate PRD. Caveat: single runs, no repeats, so differences of
  around 10% are indicative only.
