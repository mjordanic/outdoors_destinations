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

1. **Ingest** (`build_catalog` / `dayhike ingest`) reads OSM **XML or PBF**, keeps
   `route=hiking` relations whose geometry length is 1–20 km, and writes a
   SQLite Catalog plus a GeometryStore sidecar (`data/catalog.geometry.sqlite`).
2. **Rank** (`rank_dayhikes` / `dayhike rank`) compares other DayHikes to Seeds
   you name. Similarity is cosine distance of scaled FeatureVectors (length,
   elevation min/max/gain, Difficulty, amenities near the Trailhead).
   Elevation comes from OSM `ele` at ingest, then from FABDEM after
   `dayhike sample-dem`.
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
| `data/catalog.geometry.sqlite` | GeometryStore: DayHikeGeometry + raw amenities | **gitignored** |
| `data/osm/` | Geofabrik / osmium extracts you download | gitignored |
| `data/valhalla/` | Road PBF + Valhalla routing tiles Docker builds | gitignored |
| `data/valhalla/compose.yaml` | How to run Valhalla | yes |
| `data/valhalla/prepare-roads.sh` | osmium filter: car roads only, no buildings | yes |
| `data/valhalla/prepare-highways.sh` | osmium filter: motorway/trunk/primary only | yes |
| `data/valhalla/mjolnir.drive-time.json` | Valhalla flags: no walk/bike/driveways | yes |
| `data/valhalla/*.osm.pbf` | Road extract for tiles | **gitignored** |
| `data/*_FABDEM_V1-2.tif` | FABDEM 1° tiles for elevation | **gitignored** |

`uv sync` does **not** download OSM and does **not** install Valhalla. Tests
never read `data/` and never start Docker. They rebuild a Catalog in a temp
directory from the committed fixture.

## The fixture is slim on purpose

`tests/fixtures/catalog.osm` is not a Croatia extract. It is a hand-built OSM
XML file with hiking relations so ingest and ranking have a known answer:

| OSM relation | Name | What happens |
| --- | --- | --- |
| 1001 | Ridge Walk | **Kept.** ~10 km, `sac_scale=mountain_hiking`, Hut/cafe/parking/water within 20 km of the Trailhead, elevation tags on nodes. |
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

Progress (`filter:`, `relations:`, `ways:`, `nodes:`, `access:`, `assemble:`,
`write:`) goes to **stderr**. Add `--list` to print kept DayHikes as
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
1002	Valley Path	<score>
```

The fixture Catalog now has several DayHikes, so Ridge Walk has more than one neighbour. Scores are cosine similarity after scaling.

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

OSM `ele` is sparse. After ingest, download FABDEM tiles for the PRD
Alps+Dinarides bbox and sample them onto GeometryStore vertices (this also
refreshes Catalog elevation min/max/gain and the scaler):

```bash
uv run dayhike fetch-dem --dest data
uv run dayhike sample-dem --catalog data/catalog.sqlite --dem data
```

`fetch-dem` skips tiles already on disk. `--bounds south,west,north,east`
overrides the default bbox. GeoTIFFs are gitignored. FABDEM V1-2 is
University of Bristol data, CC BY-NC-SA 4.0 (non-commercial). SolarAspect
stays in `notebooks/rank-feature-lab.ipynb` until it is promoted.

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
   kept. Buildings, shops, and the rest of the map are dropped. XML skips the
   filter. A file already named `*.catalog.osm.pbf` is read as-is. If `osmium`
   is missing, ingest reads the source file in several passes instead.
2. **Work file.** Relations, member ways, needed nodes, and amenities go to
   `data/catalog.sqlite.work` (same directory as the Catalog). Driveable
   highways are kept only when they fall inside the Trailhead access radius
   around a kept DayHike endpoint. That radius is `TRAILHEAD_ACCESS_RADIUS_M`
   in the package (the same band used to pick a Trailhead). If `osmium` is on
   `PATH` and the source is PBF, ingest extracts those disks from the
   unfiltered source (`osmium extract --strategy complete_ways`) and scans the
   extract; the Catalog is the same as a full-source access scan. Sidecars
   `*.sqlite.access*` are removed after a successful write. XML, or a missing
   `osmium`, scans the source file.
3. **Progress.** Each stage prints to stderr: `filter`, `relations`, `ways`,
   `nodes`, `access`, `assemble`, `write`. After nodes, `access:` first loads
   the work file and chooses DayHike endpoints, then extracts. Counts print
   every 100k objects during a load or scan. `osmium extract` prints its own
   progress on stderr.
4. **Resume.** The same `dayhike ingest` command on the same inputs continues
   the work file. `--fresh` (or `build_catalog(..., fresh=True)`) starts over.
   A `progress` callback on `build_catalog` receives the same stage lines.
5. **Replace.** The new Catalog is written to a temp file and renamed onto
   `catalog.sqlite` only when it is complete. A kill during ingest leaves the
   previous Catalog in place.

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
  nwr/amenity=parking,restaurant,cafe,drinking_water,shelter \
  nwr/natural=peak \
  -o data/osm/croatia-latest.catalog.osm.pbf
```

