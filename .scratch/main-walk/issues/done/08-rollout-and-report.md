# 08: Rollout and report

**What to build:** The real local data follows the main walk end to end, and
the effect is recorded.

1. Copy `data/catalog.sqlite` and `data/catalog.similarity_facts.pkl` to
   `data/baseline/` (gitignored). Do not copy the 19 GB GeometryStore. The
   disk had about 68 GB free when this was planned, so check free space first
   and stop with `blocked` if the re-ingest would not fit.
2. Full re-ingest from `data/osm/europe-latest.catalog.osm.pbf` into
   `data/catalog.sqlite`. Keep the ingest report's counts (kept, dropped for
   length, dropped as superroute).
3. Run `sample-dem`, `sample-landcover` and `sample-climate` on it, with the
   raster folders the README names.
4. Rebuild the similarity facts by running notebook 05.
5. Run all real-data checks (`DAYHIKE_REAL_DATA=1`), including ticket 06's.
6. Write `.scratch/main-walk/implementation_report.md`:
   - DayHike count before (155,844) and after, by drop reason
   - the ids that left and joined, with reasons, from ticket 06
   - the PRD's "before" audit table next to the same measures after
   - Leftover metres and DayHikes per reason
   - the loop/line share
   - DayHikes whose length changed by more than 25%
   - the steps longer than 2 km left inside single ways (ferry, bus)
   - anything that failed and why

User stories 21, 22, 31, 34, 37.

**Blocked by:** 05 (SurfaceMix and TarmacApproach follow the walk), 06
(Opt-in real-data checks), 07 (Notebook 04a: Leftovers by reason)

**Status:** ready-for-agent

- [ ] `data/baseline/` holds the old Catalog and similarity facts before the
      re-ingest starts.
- [ ] The new Catalog, GeometryStore (with `leftover`, `walked_m`,
      `walk_order`), DEM, LandCover and climate samples, and similarity facts
      are written.
- [ ] All real-data checks pass, or each failure is in the report with the
      offending ids and a proposed follow-up.
- [ ] No DayHike left the Catalog for lack of an access point.
- [ ] `implementation_report.md` is committed. It is trackable: `.scratch/` is
      not gitignored, unlike `data/`.

Prior art: `.scratch/facet-similarity/implementation_report.md`, the README's
ingest and sampling sections, the real-data test module docstring (run order).
Glossary: DayHike, Catalog, GeometryStore, Leftover.
