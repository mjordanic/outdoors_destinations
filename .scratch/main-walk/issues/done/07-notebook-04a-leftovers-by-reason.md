# 07: Notebook 04a: Leftovers by reason

**What to build:** Notebook 04a's leftover section shows what the main-walk
rules did:

- One example DayHike per Leftover reason (`role`, `repeat`, `disconnected`,
  `dead_end`, `shortcut`), drawn as the main walk with its Leftovers in another
  colour, plus the Trailhead.
- Counts of DayHikes and metres per reason.
- The share of main pieces classed as loops versus lines.
- `unconnected_m` against `length_m`.

The markdown cites ADR-0012 instead of ADR-0009. When a GeometryStore has no
`leftover` column, the notebook prints that it needs a re-ingest, as it does
today for older stores.

User stories 32, 33.

**Blocked by:** 04 (Loops: shortcuts, stems, start near access)

**Status:** ready-for-agent

- [ ] The section runs on the committed fixture (whichever reasons the fixture
      has) and on the real data.
- [ ] Fixture relations exist so every reason appears at least once on the
      fixture, or the notebook says which reasons the fixture lacks.
- [ ] `test_demonstration_notebooks` passes.
- [ ] The notebook still does not write the Catalog or rank.

Prior art: notebook 04a's current "Leftover member ways" section and
`plot_dayhike_progress`, `test_demonstration_notebooks`. Glossary: Leftover,
DayHikeGeometry, GeometryStore.
