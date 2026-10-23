# 05: Stale sweep after the main walk and parking-only access

**What to build:** Review the whole repo for parts made obsolete by the main
walk (ADR-0012) and parking-only access (tickets 01–04). Remove or rewrite
what is stale. The commit body lists every area checked and what changed in
it, or "nothing". Do not refactor working code.

Check at least:

- **Package code.** In the ingest and catalog modules, the CLI options and
  their help text, look for constants, helpers, parameters, docstrings and
  comments that have no caller left or still describe road access. One
  example is the Trailhead access radius comment "parking / driveable
  highway".
- **README.** The ingest, access and GeometryStore sections: the access
  radius, the osmium access extract, the `*.sqlite.access*` sidecars, the
  `access:` progress stage, and the paragraph on `in_chain`, the "connected
  chain" and ADR-0009.
- **Docs.** ADR-0005's access-clipping text gets a note that ADR-0012
  "Round 2" supersedes it. Accepted ADRs are records: add the note, keep the
  history. Check ADR cross-references, and future-work items that are now
  done or moot.
- **CONTEXT.md.** Terms such as Trailhead's "tagged access point".
- **Notebooks.** Markdown text in notebooks 04a, 05 and 05a that describes
  the one-pass chain, the "vertex chain" or road access.
- **Tests.** Tests and fixtures that nothing reads any more.

The old in-memory OSM parser has no caller in the package or tests. Only the
`.scratch/ingest-perf` profiling script uses it. It is pre-existing dead
code, so list it in the commit body and do not delete it.

User stories 18, 19, 20, 21.

**Blocked by:** 02 (Dead-end tip join), 03 (A cycle hanging off a line is a
dead end), 04 (Real-data checks agree with ingest)

**Status:** ready-for-agent

- [ ] No README section, doc, docstring, comment or notebook text describes
      drivable-road access points, the access extract, the one-pass chain or
      ADR-0009 as current.
- [ ] Tickets 01–04 and the main walk leave no constant, helper, parameter or
      fixture without a caller. Pre-existing dead code is listed in the
      commit body, not deleted.
- [ ] The commit body lists each area above with what changed, or "nothing".
- [ ] `uv run pytest` passes, including the notebook tests.

Prior art: ADR-0012 (with its "Round 2" note), the `CONTEXT.md` Trailhead
entry, the README ingest steps, `test_demonstration_notebooks`. Glossary:
Trailhead, DayHikeGeometry, Leftover, GeometryStore.
