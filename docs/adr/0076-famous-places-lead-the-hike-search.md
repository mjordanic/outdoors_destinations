---
status: accepted (amends ADR-0053 and ADR-0055)
---

# Famous places lead the hike search, and "Mt" and a country or state are understood

Round 4 of the visitor test found the hike search landing on the wrong place for famous names. "Matterhorn"
listed Matterhorn Trail in Oregon first, "Mount Rainier" hikes in a Maryland town of that name, "Grand Canyon"
a 1.6 km walk of that name near Cairo, New York, and "Half Dome" the 3.2 km Half Dome Trail before Half Dome via
Subdome. "Mt Whitney" and "Triglav, Slovenia" found nothing, though "Mount Whitney Trail" and "Triglav" did.

The Landmarks already put the famous place first among themselves (ADR-0055: the one with the most names). The
page listed every hike name found before any Landmark, so the Swiss Matterhorn came after 20 names.

**A Landmark named exactly the query leads the hike names when it is famous or the most photographed.**
`/api/landmarks` marks such a Landmark `leads`, and the page lists its hikes before the hike names; each hike
is still listed once, where it first comes. A Landmark leads when its name or one of its aliases is the query
and either:

- it is a peak, lake, pass or ridge with at least 10 names (`FAMOUS_NAMES`): 466 peaks, 188 lakes, 30 passes
  and 4 ridges of the review export, among them the Matterhorn (48), Mount Rainier (43), Mount Whitney (47),
  Half Dome (35), Triglav (36) and Lago di Garda. Towns are left out: one often has as many names in GeoNames
  as a famous peak (the town of Mount Rainier in Maryland has 19);
- or, of the Landmarks named so, its hikes have the most Commons contributors (ADR-0064), more than any of
  the hikes the name search ranks best (the same relevance as its first). The Grand Canyon itself is no
  GeoNames Landmark near the trails (its GeoNames valley point lies 170 km west of Grand Canyon Village);
  the town of Grand Canyon, Arizona, is, and its hikes have up to 41 contributors against the New York
  walk's none. Bled and St. Moritz lead so too.

**A hike to a summit from a car park comes before one starting far from any.** Among the hikes reaching the
top of a Peak named like the query (ADR-0053's summits first), and among a Peak Landmark's hikes reaching its
top, one starting more than 2 km from a car park (`FAR_PARKING_M`, the page's note "It starts … from the
nearest car park") comes after the rest. Half Dome Trail starts 4.6 km from one, Half Dome via Subdome 71 m.

**"Mt" is Mount and "St" is Saint (`SHORT_WORDS`).** Search writes them out, with or without a full stop, in
the query and in every hike name, so "Mt Whitney" finds Mount Whitney Trail and "Mount Hood" a name spelled
"Mt. Hood". A Landmark's names are matched as written, so its query is tried spelled each way.

**A country or US state at the end narrows the search.** After a comma ("Triglav, Slovenia", "Rainier,
Washington") the last words are always a place when they name one of the countries in `emergency.csv` or a
US state; without a comma ("Triglav Slovenia") only when the whole query finds no name. The words before it
are searched, keeping the hikes starting there (the country of `countries.npy`, else of the nearest town) and
the Landmarks whose nearest town is there.

**A US place says its state.** `places.sqlite` keeps each place's GeoNames admin1 code, for a US place its
state's postal code. A hike starting near a US town says "near Joseph, OR, US", and a Landmark its state.
A Landmark with no town within 30 km now says the country and state of the nearest town at any distance:
Mount Rainier's peak said nothing of where it is.

**Export.** The admin1 code is the only export change: run `dayhike export-places` again beside the export
(it rewrites `places.sqlite` and `landmarks.sqlite`; 1.5 min for `places.sqlite` here). No hike changes. Every
US hike's start near a town then says its state (104,178 of the review export's 889,525 hikes start in the United States); without the column
a server works as before and a state narrows a search to the United States only.

**Considered Options**:

- *Rank by Wikidata or Wikipedia tags.* The serving export keeps no such tag on a Landmark; adding them means
  ingesting them for every OSM point, a rebuild, where the names count is already there.
- *Interleave Landmarks and hike names by one score.* Hard to explain, and a name typed exactly would move
  down for any well-known peak with a word of it.
- *Count every town's names.* Mount Rainier, Maryland, would lead "Mount Rainier" beside the volcano.
