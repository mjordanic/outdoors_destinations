# DayHike catalog

A local Catalog of DayHikes from OpenStreetMap, ranked by similarity to Seeds
you already know. DriveTime is an optional query-time filter from today's
Origin. DriveTime is not a FeatureVector column.

This is a Python package plus a SQLite file on disk. There is no web app, no
live Overpass query, and no database server to start. You ingest an OSM
snapshot once, then rank from that file.

## Install

```bash
uv sync
```

The package is `dayhike`. Tests: `uv run pytest`.

## How the pieces fit

```
OSM XML or PBF            SQLite Catalog              Ranking
(fixture or Geofabrik     data/catalog.sqlite         CLI or Python
 under data/osm/)      -> DayHikes, Trailheads,  ->  scores vs Seeds
                          AmenityFacts, scaler        optional DriveTime
                                                      via local Valhalla
```

1. **Ingest** (`build_catalog` / `dayhike ingest`) reads OSM **XML or PBF**, keeps named
   `route=hiking` relations whose geometry length is 2–25 km, and writes a
   SQLite Catalog.
2. **Rank** (`rank_dayhikes` / `dayhike rank`) compares other DayHikes to Seeds
   you name. Similarity is cosine distance of scaled FeatureVectors (length,
   elevation when OSM `ele` exists, Difficulty, amenities near the Trailhead).
3. **Filter** (`filter_by_drive_time`) is optional. It asks a **local Valhalla**
   HTTP service how long the drive is from today's Origin to each Trailhead and
   drops anything over the max (or unreachable). Ranking without Origin never
   talks to Valhalla.

Nothing is fetched from the network at rank time except that optional Valhalla
call on your machine.

## Where files live

| Path | What it is | In git? |
| --- | --- | --- |
| `tests/fixtures/catalog.osm` | Tiny OSM XML **oracle** for tests and demos | yes |
| `data/catalog.sqlite` | The Catalog you work with (SQLite database) | **gitignored** |
| `data/osm/` | Geofabrik / osmium extracts you download | gitignored |
| `data/valhalla/` | OSM PBF + Valhalla routing tiles Docker builds | gitignored |
| `data/valhalla/compose.yaml` | How to run Valhalla | yes |
| `data/valhalla/*.osm.pbf` | Road extract for tiles | **gitignored** |

`uv sync` does **not** download OSM and does **not** install Valhalla. Tests
never read `data/` and never start Docker. They rebuild a Catalog in a temp
directory from the committed fixture.

## The fixture is slim on purpose

`tests/fixtures/catalog.osm` is not a Croatia extract. It is a hand-built OSM
XML file with five hiking relations so ingest and ranking have a known answer:

| OSM relation | Name | What happens |
| --- | --- | --- |
| 1001 | Ridge Walk | **Kept.** ~10 km, `sac_scale=mountain_hiking`, Hut/cafe/parking/water within 1 km of the Trailhead, elevation tags on nodes. |
| 1002 | Valley Path | **Kept.** ~5 km, no `sac_scale`, no nearby amenities, no `ele`. |
| 1003 | *(unnamed)* | Dropped (`dropped_unnamed`). |
| 1004 | Long Traverse | Dropped: ~30 km, outside the 2–25 km band (`dropped_length`). |
| 1005 | Via Dinarica Red | Dropped: `network=iwn` superroute (`dropped_network`). |

That is why `tests/fixtures/` looks small: the PRD uses it as the oracle, not as
the hiking dataset. A real Catalog comes from Geofabrik extracts (see below).

## 1. Build a Catalog from the fixture

```bash
uv sync
mkdir -p data
uv run dayhike ingest tests/fixtures/catalog.osm --catalog data/catalog.sqlite
```

Expected output (numbers are metres; length is a hair over 10 km / 5 km because
it is computed on the sphere):

```
data/catalog.sqlite
IngestReport(kept=2, dropped_unnamed=1, dropped_length=1, dropped_network=1)
1001	Ridge Walk	10000.00…
1002	Valley Path	5000.00…
```

Same thing from Python:

```python
from dayhike import build_catalog

catalog = build_catalog("tests/fixtures/catalog.osm", "data/catalog.sqlite")
print(catalog.ingest_report)
for hike in catalog.dayhikes:
    print(hike.id, hike.name, hike.length_m, hike.difficulty.sac_scale)
```

