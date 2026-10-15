# 04: Record before/after sampling timing on the local extract

**What to build:** A maintainer can read in the PRD how long `sample-landcover`
and `sample-climate` took on the real local extract before and after the
open-once change. With those numbers they can decide whether window reading is
still worth doing. "Before" is commit `316e27f` (the last commit before this
feature). "After" is the integration branch HEAD with 01 and 02 merged. Because
the baseline is a fixed commit, the timing can run after the change lands.

Run the baseline from a temporary `git worktree` at `316e27f`. Never check out
the old commit in the main checkout, and remove the worktree when done.

Time each command once per side with the same rasters and the same Catalog. Do
not touch the Hiker's working files: copy the `data/` Catalog and GeometryStore
into a scratch location for each run, and point both runs at the same
`data/landcover` and `data/climate` rasters. Record the vertex count, the number
of chunks, the raster counts (WorldCover tiles, climate months) and the
wall-clock time for each command on each side. If climate gains little,
because its rasters are clipped and cheap to open, say so plainly rather than
claim a speed-up.

**Blocked by:** 01 (LandCover opens tiles once), 02 (Climate opens rasters once)

**Status:** done

**Complexity:** low

**Prior art:** PRD `.scratch/sampler-raster-reuse/PRD.md` story 19,
Implementation Decisions (timing on record), Further Notes (the 740 × 36
estimate is from review, not measured). README sampling commands for the exact
invocations. The local extract under `data/` (gitignored, not committed).

- [ ] Before and after timings for both commands are measured on copies of the
      `data/` Catalog and GeometryStore; the originals in `data/` are untouched
- [ ] The PRD gains a `## Comments` entry with the commit hashes, vertex and
      chunk counts, raster counts, and the timings
- [ ] The entry states plainly whether window reading looks needed, including
      an honest statement if either sampler gained little
- [ ] No code changes in this ticket
