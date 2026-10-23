# DayHike catalog

A local Catalog of DayHikes from OpenStreetMap, ranked by similarity to Seeds
you already know. DriveTime is an optional query-time filter from today's
Origin. DriveTime is not a FeatureVector column.

This is a Python package plus SQLite files on disk. There is no web app, no
live Overpass query, and no database server to start. You ingest an OSM
snapshot once, optionally sample a DEM, LandCover, and climate onto the
geometries, then rank from those files.

## Install

```bash
uv sync
```

The package is `dayhike`. Tests: `uv run pytest`. Checks on the real data in
`data/` are opt-in: `DAYHIKE_REAL_DATA=1 uv run pytest tests/test_real_data.py`
(see [Re-ingest after an ingest change](#re-ingest-after-an-ingest-change)).

## How data is created (command order)

Nothing useful exists after `uv sync` except the tiny committed fixture.
Every real file under `data/` is built by a command. This is the order:

```
1. OSM snapshot          data/osm/*.osm.pbf          fetch-extracts (or curl)
2. Ingest                data/catalog.sqlite         dayhike ingest
                         data/catalog.geometry.sqlite     (always, same ingest)
3. DEM rasters           data/dem/*_FABDEM_V1-2.tif  dayhike fetch-dem
4. Sample heights        updates both SQLite files   dayhike sample-dem
5. LandCover rasters     data/landcover/*.tif        dayhike fetch-landcover
6. Sample LandCover      updates both SQLite files   dayhike sample-landcover
7. Climate rasters       data/climate/*.tif          dayhike fetch-climate
8. Sample climate        updates both SQLite files   dayhike sample-climate
9. Rank                  stdout scores               dayhike rank [--month]
10. Optional DriveTime   local Valhalla Docker       rank --origin --max-hours
```

```bash
uv sync
mkdir -p data/osm data/dem data/landcover data/climate

# 1. Snapshot (Geofabrik PBF). Ranking never talks to OpenStreetMap.
uv run dayhike fetch-extracts --dest data/osm

# 2. Catalog + GeometryStore. One command writes both SQLite files.
uv run dayhike ingest data/osm --catalog data/catalog.sqlite

# 3–4. Elevation. Skip these if OSM ele on nodes is enough (it usually is not).
uv run dayhike fetch-dem          # default bbox: Alps + Dinarides
uv run dayhike sample-dem         # writes heights into the two SQLite files

# 5–8. LandCover and climate. Skip these if length, elevation, and amenities are enough.
uv run dayhike fetch-landcover    # ESA WorldCover 2021 tiles
uv run dayhike sample-landcover
uv run dayhike fetch-climate      # WorldClim 2.1 monthly normals
uv run dayhike sample-climate

# 9. Rank by Seed (no Valhalla). Omit --month for the June–August climate regime.
uv run dayhike rank --catalog data/catalog.sqlite "Exact Name From The Catalog"

# 10. Optional: car DriveTime filter (needs Docker Valhalla, see below).
uv run dayhike rank --catalog data/catalog.sqlite \
  --origin 45.8,15.97 --max-hours 2 \
  "Exact Name From The Catalog"
```

What each step **does not** do:

| Step | Writes | Does not |
| --- | --- | --- |
| `fetch-extracts` | `data/osm/*.pbf` | Catalog, GeometryStore, DEM |
| `ingest` | Catalog + GeometryStore from the PBF | download OSM; download rasters; talk to Valhalla |
| `fetch-dem` | GeoTIFFs in `data/dem/` | touch SQLite; sample heights |
| `sample-dem` | `vertices.ele` + Catalog elevation + scaler | download rasters; rewrite hike list |
| `fetch-landcover` | GeoTIFFs in `data/landcover/` | touch SQLite; sample classes |
| `sample-landcover` | `vertices.landcover` + Catalog fractions + scaler | download rasters; rewrite hike list |
| `fetch-climate` | GeoTIFFs in `data/climate/` | touch SQLite; sample climate |
| `sample-climate` | `vertex_climate` + Catalog monthly means + scaler | download rasters; rewrite hike list |
| `rank` | nothing on disk | fetch OSM or rasters |

Ingest **replaces** the Catalog (it does not append). `sample-dem`,
`sample-landcover`, and `sample-climate` do **not** replace the Catalog: they
only overwrite sampled columns on hikes whose geometry sits on a downloaded
tile.

For a continent PBF (for example `europe-latest.osm.pbf`), ingest is the same
command. After ingest, widen raster coverage with
`dayhike fetch-dem --bounds south,west,north,east` (same `--bounds` on
`fetch-landcover` and `fetch-climate`) then sample again.

Nothing is fetched from the network at rank time except that optional Valhalla
call on your machine.

## Where files live

| Path | What it is | In git? |
| --- | --- | --- |
| `tests/fixtures/catalog.osm` | Tiny OSM XML **oracle** for tests and demos | yes |
| `data/catalog.sqlite` | The Catalog you work with (SQLite database) | **gitignored** |
| `data/catalog.geometry.sqlite` | GeometryStore: DayHikeGeometry + raw amenities | **gitignored** |
| `data/osm/` | Geofabrik / osmium extracts you download | gitignored |
| `data/valhalla/` | Road PBF + Valhalla routing tiles Docker builds | gitignored |
| `data/valhalla/compose.yaml` | How to run Valhalla | yes |
| `data/valhalla/prepare-roads.sh` | osmium filter: car roads only, no buildings | yes |
| `data/valhalla/prepare-highways.sh` | osmium filter: motorway/trunk/primary only | yes |
| `data/valhalla/mjolnir.drive-time.json` | Valhalla flags: no walk/bike/driveways | yes |
| `data/valhalla/*.osm.pbf` | Road extract for tiles | **gitignored** |
| `data/dem/` | FABDEM 1° GeoTIFFs for elevation | **gitignored** |
| `data/landcover/` | ESA WorldCover 2021 map GeoTIFFs | **gitignored** |
| `data/climate/` | WorldClim 2.1 monthly GeoTIFFs | **gitignored** |

`uv sync` does **not** download OSM and does **not** install Valhalla. Tests
never read `data/` and never start Docker. They rebuild a Catalog in a temp
directory from the committed fixture.

## The fixture is slim on purpose

`tests/fixtures/catalog.osm` is not a Croatia extract. It is a hand-built OSM
XML file with hiking relations so ingest and ranking have a known answer:

| OSM relation | Name | What happens |
| --- | --- | --- |
| 1001 | Ridge Walk | **Kept.** ~10 km, `sac_scale=mountain_hiking`, Hut/cafe/parking/water within 20 km of the Trailhead, elevation tags on nodes. A disconnected extra member is skipped (`unconnected_m` ≈ 1 km). |
| 1002 | Valley Path | **Kept.** ~5 km, no `sac_scale`, no nearby amenities, no `ele`. |
| 1003 | `relation:1003` | **Kept.** No OSM `name`. `name_origin=synthetic`. |
| 1004 | Long Traverse | Dropped: ~30 km, outside the 1–20 km band (`dropped_length`). |
| 1005 | Via Dinarica Red | Dropped: `network=iwn` superroute (`dropped_network`). |
| 1006 | Fifty Km Walk | Dropped: ~50 km (`dropped_length`). |
| 1007 | PS-1 | **Kept.** OSM `ref` only (`name_origin=osm_ref`). |

That is why `tests/fixtures/` looks small: the PRD uses it as the oracle, not as
the hiking dataset. A real Catalog comes from Geofabrik extracts (see below).

## 1. Build a Catalog from the fixture

```bash
uv sync
mkdir -p data
uv run dayhike ingest tests/fixtures/catalog.osm --catalog data/catalog.sqlite
```

Stdout is the Catalog path and the ingest report (numbers are metres; length is
a hair over 10 km / 5 km because it is computed on the sphere):

```
data/catalog.sqlite
IngestReport(kept=4, dropped_unnamed=0, dropped_length=2, dropped_network=1, named_osm_name=2, named_osm_ref=1, named_synthetic=1)
```

Progress (`filter:`, `relations:`, `ways:`, `nodes:`, `assemble:`, `write:`)
goes to **stderr**. The same command also writes
`data/catalog.geometry.sqlite`. Add `--list` to print kept DayHikes as
`id`, name, `length_m`. `--fresh` ignores a leftover work file and starts
again.

Same thing from Python:

```python
from dayhike import build_catalog

catalog = build_catalog("tests/fixtures/catalog.osm", "data/catalog.sqlite")
print(catalog.ingest_report)
for hike in catalog.dayhikes:
    print(hike.id, hike.name, hike.length_m, hike.difficulty.sac_scale)
```

`build_catalog` **replaces** the SQLite file if it already exists; it does not
append. The previous Catalog stays on disk until the new file is complete. XML
and PBF are both accepted (pyosmium). Multiple files (or a directory) in
**one** run become one Catalog. See **Ingest replaces the Catalog** below if
you ingest more than one country.

Walk through stages, keep/drop, and resume after a kill in
[`notebooks/01_raw_data_and_ingestion.ipynb`](notebooks/01_raw_data_and_ingestion.ipynb).

## 2. Open the Catalog later (it is a SQLite database)

After ingest, the Catalog is an ordinary SQLite file. You do not run Postgres
or SQLite server. Either load it through the package, or query SQL yourself.

### Python objects

```python
from dayhike import Catalog, resolve_seed

catalog = Catalog.open("data/catalog.sqlite")
print(catalog.attribution)
print(catalog.ingest_report)
print(catalog.scaler.feature_names)

ridge = next(hike for hike in catalog.dayhikes if hike.name == "Ridge Walk")
print(ridge.id, ridge.trailhead.latitude, ridge.trailhead.longitude)
print(ridge.trailhead.amenity_facts)
print(ridge.feature_vector)

print(resolve_seed(catalog, "ridge walk"))  # 1001; case-insensitive exact match
```

`Catalog.open` reads these tables into memory:

| Table | Contents |
| --- | --- |
| `dayhikes` | OSM relation id, Seed label, `name_origin`, length_m, Difficulty (`sac_scale` or NULL) |
| `trailheads` | lat/lon used for amenities and DriveTime |
| `amenity_facts` | per DayHike: hut, parking, restaurant_cafe, drinking_water (count + nearest_m) |
| `feature_vectors` | length, elevation min/max/gain, Difficulty ordinal, LandCover fractions, regime climate |
| `climate_months` | hike-level monthly temperature (°C), precipitation (mm), solar radiation |
| `scaler` | mean and scale for each FeatureVector column |
| `labels` | vacant (`hiker_id`, `dayhike_id`, `score`, `created_at`) for a later website |
| `catalog_meta` | OSM attribution and the ingest report JSON |

Zero amenity counts are stored (Valley Path has four rows with `count=0`).
Missing elevation, missing `sac_scale`, missing LandCover fractions, and
missing climate are SQL NULL, not zero.

Ranking reads **only** this file. Polylines and raw amenity points live in
the GeometryStore beside it.

## GeometryStore (written at ingest, not downloaded)

The GeometryStore is a sidecar SQLite file. It is **not** pulled from a
server. `dayhike ingest` always writes it next to the Catalog:
`data/catalog.sqlite` → `data/catalog.geometry.sqlite`
(`geometry_store_path_for`). There is no fetch-geometry command. Rank does
not read it. If you only have `catalog.sqlite`, re-run ingest; `sample-dem`,
`sample-landcover`, and `sample-climate` cannot create the sidecar by themselves.

It holds **Catalog DayHikes only** (dropped length/network relations are
absent):

| Table | Contents |
| --- | --- |
| `dayhikes` | id, Seed label, `name_origin`, length, `unconnected_m`, Difficulty, member list, relation tags |
| `vertices` | ordered DayHikeGeometry: `seq`, OSM `node_id`, lat, lon, `ele`, `landcover` |
| `vertex_climate` | per sampled vertex per month 1–12: temperature, precipitation, solar radiation |
| `ways` | member ways in listed order: node id lists, way tags, `in_chain` (1 when any part of the way is walked), `leftover` (the reason when not), `walked_m`, `walk_order` |
| `amenities` | raw points: OSM id, kind, lat, lon, tags |
| `geometry_meta` | OSM attribution; WorldCover and WorldClim after those samples |

DayHikeGeometry is the main walk of the member-way graph: each distinct member
way once, from the Trailhead, with no jump between consecutive ways except a
joined gap of 50 m or less. Member ways not on the walk are Leftovers. They
stay in `ways` with `in_chain = 0` and a `leftover` reason (`role`, `repeat`,
`disconnected`, `dead_end`, `shortcut`), and `dayhikes.unconnected_m` is their
distinct length. Catalog `length_m` and `vertices` follow the walk only. See
[`docs/adr/0012-main-walk-of-member-way-graph.md`](docs/adr/0012-main-walk-of-member-way-graph.md),
which supersedes ADR-0009.

At ingest, `vertices.ele` is OSM `ele` on that node, or NULL, and
`vertices.landcover` is NULL. `vertex_climate` is empty. After
`sample-dem`, vertices that sit on a FABDEM tile get raster heights in
metres. After `sample-landcover`, covered vertices get a WorldCover class
code (10 tree, 20 shrub, 30 grass, 60 bare, 70 permanent snow; other codes
stay in the leftover share). After `sample-climate`, each covered vertex
gets twelve monthly rows (°C, mm, kJ m⁻² day⁻¹). Vertices with no covering
tile stay unset. Amenities include hut, parking, restaurant/cafe, drinking
water, Viewpoint, Peak, Shelter, and the other WaterSources (spring, water tap,
water point). Catalog AmenityFacts still use only the first four kinds, counted
from the Trailhead.

```bash
sqlite3 data/catalog.geometry.sqlite \
  'SELECT dayhike_id, seq, latitude, longitude, ele, landcover FROM vertices LIMIT 5;'
sqlite3 data/catalog.geometry.sqlite \
  'SELECT dayhike_id, seq, month, temperature_c, precipitation_mm, solar_radiation FROM vertex_climate LIMIT 5;'
sqlite3 data/catalog.geometry.sqlite \
  'SELECT kind, COUNT(*) FROM amenities GROUP BY 1;'
```

Load the tables as GeoPandas in
[`notebooks/04a_geometry.ipynb`](notebooks/04a_geometry.ipynb). Invent
FeatureVector columns from this file in
[`notebooks/04_rank_feature_lab.ipynb`](notebooks/04_rank_feature_lab.ipynb).
`rank_dayhikes` does not read it.

Pandas, maps, and DayHike objects:
[`notebooks/02_catalog.ipynb`](notebooks/02_catalog.ipynb).

### Query the Catalog with SQL

```bash
uv run python -c "
import sqlite3
c = sqlite3.connect('data/catalog.sqlite')
c.row_factory = sqlite3.Row
for row in c.execute('SELECT id, name, length_m, difficulty FROM dayhikes'):
    print(dict(row))
for row in c.execute('SELECT * FROM amenity_facts WHERE dayhike_id = 1001'):
    print(dict(row))
"
```

Or with the `sqlite3` CLI if you have it:

```bash
sqlite3 data/catalog.sqlite 'SELECT id, name, length_m FROM dayhikes;'
```

Do not hand-edit FeatureVectors and expect Ranking to stay consistent unless
you also change `scaler`. The supported write paths are `build_catalog` /
`dayhike ingest` (full Catalog), `dayhike sample-dem` (elevation columns and
scaler), `dayhike sample-landcover` (LandCover fractions and scaler), and
`dayhike sample-climate` (climate columns, monthly table, and scaler).

## 3. Rank by Seed

Seed names must exist in the Catalog (unknown or ambiguous names fail; there
is no fuzzy lookup). The Seed itself is omitted from the result list.

Walk through one Seed, two Seeds (max-not-mean), unknown names, a stubbed
DriveTime filter, Ranking with no month and with QueryMonth 7, and LandCover
order in
[`notebooks/03_ranking.ipynb`](notebooks/03_ranking.ipynb). Try new columns in
[`notebooks/04_rank_feature_lab.ipynb`](notebooks/04_rank_feature_lab.ipynb).

```bash
uv run dayhike rank --catalog data/catalog.sqlite "Ridge Walk"
uv run dayhike rank --catalog data/catalog.sqlite --month 7 "Ridge Walk"
```

Prints DayHike id, name, and similarity score (tab-separated):

```
1002	Valley Path	<score>
```

The fixture Catalog now has several DayHikes, so Ridge Walk has more than one neighbour. Scores are cosine similarity after scaling.

Omit `--month` to score climate as the June–August regime (mean temperature,
mean monthly precipitation, June–August solar radiation). Pass `--month 7`
to fill those three slots from July. QueryMonth is an integer from 1 to 12.
A month before `sample-climate` fails with a message that climate has not
been sampled. Omitting the month never fails for that reason. LandCover
fractions do not change with the month.

Python:

```python
from dayhike import Catalog, rank_dayhikes

catalog = Catalog.open("data/catalog.sqlite")
ranking = rank_dayhikes(catalog, ["Ridge Walk"])
july = rank_dayhikes(catalog, ["Ridge Walk"], month=7)
for item in ranking.items:
    print(item.dayhike.id, item.dayhike.name, item.score)
```

Several Seeds: each candidate's score is the **maximum** similarity to any
Seed, not the mean. Ranking without Origin does not talk to Valhalla.

Unknown name:

```python
from dayhike import UnknownSeedName, rank_dayhikes
rank_dayhikes(catalog, ["Unknown Ridge"])  # raises UnknownSeedName
```

## 4. Optional Origin and max hours

Pass `--origin` (latitude,longitude) and `--max-hours` together to drop
DayHikes whose DriveTime from Origin exceeds the max:

```bash
uv run dayhike rank --catalog data/catalog.sqlite \
  --origin 45.8,15.97 --max-hours 2 \
  "Ridge Walk"
```

That needs a **running** local Valhalla on `http://127.0.0.1:8002` (override
with `--valhalla-url`), built from a car-only roads PBF
(`prepare-roads.sh`). See [docs/valhalla.md](docs/valhalla.md) and
[data/valhalla/compose.yaml](data/valhalla/compose.yaml).

Python, with the real client:

```python
from dayhike import (
    Catalog,
    Origin,
    ValhallaMatrixClient,
    filter_by_drive_time,
    rank_dayhikes,
)

catalog = Catalog.open("data/catalog.sqlite")
ranking = rank_dayhikes(catalog, ["Ridge Walk"])
filtered = filter_by_drive_time(
    ranking,
    Origin(latitude=45.8, longitude=15.97),
    max_hours=2,
    matrix_client=ValhallaMatrixClient("http://127.0.0.1:8002"),
)
for item in filtered.items:
    print(item.dayhike.name, item.score, item.drive_time.hours)
```

Unreachable Trailheads are dropped (no fake DriveTime). Tests stub this HTTP
API; they never build tiles.

## Pulling a real extract

Ranking never talks to OpenStreetMap. You download a **frozen snapshot**
(Geofabrik `.osm.pbf`), save it under gitignored `data/osm/`, then run
`dayhike ingest`. That writes `data/catalog.sqlite`. After that you rank from
the SQLite file only.

Do this from the **repo root** after `uv sync`.

### What you are downloading

[Geofabrik](https://www.geofabrik.de/) publishes daily OSM extracts. Index of
Europe: [download.geofabrik.de/europe.html](https://download.geofabrik.de/europe.html).
Each region page has a **`.osm.pbf`** file (binary OSM). That is the file
`dayhike ingest` reads. You do **not** need Overpass, a browser login, or an
API key.

`*-latest.osm.pbf` means “current daily extract,” not a special edition. Sizes
are on each Geofabrik page (typically tens to hundreds of MB per country;
Austria / Switzerland / Bayern / Italy nord-est are the large ones).

### Download links (PRD regions)

Use either the **direct PBF** (curl/wget/`fetch-extracts`) or the **region
page** (browser, shows size and `.md5`).

| Region | Region page | Direct `.osm.pbf` | Filename on disk |
| --- | --- | --- | --- |
| Croatia | [europe/croatia.html](https://download.geofabrik.de/europe/croatia.html) | [croatia-latest.osm.pbf](https://download.geofabrik.de/europe/croatia-latest.osm.pbf) | `croatia-latest.osm.pbf` |
| Slovenia | [europe/slovenia.html](https://download.geofabrik.de/europe/slovenia.html) | [slovenia-latest.osm.pbf](https://download.geofabrik.de/europe/slovenia-latest.osm.pbf) | `slovenia-latest.osm.pbf` |
| Bosnia and Herzegovina | [europe/bosnia-herzegovina.html](https://download.geofabrik.de/europe/bosnia-herzegovina.html) | [bosnia-herzegovina-latest.osm.pbf](https://download.geofabrik.de/europe/bosnia-herzegovina-latest.osm.pbf) | `bosnia-herzegovina-latest.osm.pbf` |
| Montenegro | [europe/montenegro.html](https://download.geofabrik.de/europe/montenegro.html) | [montenegro-latest.osm.pbf](https://download.geofabrik.de/europe/montenegro-latest.osm.pbf) | `montenegro-latest.osm.pbf` |
| Austria | [europe/austria.html](https://download.geofabrik.de/europe/austria.html) | [austria-latest.osm.pbf](https://download.geofabrik.de/europe/austria-latest.osm.pbf) | `austria-latest.osm.pbf` |
| Switzerland | [europe/switzerland.html](https://download.geofabrik.de/europe/switzerland.html) | [switzerland-latest.osm.pbf](https://download.geofabrik.de/europe/switzerland-latest.osm.pbf) | `switzerland-latest.osm.pbf` |
| Italy nord-est | [europe/italy/nord-est.html](https://download.geofabrik.de/europe/italy/nord-est.html) | [nord-est-latest.osm.pbf](https://download.geofabrik.de/europe/italy/nord-est-latest.osm.pbf) | `nord-est-latest.osm.pbf` |
| Germany Bayern | [europe/germany/bayern.html](https://download.geofabrik.de/europe/germany/bayern.html) | [bayern-latest.osm.pbf](https://download.geofabrik.de/europe/germany/bayern-latest.osm.pbf) | `bayern-latest.osm.pbf` |

Checksums (same name + `.md5`), e.g.
[croatia-latest.osm.pbf.md5](https://download.geofabrik.de/europe/croatia-latest.osm.pbf.md5).
Sub-region index for Italy:
[europe/italy.html](https://download.geofabrik.de/europe/italy.html). For
Germany: [europe/germany.html](https://download.geofabrik.de/europe/germany.html).

### Walkthrough A — one country (start here)

Croatia only. Enough to see real DayHike names and rank them.

**1. Create the download directory**

```bash
mkdir -p data/osm
```

**2. Download the PBF** (pick one)

Browser: open the Croatia page above → click `croatia-latest.osm.pbf` → move
the file to `data/osm/croatia-latest.osm.pbf`.

Or curl:

```bash
curl -L --fail --retry 3 -o data/osm/croatia-latest.osm.pbf \
  https://download.geofabrik.de/europe/croatia-latest.osm.pbf
```

Or wget:

```bash
wget -O data/osm/croatia-latest.osm.pbf \
  https://download.geofabrik.de/europe/croatia-latest.osm.pbf
```

**3. Confirm the file is a real extract, not an HTML error page**

```bash
ls -lh data/osm/croatia-latest.osm.pbf
file data/osm/croatia-latest.osm.pbf
```

You want tens of megabytes (or more) and a binary file. A few-kilobyte file
that `file` calls HTML or XML means the download failed.

Optional checksum:

```bash
curl -L -o data/osm/croatia-latest.osm.pbf.md5 \
  https://download.geofabrik.de/europe/croatia-latest.osm.pbf.md5
(cd data/osm && md5sum -c croatia-latest.osm.pbf.md5)
```

On macOS use `md5` / `shasum` against the hash in the `.md5` file if `md5sum`
is missing.

**4. Ingest into the Catalog** (this replaces `data/catalog.sqlite` when the
new file is complete)

```bash
uv run dayhike ingest data/osm/croatia-latest.osm.pbf \
  --catalog data/catalog.sqlite
```

Stderr shows stage lines while it runs. Stdout is the path and an
`IngestReport`. Routes outside 1–20 km and iwn/nwn superroutes are counted as
dropped and are **not** DayHikes. A missing OSM `name` is kept: the Seed label
is `name`, else `ref`, else `relation:{id}`, with `name_origin` recording which.
If the process
stops before `write:`, run the same command again; it resumes from
`data/catalog.sqlite.work`. `--fresh` discards that work file.

**5. See which names you can use as Seeds**

Those names must exist in the Catalog. Copy one exactly from this list:

```bash
sqlite3 data/catalog.sqlite \
  'SELECT id, name, round(length_m) FROM dayhikes ORDER BY name LIMIT 30;'
```

Or:

```bash
uv run python -c "
from dayhike import Catalog
c = Catalog.open('data/catalog.sqlite')
print(c.ingest_report)
print(c.attribution)
for h in c.dayhikes[:20]:
    print(h.id, h.name, round(h.length_m), h.difficulty.sac_scale)
"
```

**6. Rank** (no Valhalla)

Replace `"Exact Name From The List"` with a `name` from step 5:

```bash
uv run dayhike rank --catalog data/catalog.sqlite "Exact Name From The List"
```

If the name is unknown or matches two DayHikes, ingest did not fail — Seed
resolution failed. Use the SQL list; there is no fuzzy search.

**7–8. Elevation (optional, after ingest)**

OSM `ele` is sparse. `fetch-dem` downloads FABDEM GeoTIFFs into `data/dem/`
(default bbox is the PRD Alps+Dinarides). `sample-dem` then copies heights
onto GeometryStore vertices and Catalog elevation. See **Elevation from
FABDEM** below.

```bash
uv run dayhike fetch-dem
uv run dayhike sample-dem
```

**9–12. LandCover and climate (optional, after ingest)**

`fetch-landcover` / `sample-landcover` and `fetch-climate` / `sample-climate`
fill LandCover fractions and monthly climate the same way elevation is
filled. See **LandCover and ClimateNormal** below.

```bash
uv run dayhike fetch-landcover
uv run dayhike sample-landcover
uv run dayhike fetch-climate
uv run dayhike sample-climate
```

### Walkthrough B — every PRD region

Downloads all eight PBFs into `data/osm/`, then merges them in one ingest.
This is several GB and can take a long time and a lot of RAM.

```bash
mkdir -p data/osm
uv run dayhike fetch-extracts --dest data/osm
ls -lh data/osm
uv run dayhike ingest data/osm --catalog data/catalog.sqlite
```

`ingest` with a **directory** reads every `.osm` / `.pbf` in that folder.
Equivalent explicit files:

```bash
uv run dayhike ingest \
  data/osm/croatia-latest.osm.pbf \
  data/osm/slovenia-latest.osm.pbf \
  data/osm/bosnia-herzegovina-latest.osm.pbf \
  data/osm/montenegro-latest.osm.pbf \
  --catalog data/catalog.sqlite
```

Python (same downloads as `fetch-extracts`):

```python
from dayhike import build_catalog
from dayhike.extracts import fetch_geofabrik_extracts

paths = fetch_geofabrik_extracts("data/osm")
catalog = build_catalog(paths, "data/catalog.sqlite")
print(catalog.ingest_report)
```

To add one extra country later, download its PBF into `data/osm/` and run
`ingest` again on the **whole directory** (ingest replaces the SQLite file; it
does not append).

### Elevation from FABDEM

Ingest does **not** download a DEM. It only copies OSM `ele` tags (often
NULL) onto GeometryStore vertices and Catalog `feature_vectors`.

**When:** after a successful ingest, when both `data/catalog.sqlite` and
`data/catalog.geometry.sqlite` exist.

**Pull (rasters only):** `dayhike fetch-dem` downloads FABDEM V1-2 1°
GeoTIFFs into `data/dem/` (gitignored). Default `--bounds` is
`41.8,5.9,50.6,20.4` (Alps + Dinarides, same countries as
`fetch-extracts`). Tiles already on disk are skipped. 404s (ocean) are
skipped. This command does not open SQLite.

```bash
uv run dayhike fetch-dem
# Wider than the default, e.g. a Europe Catalog:
uv run dayhike fetch-dem --bounds 32.6,-31.3,71.2,46.4
```

`--dest` overrides `data/dem/`. FABDEM V1-2 is University of Bristol data,
CC BY-NC-SA 4.0 (non-commercial).

**Store and update SQLite:** `dayhike sample-dem` reads those GeoTIFFs and:

1. Writes sampled metres onto GeometryStore `vertices.ele` for vertices that
   fall on a downloaded tile. Vertices with no covering tile keep OSM `ele`
   or NULL.
2. Recomputes Catalog `feature_vectors.elevation_min_m` / `elevation_max_m` /
   `elevation_gain_m` for DayHikes that got DEM samples (cumulative uphill,
   not max−min).
3. Refits the Catalog `scaler` so Ranking stays consistent.

It does not add or drop DayHikes, does not change amenities, and does not
download tiles. `--catalog` defaults to `data/catalog.sqlite`; `--dem`
defaults to `data/dem/`.

```bash
uv run dayhike sample-dem
```

After this, `rank_dayhikes` uses DEM elevation. SolarAspect (which way the
geometry faces the sun) is **not** a Catalog column; prototype it in
[`notebooks/04_rank_feature_lab.ipynb`](notebooks/04_rank_feature_lab.ipynb).
The Weather Facet in
[`notebooks/05_similarity_facts.ipynb`](notebooks/05_similarity_facts.ipynb)
samples it as `southness` with a corrected formula (1 south, 0 north).

Python:

```python
from dayhike import fetch_fabdem, sample_dem
from dayhike.dem import DEFAULT_DEM_BOUNDS, DEFAULT_DEM_DIR

fetch_fabdem(DEFAULT_DEM_DIR, DEFAULT_DEM_BOUNDS)
sample_dem("data/catalog.sqlite", DEFAULT_DEM_DIR)
```

### LandCover and ClimateNormal

Ingest does **not** download WorldCover or WorldClim. A fresh Catalog has
NULL LandCover fractions, NULL regime climate, and no `climate_months` rows.
A fresh GeometryStore has NULL `vertices.landcover` and an empty
`vertex_climate` table. Ranking without a month still works on that Catalog.

**When:** after a successful ingest, when both `data/catalog.sqlite` and
`data/catalog.geometry.sqlite` exist. Same moment as FABDEM sampling. You
can sample DEM, LandCover, and climate in any order. `sample-climate` needs all
36 monthly rasters (tavg, prec, srad × 12) and stops with the missing names if
any are absent. A sample that covers no vertex prints a warning and `0`. Ranking with `--month`
needs climate sampled.

**Pull (rasters only):** `dayhike fetch-landcover` downloads ESA WorldCover
2021 v200 3×3° Map Cloud-Optimized GeoTIFFs into `data/landcover/`
(gitignored). `dayhike fetch-climate` downloads WorldClim 2.1 30 arc-second
monthly GeoTIFFs for mean temperature (°C), precipitation (mm), and solar
radiation (kJ m⁻² day⁻¹) into `data/climate/` (gitignored), from the three
base zips `wc2.1_30s_tavg`, `wc2.1_30s_prec`, and `wc2.1_30s_srad`, then
clips them to the bbox. Default `--bounds` is `41.8,5.9,50.6,20.4` (Alps +
Dinarides, same as `fetch-dem`). Tiles already on disk are skipped.
`fetch-landcover` skips a tile the server does not have, but fails if a tile
download errors. `fetch-climate` always downloads the three **global** base zips
in full (very large downloads) into a temporary directory before clipping, even for a
small bbox, and repeats that for any variable with a missing month. Neither
command opens SQLite.

```bash
uv run dayhike fetch-landcover
uv run dayhike fetch-climate
# Wider than the default, e.g. a Europe Catalog:
uv run dayhike fetch-landcover --bounds 32.6,-31.3,71.2,46.4
uv run dayhike fetch-climate --bounds 32.6,-31.3,71.2,46.4
```

`--dest` overrides `data/landcover/` or `data/climate/`. WorldCover is ESA
WorldCover 2021. WorldClim 2.1 normals are 1970–2000.

**Store and update SQLite:** `dayhike sample-landcover` and
`dayhike sample-climate` read those GeoTIFFs and:

1. `sample-landcover` writes the map class onto GeometryStore
   `vertices.landcover` for vertices that fall on a downloaded tile.
   Uncovered vertices stay NULL. It then writes Catalog
   `tree_fraction` / `shrub_fraction` / `grass_fraction` / `bare_fraction` /
   `snow_fraction` (each GeometryStore vertex counted once; crop, built-up,
   water, wetland, and moss stay in the leftover share) and refits the
   Catalog `scaler`.
2. `sample-climate` writes twelve months onto GeometryStore `vertex_climate`
   for covered vertices. Uncovered vertices get no climate rows. It then
   writes Catalog `climate_months` (hike-level means) and the three regime
   columns: June–August mean temperature, mean of the twelve monthly
   precipitations, June–August solar radiation. It refits the main scaler
   (regime climate in those three slots) and twelve month slices for the
   climate names only.

They do not add or drop DayHikes, do not change amenities, and do not
download tiles. `--catalog` defaults to `data/catalog.sqlite`.
`--landcover` defaults to `data/landcover/`; `--climate` defaults to
`data/climate/`. Re-ingest replaces both SQLite files and clears samples.

Each sampling run opens each raster once and holds it open until the run
ends, so run time grows with the number of vertices sampled, not with repeated
file opens. That means about 36 climate files (`sample-climate`) or one file
per WorldCover tile covering the Catalog bbox (`sample-landcover`) are open at
once. A bbox much wider than Europe may need your open file-handle limit
checked (`ulimit -n`).

```bash
uv run dayhike sample-landcover
uv run dayhike sample-climate
```

After this, `rank_dayhikes` uses LandCover fractions. With no QueryMonth it
uses the regime climate columns. With `month=7` it substitutes July's
hike-level means into the three climate slots and scales those three on
July's slice. LandCover does not change with the month.

Python:

```python
from dayhike import fetch_worldcover, sample_landcover, fetch_worldclim, sample_climate
from dayhike.climate import DEFAULT_CLIMATE_DIR
from dayhike.dem import DEFAULT_DEM_BOUNDS
from dayhike.landcover import DEFAULT_LANDCOVER_DIR

fetch_worldcover(DEFAULT_LANDCOVER_DIR, DEFAULT_DEM_BOUNDS)
sample_landcover("data/catalog.sqlite", DEFAULT_LANDCOVER_DIR)
fetch_worldclim(DEFAULT_CLIMATE_DIR, DEFAULT_DEM_BOUNDS)
sample_climate("data/catalog.sqlite", DEFAULT_CLIMATE_DIR)
```

### Walkthrough C — browser only, then ingest

1. Open [Geofabrik Europe](https://download.geofabrik.de/europe.html).
2. Open a region page from the table above.
3. Download the file named `*-latest.osm.pbf` (not `.shp.zip`, not `.osm.bz2`
   unless you convert it).
4. Put it in `data/osm/` with the filename in the table (so Italy nord-est is
   `data/osm/nord-est-latest.osm.pbf`, not a `Download (1).pbf` in Downloads).
5. Run the ingest command from walkthrough A step 4.

### How ingest runs

Ingest does not load the full OSM snapshot into RAM. A continent PBF (for example
Geofabrik `europe-latest.osm.pbf`) is a valid input.

1. **Filter.** If [osmium-tool](https://osmcode.org/osmium-tool/) is on `PATH`
   (`brew install osmium-tool` on macOS) and the input is PBF, ingest writes a
   hiking-and-amenity extract next to the source
   (`europe-latest.catalog.osm.pbf`). Referenced member ways and nodes are
   kept. Buildings, shops, and the rest of the map are dropped. The filter
   list goes next to the extract (`europe-latest.catalog.osm.pbf.filters`).
   The next ingest reuses the extract only when it is newer than the source and
   was made with the same filter list; otherwise it filters again. XML skips the
   filter. A file already named `*.catalog.osm.pbf` is read as-is. If `osmium`
   is missing, ingest reads the source file in several passes instead.
2. **Work file.** Relations, member ways, needed nodes, and amenities go to
   `data/catalog.sqlite.work` (same directory as the Catalog). Ingest reads only
   the hiking extract from step 1, or the source file itself when there is no
   extract. Access points are parking only (ADR-0012 "Round 2"), so there is
   no scan of drivable roads and no second extract.
3. **Progress.** Each stage prints to stderr: `filter`, `relations`, `ways`,
   `nodes`, `assemble`, `write`. After nodes, `assemble:` first loads the work
   file, then assembles each relation's main walk. Counts print every 100k
   objects during a load or scan. `osmium tags-filter` prints its own progress
   on stderr.
4. **Resume.** The same `dayhike ingest` command on the same inputs continues
   the work file. `--fresh` (or `build_catalog(..., fresh=True)`) starts over.
   A `progress` callback on `build_catalog` receives the same stage lines.
5. **Replace.** The new Catalog is written to a temp file and renamed onto
   `catalog.sqlite` only when it is complete. Then ingest writes the
   GeometryStore sidecar and deletes the work file. A kill during ingest
   leaves the previous Catalog in place.

See [`docs/adr/0005-streaming-ingest.md`](docs/adr/0005-streaming-ingest.md).

The unfiltered Geofabrik file stays in `data/osm/` as the source. Catalog
ingest may use the hiking extract; Valhalla must **not** — DriveTime needs
roads, and `prepare-roads.sh` builds a separate car-only PBF. See **Valhalla
(DriveTime only)** below.

Equivalent osmium filter if you want to run it yourself:

```bash
osmium tags-filter data/osm/croatia-latest.osm.pbf \
  r/route=hiking \
  nwr/tourism=alpine_hut,wilderness_hut,viewpoint \
  nwr/amenity=parking,restaurant,cafe,drinking_water,shelter,water_point \
  nwr/natural=peak,spring \
  nwr/man_made=water_tap \
  -o data/osm/croatia-latest.catalog.osm.pbf
```

### What ingest keeps

Same rules as the fixture: `route=hiking` relations whose geometry is 1–20 km.
Amenities on nodes, ways, and tagged relations: Catalog AmenityFacts still use
hut/parking/restaurant_cafe/drinking_water within 20 km of the Trailhead.
Viewpoint, Peak, Shelter, and the other WaterSources (`natural=spring`,
`man_made=water_tap`, `amenity=water_point`) are stored on GeometryStore only. A
spring, tap, or water point tagged `drinking_water=no` is not kept. A spring or
tap that is also `amenity=drinking_water` is a drinking-water point.

The geometry is the main walk of the relation's member ways (ADR-0012): each
distinct way once, side trips and shortcuts left out as Leftovers, and the
length band uses its length. The Trailhead is where the walk starts, chosen
from parking only. A line starts at the end nearer a parking point within 5 km,
or else at the end where member order starts it. A loop starts at its stem's
far end, or else at its vertex nearest parking within 5 km.

A hike that is not a `route=hiking` relation in this Catalog cannot be a Seed.
Unnamed forest paths (`highway=path` without a hiking relation) never appear.
Filter `name_origin = 'synthetic'` if you want to drop ingest-assigned labels.

### Ingest replaces the Catalog

Each `dayhike ingest` (and `build_catalog`) writes a **new** Catalog and
replaces the SQLite file when that write finishes. It does not append. Rows
from a previous ingest are not kept.

Several OSM files in **one** command become one Catalog. The same OSM relation
id in two extracts (for example a route on the Croatia–Slovenia border)
becomes **one** DayHike, not two rows.

```bash
# One Catalog with Croatia and Slovenia
uv run dayhike ingest \
  data/osm/croatia-latest.osm.pbf \
  data/osm/slovenia-latest.osm.pbf \
  --catalog data/catalog.sqlite

# Replaces that Catalog with Croatia only. Slovenia is gone.
uv run dayhike ingest data/osm/croatia-latest.osm.pbf \
  --catalog data/catalog.sqlite
```

To add a country later, put its PBF next to the others and ingest the **whole
directory** (or list every file you want) again.

### Re-ingest after an ingest change

When ingest changes what it keeps or how it walks a route (as when the main
walk replaced the one-pass chain, ADR-0012), an existing Catalog does not
change by itself. Ingest the same snapshot again, then sample again, because
ingest resets every vertex sample:

```bash
mkdir -p data/baseline   # optional: keep the old Catalog to compare against
cp -c data/catalog.sqlite data/catalog.similarity_facts.pkl data/baseline/
uv run dayhike ingest data/osm/europe-latest.catalog.osm.pbf --catalog data/catalog.sqlite --fresh
uv run dayhike sample-dem
uv run dayhike sample-landcover
uv run dayhike sample-climate
# run notebooks/05_similarity_facts.ipynb to rebuild the facts table, then:
DAYHIKE_REAL_DATA=1 uv run pytest tests/test_real_data.py
```

Ingest reads the hiking extract (`*.catalog.osm.pbf`) as is. Given the full
Europe file instead, it filters it first and caches the extract, again when the
filter list changed. The checks in `tests/test_real_data.py` look at the data
as it is (geometry, WaterSources, the main walk: steps, ways walked once,
length band, Leftover reasons, loop starts, notebook 05a's verification lines)
and, with `data/baseline/`, list the DayHikes that left and joined the Catalog;
each that left must have left by the main walk. On the Europe snapshot
(161,614 DayHikes) on an 8-core Mac this took: ingest 2 h 7 min, `sample-dem`
24 min, `sample-landcover` 12 min, `sample-climate` 3 h 54 min, notebook 05
2 min, the checks 1 h 42 min. The baseline is about 150 MB; delete
`data/baseline/` once the checks pass.

## Valhalla (DriveTime only)

Ranking by Seed does **not** need Valhalla. Valhalla is only for `--origin` /
`--max-hours`: car DriveTime from today's Origin to each Trailhead.

It is a **Docker** service, not a Python package. `uv sync` does not install
it. The image is
[`ghcr.io/gis-ops/docker-valhalla/valhalla:latest`](https://github.com/gis-ops/docker-valhalla).
Compose file: [`data/valhalla/compose.yaml`](data/valhalla/compose.yaml).
API used: `POST /sources_to_targets` (matrix, costing `auto`), not isochrones.
Default URL: `http://127.0.0.1:8002`. Tests never start Docker.

You need **Docker Desktop** (or another Docker Engine) running. Image docs:
[gis-ops/docker-valhalla](https://github.com/gis-ops/docker-valhalla). Extra
notes: [docs/valhalla.md](docs/valhalla.md).

### Car-only graph (what changed, and why)

DriveTime is car travel time. The first tile build is slow if Valhalla has to
parse a full Geofabrik extract (buildings, POIs, landuse, footways, …) and
build walk/bike edges we never query. Two layers strip that:

1. **An osmium prepare script** writes one PBF into `data/valhalla/`. Pick
   one; both drop buildings, amenities, landuse, footways, paths, steps,
   pedestrian streets, and cycleways.

   | Script | Keeps | Use for |
   | --- | --- | --- |
   | `prepare-roads.sh` → `*-roads.osm.pbf` | Motorway through residential, `service` (not driveways), `track`, ferries, turn restrictions | DriveTime to Trailheads |
   | `prepare-highways.sh` → `*-highways.osm.pbf` | `motorway`, `trunk`, `primary` (plus `_link` ramps) and ferries | Long-distance travel across Europe; much smaller file |

   `prepare-highways.sh` omits turn restrictions on purpose: osmium would
   otherwise pull the small `from`/`via`/`to` member ways into the extract.
   It will **not** reach most Trailheads. Use `prepare-roads.sh` for ranking
   with `--origin`.

2. **`data/valhalla/mjolnir.drive-time.json`** is merged into `valhalla.json`
   on every `docker compose up`. It sets `include_pedestrian`,
   `include_bicycle`, and `include_driveways` to `false`, so even an
   unfiltered PBF does not grow walk-only, bike-only, or driveway edges.

On-road cycle lanes (`cycleway=lane` on a car road) stay; they are tags on
roads we keep. Separate `highway=cycleway` ways are removed.

`data/osm/` stays the unfiltered source. Do not overwrite it with the roads
PBF. Do **not** copy the whole `data/osm/` tree into `data/valhalla/`: if
Valhalla sees two or more extracts it warns
([valhalla#3925](https://github.com/valhalla/valhalla/issues/3925)) and can
build a bad graph.

| | Catalog ingest | Valhalla tiles |
| --- | --- | --- |
| File | Geofabrik `*-latest.osm.pbf` (or a hiking-only filter) | **One** prepare-script output (`*-roads.osm.pbf` or `*-highways.osm.pbf`) |
| Directory | `data/osm/` | `data/valhalla/` |
| How many `.pbf` files | One or many (ingest merges) | **Exactly one** |
| Hiking-only `osmium tags-filter` output | OK | **No** — DriveTime needs roads |

`data/valhalla/` is Valhalla's workshop: it reads that PBF and **writes**
`valhalla_tiles/`, `valhalla.json`, hashes. Leave `compose.yaml`,
`prepare-roads.sh`, `prepare-highways.sh`, and `mjolnir.drive-time.json`
there.

If you already built tiles from a full extract, delete `valhalla_tiles/`,
`valhalla_tiles.tar`, and `file_hashes.txt` after switching to a roads PBF
(or after changing the mjolnir flags). The image will not rebuild just
because the JSON flags changed.

### How to restore a fuller graph

Need walk/bike costing, driveways, or the last metres of a path tagged only
as foot/cycle? Put the **unfiltered** Geofabrik file in `data/valhalla/`
(skip both prepare scripts), set all three flags in
`mjolnir.drive-time.json` to `true`, wipe `valhalla_tiles/`,
`valhalla_tiles.tar`, and `file_hashes.txt`, then `docker compose up` again.

Buildings and POIs never become routing edges. Copying an unfiltered PBF
without flipping those flags only slows the parse; it does not add walk/bike
edges. Flipping the flags without an unfiltered PBF cannot restore ways a
prepare script already dropped. To go from the highways extract back to
Trailhead DriveTime, run `prepare-roads.sh` on the unfiltered file in
`data/osm/` and wipe tiles.

### Walkthrough A — one country (start here)

Croatia only. Keep the unfiltered extract in `data/osm/`; filter a copy for
Valhalla.

**1. Confirm the unfiltered PBF exists**

```bash
ls -lh data/osm/croatia-latest.osm.pbf
```

If you only kept a `croatia-hiking.osm.pbf` from `tags-filter`, download the
full extract again into `data/osm/` (links in the table above).

**2. Install osmium-tool** (global; macOS Homebrew) if you do not have it

```bash
brew install osmium-tool
osmium --version
```

Package: [osmium-tool](https://osmcode.org/osmium-tool/),
[Homebrew formula](https://formulae.brew.sh/formula/osmium-tool). Linux:
your distro package (often `osmium-tool`). `uv` cannot install it.

**3. Write the car-only PBF into `data/valhalla/`**

```bash
mkdir -p data/valhalla
docker compose -f data/valhalla/compose.yaml down
./data/valhalla/prepare-roads.sh data/osm/croatia-latest.osm.pbf
ls data/valhalla/*.osm.pbf
```

You should see a **single** `.osm.pbf` (`croatia-latest-roads.osm.pbf`). Keep
`compose.yaml`, the prepare scripts, and `mjolnir.drive-time.json`.

**4. Start Valhalla** (from the repo root; first run builds tiles)

```bash
docker compose -f data/valhalla/compose.yaml up
```

Wait until the image finishes building. Croatia is usually minutes; Alpine
extracts can take much longer and several GB of RAM. Leave this terminal
running. Later starts reuse `valhalla_tiles/` unless you change the PBF.

**5. Confirm it is up** (another terminal)

```bash
curl -s http://127.0.0.1:8002/status
```

Optional probe of the same matrix API `dayhike` uses (`time` is seconds; `null`
means unreachable):

```bash
curl -s -H 'Content-Type: application/json' \
  -d '{"sources":[{"lat":45.8,"lon":15.97}],"targets":[{"lat":45.81,"lon":15.98}],"costing":"auto"}' \
  http://127.0.0.1:8002/sources_to_targets
```

**6. Rank with Origin and max hours**

`--origin` is `latitude,longitude` (WGS84). Use a Seed name from your Catalog
(step 5 of ingest), not a fixture name unless you ingested the fixture.

```bash
uv run dayhike rank --catalog data/catalog.sqlite \
  --origin 45.8,15.97 --max-hours 2 \
  "Exact Name From The List"
```

Output is tab-separated: DayHike id, name, similarity score, DriveTime in
**hours**. Unreachable Trailheads are omitted (no fake time).

`--valhalla-url` overrides `http://127.0.0.1:8002`. Stop with Ctrl+C in the
compose terminal, or:

```bash
docker compose -f data/valhalla/compose.yaml down
```

Tiles stay on disk.

### Walkthrough B — several countries (one merged PBF)

Valhalla cannot build from `croatia-latest.osm.pbf` + `slovenia-latest.osm.pbf`
+ `austria-latest.osm.pbf` sitting side by side. Merge them into **one** file
first, then run `prepare-roads.sh` on the merge.

That merge is the **osmium-tool** CLI (C++), not the Python `osmium` package
this repo already has (pyosmium, used only for ingest).

**1. Install osmium-tool** as in walkthrough A step 2.

**2. Merge the unfiltered extracts you already have in `data/osm/`**

```bash
osmium merge \
  data/osm/croatia-latest.osm.pbf \
  data/osm/slovenia-latest.osm.pbf \
  data/osm/austria-latest.osm.pbf \
  -o data/osm/hr-si-at.osm.pbf
```

Add or drop files to match the countries you drive. Output can be large (sum
of the inputs).

**3. Filter the merge, wipe old tiles, rebuild**

```bash
docker compose -f data/valhalla/compose.yaml down
./data/valhalla/prepare-roads.sh data/osm/hr-si-at.osm.pbf
rm -rf data/valhalla/valhalla_tiles data/valhalla/valhalla_tiles.tar \
  data/valhalla/file_hashes.txt
ls data/valhalla/*.osm.pbf
docker compose -f data/valhalla/compose.yaml up
```

If you skip deleting `valhalla_tiles/`, the image may keep an old graph.
Then confirm with `curl -s http://127.0.0.1:8002/status` and rank as in
walkthrough A step 6.

If the container logs
`Tile building using more than one osm.pbf extract is discouraged`, you still
have extra `.pbf` files in `data/valhalla/`. `ls` that directory and remove
them until one remains (plus `compose.yaml`, the prepare scripts,
`mjolnir.drive-time.json`, and tile output).

### Walkthrough C — long-distance Europe (highways only)

Same as A or B, but run `prepare-highways.sh` instead of `prepare-roads.sh`.
The output is `*-highways.osm.pbf`: motorway, trunk, primary, ramps, and
ferries. Secondary roads, towns, and Trailhead access tracks are gone, so
the file stays small enough for a multi-country or Europe extract.

```bash
docker compose -f data/valhalla/compose.yaml down
./data/valhalla/prepare-highways.sh data/osm/hr-si-at.osm.pbf
rm -rf data/valhalla/valhalla_tiles data/valhalla/valhalla_tiles.tar \
  data/valhalla/file_hashes.txt
docker compose -f data/valhalla/compose.yaml up
```

Do not use this graph to filter DayHikes by DriveTime to a Trailhead. Switch
back with `prepare-roads.sh` on the unfiltered `data/osm/` file and wipe
tiles.

## Python API

The same seams the CLI wraps:

- `build_catalog` — OSM XML or PBF snapshot(s) → Catalog of DayHikes
  (`progress=` for stage lines, `fresh=True` to ignore a work file)
- `Catalog.open` — load an already-built SQLite Catalog
- `resolve_seed` — Seed name → DayHike id (must exist in the Catalog)
- `rank_dayhikes` — Catalog + Seeds → Ranking (`month=` QueryMonth 1–12; omit for the June–August climate regime)
- `rank_scaled_rows` / `fit_scaler_from_matrix` / `scale_matrix` / `cosine_similarity` — same scoring on an experimental feature matrix
- `geometry_store_path_for` — sidecar path for DayHikeGeometry
- `fetch_fabdem` / `sample_dem` — local FABDEM tiles → GeometryStore vertices and Catalog elevation
- `fetch_worldcover` / `sample_landcover` — local WorldCover tiles → GeometryStore classes and Catalog fractions
- `fetch_worldclim` / `sample_climate` — local WorldClim tiles → GeometryStore monthly climate and Catalog means
- `filter_by_drive_time` — Ranking filtered by DriveTime from Origin
- `ValhallaMatrixClient` — HTTP adapter for `/sources_to_targets`

CLI: `dayhike ingest`, `dayhike fetch-extracts`, `dayhike fetch-dem`,
`dayhike sample-dem`, `dayhike fetch-landcover`, `dayhike sample-landcover`,
`dayhike fetch-climate`, `dayhike sample-climate`, `dayhike rank` (`--month`).

Docstrings on those functions use the glossary in `CONTEXT.md`.
Examples: [`01`](notebooks/01_raw_data_and_ingestion.ipynb) ingest,
[`02`](notebooks/02_catalog.ipynb) Catalog objects,
[`03`](notebooks/03_ranking.ipynb) ranking,
[`04`](notebooks/04_rank_feature_lab.ipynb) experimental columns,
[`04a`](notebooks/04a_geometry.ipynb) GeometryStore as GeoPandas.

## Notebook walkthrough

Open with the project interpreter after `uv sync`. Run them in order:

```bash
uv run jupyter notebook notebooks/01_raw_data_and_ingestion.ipynb
```

| Notebook | What you will find |
| --- | --- |
| [`notebooks/01_raw_data_and_ingestion.ipynb`](notebooks/01_raw_data_and_ingestion.ipynb) | OSM fixture vs a real snapshot, ingest stages, keep/drop, resume after a kill. |
| [`notebooks/02_catalog.ipynb`](notebooks/02_catalog.ipynb) | SQLite Catalog + GeometryStore: schema (including LandCover fractions, regime climate, and `climate_months`), pandas, maps, DayHike objects. Uses `data/catalog.sqlite` when present. |
| [`notebooks/03_ranking.ipynb`](notebooks/03_ranking.ipynb) | Production ranking: cosine after scaling, max-not-mean, unknown Seeds, stubbed DriveTime, Ranking with no month and with QueryMonth 7, LandCover order. |
| [`notebooks/04_rank_feature_lab.ipynb`](notebooks/04_rank_feature_lab.ipynb) | Sandbox to add FeatureVector columns, ablate them, and compare order to production. SolarAspect lives here. |
| [`notebooks/04a_geometry.ipynb`](notebooks/04a_geometry.ipynb) | GeometryStore tables as GeoPandas: vertices, amenities, DayHike lines, member ways, leftover `in_chain`, per-vertex land-cover classes and `vertex_climate` (synthetic rasters, no download). |
| [`notebooks/05_similarity_facts.ipynb`](notebooks/05_similarity_facts.ipynb) | Facet similarity design (ADR-0011, proposed): load the Slovenia + Croatia region of the GeometryStore with `dayhike.similarity.load_region`, then one section per Facet (Effort, Altitude, Profile, Landscape, Weather, Water, Facilities, Trail, RoundTrip) that extracts its facts, plots their spread and an example map, and states the rule and its data gaps (Water counts mapped drinking water, springs, taps and water points only). Writes `data/catalog.similarity_facts.pkl` (gitignored). |
| [`notebooks/05a_similarity.ipynb`](notebooks/05a_similarity.ipynb) | All nine FacetScores from the similarity facts table: measure spreads and constants, named similar/dissimilar pairs with `verify` PASS/FAIL lines, identity/symmetry/reversed checks, and the Profile lock-step vs DTW choice. Then one Seed ranked under a Facilities-heavy and an Altitude-heavy FacetWeights set (top five with per-Facet scores, combined score, coverage and a map each), a RoundTrip Seed with and without RoundTrip weight, two Seeds under the maximum rule, how often each Facet is missing, one verification summary table, and the design choices for review. Production Ranking is unchanged. |

`01` and `03` always rebuild the committed OSM fixture (no Geofabrik, no live
Valhalla). `02`, `04`, `04a`, `05`, and `05a` open your local Catalog and
GeometryStore when those files exist; `05a` reads the facts table `05` wrote. Tests force the fixture so they do not load a Europe Catalog.

## Attribution

OpenStreetMap data is © OpenStreetMap contributors, licensed under the Open
Database License (ODbL). The Catalog stores that attribution next to the
DayHikes.

ESA WorldCover 2021: © ESA WorldCover project 2021 / contains modified
Copernicus Sentinel data (2021) processed by ESA WorldCover consortium.
Written into GeometryStore `geometry_meta` when LandCover has been sampled.

WorldClim 2.1: Fick, S.E. and R.J. Hijmans, 2017. WorldClim 2: new 1km
spatial resolution climate surfaces for global land areas. International
Journal of Climatology 37 (12): 4302-4315. Written into GeometryStore
`geometry_meta` when climate has been sampled.

## What is gitignored under `data/`

Bulky local files stay out of git:

- `data/catalog.sqlite` — Catalog built from ingest
- `data/catalog.geometry.sqlite` — GeometryStore (DayHikeGeometry + raw amenities)
- `data/catalog.sqlite.work` — in-progress ingest (resume handle)
- `data/osm/` — Geofabrik / osmium extracts
- `data/dem/` — FABDEM GeoTIFFs
- `data/landcover/` — ESA WorldCover GeoTIFFs
- `data/climate/` — WorldClim monthly GeoTIFFs
- `data/valhalla/` — road PBF and routing tiles (`compose.yaml`,
  `prepare-roads.sh`, `prepare-highways.sh`, and `mjolnir.drive-time.json`
  are tracked)
- `data/*.pbf`, `data/*.tif`, `data/*.tiff`

The committed fixture lives under `tests/fixtures/`, not `data/`.
