---
status: accepted (amends ADR-0017's labels and ADR-0066's names and floor)
---

# Every hike name in a build is unique

The 48-state US preview (2026-11-17, 61,355 hikes) showed two naming faults. Names repeat: 2,885 names were shared by 8,883 hikes (14 %), 3,066 of them relation hikes ("Blue Trail" 127 times, "Hike near Somers" 10 times), 5,800 named trails and 17 stages, although named trails already added "near {place}" to a shared name. And some names name nothing: 2.6 % of named trails were called things like "Road", "Trail", "Forest Route 2165", "FS 700D" or "392". The captain: "Names must be unique. If there is a dummy name like Road, you can use our naming strategy of closest town or location and code."

**Now** ingest labels each hike by its own rules (ADR-0017, ADR-0039, ADR-0040, ADR-0062, ADR-0066, ADR-0067), then one last step, `dayhike.names.unique_names`, runs over every hike of the build, of every kind, Europe and US alike:

- **Placeholders.** A name of only road and trail words and codes (`is_placeholder`: `PLACEHOLDER_WORDS` such as road, rd, trail, path, loop, route, forest, fs, fr, connector, spur, firebreak, unnamed; a code is a word with a digit or a single letter) says nothing about where a hike is. It becomes "Hike near {place}", with the old name in brackets as its code when it holds a number: "Road" → "Hike near Concord", "Forest Route 2165" → "Hike near Idyllwild (Forest Route 2165)". A name that starts with a code and has a word too is a name ("1777 Trail", "4T Trail"); a designation puts its number last ("Trail 122"). A name with a word of its own stays a name, however road-like: "Pig Farm Road", "Old Tioga Road", "Forest Route 33E32 (Cannell Trail)". The "Hike" of ingest's own "Hike near {place}" counts as a placeholder, so such a label is moved like one when it is shared.
- **Shared names.** A name two or more hikes share (case ignored) keeps its words and adds where each starts: "Blue Trail near Somers". Every hike that shares it changes, of any kind: a named trail and a relation hike of one name are both placed.
- **The place.** The nearest town, village or hamlet within 5 km of the Trailhead (`PLACE_RADIUS_M`, as for ADR-0017's labels); else the nearest named Peak, Hut, Viewpoint or lake within 5 km ("location"); else the nearest town at any distance. A label still shared moves on to the next of these, unless every hike that shares it would move to one same place.
- **The code.** Labels still shared after that are numbered in id order: "Hike near Somers (1)", "Hike near Somers (2)".
- **Unique names stay.** A name no other hike has, and that is no placeholder, is kept as it is, and is never changed to make room for another hike's new label.

The step changes only `name`: ids, `name_origin` and the ingest report's NameOrigin counts are as before, so a `#hike=` link and a DayStage's "{tour}, stage k" (named before the step from the tour's label) stay. Named trails keep one rule of their own: a colour name ("Red Trail", "Blue") adds the nearest place even when unique, since it is a park's blaze. Their old rule for a shared name moved here, and "Trail" alone is now a placeholder.

**Named trails start at 1 km** (`MIN_TRAIL_LENGTH_M` in `dayhike.named_trails`), its own constant beside these names (the captain chose the 1 km minimum, 2026-11-17). Relation hikes and DayStages keep their own rules (ADR-0033, ADR-0040). The preview built no named trail under 1 km, so this changes no count today.

**Measured** on the US preview Catalog (a dry run of `unique_names` on its names, Trailheads and GeometryStore places, peaks, huts and viewpoints): every one of the 61,355 names is unique after it; 9,666 change (6,502 named trails, 3,147 relation hikes, 17 stages), 1,035 of them placeholders; 2,337 end in a number.

**Considered Options**: rename only named trails (a relation "Blue Trail" still repeats 127 times); drop placeholder hikes (they are walks, only badly named); a code from the OSM id ("Blue Trail #48399") or a hash (stable, but means nothing to a walker; the number only has to tell hikes near one place apart); a compass word ("north") (needs a centre to be north of, and does not make every label unique). Rejected.