`build_catalog` **replaces** the SQLite file if it already exists. XML and PBF
are both accepted (pyosmium). Multiple files (or a directory) merge into one
Catalog.

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
| `dayhikes` | OSM relation id, name, length_m, Difficulty (`sac_scale` or NULL) |
| `trailheads` | lat/lon used for amenities and DriveTime |
| `amenity_facts` | per DayHike: hut, parking, restaurant_cafe, drinking_water (count + nearest_m) |
| `feature_vectors` | length, elevation min/max/gain, Difficulty ordinal |
| `scaler` | mean and scale for each FeatureVector column |
| `labels` | vacant (`hiker_id`, `dayhike_id`, `score`, `created_at`) for a later website |
| `catalog_meta` | OSM attribution and the ingest report JSON |

Zero amenity counts are stored (Valley Path has four rows with `count=0`).
Missing elevation and missing `sac_scale` are SQL NULL, not zero.

### SQL

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
you also change `scaler`. The supported write path is `build_catalog`.

## 3. Rank by Seed

Seed names must exist in the Catalog (unknown or ambiguous names fail; there
is no fuzzy lookup). The Seed itself is omitted from the result list.

```bash
uv run dayhike rank --catalog data/catalog.sqlite "Ridge Walk"
```

Prints DayHike id, name, and similarity score (tab-separated):

```
1002	Valley Path	-1.0
```

The fixture only has two DayHikes, so the only neighbor of Ridge Walk is Valley
Path. With two very different FeatureVectors the cosine score can be about
`-1`. That is expected on this toy Catalog, not a routing error.

Python:

```python
from dayhike import Catalog, rank_dayhikes

catalog = Catalog.open("data/catalog.sqlite")
ranking = rank_dayhikes(catalog, ["Ridge Walk"])
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
with `--valhalla-url`). See [docs/valhalla.md](docs/valhalla.md) and
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

**4. Ingest into the Catalog** (this overwrites `data/catalog.sqlite` if it
already exists)

```bash
uv run dayhike ingest data/osm/croatia-latest.osm.pbf \
  --catalog data/catalog.sqlite
```

Wait until it prints an `IngestReport` and a list of kept DayHikes. Unnamed
relations, routes outside 2–25 km, and iwn/nwn superroutes are counted as
dropped and are **not** DayHikes.

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

### Walkthrough C — browser only, then ingest

1. Open [Geofabrik Europe](https://download.geofabrik.de/europe.html).
2. Open a region page from the table above.
3. Download the file named `*-latest.osm.pbf` (not `.shp.zip`, not `.osm.bz2`
   unless you convert it).
4. Put it in `data/osm/` with the filename in the table (so Italy nord-est is
   `data/osm/nord-est-latest.osm.pbf`, not a `Download (1).pbf` in Downloads).
5. Run the ingest command from walkthrough A step 4.

### Optional: shrink a country PBF with osmium

A full country PBF includes roads, buildings, and everything else. Ingest can
use it, but RAM use is high. [osmium-tool](https://osmcode.org/osmium-tool/)
(`brew install osmium-tool` on macOS) can keep hiking routes and amenities:

```bash
osmium tags-filter data/osm/croatia-latest.osm.pbf \
  nwr/route=hiking \
  nwr/tourism=alpine_hut,wilderness_hut \
  nwr/amenity=parking,restaurant,cafe,drinking_water \
  -o data/osm/croatia-hiking.osm.pbf

uv run dayhike ingest data/osm/croatia-hiking.osm.pbf \
  --catalog data/catalog.sqlite
