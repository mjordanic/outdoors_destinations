---
status: accepted
---

# Commons contributors are counted beside a hike and never scored

HikesAlike shows what a hike is like, not whether people know it. Some hikers want the classic, others want to avoid the crowds, so how well known a hike is helps on its own. #156 measured open-data signals for Slovenia and #170 repeated the check in Colorado (`docs/future-work.md` section 15). Distinct Wikimedia Commons uploaders of geotagged files within 150 m of the line separated 30 well-known from 30 obscure hikes at 0.90 in Slovenia and at 0.64 in Colorado, where long stages through towns collect town photos. OSM route sharing fell from 0.82 to 0.49, since Colorado's relations rarely overlap.

**The index.** A DayHike's Commons contributors are the distinct uploaders of Commons files whose photo spot lies within 150 m of its line. A photo spot is the file's primary coordinate on Earth (`{{Location}}`, where the camera stood, not `{{Object location}}`). Both its latitude and longitude must have more than three decimals: a round coordinate marks a place, not a photo spot. The uploader is the account of the file's current version. Counting uploaders rather than files keeps one bulk upload from counting as many. Route sharing is not used: it measured Europe's dense networks of waymarked routes, not fame.

**A count, not a badge.** The page shows "N Commons contributors" in the hike panel's badges, and `/api/hikes/{id}` carries `commons_contributors`. Hovering the badge says what it counts and that it changes no score or ranking; the credits page names Commons as the source. No threshold makes a hike "well known": in Colorado a threshold of 3 marks 11 of 21 obscure long stages and misses 9 of 25 classics. There is no sort or filter by the count for now.

**Where Commons has no data.** A counted hike with no file nearby shows 0. Commons is sparse in the mountains (6 of the 25 Colorado classics have none), so 0 means nobody put a geotagged photo there, not that the hike is unknown; the hover text calls the count a hint. A Catalog that was never counted has no `commons_contributors` column; its export carries NULL, the API says `null`, and the panel shows no badge.

**Never scored.** The count lives in the Catalog's `dayhikes.commons_contributors` and in `hikes.sqlite` only. The similarity store, the facts, Twins and every Ranking never read it, and `tests/test_commons.py` ranks the same export before and after counting and gets the same order and scores.

**Built from the dumps, not the API.** `dayhike fetch-commons` downloads three dumps from `https://dumps.wikimedia.org/commonswiki/latest/` into `data/commons`: `geo_tags` (photo spots by page id, 0.8 GB), `page` (file name of each page id, 7.4 GB) and `image` (uploader of each file, 18.8 GB), about 27 GB gzipped in November 2026. `dayhike count-commons` streams them once, keeps the photo spots inside the box of the Catalog's lines, and writes each DayHike's count. It needs no Docker and no network.

**Privacy.** Uploaders are told apart by account number only in memory while counting. Nothing but the count per hike is written: no file name, user name or account is kept in the Catalog, a cache or the serving export.

**Licence.** Commons structured data and file metadata are CC0; a geotag and the fact that some account uploaded a file are data, not the file's licensed content. The count is a fact derived from them. `docs/sources.csv` lists Commons as used and `DATA-LICENSES.md` has its row.

**Considered Options**

- **A "well known" badge above a threshold.** Rejected: no threshold holds in both regions (above).
- **Route sharing as a second opinion.** Rejected: 0.49 in Colorado.
- **The count per km, or leaving out files inside towns.** Not measured yet. Either could lift the long-stage case; a later change can replace the count's definition here.
- **The Commons API (geosearch) per hike.** Rejected for a whole Catalog: it throttles anonymous clients, and #156 and #170 read it at its pace for 60 and 55 hikes only.
- **A cache of photo spots with uploader numbers, to recount without the dumps.** Rejected: it would keep accounts outside memory, and the count is rebuilt with the Catalog.
