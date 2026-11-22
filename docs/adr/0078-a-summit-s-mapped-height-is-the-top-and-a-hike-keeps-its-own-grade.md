---
status: accepted (amends ADR-0019)
---

# A summit's mapped height is the top, and a hike keeps its own grade

Round 4 of the visitor test found two figures off on famous peaks.

**Triglav's top read 2,801 m; the summit is 2,864 m.** Every hike to the summit walks to within 2 m of the
peak node, but Copernicus GLO-30 reads the summit's cell as 2,801 m, and no cell within 80 m is higher: a
30 m cell flattens a sharp top. The DEM cannot give the summit; OSM can: the peak node has `ele=2864`.
Now `build-similarity` takes a walk's highest point as the highest of its vertices, or the mapped height of
a Peak it summits (within 50 m of the line, as `peak_summited` counts it) when that is higher, but at most
100 m higher (`SUMMIT_TRUST_M`: an `ele` in feet read as metres, or a typo, is further). A ComposedHike is
measured the same way. Triglav's hikes top at 2,864 m, Mount Whitney's at 4,420 m (DEM 4,409 m),
Galdhøpiggen's at 2,469 m (DEM 2,459 m). On a sample of 15,000 DayHikes, 14 % gain a higher top: by 8 m
at the median, 28 m at the 90th percentile, 2.4 % by 20 m or more. Climb, the ElevationProfile and the
ProfileShape still read the DEM: one mapped point is not a profile.

**Mount Whitney Trail read T5; every way of it is `sac_scale=hiking`.** ADR-0019 let the hike's facts with
the way back take the way back's Difficulty. The shortest way back from the summit found the Mountaineer's
Route (T5), so the card showed T5 for a T1 trail; Besseggen showed T6 the same way. A grade describes the
walk the hike names: Difficulty with the way back now stays the hike's own, for a Catalog DayHike in
`build-similarity` and for a ComposedHike at query time, as length, climb and the rest still add the way
back. Which way back is found is not changed here.

**Consequences.** `build-similarity` again (its facts version changes), then `export-serving`; no re-ingest.

**Considered Options.** Take the highest DEM cell near the summit (rejected: at Triglav none is higher than
2,801 m). Raise the summit vertex's height (rejected: one 63 m step would be a spike to the climb and the
profile, and the climb rule drops spikes).
