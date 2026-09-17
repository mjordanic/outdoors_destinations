# DayHike catalog

A local Catalog of DayHikes from OpenStreetMap, ranked by similarity to Seeds
you already know. DriveTime is an optional query-time filter from today's
Origin. DriveTime is not a FeatureVector column.

## Install

```bash
uv sync
```

The package is `dayhike`. Tests: `uv run pytest`.

## Ingest the committed fixture

The oracle OSM extract is `tests/fixtures/catalog.osm` (not a Geofabrik
download). Build a Catalog on disk with `build_catalog`:

```bash
mkdir -p data
uv run python -c "from dayhike import build_catalog; print(build_catalog('tests/fixtures/catalog.osm', 'data/catalog.sqlite').ingest_report)"
```

That writes gitignored `data/catalog.sqlite`. The fixture is OSM XML that
`build_catalog` can parse; tests never read `data/`.

## Rank by Seed

The CLI is the documented entry point. Seed names must exist in the Catalog
(unknown or ambiguous names fail; there is no fuzzy lookup):

```bash
uv run dayhike rank --catalog data/catalog.sqlite "Ridge Walk"
```

Prints DayHike id, name, and similarity score. Ranking without Origin does not
talk to Valhalla.

## Optional Origin and max hours

Pass `--origin` (latitude,longitude) and `--max-hours` together to drop
DayHikes whose DriveTime from Origin exceeds the max:

```bash
uv run dayhike rank --catalog data/catalog.sqlite \
  --origin 45.8,15.97 --max-hours 2 \
  "Ridge Walk"
```

That needs a local Valhalla instance. See [docs/valhalla.md](docs/valhalla.md)
and [docs/valhalla/compose.yaml](docs/valhalla/compose.yaml).

## Python API

The same seams the CLI wraps:

- `build_catalog` — OSM snapshot → Catalog of DayHikes
- `resolve_seed` — Seed name → DayHike id (must exist in the Catalog)
- `rank_dayhikes` — Catalog + Seeds → Ranking
- `filter_by_drive_time` — Ranking filtered by DriveTime from Origin

Docstrings on those functions use the glossary in `CONTEXT.md`.

## Demonstration notebooks

[`notebooks/catalog-and-ranker.ipynb`](notebooks/catalog-and-ranker.ipynb)
runs against the committed OSM fixture (no Geofabrik, no live Valhalla).
Open it with the project interpreter after `uv sync`.

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
