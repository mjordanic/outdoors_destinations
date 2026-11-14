---
status: accepted
---

# Elevation gain smooths only the noisy stretches

On a path cut into a cliff, along an irrigation channel (a levada, bisse, Suone or Waal) or along a steep lake shore, the DEM reads the cells above and below the path in turn, 10–40 m apart, for hundreds of metres. Neither the spike rule (a run of up to 60 m) nor the 10 m dead-band removes that, so such paths climbed far more than they do: Bisse d'Ayent 990 m against OSM's 660 m, Marlinger Waalweg 475 m over a height range of 123 m ([future work §12](../future-work.md#12-elevation-gain-on-cliff-side-and-gorge-routes), issue #138). A 90 m moving average everywhere fixes those but trims real climbs by about 10 %, and #96 rejected it.

**The rule.** After the spike rule, `climb_and_descent` reads the kept heights as a line between the vertices (`_smooth_noisy`). The line's moving average at a place is its mean over 90 m centred there, less near an end, so the ends keep their heights and a straight slope is its own average. A vertex is noisy where the line, read at the vertices and every 10 m between them, strays 2.5 m or more on average from its moving average over 200 m either side. A noisy vertex takes its moving average; every other vertex keeps its height. Then the 10 m dead-band counts as before. The rule sees only the heights, so the Catalog's gain, the similarity facts, a ComposedHike's climb and the page's climb and descent all follow it, and a walk reversed still swaps climb and descent exactly (ADR-0048).

**What it gives** (measured on the profiles in the Europe GeometryStore, FABDEM; the error is |gain − tag| / tag, the tag the larger of OSM `ascent` and `descent`):

| Set | Before | After |
| --- | --- | --- |
| 26 OSM relations along a bisse, Suone, canal or steep lake shore (`tests/fixtures/cliff_canal_gain.json`): median / mean error | 16.3 % / 22.2 % | 9.8 % / 17.1 % |
| 2,926 random DayHikes: the median hike's change | — | 0.0 % (76 % unchanged, 7.7 % move by more than 5 %; the median gain 177 → 174 m) |
| 11,749 DayHikes with both tags: median error / share over 1.5 × the tag | 10.8 % / 3.3 % | 10.4 % / 2.8 % |
| Their 3,434 with a height range over 500 m: median error / median gain over tag | 5.4 % / 1.007 | 4.9 % / 0.996 |
| 104 Liechtenstein DayHikes with both tags (a fresh ingest of its extract): median error | 14.5 % | 17.7 % |

The error falls in every band of tagged gain (20–100 m up to over 800 m), and 55 % of the 3,289 tagged hikes the rule moves by more than 2 % come closer to their tag. Liechtenstein goes the other way: FABDEM's count there is already 14 % below its tags (median), so smoothing moves the two further apart. Some of the short wobbles the rule smooths there are real.

**What it does not fix.** Where the DEM is wrong by tens of metres over long, sparsely noded stretches the moving average is not enough: Levada da Ribeira da Janela (Madeira, nearly flat) still climbs 1,077 m (1,132 m before), Levada do Caldeirão Verde 950 m. Grand Bisse de St-Jean stays at 353 m against OSM's 190 m, and Chemin des Bisses 02 drops further below its tag (439 → 291 m against 600 m). A better DEM there (lidar where a country publishes one) is the open half of issue #138.

**Storage.** The facts version changes (the `NOISY_*` constants), so stored similarity facts are refused until `dayhike build-similarity` is run. The Catalog's `elevation_gain_m` follows the rule only when `dayhike sample-dem` is run again. The serving export carries the new climb and descent once both have run and it is made again. The serving export's size and format do not change.

**Considered Options**:

- A 90 m moving average everywhere: rejected again. On the 26 hikes the median error rises to 23 %, as flat lake-shore paths lose real gain, and the median random hike's gain falls by about 7 %.
- Detect the stretches from OSM (a `waterway=canal` beside the path, a levada name, a lake shore in WorldCover): rejected. `climb_and_descent` sees only heights, and a ComposedHike or a way back has no relation tags; the heights show the noise wherever it comes from.
- A dead-band that grows with the local noise (4 × the mean stray): about as good on the 26 hikes (9.5 % median), but it changes more random hikes and needs the same noise measure.
- Smooth only where the moving average is gentle (under 5–15 % over the 400 m): rejected. It leaves Liechtenstein nearly as far off (16–17 %) and helps the 26 hikes less.
- Measure the stray from a shorter moving average (30–60 m), for DEM-cell noise only: rejected. At the same number of hikes changed, the 26 hikes and Liechtenstein both come out the same or worse.
