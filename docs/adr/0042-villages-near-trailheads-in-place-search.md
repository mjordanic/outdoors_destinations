---
status: accepted (amends ADR-0029)
---

# "Find place" also finds the villages hikes start from

Hiker testing found that Névache, Montgenèvre, Wengen and Mürren gave "Place not found": `cities1000` (ADR-0029) holds only places of 1,000 people or more, and many trailhead villages are smaller (Wengen has no population in GeoNames at all). `places.sqlite` is now built from GeoNames `allCountries`: the towns as before (1,000 people or more, or a capital or seat down to `PPLA3`, within `PLACE_MARGIN_DEG` of the Trailheads' box, under every name), plus every smaller populated place (`P` class, not a section `PPLX`, nor historical, abandoned, destroyed or a religious centre) within `VILLAGE_M` (5 km) of a Trailhead, under its name and ASCII name only. A village's alternate names would take most of the file and are rarely what a Hiker types.

On the Europe Trailheads of the 2026-10-28 export (135,588) the file grows from 14.3 MB (78,902 places) to 31.8 MB (329,587 places, 262,962 under 1,000 people). In the serving image, 300 one- and two-letter queries (the per-client limit for a minute, and the heaviest prefixes) raise the container's memory 22 MB more than with the towns alone; a whole-name query costs about 1 ms either way.

**Considered Options**: `cities500` (finds only Montgenèvre of the four); every populated place in the box with all its names (110 MB, about 1 million places); the same with own names only (80 MB); 2 km or 10 km instead of 5 km (20 MB and 40 MB; at 2 km Montgenèvre is lost on the older export). Rejected.

**Consequences**: the export downloads `allCountries.zip` (about 420 MB) instead of `cities1000.zip` (11 MB), and reads it in about 30 s. A village farther than 5 km from every Trailhead is still not found, nor a village by an alternate name only. Many villages share a name and have no population, so among equal names the lowest GeoNames id comes first (Wengen in Switzerland before the German hamlets).
