# Data licences

In short: the code is free to reuse under the MIT licence. The hike data is
built from open map, height, land-cover and climate data, and each source keeps
its own licence, listed below. The heights (FABDEM) allow only non-commercial
use.

The code in this repository is under the MIT licence ([LICENSE](LICENSE)).
The MIT licence covers the code only. The data the code reads, and the data it
builds from those inputs, keep the licences of their sources:

| Data | Licence | What it covers here |
| --- | --- | --- |
| OpenStreetMap | [ODbL 1.0](https://opendatacommons.org/licenses/odbl/) | The Catalog, the GeometryStore and the web serving export (the hike database files `dayhike` builds) are derivative databases of OpenStreetMap. Used publicly, they are offered under the ODbL; this code is the method that makes them. The OpenStreetMap part is also offered as a free download, `hikes-osm.sqlite` (`dayhike export-osm-download`, [docs/deploy.md](docs/deploy.md), section 11), linked from the Data and credits page: the OpenStreetMap hikes, their lines and the OpenStreetMap points along them. It leaves out heights and everything derived from them, land cover, climate, GeoNames places and Landmarks, and hikes from other sources, which keep their own licences below (the OSMF Collective Database guideline: those need not be shared alike). |
| Wikimedia Commons | CC0, CC BY or CC BY-SA, per file | Photos shown when a hike is opened. The image is not copied: the browser loads it from `upload.wikimedia.org`. The serving export stores the file name, author, licence and licence URL (`dayhike export-photos`). A file with any other licence is left out. |
| FABDEM V1-2 | [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) | University of Bristol data; `dayhike fetch-dem` downloads it from a third-party mirror on Hugging Face (`huggingface.co/datasets/pavelzaitsau/fabdem-v12`). Every elevation-derived value (heights, gain, profiles, ProfileShape, ClimbGrade, SteepestKm, SolarAspect) is adapted material, shared under CC BY-NC-SA 4.0 and only non-commercially. An export sampled from Copernicus DEM GLO-30 instead (ADR-0030) carries the Copernicus DEM licence notice in place of this row, and is free of the non-commercial limit only when every DEM step used it ([docs/deploy.md](docs/deploy.md)). |
| USGS 3DEP 1 arc-second | Public domain ([USGS copyrights and credits](https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits)) | Bare-earth heights of the hikes in the United States, read where a build holds its tiles beside the world DEM (ADR-0063); `dayhike fetch-dem --source 3dep` downloads it from the USGS staged-products bucket. Credit: U.S. Geological Survey. The values derived from it carry no licence limit of their own; a hike read from FABDEM keeps FABDEM's. |
| ESA WorldCover 2021 | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | Land-cover shares and the waterside share per hike. |
| Sport Ireland Trail Routes | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | Ireland's official walking trails, credit "Sport Ireland" ([data.gov.ie](https://data.gov.ie/dataset/getirelandactive_trailroutes)). Each is an external hike (ADR-0062): its own row with its own line, name and record link, never merged into an OpenStreetMap hike, so it is a separate layer beside the ODbL data and keeps CC BY 4.0. `dayhike fetch-external --source ie-sport-ireland` downloads it. |
| Jyväskylän yliopisto, Lipas Liikuntapaikat.fi | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | Finland's hiking routes and nature trails (types 4405 and 4404, status active) from [Lipas](https://www.jyu.fi/fi/avoimet-rajapinnat-ja-ladattavat-lipas-aineistot). The licence page asks for this credit. Each is an external hike (ADR-0062): its own row with its own line, name and record link, never merged into an OpenStreetMap hike, so it is a separate layer beside the ODbL data and keeps CC BY 4.0. `dayhike fetch-external --source fi` downloads it. |
| Turrutebasen (Kartverket) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | Norway's marked foot routes (`Fotrute`) from the national Geonorge file. The licence asks for credit to Kartverket. Each is an external hike (ADR-0062): its own row with its own line, name and route number (Turrutebasen has no page per route), never merged into an OpenStreetMap hike, so it is a separate layer beside the ODbL data and keeps CC BY 4.0. `dayhike fetch-external --source no` downloads it. |
| Randonnées du Parc national des Écrins | [Licence Ouverte 2.0](https://www.etalab.gouv.fr/licence-ouverte-open-licence) | The walks of the Parc national des Écrins' hiking routes, a schema_randonnee GeoJSON published on [data.gouv.fr](https://www.data.gouv.fr/fr/datasets/5bf88cf28b4c4151e7a136d2/). The licence asks for credit to the producer, the Parc national des Écrins. Each is an external hike (ADR-0062): its own row with its own line, name and record link, never merged into an OpenStreetMap hike, so it is a separate layer beside the ODbL data and keeps Licence Ouverte 2.0. The photos the records link are all rights reserved and are not used. Another French schema_randonnee dataset is used only when data.gouv.fr publishes it under Licence Ouverte 2.0 or the ODbL and its file follows the schema (`data/fr_randonnee.toml`, `docs/sources.csv`). `dayhike fetch-external --source fr` downloads them. |
| Percorsi escursionistici, Provincia Autonoma di Bolzano - Alto Adige | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) | South Tyrol's marked hiking trails (WFS layer `Routes-HikingTrails`) from the province's [open data portal](https://data.civis.bz.it/dataset/percorsi-escursionistici), where the dataset is listed as cc-zero; no credit is required, the province is named as a courtesy. `dayhike fetch-external --source it-odh-suedtirol` downloads it and refuses it if the portal no longer lists cc-zero. Each is an external hike (ADR-0062): its own row with its own line, name and dataset link, never merged into an OpenStreetMap hike, so it is a separate layer beside the ODbL data. The NOI Open Data Hub tourism API serves the same trails marked `Closed` and its CC0 hiking records carry no track, so that API is not read. |
| CHELSA V2.1 | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) | Per-hike monthly climate averages. |
| GeoNames `allCountries` | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | The towns and villages of the web demo's place search ("Find place"), served from `places.sqlite` in the serving export. |
| Wikimedia Commons | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) (structured data; each file keeps its own licence) | The Commons contributors count per hike: how many distinct uploaders put geotagged files within 150 m of its line, counted from the `geo_tags`, `page` and `image` database dumps by `dayhike count-commons`. Only the count is kept, never a file, name or account. It is shown beside the hike and never scored. |
| Natural Earth | Public domain | Country shapes of the coverage map, and the country each hike starts in. |
| Emergency numbers | Facts, each row citing its official source | `src/dayhike/data/emergency.csv`: per country the emergency numbers and mountain rescue service a hike's panel shows, gathered by hand from each country's government, emergency service or mountain rescue site, with the EU's 112 page or the UK government's travel advice only as an extra source (a row's `notes` says where no official source was found). |

The OpenStreetMap snapshots are downloaded from Geofabrik's extracts, which
carry the same ODbL. The web demo's base map tiles come from the OpenStreetMap tile server, under
its [tile usage policy](https://operations.osmfoundation.org/policies/tiles/).
When an export has no `places.sqlite`, its place search asks Nominatim, which
serves OpenStreetMap data.

Every data source the project uses, plans, is checking or rejected is listed,
with its licence, the reason and the date its licence was last read, in the
sources register [`docs/sources.csv`](docs/sources.csv) (ADR-0060). Each hike
records its source there.

The full citations and notices are in the technical docs'
[Attribution](docs/technical.md#attribution) section and on the web demo's Data and
credits page (`credits.html`).