### What ingest keeps

Same rules as the fixture: `route=hiking` relations whose geometry is 1–20 km.
Amenities on nodes, ways, and tagged relations: Catalog AmenityFacts still use
hut/parking/restaurant_cafe/drinking_water within 20 km of the Trailhead.
Viewpoint, Peak, and Shelter are stored on GeometryStore only. The Trailhead is the LineString endpoint nearer a parking lot or
driveable highway within 5 km, otherwise the first vertex. Ways in a route
relation are reversed as needed so the geometry is one continuous line.

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
- `rank_dayhikes` — Catalog + Seeds → Ranking
- `rank_scaled_rows` / `fit_scaler_from_matrix` / `scale_matrix` / `cosine_similarity` — same scoring on an experimental feature matrix
- `geometry_store_path_for` — sidecar path for DayHikeGeometry
- `fetch_fabdem` / `sample_dem` — local FABDEM tiles → GeometryStore vertices and Catalog elevation
- `filter_by_drive_time` — Ranking filtered by DriveTime from Origin
- `ValhallaMatrixClient` — HTTP adapter for `/sources_to_targets`

CLI: `dayhike ingest`, `dayhike fetch-extracts`, `dayhike fetch-dem`,
`dayhike sample-dem`, `dayhike rank`.

Docstrings on those functions use the glossary in `CONTEXT.md`.

## Demonstration notebooks

- [`notebooks/catalog-and-ranker.ipynb`](notebooks/catalog-and-ranker.ipynb) —
  ingest the fixture, inspect FeatureVectors, rank by Seed (including
  max-not-mean), unknown Seed failure, stubbed DriveTime.
- [`notebooks/working-with-the-catalog.ipynb`](notebooks/working-with-the-catalog.ipynb) —
  SQLite tables, `Catalog.open`, and how to work with DayHike objects.
- [`notebooks/ingest-a-snapshot.ipynb`](notebooks/ingest-a-snapshot.ipynb) —
  ingest stages, progress, resume after a kill, and unused OSM objects, all
  on the committed fixture.
- [`notebooks/inspect-the-catalog.ipynb`](notebooks/inspect-the-catalog.ipynb) —
  schema, pandas DataFrames, sanity-check `data/catalog.sqlite` (counts,
  length band, Trailhead map). Falls back to the fixture if that file is missing.
- [`notebooks/rank-feature-lab.ipynb`](notebooks/rank-feature-lab.ipynb) —
  ranking sandbox: production `rank_dayhikes` vs experimental FeatureVectors
  on GeometryStore, optional FABDEM sampling.

The first three run against the committed OSM fixture (no Geofabrik, no live
Valhalla). `inspect-the-catalog` and `rank-feature-lab` open your local Catalog
when present (re-ingest so GeometryStore and `name_origin` exist).
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
- `data/catalog.geometry.sqlite` — GeometryStore (DayHikeGeometry + raw amenities)
- `data/catalog.sqlite.work` — in-progress ingest (resume handle)
- `data/osm/` — Geofabrik / osmium extracts
- `data/valhalla/` — road PBF and routing tiles (`compose.yaml`,
  `prepare-roads.sh`, `prepare-highways.sh`, and `mjolnir.drive-time.json`
  are tracked)
- `data/*.pbf`, `data/*.tif`, `data/*.tiff`

The committed fixture lives under `tests/fixtures/`, not `data/`.