```

Keep the **unfiltered** PBF if you will also build Valhalla tiles (DriveTime
needs the road network). See **Valhalla (DriveTime only)** below.

### What ingest keeps

Same rules as the fixture: named `route=hiking` relations whose geometry is
2–25 km. Amenities on nodes, ways, and tagged relations within 1 km of the
Trailhead. The Trailhead is the LineString endpoint nearer a parking lot or
driveable highway within 1 km, otherwise the first vertex. Ways in a route
relation are reversed as needed so the geometry is one continuous line.

A hike that is not a named `route=hiking` relation in this Catalog cannot be
a Seed. Unnamed forest paths never appear.

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

### Same PBF kind as ingest — not the same folder

| | Catalog ingest | Valhalla tiles |
| --- | --- | --- |
| File | Geofabrik `*-latest.osm.pbf` (same downloads as above) | **Unfiltered** copy of that PBF |
| Directory | `data/osm/` | `data/valhalla/` |
| How many `.pbf` files | One or many (ingest merges) | **Exactly one** |
| Hiking-only `osmium tags-filter` output | OK | **No** — DriveTime needs roads |

Do **not** copy the whole `data/osm/` tree into `data/valhalla/`. If Valhalla
sees two or more extracts it warns
([valhalla#3925](https://github.com/valhalla/valhalla/issues/3925)) and can
build a bad graph.

`data/valhalla/` is Valhalla's workshop: it reads the PBF and **writes**
`valhalla_tiles/`, `valhalla.json`, hashes. Leave `compose.yaml` there.

### Walkthrough A — one country (start here)

Croatia only. Use the same file you already ingested.

**1. Confirm the unfiltered PBF exists**

```bash
ls -lh data/osm/croatia-latest.osm.pbf
```

If you only kept a `croatia-hiking.osm.pbf` from `tags-filter`, download the
full extract again into `data/osm/` (links in the table above).

**2. Put exactly that one file in the Valhalla directory**

```bash
mkdir -p data/valhalla
rm -f data/valhalla/*.osm.pbf
cp data/osm/croatia-latest.osm.pbf data/valhalla/croatia-latest.osm.pbf
ls data/valhalla/*.osm.pbf
```

You should see a **single** `.pbf`. Keep `compose.yaml`.

**3. Start Valhalla** (from the repo root; first run builds tiles)

```bash
docker compose -f data/valhalla/compose.yaml up
```

Wait until the image finishes building. Croatia is usually minutes; Alpine
extracts can take much longer and several GB of RAM. Leave this terminal
running. Later starts reuse `valhalla_tiles/` unless you change the PBF.

**4. Confirm it is up** (another terminal)

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

**5. Rank with Origin and max hours**

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
first.

That merge is the **osmium-tool** CLI (C++), not the Python `osmium` package
this repo already has (pyosmium, used only for ingest). `uv` cannot install
osmium-tool.

**1. Install osmium-tool** (global; macOS Homebrew)

```bash
brew install osmium-tool
osmium --version
```

Package: [osmium-tool](https://osmcode.org/osmium-tool/),
[Homebrew formula](https://formulae.brew.sh/formula/osmium-tool). Linux:
your distro package (often `osmium-tool`).

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

**3. Put only the merged file in `data/valhalla/` and rebuild tiles**

```bash
docker compose -f data/valhalla/compose.yaml down
rm -f data/valhalla/*.osm.pbf
cp data/osm/hr-si-at.osm.pbf data/valhalla/hr-si-at.osm.pbf
rm -rf data/valhalla/valhalla_tiles data/valhalla/file_hashes.txt
ls data/valhalla/*.osm.pbf
docker compose -f data/valhalla/compose.yaml up
```

If you skip deleting `valhalla_tiles/`, the image may keep an old graph.
Then confirm with `curl -s http://127.0.0.1:8002/status` and rank as in
walkthrough A step 5.

If the container logs
`Tile building using more than one osm.pbf extract is discouraged`, you still
have extra `.pbf` files in `data/valhalla/`. `ls` that directory and remove
them until one remains (plus `compose.yaml` and tile output).

## Python API

The same seams the CLI wraps:

- `build_catalog` — OSM XML or PBF snapshot(s) → Catalog of DayHikes
- `Catalog.open` — load an already-built SQLite Catalog
- `resolve_seed` — Seed name → DayHike id (must exist in the Catalog)
- `rank_dayhikes` — Catalog + Seeds → Ranking
- `filter_by_drive_time` — Ranking filtered by DriveTime from Origin
- `ValhallaMatrixClient` — HTTP adapter for `/sources_to_targets`

CLI: `dayhike ingest`, `dayhike fetch-extracts`, `dayhike rank`.

Docstrings on those functions use the glossary in `CONTEXT.md`.

## Demonstration notebooks

- [`notebooks/catalog-and-ranker.ipynb`](notebooks/catalog-and-ranker.ipynb) —
  ingest the fixture, inspect FeatureVectors, rank by Seed (including
  max-not-mean), unknown Seed failure, stubbed DriveTime.
- [`notebooks/working-with-the-catalog.ipynb`](notebooks/working-with-the-catalog.ipynb) —
  SQLite tables, `Catalog.open`, and how to work with DayHike objects.

Both run against the committed OSM fixture (no Geofabrik, no live Valhalla).
Open them with the project interpreter after `uv sync`.

```bash
uv run jupyter notebook notebooks/catalog-and-ranker.ipynb
```

## Attribution (ODbL)

OpenStreetMap data is © OpenStreetMap contributors, licensed under the Open
Database License (ODbL). The Catalog stores that attribution next to the
DayHikes.

## What is gitignored under `data/`

Bulky local files stay out of git:

- `data/catalog.sqlite` — Catalog built from ingest
- `data/osm/` — Geofabrik / osmium extracts
- `data/valhalla/` — routing tiles
- `data/*.pbf`, `data/*.tif`, `data/*.tiff`

The committed fixture lives under `tests/fixtures/`, not `data/`.
