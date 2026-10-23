# 06: Round 2 rollout and report

**What to build:** The real local data follows the round-2 main walk end to
end, and the effect of both rounds is recorded.

1. Check that `data/baseline/` holds the 2026-10-21 Catalog (155,844
   DayHikes) and its similarity facts, and keep them. Check free disk. The
   GeometryStore is about 21 GB after sampling, and 63 GiB was free at
   planning. Stop with `blocked` if the re-ingest would not fit.
2. Run a fresh re-ingest from the hiking extract
   `data/osm/europe-latest.catalog.osm.pbf` into `data/catalog.sqlite`. Ingest
   no longer needs the unfiltered Europe file. Record the ingest report's
   counts and the time per stage (round 1: 2 h 10 min).
3. Run `sample-dem`, `sample-landcover` and `sample-climate` with the raster
   folders the README names. Round 1 took about 4 h 20 min in all, nearly
   4 h of it climate.
4. Rebuild the similarity facts by running notebook 05. Do not save its
   outputs over the committed notebook.
5. Re-pick any notebook 05a pair whose DayHike is no longer in the Catalog.
   Pick it from DayHikes in the new Catalog, on the same criteria the pair
   was chosen for, as the WaterSource re-ingest did. In round 1, 1966959
   Kurtagić - Vaganj - Vrdovo and 16364831 Mala Kočevska planinska pot
   (`WATER_MORE`) and 1550539 Welschlauf Südsteiermark (`TRAIL_MORE`) left as
   over 20 km.
6. Run all real-data checks (`DAYHIKE_REAL_DATA=1`). Round 1 took 87 min.
7. Add a short "Round 2" section to
   `.scratch/main-walk-r2/implementation_report.md`. The implement-issues
   orchestrator writes that file too: add the section, do not overwrite its
   other sections. The section holds:
   - DayHike counts by drop reason against the baseline (155,844) and
     round 1 (161,599)
   - each check's result
   - the number of line walks that fell back to the member-order walk
     (target 0; round 1: 1,395), and how they were counted
   - the loop/line share, including the gap-closed routes (round 1: 5,379
     read as lines)
   - the loop-start failure share (round 1: 4.81%)
   - ingest time by stage
   - anything that failed and why, with a few offending ids and a proposed
     follow-up
8. Delete `data/baseline/` only when every real-data check passes. Otherwise
   keep it and say why in the report.

User stories 11, 12 and 16 (real-data side), 17, 23, 24, 25.

**Blocked by:** 05 (Stale sweep)

**Status:** ready-for-agent

- [ ] The new Catalog, GeometryStore, DEM, LandCover and climate samples, and
      similarity facts are written.
- [ ] Ingest wrote no `*.access*` sidecar or access extract.
- [ ] Notebook 05a's pairs are Catalog DayHikes, and 05a passes every
      verification line on the real region.
- [ ] All fourteen real-data checks pass, or each failure is in the report
      with offending ids and a proposed follow-up.
- [ ] No line walk falls back to member order, or the count and examples are
      in the report.
- [ ] The loop-start failure share is under 2%.
- [ ] The "Round 2" section is committed. It is trackable: `.scratch/` is not
      gitignored, unlike `data/`.
- [ ] `data/baseline/` is deleted only after every check passed.

Prior art: the round-1 report's "Rollout results" (`.scratch/main-walk/`), the
commit "Describe WaterSources in the similarity notebooks after the
re-ingest" (re-picking 05a pairs), the README ingest and sampling sections,
and the real-data test module docstring (run order). Glossary: DayHike,
Catalog, GeometryStore, Leftover, Trailhead.
