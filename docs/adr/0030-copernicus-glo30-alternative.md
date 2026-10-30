---
status: accepted (an alternative beside ADR-0008; FABDEM stays the default)
---

# Copernicus GLO-30 is a selectable elevation source beside FABDEM

FABDEM (ADR-0008) is CC BY-NC-SA 4.0: the site may not be commercial while its heights, climb, profiles, steepness and slope aspect come from it. Copernicus DEM GLO-30 is free for commercial use, under the Copernicus DEM licence with its notice and no-liability sentence. ADR-0008 rejected it because it is a surface model: tree tops and roofs are in it. The captain asked for it as an alternative, so the elevation source is now a choice, and FABDEM stays the default until the captain switches.

**How it is chosen.** `fetch-dem --source copernicus` downloads the GLO-30 1° Cloud Optimized GeoTIFFs from the AWS open bucket (`copernicus-dem-30m`, HTTPS, no account), skipping the 25 tiles GLO-30 Public leaves out (Armenia, Azerbaijan and their borders; FABDEM lacks them too). Every DEM step (`sample-dem`, `sample-trailgraph`, `build-similarity`) reads the source from the tile names in its `--dem` directory. A directory holding both is refused, so one DayHike is never measured on two maps. A Copernicus `sample-dem` writes `copernicus_attribution` into `geometry_meta` (a FABDEM run removes it); the serving export carries it in place of FABDEM's attribution, and the site then names Copernicus GLO-30 instead of FABDEM in the Measure sources, the score help, the credits page and the home page footer line (the server swaps the marked elevation credit). A FABDEM build gives the same values as before.

**The credit follows the last `sample-dem` only.** Nothing records which map each step or vertex used. A Copernicus export lifts FABDEM's non-commercial limit only when every DEM step (`sample-dem`, `sample-trailgraph`, `build-similarity`) ran with the same Copernicus `--dem` over tiles covering every DayHike; otherwise some values are still FABDEM, and its CC BY-NC-SA credit and the non-commercial limit still apply, even though the page names only Copernicus.

**A GLO-30 grid stops half a cell short of its tile's east and south edges** (its cells are centred on whole arc-seconds and the bucket drops the shared east column and south row), and it has no nodata value, so a read there gives 0. Such a point reads the tile's edge cell, about 15 m away, as `dem_heights` does for SolarAspect; read as 0, it gave hikes crossing a tile edge up to 1,800 m of false climb, and left uncovered it would keep its OSM or FABDEM height, measuring one hike on two maps.

**What it changes** (2,399 DayHikes lying wholly inside N45–47 E13–15: Julian and Kamnik Alps, Karavanke, Snežnik, Gorski kotar, Učka; one way; forest is a WorldCover tree share of at least 0.7):

| DayHikes | Elevation gain, Copernicus / FABDEM | ClimbGrade | SteepestKm | Highest point |
|---|---|---|---|---|
| forest (1,358) | median 1.02 (p10 0.97, p90 1.13) | 1.03 (0.96–1.18) | 1.01 (0.97–1.06) | +6 m |
| mixed (740) | 1.01 (0.97–1.12) | 1.03 (0.97–1.20) | 1.00 (0.97–1.06) | +1 m |
| open (301) | 1.00 (0.97–1.05) | 1.00 (0.96–1.08) | 1.00 (0.99–1.01) | +1 m |

The canopy false climb is real but small: about 2 m per km in forest, 0.1 m per km in the open. The 10 m hysteresis and the spike rule of the gain absorb most of it. Copernicus adds more than 50 m of gain to 8.7 % of forest hikes and more than 100 m to 1.3 %; the worst are long, flat-ish forest walks (Šetnica uz Rječinu 321 → 489 m, Lom – Jelendol – Dolga njiva 889 → 1,099 m). Summit routes barely move (Trnovo – Krn 2,720 → 2,730 m; Mašun – Veliki Snežnik 932 → 945 m).

**Mitigation, not done.** A 20 m hysteresis on Copernicus or a 150 m running median both undercount instead (gain 0.94 and 0.93 of FABDEM in the median, in the open too). A fix that only touches forest, such as a larger hysteresis on vertices whose WorldCover class is tree, would need its own spike.

**Considered Options**: replacing FABDEM outright (the captain asked for an alternative first); a commercial FABDEM licence (price unknown); national LiDAR terrain models (a patchwork of licences across 20+ countries); NASADEM/SRTM (older, voids in mountains).
