# 03: README states that sampling opens each raster once per run

**What to build:** A Hiker reading the README next to the `sample-landcover`
and `sample-climate` commands learns that each WorldCover tile and each of the
36 climate rasters is opened once per run and held open until the run ends. So
run time grows with the number of vertices sampled, not with repeated file
opens. One short note, placed where LandCover and climate sampling are already
documented. Mention the open file-handle count (about 36 climate files plus the
WorldCover tiles covering the Catalog bbox) and that a bbox much wider than
Europe may need the handle limit checked.

**Blocked by:** 01 (LandCover opens tiles once), 02 (Climate opens rasters once)

**Status:** ready-for-agent

**Complexity:** low

**Prior art:** PRD `.scratch/sampler-raster-reuse/PRD.md` story 18 and Further
Notes. The README sections that already document FABDEM, LandCover, and climate
sampling (see the done `06-readme-landcover-climate` ticket in
`.scratch/landcover-and-climate/`).

- [ ] README says each sampling run opens each raster once, next to the
      sampling commands or the LandCover/climate section
- [ ] README notes the open file-handle count and when the limit may need checking
- [ ] No other README content changes
