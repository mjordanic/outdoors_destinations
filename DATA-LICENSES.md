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
| OpenStreetMap | [ODbL 1.0](https://opendatacommons.org/licenses/odbl/) | The Catalog, the GeometryStore and the web serving export (the hike database files `dayhike` builds) are derivative databases of OpenStreetMap. Used publicly, they are offered under the ODbL; this code is the method that makes them. |
| FABDEM V1-2 | [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) | University of Bristol data; `dayhike fetch-dem` downloads it from a third-party mirror on Hugging Face (`huggingface.co/datasets/pavelzaitsau/fabdem-v12`). Every elevation-derived value (heights, gain, profiles, ProfileShape, ClimbGrade, SteepestKm, SolarAspect) is adapted material, shared under CC BY-NC-SA 4.0 and only non-commercially. An export sampled from Copernicus DEM GLO-30 instead (ADR-0030) carries the Copernicus DEM licence notice in place of this row, and is free of the non-commercial limit only when every DEM step used it ([docs/deploy.md](docs/deploy.md)). |
| ESA WorldCover 2021 | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | Land-cover shares and the waterside share per hike. |
| CHELSA V2.1 | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) | Per-hike monthly climate averages. |
| GeoNames `allCountries` | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | The towns and villages of the web demo's place search ("Find place"), served from `places.sqlite` in the serving export. |
| Natural Earth | Public domain | Country shapes of the coverage map. |

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
