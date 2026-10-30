---
status: accepted (an alternative beside ADR-0008; FABDEM stays the default)
---

# Copernicus GLO-30 is a selectable elevation source beside FABDEM

FABDEM (ADR-0008) is CC BY-NC-SA 4.0: the site may not be commercial while its heights, climb, profiles, steepness and slope aspect come from it. Copernicus DEM GLO-30 is free for commercial use, under the Copernicus DEM licence with its notice and no-liability sentence. ADR-0008 rejected it because it is a surface model: tree tops and roofs are in it. The captain asked for it as an alternative, so the elevation source is now a choice, and FABDEM stays the default until the captain switches.

**How it is chosen.** `fetch-dem --source copernicus` downloads the GLO-30 1° Cloud Optimized GeoTIFFs from the AWS open bucket (`copernicus-dem-30m`, HTTPS, no account), skipping the 25 tiles GLO-30 Public leaves out (Armenia, Azerbaijan and their borders; FABDEM lacks them too). Every DEM step (`sample-dem`, `sample-trailgraph`, `build-similarity`) reads the source from the tile names in its `--dem` directory. A directory holding both is refused, so one DayHike is never measured on two maps. A Copernicus `sample-dem` writes `copernicus_attribution` into `geometry_meta` (a FABDEM run removes it); the serving export carries it in place of FABDEM's attribution, and the site then names Copernicus GLO-30 instead of FABDEM in the Measure sources, the score help, the credits page and the home page footer line (the server swaps the marked elevation credit). A FABDEM build gives the same values as before.

**The credit follows the last `sample-dem` only.** Nothing records which map each step or vertex used. A Copernicus export lifts FABDEM's non-commercial limit only when every DEM step (`sample-dem`, `sample-trailgraph`, `build-similarity`) ran with the same Copernicus `--dem` over tiles covering every DayHike; otherwise some values are still FABDEM, and its CC BY-NC-SA credit and the non-commercial limit still apply, even though the page names only Copernicus.

**A GLO-30 grid stops half a cell short of its tile's east and south edges** (its cells are centred on whole arc-seconds and the bucket drops the shared east column and south row), and it has no nodata value, so a read there gives 0. Such a point reads the tile's edge cell, about 15 m away, as `dem_heights` does for SolarAspect; read as 0, it gave hikes crossing a tile edge up to 1,800 m of false climb, and left uncovered it would keep its OSM or FABDEM height, measuring one hike on two maps.

**The canopy floor.** Measured on N45–47 E13–15 alone, Copernicus looked close to FABDEM (forest gain 1.02 of FABDEM in the median), but across Europe it adds more than 50 m of gain to one forest hike in seven (median 1.04, p90 1.35), most of it on flat-ish walks in forested gorges and along reservoirs. So a Copernicus `sample-dem` and `sample-trailgraph` read the ground under trees. A point is lowered toward its canopy floor by the share of WorldCover tree cells within 75 m of it (a square); a point with no tree cell that near reads as it is. The canopy floor is the least-squares plane through the GLO-30 cells within 75 m of the point's cell, taken at the point, plus the 35th percentile of those cells' heights above the plane: the slope stays and the tree tops go. It depends only on where the point is, so a ComposedHike that walks a DayHike's own vertices still reads the DayHike's heights. Both steps need the WorldCover tiles and refuse to run without them. Sampling with it takes about 47 µs a vertex (6.5 million vertices in 308 s). FABDEM is read as before, byte for byte.

**The drawn profile.** The page draws a Copernicus ElevationProfile resampled every 10 m, smoothed by a Savitzky-Golay quadratic over 90 m, then taken at its 128 points; a FABDEM one is drawn as before. Only the drawing changes: climb, descent and every fact read the heights as sampled. In metres drawn up and down per metre of height range, against FABDEM, the Europe sample's forest hikes draw 1.04 (p90 1.25) before, 1.00 (1.10) with the canopy floor and 0.95 (1.06) smoothed as well; FABDEM smoothed the same way would draw 0.96 (1.00).

**What it changes.** DayHikes with a FABDEM gain over 50 m, one way: the 2,368 lying wholly inside N45–47 E13–15 (Julian and Kamnik Alps, Karavanke, Snežnik, Gorski kotar, Učka) and 3,000 drawn at random across Europe. Forest is a WorldCover tree share of at least 0.7, open below 0.3. Each cell is Copernicus before → after the canopy floor, against FABDEM: median (p10–p90), and the share of hikes where Copernicus adds or removes more than 50 m.

