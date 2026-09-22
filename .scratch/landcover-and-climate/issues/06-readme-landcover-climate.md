# 06: Document LandCover and ClimateNormal like elevation

**What to build:** The README explains the new commands the way it explains
FABDEM elevation. Command order places the fetches and samples after ingest
and elevation. The file-location and gitignore lists name `data/landcover/`
and `data/climate/`. The GeometryStore section documents the per-vertex
land-cover class and the per-vertex monthly climate. A dedicated section
covers when to run the commands, what they download, what they write, a shell
example, and a Python example. The rank section shows a Ranking with a month
and without one. The Python API list names the new fetch, sample, and month
arguments. Attribution states the WorldCover credit and the WorldClim
citation beside the OSM attribution. Supported write paths name the new
sample commands beside ingest and DEM sampling.

**Status:** ready-for-agent

## Blocked by

- 02-fetch-worldcover
- 04-fetch-worldclim
- 05-notebook-examples

**Complexity:** standard

**Prior art:** README FABDEM section (when / pull / store / shell / Python),
notebook-link check in the notebook runner, PRD stories 30–35. ADR-0010.
WorldCover attribution and Fick and Hijmans 2017.

- [ ] Command order, file locations, and the gitignore list include both
      raster directories and the four new commands
- [ ] The GeometryStore section lists the land-cover class and monthly
      climate rows
- [ ] A dedicated section matches the FABDEM section's shape, including shell
      and Python
- [ ] Rank docs show `--month` and the no-month regime
- [ ] The Python API list includes the new seams and the optional month
- [ ] Attribution covers WorldCover and WorldClim
- [ ] The notebook walkthrough table describes the LandCover and QueryMonth
      examples