| DayHikes | Elevation gain / FABDEM | Adds / removes > 50 m | ClimbGrade / FABDEM | SteepestKm / FABDEM | Highest point |
|---|---|---|---|---|---|
| N45–47 E13–15 forest (1,356) | 1.020 (0.97–1.14) → 0.995 (0.92–1.08) | 8.9 % / 0.8 % → 4.2 % / 4.1 % | 1.034 (0.96–1.18) → 0.994 (0.91–1.09) | 1.005 (0.97–1.07) → 0.995 (0.93–1.06) | +6.2 → +2.2 m |
| N45–47 E13–15 mixed (727) | 1.012 (0.97–1.16) → 0.998 (0.93–1.07) | 9.6 % / 1.5 % → 2.6 % / 5.8 % | 1.029 (0.97–1.19) → 0.999 (0.94–1.09) | 1.000 (0.97–1.08) → 0.996 (0.95–1.06) | +1.2 → +0.4 m |
| N45–47 E13–15 open (285) | 1.000 (0.97–1.07) → 1.000 (0.96–1.05) | 1.4 % / 0.4 % → 1.4 % / 1.1 % | 1.002 (0.96–1.08) → 1.001 (0.95–1.06) | 1.000 (0.99–1.01) → 1.000 (0.98–1.01) | +0.5 → +0.4 m |
| Europe forest (1,379) | 1.043 (0.96–1.35) → 1.005 (0.90–1.15) | 14.6 % / 0.6 % → 4.5 % / 2.9 % | 1.070 (0.97–1.38) → 1.017 (0.91–1.17) | 1.017 (0.97–1.14) → 1.003 (0.94–1.10) | +7.1 → +4.1 m |
| Europe mixed (1,139) | 1.060 (0.97–1.45) → 1.011 (0.92–1.19) | 17.6 % / 1.0 % → 3.1 % / 1.8 % | 1.101 (0.98–1.43) → 1.026 (0.94–1.20) | 1.017 (0.98–1.17) → 1.003 (0.95–1.12) | +3.3 → +1.3 m |
| Europe open (482) | 1.005 (0.97–1.14) → 1.001 (0.94–1.08) | 2.1 % / 0.8 % → 0.8 % / 1.9 % | 1.018 (0.98–1.18) → 1.007 (0.97–1.13) | 1.000 (0.98–1.04) → 0.999 (0.98–1.02) | +0.5 → +0.4 m |

Against the OSM `ascent` tags of 11,254 DayHikes (a numeric tag of at least 100 m, and a `distance` tag within 15 % of the one-way length):

| Gain from | Median abs(log(gain / tag)) | Median log(gain / tag) | Over 1.5 × the tag |
|---|---|---|---|
| FABDEM | 0.114 | +0.015 | 8.1 % |
| Copernicus before | 0.116 | +0.034 | 9.6 % |
| Copernicus after | 0.114 | +0.014 | 7.9 % |

| DayHike | FABDEM | Copernicus before | after |
|---|---|---|---|
| Šetnica uz Rječinu (7.9 km) | 321 m | 489 m | 398 m |
| Lom – Jelendol – Dolga njiva (13.8 km) | 889 m | 1,099 m | 964 m |
| Trnovo – Krn (16.0 km) | 2,720 m | 2,730 m | 2,671 m |
| Mašun – Veliki Snežnik (10.3 km) | 932 m | 945 m | 921 m |
| Rundwanderweg A7 Neyetalsperre (14.4 km) | 156 m | 592 m | 329 m |
| Náučný chodník Prielom Hornádu (19.1 km) | 672 m | 1,157 m | 781 m |
| Pot Triglavske Bistrice (11.0 km) | 437 m | 482 m | 436 m |

The forest gain is now within 1 % of FABDEM in the median, open terrain does not move in the median, and Copernicus is as close to the OSM tags as FABDEM. What is left runs both ways: steep forest routes lose a little (Trnovo – Krn −49 m; 4.1 % of the N45–47 E13–15 forest hikes lose more than 50 m), and the worst gorges and shores keep some false climb (Neyetalsperre is still twice FABDEM: its path runs between a flattened reservoir and a wooded bank, a cell or two apart).

**Tried for the floor, on the same sample.** The vertex's own WorldCover class as the gate, with the 20th–25th percentile over about 100 m: forest barely moved (Europe p90 1.26) while mixed and open hikes climbed more, because along forest paths and shores the class flickers between tree, grass and water, so neighbouring vertices alternated between floor and canopy (Trnovo – Krn about 2,900 m). A plain percentile without the plane: biased downhill on steep slopes (Europe forest p90 1.20 at best). Windows of 40–60 m or 90 m and the 10th, 20th or 50th percentile: no better than 75 m and the 35th. A 20 m hysteresis on Copernicus or a 150 m running median: both undercount in the open too (0.94 and 0.93 of FABDEM in the median).

**Considered Options**: replacing FABDEM outright (the captain asked for an alternative first); a commercial FABDEM licence (price unknown); national LiDAR terrain models (a patchwork of licences across 20+ countries); NASADEM/SRTM (older, voids in mountains).
